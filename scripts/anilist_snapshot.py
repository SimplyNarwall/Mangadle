#!/usr/bin/env python3
"""
mangadle AniList snapshot.

Pulls every non-adult manga / manhwa / manhua (formats MANGA and ONE_SHOT, no
light novels) with AniList popularity >= MIN_POPULARITY, drops spin-offs and
side stories, builds the accepted-alias list for each title (including the
original series for sequels), assigns difficulty tiers, and writes:

    snapshot.json   one record per eligible title
    summary.txt     counts per tier / origin / format, plus what was excluded

Standard library only. Usage:
    python anilist_snapshot.py               # full run
    python anilist_snapshot.py --out data/   # choose output folder

AniList allows 90 requests/min normally, sometimes degraded to 30/min. This
script paces itself for 30/min and backs off on 429, so a full run takes a few
minutes.
"""

import argparse
import json
import re
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter

API_URL = "https://graphql.anilist.co"
# AniList sits behind Cloudflare, which rejects Python's default User-Agent with 403.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 mangadle-snapshot/1.0"
MIN_POPULARITY = 1000
PER_PAGE = 50
REQUEST_GAP_SECONDS = 2.1          # starts at ~28/min; raised to AniList's real limit after the first reply
PAGES_PER_WINDOW = 40              # restart the query after this many pages (2,000 titles)

# Tiers by AniList popularity (members with the title on their list). build_day.py
# re-derives tiers from popularity with these, so changing them needs no snapshot re-run.
TIERS = [
    ("possible", 10_000, None),       # a player has a real shot at knowing it
    ("not_possible", 1_000, 10_000),  # deep cuts
]

ORIGIN_NAMES = {"JP": "manga", "KR": "manhwa", "CN": "manhua", "TW": "manhua"}

QUERY = """
query ($page: Int, $perPage: Int, $popGreater: Int, $popLesser: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage hasNextPage }
    media(
      type: MANGA
      isAdult: false
      format_in: [MANGA, ONE_SHOT]
      popularity_greater: $popGreater
      popularity_lesser: $popLesser
      sort: POPULARITY_DESC
    ) {
      id
      idMal
      siteUrl
      title { romaji english native }
      synonyms
      format
      countryOfOrigin
      status
      startDate { year }
      endDate { year }
      chapters
      volumes
      popularity
      averageScore
      genres
      tags { name rank category isMediaSpoiler isGeneralSpoiler }
      coverImage { extraLarge large color }
      relations {
        edges {
          relationType
          node { id type format title { romaji english } synonyms }
        }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def post_graphql(query, variables, max_retries=6):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                limit = resp.headers.get("X-RateLimit-Limit")
                if limit and limit.isdigit() and int(limit) > 0:
                    global REQUEST_GAP_SECONDS
                    REQUEST_GAP_SECONDS = max(0.4, 60 / int(limit) * 1.1)  # 10% under the limit
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if remaining is not None and int(remaining) <= 2:
                    time.sleep(30)
                if "errors" in payload:
                    raise RuntimeError(payload["errors"])
                return payload["data"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "60")) + 2
                print(f"  rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                wait = 10 * (attempt + 1)
                print(f"  server error {e.code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            detail = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"AniList returned HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            wait = 10 * (attempt + 1)
            print(f"  network error {e.reason}, retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("AniList request failed after retries")


def fetch_all(fetch=None):
    """Page down the whole list, most popular first, using keyset pagination.

    AniList's pageInfo.total is not reliable (it reports 5000 for anything over
    one page), so we never look at it. We just follow hasNextPage. To avoid
    paging very deep into one query, every PAGES_PER_WINDOW pages we start a
    fresh query just below the lowest popularity seen so far. Titles that tie
    at that popularity are fetched twice and de-duplicated by id.
    """
    fetch = fetch or post_graphql
    seen = {}
    pop_lesser = None          # exclusive upper bound for the current window
    started = time.time()
    while True:
        lowest = None
        for page in range(1, PAGES_PER_WINDOW + 1):
            variables = {"page": page, "perPage": PER_PAGE, "popGreater": MIN_POPULARITY - 1}
            if pop_lesser is not None:
                variables["popLesser"] = pop_lesser
            data = fetch(QUERY, variables)
            time.sleep(REQUEST_GAP_SECONDS)
            page_data = data["Page"]
            for m in page_data["media"]:
                seen[m["id"]] = m
                pop = m.get("popularity") or 0
                lowest = pop if lowest is None else min(lowest, pop)
            if not page_data["pageInfo"].get("hasNextPage"):
                print(f"done: {len(seen)} titles in {time.time() - started:.0f}s", file=sys.stderr)
                return list(seen.values())
        print(f"  {len(seen):>6} titles so far, now at popularity {lowest:,} "
              f"({time.time() - started:.0f}s)", file=sys.stderr)
        next_lesser = lowest + 1  # +1 because popularity_lesser is exclusive: re-fetch the tie group
        if pop_lesser is not None and next_lesser >= pop_lesser:
            next_lesser = lowest  # a whole window was one popularity value; skip past it
        pop_lesser = next_lesser


# --------------------------------------------------------------------------- #
# Processing
# --------------------------------------------------------------------------- #

def is_latin_script(text):
    """True if every letter in text is Latin. Titles with no letters at all
    (like "23:45" or "11336") count too, as long as they have a digit."""
    has_letter = has_digit = False
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            has_letter = True
            if not unicodedata.name(ch, "").startswith("LATIN"):
                return False
        elif cat == "Nd":
            has_digit = True
    return has_letter or has_digit


def titles_of(media):
    """Latin-script names for a media object or relation node."""
    t = media.get("title") or {}
    names = [t.get("english"), t.get("romaji")] + list(media.get("synonyms") or [])
    out, seen = [], set()
    for n in names:
        if not n:
            continue
        n = n.strip()
        if n and is_latin_script(n) and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def main_title_parts(names):
    """'Kino's Journey: The Beautiful World' also answers as 'Kino's Journey'.
    Splits on ':' or ' - '; keeps the part before it if it's long enough to be
    a real title (not 'Re' from 'Re:Zero')."""
    out = []
    for n in names:
        for sep in (":", " - ", " ~"):
            if sep in n:
                head = n.split(sep, 1)[0].strip()
                letters = re.sub(r"[^\w]", "", head)
                if len(letters) >= 4 and head.lower() != n.lower():
                    out.append(head)
    return out


def edges(media, relation_type):
    return [
        e["node"] for e in (media.get("relations") or {}).get("edges", [])
        if e["relationType"] == relation_type and e["node"]["type"] == "MANGA"
    ]


def is_spinoff(media):
    """Spin-offs and side stories point back to their main series with PARENT.
    The parent can be a light novel or an anime, not just a manga."""
    return any(e["relationType"] == "PARENT" for e in (media.get("relations") or {}).get("edges", []))


def alternatives(media):
    """Other manga versions of the same story (e.g. two adaptations of one game)."""
    return [n for n in edges(media, "ALTERNATIVE") if n.get("format") != "NOVEL"]


def is_sequel(media, by_id=None):
    """A later entry in a series: it has a PREQUEL that is in the pool and came out
    no later than it did. (AniList also calls later-written prequels PREQUEL, e.g.
    Corpse Party's Cemetery 0; those don't count.) With no by_id, e.g. for random
    picks outside the pool, any manga PREQUEL counts."""
    prequels = [n for n in edges(media, "PREQUEL") if n.get("format") != "NOVEL"]
    if by_id is None:
        return bool(prequels)
    year = (media.get("startDate") or {}).get("year")
    for n in prequels:
        full = by_id.get(n["id"])
        if full is None:
            continue  # obscure prequel outside the pool: this entry stands in for the series
        p_year = (full.get("startDate") or {}).get("year")
        if year is None or p_year is None or p_year <= year:
            return True
    return False


def is_later_prequel(media, by_id):
    """A prequel written after the series it leads into: it has a SEQUEL in the pool that
    came out earlier (e.g. Psycho-Pass: Inspector Shinya Kogami, 2014, leads into
    Inspector Akane Tsunemori, 2012). The original must also be more popular, so two
    hit series AniList happens to link (e.g. a shared universe) both stay."""
    year = (media.get("startDate") or {}).get("year")
    pop = media.get("popularity") or 0
    for n in edges(media, "SEQUEL"):
        if n.get("format") == "NOVEL":
            continue
        full = by_id.get(n["id"])
        if full is None:
            continue
        s_year = (full.get("startDate") or {}).get("year")
        if year is None or s_year is None:
            continue
        # the original came out first and is the better-known one
        if s_year <= year and (full.get("popularity") or 0) > pop:
            return True
    return False


def _norm_title(t):
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (t or "").lower())).strip()


def build_title_index(raw):
    """normalized main title -> highest popularity of any manga using it as its full title."""
    idx = {}
    for m in raw:
        if m.get("isAdult") or m.get("format") == "NOVEL":
            continue
        for t in ((m.get("title") or {}).get("english"), (m.get("title") or {}).get("romaji")):
            k = _norm_title(t)
            if k:
                idx[k] = max(idx.get(k, 0), m.get("popularity") or 0)
    return idx


def is_named_spinoff(media, title_index):
    """'Attack on Titan: Before the Fall' when 'Attack on Titan' is a more popular series.
    Catches spin-offs AniList only links through their light novel source."""
    pop = media.get("popularity") or 0
    t = media.get("title") or {}
    for head in main_title_parts([x for x in (t.get("english"), t.get("romaji")) if x]):
        if title_index.get(_norm_title(head), 0) > pop:
            return True
    return False


def is_lesser_alternative(media, by_id):
    """True if a more popular alternative version of this story is also in the pool.
    Only the most popular version gets picked; its answers accept every version's name."""
    pop = media.get("popularity") or 0
    for n in alternatives(media):
        other = by_id.get(n["id"])
        if other is None:
            continue  # below the popularity floor, so not competing
        other_pop = other.get("popularity") or 0
        if other_pop > pop or (other_pop == pop and other["id"] < media["id"]):
            return True
    return False


def series_root(media, by_id, max_depth=20):
    """Follow PREQUEL links back to the first entry. Returns (root_node, chain_ids)."""
    current, chain = media, [media["id"]]
    for _ in range(max_depth):
        prequels = [n for n in edges(current, "PREQUEL") if n.get("format") != "NOVEL"]
        if not prequels:
            break
        prev = prequels[0]
        if prev["id"] in chain:
            break  # bad data loop
        chain.append(prev["id"])
        # Use the full record if we have it so we can keep walking.
        current = by_id.get(prev["id"], prev)
        if current is prev:
            break
    return current, chain


def tier_for(popularity):
    for name, lo, hi in TIERS:
        if popularity >= lo and (hi is None or popularity < hi):
            return name
    return "random_only"


def build_record(m, by_id):
    root, chain = series_root(m, by_id)
    aliases = titles_of(m)
    root_aliases = []
    for node_id in chain[1:]:
        node = by_id.get(node_id)
        if node is None and node_id == root["id"]:
            node = root
        if node is not None:
            root_aliases.extend(titles_of(node))
    alt_aliases = []
    for n in alternatives(m):
        alt_aliases.extend(titles_of(by_id.get(n["id"], n)))
    # The light novel / web novel this manga adapts usually goes by the same name.
    source_aliases = []
    for e in (m.get("relations") or {}).get("edges", []):
        if e["relationType"] in ("ADAPTATION", "SOURCE") and e["node"].get("format") == "NOVEL":
            source_aliases.extend(titles_of(e["node"]))
    full = aliases + root_aliases + alt_aliases + source_aliases
    accepted = list(dict.fromkeys(full + main_title_parts(full)))
    # case-insensitive de-dupe, keeping the first spelling
    seen, unique = set(), []
    for a in accepted:
        if a.lower() not in seen:
            seen.add(a.lower())
            unique.append(a)
    accepted = unique

    tags = sorted(m.get("tags") or [], key=lambda t: -t["rank"])
    demographic = [t["name"] for t in tags if t.get("category") == "Demographic"]
    start = (m.get("startDate") or {}).get("year")
    end = (m.get("endDate") or {}).get("year")

    return {
        "anilist_id": m["id"],
        "mal_id": m.get("idMal"),
        "mangadex_id": None,  # filled in later for daily picks
        "url": m.get("siteUrl"),
        "title": {
            "romaji": (m.get("title") or {}).get("romaji"),
            "english": (m.get("title") or {}).get("english"),
            "native": (m.get("title") or {}).get("native"),
        },
        "accepted_aliases": accepted,
        "is_sequel": len(chain) > 1,
        "has_alternatives": bool(alternatives(m)),
        "series_root_id": root["id"] if len(chain) > 1 else None,
        "origin": ORIGIN_NAMES.get(m.get("countryOfOrigin"), m.get("countryOfOrigin")),
        "country": m.get("countryOfOrigin"),
        "format": "oneshot" if m.get("format") == "ONE_SHOT" else "series",
        "status": m.get("status"),
        "start_year": start,
        "end_year": end,
        "chapters": m.get("chapters"),
        "volumes": m.get("volumes"),
        "popularity": m.get("popularity"),
        "average_score": m.get("averageScore"),
        "tier": tier_for(m.get("popularity") or 0),
        "genres": m.get("genres") or [],
        "demographic": demographic,
        "tags": [
            {
                "name": t["name"],
                "rank": t["rank"],
                "spoiler": bool(t.get("isMediaSpoiler") or t.get("isGeneralSpoiler")),
            }
            for t in tags
            if t.get("category") != "Demographic"
        ],
        "cover": (m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large"),
    }


def process(raw):
    by_id = {m["id"]: m for m in raw}
    title_index = build_title_index(raw)
    records, excluded = [], Counter()
    for m in raw:
        if m.get("format") not in ("MANGA", "ONE_SHOT"):
            excluded["not manga/oneshot format"] += 1
            continue
        if is_spinoff(m):
            excluded["spin-off / side story"] += 1
            continue
        if is_named_spinoff(m, title_index):
            excluded["spin-off (named after a more popular series)"] += 1
            continue
        if is_sequel(m, by_id):
            excluded["sequel (only first entries are picked)"] += 1
            continue
        if is_later_prequel(m, by_id):
            excluded["prequel written after the original"] += 1
            continue
        if is_lesser_alternative(m, by_id):
            excluded["alternative version of a more popular entry"] += 1
            continue
        records.append(build_record(m, by_id))
    records.sort(key=lambda r: -(r["popularity"] or 0))
    return records, excluded


def summarize(records, excluded):
    lines = [f"eligible titles: {len(records)}", ""]
    tiers = Counter(r["tier"] for r in records)
    lines.append("by tier:")
    for name, lo, hi in TIERS:
        rng = f"{lo:,}+" if hi is None else f"{lo:,} to {hi:,}"
        lines.append(f"  {name:<8} ({rng}): {tiers.get(name, 0)}")
    lines.append("")
    for label, key in (("by origin", "origin"), ("by format", "format")):
        lines.append(f"{label}:")
        for k, v in Counter(r[key] for r in records).most_common():
            lines.append(f"  {k}: {v}")
        lines.append("")
    lines.append("tier x origin:")
    combo = Counter((r["tier"], r["origin"]) for r in records)
    for name, _, _ in TIERS:
        parts = ", ".join(f"{o} {combo.get((name, o), 0)}" for o in ("manga", "manhwa", "manhua"))
        lines.append(f"  {name:<8} {parts}")
    lines.append("")
    lines.append(f"with alternative versions (their names also accepted): {sum(r.get('has_alternatives', False) for r in records)}")
    lines.append(f"titles with no Latin-script alias: {sum(1 for r in records if not r['accepted_aliases'])}")
    lines.append("")
    lines.append("excluded:")
    for k, v in excluded.most_common():
        lines.append(f"  {k}: {v}")
    lines.append("")
    top, bottom = TIERS[0][0], TIERS[-1][0]
    lines.append(f"top 10 {top}:")
    for r in [r for r in records if r["tier"] == top][:10]:
        lines.append(f"  {r['popularity']:>8,}  {r['title']['english'] or r['title']['romaji']}")
    lines.append("")
    lines.append(f"{bottom} tier sample (popularity just over {MIN_POPULARITY:,}):")
    for r in [r for r in records if r["tier"] == bottom][-10:]:
        lines.append(f"  {r['popularity']:>8,}  {r['title']['english'] or r['title']['romaji']}  ({r['origin']}, {r['format']})")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=".", help="output folder (default: current folder)")
    ap.add_argument("--raw", help="reuse a saved raw_anilist.json instead of fetching")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    if args.raw:
        with open(args.raw, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = fetch_all()
        with open(os.path.join(args.out, "raw_anilist.json"), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)

    records, excluded = process(raw)
    with open(os.path.join(args.out, "snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    summary = summarize(records, excluded)
    with open(os.path.join(args.out, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(summary)


if __name__ == "__main__":
    main()
