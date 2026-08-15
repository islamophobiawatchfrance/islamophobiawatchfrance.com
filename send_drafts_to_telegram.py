#!/usr/bin/env python3
"""
Send Drafts to Telegram
------------------------
Reads queue.json and sends one Telegram message per pending post, each
with inline Approve / Reject buttons, so drafts can be reviewed from a
phone. Called automatically by run.py after drafter.py finishes; can
also be run standalone:

    python3 send_drafts_to_telegram.py
"""

import asyncio
import json
import os
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

import publisher

HERE          = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE    = os.path.join(HERE, "queue.json")
DASHBOARD_URL = "http://localhost:5000"
DRAFT_LIMIT   = 800
CHUNK_LIMIT   = 3500  # stay well under Telegram's 4096-char message cap

load_dotenv(os.path.join(HERE, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


def _md_escape(text: str) -> str:
    """Escape characters that break Telegram's legacy Markdown parser."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _plaintext_body(draft: str) -> str:
    """Render the drafter's lightweight markup (## headings, # title line,
    --- FAQ separator) as plain text for a full read on a phone."""
    lines = []
    for para in draft.split("\n\n"):
        para = para.strip()
        if not para or para.startswith("# "):
            continue  # skip a leading "# Title" line — it duplicates the title
        elif para.startswith("## "):
            lines.append(f"— {para[3:].strip().upper()} —")
        elif para == "---":
            lines.append("· · ·")
        else:
            lines.append(para.replace("**", ""))
    return "\n\n".join(lines)


def _chunk_text(text: str, limit: int) -> list:
    """Split text into <= limit chunks, breaking on paragraph boundaries where possible."""
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit * 0.5:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit * 0.5:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def _dashboard_is_public() -> bool:
    """Telegram rejects inline keyboard URL buttons that point at localhost /
    loopback addresses, so the Edit button only works once DASHBOARD_URL is a
    real public URL (e.g. an ngrok tunnel)."""
    host = urlparse(DASHBOARD_URL).hostname or ""
    return host not in ("localhost", "127.0.0.1")


def build_messages(post: dict) -> list:
    """Return a list of (text, markup, parse_mode) parts to send in order.
    Only the last part carries the Approve/Reject/Edit buttons."""
    post_id    = post["id"]
    title      = post.get("title", "Untitled")
    heat_label = post.get("heat_label", "NORMAL")
    category   = publisher._derive_category(post)
    source     = post.get("source", "")
    draft      = post.get("draft", "")

    approve_btn = InlineKeyboardButton("✓ Approve", callback_data=f"approve:{post_id}")
    reject_btn  = InlineKeyboardButton("✗ Reject", callback_data=f"reject:{post_id}")

    if post.get("type") == "website":
        # Full article body, in plain text (no parse_mode) so long AI-drafted
        # text with stray *, _, [ characters can never break Markdown parsing.
        header = f"🌐 WEBSITE ARTICLE\n🔥 {heat_label} | {category}\n\n{title}\n\n"
        footer = f"\n\nSource: {source}"
        if _dashboard_is_public():
            footer_last = footer
            buttons = [[approve_btn, reject_btn],
                       [InlineKeyboardButton("🖥 Edit in dashboard", url=DASHBOARD_URL)]]
        else:
            # Telegram rejects localhost URLs on inline buttons — fall back to
            # a plain-text link until DASHBOARD_URL points at a public tunnel.
            footer_last = footer + f"\n🖥 Edit in dashboard: {DASHBOARD_URL}"
            buttons = [[approve_btn, reject_btn]]

        body_chunks = _chunk_text(header + _plaintext_body(draft), CHUNK_LIMIT)
        body_chunks[-1] += footer_last

        parts = [(chunk, None, None) for chunk in body_chunks[:-1]]
        parts.append((body_chunks[-1], InlineKeyboardMarkup(buttons), None))
        return parts

    text = (
        f"📱 LINKEDIN DRAFT\n"
        f"🔥 {heat_label} | {category}\n\n"
        f"*{_md_escape(title)}*\n\n"
        f"{_truncate(_md_escape(draft), DRAFT_LIMIT)}\n\n"
        f"Source: {_md_escape(source)}"
    )
    return [(text, InlineKeyboardMarkup([[approve_btn, reject_btn]]), ParseMode.MARKDOWN)]


async def _send_all(pending: list) -> int:
    sent = 0
    async with Bot(token=BOT_TOKEN) as bot:
        for post in pending:
            try:
                for text, markup, parse_mode in build_messages(post):
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=text,
                        parse_mode=parse_mode,
                        reply_markup=markup,
                    )
                sent += 1
            except Exception as e:
                print(f"  [Telegram send failed for {post.get('id')}] {e}")
    return sent


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("  Telegram not configured (missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env). Skipping.")
        return

    if not os.path.exists(QUEUE_FILE):
        print("  No queue.json found. Nothing to send.")
        return

    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        queue = json.load(f)

    pending = [p for p in queue.get("posts", []) if p.get("status") == "pending"]
    if not pending:
        print("  No pending posts to send to Telegram.")
        return

    sent = asyncio.run(_send_all(pending))
    print(f"  Sent {sent}/{len(pending)} draft(s) to Telegram.")


if __name__ == "__main__":
    main()
