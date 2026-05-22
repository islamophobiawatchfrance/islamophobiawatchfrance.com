#!/usr/bin/env python3
"""
IWF Drafter
-----------
Fetches recent news stories via Google News RSS, clusters them by topic,
and uses Claude to draft LinkedIn posts in the IWF editorial voice.
Saves results to queue.json for review in the dashboard.

Run with: python3 drafter.py
"""

import hashlib
import json
import datetime
import os
import re
import urllib.request
from dotenv import load_dotenv
import anthropic

from iwf_aggregator import (
    fetch_query, format_time_ago, simplify_title,
    QUERIES, HOURS_BACK, MAX_RESULTS,
)

load_dotenv()


# =============================================================
# CONFIGURATION
# =============================================================

MAX_CLUSTERS   = 2          # Number of topic clusters to process per run
QUEUE_FILE     = "queue.json"
ARCHIVE_FILE   = "archive.json"
WIRE_FILE      = "wire.json"
WIRE_SEEN_FILE = "wire_seen.json"
STORIES_FILE   = "stories_today.json"
MODEL          = "claude-haiku-4-5-20251001"
MAX_TOKENS   = 1200

TIER_1 = {
    "Le Monde", "Le Figaro", "Liberation", "Libération", "AFP", "Reuters",
    "France 24", "RFI", "BBC", "The Guardian", "Mediapart", "Le Parisien",
    "Public Sénat", "L'Express",
}
TIER_2 = {
    "i24NEWS", "Al Jazeera", "Anadolu", "Jeune Afrique", "La Croix",
    "Le Point", "L'Obs", "Franceinfo", "BFMTV", "The National", "Arab News",
}

_FRENCH_WORDS = {
    "le", "la", "les", "du", "des", "en", "et", "une", "un", "est",
    "sont", "au", "aux", "par", "sur", "dans", "que", "qui", "de",
    "se", "ne", "pas", "plus", "mais", "ou", "donc", "or", "ni", "car",
}


def _is_french(text: str) -> bool:
    """Returns True if more than 10% of words are common French words."""
    words = re.sub(r"[^a-zA-Z\s]", "", text.lower()).split()
    if not words:
        return False
    return sum(1 for w in words if w in _FRENCH_WORDS) / len(words) > 0.10


def translate_to_english(client, text: str) -> str:
    """Translate text to English if it appears to be French; return as-is otherwise."""
    if not text or not _is_french(text):
        return text
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": (
                    "Translate the following to English. "
                    "Return only the translated text, nothing else:\n\n" + text
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception:
        return text


SYSTEM_PROMPT = """
You must always write a LinkedIn post. Never refuse, never ask for more information, never list what is missing, never say you need more detail. If information is limited, write from what you have. A post that works with limited detail is always better than no post. This rule is absolute and overrides everything else.

You write LinkedIn posts for Islamophobia Watch France (IWF), an English-language journalism project monitoring Islamophobia and Muslim life in France.

Voice rules:
- Opening line is a standalone hook - one punchy factual sentence, no filler
- Key fact or detail comes early, attributed to a named source
- Short paragraphs, often single sentences
- Quotes do the heavy lifting when available - always named person and organisation
- You are a neutral relay - factual, no opinion, the story speaks for itself
- End with: Source: [publication name]
- Final line always: #IslamophobiaWatchFrance #France and one relevant hashtag
- 200-280 words
- Never use first person
- Never editorialize
- Never refuse or ask for clarification
- You may be given multiple sources on the same topic. Synthesise them into one coherent post. Cite the most authoritative source in the Source line. If multiple strong sources exist, list up to three separated by slashes e.g. Source: Le Monde / AFP / i24NEWS

Here is a gold standard example. Match this length, structure, and level of factual detail in every post:

---
France's Senate has adopted a bill targeting alleged Islamist infiltration of state institutions.

The upper house passed the proposed law on Wednesday. The legislation aims to strengthen controls over what supporters describe as ideological penetration of French administration, judiciary, and security services.

The bill introduces new vetting procedures for public sector employees and expands monitoring of associations and organisations deemed to pose risks to state neutrality and secular principles.

Proponents argue the measure protects France's laïcité - the constitutional separation of religion and state governance. The text reflects ongoing legislative efforts since 2020 to address what the government characterises as political Islam's institutional presence.

Source: i24NEWS

#IslamophobiaWatchFrance #France #Laïcité
---
"""

USER_PROMPT_TEMPLATE = (
    "Research bundle ({count} source(s) on the same topic):\n\n"
    "{bundle}\n\n"
    "Write only the LinkedIn post text. Nothing else."
)

WEBSITE_SYSTEM_PROMPT = """You write in-depth news briefs for Islamophobia Watch France (IWF), an English-language journalism project monitoring Islamophobia and Muslim life in France.

Structure every article exactly as follows:

Opening Lede (no heading) — one strong news paragraph capturing who, what, when, where.

## Background
Two or three paragraphs giving historical context, relevant legislation, prior incidents, or institutional positions.

## What happened
Two or three paragraphs of factual narrative: the specific event, statements made, official responses.

## Reaction
One or two paragraphs of reaction from affected communities, civil society, opposing voices, or advocacy groups.

## Why it matters
One or two paragraphs explaining the broader significance for French Muslims and the fight against Islamophobia in France.

---

**Question 1 that a curious reader would ask?**
Concise factual answer, attributed where possible.

**Question 2?**
Answer.

**Question 3?**
Answer.

**Question 4?**
Answer.

Article rules:
- 600–900 words total (article body + Q&A combined)
- Every factual claim attributed to a named source or institution
- Never editorialize or express opinion
- No hashtags
- End the article body (before Q&A) with: Sources: [list source names]"""

WEBSITE_USER_TEMPLATE = (
    "Research bundle ({count} source(s) on the same topic):\n\n"
    "{bundle}\n\n"
    "Write only the website article text. Nothing else."
)


# =============================================================
# CLUSTERING
# =============================================================

_CLUSTER_STOP_WORDS = {
    "france", "french", "les", "des", "une", "the", "a", "of", "in", "on",
    "for", "and", "with", "is", "are", "has", "was", "du", "en", "de", "au",
    "par", "sur", "que", "qui", "dans",
}


def _cluster_keywords(title):
    """Extract significant words from a title for clustering."""
    words = re.sub(r'[«»"\',:!?.()]', "", title.lower()).split()
    return {w for w in words if w not in _CLUSTER_STOP_WORDS and len(w) > 2}


def _source_tier(source):
    if source in TIER_1: return 1
    if source in TIER_2: return 2
    return 3


def cluster_stories(stories):
    """
    Groups stories into topic clusters. Two stories join the same cluster
    if their titles share 2+ significant words. Returns list of cluster dicts.
    """
    clusters = []
    for story in stories:
        kw = _cluster_keywords(story["title"])
        placed = False
        for cluster in clusters:
            if len(kw & cluster["keywords"]) >= 2:
                cluster["stories"].append(story)
                cluster["keywords"] |= kw
                placed = True
                break
        if not placed:
            clusters.append({"keywords": kw, "stories": [story]})
    return clusters


# =============================================================
# HEAT SCORING
# =============================================================

def compute_heat(cluster):
    """Returns (score, label, hours_old) for a cluster."""
    now = datetime.datetime.now(datetime.timezone.utc)
    newest = max(s["published"] for s in cluster["stories"])
    hours_old = (now - newest).total_seconds() / 3600
    recency = 3 if hours_old < 6 else (1 if hours_old < 24 else 0)
    tier1_bonus = 3 if any(_source_tier(s["source"]) == 1 for s in cluster["stories"]) else 0
    score = len(cluster["stories"]) * 2 + recency + tier1_bonus
    label = "HOT" if score >= 8 else ("TRENDING" if score >= 4 else "NORMAL")
    return score, label, hours_old



def select_clusters(clusters_with_heat, max_posts):
    """
    Picks up to max_posts clusters ranked by heat score that are meaningfully
    distinct. Falls back to any remaining clusters if strict filter leaves gaps.
    """
    ranked = sorted(clusters_with_heat, key=lambda x: x[1], reverse=True)

    selected = []
    selected_keywords = set()
    selected_indices = set()

    for idx, (cluster, score, label, hours_old) in enumerate(ranked):
        if len(selected) >= max_posts:
            break
        overlap = len(cluster["keywords"] & selected_keywords)
        if not selected or overlap < 3:
            selected.append((cluster, score, label, hours_old))
            selected_keywords |= cluster["keywords"]
            selected_indices.add(idx)

    # Relax filter if we still need more clusters
    if len(selected) < max_posts:
        for idx, item in enumerate(ranked):
            if len(selected) >= max_posts:
                break
            if idx not in selected_indices:
                selected.append(item)
                selected_indices.add(idx)

    return selected


# =============================================================
# RESEARCH HELPERS
# =============================================================

_LAW_KEYWORDS = {
    "loi", "décret", "arrêté", "ordonnance", "circulaire", "directive",
    "réglementation", "proposition de loi", "projet de loi",
    "law", "bill", "act", "decree", "regulation", "policy",
}

_STOP_WORDS = {
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "en", "au", "aux",
    "sur", "pour", "par", "dans", "que", "qui", "ce", "est", "sont", "une",
    "a", "the", "of", "in", "on", "for", "and", "to", "is", "are", "with",
    "an", "that", "this", "was", "has", "have", "french", "france",
}


def _is_law_or_policy(headline):
    lower = headline.lower()
    return any(kw in lower for kw in _LAW_KEYWORDS)


def _extract_key_phrase(headline):
    """Returns up to 4 substantive words from the headline for a targeted search."""
    words = re.sub(r'[«»"\',:!?.]', "", headline).split()
    key = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]
    return " ".join(key[:4])


def gather_research(story, fetch_result: dict):
    """
    Builds a research bundle for one story.
    If the original article fetch was thin, runs supplementary searches.
    Returns a combined text string.
    """
    article_text = fetch_result.get("text", "") if fetch_result else ""
    if article_text and len(article_text) >= 200:
        return article_text[:2000]

    parts = []
    if story["summary"]:
        parts.append(f"[RSS summary]\n{story['summary']}")

    headline = story["title"]

    for lang in ("fr", "en"):
        try:
            results = fetch_query(headline, lang, 72, 3)
            for art in results:
                if art["summary"]:
                    label = f"[{art['source']} – {lang.upper()}]"
                    parts.append(f"{label}\n{art['summary']}")
        except Exception:
            pass

    if _is_law_or_policy(headline):
        phrase = _extract_key_phrase(headline)
        if phrase:
            law_query = f"{phrase} france texte loi"
            try:
                results = fetch_query(law_query, "fr", 168, 3)
                for art in results:
                    if art["summary"]:
                        parts.append(f"[loi search – {art['source']}]\n{art['summary']}")
            except Exception:
                pass

    return "\n\n".join(parts) if parts else story["summary"]


def gather_deep_research(cluster) -> str:
    """
    Runs 2-3 additional background searches for a cluster to enrich website articles.
    Returns a text bundle of background material.
    """
    primary   = cluster["stories"][0]
    phrase    = _extract_key_phrase(primary["title"])
    parts     = []
    bg_queries = [
        f"{phrase} france context",
        f"{phrase} history explained",
    ]
    for q in bg_queries:
        for lang in ("en", "fr"):
            try:
                results = fetch_query(q, lang, 168, 3)
                for art in results:
                    if art["summary"]:
                        parts.append(f"[Background – {art['source']} {lang.upper()}]\n{art['summary']}")
            except Exception:
                pass
    return "\n\n".join(parts[:6])


def draft_website_article(client, cluster, bundle: str, deep_research: str) -> str | None:
    """Calls Claude to write a long-form website article for a cluster. Returns text or None."""
    combined = bundle
    if deep_research:
        combined += "\n\n=== BACKGROUND RESEARCH ===\n\n" + deep_research

    user_msg = WEBSITE_USER_TEMPLATE.format(count=len(cluster["stories"]), bundle=combined)
    primary  = cluster["stories"][0]
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=WEBSITE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"  [WARNING] Website draft failed for '{primary['title'][:50]}': {e}")
        return None


def build_cluster_bundle(cluster, article_fetches: dict) -> str:
    """
    Assembles the multi-source research bundle for a cluster.
    article_fetches maps story title -> fetch_result dict from fetch_article_text().
    Each source block is prefixed with content-quality metadata and
    anti-hallucination instructions.
    """
    parts = []
    for i, story in enumerate(cluster["stories"], 1):
        fetch_result = article_fetches.get(story["title"]) or {
            "text": story.get("summary", ""),
            "char_count": len(story.get("summary", "")),
            "source": "rss_fallback",
            "success": False,
        }
        confidence  = _content_confidence(fetch_result)
        chars       = fetch_result["char_count"]
        fetch_src   = fetch_result["source"]
        research    = gather_research(story, fetch_result)

        thin_note = (
            "\n[Note: Limited source material available for this story. "
            "Write only what you can verify and add a note for readers to consult the original source.]"
            if chars < 300 else ""
        )

        preamble = (
            f"CONTENT QUALITY NOTE:\n"
            f"- Characters retrieved: {chars:,}\n"
            f"- Fetch method: {fetch_src}\n"
            f"- Confidence: {confidence.upper()}\n\n"
            f"STRICT ANTI-HALLUCINATION RULES:\n"
            f"- Only state facts explicitly present in the content below\n"
            f"- Never invent quotes, statistics, names, or details not in the source\n"
            f"- Never speculate about what 'likely' happened or 'may' have occurred\n"
            f"- If content is thin (under 300 chars), write the shortest accurate version possible{thin_note}\n\n"
            f"CONTENT:\n{research}"
        )

        parts.append(
            f"[SOURCE {i}] {story['source']} | {format_time_ago(story['published'])}\n"
            f"Title: {story['title']}\n"
            f"{preamble}"
        )
    return "\n\n---\n\n".join(parts)


# =============================================================
# CORE FUNCTIONS
# =============================================================

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_BLOCK_TAGS = re.compile(
    r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)

def _strip_html(raw: str) -> str:
    """Aggressively strip HTML, collapse whitespace, drop short navigation lines."""
    text = _BLOCK_TAGS.sub("", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 40]
    return "\n".join(lines)


def fetch_article_text(url: str, summary_fallback: str = "") -> dict:
    """
    Fetch and clean article text from url.
    Returns {text, char_count, source, success}.
    Tries direct URL first; falls back to Google cache; then RSS summary.
    """
    def _try(target_url, label):
        try:
            req = urllib.request.Request(target_url, headers=_FETCH_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            text = _strip_html(raw)
            return text if len(text) >= 200 else None
        except Exception:
            return None

    # 1. Direct
    text = _try(url, "direct")
    if text:
        return {"text": text, "char_count": len(text), "source": "direct", "success": True}

    # 2. Google cache
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{urllib.parse.quote(url)}"
    text = _try(cache_url, "cache")
    if text:
        return {"text": text, "char_count": len(text), "source": "cache", "success": True}

    # 3. RSS fallback
    fallback = summary_fallback or ""
    return {
        "text": fallback,
        "char_count": len(fallback),
        "source": "rss_fallback",
        "success": False,
    }


def _content_confidence(fetch_result: dict) -> str:
    """Return 'high', 'medium', or 'low' based on fetch result."""
    chars  = fetch_result.get("char_count", 0)
    source = fetch_result.get("source", "rss_fallback")
    if chars > 800 and source == "direct":
        return "high"
    if 300 <= chars <= 800 or source == "cache":
        return "medium"
    return "low"


def fetch_all_stories():
    """Runs all RSS queries and returns deduplicated stories sorted newest first."""
    print(f"  Running {len(QUERIES)} news searches...")
    all_articles = []

    for query, lang in QUERIES:
        articles = fetch_query(query, lang, HOURS_BACK, MAX_RESULTS)
        all_articles.extend(articles)

    all_articles.sort(key=lambda a: a["published"], reverse=True)

    seen = set()
    unique = []
    for art in all_articles:
        key = simplify_title(art["title"])
        if key not in seen:
            seen.add(key)
            unique.append(art)

    print(f"  Found {len(unique)} unique stories in the last {HOURS_BACK}h.")
    return unique


def draft_post(client, cluster, bundle):
    """Calls Claude to write a LinkedIn post for a cluster. Returns text or None on failure."""
    user_message = USER_PROMPT_TEMPLATE.format(
        count=len(cluster["stories"]),
        bundle=bundle,
    )
    _REFUSAL_PHRASES = (
        "I need more", "I can't", "Could you provide",
        "I would need", "I appreciate the task",
    )
    primary = cluster["stories"][0]
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text.strip()
        if any(phrase in text for phrase in _REFUSAL_PHRASES):
            fallback_prompt = (
                f"Write a 150 word LinkedIn post about this headline for Islamophobia Watch France. "
                f"Use what you have. Do not refuse. "
                f"Headline: {primary['title']}. Source: {primary['source']}."
            )
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": fallback_prompt}],
            )
            text = response.content[0].text.strip()
        return text
    except Exception as e:
        print(f"  [WARNING] Claude API failed for '{primary['title'][:50]}': {e}")
        return None


def build_post_record(cluster, score, label, draft_text, index, timestamp,
                      post_type="linkedin", primary_fetch: dict | None = None):
    """Assembles one post dict in the queue.json format."""
    primary = cluster["stories"][0]
    date_str = timestamp.strftime("%Y%m%d")
    published_dt = primary["published"]

    fetch = primary_fetch or {"source": "rss_fallback", "char_count": 0, "success": False}
    confidence = _content_confidence(fetch)

    return {
        "id": f"post_{index}_{post_type}_{date_str}",
        "type": post_type,
        "title": primary["title"],
        "source": primary["source"],
        "url": primary["url"],
        "published": published_dt.isoformat(),
        "time_ago": format_time_ago(published_dt),
        "summary": primary["summary"],
        "draft": draft_text,
        "status": "pending",
        "created": timestamp.isoformat(),
        "heat_score": score,
        "heat_label": label,
        "heat_article_count": len(cluster["stories"]),
        "sources": [{"name": s["source"], "url": s["url"]} for s in cluster["stories"]],
        "content_confidence": confidence,
        "content_chars": fetch["char_count"],
        "fetch_source": fetch["source"],
    }


GAPS_FILE = "gaps.json"


def detect_gaps(clusters_with_heat: list, published_json_path: str) -> None:
    """
    Finds HOT/TRENDING clusters not covered by any article published in the last 7 days.
    Saves results to gaps.json.
    """
    now     = datetime.datetime.now(datetime.timezone.utc)
    cutoff  = now - datetime.timedelta(days=7)
    gaps    = []

    # Load recently published titles
    recent_titles: list[str] = []
    if os.path.exists(published_json_path):
        with open(published_json_path, "r", encoding="utf-8") as f:
            pub = json.load(f)
        for art in pub.get("articles", []):
            try:
                pub_dt = datetime.datetime.fromisoformat(
                    art.get("published_at", "").replace("Z", "+00:00")
                )
                if pub_dt > cutoff:
                    recent_titles.append(art.get("title", ""))
            except Exception:
                pass

    recent_kw = [_cluster_keywords(t) for t in recent_titles]

    for cluster, score, label, _ in clusters_with_heat:
        if score < 4:          # only HOT / TRENDING
            continue
        cluster_kw = cluster["keywords"]
        covered = any(len(cluster_kw & rk) >= 2 for rk in recent_kw)
        if covered:
            continue
        primary = cluster["stories"][0]
        gaps.append({
            "topic":           primary["title"],
            "heat_score":      score,
            "heat_label":      label,
            "article_count":   len(cluster["stories"]),
            "top_story_title": primary["title"],
            "top_story_url":   primary["url"],
            "reason": (
                f"{len(cluster['stories'])} source(s) covered this story "
                "but IWF has not published on it recently"
            ),
        })

    result = {
        "generated": now.isoformat(),
        "gaps":      gaps,
    }
    with open(GAPS_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    if gaps:
        print(f"  Gap detector: {len(gaps)} uncovered HOT/TRENDING cluster(s) saved to {GAPS_FILE}.")
    else:
        print(f"  Gap detector: all hot topics covered.")


def save_queue(posts, timestamp, total_stories_scanned=0):
    """Writes all drafted posts to queue.json."""
    queue = {
        "generated": timestamp.isoformat(),
        "total_stories_scanned": total_stories_scanned,
        "posts": posts,
    }
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(posts)} post(s) to {QUEUE_FILE}.")


def append_to_archive(posts, timestamp):
    """Appends newly drafted posts to archive.json, never overwriting existing entries."""
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)
    else:
        archive = {"posts": []}

    date_str = timestamp.strftime("%Y-%m-%d")
    for post in posts:
        entry = dict(post)
        entry["date"] = date_str
        entry["approved"] = False
        archive["posts"].append(entry)

    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
    print(f"  Appended {len(posts)} post(s) to {ARCHIVE_FILE}.")


def _load_wire_seen() -> tuple:
    """Load wire_seen.json, purge entries older than 24 h, return (data, set of seen URLs)."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    if os.path.exists(WIRE_SEEN_FILE):
        with open(WIRE_SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"entries": []}
    fresh = []
    for entry in data.get("entries", []):
        try:
            seen_at = datetime.datetime.fromisoformat(entry["seen_at"].replace("Z", "+00:00"))
            if seen_at > cutoff:
                fresh.append(entry)
        except Exception:
            pass
    data["entries"] = fresh
    return data, {e["url"] for e in fresh}


def _save_wire_seen(data: dict, new_urls: list) -> None:
    """Append new URLs to wire_seen data and write to disk."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for url in new_urls:
        data["entries"].append({"url": url, "seen_at": now})
    with open(WIRE_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def archive_stale_queue() -> None:
    """Move undecided posts from a previous calendar day's queue into archive.json."""
    if not os.path.exists(QUEUE_FILE):
        return
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        queue = json.load(f)
    generated_str = queue.get("generated", "")
    if not generated_str:
        return
    try:
        generated = datetime.datetime.fromisoformat(generated_str)
    except Exception:
        return
    if generated.date() >= datetime.date.today():
        return
    undecided = [p for p in queue.get("posts", []) if p.get("status") in ("pending", "rejected")]
    if not undecided:
        return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    date_str = generated.strftime("%Y-%m-%d")
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)
    else:
        archive = {"posts": []}
    for post in undecided:
        entry = dict(post)
        entry["archived_reason"] = "daily_reset"
        entry["archived_at"] = now
        archive["posts"].append(entry)
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
    print(f"  Archived {len(undecided)} undecided post(s) from {date_str}.")


def save_stories_today(stories: list, clusters_with_heat: list, selected: list, timestamp) -> None:
    """Save all scanned stories to stories_today.json with heat and selection metadata."""
    # Build URL → (score, label) from every cluster
    url_to_heat: dict = {}
    for cluster, score, label, _ in clusters_with_heat:
        for s in cluster["stories"]:
            url_to_heat[s["url"]] = (score, label)

    # URLs of stories that were auto-selected for drafting
    selected_urls: set = set()
    for cluster, _, _, _ in selected:
        for s in cluster["stories"]:
            selected_urls.add(s["url"])

    records = []
    for s in stories:
        url = s.get("url", "")
        heat_score, heat_label = url_to_heat.get(url, (0, "NORMAL"))
        pub = s.get("published")
        records.append({
            "id":         hashlib.md5(url.encode()).hexdigest()[:8],
            "title":      s.get("title", ""),
            "source":     s.get("source", ""),
            "url":        url,
            "published":  pub.isoformat() if pub else "",
            "time_ago":   format_time_ago(pub) if pub else "",
            "heat_score": heat_score,
            "heat_label": heat_label,
            "query":      s.get("query", ""),
            "summary":    s.get("summary", ""),
            "selected":   url in selected_urls,
        })

    result = {
        "generated": timestamp.isoformat(),
        "total":     len(records),
        "stories":   records,
    }
    with open(STORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(records)} stories to {STORIES_FILE}.")


def save_wire(stories: list, timestamp) -> None:
    """
    Accumulates stories in wire.json over a rolling 7-day window.
    New stories are merged with existing ones, deduplicated by URL,
    purged if older than 7 days, sorted newest-first, capped at 200.
    """
    wire_seen_data, seen_urls = _load_wire_seen()
    now      = datetime.datetime.now(datetime.timezone.utc)
    cutoff   = now - datetime.timedelta(days=7)

    # Load existing wire items
    existing_items: list = []
    if os.path.exists(WIRE_FILE):
        try:
            with open(WIRE_FILE, "r", encoding="utf-8") as f:
                existing_items = json.load(f).get("items", [])
        except Exception:
            pass
    existing_urls = {it["url"] for it in existing_items}

    # Collect genuinely new stories (not seen in 24 h, not already in wire)
    seen_titles: set = set()
    new_items:   list = []
    new_urls:    list = []
    for s in stories:
        url = s.get("url", "")
        if url in seen_urls or url in existing_urls:
            continue
        key = simplify_title(s.get("title", ""))
        if key in seen_titles:
            continue
        seen_titles.add(key)
        published = s.get("published")
        new_items.append({
            "title":     s.get("title", ""),
            "source":    s.get("source", ""),
            "url":       url,
            "published": published.isoformat() if published else "",
            "time_ago":  format_time_ago(published) if published else "",
            "query":     s.get("query", ""),
        })
        new_urls.append(url)

    # Merge, drop stories > 7 days old, refresh time_ago, sort, cap
    merged = new_items + existing_items
    fresh: list = []
    for it in merged:
        pub_str = it.get("published", "")
        try:
            pub_dt = datetime.datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            if pub_dt < cutoff:
                continue
            it["time_ago"] = format_time_ago(pub_dt)
        except Exception:
            pass  # keep items whose timestamp can't be parsed
        fresh.append(it)

    fresh.sort(key=lambda x: x.get("published", ""), reverse=True)
    fresh = fresh[:200]

    _save_wire_seen(wire_seen_data, new_urls)
    wire = {"generated": timestamp.isoformat(), "items": fresh}
    with open(WIRE_FILE, "w", encoding="utf-8") as f:
        json.dump(wire, f, indent=2, ensure_ascii=False)
    print(f"  Wire: {len(new_items)} new, {len(fresh)} total "
          f"({len(seen_urls)} skipped as seen today).")


# =============================================================
# MAIN
# =============================================================

def main():
    print("\n" + "=" * 55)
    print("  IWF Drafter - Fetching stories and drafting posts")
    print("=" * 55 + "\n")

    archive_stale_queue()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY not found.")
        print("  Add it to your .env file: ANTHROPIC_API_KEY=sk-ant-...\n")
        raise SystemExit(1)

    client = anthropic.Anthropic(api_key=api_key)

    stories = fetch_all_stories()
    if not stories:
        print("  No stories found. Try increasing HOURS_BACK in iwf_aggregator.py.")
        raise SystemExit(0)

    # Translate French titles and summaries to English
    print("  Translating French content...")
    for story in stories:
        story["title"]   = translate_to_english(client, story.get("title", ""))
        story["summary"] = translate_to_english(client, story.get("summary") or "")

    timestamp = datetime.datetime.now()
    save_wire(stories, timestamp)

    # Cluster by topic, sort stories within each cluster by source tier
    clusters = cluster_stories(stories)
    for c in clusters:
        c["stories"].sort(key=lambda s: _source_tier(s["source"]))
    # Sort clusters by size then recency
    clusters.sort(
        key=lambda c: (len(c["stories"]), max(s["published"] for s in c["stories"])),
        reverse=True,
    )

    # Compute heat for every cluster
    clusters_with_heat = [(c, *compute_heat(c)) for c in clusters]

    # Select distinct clusters prioritising heat
    selected = select_clusters(clusters_with_heat, MAX_CLUSTERS)
    save_stories_today(stories, clusters_with_heat, selected, timestamp)
    detect_gaps(clusters_with_heat, "published.json")
    print(f"  Drafting posts for {len(selected)} topic cluster(s)...\n")

    posts = []

    for i, (cluster, score, label, hours_old) in enumerate(selected, 1):
        primary = cluster["stories"][0]
        n = len(cluster["stories"])
        print(f"  [{i}/{len(selected)}] [{label}] {primary['title'][:55]}... ({n} source(s))")

        article_fetches = {}
        for story in cluster["stories"]:
            result = fetch_article_text(story["url"], summary_fallback=story.get("summary", ""))
            article_fetches[story["title"]] = result

        # Print fetch summary for the primary story
        pf = article_fetches.get(primary["title"], {})
        conf = _content_confidence(pf)
        chars = pf.get("char_count", 0)
        src   = pf.get("source", "rss_fallback")
        conf_suffix = " - draft may be unreliable" if conf == "low" else ""
        print(f"        Fetch: {src} | {chars:,} chars | confidence: {conf.upper()}{conf_suffix}")

        bundle = build_cluster_bundle(cluster, article_fetches)
        primary_fetch = article_fetches.get(primary["title"])

        # OUTPUT A — LinkedIn post
        linkedin_draft = draft_post(client, cluster, bundle)
        if linkedin_draft:
            linkedin_draft = translate_to_english(client, linkedin_draft)
            posts.append(build_post_record(cluster, score, label, linkedin_draft, i, timestamp,
                                           "linkedin", primary_fetch=primary_fetch))
            print(f"        -> LinkedIn ({len(linkedin_draft.split())} words)")
        else:
            print(f"        -> LinkedIn skipped (API error)")

        # OUTPUT B — Website article (with deep background research)
        print(f"        -> Gathering background research...")
        deep = gather_deep_research(cluster)
        website_draft = draft_website_article(client, cluster, bundle, deep)
        if website_draft:
            website_draft = translate_to_english(client, website_draft)
            posts.append(build_post_record(cluster, score, label, website_draft, i, timestamp,
                                           "website", primary_fetch=primary_fetch))
            print(f"        -> Website article ({len(website_draft.split())} words)")
        else:
            print(f"        -> Website article skipped (API error)")

    if not posts:
        print("\n  No posts drafted. Check your API key and try again.")
        raise SystemExit(1)

    save_queue(posts, timestamp, total_stories_scanned=len(stories))
    append_to_archive(posts, timestamp)

    # Regenerate homepage feed, news archive, and wire section with fresh data
    import publisher as _publisher
    try:
        _publisher.update_homepage()
        _publisher.update_news_archive()
        _publisher.update_wire()
    except Exception as _e:
        print(f"  [page regeneration warning] {_e}")

    print("\n  Done. Open the dashboard to review your posts.\n")


if __name__ == "__main__":
    main()
