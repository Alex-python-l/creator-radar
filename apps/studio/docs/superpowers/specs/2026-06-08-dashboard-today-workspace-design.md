# Dashboard Redesign — Today Workspace

## Overview

Rebuild the `/` (Dashboard) page from the ground up. The current page is a 5-stat-card row + a "Recent Activity" job-runs list + a "Stop All Phones" red button. The 5 stats are all derivable from the Accounts page, "Recent Activity" is audit data that violates the project's minimal-UI-density convention, and the operator has never used "Stop All Phones".

This redesign:

- Removes the "Stop All Phones" button and all backing code (frontend + API route + service function).
- Replaces stats + audit log with a **Today Workspace**: three glance-stat cards (today's publishing progress / account readiness / video library) plus one **Exceptions** list card surfacing the things the operator must act on today.
- All "today" boundaries are computed in `Asia/Shanghai` local time (the operator's timezone), not UTC.

## Goals

- One screen the operator can scan in five seconds: "what got posted today, what's blocked, am I about to run out of accounts or videos".
- Surface *only* things that need attention. No audit data, no derived counts that already exist elsewhere.
- Each stat card is clickable and routes to its source page (Videos / Accounts) so the operator can drill in. v1 ships without query-param pre-filtering — the destination page's existing filters are enough.
- The Exceptions list is the single inbox of "what blocks publishing today" — failed publications, dead proxies, not-logged-in phones, stuck warmups.

## Non-Goals

- No "Stop All Phones" button. The button, the `POST /api/phones/stop-all` endpoint, the `stop_all_phones` service function, and the i18n strings around them are deleted. `client.stop_phones()` itself stays — it is still used by `warmup_service`, `engagement_service`, and `geelark_service.delete_phones`.
- No `app/locks.py` removal. `OPERATION_LOCK` is still used by `accounts.py`, `videos.py`, and `sse.py`.
- No deep-link query parameters added to Videos / Accounts in this pass. Stat cards route to the page root.
- No per-platform breakdown on the "today" card. Only totals (one number + caption).
- No retention / archival policy for old FAILUREs. They naturally sink in the list because exceptions are sorted by `last_checked_at DESC`.
- No new manual "retry" or "stop phone" actions on the Exceptions list. Each row routes to the page where existing controls live.
- No "warmup stuck" threshold setting. The 24-hour threshold is hard-coded for v1; promote to a setting if the operator asks.
- No new frontend tests. The repo has no frontend test baseline; the page is verified via the existing dev-server preview workflow.

## Page Structure

```
┌─ Sidebar (existing Shell) ─┬─ Main ────────────────────────────────────────────┐
│                            │  今日工作台                                          │
│                            │  2026-06-08 · Asia/Shanghai                         │
│                            │                                                    │
│                            │  ┌── 今日发布 ──┐  ┌── 账号健康 ──┐  ┌── 素材库 ──┐  │
│                            │  │     12      │  │   32 / 50    │  │     24    │  │
│                            │  │ 已发成功     │  │ 已准备好      │  │ 未排期视频 │  │
│                            │  │ 失败 1·进行 3│  │ 养号 8·未启 10│  │ 共 100    │  │
│                            │  └──────────────┘  └──────────────┘  └────────────┘  │
│                            │                                                    │
│                            │  异常需处理 (8)                       [查看全部 →]   │
│                            │  ┌────────────────────────────────────────────────┐ │
│                            │  │ 🔴 发布失败 · tt_xyz · vid_42 · TikTok          │ │
│                            │  │    captcha required                  [去查看 →] │ │
│                            │  ├────────────────────────────────────────────────┤ │
│                            │  │ 🟠 代理挂掉 · tt_abc · 50h 前掉线               │ │
│                            │  │                                       [去查看 →]│ │
│                            │  ├────────────────────────────────────────────────┤ │
│                            │  │ 🟠 未登录   · tt_def · TikTok                   │ │
│                            │  │                                       [去查看 →]│ │
│                            │  ├────────────────────────────────────────────────┤ │
│                            │  │ 🟡 养号卡住 · tt_ghi · 36h 没新进度             │ │
│                            │  │                                       [去查看 →]│ │
│                            │  └────────────────────────────────────────────────┘ │
└────────────────────────────┴────────────────────────────────────────────────────┘
```

When there are zero exceptions, the Exceptions card renders an `EmptyState`: title "All quiet", body "系统目前一切正常". The header still shows `异常需处理 (0)` so the count is unambiguous.

### Header

- `PageTitle` with title `今日工作台` and subtitle `YYYY-MM-DD · Asia/Shanghai`.
- No right-side action button. The Stop-All button slot is gone.

### Stat row

- Three `StatCard` instances laid out horizontally with the same flex-wrap behavior as the current Dashboard.
- Each card is wrapped in a clickable anchor that calls the React Router `navigate(...)`:
  - 今日发布 → `/videos`
  - 账号健康 → `/accounts`
  - 素材库 → `/videos`
- The existing `StatCard` primitive does not have a built-in click affordance. Wrap it in a `button` with `cursor-pointer` and a hover ring, or add an optional `onClick` prop. Pick the lowest-touch option that matches the rest of the codebase (see Implementation Notes).

| Card | Big number | Caption template |
|---|---|---|
| 今日发布 | `today.done` | `失败 {today.failed} · 进行中 {today.in_progress}` |
| 账号健康 | `accounts.ready / accounts.total` (rendered "32 / 50") | `养号 {accounts.warming} · 未启 {accounts.not_started}` |
| 素材库 | `videos.unscheduled` | `共 {videos.total}` |

The 今日发布 caption hides the trailing segments when both counts are 0: caption becomes `今日无失败 · 队列空闲`. This keeps the card visually quiet on a smooth day.

### Exceptions card

- Header line: `异常需处理 (N)` left, `[查看全部 →]` right when `exceptions_total > exceptions.length`.
- Body: at most 5 rows, separated by `divide-y divide-[var(--color-line)]` matching the current Dashboard's job list.
- Each row:
  - Severity dot (red / orange / yellow) — uses the `Badge` primitive or a small custom dot, whichever is closer to the comments-page style.
  - Type label (Chinese, from i18n).
  - Account `profile_name` (or `—` if NULL).
  - Optional context: video name (publication_failed), hours since (proxy_down / warmup_stuck), platform (publication_failed / not_logged_in).
  - Reason line below the meta line for `publication_failed` (the `notes` column truncated).
  - Right-aligned `[去查看 →]` link.
- `[查看全部 →]` and per-row `[去查看 →]` both route to:
  - `publication_failed` → `/videos`
  - `proxy_down` → `/accounts`
  - `not_logged_in` → `/accounts`
  - `warmup_stuck` → `/accounts`

### Polling

- Auto-refresh every 15 s, same `setInterval` shape as the current Dashboard's `load()` loop.
- On fetch error, the page silently keeps the last good state (existing `catch(() => {})` pattern). No toast — a toast every 15 s would spam.

## Data Contract

### `GET /api/dashboard` response

```json
{
  "today": {
    "done": 12,
    "failed": 1,
    "in_progress": 3
  },
  "exceptions": [
    {
      "kind": "publication_failed",
      "severity": "red",
      "publication_id": 42,
      "account": "tt_xyz",
      "account_id": 7,
      "video": "vid_42.mp4",
      "platform": "tiktok",
      "reason": "captcha required"
    },
    {
      "kind": "proxy_down",
      "severity": "orange",
      "account_id": 8,
      "account": "tt_abc",
      "hours_since": 50
    },
    {
      "kind": "not_logged_in",
      "severity": "orange",
      "account_id": 9,
      "account": "tt_def",
      "platform": "tiktok"
    },
    {
      "kind": "warmup_stuck",
      "severity": "yellow",
      "account_id": 10,
      "account": "tt_ghi",
      "hours_since": 36
    }
  ],
  "exceptions_total": 8,
  "accounts": {
    "total": 50,
    "ready": 32,
    "warming": 8,
    "not_started": 10
  },
  "videos": {
    "unscheduled": 24,
    "total": 100
  }
}
```

### Today boundaries

Python-side computation (in `app/api/dashboard.py`):

```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

def _today_window_utc() -> tuple[datetime, datetime]:
    now_local = datetime.now(TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc
```

Both returned values are naïve `datetime` in UTC, matching the project's `TIMESTAMP WITHOUT TIME ZONE` convention.

### Counts

- `today.done` = `SELECT COUNT(*) FROM publications WHERE result='SUCCESS' AND posted_at >= :start AND posted_at < :end`
- `today.failed` = `SELECT COUNT(*) FROM publications WHERE result='FAILURE' AND last_checked_at >= :start AND last_checked_at < :end`
- `today.in_progress` = `SELECT COUNT(*) FROM publications WHERE result IN ('QUEUED','INITIATED')` (no date predicate)
- `accounts.total` = `SELECT COUNT(*) FROM accounts`
- `accounts.ready` = `SELECT COUNT(*) FROM accounts WHERE warmed_up = 1`
- `accounts.warming` = `SELECT COUNT(*) FROM accounts WHERE warmed_up = 0 AND warmup_count > 0`
- `accounts.not_started` = `accounts.total - accounts.ready - accounts.warming`
- `videos.unscheduled` = `SELECT COUNT(*) FROM videos v WHERE NOT EXISTS (SELECT 1 FROM publications p WHERE p.video_id = v.id)`
- `videos.total` = `SELECT COUNT(*) FROM videos`

### Exceptions

Each kind runs two queries: a `COUNT(*)` over the full predicate (contributes to `exceptions_total`) and a `SELECT … ORDER BY <kind_timestamp> DESC LIMIT 5` returning candidate rows. The endpoint then merges all four kinds' candidates, sorts by severity (`red > orange > yellow`) then by the kind's order timestamp `DESC`, and slices to the first 5 for the `exceptions` array.

| `kind` | Severity | SQL predicate | Order timestamp |
|---|---|---|---|
| `publication_failed` | red | `publications.result = 'FAILURE'` | `last_checked_at` |
| `proxy_down` | orange | `accounts.proxy_status = 'fail' AND proxy_checked_at IS NOT NULL` | `proxy_checked_at` |
| `not_logged_in` | orange | `accounts.geelark_phone_id IS NOT NULL AND (tiktok_logged_in = 0)` — see "Platform scope" below | `updated_at` |
| `warmup_stuck` | yellow | `accounts.warmed_up = 0 AND warmup_count < target_warmup_count AND last_warm_at IS NOT NULL AND last_warm_at < now() - interval '24 hours'` | `last_warm_at` |

**Platform scope for `not_logged_in`.** v1 only flags `tiktok_logged_in = 0` (TikTok is the primary platform across all groups today). Multi-platform expansion is a follow-up: it requires either reading each account's group's enabled platforms or storing platforms on the account. Document this as a known limitation in the spec, not a backlog item — the operator can ask once they need it.

**Proxy_status concrete value.** `proxy_check_service.py` writes `'ok'` or `'fail'` only. NULL means the account has never been checked and is excluded from this exception (no signal yet).

**Exclude already-removed accounts.** All three account-based exception queries should `JOIN accounts ON …` and skip rows where the account row is missing (foreign-key `SET NULL` on publications.account_id). For `publication_failed`, render `account` as `—` when null rather than dropping the row.

### Index considerations

v1 adds no indexes. `publications.posted_at` and `publications.last_checked_at` have no current index, but the existing composite `idx_publications_status (result, platform)` plus the small table size at present (thousands of rows) makes a sequential scan acceptable. Revisit only if the dashboard endpoint shows up in slow-query logs after deployment.

## Architecture

### Backend

- `app/api/phones.py` — **delete the file**.
- `app/main.py` — remove the `phones.router` import and `app.include_router(phones.router, ...)` registration. No other references.
- `app/services/geelark_service.py` — delete `stop_all_phones(session, client)`. No other internal callers.
- `app/api/dashboard.py` — **rewrite**. New shape:
  - One `_today_window_utc()` helper.
  - One function per region (`_today_counts`, `_account_counts`, `_video_counts`, `_exceptions`). Each wrapped in `try / except` that logs and returns the region's zero/empty default so a partial failure never returns 500.
  - The `/api/dashboard` handler composes them into the JSON shape above.
- No new repositories. SQL lives inline in `app/api/dashboard.py` via `session.execute(sa.text(...))`, matching the current dashboard pattern.

### Frontend

- `frontend/src/pages/Dashboard.tsx` — **rewrite**.
- `frontend/src/api.ts`:
  - Delete `stopAll`.
  - Update the `dashboard()` return type to the new shape.
- `frontend/src/components/ui.tsx` — `StatCard` may need an optional `onClick` prop for the clickable cards. Prefer adding the prop to extending the call site, so all consumers benefit. The non-click behavior must not change.
- New small component(s) inside `Dashboard.tsx`:
  - `ExceptionRow` — renders one exception row by `kind`.
  - `SeverityDot` — small colored circle (12px) used as the leading glyph.
  - Both stay inside `Dashboard.tsx` unless they grow past 50 LOC, in which case extract to `frontend/src/components/dashboard/`.
- i18n changes in `frontend/src/i18n/locales/{en,zh}.json`:
  - **Delete**: `dashboard.stopAll`, `dashboard.toast.stop*`, `dashboard.stopModal.*`, `dashboard.recentActivity`, `dashboard.schedulerIdleTitle`, `dashboard.schedulerIdleBody`, `dashboard.jobLabel.*`, `dashboard.jobStatus.*`, `dashboard.stat.loggedIn*`, `dashboard.stat.loggedInWarmedCaption`, `dashboard.stat.pctOfTotal`, `dashboard.stat.warmed`, `dashboard.stat.pctReadyToPost`, `dashboard.stat.pending`, `dashboard.stat.postedToday`, `dashboard.stat.utcDay`, `dashboard.stat.waitingInQueue`, `dashboard.stat.queueEmpty`.
  - **Keep**: `dashboard.title` (rewrite copy to `今日工作台` / `Today`).
  - **Rewrite**: `dashboard.subtitle` to template `"{{date}} · Asia/Shanghai"` (positional `{{date}}` placeholder).
  - **Add**:
    - `dashboard.stat.todayDone`: `"已发成功"` / `"Posted today"`.
    - `dashboard.stat.todayCaption`: `"失败 {{failed}} · 进行中 {{in_progress}}"` / `"Failed {{failed}} · In progress {{in_progress}}"`.
    - `dashboard.stat.todayCaptionEmpty`: `"今日无失败 · 队列空闲"` / `"No failures today · Queue idle"`.
    - `dashboard.stat.accountsReady`: `"已准备好"` / `"Ready to post"`.
    - `dashboard.stat.accountsCaption`: `"养号 {{warming}} · 未启 {{not_started}}"` / `"Warming {{warming}} · Not started {{not_started}}"`.
    - `dashboard.stat.videosUnscheduled`: `"未排期视频"` / `"Unscheduled videos"`.
    - `dashboard.stat.videosCaption`: `"共 {{total}}"` / `"{{total}} total"`.
    - `dashboard.exceptions.title`: `"异常需处理"` / `"Needs attention"`.
    - `dashboard.exceptions.viewAll`: `"查看全部 →"` / `"View all →"`.
    - `dashboard.exceptions.allQuietTitle`: `"All quiet"` (same in both locales).
    - `dashboard.exceptions.allQuietBody`: `"系统目前一切正常"` / `"Everything is running smoothly"`.
    - `dashboard.exception.kind.publicationFailed`: `"发布失败"` / `"Publish failed"`.
    - `dashboard.exception.kind.proxyDown`: `"代理挂掉"` / `"Proxy down"`.
    - `dashboard.exception.kind.notLoggedIn`: `"未登录"` / `"Not logged in"`.
    - `dashboard.exception.kind.warmupStuck`: `"养号卡住"` / `"Warmup stuck"`.
    - `dashboard.exception.action.go`: `"去查看 →"` / `"Open →"`.
    - `dashboard.exception.proxyDownContext`: `"{{hours}}h 前掉线"` / `"{{hours}}h since last check"`.
    - `dashboard.exception.warmupStuckContext`: `"{{hours}}h 没新进度"` / `"{{hours}}h since last progress"`.

## Error Handling

- **Endpoint partial failure**: each region wrapped in `try / except Exception`, logs via `logging.exception(...)`, returns its zero/empty default. The HTTP response is always 200 with a complete shape.
- **Frontend fetch failure**: existing silent-fail pattern preserved. The page keeps showing the last successful payload until the next 15 s poll succeeds.
- **Missing account on exception row**: render `account` field as `"—"`, do not drop the row. `account_id` is still emitted so the "去查看" link can fall back to the page root.
- **Empty data**: All-zero stats render normally (`0` is a valid count). Empty exceptions list renders the `EmptyState`.

## Testing

### Backend (pytest)

New: `tests/test_api_dashboard.py` — replaces any existing dashboard test referencing the old shape.

Test cases:
- `test_today_done_counts_only_local_today`: SUCCESS with `posted_at` 1h before local-today-start → excluded; SUCCESS at local-today-start exactly → included; SUCCESS 23:59 local → included; SUCCESS at local-tomorrow-start → excluded.
- `test_today_failed_uses_last_checked_at`: failure created yesterday but `last_checked_at` is today → included; failure last checked yesterday → excluded.
- `test_today_in_progress_no_date_filter`: QUEUED rows from any date counted; PENDING rows excluded.
- `test_account_counts_partition`: 3 ready + 2 warming + 5 not-started → ready=3, warming=2, not_started=5, total=10.
- `test_video_counts_unscheduled`: video with at least one publication → not unscheduled; video with zero publications → unscheduled.
- `test_exception_publication_failed`: FAILURE with notes "captcha" → row emitted with `reason="captcha"` and `severity="red"`.
- `test_exception_proxy_down`: account with `proxy_status='down'` and recent `proxy_checked_at` → row emitted; account with `proxy_status=NULL` → not emitted.
- `test_exception_not_logged_in`: account with `geelark_phone_id` set and `tiktok_logged_in=0` → row emitted; account with no phone id → not emitted.
- `test_exception_warmup_stuck`: account with `warmed_up=0`, `warmup_count < target`, `last_warm_at = 25h ago` → row emitted; `last_warm_at = 23h ago` → not emitted.
- `test_exception_severity_order`: mix of all four kinds → list sorted red → orange → yellow.
- `test_exception_cap_5`: 10 of one kind → response slice is 5, `exceptions_total = 10`.
- `test_endpoint_partial_failure`: monkeypatch one region's query to raise → response is 200, that region is default-zero, other regions correct. (Use `monkeypatch` on the internal helper, not the route.)
- `test_phones_stop_all_removed`: `POST /api/phones/stop-all` returns 404 (router gone).

Also update / delete:
- Any existing `tests/test_api_dashboard*.py` against the old shape → delete.
- Search `tests/` for `stop_all_phones` / `/phones/stop-all` references and update or delete.

### Frontend

No new tests (project has no frontend test baseline). Verify via dev-server preview:
- Load `/`, confirm three stat cards render with caption.
- Each stat card is clickable and routes to the right page.
- Force an exception (e.g. mark one publication FAILURE in DB) → row appears with correct severity dot.
- Empty Exceptions card renders "All quiet".

## Implementation Notes

- **Order of work**: backend first (endpoint + tests), then frontend (rewrite + i18n). Last step deletes `app/api/phones.py`, registration, and `stop_all_phones`, after a final grep confirms no stragglers.
- **Migration files**: none. No schema changes.
- **`StatCard` onClick**: `StatCard` in `frontend/src/components/ui.tsx` currently takes `{label, value, caption, tone}` only. Add an optional `onClick?: () => void` prop. When present, the card renders with `cursor-pointer` and a subtle hover ring (match `ListRow` or `Card` hover patterns already in `ui.tsx`); when absent, the card behaves exactly as today, so existing consumers (none today besides Dashboard, but the primitive is general) are unaffected.
- **Severity color tokens**: red / orange / yellow should map to existing CSS variables (`--color-red-*`, etc.) consistent with the `Badge` tone palette used in `Dashboard.tsx`'s existing job-status badges. Pick the closest tone variants.
- **Auth**: the dashboard route is already behind `require_auth` via `main.py`. No new auth wiring needed.
- **No SSE**: the page is plain polling. No need to touch `app/sse.py`.

## Deployment

After local verification:
1. `uv run pytest -q` → ≥ 385 pass (baseline + new dashboard tests).
2. `cd frontend && npm run build` clean.
3. Manual smoke in dev preview.
4. Follow `docs/production-deploy.md` to ship to aliyun. No schema migration to run on the server. Restart `mkt` and `mkt-worker` services (deploy doc covers `mkt`; restart `mkt-worker` separately because the tarball replaces shared `app/` code).

No production data backup needed — this change is read-only at the DB layer.
