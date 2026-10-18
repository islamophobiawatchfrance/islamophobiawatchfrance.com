#!/usr/bin/env python3
"""
IWF Queue Store
---------------
Shared, defensive read/write helpers for queue.json, used by drafter.py,
app.py and telegram_bot.py so a single bad post (or a half-written file)
can never block the whole queue.

- load_queue()          never raises; a corrupt file is backed up and replaced
- archive_stale_posts() moves posts older than 48 h out of queue.json;
                        pending/rejected/corrupt ones are kept in archive.json
"""

import datetime
import json
import os
import shutil

HERE         = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE   = os.path.join(HERE, "queue.json")
ARCHIVE_FILE = os.path.join(HERE, "archive.json")

MAX_AGE_HOURS = 48


def _empty_queue() -> dict:
    return {"generated": datetime.datetime.now().isoformat(), "posts": []}


def _post_time(post: dict):
    """Aware datetime a post was created, or None if missing/unparseable."""
    raw = post.get("created") or post.get("published_at") or ""
    try:
        dt = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    # drafter.py historically wrote naive local timestamps
    return dt if dt.tzinfo else dt.astimezone()


def load_queue(path: str = QUEUE_FILE) -> dict:
    """
    Load queue.json. Never raises: an unreadable file is copied to
    queue.corrupt-<timestamp>.json and an empty queue is returned, and
    entries that aren't valid post dicts are dropped.
    """
    if not os.path.exists(path):
        return _empty_queue()
    try:
        with open(path, "r", encoding="utf-8") as f:
            queue = json.load(f)
        if not isinstance(queue, dict):
            raise ValueError("queue.json is not a JSON object")
    except (OSError, ValueError) as e:
        backup = path.replace(".json", f".corrupt-{datetime.datetime.now():%Y%m%d-%H%M%S}.json")
        try:
            shutil.copy(path, backup)
            print(f"  [queue] queue.json unreadable ({e}) — backed up to {os.path.basename(backup)}")
        except OSError:
            pass
        return _empty_queue()

    posts = queue.get("posts")
    if not isinstance(posts, list):
        posts = []
    queue["posts"] = [p for p in posts if isinstance(p, dict) and p.get("id")]
    return queue


def save_queue(queue: dict, path: str = QUEUE_FILE) -> None:
    """Atomic write so a crash mid-save can't leave a truncated queue.json."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _append_to_archive(posts: list, reason: str) -> None:
    if not posts:
        return
    archive = {"posts": []}
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
                archive = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  [queue] archive.json unreadable ({e}) — not archiving, posts left in queue")
            raise
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for post in posts:
        entry = dict(post)
        entry["archived_reason"] = reason
        entry["archived_at"] = now
        archive.setdefault("posts", []).append(entry)
    tmp = ARCHIVE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ARCHIVE_FILE)


def archive_stale_posts(max_age_hours: int = MAX_AGE_HOURS, path: str = QUEUE_FILE) -> int:
    """
    Remove every post older than max_age_hours from queue.json. Pending,
    rejected and undatable posts are appended to archive.json first (so the
    dashboard's archive-approve undo path still works); approved posts are
    already in published.json / archive.json and are simply dropped.
    Returns the number of posts removed. Never raises.
    """
    try:
        queue = load_queue(path)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=max_age_hours)
        keep, stale = [], []
        for post in queue["posts"]:
            created = _post_time(post)
            (stale if created is None or created < cutoff else keep).append(post)
        if not stale:
            return 0
        to_archive = [p for p in stale if p.get("status") != "approved"]
        _append_to_archive(to_archive, f"stale_{max_age_hours}h")
        queue["posts"] = keep
        save_queue(queue, path)
        print(f"  [queue] Archived {len(stale)} post(s) older than {max_age_hours}h "
              f"({len(to_archive)} undecided kept in archive.json).")
        return len(stale)
    except Exception as e:
        print(f"  [queue] Stale-post cleanup skipped: {e}")
        return 0
