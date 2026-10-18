# IWF Pipeline — User Guide

This guide explains how the Islamophobia Watch France publishing pipeline works day to day, what you need to do, and how to fix the most common problems. You don't need any programming knowledge to follow it.

Where you see a command in a grey box, it goes into the **Terminal** app. Open Terminal, paste the command, and press Return. Most commands start with `cd ~/Documents/iwf-aggregator`, which moves Terminal into the pipeline's folder.

---

## 1. Daily workflow

### What happens automatically (no action needed)

Your Mac does all of this by itself, as long as it is switched on and you're logged in:

| When (Paris time) | What happens |
|---|---|
| **Every 3 hours** — 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 | The **wire runner** checks all your news sources, scores every new story for relevance, adds relevant ones to the website's wire section, and queues short wire posts for Bluesky. |
| **07:00, 08:00, 12:00, 19:00, 20:00** | Queued Bluesky wire posts go out, one at a time, at least an hour apart. |
| **07:00 and 18:00** | The **full pipeline** runs. It does everything the wire runner does, and also writes full article drafts (a website article, a LinkedIn post and a TikTok script) for the strongest stories, then sends them to your phone. |

How a story is handled depends on its **relevance score** (0–10), which is decided automatically:

- **0–5:** ignored. Not relevant enough, or only mentions Islam in passing.
- **6–7:** **wire only.** It appears in the website's wire section and gets one short Bluesky post. No article is written.
- **8–10:** **full article.** Drafts are written and sent to you for approval. Up to two topics are drafted per run.

Stories from far-right or "monitor-only" sources (for example Valeurs Actuelles) can only come through as **Category D: documented Islamophobia**. That means the person or outlet making the claim is the subject of the story, and the claim is never repeated as if it were true. Drafts like this have a red **INVERSION REQUIRED** banner in the dashboard. Check them carefully before approving.

### What arrives on your phone via Telegram

After each full pipeline run (07:00 and 18:00), the IWF bot sends you a message for each new draft:

- **🌐 WEBSITE ARTICLE** — the full article text, split over several messages if it's long. The last message has the buttons.
- **📱 LINKEDIN DRAFT** — the LinkedIn post text.
- **TikTok script** — for reference only, so it has no buttons.

Each draft is only sent once. If you don't act on it, it stays in the dashboard queue for 48 hours. After that it moves to the archive automatically.

### How to approve or reject articles

On your phone, tap the button under the draft:

- **✓ Approve on a website article:** the article is published to islamophobiawatchfrance.com and a two-post Bluesky thread is posted. The message then updates to show:
  ```
  ✓ Published
  🌐 islamophobiawatchfrance.com/news/...
  🦋 Bluesky: thread posted
  ```
- **✓ Approve on a LinkedIn draft:** the draft is marked approved and the bot sends the text back so you can copy it into the LinkedIn app. Nothing is posted to LinkedIn automatically.
- **✗ Reject:** the draft is marked rejected. It isn't deleted, so you can still undo this from the dashboard.

You can also send **/rescan** to the bot to run the full pipeline straight away.

### How to open the dashboard when at your desk

Click the **IWF icon in the menu bar** (top-right of your screen) and choose **Open dashboard**. You can also type `http://localhost:5000` into your browser's address bar.

---

## 2. The dashboard (localhost:5000)

The tabs run along the top of the page.

### Queue tab — reviewing and approving articles

This tab shows every draft from the last 48 hours. Each card has coloured labels:

- **HOT / TRENDING / NORMAL:** how many sources are covering the story and how recent it is.
- **Cat A / B / D · score:** the editorial category and relevance score. Hover over it to see the reason.
  - **A:** core coverage.
  - **B:** cover with caution.
  - **D:** documented Islamophobia (shows the red inversion banner).
- **✓ Full article / ~ Partial content / ⚠ Limited source:** how much of the original article the pipeline was able to read. With "Limited source", check the facts against the original before publishing.

You can edit the text on a card, regenerate it, approve it or reject it. Approving a website article publishes it and posts the Bluesky thread, just as it does from Telegram. If the publish step can't reach GitHub, a yellow banner appears with a **Retry push** button. The Bluesky thread is held back until the article is actually live, and goes out when the retry succeeds.

### Story Browser — finding stories the pipeline didn't select

This tab lists every story from the last run that mentioned an IWF keyword, including ones that weren't drafted. Use the filters or the search box to look through them. Click **Generate posts** on any story to have drafts written and added to the Queue.

### Write Article — writing your own articles from scratch

Write or paste your own article, add images, and publish it directly. Text pasted from other websites or from Claude is cleaned up automatically. Articles published from this tab don't post to Bluesky automatically.

### Articles — editing already-published articles

There is **no Articles tab yet**. To correct an article that's already live:

1. Click the menu bar icon and choose **Open VS Code**.
2. Open the article's file in the `news/` folder. Files are named by date and title, for example `news/2026-10-18-mosque-vandalised-in-lyon.html`.
3. Make your correction and save the file.
4. In Terminal, run:
   ```
   cd ~/Documents/iwf-aggregator && git add news && git commit -m "Correct article" && git push
   ```

### Manage Sources — adding and removing news sources

This tab lists every news source the pipeline checks. Changes save immediately and apply from the next run.

- **Enabled:** untick to pause a source without deleting it.
- **Trust:** high / medium / low / monitor-only. Choose **monitor-only** for outlets you want to watch for Islamophobic content but never quote as a news source.
- **Delete:** click once, then click **Confirm delete** within 4 seconds.
- **Add source** (form at the bottom): enter a name, the RSS feed address, the language (FR/EN), a category (for example `local-lyon`), a bias label and a trust level. The pipeline tests the feed straight away. If it says the feed returned no items, the address is probably wrong.

> **Finding an RSS address:** many sites use `/feed/` or `/rss` after their main address. Search for "[site name] RSS" if you're not sure.

### Source Report — checking source diversity

This tab shows which outlets your published articles cite, and how often. Any source behind more than 30% of citations is flagged as **Over-relied**. If you see that flag, look for other outlets to balance your coverage.

---

## 3. Bluesky

### What gets posted automatically

- **Wire posts:** one short post (280 characters at most) for each story scoring 6–7. The post leads with the key fact, credits the source at the end (for example "— Le Monde"), and uses 2–3 hashtags.
- Wire posts only go out at peak times: **07:00–09:00, 12:00–13:00 and 19:00–21:00** Paris time. They are spaced at least an hour apart. Anything not posted yet waits for the next peak time.
- A story that is being turned into a full article doesn't get a wire post, because it gets a thread when you approve it instead.

### What gets posted when you approve an article

A **two-post thread**:

1. The opening post gives IWF's view of the most important finding, with no link.
2. A reply adds more context, then "Full analysis:" with the article link, and hashtags.

This happens whether you approve from Telegram or from the dashboard.

### How to check what has been posted

- Look at the IWF profile on Bluesky.
- Every post, and every failure with the reason, is recorded in **logs/bluesky.log**. To see the latest entries, run:
  ```
  tail -20 ~/Documents/iwf-aggregator/logs/bluesky.log
  ```
- To see what's waiting and when it's scheduled to post, run:
  ```
  cd ~/Documents/iwf-aggregator && python3 post_wire_stories.py
  ```
  This also posts anything that is due right now.

### The question limit (max 2 per day)

To keep the feed from reading like clickbait, no more than **two posts a day** may contain a question mark. Once two have gone out, any new post that comes out as a question is rewritten without one. The count resets at midnight Paris time.

---

## 4. Running the pipeline manually

### Running python3 run.py

- **From the menu bar:** click the IWF icon and choose **Run pipeline now**.
- **From Terminal:**
  ```
  cd ~/Documents/iwf-aggregator && python3 run.py
  ```
  This checks sources, drafts articles, sends the drafts to Telegram and opens the dashboard. Leave the Terminal window open while you use the dashboard. Press **Ctrl+C** to stop it.

To run only the quick wire update (no article drafting), run:

```
cd ~/Documents/iwf-aggregator && python3 run_wire.py
```

### What to do if nothing appears in the queue

1. **It may just be a quiet news day.** Drafts are only written for stories that score 8 or more. Near the top of the output, the pipeline prints a line like this:
   ```
   Relevance filtered: 31 rejected, 9 wire-only, 0 full pipeline
   ```
   If "full pipeline" is 0, nothing was strong enough to draft. Check the **Story Browser** and use **Generate posts** if you want to cover something anyway.
2. **The posts may be more than 48 hours old.** In that case they've moved to the archive.
3. **The run may have failed.** Look at the end of the morning log:
   ```
   tail -40 ~/Documents/iwf-aggregator/logs/pipeline-morning.log
   ```

### How to check if Bluesky posting is working

Run:

```
cd ~/Documents/iwf-aggregator && python3 bluesky_poster.py
```

If it says `Bluesky connected: True`, posting works. If it says `False`, see "Bluesky posts not appearing" below.

---

## 5. Common problems and fixes

### Dashboard not opening ("Address already in use" / port 5000 in use)

Another copy of the dashboard is probably already running. Try these in order:

1. Click the menu bar icon and choose **Stop dashboard**, then **Open dashboard**.
2. If that doesn't work, run this in Terminal to stop whatever is using the port, then open the dashboard again:
   ```
   lsof -ti :5000 | xargs kill
   ```
3. On macOS, **AirPlay Receiver** can also take port 5000. Go to System Settings → General → AirDrop & Handoff and turn **AirPlay Receiver** off.

### Queue appears empty

See "What to do if nothing appears in the queue" in section 4. Remember that posts move to the archive after 48 hours.

### Bluesky posts not appearing

1. Run `python3 bluesky_poster.py` (see section 4). If it says `False`, open **logs/bluesky.log**:
   - **"Invalid identifier or password":** the app password is wrong or has been revoked. On Bluesky, go to Settings → Privacy and security → App passwords and create a new one. Open the `.env` file and replace the value after `BLUESKY_APP_PASSWORD=` with it. Check that `BLUESKY_HANDLE` exactly matches your Bluesky handle (for example `name.bsky.social`).
2. Check the time. Wire posts only go out at peak times, so a story found at 15:00 won't post before 19:00.
3. Article threads are held back if the git push failed. Retry the push from the dashboard.

### Articles not showing on the live site

- The site takes **about a minute** to update after publishing.
- If the dashboard showed the yellow **"Git push failed"** banner, click **Retry push**.
- Check that the file exists in the `news/` folder. If it's there but not live, the push didn't go through. See the next item.

### Git push failed

This usually means the Mac was offline or GitHub needs you to sign in again.

1. Check your internet connection.
2. Click **Retry push** in the dashboard, or run:
   ```
   cd ~/Documents/iwf-aggregator && git push
   ```
3. If Terminal asks for a username or password, or says "authentication failed", your GitHub login needs refreshing. Sign in to GitHub again (for example through GitHub Desktop or with `gh auth login`), then retry.

---

## 6. File locations

| What | Where |
|---|---|
| The whole pipeline | `~/Documents/iwf-aggregator/` |
| Published articles | `news/` folder (one HTML file per article) |
| Logs | `logs/` folder: `pipeline-morning.log`, `pipeline-evening.log`, `wire.log` (3-hourly runs and wire posting), `bluesky.log`, `telegram-bot.log`, `menubar.log` |
| News source list | `sources.json` (edit it through the dashboard's Manage Sources tab) |
| Draft queue | `queue.json` (drafts from the last 48 hours) |
| Bluesky wire queue | `wire_queue.json` (wire posts waiting to go out, and recent ones already posted) |
| Archive of old drafts | `archive.json` |
| This guide | `USER_GUIDE.md` |

### The .env file

`.env` is a hidden file in the pipeline folder that holds your passwords and keys. **Never share it or upload it anywhere.** It is excluded from what gets pushed to GitHub. It contains:

| Setting | What it's for |
|---|---|
| `ANTHROPIC_API_KEY` | Lets the pipeline use Claude to score stories and write drafts |
| `TELEGRAM_BOT_TOKEN` | The IWF Telegram bot's key |
| `TELEGRAM_CHAT_ID` | Your Telegram chat, so only you can approve posts |
| `BLUESKY_HANDLE` | The IWF Bluesky account name |
| `BLUESKY_APP_PASSWORD` | A Bluesky *app password* (not your main password) |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` / `LINKEDIN_ORG_ID` | LinkedIn app details (not used for posting at the moment) |

To open it, press **Cmd+Shift+.** in Finder to show hidden files, or open the folder in VS Code from the menu bar.
