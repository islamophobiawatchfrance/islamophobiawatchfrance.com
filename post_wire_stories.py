#!/usr/bin/env python3
"""
IWF Wire Poster
---------------
Posts queued wire stories (relevance 6-7) from wire_queue.json to Bluesky.

- Only posts inside Paris peak windows (07-09, 12-13, 19-21)
- Spaces posts at least 60 minutes apart, across runs
- Anything not due yet gets a scheduled_for time stored in wire_queue.json
- Max 2 posts containing a question per day
- Skips repeats: a story already posted in the past week (often the same
  news from another outlet) only goes out again if it adds a genuinely
  new development; when several outlets queue the same news, the best
  source is posted

Run by run_wire.py (every 3 hours) and by com.iwf.wire.poster at the
start of each peak window. Can also be run by hand:
    python3 post_wire_stories.py
"""

import datetime
import fcntl
import hashlib
import json
import os

import bluesky_poster
import queue_store
import story_dedupe
from relevance_filter import passes_geo_rule

HERE            = os.path.dirname(os.path.abspath(__file__))
WIRE_QUEUE_FILE = os.path.join(HERE, "wire_queue.json")
LOCK_FILE       = os.path.join(HERE, "wire_queue.lock")

SPACING        = datetime.timedelta(minutes=60)
DUE_TOLERANCE  = datetime.timedelta(minutes=5)   # launchd may fire a little late/early
MAX_STORY_AGE  = datetime.timedelta(hours=72)    # don't post stale news
KEEP_POSTED    = datetime.timedelta(days=7)
MAX_ATTEMPTS   = 3
REPEAT_WINDOW  = datetime.timedelta(days=7)      # compare new stories with what was posted this long ago

REPEAT_SYSTEM_PROMPT = """
You decide whether queued news items for the Islamophobia Watch France (IWF) Bluesky wire add anything new.

ALREADY POSTED lists what IWF has posted, or is about to post, in the past week. CANDIDATES are new items, often the same news from different outlets.

For each candidate decide:
- "repeat": it reports the same development as an ALREADY POSTED item or as another candidate: the same event, statement, ruling or decision, even from a different outlet, in different words, or with reactions, commentary, analysis or minor extra detail.
- "new": it is meaningfully different coverage: a different story, or a genuinely new development in a story already posted (a charge or indictment, a ruling or verdict, a new incident, a new official decision, or a significant new fact that changes the story).

When in doubt, choose "repeat".

Respond in JSON only:
{"candidates": [{"id": "n1", "verdict": "new", "same_as": null, "reason": "one short sentence"}, {"id": "n2", "verdict": "repeat", "same_as": "p4", "reason": "one short sentence"}]}
"""


# =============================================================
# WIRE QUEUE FILE
# =============================================================

def load_wire_queue() -> dict:
    if os.path.exists(WIRE_QUEUE_FILE):
        try:
            with open(WIRE_QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["posts"] = [p for p in data.get("posts", []) if isinstance(p, dict) and p.get("url")]
            return data
        except (OSError, ValueError) as e:
            print(f"  [WARNING] wire_queue.json unreadable ({e}) — starting a new one")
    return {"generated": datetime.datetime.now().isoformat(), "posts": []}


def save_wire_queue(data: dict) -> None:
    data["generated"] = datetime.datetime.now().isoformat()
    tmp = WIRE_QUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, WIRE_QUEUE_FILE)


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def add_to_wire_queue(stories: list) -> int:
    """
    Adds stories to wire_queue.json (skipping URLs already queued), and
    prunes old entries. Returns how many were added.
    """
    with _lock() as locked:
        if not locked:
            print("  Wire queue busy (another wire run in progress) — skipping queue update.")
            return 0
        data = load_wire_queue()
        known = {p["url"] for p in data["posts"]}
        now = datetime.datetime.now(datetime.timezone.utc)
        added = 0
        for s in stories:
            url = s.get("url", "")
            if not url or url in known:
                continue
            published = s.get("published")
            if isinstance(published, datetime.datetime):
                published = published.isoformat()
            data["posts"].append({
                "id":                 "wire_" + hashlib.md5(url.encode()).hexdigest()[:10],
                "type":               "wire",
                "title":              s.get("title", ""),
                "source":             s.get("source", ""),
                "url":                url,
                "published":          published or "",
                "summary":            s.get("summary", ""),
                "source_bias":        s.get("source_bias", ""),
                "relevance_score":    s.get("relevance_score"),
                "relevance_category": s.get("relevance_category", ""),
                "relevance_reason":   s.get("relevance_reason", ""),
                "inversion_required": bool(s.get("inversion_required")),
                "geo_scope":          s.get("geo_scope", ""),
                "islamophobia_central": bool(s.get("islamophobia_central")),
                "queued_at":          now.isoformat(),
                "scheduled_for":      None,
                "bluesky_posted":     False,
            })
            known.add(url)
            added += 1

        def _keep(p):
            if p.get("bluesky_posted"):
                posted = _parse(p.get("posted_at"))
                return posted is None or now - posted < KEEP_POSTED
            queued = _parse(p.get("queued_at"))
            return queued is None or now - queued < KEEP_POSTED

        data["posts"] = [p for p in data["posts"] if _keep(p)]
        save_wire_queue(data)
    if added:
        print(f"  Wire queue: {added} new stor{'y' if added == 1 else 'ies'} queued for Bluesky.")
    return added


class _lock:
    """Non-blocking exclusive lock so overlapping runs can't double-post."""
    def __enter__(self):
        self.f = open(LOCK_FILE, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close()


# =============================================================
# SCHEDULING + POSTING
# =============================================================

def _next_slot(after: datetime.datetime) -> datetime.datetime:
    """Earliest time >= after that is inside a Paris peak window."""
    return bluesky_poster.next_window_start(after)


def _drafted_urls() -> set:
    """URLs currently being turned into full articles — those get a thread instead."""
    return {p.get("url") for p in queue_store.load_queue().get("posts", [])}


def _is_eligible(p: dict, now: datetime.datetime) -> bool:
    if p.get("bluesky_posted") or p.get("status") in ("failed", "expired", "skipped"):
        return False
    if not passes_geo_rule(p):
        p["status"] = "skipped"
        p["skip_reason"] = "No France angle (or European story not centrally about Islamophobia)"
        return False
    published = _parse(p.get("published"))
    if published and now - published > MAX_STORY_AGE:
        p["status"] = "expired"
        return False
    return True


def _mark_repeat(p: dict, same_as: dict, reason: str = "") -> None:
    p["repeat_check"] = "repeat"
    p["status"] = "skipped"
    p["repeat_of"] = same_as.get("url", "")
    p["skip_reason"] = f"Repeat of already-covered story: {same_as.get('title', '')[:90]}" + \
                       (f" ({reason})" if reason else "")


def _preferred(items: list, sources: dict) -> dict:
    """Same news from several outlets: the best source, then the highest relevance, then the earliest."""
    return min(items, key=lambda p: (story_dedupe.source_rank(p.get("source", ""), sources),
                                     -(p.get("relevance_score") or 0),
                                     p.get("published", "")))


def drop_repeats(pending: list, posts: list, now: datetime.datetime) -> list:
    """
    Checks pending stories that haven't been checked yet against everything
    posted in the last REPEAT_WINDOW (plus stories already cleared to post)
    and against each other. Repeats are marked skipped. Returns the pending
    stories cleared to post; unchecked ones (if the model call fails) wait
    for the next run.
    """
    unchecked = [p for p in pending if not p.get("repeat_check")]
    if not unchecked:
        return pending
    sources = story_dedupe.load_sources()

    reference = [p for p in posts if p.get("bluesky_posted")
                 and (_parse(p.get("posted_at")) or now) > now - REPEAT_WINDOW]
    reference += [p for p in pending if p.get("repeat_check") == "new"]
    reference += [{"title": p.get("title", ""), "url": p.get("url", ""), "bluesky_text": ""}
                  for p in queue_store.load_queue().get("posts", [])
                  if p.get("type") == "website" and p.get("bluesky_posted")]

    # Stage 1: same link or near-identical title, no model call needed
    for p in unchecked:
        url = story_dedupe.canonical_url(p["url"])
        match = next((r for r in reference if story_dedupe.canonical_url(r.get("url", "")) == url
                      or story_dedupe.titles_match(r.get("title", ""), p.get("title", ""))), None)
        if match:
            _mark_repeat(p, match, "same headline")
    remaining = [p for p in unchecked if not p.get("repeat_check")]
    for i, p in enumerate(remaining):
        twin = next((q for q in remaining[:i] if not q.get("repeat_check")
                     and story_dedupe.titles_match(q.get("title", ""), p.get("title", ""))), None)
        if twin:
            keep = _preferred([twin, p], sources)
            _mark_repeat(twin if keep is p else p, keep, "same headline")
    remaining = [p for p in remaining if not p.get("repeat_check")]

    # Stage 2: one model call for everything left
    if remaining:
        refs = {f"p{i + 1}": r for i, r in enumerate(reference)}
        cands = {f"n{i + 1}": p for i, p in enumerate(remaining)}
        user = "ALREADY POSTED:\n\n" + ("\n\n".join(
            f"id: {rid}\ntitle: {r.get('title', '')}\nposted text: {r.get('bluesky_text', '')}"
            for rid, r in refs.items()) or "(nothing)") + "\n\nCANDIDATES:\n\n" + "\n\n".join(
            f"id: {cid}\nsource: {p.get('source', '')}\npublished: {p.get('published', '')[:16]}\n"
            f"title: {p.get('title', '')}\nsummary: {(p.get('summary') or '')[:300]}"
            for cid, p in cands.items())
        try:
            verdicts = story_dedupe.ask_json(story_dedupe.CLUSTER_MODEL, REPEAT_SYSTEM_PROMPT, user,
                                             max_tokens=3000).get("candidates", [])
        except Exception as e:
            print(f"  [WARNING] Repeat check failed ({e}) — {len(remaining)} stor"
                  f"{'y' if len(remaining) == 1 else 'ies'} held until the next run.")
            verdicts = []

        # Candidates that repeat each other form a group; a group repeating a
        # posted story is all repeats, otherwise its best source goes out.
        group = {cid: cid for cid in cands}

        def root(cid):
            while group[cid] != cid:
                cid = group[cid]
            return cid

        decided, reasons, posted_match = set(), {}, {}
        for v in verdicts:
            cid = v.get("id")
            if cid not in cands:
                continue
            decided.add(cid)
            if v.get("verdict") == "repeat":
                reasons[cid] = str(v.get("reason", ""))
            same_as = v.get("same_as")
            if v.get("verdict") == "repeat" and same_as in refs:
                posted_match[cid] = refs[same_as]
            elif v.get("verdict") == "repeat" and same_as in cands:
                group[root(cid)] = root(same_as)
            elif v.get("verdict") == "repeat":
                posted_match[cid] = {"title": "(unspecified)", "url": ""}

        members = {}
        for cid in decided:
            members.setdefault(root(cid), []).append(cid)
        for ids in members.values():
            hit = next((posted_match[c] for c in ids if c in posted_match), None)
            keep = None if hit else _preferred([cands[c] for c in ids], sources)
            for c in ids:
                if cands[c] is keep:
                    cands[c]["repeat_check"] = "new"
                elif hit:
                    _mark_repeat(cands[c], hit, reasons.get(c, ""))
                else:
                    _mark_repeat(cands[c], keep, f"same news, posting the {keep.get('source', '')} version")

    return [p for p in pending if p.get("repeat_check") == "new"]


def run(now: datetime.datetime | None = None) -> dict:
    """
    Posts every wire story that is due now and (re)schedules the rest.
    Returns {"posted": [...], "scheduled": [...], "failed": [...]}.
    """
    now = (now or datetime.datetime.now(bluesky_poster.PARIS)).astimezone(bluesky_poster.PARIS)
    summary = {"posted": [], "scheduled": [], "failed": [], "repeats": []}

    with _lock() as locked:
        if not locked:
            print("  Another wire posting run is in progress — exiting.")
            return summary

        data = load_wire_queue()
        posts = data["posts"]
        drafted = _drafted_urls()

        for p in posts:
            if not p.get("bluesky_posted") and p.get("url") in drafted:
                p["status"] = "skipped"
                p["skip_reason"] = "Drafted as a full article — gets a thread on approval"

        pending = [p for p in posts if _is_eligible(p, now)]
        cleared = drop_repeats(pending, posts, now)
        summary["repeats"] = [p for p in pending if p.get("repeat_check") == "repeat"]
        pending = cleared
        # Strongest stories first, then newest
        pending.sort(key=lambda p: (p.get("relevance_score") or 0, p.get("published", "")), reverse=True)

        posted_times = [t for t in (_parse(p.get("posted_at")) for p in posts if p.get("bluesky_posted")) if t]
        # Article threads and the weekly thread aren't in the wire queue, so also space from the last post of any kind
        last_any = bluesky_poster.last_post_time()
        if last_any:
            posted_times.append(last_any)
        earliest = max([now] + [t + SPACING for t in posted_times])
        slot = _next_slot(earliest)

        # Bad credentials shouldn't burn through the queue marking stories failed
        if pending and slot <= now + DUE_TOLERANCE and bluesky_poster.authenticate() is None:
            print("  [WARNING] Bluesky login failed — nothing posted. Check BLUESKY_HANDLE / "
                  "BLUESKY_APP_PASSWORD in .env (details in logs/bluesky.log).")
            slot = _next_slot(now + SPACING)

        for p in pending:
            if slot <= now + DUE_TOLERANCE:
                text = bluesky_poster.generate_wire_post(p)
                if (bluesky_poster.has_question(text)
                        and bluesky_poster.get_question_count_today() >= bluesky_poster.MAX_QUESTIONS_PER_DAY):
                    text = bluesky_poster.generate_wire_post(p, allow_question=False)
                if bluesky_poster.is_skip(text):
                    p["status"] = "skipped"
                    p["skip_reason"] = "Post writer found no France / Islamophobia angle"
                    save_wire_queue(data)
                    continue
                if bluesky_poster.post_wire(p, text=text):
                    p["bluesky_posted"] = True
                    p["posted_at"] = datetime.datetime.now(bluesky_poster.PARIS).isoformat()
                    p["scheduled_for"] = None
                    summary["posted"].append(p)
                    slot = _next_slot(now + SPACING)
                else:
                    p["post_attempts"] = p.get("post_attempts", 0) + 1
                    if p["post_attempts"] >= MAX_ATTEMPTS:
                        p["status"] = "failed"
                    summary["failed"].append(p)
                    # Don't burn through the whole queue if Bluesky is down
                    break
                save_wire_queue(data)
            else:
                p["scheduled_for"] = slot.isoformat()
                summary["scheduled"].append(p)
                slot = _next_slot(slot + SPACING)

        save_wire_queue(data)
    return summary


def print_summary(summary: dict) -> None:
    print(f"  Bluesky wire: {len(summary['posted'])} posted, "
          f"{len(summary['scheduled'])} scheduled, {len(summary['failed'])} failed.")
    for p in summary["posted"]:
        print(f"    ✓ posted    {p['title'][:70]}")
    for p in summary["failed"]:
        print(f"    ✗ failed    {p['title'][:70]} (see logs/bluesky.log)")
    for p in summary.get("repeats", []):
        print(f"    ↺ repeat    {p['title'][:70]}\n                {p['skip_reason'][:110]}")
    for p in summary["scheduled"]:
        when = _parse(p["scheduled_for"]).astimezone(bluesky_poster.PARIS).strftime("%a %H:%M")
        print(f"    ⏱ {when}  {p['title'][:70]}")


def main() -> None:
    print(f"=== IWF Wire Poster === {datetime.datetime.now(bluesky_poster.PARIS):%Y-%m-%d %H:%M}")
    print_summary(run())


if __name__ == "__main__":
    main()
