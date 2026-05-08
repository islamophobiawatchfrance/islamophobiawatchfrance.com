#!/usr/bin/env python3
"""
IWF Publisher
-------------
Generates article HTML, updates index.html and news.html,
then commits and pushes to GitHub so the site goes live.

Called by app.py when a post is approved in the dashboard.
"""

import html as _html
import json
import math
import os
import re
import subprocess
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TEMPLATE_FILE  = "article-template.html"
PUBLISHED_FILE = "published.json"
WIRE_FILE      = "wire.json"
SITEMAP_FILE   = "sitemap.xml"
INDEX_FILE     = "index.html"
NEWS_FILE      = "news.html"
NEWS_DIR       = "news"

CATEGORY_MAP = [
    # Check longer / accented keywords first to avoid false short-word matches
    ("islamophobie",  "Islamophobia"),
    ("islamophobia",  "Islamophobia"),
    ("agression",     "Islamophobia"),
    ("mosquée",       "Islamophobia"),
    ("mosquee",       "Islamophobia"),
    ("attaque",       "Islamophobia"),
    ("laïcité",       "Policy & Law"),
    ("laicite",       "Policy & Law"),
    ("voile",         "Policy & Law"),
    ("hijab",         "Policy & Law"),
    ("communauté",    "Muslim Life"),
    ("communaute",    "Muslim Life"),
    ("musulmans",     "Muslim Life"),
    ("muslims",       "Muslim Life"),
    ("europe",        "European Context"),
]


# ── Helpers ──────────────────────────────────────────────


def _esc(s) -> str:
    """HTML-escape a value for safe insertion into attributes and text nodes."""
    return _html.escape(str(s or ""), quote=True)


def _ascii(s: str) -> str:
    """Strip accents and return ASCII-only string."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _derive_category(post: dict) -> str:
    text = _ascii((post.get("title", "") + " " + post.get("draft", "")).lower())
    for keyword, category in CATEGORY_MAP:
        if _ascii(keyword) in text:
            return category
    return "News"


def _format_date(post: dict) -> str:
    """Return human-readable date e.g. '7 May 2026'."""
    raw = post.get("published") or post.get("created") or ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%-d %B %Y")
    except Exception:
        return datetime.now(timezone.utc).strftime("%-d %B %Y")


def _date_prefix(post: dict) -> str:
    """Return YYYY-MM-DD for use in filenames."""
    raw = post.get("published") or post.get("created") or ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _short_date(date_str: str) -> str:
    """'7 May 2026' -> '7 May'"""
    parts = date_str.split()
    return " ".join(parts[:2]) if len(parts) >= 2 else date_str


def _heat_cls(heat_label: str) -> str:
    return {"HOT": "heat-hot", "TRENDING": "heat-trending"}.get(
        heat_label.upper(), "heat-normal"
    )


def _heat_icon(heat_label: str) -> str:
    return {"HOT": "🔥", "TRENDING": "📈"}.get(heat_label.upper(), "📰")


def _meta_description(draft: str) -> str:
    """155-char meta description: first paragraph stripped of hashtags and Source lines."""
    desc = re.sub(r"(?m)^Source:.*$", "", draft, flags=re.IGNORECASE).strip()
    desc = re.sub(r"#\S+", "", desc).strip()
    first = desc.split("\n\n")[0].strip()
    first = re.sub(r"\s+", " ", first)
    if len(first) > 155:
        first = first[:155].rstrip(" .,;") + "…"
    return first


def _excerpt(draft: str, max_chars: int = 200) -> str:
    """First paragraph of draft, trimmed to max_chars."""
    first = (draft.split("\n\n")[0] if draft else "").strip()
    if len(first) <= max_chars:
        return first
    return first[:max_chars].rstrip(" .,;") + "…"


# ── Public API ────────────────────────────────────────────


_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "has", "have", "by", "from",
    "its", "this", "that", "over", "about", "after", "before", "against",
    "between", "into", "through", "during", "without", "within", "along",
    "following", "across", "behind", "beyond", "plus", "except", "up",
    "out", "around", "down", "off", "above", "below",
}


def generate_slug(title: str, max_chars: int = 60) -> str:
    """
    Convert a headline to a SEO-optimised URL slug.
    Strips stop words, lowercase ASCII, hyphens, max 60 chars at word boundary.
    """
    slug = _ascii(title).lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    words = [w.strip("-") for w in slug.split() if w.strip("-") and w.strip("-") not in _STOP_WORDS]
    slug = re.sub(r"-+", "-", "-".join(words)).strip("-")
    if len(slug) <= max_chars:
        return slug
    truncated = slug[:max_chars]
    last_hyphen = truncated.rfind("-")
    return truncated[:last_hyphen] if last_hyphen > 0 else truncated


_WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
_WIKIMEDIA_UA  = "IWF-Aggregator/1.0 (https://islamophobiawatchfrance.com)"

_CATEGORY_FALLBACK_QUERIES = {
    "Islamophobia":     "mosque france islam",
    "Policy & Law":     "france parliament protest",
    "Muslim Life":      "france mosque muslim community",
    "European Context": "europe france mosque",
    "News":             "france paris",
}


def fetch_wikimedia_image(keywords: list, category: str) -> dict | None:
    """
    Search Wikimedia Commons for a relevant image (JPEG/PNG only).
    Tries a focused keyword query first, then falls back to category terms.
    Returns {"url", "title", "author", "licence", "source", "commons_url"} or None.
    """
    fallback_q = _CATEGORY_FALLBACK_QUERIES.get(category, "france")
    # Category fallback first (reliable images), then title-specific keywords
    # Title keywords can be too literal (e.g. "roast pig") or abstract ("controversy")
    queries = [fallback_q]
    if keywords:
        queries.append(" ".join(keywords[:2]))

    def _api(params: dict) -> dict:
        qs  = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"{_WIKIMEDIA_API}?{qs}",
            headers={"User-Agent": _WIKIMEDIA_UA},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())

    def _first_photo(query: str) -> str | None:
        results = _api({
            "action": "query", "list": "search",
            "srsearch": query, "srnamespace": 6,
            "srlimit": 8, "format": "json",
        }).get("query", {}).get("search", [])
        for hit in results:
            if hit["title"].lower().endswith((".jpg", ".jpeg", ".png")):
                return hit["title"]
        return None

    try:
        file_title = None
        for q in queries:
            file_title = _first_photo(q)
            if file_title:
                break
        if not file_title:
            return None

        # Fetch image URL and licence metadata
        pages = _api({
            "action": "query", "titles": file_title,
            "prop": "imageinfo", "iiprop": "url|extmetadata",
            "format": "json",
        }).get("query", {}).get("pages", {})

        ii  = (next(iter(pages.values())).get("imageinfo") or [{}])[0]
        url = ii.get("url", "").split("?")[0]   # strip UTM tracking params
        if not url:
            return None

        meta    = ii.get("extmetadata", {})
        author  = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "Unknown")).strip() or "Unknown"
        licence = meta.get("LicenseShortName", {}).get("value", "Unknown")

        bare        = file_title.replace("File:", "").replace(" ", "_")
        commons_url = f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(bare)}"

        return {
            "url":         url,
            "title":       file_title.replace("File:", ""),
            "author":      author,
            "licence":     licence,
            "source":      "Wikimedia Commons",
            "commons_url": commons_url,
        }
    except Exception:
        return None


def generate_header_graphic(title: str, category: str, heat_label: str, date: str) -> str:
    """Return an inline SVG header graphic (fallback when no Wikimedia image is found)."""
    badge_color = {"HOT": "#ED2939", "TRENDING": "#D97706"}.get(heat_label.upper(), "#6B7280")

    # Word-wrap title to ~42 chars per line (fits ~740px at ~26px font)
    words, lines, current = title.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) > 42:
            if current:
                lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    tspans = "".join(
        f'<tspan x="30" y="{120 + i * 38}">{_esc(line)}</tspan>'
        for i, line in enumerate(lines[:4])
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 300" '
        f'width="100%" style="display:block;background:#0a0a0a">'
        f'<rect x="0"   y="0" width="6" height="300" fill="#ED2939"/>'
        f'<rect x="794" y="0" width="6" height="300" fill="#002395"/>'
        f'<text x="30" y="50" font-family="sans-serif" font-size="11" '
        f'fill="#ED2939" letter-spacing="1">{_esc(category.upper())}</text>'
        f'<text font-family="sans-serif" font-size="26" font-weight="500" fill="#ffffff">'
        f'{tspans}</text>'
        f'<text x="30" y="278" font-family="sans-serif" font-size="11" fill="#666">'
        f'{_esc(date)}</text>'
        f'<rect x="656" y="18" width="130" height="26" rx="4" fill="{badge_color}"/>'
        f'<text x="721" y="35" font-family="sans-serif" font-size="11" fill="#fff" '
        f'text-anchor="middle">{_esc(heat_label.upper())}</text>'
        f'</svg>'
    )


def generate_article_html(post: dict, published_articles: list = None) -> str:
    """
    Read article-template.html and substitute all {{placeholders}}.
    Returns the complete HTML string.
    """
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        tmpl = f.read()

    title      = post.get("title", "")
    draft      = post.get("draft", "")
    sources    = post.get("sources", [])
    heat_label = post.get("heat_label", "NORMAL")
    heat_score = str(post.get("heat_score", 0))
    slug       = generate_slug(title)
    date_str   = _format_date(post)
    category   = _derive_category(post)
    filename   = f"{_date_prefix(post)}-{slug}.html"

    raw_pub = post.get("published") or post.get("created") or ""
    try:
        published_iso = datetime.fromisoformat(raw_pub.replace("Z", "+00:00")).isoformat()
    except Exception:
        published_iso = datetime.now(timezone.utc).isoformat()

    # Header image: try Wikimedia Commons, fall back to generated SVG
    img_keywords = [
        w for w in re.sub(r"[^a-z0-9\s]", "", _ascii(title).lower()).split()
        if w not in _STOP_WORDS
    ][:4]
    wikimedia = fetch_wikimedia_image(img_keywords, category)
    if wikimedia:
        attr = (
            f'Image: <a href="{_esc(wikimedia["commons_url"])}" target="_blank" '
            f'rel="noopener noreferrer">{_esc(wikimedia["title"])}</a>'
            f' by {_esc(wikimedia["author"])} ({_esc(wikimedia["licence"])})'
            f' via Wikimedia Commons'
        )
        header_image_html = (
            f'<div class="art-header-img">\n'
            f'  <img src="{_esc(wikimedia["url"])}" alt="{_esc(title)}" loading="lazy">\n'
            f'  <p class="art-header-credit">{attr}</p>\n'
            f'</div>'
        )
    else:
        header_image_html = generate_header_graphic(title, category, heat_label, _format_date(post))

    excerpt_text      = _excerpt(draft, 160)
    meta_desc         = _meta_description(draft)
    sources_inline    = " / ".join(s.get("name", "") for s in sources) or post.get("source", "")
    page_url          = f"https://islamophobiawatchfrance.com/news/{filename}"
    title_encoded     = urllib.parse.quote(title)
    heat_article_count = str(post.get("heat_article_count", "Multiple"))
    reading_time      = str(max(1, math.ceil(len(draft.split()) / 200)))

    # Standfirst (first paragraph) + body (remaining, with pull-quote detection)
    paragraphs      = [p.strip() for p in re.split(r"\n\n+", draft) if p.strip()]
    standfirst_text = paragraphs[0] if paragraphs else ""
    body_paragraphs = paragraphs[1:] if len(paragraphs) > 1 else []

    body_lines = []
    for p in body_paragraphs:
        if p.startswith('"') or p.startswith('“') or p.startswith('‘'):
            body_lines.append(f'      <p class="pull-quote">{_esc(p)}</p>')
        else:
            body_lines.append(f'      <p>{_esc(p)}</p>')
    body_html = "\n".join(body_lines)

    # Inline source list (bottom of left column)
    source_items = "\n".join(
        f'      <div class="source-item">\n'
        f'        <span class="source-arrow">→</span>\n'
        f'        <a href="{_esc(s.get("url","#"))}" target="_blank" rel="noopener noreferrer">'
        f'{_esc(s.get("name","Source"))}</a>\n'
        f'      </div>'
        for s in sources
    )

    # Sidebar source cards
    sidebar_sources_html = "\n".join(
        f'      <div class="sidebar-source-card">\n'
        f'        <div class="sidebar-source-name">{_esc(s.get("name", "Source"))}</div>\n'
        f'        <a href="{_esc(s.get("url", "#"))}" target="_blank" rel="noopener noreferrer"'
        f' class="sidebar-source-link">Read original →</a>\n'
        f'      </div>'
        for s in sources
    )

    # Sidebar related articles
    related = [
        a for a in (published_articles or [])
        if a.get("slug") != slug
    ][-3:]

    if related:
        related_cards = []
        for rel in related:
            rel_title = _esc(rel.get("title", ""))
            rel_url   = _esc(rel.get("filename", "#"))
            rel_date  = _esc(_short_date(rel.get("date", "")))
            related_cards.append(
                f'        <div class="sidebar-related-card">\n'
                f'          <div class="sidebar-related-date">{rel_date}</div>\n'
                f'          <a href="{rel_url}" class="sidebar-related-title">{rel_title}</a>\n'
                f'        </div>'
            )
        sidebar_related_html = "\n".join(related_cards)
    else:
        sidebar_related_html = ""

    subs = {
        "{{header_image}}":       header_image_html,
        "{{title}}":              _esc(title),
        "{{excerpt}}":            _esc(excerpt_text),
        "{{meta_description}}":   _esc(meta_desc),
        "{{slug}}":               _esc(slug),
        "{{filename}}":           _esc(filename),
        "{{published_iso}}":      _esc(published_iso),
        "{{date}}":               _esc(date_str),
        "{{reading_time}}":       reading_time,
        "{{page_url}}":           _esc(page_url),
        "{{title_encoded}}":      title_encoded,
        "{{heat_badge_class}}":   _heat_cls(heat_label),
        "{{heat_icon}}":          _heat_icon(heat_label),
        "{{heat_label}}":         _esc(heat_label),
        "{{heat_score}}":         heat_score,
        "{{heat_article_count}}": heat_article_count,
        "{{category}}":           _esc(category),
        "{{sources_inline}}":     _esc(sources_inline),
        "{{standfirst}}":         _esc(standfirst_text),
        "{{body}}":               body_html,
        "{{sources}}":            source_items,
        "{{sidebar_sources}}":    sidebar_sources_html,
        "{{sidebar_related}}":    sidebar_related_html,
    }
    for placeholder, value in subs.items():
        tmpl = tmpl.replace(placeholder, value)

    # Strip the sidebar-related section entirely if there are no related articles
    if not related:
        tmpl = re.sub(
            r"\s*<!-- ── Related Articles.*?</div><!-- /\.sidebar-related -->",
            "",
            tmpl,
            flags=re.DOTALL,
        )

    return tmpl


def save_article(post: dict, repo_path: str = ".", published_articles: list = None) -> str:
    """
    Generate and save the article HTML.
    Returns the relative path e.g. 'news/2026-05-07-france-le-senat.html'
    """
    slug     = generate_slug(post.get("title", "article"))
    prefix   = _date_prefix(post)
    filename = f"{prefix}-{slug}.html"

    news_dir = os.path.join(repo_path, NEWS_DIR)
    os.makedirs(news_dir, exist_ok=True)

    article_html = generate_article_html(post, published_articles)
    filepath = os.path.join(news_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(article_html)

    rel_path = f"{NEWS_DIR}/{filename}"
    print(f"  Saved: {rel_path}")
    return rel_path


def update_homepage(repo_path: str = ".") -> None:
    """
    Replace the contents of .hero-feed in index.html with
    freshly generated feed items (newest first, max 10).
    Reads published.json directly so manual title edits are always reflected.
    """
    published_path = os.path.join(repo_path, PUBLISHED_FILE)
    if os.path.exists(published_path):
        with open(published_path, "r", encoding="utf-8") as f:
            published_articles = json.load(f).get("articles", [])
    else:
        published_articles = []

    index_path = os.path.join(repo_path, INDEX_FILE)
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    articles = sorted(published_articles, key=lambda a: a.get("date", ""), reverse=True)[:10]

    def _feed_item(a: dict) -> str:
        filename  = a.get("filename", "#")
        title     = _esc(a.get("title", ""))
        short_dt  = _esc(_short_date(a.get("date", "")))
        heat_lbl  = a.get("heat_label", "NORMAL")
        source    = _esc(a.get("sources", [{}])[0].get("name", "") or a.get("source", ""))
        expt      = _esc(_excerpt(a.get("draft", ""), 160))

        return (
            f'        <article class="news-feed-item">\n'
            f'          <div class="feed-date">{short_dt}</div>\n'
            f'          <div class="feed-content">\n'
            f'            <div class="feed-headline">\n'
            f'              <a href="{filename}">{title}</a>\n'
            f'            </div>\n'
            f'            <p class="feed-excerpt">{expt}</p>\n'
            f'            <div class="feed-meta">\n'
            f'              <span class="feed-source">{source}</span>\n'
            f'              <span class="badge {_heat_cls(heat_lbl)}">'
            f'{_heat_icon(heat_lbl)} {_esc(heat_lbl)}</span>\n'
            f'            </div>\n'
            f'          </div>\n'
            f'          <a href="{filename}" class="feed-arrow">→</a>\n'
            f'        </article>'
        )

    items_html = "\n\n".join(_feed_item(a) for a in articles)
    new_contents = f"\n{items_html}\n\n      "

    updated = re.sub(
        r'(<div class="hero-feed">).*?(</div><!-- /\.hero-feed -->)',
        lambda m: m.group(1) + new_contents + m.group(2),
        html,
        flags=re.DOTALL,
    )

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"  index.html updated ({len(articles)} items in feed)")


def update_news_archive(repo_path: str = ".") -> None:
    """
    Replace the .news-grid in news.html with real article cards (newest first).
    Reads published.json directly so manual title edits are always reflected.
    """
    published_path = os.path.join(repo_path, PUBLISHED_FILE)
    if os.path.exists(published_path):
        with open(published_path, "r", encoding="utf-8") as f:
            published_articles = json.load(f).get("articles", [])
    else:
        published_articles = []

    news_path = os.path.join(repo_path, NEWS_FILE)
    with open(news_path, "r", encoding="utf-8") as f:
        html = f.read()

    articles = sorted(published_articles, key=lambda a: a.get("date", ""), reverse=True)

    def _news_card(a: dict) -> str:
        filename     = a.get("filename", "#")
        title        = _esc(a.get("title", ""))
        date_str     = _esc(a.get("date", ""))
        heat_lbl     = a.get("heat_label", "NORMAL")
        category     = _esc(a.get("category", "News"))
        sources      = a.get("sources", [])
        source_name  = _esc(sources[0].get("name", "") if sources else a.get("source", ""))
        sources_lbl  = _esc(" / ".join(s.get("name", "") for s in sources) or source_name)
        expt         = _esc(_excerpt(a.get("draft", ""), 220))

        return (
            f'        <article class="news-card">\n'
            f'          <div class="card-meta">\n'
            f'            <span class="card-date">{date_str}</span>\n'
            f'            <span class="card-source">{source_name}</span>\n'
            f'          </div>\n'
            f'          <div class="card-badges">\n'
            f'            <span class="badge {_heat_cls(heat_lbl)}">'
            f'{_heat_icon(heat_lbl)} {_esc(heat_lbl)}</span>\n'
            f'            <span class="badge badge-category">{category}</span>\n'
            f'          </div>\n'
            f'          <h3 class="card-headline">\n'
            f'            <a href="{filename}">{title}</a>\n'
            f'          </h3>\n'
            f'          <p class="card-excerpt">{expt}</p>\n'
            f'          <div class="card-footer">\n'
            f'            <span class="card-sources-label">{sources_lbl}</span>\n'
            f'            <a href="{filename}" class="card-read-more">Read more →</a>\n'
            f'          </div>\n'
            f'        </article>'
        )

    cards_html   = "\n\n".join(_news_card(a) for a in articles)
    new_contents = f"\n{cards_html}\n\n      "

    updated = re.sub(
        r'(<div class="news-grid">).*?(</div><!-- /\.news-grid -->)',
        lambda m: m.group(1) + new_contents + m.group(2),
        html,
        flags=re.DOTALL,
    )

    with open(news_path, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"  news.html updated ({len(articles)} cards in grid)")


def _wire_item_html(item: dict) -> str:
    time_ago = _esc(item.get("time_ago", ""))
    source   = _esc(item.get("source", ""))
    title    = _esc(item.get("title", ""))
    url      = _esc(item.get("url", "#"))
    return (
        f'        <div class="wire-item">\n'
        f'          <span class="wire-time">{time_ago}</span>\n'
        f'          <span class="wire-source">{source}</span>\n'
        f'          <a href="{url}" class="wire-headline" target="_blank" '
        f'rel="noopener noreferrer">{title}</a>\n'
        f'        </div>'
    )


def update_wire(repo_path: str = ".") -> None:
    """
    Read wire.json and inject wire items into index.html and news.html.
    Anchors: <!-- /.wire-feed --> and <!-- /.wire-feed-news -->
    """
    wire_path = os.path.join(repo_path, WIRE_FILE)
    if not os.path.exists(wire_path):
        return

    with open(wire_path, "r", encoding="utf-8") as f:
        wire = json.load(f)
    items = wire.get("items", [])

    if items:
        items_html = "\n\n".join(_wire_item_html(it) for it in items)
    else:
        items_html = '        <p class="wire-empty">No wire stories available yet.</p>'

    new_block = f"\n{items_html}\n      "

    for filepath, pattern in [
        (os.path.join(repo_path, INDEX_FILE),
         r'(<div id="wire-feed" class="wire-feed">).*?(</div><!-- /\.wire-feed -->)'),
        (os.path.join(repo_path, NEWS_FILE),
         r'(<div id="wire-feed-news" class="wire-feed">).*?(</div><!-- /\.wire-feed-news -->)'),
    ]:
        with open(filepath, "r", encoding="utf-8") as f:
            html = f.read()
        updated = re.sub(
            pattern,
            lambda m: m.group(1) + new_block + m.group(2),
            html,
            flags=re.DOTALL,
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)

    print(f"  Wire updated ({len(items)} items)")


def update_sitemap(repo_path: str = ".") -> None:
    """Regenerate sitemap.xml with static pages + all published articles."""
    published_path = os.path.join(repo_path, PUBLISHED_FILE)
    if os.path.exists(published_path):
        with open(published_path, "r", encoding="utf-8") as f:
            articles = json.load(f).get("articles", [])
    else:
        articles = []

    BASE  = "https://islamophobiawatchfrance.com"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    static = [
        (f"{BASE}/",             today, "weekly",  "1.0"),
        (f"{BASE}/news.html",    today, "daily",   "0.9"),
        (f"{BASE}/about.html",   today, "monthly", "0.6"),
        (f"{BASE}/contact.html", today, "monthly", "0.5"),
    ]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url, lastmod, changefreq, priority in static:
        lines += [
            "  <url>",
            f"    <loc>{url}</loc>",
            f"    <lastmod>{lastmod}</lastmod>",
            f"    <changefreq>{changefreq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
        ]

    for a in sorted(articles, key=lambda x: x.get("published_at", ""), reverse=True):
        filepath = a.get("filename", "")
        if not filepath:
            continue
        raw = a.get("published_at", today)
        try:
            lastmod = datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except Exception:
            lastmod = today
        lines += [
            "  <url>",
            f"    <loc>{BASE}/{filepath}</loc>",
            f"    <lastmod>{lastmod}</lastmod>",
            "    <changefreq>never</changefreq>",
            "    <priority>0.8</priority>",
            "  </url>",
        ]

    lines.append("</urlset>")

    sitemap_path = os.path.join(repo_path, SITEMAP_FILE)
    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  sitemap.xml updated ({len(articles)} article(s))")


def git_publish(article_path: str, commit_message: str, repo_path: str = ".") -> bool:
    """
    Stage article + updated pages, commit, and push to GitHub.
    Returns True on success. On push failure, prints the error
    and returns False — files are already saved locally.
    """
    def _run(cmd: list):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_path)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    files = [article_path, INDEX_FILE, NEWS_FILE, PUBLISHED_FILE, WIRE_FILE, SITEMAP_FILE]

    code, _, err = _run(["git", "add"] + files)
    if code != 0:
        print(f"  [git add failed] {err}")
        return False

    code, _, err = _run(["git", "commit", "-m", commit_message])
    if code != 0:
        print(f"  [git commit failed] {err}")
        return False

    code, _, err = _run(["git", "push"])
    if code != 0:
        print(f"  [git push failed] {err}")
        print("  Files saved locally — push manually when ready: git push")
        return False

    print("  Pushed to GitHub. Live in ~60 seconds.")
    return True


def publish_post(post: dict, repo_path: str = ".") -> str:
    """
    Full publish flow for one approved post:
      1. Load published.json (create if absent)
      2. Save article HTML to news/
      3. Append article record to published.json
      4. Regenerate index.html feed
      5. Regenerate news.html card grid
      6. Git commit + push
    Returns the live article URL.
    """
    title = post.get("title", "untitled")
    print(f"\n  Publishing: {title[:70]}")

    # 1. Load existing published list
    published_path = os.path.join(repo_path, PUBLISHED_FILE)
    if os.path.exists(published_path):
        with open(published_path, "r", encoding="utf-8") as f:
            published = json.load(f)
    else:
        published = {"articles": []}

    existing = published.get("articles", [])

    # 2. Save article (pass existing list so related articles can be populated)
    article_path = save_article(post, repo_path, published_articles=existing)
    slug         = generate_slug(title)
    date_str     = _format_date(post)

    # 3. Build and append record (deduplicate by slug)
    record = {
        "title":        title,
        "date":         date_str,
        "filename":     article_path,   # e.g. "news/2026-05-07-france-le-senat.html"
        "slug":         slug,
        "sources":      post.get("sources", []),
        "heat_label":   post.get("heat_label", "NORMAL"),
        "heat_score":   post.get("heat_score", 0),
        "category":     _derive_category(post),
        "draft":        post.get("draft", ""),
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    existing = [a for a in existing if a.get("slug") != slug]
    existing.append(record)
    published["articles"] = existing

    with open(published_path, "w", encoding="utf-8") as f:
        json.dump(published, f, indent=2, ensure_ascii=False)
    print(f"  published.json: {len(existing)} article(s) total")

    # 4–7. Regenerate pages
    update_homepage(repo_path)
    update_news_archive(repo_path)
    update_wire(repo_path)
    update_sitemap(repo_path)

    # 7. Git
    commit_msg = f"Publish: {title[:60]}"
    git_publish(article_path, commit_msg, repo_path)

    url = f"https://islamophobiawatchfrance.com/{article_path}"
    print(f"  URL: {url}\n")
    return url
