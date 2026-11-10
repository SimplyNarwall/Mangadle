#!/usr/bin/env python3
"""
Pull new player suggestions from the mangadle worker into data/suggestions.json,
then mark them as used on the worker so they aren't pulled twice.

Usage:
    MANGADLE_WORKER=https://mangadle-api.you.workers.dev MANGADLE_TOKEN=... \
        python scripts/sync_suggestions.py --data data
"""
import argparse
import json
import os
import urllib.request

UA = "mangadle-sync/1.0"


def call(url, token, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": UA},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--worker", default=os.environ.get("MANGADLE_WORKER"))
    ap.add_argument("--token", default=os.environ.get("MANGADLE_TOKEN"))
    args = ap.parse_args()
    if not args.worker or not args.token:
        raise SystemExit("set MANGADLE_WORKER and MANGADLE_TOKEN (or pass --worker/--token)")

    worker = args.worker.rstrip("/")
    new = call(f"{worker}/suggestions", args.token)["suggestions"]
    path = os.path.join(args.data, "suggestions.json")
    queue = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    have = {q["anilist_id"] for q in queue}
    added = 0
    for s in new:
        if s["anilist_id"] not in have:
            queue.append({"anilist_id": s["anilist_id"], "by": s.get("note") or "player"})
            have.add(s["anilist_id"])
            added += 1
    os.makedirs(args.data, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=1)
    if new:
        call(f"{worker}/suggestions/used", args.token, {"ids": [s["id"] for s in new]})
    print(f"pulled {len(new)} suggestion(s), {added} new; queue now has {len(queue)}")


if __name__ == "__main__":
    main()
