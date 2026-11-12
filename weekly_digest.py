#!/usr/bin/env python3
"""
IWF Weekly Digest ("France this week")
--------------------------------------
Every Friday at 12:00 Paris time (com.iwf.weekly.digest) this builds a
Bluesky thread of the week's top stories, oldest first, each story once:

  1. Candidates: wire.json items and published.json articles from Monday
     00:00 to Friday 11:59, relevance >= WIRE_MIN, Category A/B/D only.
  2. Dedupe in three passes: exact (URL / title), fuzzy (title similarity),
     semantic (one Haiku call clusters items about the same event).
  3. Top MAX_STORIES by relevance, then sorted by date, one line each.
  4. The draft goes to Telegram; it's posted only on Approve (or after
     AUTO_POST_AFTER if AUTO_POST_IF_NO_RESPONSE is on).

weekly_digest_state.json records each ISO week, so a week can never be
posted twice. The launchd job also re-runs at 13:05, 14:05 and 15:05 to
post an approved thread that had to wait for the 60-minute spacing.

    python3 weekly_digest.py --dry-run   # print thread + dedupe audit only
    python3 weekly_digest.py             # what the Friday job runs
"""

import argparse
import ast
import asyncio
import datetime
import difflib
import fcntl
import json
import os
import re
import sys
import traceback
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import anthropic
from dotenv import load_dotenv

import bluesky_poster
import digest
from relevance_filter import FULL_MIN, WIRE_MIN, passes_geo_rule

HERE            = os.path.dirname(os.path.abspath(__file__))
STATE_FILE      = os.path.join(HERE, "weekly_digest_state.json")
LOCK_FILE       = os.path.join(HERE, "weekly_digest.lock")
LOG_FILE        = os.path.join(HERE, "logs", "weekly-digest.log")
WIRE_FILE       = os.path.join(HERE, "wire.json")
WIRE_QUEUE_FILE = os.path.join(HERE, "wire_queue.json")
PUBLISHED_FILE  = os.path.join(HERE, "published.json")
CACHE_FILE      = os.path.join(HERE, "relevance_cache.json")
SOURCES_FILE    = os.path.join(HERE, "sources.json")
load_dotenv(os.path.join(HERE, ".env"))

PARIS     = bluesky_poster.PARIS
SITE_URL  = "https://islamophobiawatchfrance.com"

ALLOWED_CATEGORIES = {"A", "B", "D"}
MIN_STORIES = 3            # fewer than this qualify: skip and alert on Telegram
MAX_STORIES = 8
POST_LIMIT  = 299          # Bluesky posts must be under 300 characters
SPACING     = datetime.timedelta(minutes=60)

# Approval: off means a thread is only ever posted after Approve in Telegram
AUTO_POST_IF_NO_RESPONSE = False
AUTO_POST_AFTER          = datetime.timedelta(minutes=60)

FUZZY_RATIO   = 0.80       # difflib ratio on normalised titles
FUZZY_JACCARD = 0.60       # overlap of significant title words (names, nouns)

CLUSTER_MODEL = "claude-haiku-4-5-20251001"
WRITER_MODEL  = "claude-opus-5-5"

CLUSTER_SYSTEM_PROMPT = """
You deduplicate news items for a weekly round-up by Islamophobia Watch France.

Group the items into clusters, one cluster per distinct development: items that report the same event, statement, ruling or decision belong together, whatever the source or wording.

When a later item is about the same underlying story as an earlier cluster, decide whether it adds a genuinely new development (a charge, an indictment, a ruling, a verdict, a new incident, an official decision). If it does, give it its own cluster with "follow_up_of" set to the earlier cluster id and "new_development": true. If it only repeats, reacts to or comments on the earlier development, put it in the earlier cluster.

Every item id must appear in exactly one cluster.

Respond in JSON only:
{"clusters": [{"id": "c1", "event": "short description", "items": ["g3", "g7"], "follow_up_of": null, "new_development": false}]}
"""

WRITER_SYSTEM_PROMPT = """
You write the entries of the weekly "France this week" Bluesky thread for Islamophobia Watch France (IWF).

For each item, write ONE neutral, factual sentence summarising the development, in English, at most {limit} characters. Analytical wire style. Only use facts in the item. Where a named actor made anti-Muslim statements, the actor is the subject. No questions, no emojis, no hashtags, no links, no em dashes or en dashes, no source attribution (it is added automatically).

Respond in JSON only: {{"lines": {{"<item id>": "<sentence>"}}}}
"""

STOPWORDS = set("""
the and for with from that this after over into about their they them his her its are was were
has have had been will would says said say new against amid during under more than not but who
what when where which while les des une dans pour avec sur par est sont qui que aux du au
""".split())


# =============================================================
# LOGGING + STATE
# =============================================================

def _log(message: str) -> None:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    stamp = datetime.datetime.now(PARIS).strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_state() -> dict:
    state = _load_json(STATE_FILE, {})
    return state if isinstance(state.get("weeks"), dict) else {"weeks": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


class _lock:
    """Exclusive lock so the Telegram bot and the launchd re-runs can't both post."""
    def __enter__(self):
        self.f = open(LOCK_FILE, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close()


def iso_week(now: datetime.datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def week_window(now: datetime.datetime) -> tuple:
    """Monday 00:00 to Friday 11:59:59 (Paris) of the week containing `now`."""
    monday = (now - datetime.timedelta(days=now.weekday())).date()
    start = datetime.datetime(monday.year, monday.month, monday.day, tzinfo=PARIS)
    end = start + datetime.timedelta(days=4, hours=11, minutes=59, seconds=59)
    return start, end


def _parse(ts):
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


# =============================================================
# CANDIDATES
# =============================================================

def _source_rank(name: str, sources: dict) -> int:
    """Lower is better: trust high < medium < monitor-only; state-linked outlets one step down."""
    s = sources.get((name or "").strip().lower(), {})
    trust = s.get("trust", "medium")
    bias = s.get("bias", "")
    rank = {"high": 0, "medium": 1}.get(trust, 3)
    if bias == "far-right" or trust == "monitor-only":
        rank = 3
    elif "state" in bias:
        rank += 1
    return rank


def _article_summary(draft: str) -> str:
    for para in (draft or "").split("\n\n"):
        para = para.strip()
        if para and not para.startswith("#"):
            return para.replace("**", "")[:400]
    return ""


def gather_candidates(start: datetime.datetime, end: datetime.datetime) -> tuple:
    """Returns (candidates, excluded) where excluded is [(title, reason)]."""
    sources = {s["name"].strip().lower(): s
               for s in _load_json(SOURCES_FILE, {}).get("sources", [])
               if isinstance(s, dict) and s.get("name")}
    queue = {p["url"]: p for p in _load_json(WIRE_QUEUE_FILE, {}).get("posts", [])
             if isinstance(p, dict) and p.get("url")}
    cache = _load_json(CACHE_FILE, {})
    candidates, excluded = [], []

    for it in _load_json(WIRE_FILE, {}).get("items", []):
        when = _parse(it.get("published"))
        if not when or not (start <= when <= end):
            continue
        q = queue.get(it["url"], {})
        c = cache.get(it["url"], {})
        score = q.get("relevance_score", c.get("score"))
        category = (q.get("relevance_category") or c.get("category") or "").upper()
        title = q.get("title") or it.get("title", "")
        if score is None:
            excluded.append((title, "no relevance score on record"))
            continue
        if score < WIRE_MIN:
            excluded.append((title, f"score {score} below {WIRE_MIN}"))
            continue
        if category not in ALLOWED_CATEGORIES:
            excluded.append((title, f"Category {category or '?'}"))
            continue
        geo = q or c
        if "geo_scope" in geo and not passes_geo_rule(geo):
            excluded.append((title, "no France angle"))
            continue
        candidates.append({
            "title": title, "summary": q.get("summary", ""), "source": it.get("source", ""),
            "url": it["url"], "when": when, "score": score, "category": category,
            "is_iwf": False, "source_rank": _source_rank(it.get("source", ""), sources),
        })

    for a in _load_json(PUBLISHED_FILE, {}).get("articles", []):
        when = _parse(a.get("published_at"))
        if not when or not (start <= when <= end):
            continue
        # Articles carry no score: take their source's cached score, else FULL_MIN
        # (they are only drafted at 8+, or written by hand).
        try:
            src_urls = [s.get("url") for s in ast.literal_eval(a.get("sources") or "[]")]
        except (ValueError, SyntaxError):
            src_urls = []
        scored = [cache[u] for u in src_urls if u in cache]
        score = max([s["score"] for s in scored] + [FULL_MIN])
        category = next((s["category"] for s in scored if s.get("category") in ALLOWED_CATEGORIES), "A")
        candidates.append({
            "title": a.get("title", ""), "summary": _article_summary(a.get("draft", "")),
            "source": "IWF", "url": f"{SITE_URL}/{a.get('filename', '')}", "when": when,
            "score": score, "category": category, "is_iwf": True, "source_rank": -1,
        })

    candidates.sort(key=lambda c: c["when"])
    for i, c in enumerate(candidates):
        c["id"] = f"i{i + 1}"
    return candidates, excluded


# =============================================================
# DEDUPLICATION
# =============================================================

def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."),
                       parts.path.rstrip("/"), "", ""))


def normalise_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


def _key_words(title: str) -> set:
    return {w for w in normalise_title(title).split() if len(w) >= 4 and w not in STOPWORDS}


def preferred(items: list) -> dict:
    """IWF article first, then the highest-quality source, then the earliest."""
    return min(items, key=lambda c: (not c["is_iwf"], c["source_rank"], c["when"]))


def _merge_groups(groups: list, same) -> tuple:
    """Merges groups whose representatives satisfy same(a, b). Returns (groups, merges)."""
    merged, merges = [], []
    for g in groups:
        for m in merged:
            if same(preferred(m), preferred(g)):
                merges.append((preferred(m), preferred(g)))
                m.extend(g)
                break
        else:
            merged.append(list(g))
    return merged, merges


def _fuzzy_same(a: dict, b: dict) -> bool:
    ta, tb = normalise_title(a["title"]), normalise_title(b["title"])
    if difflib.SequenceMatcher(None, ta, tb).ratio() >= FUZZY_RATIO:
        return True
    wa, wb = _key_words(a["title"]), _key_words(b["title"])
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= FUZZY_JACCARD


def _ask_json(model: str, system: str, user: str, max_tokens: int, **extra) -> dict:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    create = client.beta.messages.create if extra.get("betas") else client.messages.create
    response = create(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}], **extra)
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{model} declined the request")
    text = next((b.text for b in response.content if b.type == "text"), "")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"{model} returned no JSON: {text[:200]!r}")
    return json.loads(match.group(0))


def semantic_clusters(groups: list) -> list:
    """
    One Haiku call over the group representatives. Returns validated clusters:
    [{"id", "event", "groups": [group, ...], "follow_up_of", "new_development"}].
    """
    reps = {f"g{i + 1}": g for i, g in enumerate(groups)}
    user = "\n\n".join(
        f"id: {gid}\ndate: {preferred(g)['when'].astimezone(PARIS):%a %d %b %H:%M}\n"
        f"source: {preferred(g)['source']}\ntitle: {preferred(g)['title']}\n"
        f"summary: {preferred(g)['summary'][:300]}"
        for gid, g in reps.items())
    data = _ask_json(CLUSTER_MODEL, CLUSTER_SYSTEM_PROMPT, user, max_tokens=4000)

    clusters, seen = [], set()
    for c in data.get("clusters", []):
        ids = [i for i in c.get("items", []) if i in reps and i not in seen]
        if not ids:
            continue
        seen.update(ids)
        clusters.append({"id": str(c.get("id") or f"c{len(clusters) + 1}"),
                         "event": str(c.get("event", "")), "groups": [reps[i] for i in ids],
                         "follow_up_of": c.get("follow_up_of"),
                         "new_development": bool(c.get("new_development"))})
    # Anything the model left out becomes its own cluster rather than vanishing
    for gid, g in reps.items():
        if gid not in seen:
            clusters.append({"id": f"x{gid}", "event": "(not clustered by model)", "groups": [g],
                             "follow_up_of": None, "new_development": False})
    # Make cluster ids unique even if the model repeated one
    used = set()
    for c in clusters:
        while c["id"] in used:
            c["id"] += "'"
        used.add(c["id"])

    # A follow-up that is not a new development folds into its parent
    by_id = {c["id"]: c for c in clusters}
    for c in clusters:
        parent = by_id.get(c["follow_up_of"])
        if parent is not None and parent is not c and not c["new_development"]:
            parent["groups"].extend(c["groups"])
            c["groups"] = []
            c["merged_into"] = parent["id"]
    return clusters


def deduplicate(candidates: list, audit: list) -> list:
    """Returns one entry per distinct development, each tagged with its cluster id."""
    groups = [[c] for c in candidates]

    groups, merges = _merge_groups(groups, lambda a, b: canonical_url(a["url"]) == canonical_url(b["url"])
                                   or normalise_title(a["title"]) == normalise_title(b["title"]))
    audit.append(f"Pass 1 (exact URL/title): {len(candidates)} -> {len(groups)}")
    audit += [f"  merged  {b['id']} {b['source']}: {b['title'][:70]}\n    into  {a['id']}" for a, b in merges]

    before = len(groups)
    groups, merges = _merge_groups(groups, _fuzzy_same)
    audit.append(f"Pass 2 (fuzzy title): {before} -> {len(groups)}")
    audit += [f"  merged  {b['id']} {b['source']}: {b['title'][:70]}\n    into  {a['id']} {a['source']}: {a['title'][:70]}"
              for a, b in merges]

    clusters = semantic_clusters(groups)
    entries = []
    audit.append(f"Pass 3 (semantic, {CLUSTER_MODEL}): {len(groups)} -> "
                 f"{sum(1 for c in clusters if c['groups'])} clusters")
    for c in clusters:
        if not c["groups"]:
            audit.append(f"  [{c['id']}] folded into [{c['merged_into']}] (follow-up, no new development): {c['event']}")
            continue
        items = [m for g in c["groups"] for m in g]
        keep = preferred(items)
        keep = {**keep, "cluster_id": c["id"], "cluster_size": len(items),
                "score": max(m["score"] for m in items), "when": min(m["when"] for m in items)}
        entries.append(keep)
        tag = f" (new development after [{c['follow_up_of']}])" if c["new_development"] and c["follow_up_of"] else ""
        audit.append(f"  [{c['id']}] {c['event']}{tag}")
        audit.append(f"     KEEP  {keep['id']} {keep['source']} (score {keep['score']}): {keep['title'][:80]}")
        for m in items:
            if m["id"] != keep["id"]:
                audit.append(f"     drop  {m['id']} {m['source']}: {m['title'][:80]}")

    ids = [e["cluster_id"] for e in entries]
    assert len(ids) == len(set(ids)), f"Dedupe self-check failed: duplicate cluster ids {ids}"
    kept = [e["id"] for e in entries]
    assert len(kept) == len(set(kept)), "Dedupe self-check failed: an item was kept twice"
    return entries


# =============================================================
# THREAD
# =============================================================

def _clean(text: str) -> str:
    """House style: no long dashes, no emojis, single spaces."""
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip().strip('"')


def write_lines(entries: list, allow_questions: bool) -> dict:
    """One sentence per entry from the writer model. Returns {entry id: line}."""
    # Short enough that two stories fit in one post, so the thread stays 4-5 posts
    longest_suffix = max(len("Mon: ") + len(f" via {e['source']}") for e in entries)
    limit = max(100, (POST_LIMIT - 2) // 2 - longest_suffix)
    system = WRITER_SYSTEM_PROMPT.format(limit=limit - 10)  # margin, so the hard trim below rarely fires
    if not allow_questions:
        system += "\nDo not use any question marks."
    user = "\n\n".join(f"id: {e['id']}\ntitle: {e['title']}\nsummary: {e['summary'][:500]}" for e in entries)
    data = _ask_json(WRITER_MODEL, system, user, max_tokens=4000,
                     output_config={"effort": "medium"},
                     betas=["server-side-fallback-2026-07-01"],
                     extra_body={"fallbacks": "default"})  # installed SDK predates the fallbacks kwarg
    lines = {}
    for e in entries:
        sentence = _clean(str(data.get("lines", {}).get(e["id"]) or e["title"]))
        if len(sentence) > limit:
            sentence = sentence[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        if not sentence.endswith((".", "…", "!", "?")):
            sentence += "."
        lines[e["id"]] = f"{e['when'].astimezone(PARIS):%a}: {sentence} via {e['source']}"
    return lines


def _date_range(start: datetime.datetime, end: datetime.datetime) -> str:
    if start.month == end.month:
        return f"{start.day}-{end.day} {end:%B}"
    return f"{start.day} {start:%B}-{end.day} {end:%B}"


def build_thread(lines: list, start: datetime.datetime, end: datetime.datetime) -> list:
    """Packs lines into posts under POST_LIMIT, never splitting a story across posts."""
    posts = [f"France this week: {_date_range(start, end)}. The key stories in order."]
    for line in lines:
        if len(posts[-1]) + 2 + len(line) <= POST_LIMIT:
            posts[-1] += "\n\n" + line
        else:
            posts.append(line)
    posts.append(f"Full reporting and analysis on Islamophobia in France: {SITE_URL}")
    for p in posts:
        assert len(p) <= POST_LIMIT, f"Post over {POST_LIMIT} characters: {p!r}"
    return posts


def draft(now: datetime.datetime) -> dict:
    """Builds this week's digest. Returns {"posts", "audit", "entries", "count"}."""
    start, end = week_window(now)
    audit = [f"Window: {start:%a %d %b %H:%M} to {end:%a %d %b %H:%M} (Paris)"]
    candidates, excluded = gather_candidates(start, end)
    audit.append(f"Candidates: {len(candidates)} (score >= {WIRE_MIN}, Category A/B/D); "
                 f"excluded {len(excluded)}")
    audit += [f"  excluded  {reason}: {title[:80]}" for title, reason in excluded]
    if not candidates:
        return {"posts": [], "audit": audit, "entries": [], "count": 0}

    entries = deduplicate(candidates, audit)
    top = sorted(entries, key=lambda e: (-e["score"], not e["is_iwf"], e["when"]))[:MAX_STORIES]
    top.sort(key=lambda e: e["when"])
    audit.append(f"Selected {len(top)} of {len(entries)} distinct stories (top {MAX_STORIES} by score, "
                 f"then oldest first):")
    audit += [f"  [{e['cluster_id']}] {e['when'].astimezone(PARIS):%a %H:%M} score {e['score']} "
              f"{e['source']}: {e['title'][:70]}" for e in top]
    if len(top) < MIN_STORIES:
        return {"posts": [], "audit": audit, "entries": top, "count": len(top)}

    questions_left = bluesky_poster.MAX_QUESTIONS_PER_DAY - bluesky_poster.get_question_count_today()
    lines = write_lines(top, allow_questions=True)
    if sum(bluesky_poster.has_question(l) for l in lines.values()) > questions_left:
        lines = write_lines(top, allow_questions=False)
        lines = {k: v.replace("?", ".") for k, v in lines.items()}
    posts = build_thread([lines[e["id"]] for e in top], start, end)
    return {"posts": posts, "audit": audit, "entries": top, "count": len(top)}


# =============================================================
# TELEGRAM
# =============================================================

async def _telegram(text_parts: list, markup=None) -> None:
    from telegram import Bot

    async with Bot(token=os.getenv("TELEGRAM_BOT_TOKEN")) as bot:
        for i, text in enumerate(text_parts):
            last = i == len(text_parts) - 1
            await bot.send_message(chat_id=os.getenv("TELEGRAM_CHAT_ID"), text=text,
                                   reply_markup=markup if last else None)


def _chunks(text: str, limit: int = 3500) -> list:
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    return out + ([cur] if cur.strip() else [])


def alert(text: str) -> None:
    """Plain Telegram message; failures are only logged."""
    try:
        asyncio.run(_telegram([text]))
    except Exception as e:
        _log(f"Telegram alert failed: {e}")


def send_for_approval(week: str, posts: list, audit: list) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Approve & post", callback_data=f"weekly_approve:{week}"),
        InlineKeyboardButton("✗ Reject", callback_data=f"weekly_reject:{week}"),
    ]])
    thread = "\n\n".join(f"[{i + 1}/{len(posts)}] ({len(p)} chars)\n{p}" for i, p in enumerate(posts))
    when = "only when you approve" if not AUTO_POST_IF_NO_RESPONSE else \
        f"automatically in {int(AUTO_POST_AFTER.total_seconds() // 60)} min unless you reject"
    parts = _chunks("DEDUPE AUDIT\n\n" + "\n".join(audit))
    parts += _chunks(f"🦋 FRANCE THIS WEEK ({week}): Bluesky thread, posted {when}\n\n{thread}")
    asyncio.run(_telegram(parts, markup))


# =============================================================
# POSTING
# =============================================================

def next_allowed(now: datetime.datetime) -> datetime.datetime:
    """Earliest time the thread may go out, given the 60-minute spacing from the last post."""
    last = digest.last_bluesky_post()
    return max(now, last + SPACING) if last else now


def _post_thread(posts: list) -> list:
    from atproto import models

    client = bluesky_poster.authenticate()
    if client is None:
        raise RuntimeError("Bluesky login failed")
    uris, root, parent = [], None, None
    for text in posts:
        reply_to = None
        if root is not None:
            reply_to = models.AppBskyFeedPost.ReplyRef(parent=parent, root=root)
        response = client.send_post(bluesky_poster._rich_text(text), reply_to=reply_to, langs=["en"])
        parent = models.create_strong_ref(response)
        root = root or parent
        uris.append(response.uri)
        if bluesky_poster.has_question(text):
            bluesky_poster.increment_question_count()
    return uris


def post_if_due(week: str, now: datetime.datetime | None = None) -> tuple:
    """
    Posts the week's thread if it is approved (or auto-post is due) and the
    spacing allows. Returns (status, due_time): status is "posted", "waiting",
    or the week's current status if there is nothing to do.
    """
    now = (now or datetime.datetime.now(PARIS)).astimezone(PARIS)
    with _lock():
        state = load_state()
        entry = state["weeks"].get(week)
        if entry is None:
            return "missing", None
        status = entry.get("status")
        created = _parse(entry.get("created_at"))
        if status == "pending" and AUTO_POST_IF_NO_RESPONSE and created and now - created >= AUTO_POST_AFTER:
            entry["status"] = status = "approved"
            entry["approved_by"] = "auto (no response)"
            save_state(state)
        if status != "approved":
            return status, None
        due = next_allowed(now)
        if due > now:
            return "waiting", due

        # Mark before sending: if this crashes mid-thread it must never be re-posted blindly
        entry["status"] = "posting"
        save_state(state)
        try:
            uris = _post_thread(entry["posts"])
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            save_state(state)
            _log(f"POST FAILED {week}: {e}")
            bluesky_poster._log(f"WEEKLY FAILED {week} | {e}")
            raise
        entry.update(status="posted", uris=uris, posted_at=datetime.datetime.now(PARIS).isoformat())
        save_state(state)
    bluesky_poster._record_post("weekly", uris[0])
    bluesky_poster._log(f"WEEKLY OK {uris[0]} | {len(uris)} posts")
    _log(f"POSTED {week}: {len(uris)} posts, {uris[0]}")
    return "posted", None


def approve(week: str) -> datetime.datetime | None:
    """Marks the week approved. Returns when it may post, or None if not pending."""
    with _lock():
        state = load_state()
        entry = state["weeks"].get(week)
        if not entry or entry.get("status") != "pending":
            return None
        entry.update(status="approved", approved_by="telegram",
                     approved_at=datetime.datetime.now(PARIS).isoformat())
        save_state(state)
    _log(f"APPROVED {week}")
    return next_allowed(datetime.datetime.now(PARIS))


def reject(week: str) -> bool:
    with _lock():
        state = load_state()
        entry = state["weeks"].get(week)
        if not entry or entry.get("status") != "pending":
            return False
        entry["status"] = "rejected"
        save_state(state)
    _log(f"REJECTED {week}")
    return True


# =============================================================
# MAIN
# =============================================================

def run(now: datetime.datetime) -> None:
    week = iso_week(now)
    state = load_state()
    entry = state["weeks"].get(week)

    if entry is None:
        result = draft(now)
        print("\n".join(result["audit"]))
        if result["count"] < MIN_STORIES:
            with _lock():
                state = load_state()
                state["weeks"][week] = {"status": "skipped", "created_at": now.isoformat(),
                                        "reason": f"only {result['count']} stories qualified"}
                save_state(state)
            _log(f"SKIPPED {week}: only {result['count']} stories qualified")
            alert(f"⚠️ France this week ({week}): not posted. Only {result['count']} "
                  f"stor{'y' if result['count'] == 1 else 'ies'} qualified (minimum {MIN_STORIES}).")
            return
        with _lock():
            state = load_state()
            if week in state["weeks"]:
                return  # another run got here first
            state["weeks"][week] = {"status": "pending", "created_at": now.isoformat(),
                                    "posts": result["posts"], "audit": result["audit"]}
            save_state(state)
        send_for_approval(week, result["posts"], result["audit"])
        _log(f"DRAFTED {week}: {len(result['posts'])} posts, {result['count']} stories, sent to Telegram")
        return

    status, due = post_if_due(week, now)
    if status == "waiting":
        _log(f"WAITING {week}: approved, spacing allows posting from {due:%H:%M}")
    elif status == "failed":
        alert(f"⚠️ France this week ({week}) failed to post: {entry.get('error')}. See logs/weekly-digest.log.")
    elif status == "posting":
        alert(f"⚠️ France this week ({week}) stopped part-way through posting. Check Bluesky "
              f"before doing anything; it will not be retried automatically.")


def main() -> None:
    parser = argparse.ArgumentParser(description="IWF weekly 'France this week' Bluesky thread")
    parser.add_argument("--dry-run", action="store_true", help="print the thread and dedupe audit, post nothing")
    args = parser.parse_args()
    now = datetime.datetime.now(PARIS)

    if args.dry_run:
        result = draft(now)
        print("\n".join(result["audit"]))
        print(f"\n{'=' * 60}\nTHREAD ({len(result['posts'])} posts)\n{'=' * 60}")
        if not result["posts"]:
            print(f"Not enough stories ({result['count']}; minimum {MIN_STORIES}). Would skip and alert.")
        for i, p in enumerate(result["posts"]):
            print(f"\n[{i + 1}/{len(result['posts'])}] ({len(p)} chars)\n{p}")
        return

    print(f"=== IWF Weekly Digest === {now:%Y-%m-%d %H:%M}")
    try:
        run(now)
    except Exception as e:
        _log(f"ERROR {iso_week(now)}: {e}\n{traceback.format_exc()}")
        alert(f"⚠️ France this week ({iso_week(now)}): the digest job crashed: {e}. See logs/weekly-digest.log.")
        sys.exit(1)


if __name__ == "__main__":
    main()
