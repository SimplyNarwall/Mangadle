#!/usr/bin/env python3
"""
mangadle dev tools. Run from anywhere; paths default to this repo.

  python3 scripts/dev.py serve                  run the site at http://localhost:8000
  python3 scripts/dev.py fake                   make a fake practice day (no internet needed)
  python3 scripts/dev.py check 2026-11-11       review a day: hints, leaks, review notes
  python3 scripts/dev.py rebuild 2026-11-11     re-pick and rebuild a day
  python3 scripts/dev.py rebuild 2026-11-11 --same-titles   keep the picks, refresh hints/redaction
  python3 scripts/dev.py approve 2026-11-11     mark a day "reviewed": true

Browser helpers (add to the URL while serving):
  ?date=2026-11-11       play any date
  ?reset                 wipe your progress for that date and start over
  ?api=http://localhost:8787   use a local worker (npx wrangler dev) instead of config.js
"""

import argparse
import datetime as dt
import functools
import http.server
import json
import os
import re
import sys
import webbrowser
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
DAYS = os.path.join(DOCS, "days")
DATA = os.path.join(ROOT, "data")
SITE_TZ = ZoneInfo("America/Chicago")
FAKE_DATE = "2000-01-01"

sys.path.insert(0, HERE)


def today():
    return dt.datetime.now(SITE_TZ).date()


def day_path(date):
    return os.path.join(DAYS, f"{date}.json")


def load_day(date):
    path = day_path(date)
    if not os.path.exists(path):
        raise SystemExit(f"no day file at {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


# --------------------------------------------------------------------- serve --
class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and str(args[1]).startswith(("4", "5")):  # only log errors
            super().log_message(fmt, *args)


def cmd_serve(args):
    handler = functools.partial(NoCacheHandler, directory=DOCS)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    base = f"http://localhost:{args.port}/"
    days = sorted(f[:-5] for f in os.listdir(DAYS) if f.endswith(".json")) if os.path.isdir(DAYS) else []
    print(f"serving docs/ at {base}  (Ctrl+C to stop)")
    print(f"  today ({today()}): {base}")
    for d in days[-8:]:
        print(f"  {d}: {base}?date={d}")
    if not days:
        print("  no day files yet; try: python3 scripts/dev.py fake")
    print(f"  add &reset to any URL to wipe your progress for that day")
    if not args.no_open:
        webbrowser.open(base + (f"?date={args.date}" if args.date else ""))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


# ---------------------------------------------------------------------- fake --
FAKE_TITLES = [
    ("Moon Rabbit Swordsman", "Tsuki Usagi no Kenshi", "manga", "easy", ["Action", "Fantasy"], "Shounen"),
    ("The Tower Climber's Ledger", "Tap Deungban Jangbu", "manhwa", "easy", ["Action", "Adventure"], None),
    ("Bakery at the End of the World", "Sekai no Hate no Pan-ya", "manga", "medium", ["Slice of Life", "Drama"], "Seinen"),
    ("Return of the 9th Mercenary", "Gubeonjjae Yongbyeong-ui Gwihwan", "manhwa", "medium", ["Action", "Fantasy"], None),
    ("Paper Crane Detective", "Orizuru Tantei", "manga", "hard", ["Mystery"], "Josei"),
    ("Jade Pavilion Cultivator", "Yu Ge Xiu Shi", "manhua", "hard", ["Action", "Fantasy"], None),
    ("Last Train to Nowhere", "Shuuden no Saki", "manga", "medium", ["Psychological", "Drama"], "Seinen"),
    ("My Rival Is a Vending Machine", "Raibaru wa Jihanki", "manga", "random", ["Comedy", "Romance"], None),
    ("Kaiju Club No. 3", "Kaijuu-bu San-gou", "manga", "easy", ["Comedy", "Sci-Fi"], "Shounen"),
    ("One Summer Afternoon", "Aru Natsu no Gogo", "manga", "random", ["Drama"], None),
]


def placeholder_cover(k, title):
    hue = (k * 37) % 360
    words = title.upper().split()
    lines = "".join(f'<text x="18" y="{50 + i * 34}" font-size="28" font-family="sans-serif" font-weight="bold" fill="white">{w}</text>' for i, w in enumerate(words[:3]))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="230" height="330">'
           f'<rect width="230" height="330" fill="hsl({hue},55%,40%)"/>{lines}'
           f'<circle cx="115" cy="230" r="55" fill="hsl({(hue + 180) % 360},60%,75%)"/>'
           f'<rect x="85" y="275" width="60" height="55" fill="hsl({(hue + 180) % 360},60%,75%)"/></svg>')
    import urllib.parse
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)


def cmd_fake(args):
    os.makedirs(DAYS, exist_ok=True)
    titles = []
    for k, (en, romaji, origin, tier, genres, demo) in enumerate(FAKE_TITLES):
        cover = placeholder_cover(k, en)
        is_oneshot = en == "One Summer Afternoon"
        titles.append({
            "anilist_id": 900000 + k, "source": "fallback", "tier": tier,
            "revealed": {"origin": origin, "format": "oneshot" if is_oneshot else "series",
                         "popularity": [120000, 95000, 30000, 22000, 4000, 2500, 15000, 300, 70000, 800][k],
                         "average_score": 70 + k, "genres": genres, "demographic": [demo] if demo else [],
                         "release": "2019, ongoing" if k % 3 == 0 else f"{2010 + k} to {2014 + k}"},
            "hints": {
                "cover": {"cost": 50, "url": cover},
                "summary": {"cost": 100, "text": f"[character] has spent years hiding a secret. When [title] begins, a chance meeting with [character] changes everything.\n\nFake summary #{k + 1} for testing."},
                "tags": {"cost": 75, "items": [{"name": "Fake Tag A", "rank": 91, "spoiler": False},
                                               {"name": "Big Twist", "rank": 84, "spoiler": True},
                                               {"name": "Fake Tag B", "rank": 77, "spoiler": False},
                                               {"name": "Fake Tag C", "rank": 66, "spoiler": False},
                                               {"name": "Fake Tag D", "rank": 60, "spoiler": False}]},
                "creators": {"cost": 50, "items": [{"name": f"Author {k + 1}", "native": None, "role": "Story & Art"}]},
                "characters": None if k == 4 else {"cost": 75, "main": [{"name": f"Hero {k + 1}", "image": None}, {"name": f"Rival {k + 1}", "image": None}],
                                                   "supporting": [{"name": f"Friend {k + 1}", "image": None}]},
                "reviews": [] if tier in ("hard", "random") else [
                    {"cost": 50, "summary": "A slow start that pays off", "excerpt": "I almost dropped [title] after the first arc, but [character] grows on you...", "score": 85, "upvotes": 40, "url": None},
                    {"cost": 50, "summary": "Overrated", "excerpt": "The art is great but the pacing in the middle is rough.", "score": 60, "upvotes": 12, "url": None},
                ],
            },
            "answer": {"title": en, "romaji": romaji, "native": None, "url": "https://anilist.co",
                       "cover": cover, "accepted_aliases": [en, romaji], "mangadex_id": None},
            "review_notes": [],
        })
    day = {"date": FAKE_DATE, "number": 0, "reviewed": True, "start_points": 1000, "_fake": True, "titles": titles}
    with open(day_path(FAKE_DATE), "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, indent=1)
    print(f"wrote {day_path(FAKE_DATE)}")
    print("answers:", ", ".join(t[0] for t in FAKE_TITLES))
    print(f"play it: python3 scripts/dev.py serve --date {FAKE_DATE}")
    print("(docs/days/2000-01-01.json is in .gitignore so it never goes live)")


# --------------------------------------------------------------------- check --
COMMON = set("""the a an of and in on to no wa ga wo ni de na to is be my your his her its our their this that
with from for at by as or not are was were will can love life world story new girl boy man woman king queen
lord god demon hero master level return reborn after before one two three ghost dead live""".split())


def norm(text):
    import unicodedata
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", t)).strip()


def leak_scan(text, aliases):
    """Words or phrases from the answer's aliases that still appear in hint text."""
    found = set()
    nt = f" {norm(text)} "
    for a in aliases:
        na = norm(a)
        if len(na) >= 4 and f" {na} " in nt:
            found.add(a)
        for w in na.split():
            if len(w) >= 5 and w not in COMMON and f" {w} " in nt:
                found.add(w)
    return sorted(found)


def cmd_check(args):
    date = args.date or str(today())
    day, path = load_day(date)
    problems = 0
    print(f"{path}\n#{day.get('number')}  {day['date']}  reviewed={day.get('reviewed')}  titles={len(day['titles'])}\n")
    if len(day["titles"]) != 10:
        print(f"!! expected 10 titles, found {len(day['titles'])}")
        problems += 1
    ids = [t["anilist_id"] for t in day["titles"]]
    if len(set(ids)) != len(ids):
        print("!! duplicate titles in this day")
        problems += 1

    for i, t in enumerate(day["titles"], 1):
        a, hn, rv = t["answer"], t["hints"], t["revealed"]
        print(f"{i:>2}. {a['title']}  [{t['tier']}, {t['source']}, pop {rv.get('popularity') or 0:,}]  {a.get('url') or ''}")
        avail = {
            "cover": bool((hn.get("cover") or {}).get("url")),
            "summary": bool((hn.get("summary") or {}).get("text")),
            "tags": bool((hn.get("tags") or {}).get("items")),
            "creators": bool((hn.get("creators") or {}).get("items")),
            "characters": bool(hn.get("characters") and ((hn["characters"].get("main") or hn["characters"].get("supporting")))),
            "reviews": len(hn.get("reviews") or []),
        }
        print("    hints: " + "  ".join(f"{k}:{'yes' if v is True else 'NO' if v is False else v}" for k, v in avail.items()))
        print(f"    accepted: {', '.join(a['accepted_aliases'][:6])}{' ...' if len(a['accepted_aliases']) > 6 else ''}")
        if not a["accepted_aliases"]:
            print("    !! no accepted aliases: nobody can get this right")
            problems += 1

        texts = [("summary", (hn.get("summary") or {}).get("text") or "")]
        for n, r in enumerate(hn.get("reviews") or [], 1):
            texts.append((f"review {n}", f"{r.get('summary') or ''}\n{r.get('excerpt') or ''}"))
        for label, text in texts:
            leaks = leak_scan(text, a["accepted_aliases"])
            if leaks:
                print(f"    !! possible leak in {label}: {', '.join(leaks)}")
                problems += 1
        if t.get("review_notes"):
            print(f"    note: unredacted names in text: {', '.join(t['review_notes'])}")
        if args.full:
            for label, text in texts:
                if text.strip():
                    print(f"    --- {label} ---")
                    for line in text.strip().splitlines():
                        print(f"      {line}")
        print()

    print(f"{problems} problem(s) flagged." + ("" if args.full else " Add --full to print every summary and review."))
    if not day.get("reviewed"):
        print(f"When it looks good: python3 scripts/dev.py approve {date}")


def cmd_approve(args):
    day, path = load_day(args.date)
    day["reviewed"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, indent=1)
    print(f"{args.date} marked reviewed")


# ------------------------------------------------------------------- rebuild --
def cmd_rebuild(args):
    import build_day
    date = dt.date.fromisoformat(args.date)
    build_day.build_day(date, args.data, force=True, days_dir=DAYS, same_titles=args.same_titles)
    print(f"\ncheck it: python3 scripts/dev.py check {date}")
    print(f"play it:  http://localhost:8000/?date={date}&reset")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the site locally")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--date", help="open this date in the browser")
    s.add_argument("--no-open", action="store_true", help="don't open a browser")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("fake", help=f"write a fake practice day for {FAKE_DATE}")
    s.set_defaults(func=cmd_fake)

    s = sub.add_parser("check", help="review a day file")
    s.add_argument("date", nargs="?", help="YYYY-MM-DD (default: today)")
    s.add_argument("--full", action="store_true", help="print every summary and review")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("approve", help='set "reviewed": true on a day')
    s.add_argument("date")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("rebuild", help="rebuild one day (overwrites it)")
    s.add_argument("date")
    s.add_argument("--same-titles", action="store_true", help="keep the same 10 titles")
    s.add_argument("--data", default=DATA)
    s.set_defaults(func=cmd_rebuild)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
