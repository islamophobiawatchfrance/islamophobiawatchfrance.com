#!/usr/bin/env python3
"""
IWF Wire Runner - runs every 3 hours
Fetches new stories, filters for relevance, queues wire posts for Bluesky, updates website wire feed
Does NOT draft full articles (that is run.py's job at 7am and 6pm)

Run with: python3 run_wire.py
"""

import datetime
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

import anthropic
from dotenv import load_dotenv

import iwf_aggregator
import post_wire_stories
import publisher
from drafter import save_wire, translate_to_english, fetch_all_stories
from relevance_filter import score_relevance, route, apply_score

load_dotenv(os.path.join(HERE, ".env"))

WIRE_FILES = ["wire.json", "index.html", "news.html"]


def _known_urls() -> set:
    """URLs already on the website wire or in the Bluesky wire queue."""
    urls = {p["url"] for p in post_wire_stories.load_wire_queue()["posts"]}
    try:
        with open("wire.json", "r", encoding="utf-8") as f:
            urls |= {it.get("url") for it in json.load(f).get("items", [])}
    except (OSError, ValueError):
        pass
    return urls


def _push_wire_update() -> None:
    """Commit and push only the wire files, so the live site's wire section updates."""
    def _git(*args):
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)

    if not _git("status", "--porcelain", "--", *WIRE_FILES).stdout.strip():
        print("  Website wire: no changes to push.")
        return
    _git("add", "--", *WIRE_FILES)
    commit = _git("commit", "-m", f"Wire update {datetime.datetime.now():%Y-%m-%d %H:%M}", "--", *WIRE_FILES)
    if commit.returncode != 0:
        print(f"  [WARNING] Wire commit failed: {commit.stderr.strip() or commit.stdout.strip()}")
        return
    push = _git("push")
    if push.returncode == 0:
        print("  Website wire: pushed to GitHub (live in ~60 seconds).")
    else:
        print(f"  [WARNING] Wire push failed (committed locally): {push.stderr.strip()}")


def main() -> None:
    print(f"\n=== IWF Wire Runner === {datetime.datetime.now():%Y-%m-%d %H:%M}\n")

    # The full pipeline (run.py) also rewrites wire.json — if it's mid-run,
    # just post anything already due and leave fetching to the next wire run.
    lock = os.path.join(HERE, "pipeline.lock")
    if os.path.exists(lock) and (datetime.datetime.now().timestamp() - os.path.getmtime(lock)) < 7200:
        print("  Full pipeline is running — skipping fetch this time, posting due wire stories only.")
        post_wire_stories.print_summary(post_wire_stories.run())
        return

    stories = fetch_all_stories()
    stats = iwf_aggregator.fetch_all_stories.last_stats

    counts = {"reject": 0, "wire": 0, "full": 0}
    relevant = []
    for story in stories:
        result = score_relevance(story, story)
        apply_score(story, result)
        bucket = route(result["score"])
        counts[bucket] += 1
        if bucket != "reject":
            relevant.append(story)

    print(f"  Sources polled: {stats['sources_polled']}")
    print(f"  Raw stories found: {stats['raw']}")
    print(f"  Relevance filtered: {counts['reject']} rejected, {counts['wire']} wire-only, "
          f"{counts['full']} full pipeline\n")

    # Only translate stories we haven't already handled on an earlier run
    known = _known_urls()
    new = [s for s in relevant if s["url"] not in known]
    if new:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        for s in new:
            s["title_original"] = s["title"]
            s["title"]   = translate_to_english(client, s["title"])
            s["summary"] = translate_to_english(client, s.get("summary") or "")

    post_wire_stories.add_to_wire_queue(new)
    save_wire(new, datetime.datetime.now())

    summary = post_wire_stories.run()
    post_wire_stories.print_summary(summary)

    try:
        publisher.update_wire(HERE)
        _push_wire_update()
    except Exception as e:
        print(f"  [WARNING] Website wire update failed: {e}")

    print(f"\n  Done: {len(new)} new relevant stor{'y' if len(new) == 1 else 'ies'}, "
          f"{len(summary['posted'])} posted to Bluesky, {len(summary['scheduled'])} scheduled.\n")


if __name__ == "__main__":
    main()
