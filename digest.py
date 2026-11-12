#!/usr/bin/env python3
"""
IWF Weekly Digest
-----------------
Fallback Bluesky post for quiet spells: when a posting slot finds the wire
queue empty and the account has been silent for SILENCE_HOURS, a short
"the week in Islamophobia in France" post is drafted from the past week's
wire posts and sent to Telegram with Approve / Reject buttons. Nothing is
posted until it's approved (telegram_bot.py handles the buttons).

  maybe_propose()   called by post_wire_stories.run() at empty slots
  post(digest_id)   called by telegram_bot.py on Approve

Proposals are stored in digest.json. Preview one by hand (sends nothing):
    python3 digest.py
"""

import asyncio
import datetime
import hashlib
import json
import os
import re

import anthropic
from dotenv import load_dotenv

import bluesky_poster

HERE        = os.path.dirname(os.path.abspath(__file__))
DIGEST_FILE = os.path.join(HERE, "digest.json")
WIRE_QUEUE_FILE = os.path.join(HERE, "wire_queue.json")
load_dotenv(os.path.join(HERE, ".env"))

SILENCE_HOURS  = 24   # only offer a digest after this long with no Bluesky post
COOLDOWN_DAYS  = 7    # at most one posted digest a week
RETRY_HOURS    = 24   # after a reject (or no answer), wait this long before offering again
MIN_STORIES    = 3    # not enough happened for a digest below this
HASHTAGS       = "#IslamophobiaWatchFrance #France"

# Once a week and read by a human before posting, so worth the stronger model
MODEL = "claude-opus-5-5"

DIGEST_SYSTEM_PROMPT = """
You write the weekly round-up post on Bluesky for Islamophobia Watch France (IWF).

You get the wire posts IWF published over the past seven days. Write ONE post summarising the week in Islamophobia and anti-Muslim discrimination in France.

RULES:
- Maximum {limit} characters. Aim well under it: short clauses separated by semicolons
- Open with "This week in France:" then the two or three most significant developments, most important first
- Report what happened THIS week. If an event happened earlier and this week's posts are follow-ups (charges, funerals, petitions, rulings), report the follow-up, not the original event as if it were new
- Only state facts that appear in the posts provided - never add details, numbers or names that are not there
- Neutral, factual wire style. Where an actor made anti-Muslim statements, the actor is the subject
- No links, no source attribution, no questions, no hashtags (they are added automatically)

Return only the post text, nothing else.
"""


# =============================================================
# STATE
# =============================================================

def _load() -> dict:
    try:
        with open(DIGEST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("digests"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"digests": []}


def _save(data: dict) -> None:
    data["digests"] = data["digests"][-20:]
    tmp = DIGEST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DIGEST_FILE)


def _parse(ts):
    try:
        dt = datetime.datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=bluesky_poster.PARIS)


def last_bluesky_post() -> datetime.datetime | None:
    """Time of the most recent successful Bluesky post of any kind, from logs/bluesky.log."""
    try:
        with open(bluesky_poster.LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (WIRE|THREAD|DIGEST|WEEKLY) OK", line)
        if m:
            return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=bluesky_poster.PARIS)
    return None


def _week_posts(now: datetime.datetime) -> list:
    try:
        with open(WIRE_QUEUE_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f).get("posts", [])
    except (OSError, ValueError):
        return []
    cutoff = now - datetime.timedelta(days=7)
    week = [p for p in posts if isinstance(p, dict) and p.get("bluesky_posted")
            and p.get("bluesky_text") and (_parse(p.get("posted_at")) or cutoff) > cutoff]
    week.sort(key=lambda p: p["posted_at"])
    return week


# =============================================================
# DRAFTING
# =============================================================

def _ask(system: str, user: str) -> str:
    response = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")).beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},  # installed SDK predates the fallbacks kwarg
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("digest request was declined by the model")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return text.strip().strip('"').strip()


def generate(now: datetime.datetime | None = None) -> str | None:
    """Drafts the digest text, or None if fewer than MIN_STORIES were posted this week."""
    now = (now or datetime.datetime.now(bluesky_poster.PARIS)).astimezone(bluesky_poster.PARIS)
    week = _week_posts(now)
    if len(week) < MIN_STORIES:
        return None
    user = "IWF wire posts from the past seven days, oldest first:\n\n" + "\n\n".join(
        f"[{_parse(p['posted_at']):%a %d %b}] {p['bluesky_text']}" for p in week)
    # Hashtags are appended in code so trimming an over-long draft can never cut them off
    limit = bluesky_poster.MAX_CHARS - len(HASHTAGS) - 2
    system = DIGEST_SYSTEM_PROMPT.format(limit=limit)
    text = _ask(system, user)
    text = bluesky_poster._fit(text, limit, lambda note: _ask(system + note, user))
    return f"{re.sub(r'\s*#\w+', '', text).strip()}\n\n{HASHTAGS}"


def _should_propose(data: dict, now: datetime.datetime) -> bool:
    last = last_bluesky_post()
    if last and now - last < datetime.timedelta(hours=SILENCE_HOURS):
        return False
    # The Friday "France this week" thread (weekly_digest.py) already covers the week
    try:
        with open(os.path.join(HERE, "weekly_digest_state.json"), "r", encoding="utf-8") as f:
            weeks = json.load(f).get("weeks", {}).values()
    except (OSError, ValueError):
        weeks = []
    for w in weeks:
        posted = _parse(w.get("posted_at"))
        if posted and now - posted < datetime.timedelta(days=COOLDOWN_DAYS):
            return False
    for d in data["digests"]:
        created = _parse(d.get("created_at"))
        posted  = _parse(d.get("posted_at"))
        if posted and now - posted < datetime.timedelta(days=COOLDOWN_DAYS):
            return False
        if created and now - created < datetime.timedelta(hours=RETRY_HOURS):
            return False
    return True


# =============================================================
# TELEGRAM
# =============================================================

async def _send(digest: dict) -> None:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Approve & post", callback_data=f"digest_approve:{digest['id']}"),
        InlineKeyboardButton("✗ Reject", callback_data=f"digest_reject:{digest['id']}"),
    ]])
    async with Bot(token=os.getenv("TELEGRAM_BOT_TOKEN")) as bot:
        await bot.send_message(
            chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            text=(f"🦋 BLUESKY WEEKLY DIGEST\n"
                  f"Nothing new to post for {SILENCE_HOURS}h+ — proposed round-up "
                  f"({len(digest['text'])} chars):\n\n{digest['text']}"),
            reply_markup=markup,
        )


def maybe_propose(now: datetime.datetime | None = None) -> bool:
    """
    If the account has gone quiet, drafts a digest and sends it to Telegram
    for approval. Returns True if a proposal was sent.
    """
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        return False
    now = (now or datetime.datetime.now(bluesky_poster.PARIS)).astimezone(bluesky_poster.PARIS)
    data = _load()
    if not _should_propose(data, now):
        return False
    text = generate(now)
    if not text:
        print("  Digest: account is quiet but too few stories this week for a round-up.")
        return False
    digest = {
        "id":         "digest_" + hashlib.md5(now.isoformat().encode()).hexdigest()[:10],
        "text":       text,
        "created_at": now.isoformat(),
        "status":     "pending",
    }
    asyncio.run(_send(digest))
    data["digests"].append(digest)
    _save(data)
    print("  Digest: weekly round-up sent to Telegram for approval.")
    bluesky_poster._log(f"DIGEST PROPOSED {digest['id']} | {text!r}")
    return True


# =============================================================
# APPROVE / REJECT (called by telegram_bot.py)
# =============================================================

def get(digest_id: str) -> dict | None:
    return next((d for d in _load()["digests"] if d.get("id") == digest_id), None)


def reject(digest_id: str) -> None:
    data = _load()
    for d in data["digests"]:
        if d.get("id") == digest_id:
            d["status"] = "rejected"
    _save(data)


def post(digest_id: str) -> str | None:
    """Posts an approved digest to Bluesky. Returns the post URI, or None on failure."""
    data = _load()
    digest = next((d for d in data["digests"] if d.get("id") == digest_id), None)
    if digest is None or digest.get("status") != "pending":
        return None
    try:
        client = bluesky_poster.authenticate()
        if client is None:
            bluesky_poster._log(f"DIGEST FAILED (not authenticated): {digest_id}")
            return None
        response = client.send_post(bluesky_poster._rich_text(digest["text"]), langs=["en"])
    except Exception as e:
        bluesky_poster._log(f"DIGEST FAILED: {digest_id} | {e}")
        return None
    bluesky_poster._record_post("digest", response.uri)
    bluesky_poster._log(f"DIGEST OK {response.uri} | {digest['text']!r}")
    digest.update(status="posted", uri=response.uri,
                  posted_at=datetime.datetime.now(bluesky_poster.PARIS).isoformat())
    _save(data)
    return response.uri


if __name__ == "__main__":
    print(generate() or "Too few stories posted this week for a digest.")
