# GeeLark Studio — User Guide

Welcome! This guide walks you through every step of the new single-page dashboard. No spreadsheets, no Google Drive — everything happens right here in the app.

---

## Getting Started

Open the dashboard URL your tech team gave you and sign in with your username and password. First time here? Click **Register** on the login page and enter the invite code your admin shared. You'll land on the **Dashboard** tab, which shows recent background activity.

The four tabs are:

| Tab | What it's for |
|---|---|
| **Dashboard** | See recent job runs and activity; emergency Stop All Phones button |
| **Accounts** | Manage your accounts (TikTok, YouTube, Instagram, Facebook) and GeeLark phones, organized into groups |
| **Videos** | Upload videos and publish them across one or more platforms |
| **Settings** | Configure schedule times, cooldown, caption defaults, and secrets |

---

## Setting Up a New Account

### Organize by group

The Accounts tab has a **group rail** on the left. Each group is a logical bucket of accounts that share a campaign / lane / persona (e.g. "IELTS Australia", "Tokyo Travel"). You'll mostly work inside one group at a time. Click **New group** to create one, or pick **All accounts** at the top of the rail to see everything in one table.

Per group you can also click the **⋯** menu next to the group name and pick **Group settings** to edit the group name and choose its **Caption prompt** — see the Settings section below for how prompts work.

### Step 1 — Import or add accounts

**If you already have phones in GeeLark:**
Go to the **Accounts** tab and click **Import from GeeLark**. This pulls in all existing GeeLark profiles so you don't have to add them by hand.

**If you're starting fresh:**
Click **+ Add** in the Accounts tab. A small prompt will pop up asking for a profile name — type a unique name and confirm. The account appears instantly as a new row in the table.

Now fill in the rest by clicking directly on the cells in that row:

| Column | What to enter |
|---|---|
| **email** | The TikTok account email address |
| **proxy** | Your proxy string, e.g. `socks5://user:pass@host:port` |
| **pic_url** | A direct image link for the profile picture (square, publicly accessible) |
| **max_warm** | Target warm-up duration in seconds — `500` is a good starting point (~8 min) |

Each cell saves automatically when you click away — no Save button needed. Repeat for each account.

---

### Step 2 — Create Profiles in GeeLark

Once your accounts are listed, click **▶ Create Profiles**. This sends the accounts to GeeLark, creating a cloud phone for each one and auto-installing TikTok.

Wait for the progress indicator to complete.

---

### Step 3 — Log in to TikTok manually

This is the one step you must do yourself — the system cannot log in to TikTok on your behalf.

1. Open the **GeeLark** desktop app
2. Find each phone by name and click **Start**
3. Inside the phone, open TikTok and log in with the account email and password
4. Complete any verification (email code, captcha, etc.)
5. Once you can see the TikTok home feed, you're done — close the phone in GeeLark
6. Back in the dashboard's **Accounts** tab, tick the **Logged in** checkbox next to that account

> Warmup and posting skip any account where Logged in is not ticked.

---

## Warmup — Fully Automatic

You don't need to click a button to run warmup. The scheduler runs warmup sessions automatically at the times configured in **Settings → Warmup times** (default: 09:00, 14:00, 20:00 daily).

Each session runs a short automated browsing session per phone. Once an account has accumulated enough warm time (its Max Warm Time), it is marked warmed up and excluded from future sessions.

You can watch warmup runs appear in the **Dashboard** tab under recent activity.

If you need to trigger warmup outside the schedule (e.g. for testing), contact your tech team — it can be triggered via the API.

---

## Posting Videos

### Upload and publish

1. Go to the **Videos** tab
2. Drag and drop your video files onto the upload area (or click to browse)
3. Pick which **platforms** the video should go to (TikTok / YouTube / Instagram / Facebook — one or more)
4. Click **▶ Publish** — the system automatically assigns each video to the best available warmed-up account in the right group, respecting the 2-hour cooldown (no account gets two posts within 2 hours of each other)

You don't need to pick which account gets each video — the system chooses the one that has gone the longest without a post. If you picked multiple platforms, one publication row is created per platform and you'll see a status badge per platform on each video.

The caption is generated automatically by Gemini using the **Caption prompt** bound to the video's group. Groups without a caption prompt are skipped until you assign one in Group settings.

### Status auto-refreshes

You don't need to click "Check Status" — the background worker polls GeeLark and updates each publication's status from **INITIATED** to **SUCCESS** or **FAILURE** automatically.

| Status | Meaning |
|---|---|
| PENDING | Video uploaded but not yet published |
| INITIATED | Post task submitted to GeeLark, waiting for result |
| SUCCESS | Video posted successfully |
| FAILURE | Something went wrong — check the notes column for details |

---

## Settings

Open the **Settings** tab to adjust:

| Section | Setting | What it controls |
|---|---|---|
| Scheduler | Warmup times | Comma-separated HH:MM values for daily warmup schedule, e.g. `09:00,14:00,20:00` |
| Scheduler | Post cooldown hours | Minimum hours between posts to the same account (default 2) |
| Scheduler | Default group | GeeLark group name for new profiles |
| Scheduler | Default OS | Android version for new profiles |
| Scheduler | Default max warm time | Default warm-up target in seconds for new accounts |
| Caption prompts | Prompt library | Reusable prompt bodies. Each group must be assigned one caption prompt before it can publish. |
| Caption | Max length | Hard cap on caption length including hashtags. Default 220. |
| Caption | High-quality mode | Off by default. Turn on to enable Gemini "thinking" — slower and a bit pricier, but useful when the caption quality looks unstable. |
| Secrets | GeeLark / Gemini / Dashboard | Editable from the dashboard so you don't have to SSH in to rotate tokens. Values write back to `.env` and hot-reload into the running process. |

Click **Save** after making changes.

> **Per-group caption prompt**: if you want a specific group of accounts to use a different voice (e.g. a Tokyo Travel group vs. IELTS), open Accounts → click **⋯** next to the group name → **Group settings** → choose a **Caption prompt**. Leaving it blank blocks publishing for that group.

---

## Emergency Stop

If something goes wrong and phones are left running — **act fast, phones bill per minute**.

1. Go to the **Dashboard** tab
2. Click **🛑 Stop All Phones**

This immediately sends a stop command to every running phone in your GeeLark account.

---

## Tips

- **Warmup happens automatically** — you don't need to come back every morning and click a button.
- **Status updates are automatic** — after you publish, just leave the Videos tab open and the statuses will update on their own (per platform if you published to more than one).
- **The 2-hour cooldown is automatic** — the system will never post twice to the same account within 2 hours, even if you click Publish repeatedly.
- **If a publication shows FAILURE**, look at the notes for that row — it will say what went wrong. Contact your tech team if you need a post retried.
- **Caption prompts are required per group.** If publishing does not start, check that the video's group has a caption prompt assigned.

---

## Need Help?

Take a screenshot of the dashboard and the relevant tab (Accounts or Videos), note any error messages you see, and send it to your tech team. The more detail you include, the faster the issue can be resolved.
