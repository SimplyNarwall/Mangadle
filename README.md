# mangadle

A daily guessing game for manga, manhwa, and manhua. 10 titles a day, one guess each, hints cost points.

```
docs/      the website (GitHub Pages serves this folder)
  days/    one puzzle file per date, made by scripts/build_day.py
  js/config.js   site settings: worker URL, time zone
worker/    Cloudflare Worker + D1 database for shared stats and suggestions
scripts/   Python tools that make the puzzles (standard library only)
data/      snapshot, history, and the suggestion queue
```

## 1. Make the title pool

```
python3 scripts/anilist_snapshot.py --out data
```

Takes a few minutes. Writes `data/snapshot.json`, `data/raw_anilist.json`, and `data/summary.txt` (tier counts). The two big files are in `.gitignore`; re-run this every month or so to pick up new titles.

## 2. Build puzzles

```
python3 scripts/build_day.py --data data --days-dir docs/days --ahead 7
```

Builds today plus the next 6 days, skipping days that already exist. Each run also updates `data/history.json` (so nothing repeats within 50 days) and uses up entries from `data/suggestions.json`.

**Check each day before it goes live.** Open `docs/days/<date>.json` and look at `review_notes` for each title: names that were not redacted automatically but still appear in the summary or reviews. Edit any text that gives the answer away, then set `"reviewed": true`. To redo a day: add `--date 2026-11-14 --force`.

Then commit and push. The site picks up new day files automatically.

## 3. Turn on GitHub Pages

Repo Settings > Pages > Build and deployment: "Deploy from a branch", branch `main`, folder `/docs`.

The game works at this point. Without the worker it just skips shared stats and suggestions.

## 4. Deploy the worker (stats + suggestions)

Needs a free Cloudflare account and Node.js.

```
cd worker
npx wrangler login
npx wrangler d1 create mangadle          # copy the database_id it prints into wrangler.toml
npx wrangler d1 execute mangadle --remote --file=schema.sql
npx wrangler secret put ADMIN_TOKEN      # any long random string; used by sync_suggestions.py
npx wrangler deploy                      # prints your worker URL
```

Then:
- Put the worker URL in `docs/js/config.js` (`workerUrl`).
- In `worker/wrangler.toml`, set `ALLOWED_ORIGIN` to your Pages origin (e.g. `https://yourname.github.io`) and run `npx wrangler deploy` again.

## 5. Pull player suggestions

```
export MANGADLE_WORKER=https://mangadle-api.yourname.workers.dev
export MANGADLE_TOKEN=<your ADMIN_TOKEN>
python3 scripts/sync_suggestions.py --data data
```

Run this before `build_day.py`. Suggested titles fill the next day's slots first; random picks cover whatever tiers are missing.

## Launch date and puzzle numbers

Puzzle #1 is `LAUNCH_DATE` near the top of `scripts/build_day.py` (currently 2026-11-11). Every day's number is counted from it, so to move the launch:

1. Change `LAUNCH_DATE`.
2. Run `python3 scripts/dev.py renumber` to update every day file, `data/history.json`, and the past days list.

Day files dated before the launch stay playable by URL but are hidden from the past days page; delete them if you don't want them.

## Past days

`archive.html` lists every day from launch through today (never future days, even ones already built), with your score for each one played in this browser. The list comes from `docs/days/index.json`, which `build_day.py` rewrites on every build; `python3 scripts/dev.py index` regenerates it by hand.

Future days can't be played on the site unless the URL has `&preview`.

## Spoiler-free checking

The site never reads `"reviewed"`, so days go live as soon as they're pushed whether or not you check them. If you'll be playing too:

```
python3 scripts/dev.py check 2026-11-11 --blind         # counts only: "title 4: possible leaks in review 2 (1)"
python3 scripts/dev.py check 2026-11-11 --blind --fix   # redact flagged words in place without showing them
```

`--fix` replaces leaked title words with [title] and unredacted character names with [character]. Avoid plain `check`, `--full`, and opening day files; they show the answers.

## Testing locally

```
python3 scripts/dev.py fake                  # fake practice day for 2000-01-01, no internet needed
python3 scripts/dev.py serve                 # site at http://localhost:8000, opens your browser
```

While serving, add these to the URL:
- `?date=2026-11-11` plays any date (`?date=2000-01-01` for the fake day)
- `&reset` wipes your progress for that date so you can replay it
- `&preview` lets you play a date that hasn't happened yet
- `&api=http://localhost:8787` points at a local worker instead of the one in `config.js`

Reviewing and fixing a day:

```
python3 scripts/dev.py check 2026-11-11              # hints per title, accepted answers, possible leaks
python3 scripts/dev.py check 2026-11-11 --full       # also print every summary and review
python3 scripts/dev.py rebuild 2026-11-11 --same-titles   # same 10 titles, fresh hints/redaction
python3 scripts/dev.py rebuild 2026-11-11            # new picks (uses the suggestion queue again)
python3 scripts/dev.py approve 2026-11-11            # set "reviewed": true
```

`check` flags any title words that still appear in a summary or review. Some are false alarms (a summary for *Tower of God* can say "tower"), so read them before editing. To fix one by hand, edit the text in `docs/days/<date>.json`; a `--same-titles` rebuild would overwrite your edits.

Local worker (optional):

```
cd worker
npx wrangler d1 execute mangadle --local --file=schema.sql
npx wrangler dev                             # http://localhost:8787
```

## Daily routine

```
python3 scripts/sync_suggestions.py --data data
python3 scripts/build_day.py --data data --days-dir docs/days --ahead 7
python3 scripts/dev.py check --blind --fix   # spoiler-free leak cleanup for today
# then:
git add docs/days data && git commit -m "puzzles" && git push
```

## Notes

- The puzzle date follows `timezone` in `docs/js/config.js` (America/Chicago). `SITE_TZ` in `scripts/build_day.py` must match it.
- Test any date with `?date=2026-11-11` in the URL.
- Day files include the answers, so a player who opens the browser's network tab can see them. Fine for friends; a public version would need the worker to check guesses.
- Cover blur strength is `--cover-blur` in `docs/css/style.css` (default 14px).
