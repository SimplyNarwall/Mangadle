#!/usr/bin/env python3
"""
mangadle daily builder.

Reads the snapshot made by anilist_snapshot.py, picks each day's 10 titles,
fetches everything the hints need, redacts summaries and reviews, and writes
one self-contained JSON file per day that the game loads:

    data/
      snapshot.json          (from anilist_snapshot.py)
      raw_anilist.json       (from anilist_snapshot.py, used for sequel lookups)
      suggestions.json       queue of player suggestions (you create/append)
      history.json           every built day: date, puzzle number, AniList ids
      days/2026-11-11.json   the puzzle for that date

Usage (run from the folder that contains both scripts):
    python build_day.py --data data                      # build tomorrow
    python build_day.py --data data --date 2026-11-11    # build a given date
    python build_day.py --data data --ahead 7            # build the next 7 missing days
    python build_day.py --data data --date 2026-11-11 --force   # rebuild one day
    python build_day.py --data data --days-dir docs/days --ahead 7   # write straight into the site
    python build_day.py --data data --date 2026-11-11 --same-titles  # same picks, fresh hints/redaction

suggestions.json format (oldest first; used entries are removed; duplicates ignored):
    [ {"anilist_id": 30013, "by": "ethan", "suggested_at": "2026-11-10"}, {"anilist_id": 105398} ]
A suggestion plays no sooner than MIN_SUGGESTION_AGE_DAYS (7) after suggested_at.
Entries without suggested_at (e.g. added by hand) can play right away.

Every built day has "reviewed": false. Read its "review_notes" (names that were
not auto-redacted but appear in the text) and fix anything that leaks, then
set "reviewed": true.

Standard library only.
"""

import argparse
import datetime as dt
import html
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anilist_snapshot as snap  # noqa: E402

# ----------------------------------------------------------------- settings --
SITE_TZ = ZoneInfo("America/Chicago")   # must match docs/js/config.js timezone
# Puzzle #1 is this date; every later day's number is counted from it. To move
# the launch, change this and run: python3 scripts/dev.py renumber
LAUNCH_DATE = dt.date(2026, 11, 11)
TITLES_PER_DAY = 10
NO_REPEAT_DAYS = 50
MIN_SUGGESTION_AGE_DAYS = 7         # a suggestion can't be played until a week after it was sent
# Each day's mix: 7 "possible" (10k+ popularity), 2 "not possible" (1k to 10k), 1 fully random
# (no popularity floor). Tier cutoffs live in anilist_snapshot.TIERS. Any slots beyond this list
# (TITLES_PER_DAY - len(REQUIRED_TIERS)) would be wildcards drawn from WILDCARD_CHOICES.
REQUIRED_TIERS = ["possible"] * 7 + ["not_possible"] * 2 + ["random"]
WILDCARD_CHOICES = ["possible", "not_possible", "random"]
TAG_RANK_CUTOFF = 70
MIN_TAGS = 5
MAX_CHARACTERS = 15
REVIEW_COUNT = 3
REVIEW_EXCERPT_CHARS = 400          # "X" in the spec; change freely
MIN_REDACT_NAME_LEN = 3             # shorter names are not auto-redacted
RANDOM_ID_MAX = 200_000             # AniList ids are below this
MANGADEX_ALIAS_LANGS = {"en", "ja-ro", "ko-ro", "zh-ro"}
CREATOR_ROLE = re.compile(r"^(story|art|story & art|original creator|original story|original work)\b", re.I)

# Common words that are also character names; never auto-redacted, but flagged
# in review_notes if they appear.
COMMON_WORDS = {
    "hope", "rock", "king", "queen", "light", "may", "will", "grace", "joy", "faith",
    "love", "sky", "rain", "snow", "angel", "death", "god", "lord", "master", "father",
    "mother", "brother", "sister", "boss", "doctor", "captain", "hero", "demon", "the",
    "and", "you", "her", "his", "one", "two", "red", "blue", "black", "white", "gold",
}

DETAIL_QUERY = """
query ($id: Int) {
  Media(id: $id, type: MANGA) {
    id idMal siteUrl isAdult
    title { romaji english native }
    synonyms format countryOfOrigin status
    startDate { year } endDate { year }
    chapters volumes popularity averageScore genres description(asHtml: false)
    tags { name rank category isMediaSpoiler isGeneralSpoiler }
    coverImage { extraLarge large color }
    relations { edges { relationType node { id type format title { romaji english } synonyms } } }
    staff(perPage: 25, sort: [RELEVANCE]) { edges { role node { name { full native } } } }
    mainChars: characters(role: MAIN, sort: [FAVOURITES_DESC], perPage: 25) {
      edges { role node { name { full first last native alternative } image { medium } } }
    }
    supportChars: characters(role: SUPPORTING, sort: [FAVOURITES_DESC], perPage: 15) {
      edges { role node { name { full first last native alternative } image { medium } } }
    }
    reviews(sort: [RATING_DESC], perPage: 3) {
      nodes { summary body rating ratingAmount score siteUrl }
    }
  }
}
"""

RANDOM_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: MANGA, isAdult: false, format_in: [MANGA, ONE_SHOT]) {
      id popularity
      relations { edges { relationType node { id type format } } }
    }
  }
}
"""


# ----------------------------------------------------------------- io utils --
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def anilist(query, variables):
    data = snap.post_graphql(query, variables)
    time.sleep(snap.REQUEST_GAP_SECONDS)
    return data


def mangadex_get(path, params):
    url = "https://api.mangadex.org" + path + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "mangadle-builder/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  MangaDex lookup failed: {e}", file=sys.stderr)
        return None
    time.sleep(0.4)  # MangaDex allows ~5 req/s
    return out


# --------------------------------------------------------------- selection --
def recent_ids(history, date, days=NO_REPEAT_DAYS):
    """Ids played within `days` of date, in either direction (days built ahead count too)."""
    out = set()
    for entry in history:
        d = dt.date.fromisoformat(entry["date"])
        if d != date and abs((d - date).days) <= days:
            out.update(entry["anilist_ids"])
    return out


def pick_random_id(rng, exclude, fetch=anilist, attempts=40):
    """Fully random eligible title with no popularity floor: sample random ids 50 at a time."""
    for _ in range(attempts):
        ids = rng.sample(range(1, RANDOM_ID_MAX), 50)
        hits = fetch(RANDOM_QUERY, {"ids": ids})["Page"]["media"]
        # Skip spin-offs, sequels, and anything with alternative versions (we can't tell which one is main).
        hits = [m for m in hits if m["id"] not in exclude and not snap.is_spinoff(m)
                and not snap.is_sequel(m) and not snap.alternatives(m)]
        if hits:
            return rng.choice(hits)["id"]
    raise RuntimeError("could not find a random eligible title")


def choose_titles(date, snapshot, history, suggestions, fetch=anilist, avoid=(), reroll=0):
    """Returns (list of (anilist_id, source, tier)), remaining suggestions."""
    # Seeded by date so a build is reproducible; each full rebuild bumps `reroll` so it
    # re-rolls the tier mix and picks instead of repeating them.
    rng = random.Random(f"mangadle-{date.isoformat()}" + (f"-r{reroll}" if reroll else ""))
    exclude = recent_ids(history, date) | set(avoid)
    by_id = {r["anilist_id"]: r for r in snapshot}
    tier_of = {r["anilist_id"]: snap.tier_for(r.get("popularity") or 0) for r in snapshot}
    picks, remaining = [], []

    # 1. Suggestions first (oldest first). Ones that can't play yet stay queued:
    #    too new (under MIN_SUGGESTION_AGE_DAYS before this date), played recently, or the day is full.
    #    Duplicate entries for the same title are dropped, keeping the oldest.
    seen = set()
    for s in suggestions:
        sid = s["anilist_id"]
        if sid in seen:
            continue
        seen.add(sid)
        sent = s.get("suggested_at")
        old_enough = not sent or (date - dt.date.fromisoformat(str(sent)[:10])).days >= MIN_SUGGESTION_AGE_DAYS
        if len(picks) < TITLES_PER_DAY and old_enough and sid not in exclude:
            tier = tier_of[sid] if sid in by_id else "random"
            picks.append((sid, "suggestion", tier))
        else:
            remaining.append(s)

    # 2. Fallback: cover any required tier the suggestions missed, then wildcards.
    # Tiers come from popularity using the current anilist_snapshot.TIERS, so tier changes
    # apply without re-running the snapshot. Titles nobody could guess (no accepted answers)
    # never get picked.
    pools = {t: [r["anilist_id"] for r in snapshot if tier_of[r["anilist_id"]] == t and r["accepted_aliases"]]
             for t in set(REQUIRED_TIERS) | set(WILDCARD_CHOICES)}
    last_played = {}
    for entry in history:
        for i in entry["anilist_ids"]:
            last_played[i] = max(last_played.get(i, ""), entry["date"])
    have = Counter(p[2] for p in picks)
    needed = []
    for t in REQUIRED_TIERS:
        if have[t] > 0:
            have[t] -= 1
        else:
            needed.append(t)
    while len(picks) < TITLES_PER_DAY:
        required = bool(needed)
        tier = needed.pop(0) if needed else rng.choice(WILDCARD_CHOICES)
        taken = exclude | {p[0] for p in picks}
        options = [] if tier == "random" else [i for i in pools[tier] if i not in taken]
        if tier != "random" and not options:
            if required:
                # Every title in this tier was played in the last NO_REPEAT_DAYS days:
                # reuse whichever was played longest ago rather than failing.
                today_ids = {p[0] for p in picks}
                fallback = sorted((i for i in pools[tier] if i not in today_ids),
                                  key=lambda i: last_played.get(i, ""))
                if not fallback:
                    raise RuntimeError(f"tier {tier} is empty")
                print(f"  note: every {tier} title was played in the last {NO_REPEAT_DAYS} days; "
                      f"repeating the oldest one", file=sys.stderr)
                options = fallback[:1]
            else:
                tier = "random"  # a wildcard whose tier ran dry goes fully random
        pid = pick_random_id(rng, taken, fetch) if tier == "random" else rng.choice(options)
        picks.append((pid, "fallback", tier))

    # Mix the tiers up (same order every rebuild of this date, since rng is seeded by date).
    rng.shuffle(picks)
    return picks, remaining


# --------------------------------------------------------------- redaction --
def clean_anilist_text(text):
    """AniList description / review body -> plain text."""
    if not text:
        return ""
    t = text
    t = re.sub(r"~!.*?!~", " ", t, flags=re.S)                       # spoiler blocks
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)                                    # html tags
    t = re.sub(r"(?i)\b(img|image|youtube|webm)\d*%?\s*\([^)]*\)", " ", t)   # AniList media embeds
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)                 # markdown links -> text
    t = re.sub(r"~~~|__|\*\*|~~|^#+\s*", "", t, flags=re.M)
    t = html.unescape(t)
    t = re.sub(r"(?i)[\(\[]\s*(source|from|written by)\s*:[^)\]]*[\)\]]", "", t)   # "(Source: ...)"
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def character_names(chars):
    names = set()
    for c in chars:
        n = c["name"]
        for key in ("full", "first", "last"):
            if n.get(key):
                names.add(n[key].strip())
        for alt in n.get("alternative") or []:
            if alt:
                names.add(alt.strip())
        if n.get("full"):
            names.update(part for part in n["full"].split() if part)
    return names


# Romanization variants: "Yuuri", "Yuri", "Yūri" and "Yuurii" are the same name, as are
# "Ohno"/"Ono"/"Ōno" and "Hae-In"/"Haein". Names are reduced to a skeleton (long vowels
# collapsed, accents stripped), then matched with a pattern that allows any of those
# spellings. Only vowel length and separators vary, so ordinary words aren't caught.
_VOWEL_PATTERNS = {
    "a": "(?:aa|a|ā|â)",
    "i": "(?:ii|i|ī|î)",
    "u": "(?:uu|u|ū|û)",
    "e": "(?:ee|e|ē|ê)",
    "o": "(?:ou|oo|oh(?![aeiou])|o|ō|ô)",
}


def name_skeleton(name):
    t = unicodedata.normalize("NFKD", name.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[\s\-'’‐.]+", " ", t).strip()
    t = re.sub(r"ou|oo|oh(?![aeiou])", "o", t)
    for v in "aiue":
        t = re.sub(v + "+", v, t)
    t = re.sub("o+", "o", t)
    return t


def name_pattern(name):
    """Regex matching any common romanization of name, as a whole word."""
    out = []
    for ch in name_skeleton(name):
        if ch in _VOWEL_PATTERNS:
            out.append(_VOWEL_PATTERNS[ch])
        elif ch == " ":
            out.append(r"[\s\-'’‐]?")   # "Hae-In", "Hae In", "HaeIn"
        else:
            out.append(re.escape(ch))
    return re.compile(r"(?<!\w)" + "".join(out) + r"(?!\w)", re.I)


def build_redactor(title_names, char_names):
    """Returns redact(text) -> (text, notes)."""
    def usable(name):
        return (len(name) >= MIN_REDACT_NAME_LEN and name.lower() not in COMMON_WORDS
                and name_skeleton(name) not in COMMON_WORDS)

    rules, skipped = [], []
    for label, names in (("[title]", title_names), ("[character]", char_names)):
        for n in names:
            if usable(n):
                # titles match exactly; character names also match romanization variants
                pat = name_pattern(n) if label == "[character]" else re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)", re.I)
                rules.append((len(n), pat, label))
            else:
                skipped.append(n)
    rules.sort(key=lambda r: -r[0])  # longest first so full names beat parts

    def redact(text):
        for _, pat, label in rules:
            text = pat.sub(label, text)
        text = re.sub(r"(\[(title|character)\])('s)?(\s+\[(title|character)\]('s)?)+",
                      lambda m: m.group(0).split()[-1], text)  # collapse "[character] [character]"
        notes = sorted({n for n in skipped if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text, re.I)})
        return text, notes

    return redact


# ------------------------------------------------------------------ details --
def mangadex_aliases(record):
    """Find the MangaDex entry whose AniList link matches; return (id, aliases, english_title)."""
    query = record["title"]["romaji"] or record["title"]["english"]
    if not query:
        return None, [], None
    res = mangadex_get("/manga", {"title": query, "limit": 10, "contentRating[]": ["safe", "suggestive", "erotica"]})
    for m in (res or {}).get("data", []):
        links = m["attributes"].get("links") or {}
        if str(links.get("al")) == str(record["anilist_id"]):
            names, english = [], None
            for lang, name in (m["attributes"].get("title") or {}).items():
                if lang in MANGADEX_ALIAS_LANGS and snap.is_latin_script(name):
                    names.append(name.strip())
                    if lang == "en":
                        english = english or name.strip()
            for alt in m["attributes"].get("altTitles") or []:
                for lang, name in alt.items():
                    if lang in MANGADEX_ALIAS_LANGS and snap.is_latin_script(name):
                        names.append(name.strip())
                        if lang == "en":
                            english = english or name.strip()
            return m["id"], names, english
    return None, [], None


def pick_tags(tags):
    strong = [t for t in tags if t["rank"] > TAG_RANK_CUTOFF]
    return strong if len(strong) >= MIN_TAGS else tags[:MIN_TAGS]


def build_title(anilist_id, source, tier, raw_by_id, fetch=anilist, mangadex=mangadex_aliases):
    m = fetch(DETAIL_QUERY, {"id": anilist_id})["Media"]
    if m.get("isAdult"):
        raise ValueError(f"{anilist_id} is adult")
    lookup = dict(raw_by_id)
    lookup[m["id"]] = m
    base = snap.build_record(m, lookup)

    mdx = mangadex(base)
    mdx_id, mdx_names = mdx[0], mdx[1]
    mdx_english = mdx[2] if len(mdx) > 2 else None
    aliases = list(dict.fromkeys(base["accepted_aliases"] + mdx_names))

    main = [e for e in m["mainChars"]["edges"]][:MAX_CHARACTERS]
    support = [e for e in m["supportChars"]["edges"]][:max(0, MAX_CHARACTERS - len(main))]
    redact = build_redactor(aliases, character_names([e["node"] for e in main + support]))

    summary, summary_notes = redact(clean_anilist_text(m.get("description")))

    reviews, review_notes = [], []
    for r in (m.get("reviews") or {}).get("nodes", [])[:REVIEW_COUNT]:
        body = clean_anilist_text(r.get("body"))
        if len(body) > REVIEW_EXCERPT_CHARS:
            excerpt = body[:REVIEW_EXCERPT_CHARS].rsplit(" ", 1)[0] + "..."  # cut at a word boundary
        else:
            excerpt = body
        s_text, n1 = redact(clean_anilist_text(r.get("summary")))
        e_text, n2 = redact(excerpt)
        reviews.append({"summary": s_text, "excerpt": e_text, "score": r.get("score"),
                        "upvotes": r.get("rating"), "url": r.get("siteUrl")})
        review_notes += n1 + n2

    creators = []
    for e in (m.get("staff") or {}).get("edges", []):
        if CREATOR_ROLE.match(e.get("role") or ""):
            creators.append({"name": e["node"]["name"]["full"], "native": e["node"]["name"].get("native"),
                             "role": e["role"]})

    def char_out(edges):
        return [{"name": e["node"]["name"]["full"], "image": (e["node"].get("image") or {}).get("medium")} for e in edges]

    year = base["start_year"]
    if base["status"] == "RELEASING":
        release = f"{year}, ongoing" if year else "ongoing"
    elif base["end_year"] and base["end_year"] != year:
        release = f"{year} to {base['end_year']}"
    else:
        release = str(year) if year else None

    return {
        "anilist_id": base["anilist_id"],
        "source": source,
        "tier": tier,
        "revealed": {
            "origin": base["origin"],
            "format": base["format"],
            "popularity": base["popularity"],
            "average_score": base["average_score"],
            "genres": base["genres"],
            "demographic": base["demographic"],
            "release": release,
        },
        "hints": {
            "cover": {"cost": 50, "url": base["cover"]},
            "summary": {"cost": 100, "text": summary or None},
            "tags": {"cost": 75, "items": pick_tags(base["tags"]) or None},
            "creators": {"cost": 50, "items": creators or None},
            "characters": {"cost": 75, "main": char_out(main) or None, "supporting": char_out(support) or None},
            "reviews": [{"cost": 50, **r} for r in reviews],
        },
        "answer": {
            # Shown when the title is revealed: English whenever any source has one.
            "title": base["title"]["english"] or mdx_english or base["title"]["romaji"],
            "romaji": base["title"]["romaji"],
            "native": base["title"]["native"],
            "url": base["url"],
            "cover": base["cover"],
            "accepted_aliases": aliases,
            "mangadex_id": mdx_id,
        },
        "review_notes": sorted(set(summary_notes + review_notes)),
    }


def puzzle_number(date):
    return (date - LAUNCH_DATE).days + 1


def write_index(days_dir):
    """docs/days/index.json: every real day from launch onward, for the past days page."""
    entries = []
    for name in sorted(os.listdir(days_dir)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", name):
            continue
        day = load_json(os.path.join(days_dir, name), {})
        d = dt.date.fromisoformat(name[:-5])
        if day.get("_fake") or d < LAUNCH_DATE:
            continue
        entries.append({"date": d.isoformat(), "number": day.get("number", puzzle_number(d)),
                        "titles": len(day.get("titles", []))})
    save_json(os.path.join(days_dir, "index.json"), entries)
    return entries


# --------------------------------------------------------------------- main --
def build_day(date, data_dir, force=False, fetch=anilist, mangadex=mangadex_aliases, days_dir=None,
              same_titles=False):
    days_dir = days_dir or os.path.join(data_dir, "days")
    os.makedirs(days_dir, exist_ok=True)
    out_path = os.path.join(days_dir, f"{date.isoformat()}.json")
    hist_path = os.path.join(data_dir, "history.json")
    sugg_path = os.path.join(data_dir, "suggestions.json")

    history = load_json(hist_path, [])
    old_day = load_json(out_path, None) if same_titles else None
    if same_titles and old_day is None:
        raise SystemExit(f"{date}: --same-titles needs an existing day file at {out_path}")
    if os.path.exists(out_path) and not (force or same_titles):
        print(f"{date}: already built (use --force to rebuild)")
        return out_path
    # A full rebuild of an existing day re-picks everything and avoids the titles it had,
    # so a day you've seen (or spoiled) comes back with 10 different titles.
    previous = load_json(out_path, None) if (force and not same_titles) else None
    avoid = {t["anilist_id"] for t in previous["titles"]} if previous else set()
    reroll = (previous.get("rebuilds", 0) + 1) if previous else 0
    history = [h for h in history if h["date"] != date.isoformat()]

    snapshot = load_json(os.path.join(data_dir, "snapshot.json"), None)
    if snapshot is None:
        raise SystemExit("snapshot.json not found; run anilist_snapshot.py first")
    raw_by_id = {m["id"]: m for m in load_json(os.path.join(data_dir, "raw_anilist.json"), [])}
    suggestions = load_json(sugg_path, [])

    if old_day:
        # Re-fetch and re-redact the same titles; leave the suggestion queue alone.
        picks = [(t["anilist_id"], t.get("source", "fallback"), t.get("tier", "random")) for t in old_day["titles"]]
        remaining = suggestions
    else:
        picks, remaining = choose_titles(date, snapshot, history, suggestions, fetch, avoid=avoid, reroll=reroll)
    titles = []
    for pid, source, tier in picks:
        print(f"{date}: building {pid} ({source}, {tier})", file=sys.stderr)
        titles.append(build_title(pid, source, tier, raw_by_id, fetch, mangadex))

    number = puzzle_number(date)
    rebuilds = old_day.get("rebuilds", 0) if old_day else (reroll if previous else 0)
    day = {"date": date.isoformat(), "number": number, "reviewed": False, "rebuilds": rebuilds,
           "start_points": 1000, "titles": titles}

    save_json(out_path, day)
    write_index(days_dir)
    history.append({"date": date.isoformat(), "number": number, "anilist_ids": [t["anilist_id"] for t in titles]})
    history.sort(key=lambda h: h["date"])
    save_json(hist_path, history)
    save_json(sugg_path, remaining)

    flagged = sum(1 for t in titles if t["review_notes"])
    print(f"{date}: wrote {out_path} (#{number}); {flagged} title(s) have review notes")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="data folder (default: data)")
    ap.add_argument("--date", help="YYYY-MM-DD (default: tomorrow)")
    ap.add_argument("--ahead", type=int, help="build today and the next N-1 days, skipping any already built")
    ap.add_argument("--force", action="store_true", help="rebuild even if the day exists")
    ap.add_argument("--same-titles", action="store_true",
                    help="rebuild --date with the titles it already has (refresh hints/redaction only)")
    ap.add_argument("--days-dir", help="where day files go (default: DATA/days); use docs/days for the site")
    args = ap.parse_args()

    if args.ahead:
        today = dt.datetime.now(SITE_TZ).date()
        for i in range(0, args.ahead):  # today plus the next N-1 days
            build_day(today + dt.timedelta(days=i), args.data, force=False, days_dir=args.days_dir)
    else:
        date = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(SITE_TZ).date() + dt.timedelta(days=1)
        build_day(date, args.data, force=args.force, days_dir=args.days_dir, same_titles=args.same_titles)


if __name__ == "__main__":
    main()
