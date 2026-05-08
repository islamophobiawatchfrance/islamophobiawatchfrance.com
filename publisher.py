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
import os
import re
import subprocess
import unicodedata
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


def generate_slug(title: str) -> str:
    """
    Convert a headline to a URL slug.
    Lowercase, ASCII-only, hyphens for spaces, max 60 chars.
    """
    slug = _ascii(title).lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:60].rstrip("-")


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
    slug       = generate_slug(title)
    date_str   = _format_date(post)
    category   = _derive_category(post)
    filename   = f"{_date_prefix(post)}-{slug}.html"

    raw_pub = post.get("published") or post.get("created") or ""
    try:
        published_iso = datetime.fromisoformat(raw_pub.replace("Z", "+00:00")).isoformat()
    except Exception:
        published_iso = datetime.now(timezone.utc).isoformat()

    excerpt_text   = _excerpt(draft, 160)
    meta_desc      = _meta_description(draft)
    sources_inline = " / ".join(s.get("name", "") for s in sources) or post.get("source", "")

    # Build <p>-wrapped body from double-newline paragraphs
    paragraphs = [p.strip() for p in re.split(r"\n\n+", draft) if p.strip()]
    body_html  = "\n".join(f"      <p>{_esc(p)}</p>" for p in paragraphs)

    # Build source list items
    source_items = "\n".join(
        f'      <div class="source-item">\n'
        f'        <span class="source-arrow">→</span>\n'
        f'        <a href="{_esc(s.get("url","#"))}" target="_blank" rel="noopener noreferrer">'
        f'{_esc(s.get("name","Source"))}</a>\n'
        f'      </div>'
        for s in sources
    )

    subs = {
        "{{title}}":            _esc(title),
        "{{excerpt}}":          _esc(excerpt_text),
        "{{meta_description}}": _esc(meta_desc),
        "{{slug}}":             _esc(slug),
        "{{filename}}":         _esc(filename),
        "{{published_iso}}":    _esc(published_iso),
        "{{date}}":             _esc(date_str),
        "{{heat_badge_class}}": _heat_cls(heat_label),
        "{{heat_icon}}":        _heat_icon(heat_label),
        "{{heat_label}}":       _esc(heat_label),
        "{{category}}":         _esc(category),
        "{{sources_inline}}":   _esc(sources_inline),
        "{{body}}":             body_html,
        "{{sources}}":          source_items,
    }
    for placeholder, value in subs.items():
        tmpl = tmpl.replace(placeholder, value)

    # Populate up to 3 related articles if we have published history
    related = [
        a for a in (published_articles or [])
        if a.get("slug") != slug
    ][-3:]  # take the 3 most recent others (list is newest-last after append)

    if related:
        for i, rel in enumerate(related, start=1):
            rel_heat = rel.get("heat_label", "NORMAL")
            rel_subs = {
                f"{{{{related_{i}_date}}}}":       _esc(rel.get("date", "")),
                f"{{{{related_{i}_source}}}}":     _esc(rel.get("sources", [{}])[0].get("name", "")),
                f"{{{{related_{i}_heat_class}}}}": _heat_cls(rel_heat),
                f"{{{{related_{i}_heat_label}}}}": f"{_heat_icon(rel_heat)} {_esc(rel_heat)}",
                f"{{{{related_{i}_url}}}}":        _esc(rel.get("filename", "#")),
                f"{{{{related_{i}_title}}}}":      _esc(rel.get("title", "")),
                f"{{{{related_{i}_excerpt}}}}":    _esc(_excerpt(rel.get("draft", ""), 160)),
                f"{{{{related_{i}_sources}}}}":    _esc(" / ".join(
                    s.get("name", "") for s in rel.get("sources", [])
                )),
            }
            for ph, val in rel_subs.items():
                tmpl = tmpl.replace(ph, val)

    # Clear any remaining {{related_*}} placeholders; remove empty related cards
    tmpl = re.sub(r"\{\{related_\w+\}\}", "", tmpl)

    # If no related articles, strip the whole related section so we don't show blank cards
    if not related:
        tmpl = re.sub(
            r"\s*<!-- ── Related Articles.*?</section>",
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
