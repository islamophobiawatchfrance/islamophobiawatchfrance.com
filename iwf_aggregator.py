#!/usr/bin/env python3
"""
IWF News Aggregator
-------------------
Uses Google News RSS to find the latest articles matching
your search queries. Sorts results by time, newest first.

Run with:  python3 iwf_aggregator.py

EASY TO CUSTOMISE:
- Add/remove search queries in the QUERIES section below
- Change HOURS_BACK to widen or narrow the time window
- Change MAX_RESULTS to get more or fewer articles per query
"""

import feedparser
import datetime
import urllib.parse
import re
import textwrap


# =============================================================
# CONFIGURATION - this is the only section you need to edit
# =============================================================

# SEARCH QUERIES
# Each line is a separate Google News search.
# Use quotes around phrases: "like this"
# Combine terms with +: islamophobie+france
# Google News will search across all major French and English sources.
#
# FR = French language results | EN = English language results
# You can mix both freely.

QUERIES = [

    # --- Core topic (French) ---
    ("islamophobie france",                "FR"),
    ("musulmans france",                   "FR"),
    ("mosquée france attaque",             "FR"),
    ("voile hijab france loi",             "FR"),
    ("laïcité france islam",               "FR"),
    ("frères musulmans france",            "FR"),
    ("islamisme france retailleau",        "FR"),

    # --- Key organisations & figures (French) ---
    ("CCIE islamophobie",                  "FR"),
    ("CFCM islam france",                  "FR"),
    ("Grande Mosquée Paris",               "FR"),

    # --- Incidents (French) ---
    ("agression islamophobe france",       "FR"),
    ("mosquée incendie france",            "FR"),

    # --- English language coverage ---
    ("islamophobia france",                "EN"),
    ("french muslims",                     "EN"),
    ("hijab ban france",                   "EN"),
    ("secularism france muslims",          "EN"),

    # --- European context ---
    ("islamophobia europe",                "EN"),

]

# HOW MANY HOURS BACK to include articles from.
# 48 = last two days. Use 24 for tighter, 72 for wider.
HOURS_BACK = 48

# MAX RESULTS per query. Keep this low (3-5) to avoid duplicates.
MAX_RESULTS = 4

# DEDUPLICATE: skip articles whose title is very similar to one already shown.
# True = on (recommended). False = show everything including near-duplicates.
DEDUPLICATE = True


# =============================================================
# CORE FUNCTIONS - you don't need to edit below this line
# =============================================================

def build_google_news_url(query, language_code):
    """
    Builds a Google News RSS URL for a given search query.
    language_code: "FR" for French results, "EN" for English.
    """
    encoded_query = urllib.parse.quote(query)

    if language_code == "FR":
        return (f"https://news.google.com/rss/search"
                f"?q={encoded_query}&hl=fr&gl=FR&ceid=FR:fr")
    else:
        return (f"https://news.google.com/rss/search"
                f"?q={encoded_query}&hl=en&gl=GB&ceid=GB:en")


def get_pub_time(entry):
    """
    Returns a datetime object for an article, or a very old date as fallback.
    """
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime.datetime(
            *entry.published_parsed[:6],
            tzinfo=datetime.timezone.utc
        )
    return datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)


def is_recent(entry, hours_back):
    """
    Returns True if the article is within the HOURS_BACK window.
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
    Google News summaries often contain HTML like <b> or <a href>.
    """
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', raw)
    # Collapse multiple spaces and newlines into one space
    text = re.sub(r'\s+', ' ', text).strip()
    # Truncate to 1500 characters so more story detail reaches Claude
    if len(text) > 1500:
        text = text[:1497] + "..."
    return text if text else "No summary available."


def wrap(text, width=60, indent="      "):
    """
    Word-wraps a string to fit neatly in the terminal.
    """
    return textwrap.fill(text, width=width,
                         initial_indent=indent,
                         subsequent_indent=indent)


def fetch_query(query, language_code, hours_back, max_results):
    """
    Fetches one Google News RSS query and returns matching articles as a list.
    """
    results = []
    url = build_google_news_url(query, language_code)

    try:
        feed = feedparser.parse(url)

        count = 0
        for entry in feed.entries:
            if count >= max_results:
                break
            if not is_recent(entry, hours_back):
                continue

            raw_title = getattr(entry, "title", "(no title)")
            if " - " in raw_title:
                title, source = raw_title.rsplit(" - ", 1)
            else:
                title = raw_title
                source = "Unknown"

            # Extract and clean the summary from the RSS entry
            raw_summary = getattr(entry, "summary", "")
            summary = clean_summary(raw_summary)

            results.append({
                "source":    source.strip(),
                "title":     title.strip(),
                "url":       getattr(entry, "link", "(no link)"),
                "published": get_pub_time(entry),
                "query":     query,
                "summary":   summary,
            })
            count += 1

    except Exception as e:
        print(f"  [WARNING] Failed on query '{query}': {e}")

    return results


def format_time_ago(pub_time):
    """
    Returns a human-readable string like '3h ago' or '1d 4h ago'.
    """
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


def print_digest(articles):
    """
    Prints the final digest sorted by time, newest first.
    Each article shows: time, source, headline, summary, link.
    """
    if not articles:
        print(f"\n  No articles found in the last {HOURS_BACK} hours.")
        print("  Try increasing HOURS_BACK in the configuration section.\n")
        return

    articles.sort(key=lambda a: a["published"], reverse=True)

    if DEDUPLICATE:
        seen = set()
        unique = []
        for art in articles:
            key = simplify_title(art["title"])
            if key not in seen:
                seen.add(key)
                unique.append(art)
        articles = unique

    print(f"\n{'='*65}")
    print(f"  IWF NEWS DIGEST")
    print(f"  Last {HOURS_BACK}h  |  {len(articles)} articles")
    print(f"  {datetime.datetime.now().strftime('%d %b %Y  %H:%M')}")
    print(f"{'='*65}")

    for i, art in enumerate(articles, 1):
        time_str = format_time_ago(art["published"])

        print(f"\n  [{i:02d}]  {time_str}  |  {art['source']}")
        print(f"  {'─'*61}")

        # Headline - wrapped so long titles don't run off screen
        print(wrap(art["title"], width=61, indent="  "))

        # Summary
        if art["summary"] and art["summary"] != "No summary available.":
            print()
            print(wrap(art["summary"], width=61, indent="  "))

        # Link on its own line
        print()
        print(f"  -> {art['url']}")

    print(f"\n{'='*65}")
    print(f"  End of digest  |  {len(articles)} articles  |  last {HOURS_BACK}h")
    print(f"{'='*65}\n")


# =============================================================
# MAIN
# =============================================================

def main():
    total_queries = len(QUERIES)
    print(f"\nRunning {total_queries} searches on Google News...\n")

    all_articles = []

    for query, lang in QUERIES:
        lang_label = "FR" if lang == "FR" else "EN"
        print(f"  [{lang_label}] {query}...")
        articles = fetch_query(query, lang, HOURS_BACK, MAX_RESULTS)
        all_articles.extend(articles)
        if articles:
            print(f"        -> {len(articles)} article(s) found")

    print()
    print_digest(all_articles)


if __name__ == "__main__":
    main()