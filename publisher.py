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
from datetime import datetime, timedelta, timezone

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
    raw = post.get("published") or post.get("created") or post.get("published_at") or ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    # Fallback: parse human-readable date e.g. "8 May 2026"
    try:
        dt = datetime.strptime(post.get("date", ""), "%d %B %Y")
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


def _heat_badge_fresh(published_at: str, hours: int = 72) -> bool:
    """Return True if published_at is within the last `hours` hours."""
    try:
        dt = datetime.fromisoformat(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt < timedelta(hours=hours)
    except (ValueError, TypeError):
        return False


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


def _render_inline(text: str) -> str:
    """HTML-escape text then apply **bold** and *italic* markdown."""
    s = _esc(text)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'\*(.+?)\*', r'<em>\1</em>', s)
    return s


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


def generate_header_graphic(title: str, category: str, heat_label: str, date: str) -> str:
    """Return a branded inline SVG banner for every article header."""
    badge_bg    = {"HOT": "#A32D2D", "TRENDING": "#854F0B"}.get(heat_label.upper(), "#444444")
    badge_fg    = {"HOT": "#FFAAAA", "TRENDING": "#FFD580"}.get(heat_label.upper(), "#AAAAAA")

    # Word-wrap title: max 42 chars per line, two lines max (rest truncated with ellipsis)
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

    if len(lines) > 2:
        lines = lines[:2]
        if len(lines[1]) > 39:
            lines[1] = lines[1][:39].rstrip() + "…"
        else:
            lines[1] = lines[1] + "…"

    tspans = "".join(
        f'<tspan x="40" dy="{0 if i == 0 else 44}">{_esc(line)}</tspan>'
        for i, line in enumerate(lines)
    )

    badge_label = _esc(heat_label.upper())
    badge_w     = max(len(badge_label) * 7 + 20, 80)
    badge_x     = 800 - badge_w - 40

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 220" '
        f'width="100%" style="display:block">'
        # Background
        f'<rect width="800" height="220" fill="#0a0a0a"/>'
        # Top red bar
        f'<rect x="0" y="0" width="800" height="4" fill="#ED2939"/>'
        # Bottom blue bar
        f'<rect x="0" y="216" width="800" height="4" fill="#002395"/>'
        # IWF watermark — faint, far right
        f'<text x="580" y="180" font-family="sans-serif" font-size="120" font-weight="bold" '
        f'fill="#1a1a1a" dominant-baseline="auto">IWF</text>'
        # Category label
        f'<text x="40" y="50" font-family="sans-serif" font-size="11" font-weight="normal" '
        f'fill="#ED2939" letter-spacing="0.15em">{_esc(category.upper())}</text>'
        # Heat badge — right-aligned on same row as category
        f'<rect x="{badge_x}" y="34" width="{badge_w}" height="22" rx="3" fill="{badge_bg}"/>'
        f'<text x="{badge_x + badge_w // 2}" y="49" font-family="sans-serif" font-size="11" '
        f'fill="{badge_fg}" text-anchor="middle">{badge_label}</text>'
        # Title
        f'<text x="40" y="100" font-family="sans-serif" font-size="28" font-weight="bold" '
        f'fill="#ffffff">{tspans}</text>'
        # Date
        f'<text x="40" y="190" font-family="sans-serif" font-size="11" fill="#666666">'
        f'{_esc(date)}</text>'
        f'</svg>'
    )
    return f'<div class="art-header-img">{svg}</div>'


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
    is_manual  = bool(post.get("manual"))

    raw_pub = post.get("published") or post.get("created") or ""
    try:
        published_iso = datetime.fromisoformat(raw_pub.replace("Z", "+00:00")).isoformat()
    except Exception:
        published_iso = datetime.now(timezone.utc).isoformat()

    header_image_html = generate_header_graphic(title, category, heat_label, date_str)

    # For manual posts, draft is pre-rendered HTML; strip tags for text-only fields
    draft_text = re.sub(r"<[^>]+>", " ", draft).strip() if is_manual else draft

    excerpt_text      = _excerpt(draft_text, 160)
    meta_desc         = _meta_description(draft_text)
    sources_inline    = " / ".join(s.get("name", "") for s in sources) or post.get("source", "")
    page_url          = f"https://islamophobiawatchfrance.com/news/{filename}"
    title_encoded     = urllib.parse.quote(title)
    heat_article_count = str(post.get("heat_article_count", "Multiple"))
    reading_time      = str(max(1, math.ceil(len(draft_text.split()) / 200)))

    schema = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": title,
        "datePublished": published_iso,
        "description": meta_desc,
        "url": page_url,
        "articleSection": category,
        "publisher": {
            "@type": "Organization",
            "name": "Islamophobia Watch France",
            "url": "https://islamophobiawatchfrance.com",
        },
    }
    schema_json_html = (
        '<script type="application/ld+json">\n'
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + '\n  </script>'
    )

    if is_manual:
        # draft is already rendered HTML from the rich text editor; use it directly
        standfirst_text = ""
        body_html = draft
    else:
        # Standfirst (first paragraph) + body (remaining)
        paragraphs      = [p.strip() for p in re.split(r"\n\n+", draft) if p.strip()]
        standfirst_text = paragraphs[0] if paragraphs else ""

        # Find the FAQ separator (first standalone "---")
        faq_sep_idx = next(
            (i for i, p in enumerate(paragraphs) if p.strip() == "---"), None
        )
        article_paras = paragraphs[1:faq_sep_idx] if faq_sep_idx is not None else paragraphs[1:]
        faq_paras     = paragraphs[faq_sep_idx + 1:] if faq_sep_idx is not None else []

        body_lines = []
        for p in article_paras:
            if p.startswith("## "):
                body_lines.append(f'      <h2 class="art-h2">{_esc(p[3:].strip())}</h2>')
            elif p.strip() == "---":
                body_lines.append('      <hr class="art-divider">')
            elif p.startswith("> "):
                body_lines.append(f'      <p class="pull-quote">{_render_inline(p[2:])}</p>')
            elif p.startswith(('"', '"', '"', ''', ''')):
                body_lines.append(f'      <p class="pull-quote">{_render_inline(p)}</p>')
            else:
                body_lines.append(f'      <p>{_render_inline(p)}</p>')

        if faq_paras:
            faq_items = []
            fi = 0
            while fi < len(faq_paras):
                p = faq_paras[fi]
                m = re.match(r'^\*\*(.+?)\*\*\s*\n?(.*)', p, re.DOTALL)
                if m:
                    question      = m.group(1).strip()
                    answer_inline = m.group(2).strip()
                    faq_items.append(f'          <dt class="faq-question">{_esc(question)}</dt>')
                    if answer_inline:
                        faq_items.append(f'          <dd class="faq-answer">{_render_inline(answer_inline)}</dd>')
                    elif fi + 1 < len(faq_paras):
                        fi += 1
                        faq_items.append(f'          <dd class="faq-answer">{_render_inline(faq_paras[fi])}</dd>')
                elif p.strip():
                    faq_items.append(f'          <dd class="faq-answer">{_render_inline(p)}</dd>')
                fi += 1
            body_lines.append(
                '      <div class="art-faq">\n'
                '        <h2 class="art-faq-heading">Q&amp;A</h2>\n'
                '        <dl class="faq-list">\n' +
                "\n".join(faq_items) +
                '\n        </dl>\n      </div>'
            )

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
        "{{schema_json}}":        schema_json_html,
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

    articles = sorted(published_articles, key=lambda x: x.get("published_at", ""), reverse=True)[:10]

    def _feed_item(a: dict) -> str:
        filename  = a.get("filename", "#")
        title     = _esc(a.get("title", ""))
        short_dt  = _esc(_short_date(a.get("date", "")))
        heat_lbl  = a.get("heat_label", "NORMAL")
        _srcs  = a.get("sources") or []
        source = _esc((_srcs[0].get("name", "") if _srcs else "") or a.get("source", ""))
        expt      = _esc(_excerpt(a.get("draft", ""), 160))
        heat_html = (
            f'<span class="badge {_heat_cls(heat_lbl)}">'
            f'{_heat_icon(heat_lbl)} {_esc(heat_lbl)}</span>\n'
            if _heat_badge_fresh(a.get("published_at", "")) else ""
        )

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
            f'              {heat_html}'
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

    # Inject live story count into #iwf-stats (count from 7-day wire window)
    story_count = 0
    wire_path = os.path.join(repo_path, WIRE_FILE)
    if os.path.exists(wire_path):
        try:
            with open(wire_path, "r", encoding="utf-8") as f:
                story_count = len(json.load(f).get("items", []))
        except Exception:
            pass
    stats_text = f"{story_count} stories monitored in the last 7 days"
    updated = re.sub(
        r'(<div id="iwf-stats"[^>]*>)[^<]*(</div>)',
        lambda m: m.group(1) + stats_text + m.group(2),
        updated,
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

    articles = sorted(published_articles, key=lambda x: x.get("published_at", ""), reverse=True)

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
        heat_html = (
            f'            <span class="badge {_heat_cls(heat_lbl)}">'
            f'{_heat_icon(heat_lbl)} {_esc(heat_lbl)}</span>\n'
            if _heat_badge_fresh(a.get("published_at", "")) else ""
        )

        return (
            f'        <article class="news-card">\n'
            f'          <div class="card-meta">\n'
            f'            <span class="card-date">{date_str}</span>\n'
            f'            <span class="card-source">{source_name}</span>\n'
            f'          </div>\n'
            f'          <div class="card-badges">\n'
            f'{heat_html}'
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


def _wire_date_sep(label: str) -> str:
    return f'        <div class="wire-date-sep"><span>{_esc(label)}</span></div>'


def update_wire(repo_path: str = ".") -> None:
    """
    Read wire.json and inject all 7-day wire items into index.html and news.html,
    with a date-separator divider between each calendar day.
    """
    wire_path = os.path.join(repo_path, WIRE_FILE)
    if not os.path.exists(wire_path):
        return

    with open(wire_path, "r", encoding="utf-8") as f:
        wire = json.load(f)
    items = wire.get("items", [])

    if not items:
        items_html = '        <p class="wire-empty">No wire stories available yet.</p>'
    else:
        blocks: list = []
        current_day: str = ""
        for it in items:
            pub_str = it.get("published", "")
            day_label = ""
            try:
                pub_dt   = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                day_label = pub_dt.strftime("%A %-d %B")   # e.g. "Thursday 8 May"
            except Exception:
                pass
            if day_label and day_label != current_day:
                current_day = day_label
                blocks.append(_wire_date_sep(day_label))
            blocks.append(_wire_item_html(it))
        items_html = "\n\n".join(blocks)

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

    print(f"  Wire updated ({len(items)} items across 7 days)")


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


def generate_source_report(repo_path: str = ".") -> None:
    """
    Read published.json + archive.json, count per-source citations,
    flag any source >30% of total, save source_report.json.
    """
    counts: dict = {}

    for fname in [PUBLISHED_FILE, "archive.json"]:
        fpath = os.path.join(repo_path, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # published.json uses {"articles": [...]}, archive.json uses {"posts": [...]}
        items = data.get("articles", data.get("posts", [])) if isinstance(data, dict) else data
        for item in items:
            if not isinstance(item, dict):
                continue
            for src in item.get("sources", []):
                name = (src.get("name", "") if isinstance(src, dict) else str(src)).strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1
            plain_src = item.get("source", "").strip()
            if plain_src and not item.get("sources"):
                counts[plain_src] = counts.get(plain_src, 0) + 1

    total = sum(counts.values())
    if total == 0:
        report = {"total": 0, "sources": [], "flagged": []}
    else:
        sources = sorted(
            [{"name": n, "count": c, "pct": round(c / total * 100, 1)} for n, c in counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )
        flagged = [s["name"] for s in sources if s["pct"] > 30]
        report = {"total": total, "sources": sources, "flagged": flagged}

    out_path = os.path.join(repo_path, "source_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"  source_report.json: {len(report['sources'])} source(s), {len(report['flagged'])} flagged")


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
        print(f"\nWARNING: Git push failed. Article saved locally but not live.")
        print(f"  Error: {err}")
        print(f"  Run: git add . && git commit -m 'Manual push' && git push\n")
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
    existing.insert(0, record)
    published["articles"] = existing

    with open(published_path, "w", encoding="utf-8") as f:
        json.dump(published, f, indent=2, ensure_ascii=False)
    print(f"  published.json: {len(existing)} article(s) total")

    # 4–7. Regenerate pages
    update_homepage(repo_path)
    update_news_archive(repo_path)
    update_wire(repo_path)
    update_sitemap(repo_path)
    generate_source_report(repo_path)

    # 7. Git
    commit_msg = f"Publish: {title[:60]}"
    push_ok = git_publish(article_path, commit_msg, repo_path)

    url = f"https://islamophobiawatchfrance.com/{article_path}"
    print(f"  URL: {url}\n")
    return url, push_ok
