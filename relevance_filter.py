#!/usr/bin/env python3
"""
IWF Relevance Filter
--------------------
score_relevance(story, source) decides whether a story belongs in the IWF
pipeline at all, in two stages:

  1. Keyword pre-filter (free, instant) — no IWF keyword, score 0, no API call.
  2. Claude Haiku editorial scoring against the IWF A/B/C/D categories.

Scores route the story:
   0-5  reject
   6-7  wire only (Bluesky wire post + website wire feed)
   8-10 full pipeline (article draft, Telegram approval, publish)

Results are cached by URL in relevance_cache.json for 7 days, because
run_wire.py re-polls the same 72-hour window every 3 hours and would
otherwise pay to re-score the same stories eight times a day.
"""

import datetime
import json
import os
import re

import anthropic
from dotenv import load_dotenv

HERE       = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "relevance_cache.json")
load_dotenv(os.path.join(HERE, ".env"))

MODEL      = "claude-haiku-4-5-20251001"
MAX_TOKENS = 200

REJECT_MAX = 5    # 0-5   -> reject
WIRE_MIN   = 6    # 6-7   -> wire only
FULL_MIN   = 8    # 8-10  -> full pipeline

CACHE_DAYS = 7

# Bump whenever SYSTEM_PROMPT or the enforcement rules change, so cached
# scores made under the old rules are thrown away and stories are re-scored.
PROMPT_VERSION = 3

FR_KEYWORDS = [
    "islam", "musulman", "musulmane", "musulmans", "mosquée", "islamophobie",
    "hijab", "voile", "laïcité", "laicite", "ccie", "ccif", "halal", "imam",
    "ramadan", "frères musulmans", "séparatisme", "séparatiste", "entrisme",
    "islamiste", "islamisme", "identitaire", "xénophobie", "discrimination",
    "racisme", "raciste", "minorité", "communauté musulmane", "abaya", "niqab",
    "burkini",
]
EN_KEYWORDS = [
    "islam", "muslim", "muslims", "mosque", "islamophobia", "hijab", "veil",
    "secularism", "brotherhood", "separatism", "islamist", "discrimination",
    "minority rights", "hate crime", "far-right france", "laicite",
]

# Match at the start of a word only, so "veil" catches "veiled" but not
# "surveillance", and "islam" catches "islamophobe" / "islamique".
_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(set(FR_KEYWORDS + EN_KEYWORDS), key=len, reverse=True)) + r")",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """
You are the editorial filter for Islamophobia Watch France (IWF), an English-language journalism project with this mission: monitoring anti-Muslim discrimination, Islamophobia, and related policy in France and Europe, while also covering Muslim community life, culture, and achievement.

EDITORIAL CATEGORIES:

Category A - Cover always (score 8-10):
Documented Islamophobia incidents, mosque attacks, legal cases involving discrimination, legislation targeting Muslim practice, official statistics on anti-Muslim acts, civil society reports, Muslim community achievements, French Muslim figures in public life.

Category B - Cover with caution (score 6-7):
Stories framing Muslims through security lens (only if discrimination angle exists), laïcité debates (only if Muslim community impact is specific), immigration stories (only if Muslim angle is explicit), politician statements about Islam (only if named and attributed).

Category C - Reject (score 0-3):
Stories framing Muslims as security threat with no discrimination angle, Islamophobic claims presented as fact without challenge, terrorism stories with no discrimination dimension, general crime with religion mentioned incidentally.

Category D - Cover as documented Islamophobia (score 6-9):
Stories where a named politician, publication, broadcaster, or influencer makes anti-Muslim statements or publishes Islamophobic content. The Islamophobia IS the story. INVERSION REQUIRED: the actor making the claim is the subject, never the Muslim community.

BIAS CORRECTION RULE: If the source has bias rating "far-right" or "monitor-only", the story can only be Category D - document the Islamophobia, never relay the claim as news.

GEOGRAPHIC RULE (mandatory - IWF covers France first):
Set geo_scope:
- "france": the story happens in France or directly involves French institutions, politicians, media, courts, law, or Muslims in France (including French actors abroad and French overseas territories).
- "europe": the story is about another European country (including the UK) or EU institutions, with no direct French involvement.
- "other": anywhere outside Europe with no French involvement.

Set islamophobia_central to true when the story's MAIN subject is Islamophobia or anti-Muslim discrimination: an anti-Muslim incident or attack, a law, ban or policy restricting Muslim practice (e.g. headscarf, hijab, niqab or halal bans), a court case about such discrimination, official statistics or a monitoring report on anti-Muslim acts, or a named actor's anti-Muslim statements. Set it to false when Islamophobia is only background or motivation (art, culture, achievements, religious life, general immigration or security).

Scoring by geo_scope:
- "france": apply the categories above normally.
- "europe" with islamophobia_central true: European stories are welcome - score them with the categories above exactly as you would a French story (a record rise in anti-Muslim hate crime in another European country is Category A). The lack of a French angle is NOT a reason to lower the score.
- "europe" with islamophobia_central false: score 0-3, Category C.
- "other": always score 0-3, Category C.

SOURCE BIAS: {source_bias} | TRUST: {source_trust}

Respond in JSON only:
{{"score": 0-10, "category": "A/B/C/D", "reason": "one sentence", "inversion_required": true/false, "geo_scope": "france/europe/other", "islamophobia_central": true/false}}
"""

_client = None
_cache  = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = {}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    _cache = json.load(f)
            except (OSError, ValueError):
                _cache = {}
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=CACHE_DAYS)).isoformat()
        _cache = {u: r for u, r in _cache.items()
                  if r.get("scored_at", "") > cutoff and r.get("prompt_version") == PROMPT_VERSION}
    return _cache


def _save_cache() -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=1, ensure_ascii=False)
    except OSError as e:
        print(f"  [WARNING] Could not save relevance cache: {e}")


def _source_fields(story: dict, source) -> tuple:
    """Returns (name, bias, trust), accepting a sources.json entry, a story dict, or a name."""
    if isinstance(source, dict):
        name  = source.get("name") or source.get("source") or story.get("source", "")
        bias  = source.get("bias") or source.get("source_bias")
        trust = source.get("trust") or source.get("source_trust")
    else:
        name, bias, trust = (source or story.get("source", "")), None, None
    return (name,
            bias or story.get("source_bias", "unknown"),
            trust or story.get("source_trust", "medium"))


def _is_monitor_only(bias: str, trust: str) -> bool:
    return bias == "far-right" or trust == "monitor-only"


def bias_flag(bias: str, trust: str) -> str:
    if _is_monitor_only(bias, trust):
        risk = "HIGH risk - Category D only, never relay claims"
    elif "state" in bias:
        risk = "state-linked - verify claims independently"
    elif trust == "medium":
        risk = "medium risk"
    else:
        risk = "low risk"
    return f"{bias} - {risk}"


def matched_keywords(story: dict) -> list:
    text = f"{story.get('title', '')} {story.get('summary', '')}"
    return sorted({m.lower() for m in _KEYWORD_RE.findall(text)})


def passes_geo_rule(story: dict) -> bool:
    """True if a scored story meets the France / European-Islamophobia rule."""
    geo = story.get("geo_scope")
    return geo == "france" or (geo == "europe" and bool(story.get("islamophobia_central")))


def apply_score(story: dict, result: dict) -> dict:
    """Copy a score_relevance() result onto the story dict (fields used downstream)."""
    story["relevance_score"]      = result["score"]
    story["relevance_category"]   = result["category"]
    story["relevance_reason"]     = result["reason"]
    story["inversion_required"]   = result["inversion_required"]
    story["bias_flag"]            = result["bias_flag"]
    story["geo_scope"]            = result.get("geo_scope", "unknown")
    story["islamophobia_central"] = result.get("islamophobia_central", False)
    return story


def route(score: int) -> str:
    """'reject', 'wire' or 'full' for a relevance score."""
    if score >= FULL_MIN:
        return "full"
    if score >= WIRE_MIN:
        return "wire"
    return "reject"


def _parse_response(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return {
            "score":              max(0, min(10, int(data.get("score", 5)))),
            "category":           str(data.get("category", "C")).strip().upper()[:1] or "C",
            "reason":             str(data.get("reason", "")).strip(),
            "inversion_required": bool(data.get("inversion_required", False)),
            "geo_scope":          str(data.get("geo_scope", "other")).strip().lower(),
            "islamophobia_central": bool(data.get("islamophobia_central", False)),
        }
    except (ValueError, TypeError):
        return None


def score_relevance(story: dict, source=None) -> dict:
    """
    Scores one story for IWF relevance. `source` may be the sources.json entry,
    the story dict itself, or just the source name.

    Returns {"score", "category", "reason", "inversion_required", "bias_flag"}.
    """
    name, bias, trust = _source_fields(story, source)
    flag = bias_flag(bias, trust)

    # ── Stage 1: keyword pre-filter ──────────────────────────
    if not matched_keywords(story):
        return {"score": 0, "category": "C", "reason": "No IWF keywords found",
                "inversion_required": False, "bias_flag": flag,
                "geo_scope": "unknown", "islamophobia_central": False}

    url   = story.get("url", "")
    cache = _load_cache()
    if url and url in cache:
        cached = dict(cache[url])
        cached.pop("scored_at", None)
        cached.pop("prompt_version", None)
        return cached

    # ── Stage 2: Claude Haiku editorial scoring ──────────────
    user_message = (f"Title: {story.get('title', '')}\n"
                    f"Summary: {story.get('summary', '')}\n"
                    f"Source: {name}")
    try:
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT.format(source_bias=bias, source_trust=trust),
            messages=[{"role": "user", "content": user_message}],
        )
        parsed = _parse_response(response.content[0].text)
    except Exception as e:
        print(f"  [WARNING] Relevance scoring failed for '{story.get('title', '')[:50]}': {e}")
        parsed = None

    if parsed is None:
        # Not cached, so the story is re-scored on the next run.
        return {"score": 5, "category": "C", "reason": "Scoring unavailable - fallback score",
                "inversion_required": False, "bias_flag": flag,
                "geo_scope": "unknown", "islamophobia_central": False}

    # Enforce the bias correction rule in code too: a far-right / monitor-only
    # source can only ever enter the pipeline as documented Islamophobia.
    if _is_monitor_only(bias, trust):
        if parsed["category"] != "D":
            parsed["score"] = min(parsed["score"], 3)
            parsed["category"] = "C"
            parsed["reason"] = f"Monitor-only source, not Category D: {parsed['reason']}"
        else:
            parsed["inversion_required"] = True
    if parsed["category"] == "D":
        parsed["inversion_required"] = True

    # Enforce the geographic rule in code: no France angle and no central
    # Islamophobia angle in Europe means the story never enters the pipeline.
    geo_reject = None
    if parsed["geo_scope"] not in ("france", "europe"):
        geo_reject = "No France angle"
    elif parsed["geo_scope"] == "europe" and not parsed["islamophobia_central"]:
        geo_reject = "European story without Islamophobia as the central subject"
    if geo_reject:
        parsed["score"] = min(parsed["score"], 3)
        parsed["category"] = "C"
        parsed["inversion_required"] = False
        parsed["reason"] = f"{geo_reject}: {parsed['reason']}"

    result = {**parsed, "bias_flag": flag}
    if url:
        cache[url] = {**result, "prompt_version": PROMPT_VERSION,
                      "scored_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        _save_cache()
    return result


if __name__ == "__main__":
    test = {"title": "Mosque vandalized in Lyon",
            "summary": "A mosque in Lyon was targeted overnight with Islamophobic graffiti",
            "source": "Lyon Capitale", "source_bias": "centrist-local",
            "source_trust": "high", "language": "EN"}
    print(score_relevance(test, test))
