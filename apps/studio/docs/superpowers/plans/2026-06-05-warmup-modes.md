# Warmup Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-account `warmup_mode` of `count` (status quo: rush to N then mark warmed_up=1) or `daily` (run ~4 sessions/day forever; operator manually marks ready via existing **Mark warmed up**).

**Architecture:** Add one column `accounts.warmup_mode` and one setting `daily_warmup_count` (default `4`). Branch behaviour in `warmup_service._warm_one` and `tasks/warmup._should_requeue_after_success` so daily-mode runs are never auto-completing and never self-requeue. Drive the cadence with a new `daily_warmup_scan` periodic task (cron `*/10 * * * *`) that selects stale daily-mode accounts and enqueues the existing `warmup_account_task` with a 0–30 min `schedule_in` jitter. Front-end gets a Mode badge column, a Daily progress label, and a Switch mode button in the selection toolbar.

**Tech Stack:** FastAPI + SQLModel + PostgreSQL + Alembic + Procrastinate (worker), React 19 + TypeScript + Vite (frontend), pytest.

**Spec:** `docs/superpowers/specs/2026-06-05-warmup-modes-design.md` (commit `eee0b1e`)

---

## File Map

**Create**

- `alembic/versions/a2b8d4e6f013_account_warmup_mode.py` — add column + seed setting
- `app/tasks/daily_warmup.py` — periodic scan that enqueues warmup runs every ~6h per daily-mode account

**Modify**

- `app/models.py` — add `Account.warmup_mode` field
- `app/repositories/accounts.py` — `_COLUMNS` add `warmup_mode`; `mark_warmed_up` also resets it to `count`
- `app/services/warmup_service.py:42-52` — `_warm_one` only sets `warmed_up=1` when `warmup_mode == 'count'`
- `app/tasks/warmup.py:42-50` — `_should_requeue_after_success` returns `False` for `warmup_mode == 'daily'`
- `app/tasks/procrastinate_app.py` — add `"app.tasks.daily_warmup"` to `import_paths`
- `app/api/accounts.py` — new endpoint `POST /api/accounts/warmup-mode`
- `app/config.py` — `SETTINGS_DEFAULTS` add `"daily_warmup_count": "4"`
- `frontend/src/api.ts` — types: extend Account with `warmup_mode`; add `setWarmupMode` helper
- `frontend/src/pages/Accounts.tsx` — Mode column, Daily progress label, Switch mode toolbar action
- `frontend/src/i18n/locales/en.json` and `zh.json` — new strings

**Test-only files modified**

- `tests/test_services.py` — new daily-mode warmup tests
- `tests/test_tasks_periodic.py` — `_should_requeue_after_success` daily case, `daily_warmup_scan` smoke
- `tests/test_repositories.py` — `mark_warmed_up` resets warmup_mode
- `tests/test_api_accounts_batch.py` — `/api/accounts/warmup-mode` endpoint
- `tests/test_settings_defaults.py` — `daily_warmup_count` is in SETTINGS_DEFAULTS
- `tests/test_tasks_periodic.py` — `daily_warmup_scan` is registered as cron

---

## Conventions

- Every commit uses **explicit paths**, never `git add .` / `-A`. (Repo rule.)
- Run `uv run pytest -q` after each task; baseline ≥ 385 must hold.
- Run `uv run alembic upgrade head` against `mkt_test` after Task 1 so subsequent tests see the new column.
- Frontend changes end with `cd frontend && npm run build` to confirm types/build.
- All user-facing strings are English (this project's convention).

---

## Task 1: Add `warmup_mode` column + `daily_warmup_count` setting

**Files:**
- Create: `alembic/versions/a2b8d4e6f013_account_warmup_mode.py`
- Modify: `app/models.py` — add field after `target_warmup_count`
- Modify: `app/repositories/accounts.py` — add `"warmup_mode"` to `_COLUMNS`
- Modify: `app/config.py` — add to `SETTINGS_DEFAULTS`
- Test: `tests/test_repositories.py`, `tests/test_settings_defaults.py`

- [ ] **Step 1: Write failing repository test for warmup_mode default**

Append to `tests/test_repositories.py` (after the existing warmup_count test at line ~22):

```python
def test_account_warmup_mode_defaults_to_count(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "wm-default"})
    row = acc_repo.get(db, aid)
    assert row["warmup_mode"] == "count"


def test_account_warmup_mode_round_trip(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "wm-rt",
                               "warmup_mode": "daily"})
    assert acc_repo.get(db, aid)["warmup_mode"] == "daily"
    acc_repo.update(db, aid, {"warmup_mode": "count"})
    assert acc_repo.get(db, aid)["warmup_mode"] == "count"
```

- [ ] **Step 2: Write failing settings-defaults test**

Append to `tests/test_settings_defaults.py`:

```python
def test_daily_warmup_count_default_is_4():
    from app.config import SETTINGS_DEFAULTS
    assert SETTINGS_DEFAULTS["daily_warmup_count"] == "4"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_repositories.py::test_account_warmup_mode_defaults_to_count tests/test_repositories.py::test_account_warmup_mode_round_trip tests/test_settings_defaults.py::test_daily_warmup_count_default_is_4 -v`

Expected: FAIL — column does not exist / setting key missing.

- [ ] **Step 4: Create migration file**

Create `alembic/versions/a2b8d4e6f013_account_warmup_mode.py` with this exact content:

```python
"""account warmup_mode column + daily_warmup_count setting

Revision ID: a2b8d4e6f013
Revises: a1b2c3d4e5f6
Create Date: 2026-06-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2b8d4e6f013"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "warmup_mode",
            sa.String(length=16),
            nullable=False,
            server_default="count",
        ),
    )
    op.execute(
        "INSERT INTO settings (key, value) "
        "VALUES ('daily_warmup_count', '4') "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_column("accounts", "warmup_mode")
    op.execute("DELETE FROM settings WHERE key='daily_warmup_count'")
```

- [ ] **Step 5: Add SQLModel field**

In `app/models.py`, inside the `Account` class right after the existing line `target_warmup_count: int = Field(default=10)` (currently line 55), insert:

```python
    warmup_mode: str = Field(default="count", nullable=False, max_length=16)
```

- [ ] **Step 6: Whitelist column in repository**

In `app/repositories/accounts.py`, find the `_COLUMNS = {...}` set (starts at line 9). Add `"warmup_mode"` to the set — easiest spot is at the end of the line that currently reads `"warmed_up", "warmup_count", "target_warmup_count", "last_warm_at",` so it becomes:

```python
    "warmed_up", "warmup_count", "target_warmup_count", "warmup_mode",
    "last_warm_at",
```

- [ ] **Step 7: Seed setting default**

In `app/config.py`, inside `SETTINGS_DEFAULTS` (the dict starting at line 51), add this line right after `"default_target_warmup_count": "10",`:

```python
    "daily_warmup_count": "4",
```

- [ ] **Step 8: Apply migration to test DB**

Run: `DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run alembic upgrade head`

Expected output (last line): `Running upgrade a1b2c3d4e5f6 -> a2b8d4e6f013, account warmup_mode column + daily_warmup_count setting`

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_repositories.py::test_account_warmup_mode_defaults_to_count tests/test_repositories.py::test_account_warmup_mode_round_trip tests/test_settings_defaults.py::test_daily_warmup_count_default_is_4 -v`

Expected: 3 PASSED.

- [ ] **Step 10: Run full test suite (no regressions)**

Run: `uv run pytest -q`

Expected: ≥ 388 passed (baseline 385 + 3 new).

- [ ] **Step 11: Commit**

```bash
git add alembic/versions/a2b8d4e6f013_account_warmup_mode.py \
        app/models.py app/repositories/accounts.py app/config.py \
        tests/test_repositories.py tests/test_settings_defaults.py
git commit -m "feat(warmup): add warmup_mode column + daily_warmup_count setting"
```

---

## Task 2: `_warm_one` skips auto-`warmed_up` for daily mode

**Files:**
- Modify: `app/services/warmup_service.py` — line 48
- Test: `tests/test_services.py`

- [ ] **Step 1: Write failing test — daily mode never auto-flips warmed_up**

Append to `tests/test_services.py` (after `test_warmup_increments_count_and_marks_done` ~line 142):

```python
def test_daily_warmup_keeps_warmed_up_false_even_past_target(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "geelark_phone_id": "p-daily-1",
        "profile_name": "daily-1",
        "tiktok_logged_in": 1,
        "target_warmup_count": 2,
        "warmup_count": 1,
        "warmup_mode": "daily",
    })
    summary = warmup_service.run_warmup(db, FakeWarmClient(),
                                        duration_minutes=2, wait=False)
    a = acc_repo.get(db, aid)
    # +1 to count, but warmed_up stays 0 because mode is daily
    assert a["warmup_count"] == 2
    assert a["target_warmup_count"] == 2
    assert a["warmed_up"] == 0
    assert "1 accounts warmed" in summary or "1 reached target" not in summary


def test_count_warmup_unchanged_after_daily_mode_added(db):
    """Regression: mode='count' still auto-flips warmed_up at target."""
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "geelark_phone_id": "p-count-1",
        "profile_name": "count-1",
        "tiktok_logged_in": 1,
        "target_warmup_count": 2,
        "warmup_count": 1,
        # warmup_mode omitted -> defaults to 'count'
    })
    warmup_service.run_warmup(db, FakeWarmClient(),
                              duration_minutes=2, wait=False)
    a = acc_repo.get(db, aid)
    assert a["warmup_count"] == 2
    assert a["warmed_up"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_services.py::test_daily_warmup_keeps_warmed_up_false_even_past_target -v`

Expected: FAIL — `assert a["warmed_up"] == 0` fails because current code flips it to 1.

- [ ] **Step 3: Branch on mode in `_warm_one`**

In `app/services/warmup_service.py`, replace lines 42–52 (the success branch) with:

```python
    count = int(a.get("warmup_count") or 0) + 1
    target = int(a.get("target_warmup_count") or 10)
    mode = a.get("warmup_mode") or "count"
    patch = {"warmup_count": count,
             "last_warm_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "last_warm_result": "Success"}
    reached = 0
    if mode == "count" and count >= target:
        patch["warmed_up"] = 1
        reached = 1
    acc_repo.update(session, a["id"], patch)
    return reached
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_services.py -k warmup -v`

Expected: All warmup tests pass (new ones + existing).

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`

Expected: ≥ 390 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/warmup_service.py tests/test_services.py
git commit -m "feat(warmup): _warm_one respects warmup_mode (daily never auto-completes)"
```

---

## Task 3: `_should_requeue_after_success` skips daily mode

**Files:**
- Modify: `app/tasks/warmup.py` — function at line 42
- Test: `tests/test_tasks_periodic.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_tasks_periodic.py`:

```python
def test_should_requeue_after_success_returns_false_for_daily_mode(db):
    from app.tasks import warmup as warmup_module
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "geelark_phone_id": "p-daily-requeue",
        "profile_name": "daily-requeue",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_count": 5,
        "target_warmup_count": 10,
        "warmup_mode": "daily",
        "last_warm_result": "Success",
    })
    assert warmup_module._should_requeue_after_success(db, aid) is False


def test_should_requeue_after_success_still_true_for_count_mode(db):
    """Regression: count mode keeps the burn-down behaviour."""
    from app.tasks import warmup as warmup_module
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "geelark_phone_id": "p-count-requeue",
        "profile_name": "count-requeue",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_count": 5,
        "target_warmup_count": 10,
        "last_warm_result": "Success",
    })
    assert warmup_module._should_requeue_after_success(db, aid) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tasks_periodic.py::test_should_requeue_after_success_returns_false_for_daily_mode -v`

Expected: FAIL — current code returns True regardless of mode.

- [ ] **Step 3: Add mode check**

In `app/tasks/warmup.py`, replace the `_should_requeue_after_success` function (lines 42–50) with:

```python
def _should_requeue_after_success(session, account_id: int) -> bool:
    row = acc_repo.get(session, account_id)
    if row is None or row.get("last_warm_result") != "Success":
        return False
    if row.get("warmup_mode") == "daily":
        # Cadence for daily-mode accounts is owned by daily_warmup_scan;
        # never self-requeue here, otherwise we'd run flat-out instead of
        # ~4 times per day.
        return False
    return bool(
        row.get("tiktok_logged_in")
        and not row.get("warmed_up")
        and row.get("geelark_phone_id")
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tasks_periodic.py -k 'should_requeue or warmup_account' -v`

Expected: All pass.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`

Expected: ≥ 392 passed.

- [ ] **Step 6: Commit**

```bash
git add app/tasks/warmup.py tests/test_tasks_periodic.py
git commit -m "feat(warmup): _should_requeue_after_success skips daily mode"
```

---

## Task 4: `mark_warmed_up` resets `warmup_mode` to `count`

**Files:**
- Modify: `app/repositories/accounts.py` — `mark_warmed_up` body, line 210
- Test: `tests/test_repositories.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_repositories.py`:

```python
def test_mark_warmed_up_resets_mode_to_count(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "profile_name": "exit-daily",
        "warmup_mode": "daily",
    })
    acc_repo.mark_warmed_up(db, [aid])
    row = acc_repo.get(db, aid)
    assert row["warmed_up"] == 1
    assert row["warmup_mode"] == "count"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repositories.py::test_mark_warmed_up_resets_mode_to_count -v`

Expected: FAIL — warmup_mode still 'daily'.

- [ ] **Step 3: Update `mark_warmed_up`**

In `app/repositories/accounts.py`, inside `mark_warmed_up` (lines 210–227), add one line inside the per-row loop right after `r.warmed_up = 1`:

```python
        r.warmup_mode = "count"   # exit daily-maintenance mode in one click
```

The full updated loop body becomes:

```python
    for r in rows:
        target = r.target_warmup_count or 10
        r.target_warmup_count = target
        r.warmup_count = max(r.warmup_count or 0, target)
        r.warmed_up = 1
        r.warmup_mode = "count"   # exit daily-maintenance mode in one click
        r.last_warm_at = now
        r.last_warm_result = "Success"
        r.last_warm_notes = "Manually marked warmup complete"
        r.updated_at = now
        session.add(r)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_repositories.py -k mark_warmed -v`

Expected: All pass.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`

Expected: ≥ 393 passed.

- [ ] **Step 6: Commit**

```bash
git add app/repositories/accounts.py tests/test_repositories.py
git commit -m "feat(warmup): mark_warmed_up exits daily mode in one click"
```

---

## Task 5: New `POST /api/accounts/warmup-mode` endpoint

**Files:**
- Modify: `app/api/accounts.py` — add endpoint after `mark_warmed_up` (line 98)
- Test: `tests/test_api_accounts_batch.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api_accounts_batch.py`:

```python
def test_set_warmup_mode_updates_listed_accounts(client, db):
    from app.repositories import accounts as acc_repo
    a = acc_repo.create(db, {"profile_name": "mode-a"})
    b = acc_repo.create(db, {"profile_name": "mode-b"})
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [a, b], "mode": "daily"})
    assert resp.status_code == 200
    assert resp.json() == {"updated": 2}
    assert acc_repo.get(db, a)["warmup_mode"] == "daily"
    assert acc_repo.get(db, b)["warmup_mode"] == "daily"


def test_set_warmup_mode_rejects_unknown_mode(client, db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "mode-bad"})
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [aid], "mode": "weekly"})
    assert resp.status_code == 400
    assert "mode" in resp.json()["detail"]


def test_set_warmup_mode_rejects_empty_ids(client):
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [], "mode": "daily"})
    assert resp.status_code == 400


def test_set_warmup_mode_rejects_missing_account(client):
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [999], "mode": "daily"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_accounts_batch.py -k warmup_mode -v`

Expected: FAIL — 404 on the endpoint.

- [ ] **Step 3: Add endpoint**

In `app/api/accounts.py`, insert this new route function immediately after the `mark_warmed_up` endpoint (after line 98):

```python
@router.post("/warmup-mode")
def set_warmup_mode(body: dict, session: Session = Depends(get_session)):
    account_ids = body.get("account_ids") or body.get("ids") or []
    mode = (body.get("mode") or "").strip()
    if mode not in {"count", "daily"}:
        raise HTTPException(status_code=400,
                            detail="mode must be 'count' or 'daily'")
    if (not isinstance(account_ids, list)
            or not account_ids
            or not all(isinstance(i, int) for i in account_ids)):
        raise HTTPException(status_code=400,
                            detail="account_ids must be a non-empty list")
    rows = [acc_repo.get(session, aid) for aid in account_ids]
    if any(r is None for r in rows):
        raise HTTPException(status_code=400, detail="account not found")
    for aid in account_ids:
        acc_repo.update(session, aid, {"warmup_mode": mode})
    return {"updated": len(account_ids)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_accounts_batch.py -k warmup_mode -v`

Expected: 4 PASSED.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`

Expected: ≥ 397 passed.

- [ ] **Step 6: Commit**

```bash
git add app/api/accounts.py tests/test_api_accounts_batch.py
git commit -m "feat(api): POST /api/accounts/warmup-mode (batch mode set)"
```

---

## Task 6: Periodic `daily_warmup_scan` task

**Files:**
- Create: `app/tasks/daily_warmup.py`
- Modify: `app/tasks/procrastinate_app.py` — add to `import_paths`
- Test: `tests/test_tasks_periodic.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tasks_periodic.py`:

```python
def test_daily_warmup_scan_registered_as_cron():
    import app.tasks.daily_warmup  # noqa: F401
    from app.tasks import procrastinate_app
    cron_task_names = {
        d.task.name for d in procrastinate_app.periodic_deferrer.periodic_tasks
    }
    assert "daily_warmup_scan" in cron_task_names


def test_daily_warmup_scan_enqueues_only_stale_daily_mode_accounts(
    db, procrastinate_app, monkeypatch,
):
    import asyncio
    from datetime import datetime, timedelta
    from app.tasks import daily_warmup as scan_module
    from app.repositories import accounts as acc_repo

    # Fresh daily-mode account (just warmed) — should NOT be queued
    fresh = acc_repo.create(db, {
        "geelark_phone_id": "p-fresh",
        "profile_name": "fresh",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=1),
    })
    # Stale daily-mode account — should be queued
    stale = acc_repo.create(db, {
        "geelark_phone_id": "p-stale",
        "profile_name": "stale",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=7),
    })
    # Never-warmed daily-mode account — should be queued
    never = acc_repo.create(db, {
        "geelark_phone_id": "p-never",
        "profile_name": "never",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "daily",
    })
    # count-mode stale — should NOT be queued
    acc_repo.create(db, {
        "geelark_phone_id": "p-count-stale",
        "profile_name": "count-stale",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "count",
        "last_warm_at": datetime.utcnow() - timedelta(hours=7),
    })
    # daily-mode already warmed_up — should NOT be queued
    acc_repo.create(db, {
        "geelark_phone_id": "p-warmed-daily",
        "profile_name": "warmed-daily",
        "tiktok_logged_in": 1,
        "warmed_up": 1,
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=7),
    })

    asyncio.run(scan_module.daily_warmup_scan(timestamp=0))

    jobs = procrastinate_app.connector.jobs
    queued_account_ids = sorted(
        j["args"]["account_id"] for j in jobs.values()
        if "warmup_account" in j["task_name"]
    )
    assert queued_account_ids == sorted([stale, never])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tasks_periodic.py -k daily_warmup_scan -v`

Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the task module**

Create `app/tasks/daily_warmup.py`:

```python
"""Periodic scan that re-queues warmup_account_task for daily-mode
accounts whose last_warm_at is older than 24h / daily_warmup_count
(i.e. ~6h when daily_warmup_count = 4). A 0..30 min schedule_in jitter
breaks the regular cadence so cloud-phone activity is not perfectly
periodic.

Daily-mode accounts NEVER self-requeue (see
warmup._should_requeue_after_success). All cadence flows through this
scan.

Note on time semantics: accounts.last_warm_at is TIMESTAMP WITHOUT TIME
ZONE, written with datetime.utcnow() (naive UTC). We compute the
cutoff in Python the same way and pass it as a parameter so the
comparison is naive-vs-naive — no Postgres session-timezone surprises.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db import session_scope
from app.repositories import settings as srepo
from app.tasks import procrastinate_app
from app.tasks.warmup import warmup_account_task


@procrastinate_app.periodic(cron="*/10 * * * *")
@procrastinate_app.task(queue="default", name="daily_warmup_scan",
                        queueing_lock="daily_warmup_scan")
async def daily_warmup_scan(timestamp: int) -> str:
    with session_scope() as session:
        n = int(srepo.get_all(session).get("daily_warmup_count") or 4)
        interval_seconds = max(1, 86400 // max(n, 1))
        cutoff = datetime.utcnow() - timedelta(seconds=interval_seconds)
        rows = session.execute(
            text(
                "SELECT id FROM accounts "
                "WHERE warmup_mode = 'daily' "
                "AND tiktok_logged_in = 1 "
                "AND warmed_up = 0 "
                "AND geelark_phone_id IS NOT NULL "
                "AND (last_warm_at IS NULL OR last_warm_at < :cutoff)"
            ),
            {"cutoff": cutoff},
        ).fetchall()
        candidate_ids = [r[0] for r in rows]

    deferred = 0
    for aid in candidate_ids:
        try:
            await warmup_account_task.configure(
                queueing_lock=f"warmup_account_{aid}",
                schedule_in={"seconds": random.randint(0, 1800)},
            ).defer_async(account_id=aid)
            deferred += 1
        except Exception:
            # AlreadyEnqueued via queueing_lock, or transient connector
            # error; either way the next scan will try again.
            pass
    return f"daily_warmup_scan: queued {deferred} of {len(candidate_ids)}"
```

- [ ] **Step 4: Register the module with procrastinate**

In `app/tasks/procrastinate_app.py`, inside the `import_paths` list (around line 27), add one entry — place it right after `"app.tasks.auto_publish",`:

```python
        "app.tasks.daily_warmup",
```

The list becomes:

```python
    import_paths=[
        "app.tasks.warmup",
        "app.tasks.pic_check",
        "app.tasks.proxy_check",
        "app.tasks.publish",
        "app.tasks.auto_publish",
        "app.tasks.daily_warmup",
        "app.tasks.heartbeat",
        "app.tasks.engagement",
    ],
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tasks_periodic.py -k daily_warmup_scan -v`

Expected: 2 PASSED.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q`

Expected: ≥ 399 passed.

- [ ] **Step 7: Commit**

```bash
git add app/tasks/daily_warmup.py app/tasks/procrastinate_app.py \
        tests/test_tasks_periodic.py
git commit -m "feat(worker): daily_warmup_scan re-queues daily-mode accounts every ~6h"
```

---

## Task 7: Frontend i18n strings

**Files:**
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Add English strings**

In `frontend/src/i18n/locales/en.json`, locate the `"accounts"` block (search for `"accounts": {`). Add these keys (insert next to the existing `col.*` and other accounts keys; pick any consistent slot — JSON ordering is irrelevant):

```json
    "col.mode": "Mode",
    "mode.count": "Count",
    "mode.daily": "Daily",
    "mode.switchTo": "Switch mode",
    "mode.switchToCount": "Switch to Count",
    "mode.switchToDaily": "Switch to Daily",
    "mode.switched": "{{count}} account(s) switched to {{mode}}",
    "mode.switchFailed": "Failed to switch mode",
    "dailyProgress": "Daily · {{count}} runs",
    "dailyNext": "next ≈ {{time}}"
```

- [ ] **Step 2: Add Chinese mirror**

In `frontend/src/i18n/locales/zh.json`, add equivalent keys in the `"accounts"` block:

```json
    "col.mode": "模式",
    "mode.count": "次数",
    "mode.daily": "日常",
    "mode.switchTo": "切换模式",
    "mode.switchToCount": "切换为「次数」",
    "mode.switchToDaily": "切换为「日常」",
    "mode.switched": "{{count}} 个账号已切换为 {{mode}}",
    "mode.switchFailed": "切换模式失败",
    "dailyProgress": "日常 · 已 {{count}} 次",
    "dailyNext": "下次约 {{time}}"
```

- [ ] **Step 3: Validate JSON**

Run: `node -e "JSON.parse(require('fs').readFileSync('frontend/src/i18n/locales/en.json','utf8')); JSON.parse(require('fs').readFileSync('frontend/src/i18n/locales/zh.json','utf8')); console.log('ok')"`

Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(i18n): warmup mode strings (en + zh)"
```

---

## Task 8: Frontend Mode column + Daily progress label

**Files:**
- Modify: `frontend/src/api.ts` — extend Account type
- Modify: `frontend/src/pages/Accounts.tsx` — Mode column + Daily progress branch
- Build: `cd frontend && npm run build`

- [ ] **Step 1: Extend Account type with warmup_mode**

In `frontend/src/api.ts`, find the `Account` type definition (search for `warmup_count: number`). Add this field next to the other warmup fields:

```ts
  warmup_mode: 'count' | 'daily'
```

(Nothing else in this step — we deliberately don't surface "next run ≈ HH:MM" in MVP. `dailyNext` i18n key is reserved for a future iteration.)

- [ ] **Step 2: Add Mode column header**

In `frontend/src/pages/Accounts.tsx`, find the `<Th>{t('accounts.col.warmProgress')}</Th>` line (≈line 1061) and insert a new column header right before it:

```tsx
                <Th>{t('accounts.col.mode')}</Th>
```

- [ ] **Step 3: Render Mode badge cell**

In the same file, find the corresponding row body (the `<td>` that renders the warm-progress bar at ≈line 1208). Insert a new `<td>` right before the progress cell:

```tsx
                        <td>
                          {r.warmup_mode === 'daily' ? (
                            <Badge tone="blue">{t('accounts.mode.daily')}</Badge>
                          ) : (
                            <Badge tone="gray">{t('accounts.mode.count')}</Badge>
                          )}
                        </td>
```

(`Badge` accepts tone `blue|green|orange|red|gray` — confirmed against [ui.tsx](frontend/src/components/ui.tsx).)

- [ ] **Step 4: Branch the warm-progress cell on mode**

In the same file at ≈line 1208, the progress cell currently renders a bar `r.warmup_count / r.target_warmup_count`. Wrap it so daily-mode shows the daily label instead:

```tsx
                        {r.warmup_mode === 'daily' ? (
                          <span style={{ fontSize: 12, color: '#6b7280' }}>
                            {t('accounts.dailyProgress', { count: r.warmup_count })}
                          </span>
                        ) : (
                          /* existing count-mode progress bar block stays here */
                        )}
```

Keep the existing count-mode JSX inside the `else` branch unchanged (the `ProgressBar` / `Badge` block that already exists).

- [ ] **Step 5: Build frontend to catch type errors**

Run: `cd frontend && npm run build`

Expected: build succeeds; no TypeScript errors.

- [ ] **Step 6: Run python tests (sanity)**

Run: `uv run pytest -q`

Expected: ≥ 399 passed (no change — frontend-only edit).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/Accounts.tsx
git commit -m "feat(ui): warmup Mode column + Daily progress label"
```

---

## Task 9: Switch mode toolbar action + API client

**Files:**
- Modify: `frontend/src/api.ts` — add `setWarmupMode`
- Modify: `frontend/src/pages/Accounts.tsx` — `SelectionToolbar` Switch mode button

- [ ] **Step 1: Add API client helper**

In `frontend/src/api.ts`, find the `warmupAccounts` helper (around line 216) and add this sibling helper right after it:

```ts
  setWarmupMode: (account_ids: number[], mode: 'count' | 'daily') =>
    req('/accounts/warmup-mode',
        { method: 'POST', body: JSON.stringify({ account_ids, mode }) }),
```

Use the same `req` helper that `markAccountsWarmedUp` (next to it) uses — copy that line's shape exactly.

- [ ] **Step 2: Extend `SelectionToolbar` props**

In `frontend/src/pages/Accounts.tsx`, find `function SelectionToolbar({ ... })` (line 152). The existing signature is:

```tsx
function SelectionToolbar({ count, mode, onWarmup, onPublish, onMarkWarmedUp,
                            onMove, onDelete, onCancel, markWarmedLabel }: {
  count: number; mode: 'all' | 'group'
  onWarmup: () => void; onPublish: () => void
  onMarkWarmedUp: () => void
  onMove: () => void; onDelete: () => void; onCancel: () => void
  markWarmedLabel: string
}) {
```

Follow the `markWarmedLabel` pattern — pass labels through props rather than calling `useTranslation()` in this subcomponent. Replace the function signature with:

```tsx
function SelectionToolbar({ count, mode, onWarmup, onPublish, onMarkWarmedUp,
                            onMove, onDelete, onCancel, markWarmedLabel,
                            onSwitchMode, switchToDailyLabel,
                            switchToCountLabel }: {
  count: number; mode: 'all' | 'group'
  onWarmup: () => void; onPublish: () => void
  onMarkWarmedUp: () => void
  onMove: () => void; onDelete: () => void; onCancel: () => void
  markWarmedLabel: string
  onSwitchMode: (next: 'count' | 'daily') => void
  switchToDailyLabel: string
  switchToCountLabel: string
}) {
```

- [ ] **Step 3: Render Switch mode buttons**

Inside the same component's JSX, immediately after the existing `Mark warmed up` button (the `<button onClick={onMarkWarmedUp}>` at line 175–177), insert:

```tsx
        <button onClick={() => onSwitchMode('daily')} style={secondaryBtn}>
          {switchToDailyLabel}
        </button>
        <button onClick={() => onSwitchMode('count')} style={secondaryBtn}>
          {switchToCountLabel}
        </button>
```

- [ ] **Step 4: Wire up callback + labels in the page component**

Find where `SelectionToolbar` is rendered (line 940). It currently passes `onWarmup`, `onPublish`, `onMarkWarmedUp`, `markWarmedLabel`, etc. Add three new props alongside `markWarmedLabel={t('accounts.markWarmedAction')}`:

```tsx
          onSwitchMode={(next) => doSwitchMode([...selected], next)}
          switchToDailyLabel={t('accounts.mode.switchToDaily')}
          switchToCountLabel={t('accounts.mode.switchToCount')}
```

- [ ] **Step 5: Add the handler function**

Near `doBatchWarmup` (≈line 787) and `doMarkWarmedUp` (≈line 803), add:

```tsx
  async function doSwitchMode(ids: number[], mode: 'count' | 'daily') {
    try {
      const result = await api.setWarmupMode(ids, mode)
      toast({
        kind: 'ok',
        msg: t('accounts.mode.switched', {
          count: result.updated, mode: t(`accounts.mode.${mode}`)
        }),
      })
      await refresh()
    } catch (e) {
      toast({
        kind: 'err',
        msg: errorMessage(e, t('accounts.mode.switchFailed')),
      })
    }
  }
```

Use the same `toast`, `refresh`, and `errorMessage` helpers as `doMarkWarmedUp` — copy its shape exactly.

- [ ] **Step 6: Build frontend**

Run: `cd frontend && npm run build`

Expected: build succeeds; no TypeScript errors.

- [ ] **Step 7: Run python tests (sanity)**

Run: `uv run pytest -q`

Expected: ≥ 399 passed.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/Accounts.tsx
git commit -m "feat(ui): Switch mode toolbar action"
```

---

## Task 10: Final verification

- [ ] **Step 1: Full backend test suite**

Run: `uv run pytest -q`

Expected: ≥ 399 passed, 0 failed.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`

Expected: build success.

- [ ] **Step 3: Acceptance walkthrough (manual, dev server)**

Start web tier: `./run.sh` (port :8080), and in another terminal start worker: `uv run python -m procrastinate -a app.tasks:procrastinate_app worker --queues phones,default`.

Open `http://localhost:8080`, log in, go to **Accounts**:

1. Confirm the new **Mode** column shows `Count` for every existing row.
2. Select one or more accounts → toolbar should show **Switch to Daily** / **Switch to Count** buttons.
3. Click **Switch to Daily** on one account → row redraws with `Daily` badge and "Daily · 0 runs" instead of the count progress bar.
4. Wait ≤ 10 minutes (or manually defer `daily_warmup_scan` via REPL); confirm the worker log shows the scan picked up the account and enqueued a `warmup_account` job.
5. After the warmup run completes, the row should show `Daily · 1 runs`; `warmed_up` stays unchecked.
6. Click **Mark warmed up** on the row → mode flips back to `Count`, `warmed_up=1`, and the account enters the publishing pool.

If any of these steps misbehave, fix and re-run only the impacted task before continuing.

- [ ] **Step 4: Deploy to production**

Follow `docs/production-deploy.md`. Schema change present → take a `pg_dump` of `mkt` (in the `svm-postgres` container) **before** running `alembic upgrade head` on the server. Restart **both** `mkt` and `mkt-worker` systemd units (the new periodic task needs the worker reload).

Verify post-deploy:

```bash
# On server, in the svm-postgres container
docker exec svm-postgres psql -U <user> -d mkt -c \
  "SELECT warmup_mode, COUNT(*) FROM accounts GROUP BY warmup_mode"
```

Expected: only `count` rows (server_default backfilled all existing rows). Watch `journalctl -u mkt-worker -f` for one `daily_warmup_scan: queued 0 of 0` line within ten minutes of restart, confirming the periodic task is registered.
