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
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import bluesky_poster
import publisher
import queue_store
import run as pipeline_run
import weekly_digest

HERE       = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(HERE, "queue.json")

load_dotenv(os.path.join(HERE, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


def _load_queue() -> dict:
    # Never raises: a corrupt queue.json is backed up and replaced, and
    # malformed posts are dropped, so one bad entry can't block every button.
    return queue_store.load_queue(QUEUE_FILE)


def _save_queue(queue: dict) -> None:
    queue_store.save_queue(queue, QUEUE_FILE)


def _site_link(url: str) -> str:
    return url.split("://", 1)[-1]


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _handle_callback(update, context)
    except Exception as e:
        print(f"  [callback error] {e!r}")
        try:
            await update.callback_query.edit_message_text(
                f"⚠️ Something went wrong handling that button: {e}\n\nOpen the dashboard to finish."
            )
        except Exception:
            pass


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if action.startswith("digest_"):
        # Buttons from the retired quiet-spell digest (replaced by weekly_digest.py)
        await query.edit_message_text("This digest was retired, nothing was posted. "
                                      "The Friday \"France this week\" thread replaces it.")
        return

    if action.startswith("weekly_"):
        await _handle_weekly(query, action, post_id)
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

            if not push_ok:
                await query.edit_message_text(
                    f"✓ Published (git push failed — saved locally, retry from dashboard): {title}\n"
                    f"🌐 {_site_link(url)}\n"
                    f"🦋 Bluesky: skipped until the article is live (posted when you retry the push)"
                )
                return

            await query.edit_message_text(f"✓ Published\n🌐 {_site_link(url)}\n🦋 Bluesky: posting thread…")
            bsky_ok = await asyncio.get_running_loop().run_in_executor(
                None, bluesky_poster.post_article_thread, post
            )
            # Re-load before saving: the queue may have changed while we were posting
            queue = _load_queue()
            for p in queue.get("posts", []):
                if p.get("id") == post_id:
                    p["bluesky_posted"] = bsky_ok
                    if post.get("bluesky_thread"):
                        p["bluesky_thread"] = post["bluesky_thread"]
            _save_queue(queue)
            status = "thread posted" if bsky_ok else "failed — see logs/bluesky.log"
            await query.edit_message_text(f"✓ Published\n🌐 {_site_link(url)}\n🦋 Bluesky: {status}")
        else:
            # LinkedIn posts are never auto-posted (no LinkedIn API call in
            # this pipeline) — mark approved and hand back the draft text so
            # it can be copied straight into the LinkedIn app.
            post["status"] = "approved"
            _save_queue(queue)
            draft = post.get("draft", "")
            await query.edit_message_text(f"✓ Approved: {title}\n\nCopy into LinkedIn:\n\n{draft}")


async def _handle_weekly(query, action: str, week: str) -> None:
    """Approve / Reject on the Friday "France this week" thread (see weekly_digest.py)."""
    loop = asyncio.get_running_loop()
    if action == "weekly_reject":
        ok = await loop.run_in_executor(None, weekly_digest.reject, week)
        await query.edit_message_text(f"✗ France this week ({week}) rejected, not posted." if ok
                                      else "⚠️ Weekly digest not pending (already handled?).")
        return
    if action != "weekly_approve":
        return

    due = await loop.run_in_executor(None, weekly_digest.approve, week)
    if due is None:
        await query.edit_message_text("⚠️ Weekly digest not pending (already handled?).")
        return
    # Wait out the 60-minute spacing here; if the bot restarts meanwhile, the
    # launchd re-runs (13:05, 14:05, 15:05) post the approved thread instead.
    for _ in range(6):
        wait = (due - datetime.now(weekly_digest.PARIS)).total_seconds()
        if wait > 0:
            await query.edit_message_text(f"✓ Approved. Posting at {due:%H:%M} (60 min after the last post)…")
            await asyncio.sleep(wait)
        try:
            status, due = await loop.run_in_executor(None, weekly_digest.post_if_due, week)
        except Exception as e:
            await query.edit_message_text(f"⚠️ France this week ({week}) failed to post: {e}")
            return
        if status != "waiting":
            break
    text = {"posted": f"✓ France this week ({week}) posted to Bluesky."}.get(
        status, f"France this week ({week}): {status}. See logs/weekly-digest.log.")
    await query.edit_message_text(text)


async def handle_rescan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if CHAT_ID and str(update.effective_chat.id) != str(CHAT_ID):
        return

    # drafter.py merges new drafts into queue.json (posts > 48 h old are
    # archived), so rescanning never discards unreviewed posts.
    if not pipeline_run._acquire_lock():
        await update.message.reply_text("⏳ Pipeline already running — try again shortly.")
        return

    await update.message.reply_text("🔄 Rescanning for new stories...")

    try:
        proc = await asyncio.create_subprocess_exec(sys.executable, "drafter.py", cwd=HERE)
        code = await proc.wait()
        if code != 0:
            await update.message.reply_text(
                "⚠️ Rescan failed — drafter.py exited with an error. Check logs/ for details."
            )
            return

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "send_drafts_to_telegram.py", cwd=HERE
        )
        await proc.wait()
        await update.message.reply_text("✅ Rescan complete — new drafts (if any) are above.")
    finally:
        if os.path.exists(pipeline_run.LOCK_FILE):
            os.remove(pipeline_run.LOCK_FILE)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set in .env")

    # Clear out anything stuck in the queue for more than 48 h
    queue_store.archive_stale_posts()

    async def _register_commands(a: Application) -> None:
        await a.bot.set_my_commands(
            [BotCommand("rescan", "Rescan for new stories and draft posts")]
        )

    app = Application.builder().token(BOT_TOKEN).post_init(_register_commands).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("rescan", handle_rescan))

    async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Transient Telegram network errors (e.g. "Bad Gateway") — log one line, keep polling
        print(f"  [telegram error] {context.error!r}")

    app.add_error_handler(_on_error)
    print("  Telegram bot listening for approve/reject actions...")
    app.run_polling()


if __name__ == "__main__":
    main()
