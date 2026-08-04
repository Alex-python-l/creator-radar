# Warmup Modes: Count vs Daily-Maintenance

**Date:** 2026-06-05
**Status:** Draft → awaiting user approval

## Problem

Current warmup is single-mode: every eligible account is rushed to
`target_warmup_count` successful warmup runs and then flipped to
`warmed_up=1`, after which it enters the publishing / engagement pool.

Some accounts need a different rhythm — they are **not yet ready** for
posting and should keep receiving a few warmup sessions per day for as
many days as the operator wants, until the operator manually says
"ready". The current "burn-down to N" mode cannot express that.

## Goals

- Add a second warmup mode that runs ~4 sessions per day, indefinitely,
  until the operator marks the account ready.
- Keep the existing count-based mode as the default; no surprises for
  existing accounts.
- Reuse the existing `warmed_up` gate — daily-mode accounts stay
  `warmed_up=0` and remain blocked from posting / engagement until the
  operator flips them.
- Keep the data model minimal and the scheduler architecturally similar
  to existing periodic tasks (`pic_check`, `auto_publish_scan`).

## Non-Goals

- Daily-mode accounts publishing while warming.
- Per-account override of the daily session count.
- An automatic stop after N days of daily warmup.
- Multiple distribution strategies (peak-hours, fully random, etc.).
  Only "≈ 6h apart with ±30 min jitter" is implemented.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Mode granularity | Per **account** (new column on `accounts`) |
| Daily-mode + publishing | Daily-mode means "not ready yet"; stays `warmed_up=0`, blocked from posting / engagement |
| Sessions per day | Fixed **4**, stored as `settings.daily_warmup_count="4"` (≈ one every 6h) |
| Time distribution | Even ~6h cadence + 0–30 min one-sided random jitter (delay only) |
| Default for new accounts | `warmup_mode="count"` (status quo) |
| Switching A ↔ B | Bidirectional; `warmup_count` is **not** reset on switch |
| "Stop warming" UX | Reuse existing **Mark warmed up** — it now also flips `warmup_mode` back to `count` and sets `warmed_up=1` in one click |

## Design

### 1. Data Model

`accounts` adds one column:

```
warmup_mode  VARCHAR(16)  NOT NULL  DEFAULT 'count'   -- 'count' | 'daily'
```

`settings` gets one new row (idempotent upsert in migration):

```
key='daily_warmup_count', value='4'
```

`app/config.py::SETTINGS_DEFAULTS` mirrors the default so the fallback
path matches.

Nothing else changes:
`warmup_count` / `target_warmup_count` / `warmed_up` /
`last_warm_at` / `last_warm_result` / `last_warm_notes` are shared
across both modes with identical semantics — only the *interpretation*
differs (see §3).

### 2. Switching Modes

- New endpoint: `POST /api/accounts/warmup-mode`
  body: `{"account_ids": [...], "mode": "count" | "daily"}`
  response: `{"updated": N}`
- Mirrors the shape of `/api/accounts/mark-warmed-up`.
- `mark_warmed_up` (repo) now additionally sets `warmup_mode='count'`
  so "I say it is ready" cleanly exits daily-maintenance mode in one
  user action.
- Switching does not touch `warmup_count`. If a daily-mode account has
  already accumulated `warmup_count >= target_warmup_count`, switching
  it back to `count` does NOT auto-mark it warmed_up — `_warm_one` only
  flips `warmed_up=1` on its **next** successful warmup, after the
  switch.

### 3. `_warm_one` Behaviour Difference

`app/services/warmup_service.py::_warm_one`:

```
# success path (unchanged up to here)
count = (warmup_count or 0) + 1
patch = {"warmup_count": count,
         "last_warm_at": now(),
         "last_warm_result": "Success"}

if warmup_mode == "count" and count >= target_warmup_count:
    patch["warmed_up"] = 1
    reached = 1
# daily mode: never sets warmed_up here; warmup_count is just a diagnostic

acc_repo.update(...)
```

`app/tasks/warmup.py::_should_requeue_after_success`:

```
if row.get("warmup_mode") == "daily":
    return False   # cadence is driven by the scan task, not by self-requeue
# else: existing eligibility logic
```

This means count-mode accounts still rush to target as before, and
daily-mode accounts get exactly one warmup per scan-triggered enqueue.

### 4. New Periodic Task `app/tasks/daily_warmup.py`

```
@procrastinate_app.periodic(cron="*/10 * * * *")
@procrastinate_app.task(name="daily_warmup_scan", queue="default")
async def daily_warmup_scan(timestamp: int) -> str:
    n = int(settings.get("daily_warmup_count") or 4)
    interval_seconds = 86400 // max(n, 1)        # 4 → 21600 (6h)

    # Candidates: daily-mode, logged-in TikTok, has phone,
    # not already marked warmed_up, and either never warmed
    # or warmed long enough ago.
    SELECT id FROM accounts
    WHERE warmup_mode = 'daily'
      AND tiktok_logged_in = 1
      AND warmed_up = 0
      AND geelark_phone_id IS NOT NULL
      AND (last_warm_at IS NULL
           OR last_warm_at < now() - interval_seconds * INTERVAL '1 second')

    for account_id in candidates:
        await warmup_account_task.configure(
            queueing_lock=f"warmup_account_{account_id}",
            schedule_in={"seconds": random.randint(0, 1800)},   # 0..30min jitter, delay only
        ).defer_async(account_id=account_id)

    return f"daily_warmup_scan: queued {len(candidates)} accounts"
```

Notes:

- Uses the existing `warmup_account_task` (which holds `lock="phones"`),
  so phone-mutating work is naturally serialized with publish and
  count-mode warmups.
- `queueing_lock` per account id prevents the next scan from
  double-queuing an account whose previous job hasn't started.
- Jitter is applied via `schedule_in` (not by mutating SQL) so the
  10-min scan grain doesn't leak into the cadence.

### 5. Frontend

`frontend/src/pages/Accounts.tsx`:

- New column **Mode** (i18n key `accounts.col.mode`), rendered as a
  badge: `Count` (neutral tone) / `Daily` (blue tone).
- Progress cell:
  - `mode='count'`: unchanged — progress bar `warmup_count / target_warmup_count`.
  - `mode='daily'`: shows "Daily · {count} runs · next ≈ HH:MM" where
    HH:MM = `last_warm_at + interval_seconds` (computed client-side
    from `daily_warmup_count` returned via existing settings endpoint;
    skip the "next" suffix if `last_warm_at` is null).
- `SelectionToolbar` gains a **Switch mode** action: opens a tiny menu
  (Count / Daily); calling it POSTs to the new endpoint and refreshes.
- The **Mark warmed up** button is unchanged on the frontend; backend
  now also resets `warmup_mode='count'`, so the operator does not see a
  semantic shift — it just continues to mean "this account is ready".
- i18n strings added to both `en` and `zh` locale files.

### 6. Migration

New alembic revision `XXXX_warmup_mode.py`:

```python
def upgrade():
    op.add_column(
        "accounts",
        sa.Column("warmup_mode", sa.String(length=16),
                  nullable=False, server_default="count"),
    )
    op.execute(
        "INSERT INTO settings(key, value) VALUES ('daily_warmup_count', '4') "
        "ON CONFLICT (key) DO NOTHING"
    )

def downgrade():
    op.drop_column("accounts", "warmup_mode")
    op.execute("DELETE FROM settings WHERE key='daily_warmup_count'")
```

### 7. Tests

Additions / changes (≥385 baseline must hold):

- `tests/test_warmup_service.py`
  - new: `mode='daily'` success keeps `warmed_up=0` even when
    `warmup_count >= target_warmup_count`.
  - new: `mode='count'` behaviour unchanged (regression).
- `tests/test_warmup_task.py`
  - `_should_requeue_after_success` returns `False` for mode='daily'.
- `tests/test_daily_warmup_task.py` (new file)
  - SELECT yields the right candidate set under various account states.
  - `defer_async` called once per candidate with the expected
    `queueing_lock` and `schedule_in` shape (assert via mock).
- `tests/test_accounts_repo.py`
  - `mark_warmed_up` flips `warmup_mode` back to `'count'`.
- `tests/test_accounts_api.py`
  - new endpoint validates body shape (mode must be 'count'|'daily'),
    rejects empty `account_ids`, returns `{"updated": N}`.

### 8. Known Constraint (NOT addressed in this spec)

`warmup_account_task` holds `lock="phones"`, so warmup runs are
serialized across the worker. If the daily-mode roster grows large
(e.g. 200 accounts × 4 runs/day = 800 runs/day, ~3 min each ≈ 40h of
work per day), one worker cannot keep up.

This spec intentionally does NOT solve that. Future options, listed
for visibility only:

1. Split into N phones queues (`phones_0…phones_N`) routed by
   `account_id % N`, run N workers with disjoint queue lists.
2. Allow concurrent cloud-phone starts (cost ↑, depends on GeeLark plan).
3. Cap daily-mode roster size with a UI warning.

If pain shows up in production, pick one in a follow-up.

## Rollout

1. Apply alembic migration.
2. Deploy web + worker.
3. Existing accounts stay on `mode='count'` (server_default), so no
   behaviour change for current users until they flip an account to
   `daily`.
4. No data backfill needed.
