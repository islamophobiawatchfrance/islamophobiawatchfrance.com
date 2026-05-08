#!/usr/bin/env python3
"""
IWF Dashboard
-------------
Local Flask web app for reviewing and approving drafted LinkedIn posts.
Runs at http://localhost:5000

Run with: python3 app.py
"""

import json
import os
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

QUEUE_FILE = "queue.json"
ARCHIVE_FILE = "archive.json"

# All HTML, CSS, and JS in one string — no separate template files.
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IWF Publishing Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, sans-serif; background: #f0f2f5; color: #222; }

    /* ── Header ── */
    .header {
      position: sticky; top: 0; background: #fff;
      border-bottom: 1px solid #e0e0e0;
      padding: 12px 24px; display: flex; align-items: center;
      gap: 16px; z-index: 100; flex-wrap: wrap;
    }
    .header h1 { font-size: 15px; font-weight: 700; color: #111; white-space: nowrap; }
    .header .date { font-size: 13px; color: #888; white-space: nowrap; }
    .stats { display: flex; gap: 14px; margin-left: auto; }
    .stat { font-size: 13px; font-weight: 500; }
    .stat.pending  { color: #1a73e8; }
    .stat.approved { color: #1e8e3e; }
    .stat.rejected { color: #aaa;    }

    /* ── Tab nav ── */
    .tab-nav {
      max-width: 740px; margin: 8px auto 0; padding: 0 16px;
      display: flex; border-bottom: 1px solid #e4e4e4;
    }
    .tab {
      padding: 8px 16px; font-size: 13px; font-weight: 500;
      color: #888; background: none; border: none; cursor: pointer;
      border-bottom: 2px solid transparent; margin-bottom: -1px;
      transition: color 0.1s, border-color 0.1s;
    }
    .tab.active { color: #1a73e8; border-bottom-color: #1a73e8; }
    .tab:hover:not(.active) { color: #444; }

    /* ── Layout ── */
    main { max-width: 740px; margin: 20px auto; padding: 0 16px 60px; }

    /* ── Cards ── */
    .card {
      background: #fff; border-radius: 10px;
      border: 1px solid #e4e4e4; border-left: 4px solid #e0e0e0;
      margin-bottom: 22px; padding: 22px 24px;
      transition: opacity 0.2s, border-left-color 0.2s;
    }
    .card.approved { border-left-color: #1e8e3e; }
    .card.rejected { border-left-color: #d93025; opacity: 0.5; }

    /* ── Badges ── */
    .badges { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 13px; }
    .badge {
      font-size: 11px; font-weight: 600;
      padding: 3px 9px; border-radius: 4px; letter-spacing: 0.02em;
    }
    .badge.time          { background: #f1f3f4; color: #555; }
    .badge.source        { background: #e8f0fe; color: #1a73e8; }
    .badge.approved      { background: #e6f4ea; color: #1e8e3e; }
    .badge.rejected      { background: #fce8e6; color: #d93025; }
    .badge.heat-hot      { background: #fcebeb; color: #a32d2d; }
    .badge.heat-trending { background: #faeeda; color: #854f0b; }
    .badge.heat-normal   { background: #f1f3f4; color: #666; }

    /* ── Content ── */
    .headline { font-size: 16px; font-weight: 600; line-height: 1.45; margin-bottom: 10px; }
    .summary  { font-size: 14px; color: #555; line-height: 1.65; margin-bottom: 18px; }

    /* ── Draft block ── */
    .draft-block {
      background: #f8f9fa; border-left: 3px solid #1a73e8;
      padding: 14px 16px; border-radius: 4px;
      font-size: 14px; line-height: 1.75; white-space: pre-wrap;
      color: #333; margin-bottom: 16px;
    }
    .draft-edit {
      width: 100%; min-height: 190px; resize: vertical;
      font-size: 14px; line-height: 1.75; font-family: inherit;
      padding: 12px 14px; border: 1px solid #ccc; border-radius: 6px;
      display: none; margin-bottom: 10px; color: #333;
    }
    .draft-edit:focus { outline: none; border-color: #1a73e8; }

    /* ── Action rows ── */
    .actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }

    /* ── Buttons ── */
    .btn {
      padding: 8px 15px; border-radius: 6px; border: none;
      cursor: pointer; font-size: 13px; font-weight: 500;
      transition: background 0.12s, opacity 0.12s; white-space: nowrap;
    }
    .btn-approve { background: #1e8e3e; color: #fff; }
    .btn-approve:hover { background: #186e30; }
    .btn-reject  { background: #fff; color: #d93025; border: 1px solid #d93025; }
    .btn-reject:hover  { background: #fce8e6; }
    .btn-edit    { background: #fff; color: #555; border: 1px solid #ccc; }
    .btn-edit:hover    { background: #f1f3f4; }
    .btn-save    { background: #1a73e8; color: #fff; }
    .btn-save:hover    { background: #1558b0; }
    .btn-copy    { background: #fff; color: #1a73e8; border: 1px solid #1a73e8; }
    .btn-copy:hover    { background: #e8f0fe; }
    .btn-undo    { background: #fff; color: #888; border: 1px solid #ddd; font-size: 12px; }
    .btn-undo:hover    { background: #f5f5f5; }

    /* ── Sources ── */
    .sources { margin-top: 10px; }
    .sources-label {
      font-size: 11px; font-weight: 600; color: #999;
      text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;
    }
    .sources a { display: block; font-size: 12px; color: #1a73e8; text-decoration: none; padding: 1px 0; }
    .sources a:hover { text-decoration: underline; }

    /* ── Heat summary bar ── */
    #heat-bar {
      max-width: 740px; margin: 10px auto 0; padding: 0 16px;
      font-size: 12px; color: #999; letter-spacing: 0.01em;
    }

    /* ── Archive ── */
    .archive-date-heading {
      font-size: 11px; font-weight: 700; color: #999;
      text-transform: uppercase; letter-spacing: 0.06em; margin: 24px 0 10px;
    }
    .archive-entry {
      background: #fff; border-radius: 10px;
      border: 1px solid #e4e4e4; border-left: 4px solid #1e8e3e;
      margin-bottom: 16px; padding: 20px 24px;
    }
    .archive-headline { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
    .archive-meta { font-size: 11px; color: #aaa; margin-bottom: 12px; }
    .archive-draft {
      background: #f8f9fa; border-left: 3px solid #1a73e8;
      padding: 12px 14px; border-radius: 4px;
      font-size: 13px; line-height: 1.75; white-space: pre-wrap;
      color: #444; margin-bottom: 10px;
    }

    /* ── Empty state ── */
    .empty { text-align: center; padding: 80px 20px; color: #888; }
    .empty h2 { font-size: 22px; font-weight: 600; color: #444; margin-bottom: 12px; }
    .empty p  { font-size: 14px; margin-bottom: 14px; line-height: 1.6; }
    .empty code {
      display: inline-block; background: #f1f3f4;
      padding: 8px 18px; border-radius: 6px; font-size: 14px;
      color: #333; letter-spacing: 0.02em;
    }

    /* ── Published link ── */
    .published-link {
      display: inline-block; font-size: 12px; color: #1e8e3e;
      text-decoration: none; padding: 2px 0; margin-top: 4px;
    }
    .published-link:hover { text-decoration: underline; }
    .publishing-notice { font-size: 12px; color: #999; font-style: italic; margin-top: 4px; }

    /* ── Toast ── */
    #toast {
      position: fixed; bottom: 28px; left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: #222; color: #fff;
      padding: 10px 22px; border-radius: 8px;
      font-size: 13px; pointer-events: none;
      transition: transform 0.25s ease; z-index: 9999;
      white-space: nowrap; box-shadow: 0 4px 16px rgba(0,0,0,0.2);
    }
    #toast.show { transform: translateX(-50%) translateY(0); }

    @media (max-width: 600px) {
      .stats { margin-left: 0; width: 100%; }
    }
  </style>
</head>
<body>

<header class="header">
  <h1>IWF Publishing Dashboard</h1>
  <span class="date" id="header-date"></span>
  <div class="stats">
    <span class="stat pending"  id="stat-pending">—</span>
    <span class="stat approved" id="stat-approved">—</span>
    <span class="stat rejected" id="stat-rejected">—</span>
  </div>
</header>

<nav class="tab-nav">
  <button class="tab active" id="tab-queue"   onclick="switchTab('queue')">Queue</button>
  <button class="tab"        id="tab-archive" onclick="switchTab('archive')">Archive</button>
</nav>

<div id="heat-bar"></div>
<main id="main"></main>
<div id="toast"></div>

<script>
  let queue = null;
  let archiveData = null;
  let currentTab = 'queue';

  // Escape HTML special chars to prevent injection.
  function esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Tabs ──────────────────────────────────────────────────

  function switchTab(tab) {
    currentTab = tab;
    document.getElementById('tab-queue').classList.toggle('active', tab === 'queue');
    document.getElementById('tab-archive').classList.toggle('active', tab === 'archive');
    document.getElementById('heat-bar').style.display = tab === 'queue' ? '' : 'none';
    if (tab === 'queue') {
      render();
    } else {
      loadArchive();
    }
  }

  // ── Data helpers ──────────────────────────────────────────

  function getPost(id) {
    return queue && queue.posts && queue.posts.find(p => p.id === id);
  }

  async function loadQueue() {
    try {
      const res = await fetch('/api/queue');
      queue = await res.json();
      if (currentTab === 'queue') render();
    } catch (e) {
      renderEmpty('Could not reach the dashboard server.');
    }
  }

  async function saveQueue() {
    await fetch('/api/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(queue),
    });
  }

  async function loadArchive() {
    try {
      const res = await fetch('/api/archive');
      archiveData = await res.json();
      renderArchive();
    } catch (e) {
      document.getElementById('main').innerHTML =
        '<div class="empty"><h2>Could not load archive.</h2></div>';
    }
  }

  // ── Actions ───────────────────────────────────────────────

  async function approve(id) {
    const post = getPost(id);
    if (!post) return;
    post.status = 'approved';
    await saveQueue();
    await fetch('/api/archive/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, approved_at: new Date().toISOString() }),
    });
    render();
    showToast('Approved — publishing to website…');

    try {
      const res  = await fetch('/api/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (data.ok && data.url) {
        post.published     = true;
        post.published_url = data.url;
        post.published_at  = new Date().toISOString();
        render();
        showToast('Published to islamophobiawatchfrance.com');
      } else {
        showToast('Approved — publish error: ' + (data.error || 'unknown'));
      }
    } catch (e) {
      showToast('Approved — publish failed (check server logs)');
    }
  }

  async function reject(id) {
    const post = getPost(id);
    if (!post) return;
    post.status = 'rejected';
    await saveQueue();
    render();
    showToast('Post rejected');
  }

  async function undoAction(id) {
    const post = getPost(id);
    if (!post) return;
    post.status = 'pending';
    await saveQueue();
    render();
    showToast('Moved back to pending');
  }

  function toggleEdit(id) {
    const block    = document.getElementById('draft-block-' + id);
    const textarea = document.getElementById('draft-edit-' + id);
    const saveBtn  = document.getElementById('save-btn-' + id);
    const editBtn  = document.getElementById('edit-btn-' + id);

    const editing = textarea.style.display === 'block';
    if (editing) {
      const post = getPost(id);
      textarea.value = post ? post.draft : '';
      textarea.style.display = 'none';
      block.style.display    = 'block';
      saveBtn.style.display  = 'none';
      editBtn.textContent    = 'Edit Draft';
    } else {
      textarea.style.display = 'block';
      block.style.display    = 'none';
      saveBtn.style.display  = 'inline-block';
      editBtn.textContent    = 'Cancel';
      textarea.focus();
    }
  }

  async function saveDraft(id) {
    const post = getPost(id);
    if (!post) return;
    const textarea = document.getElementById('draft-edit-' + id);
    post.draft = textarea.value;
    await saveQueue();
    render();
    showToast('Draft saved');
  }

  function copyDraft(id) {
    const post = getPost(id);
    if (!post) return;
    navigator.clipboard.writeText(post.draft).then(() => {
      showToast('Copied to clipboard — paste directly into LinkedIn');
    });
  }

  // ── Toast ─────────────────────────────────────────────────

  function showToast(msg) {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toast.classList.remove('show'), 3000);
  }

  // ── Rendering ─────────────────────────────────────────────

  function renderHeatBar() {
    const bar = document.getElementById('heat-bar');
    if (!queue || !queue.posts || queue.posts.length === 0) { bar.textContent = ''; return; }
    const counts = { HOT: 0, TRENDING: 0, NORMAL: 0 };
    queue.posts.forEach(p => { if (p.heat_label) counts[p.heat_label]++; });
    const scanned = queue.total_stories_scanned
      || queue.posts.reduce((s, p) => s + (p.heat_article_count || 1), 0);
    const parts = [];
    if (counts.HOT)      parts.push(`${counts.HOT} HOT topic${counts.HOT > 1 ? 's' : ''}`);
    if (counts.TRENDING) parts.push(`${counts.TRENDING} TRENDING topic${counts.TRENDING > 1 ? 's' : ''}`);
    if (counts.NORMAL)   parts.push(`${counts.NORMAL} NORMAL topic${counts.NORMAL > 1 ? 's' : ''}`);
    parts.push(`${scanned} article${scanned !== 1 ? 's' : ''} scanned today`);
    bar.textContent = parts.join(' · ');
  }

  function renderEmpty(msg) {
    document.getElementById('main').innerHTML = `
      <div class="empty">
        <h2>${esc(msg)}</h2>
        <p>Run the pipeline to fetch and draft today's posts.</p>
        <code>python3 run.py</code>
      </div>`;
    updateStats(0, 0, 0);
  }

  function renderSources(post) {
    if (post.sources && post.sources.length > 0) {
      const links = post.sources.map(s =>
        `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">→ ${esc(s.name)}</a>`
      ).join('');
      return `<div class="sources"><div class="sources-label">Sources</div>${links}</div>`;
    }
    return `<div class="sources"><a href="${esc(post.url)}" target="_blank" rel="noopener noreferrer">→ Read original article</a></div>`;
  }

  function renderCard(post) {
    const id = post.id;

    const statusBadge = post.status !== 'pending'
      ? `<span class="badge ${esc(post.status)}">${esc(post.status)}</span>` : '';

    const approveRejectBtns = post.status === 'pending' ? `
      <button class="btn btn-approve" onclick="approve('${id}')">Approve</button>
      <button class="btn btn-reject"  onclick="reject('${id}')">Reject</button>` : '';

    const copyBtn = post.status === 'approved'
      ? `<button class="btn btn-copy" onclick="copyDraft('${id}')">Copy text</button>` : '';

    const publishedLink = (post.published && post.published_url)
      ? `<a class="published-link" href="${esc(post.published_url)}" target="_blank" rel="noopener noreferrer">View live article →</a>`
      : (post.status === 'approved' && !post.published)
        ? `<span class="publishing-notice">Publishing…</span>` : '';

    const undoBtn = post.status !== 'pending'
      ? `<button class="btn btn-undo" onclick="undoAction('${id}')">Undo</button>` : '';

    const heatBadge = (() => {
      const hl = post.heat_label;
      if (!hl) return '';
      const cls  = hl === 'HOT' ? 'heat-hot' : (hl === 'TRENDING' ? 'heat-trending' : 'heat-normal');
      const icon = hl === 'HOT' ? '🔥' : (hl === 'TRENDING' ? '📈' : '📰');
      const n    = post.heat_article_count || 1;
      const word = n === 1 ? 'article' : 'articles';
      return `<span class="badge ${cls}">${icon} ${esc(hl)} - ${n} ${word}</span>`;
    })();

    return `
      <div class="card ${esc(post.status)}" id="card-${id}">
        <div class="badges">
          <span class="badge time">${esc(post.time_ago)}</span>
          <span class="badge source">${esc(post.source)}</span>
          ${heatBadge}
          ${statusBadge}
        </div>
        <div class="headline">${esc(post.title)}</div>
        <div class="summary">${esc(post.summary)}</div>
        <div class="draft-block" id="draft-block-${id}">${esc(post.draft)}</div>
        <textarea class="draft-edit" id="draft-edit-${id}">${esc(post.draft)}</textarea>
        <div class="actions">
          ${approveRejectBtns}
          <button class="btn btn-edit" id="edit-btn-${id}" onclick="toggleEdit('${id}')">Edit Draft</button>
          <button class="btn btn-save" id="save-btn-${id}" onclick="saveDraft('${id}')" style="display:none">Save</button>
          ${copyBtn}
          ${undoBtn}
        </div>
        ${publishedLink}
        ${renderSources(post)}
      </div>`;
  }

  function renderArchiveEntry(post) {
    const sourcesHtml = (() => {
      if (post.sources && post.sources.length > 0) {
        return post.sources.map(s =>
          `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">→ ${esc(s.name)}</a>`
        ).join('');
      }
      return `<a href="${esc(post.url)}" target="_blank" rel="noopener noreferrer">→ Read original</a>`;
    })();
    const approvedAt = post.approved_at
      ? new Date(post.approved_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
      : '';
    const meta = [post.source, approvedAt ? `Approved ${approvedAt}` : ''].filter(Boolean).join(' · ');
    return `
      <div class="archive-entry">
        <div class="archive-headline">${esc(post.title)}</div>
        <div class="archive-meta">${esc(meta)}</div>
        <div class="archive-draft">${esc(post.draft)}</div>
        <div class="sources"><div class="sources-label">Sources</div>${sourcesHtml}</div>
      </div>`;
  }

  function renderArchive() {
    if (!archiveData || !archiveData.posts) {
      document.getElementById('main').innerHTML =
        '<div class="empty"><h2>No archive yet.</h2><p>Approved posts will appear here.</p></div>';
      return;
    }
    const approved = archiveData.posts.filter(p => p.approved).reverse();
    if (approved.length === 0) {
      document.getElementById('main').innerHTML =
        '<div class="empty"><h2>No approved posts yet.</h2><p>Approve a post in the Queue tab to see it here.</p></div>';
      return;
    }
    const byDate = {};
    for (const post of approved) {
      const d = post.date || (post.created || '').slice(0, 10) || 'Unknown';
      if (!byDate[d]) byDate[d] = [];
      byDate[d].push(post);
    }
    const dates = Object.keys(byDate).sort().reverse();
    let html = '';
    for (const date of dates) {
      html += `<div class="archive-date-heading">${esc(date)}</div>`;
      html += byDate[date].map(renderArchiveEntry).join('');
    }
    document.getElementById('main').innerHTML = html;
  }

  function updateStats(pending, approved, rejected) {
    document.getElementById('stat-pending').textContent  = pending  + ' pending';
    document.getElementById('stat-approved').textContent = approved + ' approved';
    document.getElementById('stat-rejected').textContent = rejected + ' rejected';
    const d = new Date();
    document.getElementById('header-date').textContent =
      d.toLocaleDateString('en-GB', { weekday:'long', day:'numeric', month:'long', year:'numeric' });
  }

  function render() {
    if (!queue || !queue.posts || queue.posts.length === 0) {
      renderEmpty('No posts in the queue yet.');
      return;
    }

    const pending  = queue.posts.filter(p => p.status === 'pending').length;
    const approved = queue.posts.filter(p => p.status === 'approved').length;
    const rejected = queue.posts.filter(p => p.status === 'rejected').length;
    updateStats(pending, approved, rejected);
    renderHeatBar();

    document.getElementById('main').innerHTML =
      queue.posts.map(renderCard).join('');
  }

  loadQueue();
</script>
</body>
</html>"""


# =============================================================
# ROUTES
# =============================================================

@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/queue", methods=["GET"])
def get_queue():
    """Returns queue.json contents, or null if the file doesn't exist yet."""
    if not os.path.exists(QUEUE_FILE):
        return jsonify(None), 200
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/queue", methods=["POST"])
def save_queue():
    """Saves the posted JSON body back to queue.json."""
    data = request.get_json(force=True)
    if data is None:
        return jsonify({"error": "No JSON body"}), 400
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return jsonify({"ok": True})


@app.route("/api/archive", methods=["GET"])
def get_archive():
    """Returns archive.json contents, or empty posts list if file doesn't exist."""
    if not os.path.exists(ARCHIVE_FILE):
        return jsonify({"posts": []}), 200
    with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/archive/approve", methods=["POST"])
def approve_archive():
    """Sets approved=true and records approved_at on the matching archive entry."""
    data = request.get_json(force=True)
    if not data or "id" not in data:
        return jsonify({"error": "Missing id"}), 400
    if not os.path.exists(ARCHIVE_FILE):
        return jsonify({"ok": True})
    with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
        archive = json.load(f)
    for post in archive.get("posts", []):
        if post.get("id") == data["id"]:
            post["approved"] = True
            post["approved_at"] = data.get("approved_at", "")
            break
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)
    return jsonify({"ok": True})


@app.route("/api/publish", methods=["POST"])
def publish_article():
    """
    Trigger the full publish flow for an approved post:
    generates HTML, updates index/news pages, commits and pushes to GitHub.
    """
    import publisher

    data = request.get_json(force=True)
    if not data or "id" not in data:
        return jsonify({"error": "Missing id"}), 400

    if not os.path.exists(QUEUE_FILE):
        return jsonify({"error": "Queue not found"}), 404

    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    post = next(
        (p for p in queue_data.get("posts", []) if p.get("id") == data["id"]),
        None,
    )
    if not post:
        return jsonify({"error": "Post not found in queue"}), 404

    try:
        url = publisher.publish_post(post)
    except Exception as e:
        print(f"  [publish error] {e}")
        return jsonify({"error": str(e)}), 500

    # Persist publish metadata back into queue.json
    now = datetime.now(timezone.utc).isoformat()
    post["published"]     = True
    post["published_url"] = url
    post["published_at"]  = now
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True, "url": url})


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    print("\n  IWF Dashboard running at http://localhost:5000")
    print("  Press Ctrl+C to stop.\n")
    app.run(debug=False, port=5000)
