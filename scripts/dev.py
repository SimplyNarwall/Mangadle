#!/usr/bin/env python3
"""
mangadle dev tools. Run from anywhere; paths default to this repo.

  python3 scripts/dev.py serve                  run the site at http://localhost:8000
  python3 scripts/dev.py fake                   make a fake practice day (no internet needed)
  python3 scripts/dev.py check 2026-11-11       review a day: hints, leaks, review notes
  python3 scripts/dev.py rebuild 2026-11-11     re-pick and rebuild a day (also clears its shared stats)
  python3 scripts/dev.py rebuild 2026-11-11 --same-titles   keep the picks, refresh hints/redaction
  python3 scripts/dev.py check 2026-11-11 --blind         same, but spoiler-free (counts only)
  python3 scripts/dev.py check 2026-11-11 --blind --fix   redact flagged words without showing them
  python3 scripts/dev.py approve 2026-11-11     mark a day "reviewed": true (optional; the site ignores it)
  python3 scripts/dev.py shuffle --upcoming      mix up the title order of days not played yet
  python3 scripts/dev.py audit                  after re-processing the snapshot: find days to rebuild
  python3 scripts/dev.py renumber               after changing LAUNCH_DATE in build_day.py
  python3 scripts/dev.py index                  rebuild the past days list

Browser helpers (add to the URL while serving):
  ?date=2026-11-11       play any date
  ?reset                 wipe your progress for that date and start over
  ?preview               allow playing a date that hasn't happened yet
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
# Fake and test days live here instead (gitignored). `serve` also serves them under /days/.
DEV_DAYS = os.path.join(ROOT, ".dev", "days")
DATA = os.path.join(ROOT, "data")
SITE_TZ = ZoneInfo("America/Chicago")
FAKE_DATE = "2000-01-01"

sys.path.insert(0, HERE)


def today():
    return dt.datetime.now(SITE_TZ).date()


def day_path(date):
    """The real day file if it exists, otherwise the dev/test copy."""
    real = os.path.join(DAYS, f"{date}.json")
    dev = os.path.join(DEV_DAYS, f"{date}.json")
    return dev if not os.path.exists(real) and os.path.exists(dev) else real


def load_day(date):
    path = day_path(date)
    if not os.path.exists(path):
        raise SystemExit(f"no day file for {date} in docs/days or .dev/days")
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


# --------------------------------------------------------------------- serve --
class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # /days/<file> falls back to .dev/days/<file>, so fake and test days are
        # playable locally without ever being written into docs/.
        real = super().translate_path(path)
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/days/") and not os.path.exists(real):
            dev = os.path.join(DEV_DAYS, os.path.basename(clean))
            if os.path.exists(dev):
                return dev
        return real

    # Explicit types: if Python guesses wrong (e.g. text/plain for .css), browsers
    # silently refuse to apply the stylesheet or run the scripts.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        "": "application/octet-stream",
    }

    def guess_type(self, path):
        ext = os.path.splitext(path)[1].lower()
        return self.extensions_map.get(ext) or super().guess_type(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_request(self, code="-", size="-"):
        code = int(code) if str(code).isdigit() else code
        mark = "  " if isinstance(code, int) and code < 400 else "!!"
        path = self.path.split("?", 1)[0]
        ctype = ""
        if path.endswith((".css", ".js", ".json", ".html")) or path.endswith("/"):
            ctype = f"  ({self.guess_type(path if not path.endswith('/') else path + 'index.html').split(';')[0]})"
        print(f"{mark} {code} {self.command} {self.path}{ctype}", flush=True)

    def log_message(self, fmt, *args):
        pass  # log_request above covers everything useful


def cmd_serve(args):
    handler = functools.partial(NoCacheHandler, directory=DOCS)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    base = f"http://localhost:{args.port}/"
    def list_days(folder):
        return sorted(f[:-5] for f in os.listdir(folder) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f)) if os.path.isdir(folder) else []
    days = list_days(DAYS)
    dev_days = list_days(DEV_DAYS)
    print(f"serving docs/ at {base}  (Ctrl+C to stop)")
    print(f"  today ({today()}): {base}")
    for d in days[-8:]:
        print(f"  {d}: {base}?date={d}")
    for d in dev_days:
        print(f"  {d} (dev/test): {base}?date={d}")
    if not days and not dev_days:
        print("  no day files yet; try: python3 scripts/dev.py fake")
    print(f"  add &reset to any URL to wipe your progress for that day")
    print("requests are logged below; lines marked !! are errors")
    if not args.no_open:
        webbrowser.open(base + (f"?date={args.date}" if args.date else ""))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


# ---------------------------------------------------------------------- fake --
FAKE_TITLES = [
    ("Moon Rabbit Swordsman", "Tsuki Usagi no Kenshi", "manga", "possible", ["Action", "Fantasy"], "Shounen"),
    ("The Tower Climber's Ledger", "Tap Deungban Jangbu", "manhwa", "possible", ["Action", "Adventure"], None),
    ("Bakery at the End of the World", "Sekai no Hate no Pan-ya", "manga", "possible", ["Slice of Life", "Drama"], "Seinen"),
    ("Return of the 9th Mercenary", "Gubeonjjae Yongbyeong-ui Gwihwan", "manhwa", "possible", ["Action", "Fantasy"], None),
    ("Paper Crane Detective", "Orizuru Tantei", "manga", "not_possible", ["Mystery"], "Josei"),
    ("Jade Pavilion Cultivator", "Yu Ge Xiu Shi", "manhua", "not_possible", ["Action", "Fantasy"], None),
    ("Last Train to Nowhere", "Shuuden no Saki", "manga", "possible", ["Psychological", "Drama"], "Seinen"),
    ("My Rival Is a Vending Machine", "Raibaru wa Jihanki", "manga", "random", ["Comedy", "Romance"], None),
    ("Kaiju Club No. 3", "Kaijuu-bu San-gou", "manga", "possible", ["Comedy", "Sci-Fi"], "Shounen"),
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
    os.makedirs(DEV_DAYS, exist_ok=True)
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
                "reviews": [] if tier in ("not_possible", "random") else [
                    {"cost": 50, "summary": "A slow start that pays off", "excerpt": "I almost dropped [title] after the first arc, but [character] grows on you...", "score": 85, "upvotes": 40, "url": None},
                    {"cost": 50, "summary": "Overrated", "excerpt": "The art is great but the pacing in the middle is rough.", "score": 60, "upvotes": 12, "url": None},
                ],
            },
            "answer": {"title": en, "romaji": romaji, "native": None, "url": "https://anilist.co",
                       "cover": cover, "accepted_aliases": [en, romaji], "mangadex_id": None},
            "review_notes": [],
        })
    day = {"date": FAKE_DATE, "number": 0, "reviewed": True, "start_points": 1000, "_fake": True, "titles": titles}
    out = os.path.join(DEV_DAYS, f"{FAKE_DATE}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, indent=1)
    print(f"wrote {out}")
    print("answers:", ", ".join(t[0] for t in FAKE_TITLES))
    print(f"play it: python3 scripts/dev.py serve --date {FAKE_DATE}")
    print("(it's in .dev/, which git ignores, so it never goes live)")


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
    text = re.sub(r"\[(title|character|spoiler)\]", " ", text or "")  # our own placeholders aren't leaks
    nt = f" {norm(text)} "
    for a in aliases:
        na = norm(a)
        if len(na) >= 4 and f" {na} " in nt:
            found.add(a)
        for w in na.split():
            if len(w) >= 5 and w not in COMMON and f" {w} " in nt:
                found.add(w)
    return sorted(found)


def hint_texts(t):
    """(label, getter, setter) for every redactable text field in a title's hints."""
    hn = t["hints"]
    out = []
    if (hn.get("summary") or {}).get("text"):
        out.append(("summary", hn["summary"], "text"))
    for n, r in enumerate(hn.get("reviews") or [], 1):
        for field in ("summary", "excerpt"):
            if r.get(field):
                out.append((f"review {n}", r, field))
    return out


def redact_words(text, words, label):
    for w in sorted(words, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(w) + r"(?!\w)", label, text, flags=re.I)
    return text


def cmd_check(args):
    date = args.date or str(today())
    day, path = load_day(date)
    if args.fix and not args.blind:
        raise SystemExit("--fix only works with --blind (without --blind, read the leaks and edit by hand)")
    if args.blind:
        return check_blind(day, path, date, args.fix)

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


def check_blind(day, path, date, fix):
    """Spoiler-free check: counts only, never prints titles, names, or text."""
    print(f"#{day.get('number')}  {date}  ({len(day['titles'])} titles)  spoiler-free check\n")
    total = fixed = 0
    for i, t in enumerate(day["titles"], 1):
        aliases = t["answer"]["accepted_aliases"]
        notes = t.get("review_notes") or []
        issues = []
        for label, holder, field in hint_texts(t):
            leaks = leak_scan(holder[field], aliases)
            note_hits = [n for n in notes if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", holder[field])]
            if leaks or note_hits:
                issues.append(f"{label} ({len(leaks) + len(note_hits)})")
                if fix:
                    holder[field] = redact_words(holder[field], leaks, "[title]")
                    # names skipped by auto-redaction: only replace capitalized uses, so "hope" the word survives
                    for n in note_hits:
                        holder[field] = re.sub(r"(?<!\w)" + re.escape(n) + r"(?!\w)", "[character]", holder[field])
                    fixed += 1
        if not t["answer"]["accepted_aliases"]:
            issues.append("no accepted answers (unguessable)")
        missing = [k for k in ("cover", "summary", "tags", "creators", "characters")
                   if not (t["hints"].get(k) and (t["hints"][k].get("url") or t["hints"][k].get("text")
                           or t["hints"][k].get("items") or t["hints"][k].get("main") or t["hints"][k].get("supporting")))]
        extra = f"   (no {', '.join(missing)})" if missing else ""
        status = "possible leaks in " + ", ".join(issues) if issues else "ok"
        print(f"  title {i:>2} [{t.get('tier', '?')}]: {status}{extra}")
        total += len(issues)
    if fix and fixed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(day, f, ensure_ascii=False, indent=1)
        print(f"\nredacted {fixed} field(s) in place. Some fixes may be over-eager (a common word that's also")
        print("in the title), which only makes that hint a little vaguer.")
    elif total:
        print(f"\n{total} field(s) may give an answer away. Options:")
        print(f"  python3 scripts/dev.py check {date} --blind --fix     redact them automatically, unseen")
        print(f"  python3 scripts/dev.py rebuild {date}                 re-pick the whole day")
    else:
        print("\nnothing flagged.")


def cmd_approve(args):
    day, path = load_day(args.date)
    day["reviewed"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, indent=1)
    print(f"{args.date} marked reviewed")


def cmd_shuffle(args):
    """Randomly reorder each day's titles in place. Spoiler-free: prints nothing about them."""
    import random
    dates = list(args.dates)
    if args.upcoming:
        start = today() + dt.timedelta(days=0 if args.include_today else 1)
        dates += [f[:-5] for f in sorted(os.listdir(DAYS))
                  if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f) and f[:-5] >= str(start)]
    if not dates:
        raise SystemExit("give dates, or --upcoming for every day after today")
    for date in dict.fromkeys(dates):
        day, path = load_day(date)
        if date <= str(today()) and not args.force:
            print(f"{date}: skipped (today or earlier; progress saved in browsers is tied to title order). "
                  f"Use --force if nobody has played it yet.")
            continue
        random.shuffle(day["titles"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(day, f, ensure_ascii=False, indent=1)
        print(f"{date}: shuffled")


def cmd_audit(args):
    """Spoiler-free: find built days with picks the current snapshot no longer allows."""
    import build_day as bd
    snap_path = os.path.join(args.data, "snapshot.json")
    if not os.path.exists(snap_path):
        raise SystemExit(f"no snapshot at {snap_path}")
    eligible = {r["anilist_id"] for r in bd.load_json(snap_path, []) if r["accepted_aliases"]}
    start = str(today()) if not args.all else "0000"
    flagged = []
    for name in sorted(os.listdir(DAYS)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", name) or name[:10] < start:
            continue
        day = bd.load_json(os.path.join(DAYS, name), {})
        # random-tier picks were never in the snapshot, so they can't go stale
        stale = [i for i, t in enumerate(day.get("titles", []), 1)
                 if t.get("tier") != "random" and t["anilist_id"] not in eligible]
        status = f"{len(stale)} title(s) no longer eligible (positions {', '.join(map(str, stale))})" if stale else "ok"
        print(f"  {name[:10]}: {status}")
        if stale:
            flagged.append(name[:10])
    if flagged:
        print("\nRebuild those days (prints nothing about the new titles):")
        for d in flagged:
            print(f"  python3 scripts/dev.py rebuild {d}")
        if str(today()) in flagged:
            print("Today's puzzle is in the list; rebuilding it resets anyone's progress on it.")
    else:
        print("\nall good.")


def cmd_renumber(args):
    import build_day as bd
    hist_path = os.path.join(args.data, "history.json")
    history = bd.load_json(hist_path, [])
    for h in history:
        h["number"] = bd.puzzle_number(dt.date.fromisoformat(h["date"]))
    if history:
        bd.save_json(hist_path, history)
    changed = 0
    for name in sorted(os.listdir(DAYS)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", name):
            continue
        p = os.path.join(DAYS, name)
        day = bd.load_json(p, {})
        if day.get("_fake"):
            continue
        n = bd.puzzle_number(dt.date.fromisoformat(name[:-5]))
        if day.get("number") != n:
            day["number"] = n
            bd.save_json(p, day)
            changed += 1
    entries = bd.write_index(DAYS)
    print(f"launch date {bd.LAUNCH_DATE}: renumbered {changed} day file(s); {len(entries)} day(s) listed on the past days page")
    early = [e for e in sorted(os.listdir(DAYS)) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", e) and e[:10] < str(bd.LAUNCH_DATE) and e != f"{FAKE_DATE}.json"]
    if early:
        print(f"{len(early)} day file(s) are before launch: hidden from past days, but still playable by URL. Delete them if unwanted.")


def cmd_index(args):
    import build_day as bd
    print(f"{len(bd.write_index(DAYS))} day(s) in docs/days/index.json")


# ------------------------------------------------------------------- rebuild --
def reset_remote_stats(date):
    """Clear a day's shared stats on the worker. Needs MANGADLE_WORKER and MANGADLE_TOKEN."""
    import urllib.request
    worker, token = os.environ.get("MANGADLE_WORKER"), os.environ.get("MANGADLE_TOKEN")
    if not worker or not token:
        print(f"  stats not reset: set MANGADLE_WORKER and MANGADLE_TOKEN, then run "
              f"python3 scripts/dev.py reset-stats {date}")
        return False
    req = urllib.request.Request(
        worker.rstrip("/") + "/results/reset", data=json.dumps({"date": str(date)}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "mangadle-dev/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read().decode())
        print(f"  stats reset for {date} ({out.get('deleted', 0)} result(s) cleared)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  stats not reset ({e}); retry with: python3 scripts/dev.py reset-stats {date}")
        return False


def cmd_reset_stats(args):
    reset_remote_stats(dt.date.fromisoformat(args.date))


def cmd_rebuild(args):
    import build_day
    date = dt.date.fromisoformat(args.date)
    build_day.build_day(date, args.data, force=True, days_dir=DAYS, same_titles=args.same_titles)
    if not args.same_titles:
        # New titles: old results for this date no longer mean anything. (Players' saved
        # progress resets on its own; the site notices the titles changed.)
        reset_remote_stats(date)
    print(f"\ncheck it: python3 scripts/dev.py check {date}")
    print(f"play it:  http://localhost:8000/?date={date}&reset" + ("&preview" if date > today() else ""))


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
    s.add_argument("--blind", action="store_true", help="spoiler-free: counts only, no titles or text")
    s.add_argument("--fix", action="store_true", help="with --blind: redact flagged words in place")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("approve", help='set "reviewed": true on a day')
    s.add_argument("date")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("shuffle", help="randomly reorder titles within days (spoiler-free)")
    s.add_argument("dates", nargs="*", help="YYYY-MM-DD dates to shuffle")
    s.add_argument("--upcoming", action="store_true", help="every built day after today")
    s.add_argument("--include-today", action="store_true", help="with --upcoming, include today")
    s.add_argument("--force", action="store_true", help="allow today or earlier (only if nobody has played it)")
    s.set_defaults(func=cmd_shuffle)

    s = sub.add_parser("audit", help="find built days with titles the snapshot no longer allows (spoiler-free)")
    s.add_argument("--data", default=DATA)
    s.add_argument("--all", action="store_true", help="include past days too")
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("renumber", help="recount puzzle numbers after changing LAUNCH_DATE")
    s.add_argument("--data", default=DATA)
    s.set_defaults(func=cmd_renumber)

    s = sub.add_parser("index", help="regenerate docs/days/index.json")
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("reset-stats", help="clear a day's shared stats on the worker")
    s.add_argument("date")
    s.set_defaults(func=cmd_reset_stats)

    s = sub.add_parser("rebuild", help="rebuild one day (overwrites it)")
    s.add_argument("date")
    s.add_argument("--same-titles", action="store_true", help="keep the same 10 titles")
    s.add_argument("--data", default=DATA)
    s.set_defaults(func=cmd_rebuild)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
