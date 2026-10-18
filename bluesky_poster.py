#!/usr/bin/env python3
"""
IWF Bluesky Poster
------------------
Writes and posts to Bluesky for Islamophobia Watch France.

  post_wire(story)          one short wire post for a 6-7 relevance story
  post_article_thread(post) two-post thread when a full article is approved

Credentials come from .env (BLUESKY_HANDLE, BLUESKY_APP_PASSWORD). The login
session is cached in bluesky_session.txt (gitignored) so we don't log in
again for every post. Everything is logged to logs/bluesky.log.

Quick connection test:  python3 bluesky_poster.py
"""

import datetime
import json
import os
import re
from zoneinfo import ZoneInfo

import anthropic
from dotenv import load_dotenv

HERE         = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(HERE, "bluesky_session.txt")
STATS_FILE   = os.path.join(HERE, "bluesky_daily_stats.json")
LOG_FILE     = os.path.join(HERE, "logs", "bluesky.log")
load_dotenv(os.path.join(HERE, ".env"))

MODEL      = "claude-haiku-4-5-20251001"
MAX_CHARS  = 280
PARIS      = ZoneInfo("Europe/Paris")
MAX_QUESTIONS_PER_DAY = 2

# Paris peak posting windows: (start hour, end hour)
PEAK_WINDOWS = [(7, 9), (12, 13), (19, 21)]

WIRE_SYSTEM_PROMPT = """
You write short wire posts for Bluesky for Islamophobia Watch France (IWF).

RULES:
- Maximum 280 characters including hashtags
- Start with the strongest fact or development - no "IWF reports" or "according to"
- Write in wire agency style: neutral, factual, punchy
- No outside links
- Attribution at the very end: "— [Source Name]"
- Select 2-3 hashtags from the story category - make them SEO-relevant and specific to the story

HASHTAG LIBRARY (pick the most relevant 2-3):
General: #IslamophobiaWatchFrance #France #Islamophobia
Policy/Law: #Laicite #FrenchPolitics #HumanRights #Discrimination
Far-right: #RN #Bardella #LePen #FarRight #Identitaire
Community: #FrenchMuslims #MuslimFrance #Islam
Mosque: #Mosque #HateCrime #ReligiousFreedom
Legal: #Justice #CourtsOfFrance
European: #Europe #EuropeanPolitics #AntiMuslimHatred
Local: #Paris #Marseille #Lyon #Toulouse #Strasbourg (only if story is specifically local)

CATEGORY D STORIES (inversion_required=true):
Lead with the actor making the claim, not the claim itself.
Wrong: "Muslims accused of infiltrating schools"
Right: "Valeurs Actuelles claims Muslims infiltrate schools — no evidence provided"

Return only the post text, nothing else.
"""

ARTICLE_POST1_SYSTEM_PROMPT = """
Write the opening post of a Bluesky thread for an IWF original article. This is IWF's own analysis, not a wire relay. Voice should be informed and direct - a journalist who has read the source material and has a view on its significance. Maximum 280 characters. No link. End with a hook that makes people want to read the thread reply.
"""

ARTICLE_POST2_SYSTEM_PROMPT = """
Write the second (reply) post of a Bluesky thread for an Islamophobia Watch France (IWF) original article. The first post has already given the strongest finding - do not repeat it. Add one or two sentences of additional context or analysis: background, what happens next, or why it matters for Muslims in France.

Then end with 2-3 relevant hashtags, always including #IslamophobiaWatchFrance.

Maximum {limit} characters including hashtags. No link (one is added automatically). Return only the post text, nothing else.
"""

NO_QUESTION_RULE = "\n\nIMPORTANT: Do not use any question marks. Write only declarative sentences."

_client   = None
_claude   = None


# =============================================================
# LOGGING + CLAUDE HELPERS
# =============================================================

def _log(message: str) -> None:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    stamp = datetime.datetime.now(PARIS).strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")


def _get_claude():
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _claude


def _ask(system: str, user: str, max_tokens: int = 300) -> str:
    response = _get_claude().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip().strip('"').strip()


def has_question(text: str) -> bool:
    """True if the text asks a question (ignoring any '?' inside URLs)."""
    return "?" in re.sub(r"https?://\S+", "", text)


def _fit(text: str, limit: int, regenerate) -> str:
    """Return text within limit: regenerate once if too long, then trim at a word boundary."""
    if len(text) > limit:
        text = regenerate(f"\n\nYour previous attempt was {len(text)} characters. "
                          f"It MUST be under {limit} characters.")
    if len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return text


# =============================================================
# FUNCTION 1 — AUTHENTICATION
# =============================================================

def authenticate():
    """
    Returns an authenticated atproto Client, or None if credentials are missing
    or login fails. Reuses the cached session string when possible.
    """
    global _client
    if _client is not None:
        return _client

    from atproto import Client, SessionEvent

    handle   = os.getenv("BLUESKY_HANDLE")
    password = os.getenv("BLUESKY_APP_PASSWORD")
    if not handle or not password:
        _log("AUTH FAILED: BLUESKY_HANDLE / BLUESKY_APP_PASSWORD missing from .env")
        return None

    def _save_session(event, session):
        if event in (SessionEvent.CREATE, SessionEvent.REFRESH):
            try:
                with open(SESSION_FILE, "w", encoding="utf-8") as f:
                    f.write(session.export())
                os.chmod(SESSION_FILE, 0o600)
            except OSError as e:
                _log(f"Could not cache session: {e}")

    client = Client()
    client.on_session_change(_save_session)

    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                client.login(session_string=f.read().strip())
            _client = client
            return _client
        except Exception as e:
            _log(f"Cached session rejected ({e}) — logging in again")

    try:
        client.login(handle, password)
        _client = client
        _log(f"Authenticated as {handle}")
        return _client
    except Exception as e:
        _log(f"AUTH FAILED for {handle}: {e}")
        return None


def _rich_text(text: str):
    """Builds a TextBuilder so #hashtags and URLs are clickable on Bluesky."""
    from atproto import client_utils

    builder = client_utils.TextBuilder()
    pos = 0
    for m in re.finditer(r"(https?://\S+)|(#\w+)", text):
        builder.text(text[pos:m.start()])
        if m.group(1):
            url = m.group(1).rstrip(".,)")
            builder.link(url.split("://", 1)[1], url)
            builder.text(m.group(1)[len(url):])
        else:
            builder.tag(m.group(2), m.group(2)[1:])
        pos = m.end()
    builder.text(text[pos:])
    return builder


def _display_length(text: str) -> int:
    """Length as Bluesky shows it (URLs displayed without https://)."""
    return len(re.sub(r"https?://", "", text))


# =============================================================
# FUNCTION 2 — WIRE POST TEXT
# =============================================================

def generate_wire_post(story: dict, allow_question: bool = True) -> str:
    """Writes a <=280 character Bluesky wire post for a story dict."""
    system = WIRE_SYSTEM_PROMPT + ("" if allow_question else NO_QUESTION_RULE)
    user = (
        f"Title: {story.get('title', '')}\n"
        f"Summary: {story.get('summary', '')}\n"
        f"Source: {story.get('source', '')}\n"
        f"Relevance category: {story.get('relevance_category', '')}\n"
        f"inversion_required: {str(bool(story.get('inversion_required'))).lower()}"
    )
    text = _ask(system, user)
    return _fit(text, MAX_CHARS, lambda note: _ask(system + note, user))


# =============================================================
# FUNCTION 3 — ARTICLE THREAD TEXT
# =============================================================

def generate_article_post(post: dict, allow_question: bool = True) -> dict:
    """
    Writes a two-post thread for an approved article.
    Returns {"post1": lede text, "post2": detail text + url + hashtags}.
    """
    url = post.get("published_url") or post.get("url", "")
    rule = "" if allow_question else NO_QUESTION_RULE
    user = (
        f"Article title: {post.get('title', '')}\n\n"
        f"Article text:\n{post.get('draft', '')[:4000]}"
    )

    system1 = ARTICLE_POST1_SYSTEM_PROMPT + rule
    post1 = _ask(system1, user)
    post1 = _fit(post1, MAX_CHARS, lambda note: _ask(system1 + note, user))

    link_line = f"\n\nFull analysis: {url}"
    body_limit = MAX_CHARS - _display_length(link_line)
    system2 = ARTICLE_POST2_SYSTEM_PROMPT.format(limit=body_limit) + rule
    user2 = f"{user}\n\nOpening post already published:\n{post1}"
    body = _ask(system2, user2)
    body = _fit(body, body_limit, lambda note: _ask(system2 + note, user2))

    # Keep hashtags after the link so the post reads: context, link, tags
    tags = " ".join(re.findall(r"#\w+", body))
    context = re.sub(r"\s*#\w+", "", body).strip()
    post2 = f"{context}\n\nFull analysis: {url}" + (f"\n\n{tags}" if tags else "")
    return {"post1": post1, "post2": post2}


# =============================================================
# FUNCTION 4 — POST A WIRE STORY
# =============================================================

def post_wire(story: dict, text: str | None = None) -> bool:
    """
    Posts one wire post to Bluesky. Generates the text unless `text` is given
    (post_wire_stories.py passes pre-checked text for the question limit).
    Returns True on success.
    """
    try:
        if text is None:
            text = generate_wire_post(story, allow_question=get_question_count_today() < MAX_QUESTIONS_PER_DAY)
        client = authenticate()
        if client is None:
            _log(f"WIRE FAILED (not authenticated): {story.get('title', '')[:80]}")
            return False
        response = client.send_post(_rich_text(text), langs=["en"])
        if has_question(text):
            increment_question_count()
        _record_post("wire", response.uri)
        story["bluesky_text"] = text
        story["bluesky_uri"]  = response.uri
        _log(f"WIRE OK {response.uri} | {text!r}")
        return True
    except Exception as e:
        _log(f"WIRE FAILED: {story.get('title', '')[:80]} | {e}")
        return False


# =============================================================
# FUNCTION 5 — POST AN ARTICLE THREAD
# =============================================================

def post_article_thread(post: dict) -> bool:
    """
    Posts a two-part thread for an approved, published article: post1 (lede,
    no link), then post2 (context + link + hashtags) as a reply to post1.
    Returns True only if both posts went out.
    """
    from atproto import models

    try:
        allow_q = get_question_count_today() < MAX_QUESTIONS_PER_DAY
        thread = generate_article_post(post, allow_question=allow_q)
        questions = sum(has_question(t) for t in thread.values())
        if questions and get_question_count_today() + questions > MAX_QUESTIONS_PER_DAY:
            thread = generate_article_post(post, allow_question=False)

        client = authenticate()
        if client is None:
            _log(f"THREAD FAILED (not authenticated): {post.get('title', '')[:80]}")
            return False

        first = client.send_post(_rich_text(thread["post1"]), langs=["en"])
        ref = models.create_strong_ref(first)
        reply_to = models.AppBskyFeedPost.ReplyRef(parent=ref, root=ref)
        second = client.send_post(_rich_text(thread["post2"]), reply_to=reply_to, langs=["en"])

        for t in thread.values():
            if has_question(t):
                increment_question_count()
        _record_post("thread", first.uri)
        post["bluesky_thread"] = {**thread, "uri": first.uri, "reply_uri": second.uri}
        _log(f"THREAD OK {first.uri} | {thread['post1']!r} || {thread['post2']!r}")
        return True
    except Exception as e:
        _log(f"THREAD FAILED: {post.get('title', '')[:80]} | {e}")
        return False


# =============================================================
# FUNCTION 6 — PEAK WINDOWS
# =============================================================

def should_post_now(post_type: str = "wire", now: datetime.datetime | None = None) -> bool:
    """
    True if the current Paris time is inside a peak posting window
    (07:00-09:00, 12:00-13:00, 19:00-21:00). post_type is accepted for
    future per-type windows; all types currently share the same windows.
    """
    now = (now or datetime.datetime.now(PARIS)).astimezone(PARIS)
    return any(start <= now.hour < end for start, end in PEAK_WINDOWS)


def next_window_start(now: datetime.datetime | None = None) -> datetime.datetime:
    """The start of the next Paris peak window after `now` (or now, if inside one)."""
    now = (now or datetime.datetime.now(PARIS)).astimezone(PARIS)
    if should_post_now(now=now):
        return now
    for day in range(2):
        date = (now + datetime.timedelta(days=day)).date()
        for start, _ in PEAK_WINDOWS:
            candidate = datetime.datetime(date.year, date.month, date.day, start, tzinfo=PARIS)
            if candidate > now:
                return candidate
    return now


# =============================================================
# FUNCTIONS 7 + 8 — DAILY QUESTION LIMIT
# =============================================================

def _load_stats() -> dict:
    today = datetime.datetime.now(PARIS).date().isoformat()
    stats = {}
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                stats = json.load(f)
        except (OSError, ValueError):
            stats = {}
    if stats.get("date") != today:
        # New Paris day — counters reset at midnight
        stats = {"date": today, "question_count": 0, "posts": []}
    return stats


def _save_stats(stats: dict) -> None:
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def _record_post(kind: str, uri: str) -> None:
    stats = _load_stats()
    stats.setdefault("posts", []).append({
        "type": kind, "uri": uri,
        "at": datetime.datetime.now(PARIS).isoformat(timespec="seconds"),
    })
    _save_stats(stats)


def get_question_count_today() -> int:
    """Number of posts containing '?' published today (Paris time)."""
    stats = _load_stats()
    if not os.path.exists(STATS_FILE):
        _save_stats(stats)
    return stats.get("question_count", 0)


def increment_question_count() -> int:
    stats = _load_stats()
    stats["question_count"] = stats.get("question_count", 0) + 1
    _save_stats(stats)
    return stats["question_count"]


if __name__ == "__main__":
    c = authenticate()
    print("Bluesky connected:", c is not None)
    print("In a peak window now:", should_post_now())
    print("Questions posted today:", get_question_count_today())
