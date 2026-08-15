#!/usr/bin/env python3
"""
IWF Telegram Bot
----------------
Persistent listener for the Approve / Reject buttons sent by
send_drafts_to_telegram.py, so the queue can be worked entirely from a
phone without opening the dashboard.

Run with: python3 start_bot.py
"""

import asyncio
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

import publisher

HERE       = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(HERE, "queue.json")

load_dotenv(os.path.join(HERE, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


def _load_queue() -> dict:
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_queue(queue: dict) -> None:
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    # This bot can publish to the live site and push to git, so only the
    # configured chat is allowed to trigger actions.
    if CHAT_ID and str(query.message.chat_id) != str(CHAT_ID):
        await query.answer("Not authorized.", show_alert=True)
        return

    await query.answer()

    try:
        action, post_id = query.data.split(":", 1)
    except (ValueError, AttributeError):
        return

    if not os.path.exists(QUEUE_FILE):
        await query.edit_message_text("⚠️ queue.json not found.")
        return

    queue = _load_queue()
    post = next((p for p in queue.get("posts", []) if p.get("id") == post_id), None)
    if post is None:
        await query.edit_message_text("⚠️ Post not found in queue (already handled?).")
        return

    title = post.get("title", "Untitled")

    if action == "reject":
        post["status"] = "rejected"
        _save_queue(queue)
        await query.edit_message_text(f"✗ Rejected: {title}")
        return

    if action == "approve":
        if post.get("type") == "website":
            try:
                # publish_post shells out to git and can take a few seconds —
                # run it off the event loop so other callbacks stay responsive.
                url, push_ok = await asyncio.get_running_loop().run_in_executor(
                    None, publisher.publish_post, post, HERE
                )
            except Exception as e:
                await query.edit_message_text(
                    f"⚠️ Publish failed: {title}\n\n{e}\n\nLeft in queue — open the dashboard to retry."
                )
                return

            post["status"]        = "approved"
            post["published"]     = True
            post["published_url"] = url
            post["published_at"]  = datetime.now(timezone.utc).isoformat()
            post["push_failed"]   = not push_ok
            _save_queue(queue)

            if push_ok:
                await query.edit_message_text(f"✓ Published: {title}\n{url}")
            else:
                await query.edit_message_text(
                    f"✓ Published (git push failed — saved locally, retry from dashboard): {title}\n{url}"
                )
        else:
            # LinkedIn posts are never auto-posted (no LinkedIn API call in
            # this pipeline) — mark approved and hand back the draft text so
            # it can be copied straight into the LinkedIn app.
            post["status"] = "approved"
            _save_queue(queue)
            draft = post.get("draft", "")
            await query.edit_message_text(f"✓ Approved: {title}\n\nCopy into LinkedIn:\n\n{draft}")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set in .env")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("  Telegram bot listening for approve/reject actions...")
    app.run_polling()


if __name__ == "__main__":
    main()
