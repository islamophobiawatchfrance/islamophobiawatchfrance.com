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

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

import publisher

HERE          = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE    = os.path.join(HERE, "queue.json")
DASHBOARD_URL = "http://localhost:5000"
DRAFT_LIMIT   = 800

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


def _standfirst(draft: str) -> str:
    paragraphs = [p.strip() for p in draft.split("\n\n") if p.strip()]
    return paragraphs[0] if paragraphs else ""


def build_message(post: dict) -> tuple[str, InlineKeyboardMarkup]:
    post_id    = post["id"]
    title      = _md_escape(post.get("title", "Untitled"))
    heat_label = post.get("heat_label", "NORMAL")
    category   = publisher._derive_category(post)
    source     = _md_escape(post.get("source", ""))
    draft      = post.get("draft", "")

    approve_btn = InlineKeyboardButton("✓ Approve", callback_data=f"approve:{post_id}")
    reject_btn  = InlineKeyboardButton("✗ Reject", callback_data=f"reject:{post_id}")

    if post.get("type") == "website":
        word_count = len(draft.split())
        text = (
            f"🌐 WEBSITE ARTICLE\n"
            f"🔥 {heat_label} | {category}\n\n"
            f"*{title}*\n\n"
            f"{_md_escape(_standfirst(draft))}\n\n"
            f"_[Article is {word_count} words - tap to approve]_\n\n"
            f"Source: {source}"
        )
        edit_btn = InlineKeyboardButton("🖥 Edit in dashboard", url=DASHBOARD_URL)
        markup = InlineKeyboardMarkup([[approve_btn, reject_btn], [edit_btn]])
    else:
        text = (
            f"📱 LINKEDIN DRAFT\n"
            f"🔥 {heat_label} | {category}\n\n"
            f"*{title}*\n\n"
            f"{_truncate(_md_escape(draft), DRAFT_LIMIT)}\n\n"
            f"Source: {source}"
        )
        markup = InlineKeyboardMarkup([[approve_btn, reject_btn]])

    return text, markup


async def _send_all(pending: list) -> int:
    sent = 0
    async with Bot(token=BOT_TOKEN) as bot:
        for post in pending:
            text, markup = build_message(post)
            try:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
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
