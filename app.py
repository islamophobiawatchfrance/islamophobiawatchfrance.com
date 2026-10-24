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
import subprocess
from datetime import datetime, timezone
from dotenv import load_dotenv
from flask import Flask, jsonify, request, render_template_string, send_from_directory
from werkzeug.utils import secure_filename

import queue_store

# Anchor all file operations to the directory containing app.py, regardless
# of the working directory from which Flask is launched.
REPO_PATH  = os.path.dirname(os.path.abspath(__file__))
os.chdir(REPO_PATH)
load_dotenv(os.path.join(REPO_PATH, ".env"))

app = Flask(__name__)

QUEUE_FILE   = "queue.json"
ARCHIVE_FILE = "archive.json"
SOURCES_FILE = "sources.json"

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
    .stat.scanned  { color: #888;    }

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
    .btn-tiktok-send { background: #fff; color: #1e8e3e; border: 1px solid #1e8e3e; }
    .btn-tiktok-send:hover { background: #e6f4ea; }
    .btn-tiktok-send:disabled { opacity: 0.5; cursor: not-allowed; }

    /* ── TikTok script section ── */
    .tiktok-section { border-top: 1px solid #eee; margin-top: 6px; padding-top: 10px; }
    .tiktok-toggle {
      display: flex; align-items: center; gap: 6px;
      width: 100%; background: none; border: none; cursor: pointer;
      padding: 4px 0; font-size: 13px; font-weight: 600; color: #333;
      text-align: left;
    }
    .tiktok-toggle .chevron { color: #999; font-size: 11px; transition: transform 0.15s; display: inline-block; }
    .tiktok-toggle.open .chevron { transform: rotate(90deg); }
    .tiktok-body { display: none; margin-top: 12px; }
    .tiktok-body.open { display: block; }
    .tiktok-script-box {
      background: #f8f9fa; border-left: 3px solid #111;
      padding: 18px 20px; border-radius: 4px;
      font-size: 16px; line-height: 1.8; white-space: pre-wrap;
      color: #222; margin-bottom: 8px;
    }
    .tiktok-meta { font-size: 12px; color: #999; margin-bottom: 12px; }

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
    #push-fail-banner {
      display: none; position: sticky; top: 48px; z-index: 90;
      background: #c0392b; color: #fff; font-size: 13px; font-weight: 600;
      padding: 10px 16px; text-align: center; cursor: pointer;
    }
    #push-fail-banner span { font-weight: 400; margin-left: 8px; opacity: 0.85; }
    #push-fail-banner button {
      margin-left: 16px; padding: 2px 10px; font-size: 12px; font-weight: 700;
      background: rgba(255,255,255,0.25); border: 1px solid rgba(255,255,255,0.5);
      color: #fff; border-radius: 4px; cursor: pointer;
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

    /* ── Story Browser ── */
    .stories-header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
    .stories-title { font-size: 14px; font-weight: 600; }
    .stories-meta { font-size: 12px; color: #888; }
    .filter-bar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
    .filter-btn {
      padding: 4px 10px; font-size: 11px; font-weight: 600;
      border: 1px solid #ddd; border-radius: 4px; background: #fff;
      color: #555; cursor: pointer;
    }
    .filter-btn.active { background: #111; color: #fff; border-color: #111; }
    .filter-btn:hover:not(.active) { background: #f5f5f5; }
    .story-search {
      width: 100%; padding: 7px 10px; font-size: 13px;
      border: 1px solid #ccc; border-radius: 6px; margin-bottom: 12px;
      font-family: inherit; box-sizing: border-box;
    }
    .story-search:focus { outline: none; border-color: #1a73e8; }
    .story-list { display: flex; flex-direction: column; gap: 0; }
    .story-row {
      display: flex; align-items: center; gap: 8px; flex-wrap: nowrap;
      padding: 8px 0; border-bottom: 1px solid #f0f0f0; overflow: hidden;
    }
    .story-row:last-child { border-bottom: none; }
    .story-time { font-size: 11px; color: #aaa; white-space: nowrap; flex-shrink: 0; }
    .story-link {
      flex: 1; font-size: 13px; color: #111; line-height: 1.4;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      min-width: 0; text-decoration: none;
    }
    .story-link:hover { color: #1a73e8; text-decoration: underline; }
    .story-selected { background: #e6f4ea; color: #1e8e3e; font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 3px; flex-shrink: 0; }
    .btn-generate { padding: 4px 10px; font-size: 11px; white-space: nowrap; flex-shrink: 0; }
    .btn-generate.done { background: #1e8e3e; color: #fff; border-color: #1e8e3e; }

    /* ── Write article tab ── */
    .write-form { display: flex; flex-direction: column; gap: 14px; }
    .write-field-label { font-size: 11px; font-weight: 700; color: #666; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    .write-input, .write-select, .write-textarea {
      width: 100%; box-sizing: border-box; font-family: inherit; font-size: 13px;
      padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px;
      background: #fff; color: #111; outline: none;
    }
    .write-input:focus, .write-select:focus, .write-textarea:focus { border-color: #1a73e8; }
    .write-input.headline { font-size: 18px; font-weight: 600; padding: 10px 12px; }
    .write-textarea { min-height: 90px; resize: vertical; line-height: 1.6; }
    .write-row { display: flex; gap: 12px; }
    .write-row > div { flex: 1; }
    /* RTE */
    .rte-wrap { border: 1px solid #ddd; border-radius: 6px; overflow: hidden; }
    .rte-toolbar-full {
      display: flex; flex-wrap: wrap; gap: 4px;
      background: #f5f5f3; border-bottom: 0.5px solid #ddd;
      padding: 6px 8px;
    }
    .rte-tbtn {
      height: 28px; padding: 0 10px; font-size: 12px; font-weight: 500;
      background: #fff; border: 1px solid #d0d0d0; border-radius: 4px;
      cursor: pointer; font-family: monospace; color: #333; white-space: nowrap;
    }
    .rte-tbtn:hover { background: #e8f0fe; color: #1a73e8; border-color: #1a73e8; }
    .rte-tbtn.active { background: #e8f0fe; color: #1a73e8; border-color: #1a73e8; }
    .rte-body {
      min-height: 400px; padding: 1rem; font-size: 14px; line-height: 1.75;
      outline: none; color: #111;
    }
    .rte-body h2 { font-size: 1.15rem; font-weight: 700; margin: 20px 0 8px; }
    .rte-body h3 { font-size: 1rem; font-weight: 700; margin: 16px 0 6px; }
    .rte-body blockquote { border-left: 3px solid #c8502a; margin: 12px 0; padding: 8px 14px; background: #fdf5f2; font-style: italic; color: #555; }
    .rte-body hr { border: none; border-top: 1px solid #ddd; margin: 20px 0; }
    .rte-body ul { padding-left: 1.4em; margin: 8px 0; }
    .rte-body a { color: #1a73e8; }
    .rte-body img { max-width: 100%; height: auto; border-radius: 6px; margin: 0.5rem 0; }
    .rte-body figure { margin: 1rem 0; }
    .rte-body figcaption { font-size: 12px; color: #888; font-style: italic; min-width: 20px; display: block; }
    .rte-body figcaption:empty::before { content: 'Add caption…'; color: #bbb; }
    /* Article image styles (also in css/style.css for live pages) */
    .art-img { width: 100%; max-height: 500px; object-fit: cover; border-radius: 6px; margin: 1rem 0 0.25rem; display: block; }
    .art-figure { margin: 1.5rem 0; }
    .art-caption { font-size: 12px; color: #888; font-style: italic; padding: 0.25rem 0 0; display: block; }
    .art-caption:empty { display: none; }
    /* Write action buttons */
    .write-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 6px; }
    .btn-write-preview  { background: #fff; color: #555; border: 1px solid #ccc; }
    .btn-write-preview:hover  { background: #f5f5f5; }
    .btn-write-draft    { background: #fff; color: #1a73e8; border: 1px solid #1a73e8; }
    .btn-write-draft:hover    { background: #e8f0fe; }
    .btn-write-publish  { background: #1e8e3e; color: #fff; border: none; }
    .btn-write-publish:hover  { background: #157234; }
    .btn-write-publish:disabled, .btn-write-draft:disabled { opacity: 0.6; cursor: not-allowed; }
    /* Preview modal */
    .write-preview-backdrop {
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.45); z-index: 1000; align-items: center; justify-content: center;
    }
    .write-preview-backdrop.open { display: flex; }
    .write-preview-modal {
      background: #fff; border-radius: 10px; max-width: 680px; width: 90%;
      max-height: 80vh; overflow-y: auto; padding: 28px 32px; position: relative;
    }
    .write-preview-close {
      position: absolute; top: 14px; right: 18px; background: none; border: none;
      font-size: 20px; cursor: pointer; color: #888;
    }
    .write-preview-close:hover { color: #333; }
    .write-preview-headline { font-size: 22px; font-weight: 700; margin-bottom: 6px; }
    .write-preview-meta { font-size: 12px; color: #aaa; margin-bottom: 18px; }
    .write-preview-body { font-size: 14px; line-height: 1.75; }
    .write-preview-body h2 { font-size: 1.1rem; font-weight: 700; margin: 18px 0 7px; }
    .write-preview-body h3 { font-size: 0.95rem; font-weight: 700; margin: 14px 0 5px; }
    .write-preview-body blockquote { border-left: 3px solid #c8502a; margin: 10px 0; padding: 7px 12px; background: #fdf5f2; font-style: italic; color: #555; }
    .write-preview-body hr { border: none; border-top: 1px solid #ddd; margin: 18px 0; }
    .write-preview-body ul { padding-left: 1.4em; margin: 8px 0; }
    .badge.manual-badge { background: #f1f3f4; color: #666; }
    .badge.rel-a { background: #e6f4ea; color: #1e8e3e; }
    .badge.rel-b { background: #e8f0fe; color: #1a73e8; }
    .badge.rel-d { background: #fce8e6; color: #a32d2d; }
    .badge.rel-c { background: #f1f3f4; color: #666; }
    .inversion-bar { background: #fce8e6; color: #a32d2d; font-size: 12px; padding: 8px 12px; border-radius: 6px; margin: 8px 0; }
    .src-table { width:100%; border-collapse:collapse; background:#fff; border-radius:10px; border:1px solid #e4e4e4; overflow:hidden; font-size:13px; }
    .src-table th { padding:10px 12px; text-align:left; background:#f8f9fa; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:#888; }
    .src-table td { padding:8px 12px; border-top:1px solid #f0f0f0; vertical-align:middle; }
    .src-table tr.disabled td { color:#aaa; }
    .src-table select, .src-form input, .src-form select { font:inherit; font-size:13px; padding:5px 8px; border:1px solid #ddd; border-radius:6px; background:#fff; }
    .src-form { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:16px; padding:14px; background:#fff; border:1px solid #e4e4e4; border-radius:10px; }
    .src-form input { flex:1 1 140px; min-width:0; }
    .src-del { background:none; border:1px solid #f1c0bc; color:#d93025; border-radius:6px; padding:3px 9px; cursor:pointer; font-size:12px; }
    .badge.conf-high   { background: #e6f4ea; color: #1e8e3e; }
    .badge.conf-medium { background: #fef7e0; color: #854f0b; }
    .badge.conf-low    { background: #fce8e6; color: #d93025; }
    .low-confidence-bar {
      margin-top: 6px; padding: 5px 10px; font-size: 12px; color: #856404;
      background: #fff3cd; border-radius: 4px; border-left: 3px solid #ffc107;
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

    /* ── Published tab ── */
    .pub-list { max-width: 820px; margin: 0 auto; display: flex; flex-direction: column; gap: 8px; }
    .pub-row {
      display: flex; align-items: center; gap: 12px; background: #fff;
      border: 1px solid #e5e5e5; border-radius: 6px; padding: 12px 14px;
    }
    .pub-row-main { flex: 1; min-width: 0; }
    .pub-row-title { font-size: 14px; font-weight: 600; color: #222; }
    .pub-row-meta { font-size: 12px; color: #999; margin-top: 3px; }
    .pub-row a.pub-view { font-size: 12px; color: #1e8e3e; text-decoration: none; white-space: nowrap; }
    .pub-row a.pub-view:hover { text-decoration: underline; }
    .write-edit-banner {
      background: #fff8e1; border-left: 3px solid #f2a600; border-radius: 4px;
      padding: 8px 12px; font-size: 13px; color: #555;
    }

    /* ── Published link ── */
    .published-link {
      display: inline-block; font-size: 12px; color: #1e8e3e;
      text-decoration: none; padding: 2px 0; margin-top: 4px;
    }
    .published-link:hover { text-decoration: underline; }
    .publishing-notice { font-size: 12px; color: #999; font-style: italic; margin-top: 4px; }
    .push-failed-bar {
      display: flex; align-items: center; gap: 8px;
      margin-top: 8px; padding: 6px 10px;
      background: #fce8e6; border-radius: 4px; border-left: 3px solid #d93025;
    }
    .push-failed-bar .push-failed-msg { font-size: 12px; color: #d93025; font-weight: 600; flex: 1; }
    .btn-retry-push { font-size: 11px; padding: 3px 10px; background: #d93025; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
    .btn-retry-push:hover { background: #b31412; }

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
    <span class="stat scanned"  id="stat-scanned">—</span>
  </div>
</header>

<nav class="tab-nav">
  <button class="tab active" id="tab-queue"   onclick="switchTab('queue')">Queue</button>
  <button class="tab"        id="tab-stories" onclick="switchTab('stories')">Story Browser</button>
  <button class="tab"        id="tab-write"   onclick="switchTab('write')">Write article</button>
  <button class="tab"        id="tab-published" onclick="switchTab('published')">Published</button>
  <button class="tab"        id="tab-managesources" onclick="switchTab('managesources')">Manage Sources</button>
  <button class="tab"        id="tab-sources" onclick="switchTab('sources')">Source Report</button>
</nav>

<div id="push-fail-banner" onclick="dismissPushFail()">
  ⚠ Git push failed — article saved locally but not live.
  <span id="push-fail-title"></span>
  <button onclick="event.stopPropagation();runGitPush()">Retry push</button>
  <button onclick="event.stopPropagation();dismissPushFail()">✕</button>
</div>
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
    document.getElementById('tab-stories').classList.toggle('active', tab === 'stories');
    document.getElementById('tab-write').classList.toggle('active', tab === 'write');
    document.getElementById('tab-published').classList.toggle('active', tab === 'published');
    document.getElementById('tab-sources').classList.toggle('active', tab === 'sources');
    document.getElementById('tab-managesources').classList.toggle('active', tab === 'managesources');
    document.getElementById('heat-bar').style.display = tab === 'queue' ? '' : 'none';
    if (tab === 'queue')        { render(); loadGaps(); }
    else if (tab === 'stories') loadStoryBrowser();
    else if (tab === 'write')   renderWriteTab();
    else if (tab === 'published') loadPublished();
    else if (tab === 'sources') loadSourceReport();
    else if (tab === 'managesources') loadManageSources();
  }

  // ── Manage Sources tab ────────────────────────────────────

  let sourcesData = [];

  async function loadManageSources() {
    try {
      const res = await fetch('/api/sources');
      sourcesData = (await res.json()).sources || [];
      renderManageSources();
    } catch (e) {
      document.getElementById('main').innerHTML = '<div class="empty"><h2>Could not load sources.json.</h2></div>';
    }
  }

  function trustSelect(s) {
    const opts = ['high', 'medium', 'low', 'monitor-only'];
    return `<select onchange="updateSource('${esc(encodeURIComponent(s.name))}', {trust: this.value})">` +
      opts.map(o => `<option value="${o}" ${o === s.trust ? 'selected' : ''}>${o}</option>`).join('') + '</select>';
  }

  function renderManageSources() {
    const enabled = sourcesData.filter(s => s.enabled).length;
    const rows = sourcesData.map(s => {
      const key = esc(encodeURIComponent(s.name));
      return `<tr class="${s.enabled ? '' : 'disabled'}">
        <td style="font-weight:500"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none" title="${esc(s.url)}">${esc(s.name)}</a></td>
        <td>${esc(s.category)}</td>
        <td>${esc(s.language)}</td>
        <td>${esc(s.bias)}</td>
        <td>${trustSelect(s)}</td>
        <td><label style="cursor:pointer"><input type="checkbox" ${s.enabled ? 'checked' : ''}
              onchange="updateSource('${key}', {enabled: this.checked})"> ${s.enabled ? 'On' : 'Off'}</label></td>
        <td><button class="src-del" onclick="deleteSource('${key}', this)">Delete</button></td>
      </tr>`;
    }).join('');
    document.getElementById('main').innerHTML = `
      <div style="padding:8px 0;">
        <div style="font-size:12px;color:#999;margin-bottom:16px;">${sourcesData.length} sources · ${enabled} enabled · changes save immediately to sources.json and apply from the next pipeline run</div>
        <div style="overflow-x:auto">
        <table class="src-table">
          <thead><tr><th>Name</th><th>Category</th><th>Language</th><th>Bias</th><th>Trust</th><th>Enabled</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        </div>
        <form class="src-form" onsubmit="addSource(event)">
          <strong style="width:100%;font-size:13px">Add source</strong>
          <input name="name" placeholder="Name" required>
          <input name="url" placeholder="RSS URL" type="url" required style="flex-basis:260px">
          <select name="language"><option>FR</option><option>EN</option></select>
          <input name="category" placeholder="Category (e.g. local-lyon)" required>
          <input name="bias" placeholder="Bias (e.g. centrist)" required>
          <select name="trust"><option>high</option><option selected>medium</option><option>low</option><option>monitor-only</option></select>
          <button class="btn btn-approve" type="submit">Add source</button>
        </form>
      </div>`;
  }

  async function updateSource(key, changes) {
    const res  = await fetch('/api/sources/' + key, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
    });
    const data = await res.json();
    if (!res.ok) { showToast(data.error || 'Update failed'); }
    else { showToast('Saved'); }
    loadManageSources();
  }

  async function deleteSource(key, btn) {
    if (btn.dataset.confirm !== '1') {
      btn.dataset.confirm = '1';
      btn.textContent = 'Confirm delete';
      setTimeout(() => { btn.dataset.confirm = ''; btn.textContent = 'Delete'; }, 4000);
      return;
    }
    const res = await fetch('/api/sources/' + key, { method: 'DELETE' });
    const data = await res.json();
    showToast(res.ok ? 'Source deleted' : (data.error || 'Delete failed'));
    loadManageSources();
  }

  async function addSource(ev) {
    ev.preventDefault();
    const form = ev.target;
    const body = Object.fromEntries(new FormData(form).entries());
    const btn  = form.querySelector('button[type=submit]');
    btn.disabled = true; btn.textContent = 'Checking feed…';
    try {
      const res  = await fetch('/api/sources', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { showToast(data.error || 'Could not add source'); return; }
      showToast(data.feed_items > 0
        ? `Added — feed returned ${data.feed_items} item(s)`
        : 'Added, but the feed returned no items — check the URL');
      loadManageSources();
    } finally {
      btn.disabled = false; btn.textContent = 'Add source';
    }
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
          post.push_failed   = !!data.push_failed;
          render();
          if (data.push_failed) {
            showPushFailBanner(post.title);
          } else {
            const bsky = data.bluesky ? ` · 🦋 Bluesky: ${data.bluesky.status}` : '';
            showToast('Published to islamophobiawatchfrance.com' + bsky);
          }
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

  async function retryPush(id) {
    const btn = document.querySelector(`#card-${id} .btn-retry-push`);
    if (btn) { btn.disabled = true; btn.textContent = 'Pushing…'; }
    try {
      const res  = await fetch('/api/retry_push', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (data.ok) {
        const post = getPost(id);
        if (post) { post.push_failed = false; }
        render();
        showToast('Push succeeded — article is live');
      } else {
        showToast('Push failed: ' + (data.error || 'unknown error'));
        if (btn) { btn.disabled = false; btn.textContent = 'Retry push'; }
      }
    } catch (e) {
      showToast('Push failed (network error)');
      if (btn) { btn.disabled = false; btn.textContent = 'Retry push'; }
    }
  }

  function showPushFailBanner(title) {
    const banner = document.getElementById('push-fail-banner');
    document.getElementById('push-fail-title').textContent = title ? '— ' + title : '';
    banner.style.display = 'block';
  }

  function dismissPushFail() {
    document.getElementById('push-fail-banner').style.display = 'none';
  }

  async function runGitPush() {
    const banner = document.getElementById('push-fail-banner');
    banner.textContent = '↻ Retrying git push…';
    try {
      const res  = await fetch('/api/retry_push', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
      const data = await res.json();
      if (data.ok) {
        banner.style.display = 'none';
        showToast('Push succeeded — article is live');
      } else {
        banner.innerHTML = '⚠ Push failed again. <button onclick="dismissPushFail()" style="margin-left:8px;padding:2px 8px;background:rgba(255,255,255,0.25);border:1px solid rgba(255,255,255,0.5);color:#fff;border-radius:4px;cursor:pointer">Dismiss</button>';
      }
    } catch {
      banner.innerHTML = '⚠ Network error. <button onclick="dismissPushFail()" style="margin-left:8px;padding:2px 8px;background:rgba(255,255,255,0.25);border:1px solid rgba(255,255,255,0.5);color:#fff;border-radius:4px;cursor:pointer">Dismiss</button>';
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

  function cardImageUpload(id) {
    const inp = document.getElementById('card-img-input-' + id);
    if (inp) inp.click();
  }

  async function cardImageSelected(input, id) {
    const file = input.files[0];
    if (!file) return;
    input.value = '';
    const fd = new FormData();
    fd.append('image', file);
    try {
      const res  = await fetch('/api/upload_image', { method: 'POST', body: fd });
      const data = await res.json();
      if (!data.ok) { showToast('Upload failed: ' + (data.error || 'unknown')); return; }
      const ta = document.getElementById('draft-edit-' + id);
      if (!ta) return;
      const pos = ta.selectionStart;
      const insert = '\n' + data.html + '\n';
      ta.setRangeText(insert, pos, pos, 'end');
      ta.focus();
      showToast('Image uploaded');
    } catch { showToast('Network error uploading image'); }
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

  async function loadScannedCount() {
    try {
      const res  = await fetch('/api/stories');
      const data = await res.json();
      if (data.total != null) {
        document.getElementById('stat-scanned').textContent = data.total + ' scanned';
      }
    } catch (e) { /* stories_today.json may not exist yet */ }
  }

  function geoBadgeHtml(geo) {
    if (geo === 'france') return '<span class="badge time" title="France angle">🇫🇷 France</span>';
    if (geo === 'europe') return '<span class="badge time" title="European story — Islamophobia is the central subject">🇪🇺 Europe</span>';
    return '';
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

    const pushFailedBar = post.push_failed
      ? `<div class="push-failed-bar">
           <span class="push-failed-msg">⚠ Push failed — article saved locally but not live</span>
           <button class="btn-retry-push" onclick="retryPush('${id}')">Retry push</button>
         </div>` : '';

    const undoBtn = post.status !== 'pending'
      ? `<button class="btn btn-undo" onclick="undoAction('${id}')">Undo</button>` : '';

    const manualBadge = post.manual
      ? `<span class="badge manual-badge">Manual</span>` : '';

    const confBadge = (() => {
      const c = post.content_confidence;
      if (!c) return '';
      if (c === 'high')   return `<span class="badge conf-high">✓ Full article</span>`;
      if (c === 'medium') return `<span class="badge conf-medium">~ Partial content</span>`;
      return `<span class="badge conf-low">⚠ Limited source</span>`;
    })();

    const relBadge = (() => {
      const c = post.relevance_category;
      if (!c || post.relevance_score == null) return '';
      return `<span class="badge rel-${esc(c.toLowerCase())}" title="${esc(post.relevance_reason || '')}">Cat ${esc(c)} · ${esc(post.relevance_score)}/10</span>`;
    })();

    const geoBadge = geoBadgeHtml(post.geo_scope);

    const inversionBar = post.inversion_required
      ? `<div class="inversion-bar"><strong>INVERSION REQUIRED</strong> — documented Islamophobia (Category D). The actor making the claim must be the subject; check the draft never relays the claim as fact.</div>` : '';

    const lowConfBar = (post.content_confidence === 'low')
      ? `<div class="low-confidence-bar">This draft was generated from limited source material (RSS summary only). Verify facts before publishing.</div>` : '';

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
          ${relBadge}
          ${geoBadge}
          ${manualBadge}
          ${confBadge}
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
          <button class="rte-btn" onclick="cardImageUpload('${id}')">🖼 Image</button>
          <input type="file" id="card-img-input-${id}" accept="image/jpeg,image/jpg,image/png,image/gif,image/webp" style="display:none" onchange="cardImageSelected(this,'${id}')">
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
        ${inversionBar}
        ${lowConfBar}
        ${publishedLink}
        ${pushFailedBar}
        ${renderSources(post)}
      </div>`;
  }

  function renderTiktokSection(post) {
    const id     = post.id;
    const script = post.tiktok_script || '';
    const words  = script.trim() ? script.trim().split(/\s+/).length : 0;
    return `
      <div class="tiktok-section">
        <button class="tiktok-toggle" id="tiktok-toggle-${id}" onclick="toggleTiktok('${id}')">
          <span class="chevron">›</span><span>📱 TikTok Script</span>
        </button>
        <div class="tiktok-body" id="tiktok-body-${id}">
          <div class="tiktok-script-box" id="tiktok-script-${id}">${esc(script)}</div>
          <div class="tiktok-meta" id="tiktok-meta-${id}">${words} words</div>
          <div class="actions">
            <button class="btn btn-copy" onclick="copyTiktokScript('${id}')">Copy script</button>
            <button class="btn btn-tiktok-send" id="tiktok-send-btn-${id}" onclick="sendTiktokTelegram('${id}')">Send to Telegram</button>
            <button class="btn btn-regen" id="tiktok-regen-btn-${id}" onclick="regenerateTiktok('${id}')">Regenerate</button>
          </div>
        </div>
      </div>`;
  }

  function toggleTiktok(id) {
    const body   = document.getElementById('tiktok-body-'   + id);
    const toggle = document.getElementById('tiktok-toggle-' + id);
    const open   = body.classList.toggle('open');
    toggle.classList.toggle('open', open);
  }

  function copyTiktokScript(id) {
    const post = getPost(id);
    if (!post || !post.tiktok_script) return;
    navigator.clipboard.writeText(post.tiktok_script).then(() => {
      showToast('TikTok script copied to clipboard');
    });
  }

  async function sendTiktokTelegram(id) {
    const btn = document.getElementById('tiktok-send-btn-' + id);
    if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }
    try {
      const res  = await fetch('/api/send_tiktok_telegram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ post_id: id }),
      });
      const data = await res.json();
      if (data.ok) {
        showToast('TikTok script sent to Telegram');
      } else {
        showToast('Send failed: ' + (data.error || 'unknown'));
      }
    } catch (e) {
      showToast('Send failed (network error)');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Send to Telegram'; }
    }
  }

  async function regenerateTiktok(id) {
    const btn = document.getElementById('tiktok-regen-btn-' + id);
    if (btn) { btn.disabled = true; btn.textContent = 'Regenerating…'; }
    try {
      const res  = await fetch('/api/regenerate_tiktok', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (data.ok && data.tiktok_script) {
        const post = getPost(id);
        if (post) post.tiktok_script = data.tiktok_script;
        document.getElementById('tiktok-script-' + id).textContent = data.tiktok_script;
        const words = data.tiktok_script.trim().split(/\s+/).length;
        document.getElementById('tiktok-meta-' + id).textContent = words + ' words';
        showToast('TikTok script regenerated');
      } else {
        showToast('Regeneration failed: ' + (data.error || 'unknown'));
      }
    } catch (e) {
      showToast('Regeneration failed (network error)');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Regenerate'; }
    }
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
      const tiktokPost = cards.find(p => p.tiktok_script);
      const tiktokHtml = tiktokPost ? renderTiktokSection(tiktokPost) : '';
      if (cards.length > 1) {
        html += `<div class="cluster-group">${cards.map(renderCard).join('')}${tiktokHtml}</div>`;
      } else {
        html += renderCard(cards[0]) + tiktokHtml;
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

  // ── Write article tab ─────────────────────────────────────

  function renderWriteTab() {
    editingFilename = null;
    const main = document.getElementById('main');
    main.innerHTML = `
      <div class="write-form" id="write-form">
        <div>
          <div class="write-field-label">Headline</div>
          <input class="write-input headline" id="wf-headline" type="text" placeholder="Article headline">
        </div>
        <div class="write-row">
          <div>
            <div class="write-field-label">Category</div>
            <select class="write-select" id="wf-category">
              <option>Islamophobia</option>
              <option>Policy &amp; Law</option>
              <option>Muslim Life</option>
              <option>European Context</option>
              <option>Far Right</option>
              <option>Community</option>
            </select>
          </div>
          <div>
            <div class="write-field-label">Heat label</div>
            <select class="write-select" id="wf-heat">
              <option value="NORMAL">NORMAL</option>
              <option value="TRENDING">TRENDING</option>
              <option value="HOT">HOT</option>
            </select>
          </div>
        </div>
        <div>
          <div class="write-field-label">Sources <span style="font-weight:400;text-transform:none;letter-spacing:0">(Name | URL, Name | URL)</span></div>
          <input class="write-input" id="wf-sources" type="text" placeholder="Le Monde | https://lemonde.fr/..., Reuters | https://reuters.com/...">
        </div>
        <div>
          <div class="write-field-label">Body</div>
          <div class="rte-wrap" id="rte-wrap">
            <div class="rte-toolbar-full" id="rte-toolbar">
              <button class="rte-tbtn" onclick="rteCmd('h2')">H2</button>
              <button class="rte-tbtn" onclick="rteCmd('h3')">H3</button>
              <button class="rte-tbtn" onclick="rteCmd('bold')"><b>B</b></button>
              <button class="rte-tbtn" onclick="rteCmd('italic')"><i>I</i></button>
              <button class="rte-tbtn" onclick="rteCmd('quote')">Quote</button>
              <button class="rte-tbtn" onclick="rteCmd('link')">Link</button>
              <button class="rte-tbtn" onclick="rteCmd('list')">• List</button>
              <button class="rte-tbtn" onclick="rteCmd('divider')">― Divider</button>
              <button class="rte-tbtn" onclick="rteImage()">🖼 Image</button>
            </div>
            <input type="file" id="rte-img-input" accept="image/jpeg,image/jpg,image/png,image/gif,image/webp" style="display:none" onchange="rteImageSelected(this)">
            <div class="rte-body" id="rte-body" contenteditable="true" spellcheck="true"></div>
          </div>
        </div>
        <div>
          <div class="write-field-label">LinkedIn post</div>
          <textarea class="write-textarea" id="wf-linkedin" rows="5" placeholder="Short LinkedIn version of this story (200–280 words)…"></textarea>
        </div>
        <div class="write-actions">
          <button class="btn btn-write-preview" onclick="writePreview()">Preview</button>
          <button class="btn btn-write-draft"   id="btn-wdraft"   onclick="writeSaveDraft()">Save draft</button>
          <button class="btn btn-write-publish" id="btn-wpublish" onclick="writePublish()">Publish directly</button>
        </div>
      </div>

      <!-- Preview modal -->
      <div class="write-preview-backdrop" id="write-preview-backdrop" onclick="closeWritePreview(event)">
        <div class="write-preview-modal">
          <button class="write-preview-close" onclick="closeWritePreview()">✕</button>
          <div class="write-preview-headline" id="wp-headline"></div>
          <div class="write-preview-meta" id="wp-meta"></div>
          <div class="write-preview-body" id="wp-body"></div>
        </div>
      </div>
    `;
    _attachPasteHandler();
  }

  // ── RTE commands ──────────────────────────────────────────

  function rteCmd(cmd) {
    const body = document.getElementById('rte-body');
    if (!body) return;
    body.focus();
    const sel = window.getSelection();

    if (cmd === 'bold')   { document.execCommand('bold',   false, null); return; }
    if (cmd === 'italic') { document.execCommand('italic', false, null); return; }

    if (cmd === 'link') {
      const url = prompt('Enter URL:');
      if (!url) return;
      document.execCommand('createLink', false, url);
      return;
    }

    if (cmd === 'list') {
      document.execCommand('insertUnorderedList', false, null);
      return;
    }

    if (cmd === 'divider') {
      document.execCommand('insertHTML', false, '<hr class="art-divider"><p><br></p>');
      return;
    }

    // Block-level commands: h2, h3, quote — wrap current block
    const tag = cmd === 'h2' ? 'H2' : cmd === 'h3' ? 'H3' : 'BLOCKQUOTE';
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    let block = range.commonAncestorContainer;
    if (block.nodeType === 3) block = block.parentNode;
    // Walk up to find a direct child of rte-body
    while (block.parentNode && block.parentNode !== body) block = block.parentNode;

    if (block === body || !body.contains(block)) {
      // No existing block — just insert
      document.execCommand('formatBlock', false, tag);
    } else if (block.tagName === tag) {
      // Toggle off — convert back to p
      document.execCommand('formatBlock', false, 'P');
    } else {
      document.execCommand('formatBlock', false, tag);
    }
  }

  // ── Image upload (Write Article tab) ─────────────────────

  let _rteImgRange = null;  // selection saved before file dialog steals focus

  function rteImage() {
    // Save the current cursor position before the file dialog opens and
    // blurs the editor — execCommand needs an active selection to insert at.
    const body = document.getElementById('rte-body');
    if (body) {
      const sel = window.getSelection();
      if (sel && sel.rangeCount > 0 && body.contains(sel.getRangeAt(0).commonAncestorContainer)) {
        _rteImgRange = sel.getRangeAt(0).cloneRange();
      } else {
        // No cursor in editor — default to end of content
        const r = document.createRange();
        r.selectNodeContents(body);
        r.collapse(false);
        _rteImgRange = r;
      }
    }
    const inp = document.getElementById('rte-img-input');
    if (inp) inp.click();
  }

  async function rteImageSelected(input) {
    const file = input.files[0];
    if (!file) return;
    input.value = '';
    const fd = new FormData();
    fd.append('image', file);
    try {
      const res  = await fetch('/api/upload_image', { method: 'POST', body: fd });
      const data = await res.json();
      if (!data.ok) { showToast('Upload failed: ' + (data.error || 'unknown')); return; }
      const body = document.getElementById('rte-body');
      if (!body) return;
      // Restore saved selection so execCommand inserts at the right position
      body.focus();
      if (_rteImgRange) {
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(_rteImgRange);
        _rteImgRange = null;
      }
      document.execCommand('insertHTML', false, data.html + '<p><br></p>');
      // Place cursor inside the new figcaption so user can type a caption
      const caps = body.querySelectorAll('figcaption');
      const cap  = caps[caps.length - 1];
      if (cap) {
        const range = document.createRange();
        range.selectNodeContents(cap);
        range.collapse(false);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
      showToast('Image uploaded');
    } catch { showToast('Network error uploading image'); }
  }

  // ── Paste sanitizer for the Write Article editor ──────────
  // Strips all classes, styles, and data-* attrs from pasted HTML so that
  // content copied from Claude.ai (or any other rich UI) arrives as clean
  // semantic markup.  Runs entirely in the browser before the content lands
  // in the editor, so nothing dirty ever reaches published.json.

  function _cleanNode(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.cloneNode();
    if (node.nodeType !== Node.ELEMENT_NODE) return null;

    const tag = node.tagName.toUpperCase();

    // Tags we promote/allow
    const MAP = {
      P: 'P', DIV: 'P', SECTION: 'P',
      H1: 'H2', H2: 'H2', H3: 'H2', H4: 'H2', H5: 'H2', H6: 'H2',
      STRONG: 'STRONG', B: 'STRONG', EM: 'EM', I: 'EM',
      A: 'A', BR: 'BR', HR: 'HR',
      UL: 'UL', OL: 'OL', LI: 'LI',
      BLOCKQUOTE: 'BLOCKQUOTE',
      DL: 'DL', DT: 'DT', DD: 'DD',
    };

    const newTag = MAP[tag];

    if (!newTag) {
      // Unknown wrapper (span, article, header, etc.) — flatten, keep children
      const frag = document.createDocumentFragment();
      node.childNodes.forEach(c => { const n = _cleanNode(c); if (n) frag.appendChild(n); });
      return frag;
    }

    const el = document.createElement(newTag);
    if (newTag === 'A' && node.href) el.href = node.href;
    node.childNodes.forEach(c => { const n = _cleanNode(c); if (n) el.appendChild(n); });

    // Drop empty block wrappers (e.g. <p><br></p> spacers)
    if (['P','H2','DT','DD'].includes(newTag)) {
      const text = el.textContent.trim();
      if (!text && !el.querySelector('img')) return null;
    }

    return el;
  }

  function _sanitizeHtml(html) {
    const div = document.createElement('div');
    div.innerHTML = html;
    const out = document.createElement('div');
    div.childNodes.forEach(c => { const n = _cleanNode(c); if (n) out.appendChild(n); });
    return out.innerHTML;
  }

  // Wire up the paste handler once the editor is rendered
  function _attachPasteHandler() {
    const body = document.getElementById('rte-body');
    if (!body || body._pasteHandlerAttached) return;
    body._pasteHandlerAttached = true;
    body.addEventListener('paste', function(e) {
      e.preventDefault();
      const html = e.clipboardData.getData('text/html');
      const text = e.clipboardData.getData('text/plain');
      const clean = html ? _sanitizeHtml(html) : text.replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>');
      document.execCommand('insertHTML', false, clean || '');
    });
  }

  // ── Write actions ─────────────────────────────────────────

  function writeFormData() {
    return {
      headline:    document.getElementById('wf-headline')?.value.trim() || '',
      category:    document.getElementById('wf-category')?.value || 'Islamophobia',
      heat_label:  document.getElementById('wf-heat')?.value || 'NORMAL',
      sources_raw: document.getElementById('wf-sources')?.value.trim() || '',
      body_html:   document.getElementById('rte-body')?.innerHTML || '',
      linkedin_text: document.getElementById('wf-linkedin')?.value.trim() || '',
    };
  }

  function writePreview() {
    const d = writeFormData();
    if (!d.headline) { showToast('Enter a headline first'); return; }
    document.getElementById('wp-headline').textContent = d.headline;
    document.getElementById('wp-meta').textContent = d.category + ' · ' + d.heat_label;
    document.getElementById('wp-body').innerHTML = d.body_html;
    document.getElementById('write-preview-backdrop').classList.add('open');
  }

  function closeWritePreview(e) {
    if (!e || e.target === document.getElementById('write-preview-backdrop') || e.type !== 'click' || !e.target.closest) {
      document.getElementById('write-preview-backdrop')?.classList.remove('open');
    } else if (!e.target.closest('.write-preview-modal')) {
      document.getElementById('write-preview-backdrop').classList.remove('open');
    }
  }

  async function writeSaveDraft() {
    const d = writeFormData();
    if (!d.headline) { showToast('Enter a headline first'); return; }
    const btn = document.getElementById('btn-wdraft');
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      const res  = await fetch('/api/manual_draft', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(d),
      });
      const data = await res.json();
      if (data.ok) {
        showToast(data.message || 'Draft saved to queue');
        // reload queue so it reflects the new post
        const r2 = await fetch('/api/queue');
        queue = await r2.json();
      } else {
        showToast('Error: ' + (data.error || 'unknown'));
      }
    } catch { showToast('Network error'); }
    finally { btn.disabled = false; btn.textContent = 'Save draft'; }
  }

  async function writePublish() {
    const d = writeFormData();
    if (!d.headline) { showToast('Enter a headline first'); return; }
    if (!confirm('Publish this article directly without queuing?')) return;
    const btn = document.getElementById('btn-wpublish');
    btn.disabled = true; btn.textContent = 'Publishing…';
    try {
      const res  = await fetch('/api/manual_publish', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(d),
      });
      const data = await res.json();
      if (data.ok) {
        document.getElementById('main').innerHTML =
          `<div class="empty"><h2>Article published</h2><p><a href="${esc(data.url)}" target="_blank" rel="noopener">${esc(data.url)}</a></p><button class="btn btn-write-draft" onclick="renderWriteTab()" style="margin-top:12px">Write another</button></div>`;
        if (data.warning) {
          showPushFailBanner(d.headline);
        } else {
          showToast('Published to islamophobiawatchfrance.com');
        }
      } else {
        showToast('Error: ' + (data.error || 'unknown'));
        btn.disabled = false; btn.textContent = 'Publish directly';
      }
    } catch { showToast('Network error'); btn.disabled = false; btn.textContent = 'Publish directly'; }
  }

  // ── Published tab (edit live articles) ────────────────────

  let publishedArticles = [];
  let editingFilename = null;   // set while the Write form is editing a live article

  async function loadPublished() {
    const main = document.getElementById('main');
    main.innerHTML = '<div class="empty"><h2 style="font-size:15px">Loading articles…</h2></div>';
    try {
      const res  = await fetch('/api/published');
      publishedArticles = (await res.json()).articles || [];
    } catch {
      main.innerHTML = '<div class="empty"><h2>Could not load published articles.</h2></div>';
      return;
    }
    if (!publishedArticles.length) {
      main.innerHTML = '<div class="empty"><h2>No published articles yet.</h2></div>';
      return;
    }
    main.innerHTML = '<div class="pub-list">' + publishedArticles.map((a, i) => {
      const meta = [a.date, a.category, a.updated_at ? 'edited ' + a.updated_at.slice(0, 10) : '']
        .filter(Boolean).join(' · ');
      return `<div class="pub-row">
        <div class="pub-row-main">
          <div class="pub-row-title">${esc(a.title)}</div>
          <div class="pub-row-meta">${esc(meta)}</div>
        </div>
        <a class="pub-view" href="https://islamophobiawatchfrance.com/${esc(a.filename)}" target="_blank" rel="noopener">View live →</a>
        <button class="btn btn-write-draft" onclick="editPublished(${i})">Edit</button>
      </div>`;
    }).join('') + '</div>';
  }

  function editPublished(i) {
    const a = publishedArticles[i];
    if (!a) return;
    renderWriteTab();
    editingFilename = a.filename;
    document.getElementById('wf-headline').value = a.title;
    document.getElementById('wf-heat').value = a.heat_label || 'NORMAL';
    document.getElementById('wf-sources').value =
      (a.sources || []).map(s => s.url ? `${s.name} | ${s.url}` : s.name).join(', ');
    document.getElementById('rte-body').innerHTML = a.body_html;
    // Category is derived from the text at publish time; LinkedIn isn't stored
    document.getElementById('wf-category').closest('div').style.display = 'none';
    document.getElementById('wf-linkedin').closest('div').style.display = 'none';
    document.getElementById('btn-wdraft').style.display = 'none';
    const btn = document.getElementById('btn-wpublish');
    btn.textContent = 'Update live article';
    btn.onclick = writeUpdatePublished;
    const banner = document.createElement('div');
    banner.className = 'write-edit-banner';
    banner.innerHTML = `Editing the live article <b>${esc(a.filename)}</b>. Its URL and date stay the same. ` +
      `<a href="#" onclick="switchTab('published');return false">Cancel</a>`;
    const form = document.getElementById('write-form');
    form.insertBefore(banner, form.firstChild);
  }

  async function writeUpdatePublished() {
    const d = writeFormData();
    if (!d.headline) { showToast('Enter a headline first'); return; }
    if (!confirm('Update the live article with these changes?')) return;
    d.filename = editingFilename;
    const btn = document.getElementById('btn-wpublish');
    btn.disabled = true; btn.textContent = 'Updating…';
    try {
      const res  = await fetch('/api/published/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(d),
      });
      const data = await res.json();
      if (data.ok) {
        editingFilename = null;
        document.getElementById('main').innerHTML =
          `<div class="empty"><h2>Article updated</h2><p><a href="${esc(data.url)}" target="_blank" rel="noopener">${esc(data.url)}</a></p><button class="btn btn-write-draft" onclick="switchTab('published')" style="margin-top:12px">Back to published</button></div>`;
        if (data.warning) showPushFailBanner(d.headline);
        else showToast('Live article updated');
      } else {
        showToast('Error: ' + (data.error || 'unknown'));
        btn.disabled = false; btn.textContent = 'Update live article';
      }
    } catch { showToast('Network error'); btn.disabled = false; btn.textContent = 'Update live article'; }
  }

  // ── Story Browser ─────────────────────────────────────────

  let allStories = [];
  let storyFilter = 'all';
  let storySearch = '';

  async function loadStoryBrowser() {
    document.getElementById('main').innerHTML =
      '<div class="empty"><h2 style="font-size:15px">Loading stories…</h2></div>';
    try {
      const res  = await fetch('/api/stories');
      const data = await res.json();
      if (data.error) {
        document.getElementById('main').innerHTML =
          `<div class="empty"><h2>${esc(data.error)}</h2><p>Run python3 drafter.py to scan stories.</p></div>`;
        return;
      }
      allStories = data.stories || [];
      if (data.total != null) {
        document.getElementById('stat-scanned').textContent = data.total + ' scanned';
      }
      renderStoryBrowser();
    } catch (e) {
      document.getElementById('main').innerHTML =
        '<div class="empty"><h2>No stories yet.</h2><p>Run python3 drafter.py to scan stories.</p></div>';
    }
  }

  function filterStories(f) {
    storyFilter = f;
    document.querySelectorAll('.filter-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.filter === f);
    });
    renderStoryList();
  }

  function searchStories(q) {
    storySearch = q.toLowerCase();
    renderStoryList();
  }

  function visibleStories() {
    return allStories.filter(s => {
      if (storyFilter === 'hot')      return s.heat_label === 'HOT';
      if (storyFilter === 'trending') return s.heat_label === 'TRENDING';
      if (storyFilter === 'normal')   return s.heat_label === 'NORMAL';
      if (storyFilter === 'selected') return s.selected;
      return true;
    }).filter(s => {
      if (!storySearch) return true;
      return (s.title + ' ' + s.source + ' ' + s.summary).toLowerCase().includes(storySearch);
    });
  }

  function renderStoryRow(s) {
    const heatCls  = s.heat_label === 'HOT' ? 'heat-hot' : (s.heat_label === 'TRENDING' ? 'heat-trending' : 'heat-normal');
    const selBadge = s.selected ? '<span class="story-selected">Selected</span>' : '';
    return `<div class="story-row" id="story-row-${esc(s.id)}">
      <span class="badge ${heatCls}" style="flex-shrink:0;font-size:9px">${esc(s.heat_label)}</span>
      <span class="story-time">${esc(s.time_ago)}</span>
      <span class="badge source" style="flex-shrink:0">${esc(s.source)}</span>
      ${geoBadgeHtml(s.geo_scope)}
      <a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" class="story-link" title="${esc(s.title)}">${esc(s.title)}</a>
      ${selBadge}
      <button class="btn btn-regen btn-generate" id="gen-btn-${esc(s.id)}" onclick="generatePosts('${esc(s.id)}')">Generate posts</button>
    </div>`;
  }

  function renderStoryList() {
    const list = document.getElementById('story-list');
    if (!list) return;
    const visible = visibleStories();
    list.innerHTML = visible.length
      ? visible.map(renderStoryRow).join('')
      : '<div style="padding:24px 0;color:#888;font-size:13px;">No stories match this filter.</div>';
  }

  function renderStoryBrowser() {
    const generated = allStories.length > 0
      ? '' : '';
    document.getElementById('main').innerHTML = `
      <div style="padding:4px 0;">
        <div class="stories-header">
          <span class="stories-title">${allStories.length} stories scanned today</span>
        </div>
        <div class="filter-bar">
          <button class="filter-btn active" data-filter="all"      onclick="filterStories('all')">All</button>
          <button class="filter-btn"        data-filter="hot"      onclick="filterStories('hot')">HOT</button>
          <button class="filter-btn"        data-filter="trending" onclick="filterStories('trending')">TRENDING</button>
          <button class="filter-btn"        data-filter="normal"   onclick="filterStories('normal')">NORMAL</button>
          <button class="filter-btn"        data-filter="selected" onclick="filterStories('selected')">Selected</button>
        </div>
        <input class="story-search" type="text" placeholder="Filter by keyword…" oninput="searchStories(this.value)">
        <div class="story-list" id="story-list"></div>
      </div>`;
    storyFilter = 'all';
    storySearch = '';
    renderStoryList();
  }

  async function generatePosts(storyId) {
    const btn = document.getElementById('gen-btn-' + storyId);
    if (!btn || btn.disabled) return;
    btn.disabled    = true;
    btn.textContent = 'Generating…';
    try {
      const res  = await fetch('/api/generate_manual', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ story_id: storyId }),
      });
      const data = await res.json();
      if (data.ok) {
        btn.textContent = 'Generated ✓';
        btn.classList.add('done');
        showToast(data.message || '2 posts added to queue');
      } else {
        btn.textContent = 'Generate posts';
        btn.disabled    = false;
        showToast('Error: ' + (data.error || 'unknown'));
      }
    } catch (e) {
      btn.textContent = 'Generate posts';
      btn.disabled    = false;
      showToast('Network error — check server logs');
    }
  }

  loadQueue();
  loadScannedCount();
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
    # load_queue never raises — malformed posts are dropped, a corrupt file is backed up
    return jsonify(queue_store.load_queue())


@app.route("/api/queue", methods=["POST"])
def save_queue():
    """Saves the posted JSON body back to queue.json."""
    data = request.get_json(force=True)
    if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
        return jsonify({"error": "Expected a queue object with a posts list"}), 400
    queue_store.save_queue(data)
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

    queue_data = queue_store.load_queue()

    post = next(
        (p for p in queue_data.get("posts", []) if p.get("id") == data["id"]),
        None,
    )
    if not post:
        return jsonify({"error": "Post not found in queue"}), 404

    try:
        url, push_ok = publisher.publish_post(post, repo_path=REPO_PATH)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    # Persist publish metadata back into queue.json
    now = datetime.now(timezone.utc).isoformat()
    post["published"]     = True
    post["published_url"] = url
    post["published_at"]  = now
    post["push_failed"]   = not push_ok
    queue_store.save_queue(queue_data)

    # Only announce on Bluesky once the article is actually live
    bluesky = (_post_bluesky_thread(post["id"]) if push_ok
               else {"posted": False, "status": "skipped until git push succeeds"})

    return jsonify({"ok": True, "url": url, "push_failed": not push_ok, "bluesky": bluesky})


def _post_bluesky_thread(post_id: str) -> dict:
    """Post the Bluesky thread for a published queue post and record the result."""
    import bluesky_poster

    queue_data = queue_store.load_queue()
    post = next((p for p in queue_data["posts"] if p.get("id") == post_id), None)
    if post is None or post.get("bluesky_posted"):
        return {"posted": bool(post and post.get("bluesky_posted")), "status": "already posted"}
    ok = bluesky_poster.post_article_thread(post)
    # Re-load: the dashboard may have saved the queue while we were posting
    queue_data = queue_store.load_queue()
    for p in queue_data["posts"]:
        if p.get("id") == post_id:
            p["bluesky_posted"] = ok
            if post.get("bluesky_thread"):
                p["bluesky_thread"] = post["bluesky_thread"]
    queue_store.save_queue(queue_data)
    return {"posted": ok, "status": "thread posted" if ok else "failed — see logs/bluesky.log"}


@app.route("/api/retry_push", methods=["POST"])
def retry_push():
    data = request.get_json(silent=True) or {}
    post_id = data.get("id")

    # If a post id was given, mark it as no longer push-failed in queue.json
    queue_data = queue_store.load_queue()
    post = next((p for p in queue_data.get("posts", []) if p.get("id") == post_id), None) if post_id else None

    result = subprocess.run(["git", "push"], capture_output=True, text=True,
                            cwd=REPO_PATH, env=os.environ.copy())
    if result.returncode != 0:
        print(f"  [retry_push failed] stdout={result.stdout!r} stderr={result.stderr!r}")
        return jsonify({"ok": False, "error": result.stderr.strip()}), 500

    if post is None:
        return jsonify({"ok": True})

    post["push_failed"] = False
    queue_store.save_queue(queue_data)

    # The article is live now — post the Bluesky thread that was held back
    bluesky = None
    if post.get("published") and post.get("type") == "website" and not post.get("bluesky_posted"):
        bluesky = _post_bluesky_thread(post_id)
    return jsonify({"ok": True, "bluesky": bluesky})


@app.route("/api/gaps", methods=["GET"])
def get_gaps():
    path = "gaps.json"
    if not os.path.exists(path):
        return jsonify({"gaps": []}), 200
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/stories", methods=["GET"])
def get_stories():
    path = "stories_today.json"
    if not os.path.exists(path):
        return jsonify({"error": "No stories yet — run python3 drafter.py"}), 200
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/generate_manual", methods=["POST"])
def generate_manual():
    """Draft LinkedIn + website posts from a single story in stories_today.json."""
    import anthropic as _anthropic
    import datetime as _dt
    from drafter import (
        draft_post, draft_website_article, build_post_record,
        build_cluster_bundle, fetch_article_text, gather_deep_research,
    )

    data = request.get_json(force=True)
    if not data or "story_id" not in data:
        return jsonify({"error": "Missing story_id"}), 400

    stories_path = "stories_today.json"
    if not os.path.exists(stories_path):
        return jsonify({"error": "stories_today.json not found — run drafter.py first"}), 404

    with open(stories_path, "r", encoding="utf-8") as f:
        stories_data = json.load(f)

    story = next(
        (s for s in stories_data.get("stories", []) if s.get("id") == data["story_id"]),
        None,
    )
    if not story:
        return jsonify({"error": "Story not found"}), 404

    api_key = os.getenv("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    # Build a minimal cluster dict from the single story
    try:
        pub_dt = _dt.datetime.fromisoformat(story["published"].replace("Z", "+00:00"))
    except Exception:
        pub_dt = _dt.datetime.now(_dt.timezone.utc)

    cluster_story = {
        "title":   story["title"],
        "source":  story["source"],
        "url":     story["url"],
        "published": pub_dt,
        "summary": story.get("summary", ""),
        "source_trust":       story.get("source_trust", ""),
        "relevance_score":    story.get("relevance_score"),
        "relevance_category": story.get("relevance_category", ""),
        "relevance_reason":   story.get("relevance_reason", ""),
        "inversion_required": story.get("inversion_required", False),
    }
    cluster = {"stories": [cluster_story]}
    score   = story.get("heat_score", 0)
    label   = story.get("heat_label", "NORMAL")

    article_texts = {story["title"]: fetch_article_text(story["url"])}
    bundle        = build_cluster_bundle(cluster, article_texts)

    client    = _anthropic.Anthropic(api_key=api_key)
    timestamp = _dt.datetime.now(_dt.timezone.utc)
    post_idx  = int(timestamp.timestamp()) % 100000

    posts = []
    li_draft = draft_post(client, cluster, bundle)
    if li_draft:
        posts.append(build_post_record(cluster, score, label, li_draft, post_idx, timestamp, "linkedin"))

    deep      = gather_deep_research(cluster)
    web_draft = draft_website_article(client, cluster, bundle, deep)
    if web_draft:
        posts.append(build_post_record(cluster, score, label, web_draft, post_idx, timestamp, "website"))

    if not posts:
        return jsonify({"error": "Draft generation failed — check API key"}), 500

    # Append to queue.json
    queue_data = queue_store.load_queue()
    queue_data["posts"].extend(posts)
    queue_store.save_queue(queue_data)

    return jsonify({"ok": True, "message": f"{len(posts)} posts added to queue"})


@app.route("/api/source-report", methods=["GET"])
def get_source_report():
    path = "source_report.json"
    if not os.path.exists(path):
        return jsonify({"error": "No source report yet"}), 200
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


# ── Source management (sources.json) ───────────────────────

SOURCE_FIELDS = ("name", "url", "language", "bias", "trust", "category")
TRUST_LEVELS  = ("high", "medium", "low", "monitor-only")


def _load_sources() -> dict:
    if not os.path.exists(SOURCES_FILE):
        return {"sources": []}
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_sources(data: dict) -> None:
    tmp = SOURCES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SOURCES_FILE)


@app.route("/api/sources", methods=["GET"])
def get_sources():
    return jsonify(_load_sources())


@app.route("/api/sources", methods=["POST"])
def add_source():
    import iwf_aggregator

    body = request.get_json(silent=True) or {}
    source = {k: str(body.get(k, "")).strip() for k in SOURCE_FIELDS}
    missing = [k for k, v in source.items() if not v]
    if missing:
        return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
    if not source["url"].startswith(("http://", "https://")):
        return jsonify({"error": "RSS URL must start with http:// or https://"}), 400
    source["language"] = source["language"].upper()
    if source["trust"] not in TRUST_LEVELS:
        return jsonify({"error": f"Trust must be one of {', '.join(TRUST_LEVELS)}"}), 400
    source["enabled"] = True

    data = _load_sources()
    if any(s["name"].lower() == source["name"].lower() for s in data["sources"]):
        return jsonify({"error": f"A source called {source['name']} already exists"}), 409
    data["sources"].append(source)
    _save_sources(data)

    # Quick sanity check of the feed so a typo'd URL is obvious straight away
    items = len(iwf_aggregator.fetch_source(source, hours_back=24 * 365))
    return jsonify({"ok": True, "source": source, "feed_items": items})


@app.route("/api/sources/<path:name>", methods=["PUT"])
def update_source(name):
    body = request.get_json(silent=True) or {}
    data = _load_sources()
    source = next((s for s in data["sources"] if s["name"] == name), None)
    if source is None:
        return jsonify({"error": "Source not found"}), 404
    if "enabled" in body:
        source["enabled"] = bool(body["enabled"])
    if "trust" in body:
        if body["trust"] not in TRUST_LEVELS:
            return jsonify({"error": f"Trust must be one of {', '.join(TRUST_LEVELS)}"}), 400
        source["trust"] = body["trust"]
    for k in ("url", "language", "bias", "category"):
        if body.get(k):
            source[k] = str(body[k]).strip()
    _save_sources(data)
    return jsonify({"ok": True, "source": source})


@app.route("/api/sources/<path:name>", methods=["DELETE"])
def delete_source(name):
    data = _load_sources()
    remaining = [s for s in data["sources"] if s["name"] != name]
    if len(remaining) == len(data["sources"]):
        return jsonify({"error": "Source not found"}), 404
    data["sources"] = remaining
    _save_sources(data)
    return jsonify({"ok": True})


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


@app.route("/api/regenerate_tiktok", methods=["POST"])
def regenerate_tiktok():
    """
    Re-generate the TikTok script for one post using drafter.generate_tiktok_script.
    Updates queue.json in place and returns the new script text.
    """
    from drafter import generate_tiktok_script

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

    if not os.getenv("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    new_script = generate_tiktok_script(post)
    if not new_script:
        return jsonify({"error": "TikTok script generation failed"}), 500

    post["tiktok_script"] = new_script
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True, "tiktok_script": new_script})


@app.route("/api/send_tiktok_telegram", methods=["POST"])
def send_tiktok_telegram():
    """
    Send one post's TikTok script to Telegram for reference — no Approve/Reject
    buttons, just the formatted script text.
    """
    import asyncio
    from telegram import Bot
    from telegram.constants import ParseMode
    import publisher

    data = request.get_json(force=True)
    if not data or "post_id" not in data:
        return jsonify({"error": "Missing post_id"}), 400

    if not os.path.exists(QUEUE_FILE):
        return jsonify({"error": "Queue not found"}), 404

    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    post = next((p for p in queue_data.get("posts", []) if p.get("id") == data["post_id"]), None)
    if not post:
        return jsonify({"error": "Post not found"}), 404

    if not post.get("tiktok_script"):
        return jsonify({"error": "Post has no tiktok_script"}), 400

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return jsonify({"error": "Telegram not configured (missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"}), 500

    message = publisher.format_tiktok_message(post)

    async def _send():
        async with Bot(token=bot_token) as bot:
            await bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)

    try:
        asyncio.run(_send())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})


def _sanitize_body_html(html: str) -> str:
    """
    Server-side backstop: strip class/style/data-* attributes and Claude UI
    markup from article body HTML before it is stored or published.
    Runs even if the browser paste handler is bypassed.
    """
    import re as _re
    # Remove all class, style, and data-* attributes
    html = _re.sub(r'\s+class="[^"]*"', '', html)
    html = _re.sub(r'\s+style="[^"]*"', '', html)
    html = _re.sub(r'\s+data-[a-z][a-z0-9-]*="[^"]*"', '', html)
    # Promote h3–h6 to h2 (Claude uses h3 for article sections)
    html = _re.sub(r'<h[3-6](\s[^>]*)?>', '<h2>', html)
    html = _re.sub(r'</h[3-6]>', '</h2>', html)
    # Strip div wrappers entirely (keep their children)
    html = _re.sub(r'<div[^>]*>', '', html)
    html = _re.sub(r'</div>', '', html)
    # Remove empty paragraphs (just whitespace/br)
    html = _re.sub(r'<p>\s*(<br\s*/?>\s*)*</p>', '', html)
    return html.strip()


def _build_manual_post(data: dict) -> dict:
    """Build a queue post record from the Write article form payload."""
    import re as _re
    import publisher as pub

    headline     = data.get("headline", "Untitled").strip()
    category     = data.get("category", "Islamophobia")
    heat_label   = data.get("heat_label", "NORMAL").upper()
    sources_raw  = data.get("sources_raw", "")
    body_html    = _sanitize_body_html(data.get("body_html", ""))
    linkedin_text = data.get("linkedin_text", "")

    # Parse sources: "Name | URL, Name | URL"
    sources = []
    for entry in sources_raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "|" in entry:
            parts = entry.split("|", 1)
            sources.append({"name": parts[0].strip(), "url": parts[1].strip()})
        else:
            sources.append({"name": entry, "url": ""})

    first_source_name = sources[0]["name"] if sources else "IWF"
    first_source_url  = sources[0]["url"]  if sources else ""

    # Strip HTML tags for summary
    summary = _re.sub(r"<[^>]+>", "", body_html)[:200].strip()

    heat_score = {"HOT": 8, "TRENDING": 5}.get(heat_label, 2)
    now_iso    = datetime.now(timezone.utc).isoformat()
    post_id    = "manual_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    return {
        "id":                post_id,
        "title":             headline,
        "source":            first_source_name,
        "url":               first_source_url,
        "created":           now_iso,
        "time_ago":          "just now",
        "summary":           summary,
        "draft":             linkedin_text,
        "website_draft":     body_html,
        "status":            "pending",
        "type":              "website",
        "manual":            True,
        "category":          category,
        "heat_label":        heat_label,
        "heat_score":        heat_score,
        "heat_article_count": "Manual entry",
        "sources":           sources,
    }


@app.route("/api/manual_draft", methods=["POST"])
def manual_draft():
    data = request.get_json(silent=True) or {}
    if not data.get("headline"):
        return jsonify({"error": "headline required"}), 400

    post = _build_manual_post(data)

    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            queue_data = json.load(f)
    else:
        queue_data = {"posts": []}

    queue_data.setdefault("posts", []).insert(0, post)
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True, "message": "Draft saved to queue"})


@app.route("/api/manual_publish", methods=["POST"])
def manual_publish():
    import traceback as _tb
    import publisher

    try:
        data = request.get_json(silent=True) or {}
        if not data.get("headline"):
            return jsonify({"error": "headline required"}), 400

        post = _build_manual_post(data)
        post["draft"] = post.get("website_draft", "")

        url, push_ok = publisher.publish_post(post, repo_path=REPO_PATH)

        if not push_ok:
            return jsonify({
                "ok": True,
                "url": url,
                "warning": "Article saved but git push failed. Run git push manually.",
            })
        return jsonify({"ok": True, "url": url})

    except Exception as e:
        _tb.print_exc()
        return jsonify({"error": str(e)}), 500


def _draft_to_editor_html(draft: str) -> str:
    """
    Editor HTML for a published draft. Manual articles are already HTML;
    pipeline articles are markdown (paragraphs, ## headings, > quotes, ---).
    """
    import re as _re
    import publisher as pub

    if _re.search(r"<(p|h[1-6])[\s>]", draft or ""):
        return draft
    out = []
    for block in _re.split(r"\n\s*\n", draft or ""):
        block = block.strip()
        if not block:
            continue
        if block == "---":
            out.append("<hr>")
        elif block.startswith("#"):
            out.append(f"<h2>{pub._render_inline(block.lstrip('#').strip())}</h2>")
        elif block.startswith("> "):
            out.append(f"<blockquote>{pub._render_inline(block[2:])}</blockquote>")
        else:
            out.append("<p>" + "<br>".join(pub._render_inline(l) for l in block.split("\n")) + "</p>")
    return "".join(out)


@app.route("/api/published", methods=["GET"])
def list_published():
    """Published articles (newest first) with editor-ready body HTML."""
    path = os.path.join(REPO_PATH, "published.json")
    if not os.path.exists(path):
        return jsonify({"articles": []})
    with open(path, "r", encoding="utf-8") as f:
        articles = json.load(f).get("articles", [])
    articles = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)
    return jsonify({"articles": [{
        "filename":   a.get("filename", ""),
        "title":      a.get("title", ""),
        "date":       a.get("date", ""),
        "category":   a.get("category", ""),
        "heat_label": a.get("heat_label", "NORMAL"),
        "sources":    a.get("sources", []),
        "updated_at": a.get("updated_at", ""),
        "body_html":  _draft_to_editor_html(a.get("draft", "")),
    } for a in articles]})


@app.route("/api/published/update", methods=["POST"])
def update_published():
    """Save edits to a live article, keeping its URL and date, then push."""
    import traceback as _tb
    import publisher

    try:
        data = request.get_json(silent=True) or {}
        if not data.get("filename") or not data.get("headline"):
            return jsonify({"error": "filename and headline required"}), 400

        post = _build_manual_post(data)
        post["draft"] = post.get("website_draft", "")

        url, push_ok = publisher.update_article(data["filename"], post, repo_path=REPO_PATH)
        if not push_ok:
            return jsonify({
                "ok": True,
                "url": url,
                "warning": "Article updated but git push failed. Run git push manually.",
            })
        return jsonify({"ok": True, "url": url})

    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        _tb.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload_image", methods=["POST"])
def upload_image():
    ALLOWED = {"jpg", "jpeg", "png", "gif", "webp"}
    if "image" not in request.files:
        return jsonify({"error": "No image field in request"}), 400
    f = request.files["image"]
    if not f or not f.filename:
        return jsonify({"error": "No file selected"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED:
        return jsonify({"error": f"File type .{ext} not allowed"}), 400
    safe_name = secure_filename(f.filename)
    timestamp  = datetime.now().strftime("%Y%m%d")
    filename   = f"{timestamp}-{safe_name}"
    img_dir    = os.path.join(REPO_PATH, "img")
    os.makedirs(img_dir, exist_ok=True)
    dest = os.path.join(img_dir, filename)
    f.save(dest)
    url  = f"/img/{filename}"
    html = (
        f'<figure class="art-figure">'
        f'<img src="{url}" alt="" class="art-img">'
        f'<figcaption class="art-caption"></figcaption>'
        f'</figure>'
    )
    return jsonify({"ok": True, "url": url, "html": html})


@app.route("/img/<path:filename>")
def serve_image(filename):
    return send_from_directory(os.path.join(REPO_PATH, "img"), filename)


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    # Clear out anything stuck in the queue for more than 48 h
    queue_store.archive_stale_posts()
    print("\n  IWF Dashboard running at http://localhost:5000")
    print("  Press Ctrl+C to stop.\n")
    app.run(debug=False, port=5000)
