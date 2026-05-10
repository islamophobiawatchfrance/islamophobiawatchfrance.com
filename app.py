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
      margin-bottom: 14px; padding: 22px 24px;
      transition: opacity 0.2s, border-left-color 0.2s;
    }
    .card.approved { border-left-color: #1e8e3e; }
    .card.rejected { border-left-color: #d93025; opacity: 0.5; }
    .card.type-linkedin { border-left-color: #1a73e8; }
    .card.type-website  { border-left-color: #ED2939; }
    .cluster-group { margin-bottom: 28px; border: 1px solid #e8e8e8; border-radius: 12px; overflow: hidden; }
    .cluster-group .card { border-radius: 0; border: none; border-bottom: 1px solid #f0f0f0; margin-bottom: 0; }
    .cluster-group .card:last-child { border-bottom: none; }
    .type-label { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; padding: 2px 8px; border-radius: 3px; }
    .type-label.linkedin { background: #e8f0fe; color: #1a73e8; }
    .type-label.website  { background: #fce8e8; color: #c0392b; }

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

    /* ── Rich-text toolbar ── */
    .rte-toolbar { display: flex; gap: 4px; margin-bottom: 6px; }
    .rte-btn {
      padding: 3px 9px; font-size: 11px; font-weight: 600;
      border: 1px solid #ccc; border-radius: 4px; background: #f8f9fa;
      color: #444; cursor: pointer; line-height: 1.4;
    }
    .rte-btn:hover { background: #e8eaed; border-color: #aaa; }

    /* ── Inline title editing ── */
    .title-input {
      display: none; width: 100%;
      font-size: 16px; font-weight: 600; line-height: 1.45;
      border: none; border-bottom: 1.5px solid #1a73e8;
      outline: none; padding: 0; background: transparent;
      font-family: inherit; color: #222;
    }
    .title-edit-btn {
      font-size: 12px; color: #ccc; background: none;
      border: none; cursor: pointer; padding: 0 0 0 7px;
      vertical-align: middle; line-height: 1;
    }
    .title-edit-btn:hover { color: #1a73e8; }

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
    .btn-undo       { background: #fff; color: #888; border: 1px solid #ddd; font-size: 12px; }
    .btn-undo:hover { background: #f5f5f5; }
    .btn-regen      { background: #fff; color: #555; border: 1px solid #bbb; }
    .btn-regen:hover { background: #f5f5f5; }
    .btn-regen:disabled { opacity: 0.5; cursor: not-allowed; }

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
  <button class="tab"        id="tab-sources" onclick="switchTab('sources')">Sources</button>
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
    document.getElementById('tab-sources').classList.toggle('active', tab === 'sources');
    document.getElementById('heat-bar').style.display = tab === 'queue' ? '' : 'none';
    if (tab === 'queue')   { render(); loadGaps(); }
    else if (tab === 'archive') loadArchive();
    else if (tab === 'sources') loadSourceReport();
  }

  async function loadSourceReport() {
    try {
      const res  = await fetch('/api/source-report');
      const data = await res.json();
      if (data.error) { document.getElementById('main').innerHTML = `<div class="empty"><h2>${esc(data.error)}</h2><p>Run the pipeline to generate source data.</p></div>`; return; }
      const updated = data.generated ? new Date(data.generated).toLocaleString('en-GB') : 'unknown';
      let rows = (data.sources || []).map(s => {
        const flagHtml = s.flag ? ' <span style="background:#fff3cd;color:#856404;font-size:10px;padding:1px 5px;border-radius:3px;">⚠ Over-relied</span>' : '';
        const bar = `<div style="height:6px;background:#e4e4e4;border-radius:3px;margin-top:4px;"><div style="height:6px;background:${s.flag?'#ffc107':'#1a73e8'};border-radius:3px;width:${Math.min(100,s.percentage)}%;"></div></div>`;
        return `<tr>
          <td style="padding:10px 12px;font-weight:500;">${esc(s.name)}${flagHtml}</td>
          <td style="padding:10px 12px;text-align:right;">${s.count}</td>
          <td style="padding:10px 12px;min-width:140px;">${s.percentage.toFixed(1)}%${bar}</td>
        </tr>`;
      }).join('');
      const rec = data.recommendation ? `<p style="margin-top:16px;padding:12px 14px;background:#fff3cd;border-radius:6px;font-size:13px;">⚠️ ${esc(data.recommendation)}</p>` : '';
      document.getElementById('main').innerHTML = `
        <div style="padding:8px 0;">
          <div style="font-size:12px;color:#999;margin-bottom:16px;">Last updated: ${updated} · ${data.total_citations || 0} total citations</div>
          <table style="width:100%;border-collapse:collapse;background:#fff;border-radius:10px;border:1px solid #e4e4e4;overflow:hidden;font-size:13px;">
            <thead><tr style="background:#f8f9fa;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#888;">
              <th style="padding:10px 12px;text-align:left;">Source</th>
              <th style="padding:10px 12px;text-align:right;">Citations</th>
              <th style="padding:10px 12px;">Share</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
          ${rec}
        </div>`;
    } catch (e) {
      document.getElementById('main').innerHTML = '<div class="empty"><h2>No source data yet.</h2><p>Run the pipeline first.</p></div>';
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
      if (currentTab === 'queue') { render(); loadGaps(); }
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

    // Website articles get published; LinkedIn posts just get archived
    if (post.type === 'website' || !post.type) {
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
    } else {
      showToast('LinkedIn post approved');
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
    const toolbar  = document.getElementById('toolbar-' + id);

    const editing = textarea.style.display === 'block';
    if (editing) {
      const post = getPost(id);
      textarea.value = post ? post.draft : '';
      textarea.style.display = 'none';
      block.style.display    = 'block';
      saveBtn.style.display  = 'none';
      editBtn.textContent    = 'Edit Draft';
      if (toolbar) toolbar.style.display = 'none';
    } else {
      textarea.style.display = 'block';
      block.style.display    = 'none';
      saveBtn.style.display  = 'inline-block';
      editBtn.textContent    = 'Cancel';
      if (toolbar) toolbar.style.display = 'flex';
      textarea.focus();
    }
  }

  async function regenerateDraft(id) {
    const btn = document.getElementById('regen-btn-' + id);
    if (btn) { btn.disabled = true; btn.textContent = 'Regenerating…'; }
    try {
      const res  = await fetch('/api/regenerate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (data.ok && data.draft) {
        const post = getPost(id);
        if (post) post.draft = data.draft;
        document.getElementById('draft-block-' + id).textContent = data.draft;
        document.getElementById('draft-edit-' + id).value = data.draft;
        showToast('Draft regenerated');
      } else {
        showToast('Regeneration failed: ' + (data.error || 'unknown'));
      }
    } catch (e) {
      showToast('Regeneration failed (network error)');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Regenerate'; }
    }
  }

  function editTitle(id) {
    const span  = document.getElementById('title-text-'  + id);
    const input = document.getElementById('title-input-' + id);
    span.style.display  = 'none';
    input.style.display = 'inline-block';
    input.select();
  }

  async function saveTitle(id) {
    const input = document.getElementById('title-input-' + id);
    if (!input || input.style.display === 'none') return;
    const span = document.getElementById('title-text-' + id);
    const post = getPost(id);
    if (!post) return;
    const newTitle = input.value.trim() || post.title;
    input.value        = newTitle;
    post.title         = newTitle;
    span.textContent   = newTitle;
    input.style.display = 'none';
    span.style.display  = '';
    await saveQueue();
    showToast('Title updated');
  }

  function cancelTitle(id) {
    const span  = document.getElementById('title-text-'  + id);
    const input = document.getElementById('title-input-' + id);
    const post  = getPost(id);
    if (post) input.value = post.title;
    input.style.display = 'none';
    span.style.display  = '';
  }

  function insertMarkdown(id, before, after) {
    const ta = document.getElementById('draft-edit-' + id);
    if (!ta) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    const sel   = ta.value.substring(start, end);
    const replacement = before + (sel || 'text') + (after || '');
    ta.setRangeText(replacement, start, end, 'select');
    ta.focus();
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

  function renderCard(post) {
    const id   = post.id;
    const type = post.type || 'linkedin';

    const typeLabelHtml = `<span class="type-label ${type}">${type === 'website' ? 'Website article' : 'LinkedIn'}</span>`;

    const statusBadge = post.status !== 'pending'
      ? `<span class="badge ${esc(post.status)}">${esc(post.status)}</span>` : '';

    const approveRejectBtns = post.status === 'pending' ? `
      <button class="btn btn-approve" onclick="approve('${id}')">Approve</button>
      <button class="btn btn-reject"  onclick="reject('${id}')">Reject</button>` : '';

    const copyBtn = (post.status === 'approved' && type === 'linkedin')
      ? `<button class="btn btn-copy" onclick="copyDraft('${id}')">Copy text</button>` : '';

    const publishedLink = (post.published && post.published_url)
      ? `<a class="published-link" href="${esc(post.published_url)}" target="_blank" rel="noopener noreferrer">View live article →</a>`
      : (post.status === 'approved' && !post.published && type === 'website')
        ? `<span class="publishing-notice">Publishing…</span>` : '';

    const undoBtn = post.status !== 'pending'
      ? `<button class="btn btn-undo" onclick="undoAction('${id}')">Undo</button>` : '';

    const heatBadge = (() => {
      const hl = post.heat_label;
      if (!hl) return '';
      const cls  = hl === 'HOT' ? 'heat-hot' : (hl === 'TRENDING' ? 'heat-trending' : 'heat-normal');
      const icon = hl === 'HOT' ? '🔥' : (hl === 'TRENDING' ? '📈' : '📰');
      const n    = post.heat_article_count || 1;
      return `<span class="badge ${cls}">${icon} ${esc(hl)} - ${n} ${n === 1 ? 'article' : 'articles'}</span>`;
    })();

    return `
      <div class="card ${esc(post.status)} type-${type}" id="card-${id}">
        <div class="badges">
          ${typeLabelHtml}
          <span class="badge time">${esc(post.time_ago)}</span>
          <span class="badge source">${esc(post.source)}</span>
          ${heatBadge}
          ${statusBadge}
        </div>
        <div class="headline" id="title-wrap-${id}">
          <span id="title-text-${id}">${esc(post.title)}</span><button class="title-edit-btn" onclick="editTitle('${id}')" title="Edit title">✎</button>
          <input class="title-input" id="title-input-${id}" type="text" value="${esc(post.title)}"
            onblur="saveTitle('${id}')"
            onkeydown="if(event.key==='Enter'){event.preventDefault();saveTitle('${id}')}else if(event.key==='Escape'){cancelTitle('${id}')}">
        </div>
        <div class="summary">${esc(post.summary)}</div>
        <div class="draft-block" id="draft-block-${id}">${esc(post.draft)}</div>
        ${type === 'website' ? `<div class="rte-toolbar" id="toolbar-${id}" style="display:none">
          <button class="rte-btn" onclick="insertMarkdown('${id}','## ','')">H2</button>
          <button class="rte-btn" onclick="insertMarkdown('${id}','**','**')"><b>B</b></button>
          <button class="rte-btn" onclick="insertMarkdown('${id}','*','*')"><i>I</i></button>
          <button class="rte-btn" onclick="insertMarkdown('${id}','> ','')">Quote</button>
        </div>` : ''}
        <textarea class="draft-edit" id="draft-edit-${id}">${esc(post.draft)}</textarea>
        <div class="actions">
          ${approveRejectBtns}
          <button class="btn btn-edit" id="edit-btn-${id}" onclick="toggleEdit('${id}')">Edit Draft</button>
          <button class="btn btn-save" id="save-btn-${id}" onclick="saveDraft('${id}')" style="display:none">Save</button>
          <button class="btn btn-regen" id="regen-btn-${id}" onclick="regenerateDraft('${id}')">Regenerate</button>
          ${copyBtn}
          ${undoBtn}
        </div>
        ${publishedLink}
        ${renderSources(post)}
      </div>`;
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

    // Group paired linkedin+website cards into cluster groups
    const grouped = {};
    const order   = [];
    for (const post of queue.posts) {
      const key = post.id.replace(/_linkedin_|_website_/, '_');
      if (!grouped[key]) { grouped[key] = []; order.push(key); }
      grouped[key].push(post);
    }

    let html = gapBannerHtml();
    for (const key of order) {
      const cards = grouped[key];
      if (cards.length > 1) {
        html += `<div class="cluster-group">${cards.map(renderCard).join('')}</div>`;
      } else {
        html += renderCard(cards[0]);
      }
    }
    document.getElementById('main').innerHTML = html;
  }

  function gapBannerHtml() {
    return '';   // populated by loadGaps() after async fetch
  }

  async function loadGaps() {
    try {
      const res  = await fetch('/api/gaps');
      const data = await res.json();
      const gaps = data.gaps || [];
      if (gaps.length === 0) return;
      const list = gaps.map(g =>
        `<li><a href="${esc(g.top_story_url)}" target="_blank" rel="noopener noreferrer">${esc(g.topic)}</a>
         <span style="color:#856404;font-size:11px"> · ${esc(g.heat_label)} · ${g.article_count} sources</span></li>`
      ).join('');
      const banner = document.createElement('div');
      banner.id = 'gap-banner';
      banner.style.cssText = 'background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:14px 18px;margin-bottom:18px;font-size:13px;';
      banner.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <strong>⚠️ ${gaps.length} uncovered ${gaps.length === 1 ? 'story' : 'stories'} detected</strong>
          <button onclick="this.closest('#gap-banner').remove()" style="background:none;border:none;cursor:pointer;color:#666;font-size:16px;">✕</button>
        </div>
        <ul style="margin:0;padding-left:18px;line-height:1.8;">${list}</ul>`;
      const main = document.getElementById('main');
      main.insertBefore(banner, main.firstChild);
    } catch (e) { /* gaps.json may not exist yet */ }
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


@app.route("/api/gaps", methods=["GET"])
def get_gaps():
    path = "gaps.json"
    if not os.path.exists(path):
        return jsonify({"gaps": []}), 200
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/source-report", methods=["GET"])
def get_source_report():
    path = "source_report.json"
    if not os.path.exists(path):
        return jsonify({"error": "No source report yet"}), 200
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/regenerate", methods=["POST"])
def regenerate_draft():
    """
    Re-draft one post using the Anthropic API and the existing SYSTEM_PROMPT.
    Updates queue.json in place and returns the new draft text.
    """
    import anthropic
    from drafter import SYSTEM_PROMPT, MODEL, USER_PROMPT_TEMPLATE

    data = request.get_json(force=True)
    if not data or "id" not in data:
        return jsonify({"error": "Missing id"}), 400

    if not os.path.exists(QUEUE_FILE):
        return jsonify({"error": "Queue not found"}), 404

    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    post = next((p for p in queue_data.get("posts", []) if p.get("id") == data["id"]), None)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    api_key = os.getenv("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    bundle = (
        f"Title: {post.get('title', '')}\n"
        f"Source: {post.get('source', '')}\n"
        f"Summary: {post.get('summary', '')}\n"
        f"URL: {post.get('url', '')}"
    )
    user_msg = USER_PROMPT_TEMPLATE.format(count=1, bundle=bundle)

    try:
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=700,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        new_draft = response.content[0].text.strip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    post["draft"] = new_draft
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True, "draft": new_draft})


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    print("\n  IWF Dashboard running at http://localhost:5000")
    print("  Press Ctrl+C to stop.\n")
    app.run(debug=False, port=5000)
