# Comments Page Redesign

## Overview

Rebuild the `/comments` page from the ground up. The current page is a 644-line form-on-top, fat-record-cards-below layout that surfaces ~15 audit fields per run (task IDs, all four timestamps, cost, screenshot path, notes, etc.). The operator only scans for "what did the AI write, on which phone, against which video, did it land". Everything else is debug data that should not live in the daily view.

This redesign:

- Treats the AI-written comment **as the page content** (not metadata).
- Moves the submit form into a custom centered modal (not a side drawer, not the native `<dialog>`).
- Replaces "pick a group → use all eligible phones in it" with explicit per-phone multi-select across all groups.
- Drops the per-task UI: every run is its own row. There is no UI concept of "engagement task" — the backing `engagement_tasks` row stays for accounting, but is invisible.
- Drops `comment_mode` (always Studio AI / Gemini) and `comment_probability` (always 100) from the UI surface. Backend defaults handle the columns.
- Adds explicit `comments_per_video` to the submit payload and to a new `engagement_tasks.comments_per_video` column.

## Goals

- Each record card shows exactly 6 fields: status, comment text, phone name, video link, video title (if owned), timestamp. Nothing else on the main view.
- `+ 新建评论` button (top-right) opens a centered modal with: phone multi-select, target video segmented control + list / textarea, `每个视频评论数`, optional product brief, optional comment brief.
- Phones can be reused within a batch (same phone can comment on multiple videos in one submit).
- The same dispatch path used today (Procrastinate `phones` queue, `submit_engagement_run_task`, `check_publication_task`-style polling) keeps working unchanged — only the *selection* logic at submit time changes.
- Page is still single-process compatible (no new threading concerns; same SSE/polling pattern as today).

## Non-Goals

- No GeeLark AI mode picker in the UI. The column stays, the value is always `"studio_ai"`.
- No probability picker in the UI. The column stays, the value is always `100`.
- No drill-down "task" page that aggregates runs. Each run is its own row in the list; `engagement_task_id` is not surfaced.
- No new filters beyond status and source. Group filter is removed from the list (no longer the primary axis).
- No screenshot inline rendering — failed runs reference the error code only; the screenshot is reachable from a small action link in the row (see "Record card details" below).
- No bulk re-run / cancel buttons in this pass.

## Page Structure

```
┌─ Sidebar (existing Shell) ─┬─ Main ────────────────────────────────────────┐
│                            │  TikTok 评论                  [+ 新建评论]      │
│                            │                                                │
│                            │  [状态 ▾]  [来源 ▾]      ● 5 秒自动刷新         │
│                            │                                                │
│                            │  ┌── card ──────────────────────────────────┐  │
│                            │  │ "Honestly Cathoven nailed it…"           │  │
│                            │  │ ──────                                   │  │
│                            │  │ ● 已发布 · tt_cathoven_07 · 2 分钟前       │  │
│                            │  │ Cathoven 完爆 Grammarly  tiktok.com/… ↗  │  │
│                            │  └──────────────────────────────────────────┘  │
│                            │  …more cards…                                  │
└────────────────────────────┴────────────────────────────────────────────────┘
```

### Header

- No subtitle. Title `TikTok 评论` only. Primary button `+ 新建评论` aligned right.

### Filter row

- `[状态 ▾]` — multi-select pill: `全部 / 已发布 / 运行中 / 排队 / 失败`. Frontend-only filter over the loaded `EngagementRun[]`.
- `[来源 ▾]` — `全部 / 自有视频 / 手动链接`. Frontend-only filter over `run.task_source`.
- Right-aligned `● 5 秒自动刷新` live indicator. Polling stays on the existing inflight-detection logic (`isInflight` in `Comments.tsx`).
- The current "group" select disappears here. Selecting a group is no longer a primary navigation axis.

### Record card

The hero is the AI-written comment text (`run.comment_text`). Below it:

| Position | Element | Source |
|---|---|---|
| top-left status accent strip (3px) | colored by result | `run.result` + `run.task_status` |
| hero text | comment in serif font, ~18px | `run.comment_text` |
| hairline divider |  | — |
| meta line | `● <status> · <phone> · <relative time>` | `run.result`, `run.account_profile_name`, `run.created_at` |
| video line | `<title>?  <link>  ↗` | `run.target_title` (only if owned), `run.target_link` |

States:

- **SUCCESS** → green accent strip, green dot, label `已发布`.
- **INITIATED / QUEUED / PENDING** with `task_status` running → blue accent, pulsing blue dot, label `GeeLark 运行中` (running) / `排队中` (queued).
- **FAILURE** → red accent, red dot, label `失败`. Hero text is replaced by italic muted text: `<人话错误> · <error_code>` derived from `run.notes`. No quote marks.
- **SKIPPED** (probability filter) → not shown in default view since prob is always 100; if any exist from legacy data, gray accent, label `已跳过`.

### Record card details (out of scope of main view, but reachable)

The card has no expand button. The audit fields the user does not need on the main view (engagement_task_id, geelark_task_id, scheduled-at, started-at, finished-at, last-checked-at, cost, notes, screenshot) are reachable by clicking the video `↗` icon — this opens the actual TikTok video. For the small fraction of times an operator needs to debug a single failed run, they go through `/dashboard` job runs or read `engagement_runs` directly in PG. The redesigned `/comments` page is for live monitoring, not forensics.

## New Comment Modal

Custom centered modal (not Drawer, not native `<dialog>`):

- Implementation: portal into `<body>`, fixed-position scrim `rgba(0,0,0,0.34) backdrop-blur(4px)`, centered card 560px wide, max-height 86vh, scrollable body, fixed header & footer.
- Open animation: scrim fade-in 150ms + card translateY(8px) + scale(0.985) → 0 / 1 over 180ms `cubic-bezier(0.18, 0.74, 0.46, 1)`. No bounce.
- ESC closes. Click on scrim closes. Click inside card does not close.
- Focus management: on open, focus first input. On close, restore focus to `+ 新建评论` button.

### Modal body

| Field | Type | Notes |
|---|---|---|
| 可用手机 | scrollable multi-select list, max-height 200px | Lists every account in the system where `tiktok_logged_in == 1 AND warmed_up == 1 AND geelark_phone_id != ''`. Each row: checkbox, profile_name, `<group_name> · 已养号`, status (空闲 / 忙碌 — `忙碌` if the phone currently has an inflight engagement run). `全选 · 清空` shortcuts above the list. `已选 X / Y 台` counter below. |
| 目标视频 | segmented control `自有已发布 / 手动链接` + payload area | `自有已发布`: scrollable checkbox list of `CommentTarget[]` from `/api/engagement/targets` (no group_id filter — all owned targets). `手动链接`: textarea, one URL per line. |
| 每个视频评论数 | number input, default 3, min 1, max 20 | New field. Sent as `comments_per_video`. |
| 产品覆盖 (可选) | textarea | Same semantics as today's `product_info`. Empty = use built-in Cathoven brief. |
| 评论要求 (可选) | textarea | Same semantics as today's `comment_brief`. |

### Modal footer

Left side: live estimate text — `预计派发 N 条评论（M 视频 × K 条，从已选 X 台手机里随机分配）`, where `N = M * K`.

Right side: `取消` (secondary) + `开始评论` (primary).

`开始评论` is enabled when: at least 1 phone selected AND at least 1 target selected AND `comments_per_video >= 1`.

If `comments_per_video > phones_selected`, the button stays enabled (phones can be reused — see backend section), but a `field-help` note appears under the number input: `手机数小于本次评论数 — 同一台手机会被多次派单`.

## Backend Changes

### Schema

New column on `engagement_tasks`:

```python
comments_per_video: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default="1"))
```

Alembic migration adds the column with default `1` so existing rows backfill. `comment_mode` and `comment_probability` columns stay (UI sends fixed values, runtime keeps existing behavior).

### `POST /api/engagement/comment`

New payload shape (only the changed/new fields shown):

```json
{
  "account_ids": [12, 17, 21],
  "target_links": ["…"] | [],
  "target_publication_ids": [421, 422] | [],
  "comments_per_video": 3,
  "product_info": null,
  "comment_brief": null
}
```

Removed from UI payload (but still accepted server-side for legacy clients — defaulted):

- `group_id` → no longer sent. If absent, backend uses the group_id of the *first selected account* as the engagement_task's group (purely for reporting). If accounts span multiple groups, `group_id` is left `null`.
- `comment_probability` → defaults to `100` on the server.
- `comment_mode` → defaults to `"studio_ai"` on the server.

### `engagement_service.create_comment_batch` dispatch logic

Replace today's "build pool from group + eligibility filter" with: take `account_ids` verbatim and validate each (logged in + warmed up + has phone id). On invalid account → `ValueError`.

Run generation:

```python
runs = []
for target in resolved_targets:           # one entry per chosen video / link
    for k in range(comments_per_video):
        account = random.choice(account_ids)   # allow repeats
        runs.append(_build_run(target, account))
```

Same-account-on-same-video duplicates are allowed. The existing 2-hour cooldown in `assignment.build_pool` is NOT applied here — the operator explicitly chose the phones and the volume.

Failure modes:

- `account_ids` empty → 400 `"select at least one phone"`.
- `comments_per_video < 1 or > 20` → 400.
- Both target lists empty → 400 `"select at least one target"`.

### `GET /api/engagement/runs`

No backend change required. `group_id` is already optional today; when the frontend omits it, the endpoint already returns the most recent 100 runs across all groups ordered by `created_at DESC`. Status / source filters are applied client-side in v1.

### Eligible phones endpoint

New endpoint: `GET /api/engagement/eligible-phones` → returns:

```json
{
  "items": [
    { "id": 12, "profile_name": "tt_cathoven_03", "group_name": "Cathoven IELTS", "busy": false },
    …
  ]
}
```

`busy=true` when this account has at least one `EngagementRun` with `result in (PENDING, QUEUED, INITIATED)`. Used by the modal to show 空闲/忙碌. Sorted by group_name then profile_name.

## Frontend Implementation Notes

- `frontend/src/pages/Comments.tsx` is rewritten in place. Total target: ≤ 350 lines (current is 644).
- Reuse existing UI primitives: `Button`, `Card`, `EmptyState`, `PageTitle`, `Toast`. **Do NOT use the existing `Drawer` primitive** — build a thin `CommentModal` component inline (or extracted to `frontend/src/components/CommentModal.tsx` if it exceeds ~120 lines).
- Use the existing `Dot` component for status indicators where it fits; the spinning dot animation needs a small addition (`Dot` doesn't animate today — add an optional `animated` prop, or inline the CSS in the page).
- Comment hero text uses a serif stack: `'Iowan Old Style', 'Charter', 'Source Serif Pro', Georgia, serif`. Add this as `--font-serif` to `frontend/src/index.css`.
- i18n: drop the following keys from `frontend/src/i18n/locales/{en,zh}.json` after the rewrite — `commenters`, `eligibleCount`, `autoCommenters`, `noEligibleAccounts`, `commentMode`, `studioAiMode`, `geelarkAiMode`, `probability`, `task`, `geelarkTask`, `scheduled`, `started`, `finished`, `lastChecked`, `cost`, `notes`, `screenshot`, `viewScreenshot`, `summary`, `createdAt`, `publicUnverified` (the last one moves into the status label). Add new keys: `newComment`, `availablePhones`, `selectAll`, `clear`, `selected`, `commentsPerVideo`, `commentsPerVideoHelp`, `phoneReuseHint`, `estimate`, `runningState`, `queuedState`, `failedState`.
- Filter row state lives in component-local state; no URL params (consistent with the rest of the app).

## Visual Tokens

No new color tokens. Reuse existing `--color-blue / green / red / orange / ink-{1..5} / surface / line / gray-bg`. Adds one font stack variable.

Status accent strip width: 3px on the left edge of the card.
Card padding: 22px 26px 18px.
Hero text: 18px / 1.45 line-height / -0.005em letter-spacing / serif font stack.
Meta line: 12.5px / sans / `--color-ink-3`.

## Testing

- Pytest: `app/tests/test_engagement_service.py` gains 3 cases for the new dispatch path — (1) phones < videos × K reuses, (2) phones ≥ videos × K uses random sampling without explicit reuse, (3) invalid `account_ids` (not logged in) → 400.
- Frontend: no Vitest suite exists for `Comments.tsx` today; do not add one in this pass. Verify in preview via the Claude Preview workflow.
- Manual: open modal, select phones, select 2 targets, set `每个视频评论数 = 5` (phones=3 → reuse), submit, verify 10 runs are created in PG and queued in Procrastinate.

## Deploy

Tarball deploy per `docs/production-deploy.md`. Run `alembic upgrade head` on the server for the `comments_per_video` column. Restart `mkt` and `mkt-worker`.

No PG backup needed (additive column, default value).

## Rollout

One PR. No feature flag. If the new page breaks, revert the commit — the schema column is additive and stays.
