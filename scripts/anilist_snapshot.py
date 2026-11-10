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
REQUEST_GAP_SECONDS = 2.1          # ~28 requests/min, safe under the degraded limit
BAND_SPLIT_THRESHOLD = 4500        # AniList stops paginating deep result sets; split big bands

# Difficulty tiers by AniList popularity (members with the title on their list).
TIERS = [
    ("easy", 50_000, None),
    ("medium", 10_000, 50_000),
    ("hard", 1_000, 10_000),
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


def fetch_band(fetch, pop_greater, pop_lesser):
    """Fetch all media with pop_greater < popularity < pop_lesser (lesser may be None)."""
    items, page = [], 1
    while True:
        variables = {"page": page, "perPage": PER_PAGE, "popGreater": pop_greater}
        if pop_lesser is not None:
            variables["popLesser"] = pop_lesser
        data = fetch(QUERY, variables)
        time.sleep(REQUEST_GAP_SECONDS)
        page_data = data["Page"]
        info = page_data["pageInfo"]
        if page == 1:
            total = info.get("total") or 0
            hi = "max" if pop_lesser is None else pop_lesser
            print(f"band popularity ({pop_greater}, {hi}): ~{total} titles", file=sys.stderr)
            # Deep pagination is unreliable on AniList; split big bands in two.
            if total >= BAND_SPLIT_THRESHOLD and pop_lesser is not None and pop_lesser - pop_greater > 2:
                mid = (pop_greater + pop_lesser) // 2
                return fetch_band(fetch, pop_greater, mid + 1) + fetch_band(fetch, mid, pop_lesser)
        items.extend(page_data["media"])
        if not info.get("hasNextPage"):
            return items
        page += 1


# Bands chosen so each is small enough to paginate. Boundaries overlap by one
# because popularity_greater / popularity_lesser are exclusive; dedupe by id later.
BANDS = [
    (50_000 - 1, None),
    (20_000 - 1, 50_000),
    (10_000 - 1, 20_000),
    (5_000 - 1, 10_000),
    (3_000 - 1, 5_000),
    (2_000 - 1, 3_000),
    (MIN_POPULARITY - 1, 2_000),
]


def fetch_all(fetch=post_graphql):
    seen = {}
    for lo, hi in BANDS:
        for m in fetch_band(fetch, lo, hi):
            seen[m["id"]] = m
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Processing
# --------------------------------------------------------------------------- #

def is_latin_script(text):
    """True if every letter in text is Latin (allows digits, punctuation, symbols)."""
    has_letter = False
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            has_letter = True
            if not unicodedata.name(ch, "").startswith("LATIN"):
                return False
    return has_letter


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


def edges(media, relation_type):
    return [
        e["node"] for e in (media.get("relations") or {}).get("edges", [])
        if e["relationType"] == relation_type and e["node"]["type"] == "MANGA"
    ]


def is_spinoff(media):
    """Spin-offs and side stories point back to their main series with PARENT."""
    return any(n.get("format") != "NOVEL" for n in edges(media, "PARENT"))


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
    accepted = list(dict.fromkeys(aliases + root_aliases))

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
    records, excluded = [], Counter()
    for m in raw:
        if m.get("format") not in ("MANGA", "ONE_SHOT"):
            excluded["not manga/oneshot format"] += 1
            continue
        if is_spinoff(m):
            excluded["spin-off / side story"] += 1
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
    lines.append(f"sequels (original's names also accepted): {sum(r['is_sequel'] for r in records)}")
    lines.append(f"titles with no Latin-script alias: {sum(1 for r in records if not r['accepted_aliases'])}")
    lines.append("")
    lines.append("excluded:")
    for k, v in excluded.most_common():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("top 10 easy:")
    for r in [r for r in records if r["tier"] == "easy"][:10]:
        lines.append(f"  {r['popularity']:>8,}  {r['title']['english'] or r['title']['romaji']}")
    lines.append("")
    lines.append("hard tier sample (popularity just over 1,000):")
    for r in [r for r in records if r["tier"] == "hard"][-10:]:
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
