#!/usr/bin/env python3
"""
IWF News Aggregator
-------------------
Polls the RSS feeds listed in sources.json directly (no Google News),
keeps everything published in the last HOURS_BACK hours, and returns
the stories newest first.

Run with:  python3 iwf_aggregator.py

EASY TO CUSTOMISE:
- Add/remove/disable sources in sources.json (or the dashboard's
  "Manage Sources" tab)
- Change HOURS_BACK to widen or narrow the time window
"""

import datetime
import gzip
import json
import os
import re
import textwrap
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import feedparser


# =============================================================
# CONFIGURATION
# =============================================================

HERE           = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE   = os.path.join(HERE, "sources.json")
WIRE_SEEN_FILE = os.path.join(HERE, "wire_seen.json")

# HOW MANY HOURS BACK to include articles from. 72 = last three days.
HOURS_BACK = 72

# Seconds to wait for each feed before giving up on it.
FEED_TIMEOUT = 15

# Some publishers (Le Figaro, La Dépêche, Le Progrès…) refuse the default
# feedparser/urllib user agent, so present as a normal desktop browser.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


# =============================================================
# HELPERS
# =============================================================

def get_pub_time(entry):
    """
    Returns a datetime object for an article, or a very old date as fallback.
    """
    for attr in ("published_parsed", "updated_parsed"):
        parsed = entry.get(attr)
        if parsed:
            return datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
    return datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)


def is_recent(entry, hours_back):
    """
    Returns True if the article is within the HOURS_BACK window.
    Entries with no date at all are kept (year 2000 sentinel).
    """
    pub_time = get_pub_time(entry)
    if pub_time.year == 2000:
        return True
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=hours_back))
    return pub_time >= cutoff


def simplify_title(title):
    return title.lower().strip()


def clean_summary(raw):
    """
    Strips HTML tags and extra whitespace from a summary string.
    """
    text = re.sub(r'<[^>]+>', '', raw or "")
    text = re.sub(r'\s+', ' ', text).strip()
    # Truncate to 1500 characters so more story detail reaches Claude
    if len(text) > 1500:
        text = text[:1497] + "..."
    return text if text else "No summary available."


def format_time_ago(pub_time):
    """
    Returns a human-readable string like '3h ago' or '1d 4h ago'.
    Accepts a datetime or an ISO-format string.
    """
    if isinstance(pub_time, str):
        try:
            pub_time = datetime.datetime.fromisoformat(pub_time.replace("Z", "+00:00"))
        except ValueError:
            return "unknown time"
    if pub_time.year == 2000:
        return "unknown time"

    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - pub_time
    total_hours = int(delta.total_seconds() // 3600)
    days = total_hours // 24
    hours = total_hours % 24

    if days > 0:
        return f"{days}d {hours}h ago"
    elif hours > 0:
        return f"{hours}h ago"
    else:
        minutes = int(delta.total_seconds() // 60)
        return f"{minutes}m ago"


def wrap(text, width=60, indent="      "):
    return textwrap.fill(text, width=width,
                         initial_indent=indent,
                         subsequent_indent=indent)


# =============================================================
# SOURCES + SEEN-URL TRACKING
# =============================================================

def load_sources(enabled_only=True):
    """Returns the source list from sources.json (enabled ones only by default)."""
    if not os.path.exists(SOURCES_FILE):
        print(f"  [WARNING] {SOURCES_FILE} not found — no sources to poll.")
        return []
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        sources = json.load(f).get("sources", [])
    if enabled_only:
        sources = [s for s in sources if s.get("enabled", True)]
    return sources


def load_wire_seen() -> tuple:
    """Load wire_seen.json, purge entries older than 24 h, return (data, set of seen URLs)."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    data = {"entries": []}
    if os.path.exists(WIRE_SEEN_FILE):
        try:
            with open(WIRE_SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            pass
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


def save_wire_seen(data: dict, new_urls: list) -> None:
    """Append new URLs to wire_seen data and write to disk."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for url in new_urls:
        data["entries"].append({"url": url, "seen_at": now})
    with open(WIRE_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# =============================================================
# FETCHING
# =============================================================

_SITEMAP_NS = {
    "sm":   "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


def _parse_news_sitemap(raw, source, hours_back):
    """
    Reads a Google News sitemap (for publishers with no RSS, e.g. La Provence).
    Sitemaps carry a title and date but no summary.
    """
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=hours_back))
    results = []
    for url_el in ET.fromstring(raw).findall("sm:url", _SITEMAP_NS):
        url   = url_el.findtext("sm:loc", "", _SITEMAP_NS).strip()
        title = url_el.findtext("news:news/news:title", "", _SITEMAP_NS)
        date  = url_el.findtext("news:news/news:publication_date", "", _SITEMAP_NS)
        if not url or not title:
            continue
        try:
            pub_time = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
            pub_time = pub_time.astimezone(datetime.timezone.utc)
        except ValueError:
            pub_time = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
        if pub_time.year != 2000 and pub_time < cutoff:
            continue
        results.append({
            "title":        clean_summary(title),
            "summary":      "No summary available.",
            "url":          url,
            "published":    pub_time.isoformat(),
            "source":       source["name"],
            "source_bias":  source.get("bias", "unknown"),
            "source_trust": source.get("trust", "medium"),
            "language":     source.get("language", "FR"),
        })
    return results


def fetch_source(source, hours_back=HOURS_BACK):
    """
    Fetches one source's RSS feed and returns its recent entries as story dicts.
    Never raises — a broken feed just returns [] and prints a warning.
    """
    results = []
    try:
        req = urllib.request.Request(source["url"], headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as resp:
            raw = resp.read()
        # Some servers (Middle East Eye) send gzip bodies without a
        # Content-Encoding header, so urllib hands back compressed bytes.
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        if source.get("format") == "news-sitemap":
            return _parse_news_sitemap(raw, source, hours_back)
        feed = feedparser.parse(raw)
        if not feed.entries and getattr(feed, "bozo", False):
            print(f"  [WARNING] {source['name']}: unreadable feed ({feed.bozo_exception})")
            return []

        for entry in feed.entries:
            if not is_recent(entry, hours_back):
                continue
            url = entry.get("link", "")
            if not url:
                continue
            raw_summary = entry.get("summary") or entry.get("description") or ""
            results.append({
                "title":        clean_summary(entry.get("title", "(no title)")),
                "summary":      clean_summary(raw_summary),
                "url":          url,
                "published":    get_pub_time(entry).isoformat(),
                "source":       source["name"],
                "source_bias":  source.get("bias", "unknown"),
                "source_trust": source.get("trust", "medium"),
                "language":     source.get("language", "FR"),
            })
    except Exception as e:
        print(f"  [WARNING] {source.get('name', '?')}: fetch failed ({e})")
    return results


def fetch_all_stories(hours_back=HOURS_BACK, exclude_seen=False, verbose=True):
    """
    Polls every enabled source in sources.json and returns stories newest first,
    deduplicated by URL.

    exclude_seen=True also drops URLs already recorded in wire_seen.json (the
    24-hour seen list). It is off by default because run_wire.py polls every
    3 hours — if it consumed the seen list, the 7am/6pm drafter runs would
    find nothing new to draft.

    Also sets fetch_all_stories.last_stats = {"sources_polled", "sources_ok", "raw"}.
    """
    sources = load_sources()
    if verbose:
        print(f"  Polling {len(sources)} sources...")

    with ThreadPoolExecutor(max_workers=8) as pool:
        per_source = list(pool.map(lambda s: fetch_source(s, hours_back), sources))

    raw_count = sum(len(r) for r in per_source)
    seen_urls = load_wire_seen()[1] if exclude_seen else set()

    unique, urls = [], set()
    for story in (s for batch in per_source for s in batch):
        if story["url"] in urls or story["url"] in seen_urls:
            continue
        urls.add(story["url"])
        unique.append(story)

    unique.sort(key=lambda s: s["published"], reverse=True)

    fetch_all_stories.last_stats = {
        "sources_polled": len(sources),
        "sources_ok":     sum(1 for r in per_source if r),
        "raw":            raw_count,
    }
    if verbose:
        print(f"  Found {len(unique)} unique stories in the last {hours_back}h "
              f"({fetch_all_stories.last_stats['sources_ok']}/{len(sources)} sources returned items).")
    return unique


fetch_all_stories.last_stats = {"sources_polled": 0, "sources_ok": 0, "raw": 0}


# =============================================================
# MAIN — print a digest to the terminal
# =============================================================

def print_digest(articles):
    if not articles:
        print(f"\n  No articles found in the last {HOURS_BACK} hours.\n")
        return

    print(f"\n{'='*65}")
    print(f"  IWF NEWS DIGEST")
    print(f"  Last {HOURS_BACK}h  |  {len(articles)} articles")
    print(f"  {datetime.datetime.now().strftime('%d %b %Y  %H:%M')}")
    print(f"{'='*65}")

    for i, art in enumerate(articles, 1):
        print(f"\n  [{i:02d}]  {format_time_ago(art['published'])}  |  {art['source']}")
        print(f"  {'─'*61}")
        print(wrap(art["title"], width=61, indent="  "))
        if art["summary"] and art["summary"] != "No summary available.":
            print()
            print(wrap(art["summary"], width=61, indent="  "))
        print()
        print(f"  -> {art['url']}")

    print(f"\n{'='*65}\n")


def main():
    print_digest(fetch_all_stories())


if __name__ == "__main__":
    main()
