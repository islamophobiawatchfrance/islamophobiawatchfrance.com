#!/usr/bin/env python3
"""
IWF Story Dedupe
----------------
Shared helpers for telling whether two items are the same story, used by
post_wire_stories.py (don't re-post a story another outlet already broke)
and weekly_digest.py (each story once in "France this week").

  canonical_url / normalise_title   exact matching
  titles_match                      fuzzy title matching
  source_rank                       source quality from sources.json
  ask_json                          one Claude call that returns JSON
"""

import difflib
import json
import os
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import anthropic
from dotenv import load_dotenv

HERE         = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "sources.json")
load_dotenv(os.path.join(HERE, ".env"))

FUZZY_RATIO   = 0.80       # difflib ratio on normalised titles
FUZZY_JACCARD = 0.60       # overlap of significant title words (names, nouns)

CLUSTER_MODEL = "claude-haiku-4-5-20251001"

STOPWORDS = set("""
the and for with from that this after over into about their they them his her its are was were
has have had been will would says said say new against amid during under more than not but who
what when where which while les des une dans pour avec sur par est sont qui que aux du au
""".split())


def canonical_url(url: str) -> str:
    """Lower-cased scheme/host without www., no query string, fragment or trailing slash."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."),
                       parts.path.rstrip("/"), "", ""))


def normalise_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


def key_words(title: str) -> set:
    return {w for w in normalise_title(title).split() if len(w) >= 4 and w not in STOPWORDS}


def titles_match(a: str, b: str) -> bool:
    """True for near-identical titles: high character similarity or heavy keyword overlap."""
    if difflib.SequenceMatcher(None, normalise_title(a), normalise_title(b)).ratio() >= FUZZY_RATIO:
        return True
    wa, wb = key_words(a), key_words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= FUZZY_JACCARD


def load_sources() -> dict:
    """sources.json entries keyed by lower-cased source name."""
    try:
        with open(SOURCES_FILE, "r", encoding="utf-8") as f:
            sources = json.load(f).get("sources", [])
    except (OSError, ValueError):
        sources = []
    return {s["name"].strip().lower(): s for s in sources if isinstance(s, dict) and s.get("name")}


def source_rank(name: str, sources: dict) -> int:
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


def ask_json(model: str, system: str, user: str, max_tokens: int, **extra) -> dict:
    """One Claude call; returns the JSON object in the reply. Raises on refusal or no JSON."""
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
