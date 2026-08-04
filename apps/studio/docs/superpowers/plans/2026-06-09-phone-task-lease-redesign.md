# Phone-Task Lease Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Procrastinate `lock=phone:acc{id}` with a per-account `phone_busy_until` DB lease + split warmup into fire-and-forget `start_warmup` + chained `check_warmup_status` + `reclaim_in_flight_warmup` cron backstop. Apply the same lease to `publish_publication` so the two pipelines share one failure model.

**Architecture:** `accounts.phone_busy_until` (timestamp) is the single source of truth for phone occupancy. Tasks `try_acquire_phone_lease(account_id, ttl=20min)` via atomic CAS UPDATE; the lease auto-expires so worker crashes self-heal. Procrastinate keeps scheduling/retry duties only — no more in-band `lock=` field. A `reclaim_in_flight_warmup` cron every 5 minutes scans for `(warmup_task_id IS NOT NULL AND phone_busy_until < now())` rows and either clears the `'pending'` sentinel (T1↔T2 crash window) or resumes polling via a fresh `check_warmup_status` defer.

**Tech Stack:** PostgreSQL 16 + Alembic, SQLModel/SQLAlchemy, Procrastinate 3.x (PsycopgConnector), Pytest, FastAPI.

**Spec:** [`docs/superpowers/specs/2026-06-09-phone-task-lease-redesign-design.md`](../specs/2026-06-09-phone-task-lease-redesign-design.md)

---

## File Structure

**Create:**
- `alembic/versions/<hash>_phone_lease_columns.py` — schema migration
- `tests/test_phone_lease.py` — lease repo helpers
- `tests/test_warmup_lease_service.py` — `start_warmup_one` + `advance_warmup_one` covering 4 crash windows
- `tests/test_warmup_tasks_lease.py` — procrastinate task wiring
- `tests/test_daily_warmup_scan_lease.py` — scan now filters on `phone_busy_until`
- `tests/test_reclaim_in_flight_warmup.py` — pending-clear + resume branches
- `tests/test_publish_lease.py` — publish pipeline lease integration

**Modify:**
- `app/models.py` — add `phone_busy_until`, `warmup_task_id` to `Account`
- `app/repositories/accounts.py` — add `try_acquire_phone_lease`, `release_phone_lease`, `mark_warmup_task_id`, `clear_warmup_task_id`
- `app/services/warmup_service.py` — replace `_warm_one` + `run_warmup_for_accounts` + `run_warmup` with `start_warmup_one` + `advance_warmup_one` + `reclaim_in_flight_one`; add `PhoneBusy` exception; add `LEASE_TTL_SECONDS` constant
- `app/tasks/warmup.py` — replace contents: new `start_warmup_task`, `check_warmup_status_task`, `reclaim_in_flight_warmup_task`
- `app/tasks/daily_warmup.py` — add `phone_busy_until` filter; defer `start_warmup_task` instead of `warmup_account_task`
- `app/tasks/publish.py` — remove `lock=_phone_lock(account_id)`; the existing `posting_service.PhoneBusy` path becomes lease-based
- `app/services/posting_service.py` — `submit_publication_to_geelark` calls `try_acquire_phone_lease` before phone start; replaces the `account_has_inflight` check (kept as defense-in-depth)
- `app/tasks/__init__.py` — delete `reset_stale_doing_jobs` function
- `tests/test_warmup_service.py` (existing) — update or replace cases touching `_warm_one`
- `tests/test_publish_task.py` (existing, if present) — update for lease behaviour

**Deploy artifacts (no in-repo file change, runbook-only):**
- `mkt-worker.service` systemd unit on prod: remove the `ExecStartPre=...reset_stale_doing_jobs()` line
- `docs/production-deploy.md` — append a "phone-lease migration" subsection documenting the 2-deploy sequence

---

## Task 1: Alembic migration — add `phone_busy_until`, `warmup_task_id`, partial index

**Files:**
- Create: `alembic/versions/<new>_phone_lease_columns.py`

- [ ] **Step 1: Generate the migration skeleton**

Run:
```bash
uv run alembic revision -m "phone lease columns"
```
Expected output: a new file under `alembic/versions/` prefixed with a 12-char hex hash. Note its path — you will edit it next.

- [ ] **Step 2: Identify the previous head**

Run:
```bash
ls -1t alembic/versions/*.py | head -5
```
Look at the second-most-recent file (the new one is most recent). Open it and copy its `revision = "..."` value — that is `down_revision` for the new file.

- [ ] **Step 3: Write the migration body**

Replace the contents of the new migration file with:

```python
"""phone lease columns

Adds accounts.phone_busy_until (lease expiration timestamp) and
accounts.warmup_task_id (in-flight GeeLark task id, with 'pending'
sentinel between lease grab and GeeLark submit). Partial index
supports the reclaim_in_flight_warmup cron scan.
"""
from alembic import op
import sqlalchemy as sa


revision = "<keep the hash already in this file>"
down_revision = "<the previous head you identified in Step 2>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("phone_busy_until", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("warmup_task_id", sa.String(length=64), nullable=True),
    )
    op.execute(
        "CREATE INDEX idx_accounts_warmup_inflight "
        "ON accounts (phone_busy_until) "
        "WHERE warmup_task_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_accounts_warmup_inflight")
    op.drop_column("accounts", "warmup_task_id")
    op.drop_column("accounts", "phone_busy_until")
```

- [ ] **Step 4: Apply against a local mkt_test DB and verify**

Run:
```bash
DATABASE_URL=postgresql+psycopg://$(whoami)@localhost:5432/mkt_test uv run alembic upgrade head
DATABASE_URL=postgresql+psycopg://$(whoami)@localhost:5432/mkt_test psql -c "\d accounts" | grep -E "phone_busy_until|warmup_task_id"
```
Expected: two lines, both showing the new columns with type/nullability as defined.

- [ ] **Step 5: Verify the index exists**

Run:
```bash
DATABASE_URL=postgresql+psycopg://$(whoami)@localhost:5432/mkt_test psql -c "\di idx_accounts_warmup_inflight"
```
Expected: one row showing the index.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/*phone_lease_columns*.py
git commit -m "feat(db): add phone lease columns to accounts"
```

---

## Task 2: Add `phone_busy_until` + `warmup_task_id` to the Account model

**Files:**
- Modify: `app/models.py:54-60`

- [ ] **Step 1: Read the surrounding model context**

Run:
```bash
sed -n '50,65p' app/models.py
```
Expected: see existing warmup-related fields like `warmup_count`, `target_warmup_count`, `warmup_mode`, `last_warm_at`, etc.

- [ ] **Step 2: Insert the new fields below `last_warm_notes`**

Edit `app/models.py`. Find the line `last_warm_notes: Optional[str] = None` (around line 59) and add directly after it:

```python
    phone_busy_until: Optional[datetime] = None
    warmup_task_id: Optional[str] = Field(default=None, max_length=64)
```

(indentation matches the surrounding class body — 4 spaces)

- [ ] **Step 3: Run existing model-touching tests to ensure no regression**

Run:
```bash
uv run pytest tests/ -q -k "model or account" --no-header 2>&1 | tail -20
```
Expected: no failures from model changes (test count unchanged or higher).

- [ ] **Step 4: Commit**

```bash
git add app/models.py
git commit -m "feat(models): Account.phone_busy_until + warmup_task_id"
```

---

## Task 3: Repository — `try_acquire_phone_lease`, `release_phone_lease`, `mark_warmup_task_id`, `clear_warmup_task_id`

**Files:**
- Modify: `app/repositories/accounts.py` (append to file)
- Test: `tests/test_phone_lease.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_phone_lease.py`:

```python
"""Lease primitive tests using the in-memory test DB harness."""
from datetime import datetime, timedelta

from app.repositories import accounts as acc_repo


def test_try_acquire_idle_succeeds(test_session, sample_account_id):
    ok = acc_repo.try_acquire_phone_lease(test_session, sample_account_id, 60)
    assert ok is True
    a = acc_repo.get(test_session, sample_account_id)
    assert a["phone_busy_until"] is not None
    assert a["phone_busy_until"] > datetime.utcnow()


def test_try_acquire_when_held_fails(test_session, sample_account_id):
    assert acc_repo.try_acquire_phone_lease(test_session, sample_account_id, 60)
    assert acc_repo.try_acquire_phone_lease(test_session, sample_account_id, 60) is False


def test_try_acquire_when_expired_succeeds(test_session, sample_account_id):
    # Manually expire
    acc_repo.update(test_session, sample_account_id, {
        "phone_busy_until": datetime.utcnow() - timedelta(seconds=10)})
    test_session.commit()
    assert acc_repo.try_acquire_phone_lease(test_session, sample_account_id, 60) is True


def test_release_phone_lease_clears_both_fields(test_session, sample_account_id):
    acc_repo.try_acquire_phone_lease(test_session, sample_account_id, 60)
    acc_repo.mark_warmup_task_id(test_session, sample_account_id, "geelark-task-123")
    acc_repo.release_phone_lease(test_session, sample_account_id)
    a = acc_repo.get(test_session, sample_account_id)
    assert a["phone_busy_until"] is None
    assert a["warmup_task_id"] is None


def test_mark_and_clear_warmup_task_id(test_session, sample_account_id):
    acc_repo.mark_warmup_task_id(test_session, sample_account_id, "pending")
    assert acc_repo.get(test_session, sample_account_id)["warmup_task_id"] == "pending"
    acc_repo.mark_warmup_task_id(test_session, sample_account_id, "real-task-id")
    assert acc_repo.get(test_session, sample_account_id)["warmup_task_id"] == "real-task-id"
    acc_repo.clear_warmup_task_id(test_session, sample_account_id)
    assert acc_repo.get(test_session, sample_account_id)["warmup_task_id"] is None
```

Check `tests/conftest.py` for the actual fixture names — if `test_session` / `sample_account_id` are named differently in this repo, rename to match the existing convention.

- [ ] **Step 2: Run the test, confirm all five fail**

Run:
```bash
uv run pytest tests/test_phone_lease.py -v 2>&1 | tail -15
```
Expected: 5 FAILED with `AttributeError: module 'app.repositories.accounts' has no attribute 'try_acquire_phone_lease'` (or similar).

- [ ] **Step 3: Implement the four lease helpers**

Append to `app/repositories/accounts.py`:

```python
def try_acquire_phone_lease(session, account_id: int, ttl_seconds: int) -> bool:
    """Atomic CAS lease grab. Returns True iff the caller now holds the lease.

    Sets ONLY phone_busy_until — does not touch warmup_task_id. Callers that
    want to record an in-flight task id call mark_warmup_task_id separately.
    The split keeps reclaim_cron able to renew a lease without overwriting
    the live task_id.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import text
    now = datetime.utcnow()
    expires = now + timedelta(seconds=ttl_seconds)
    result = session.execute(
        text(
            "UPDATE accounts "
            "SET phone_busy_until = :expires "
            "WHERE id = :aid "
            "  AND (phone_busy_until IS NULL OR phone_busy_until < :now)"
        ),
        {"aid": account_id, "expires": expires, "now": now},
    )
    session.commit()
    return result.rowcount == 1


def release_phone_lease(session, account_id: int) -> None:
    """Unconditional release: clears both phone_busy_until and warmup_task_id."""
    from sqlalchemy import text
    session.execute(
        text("UPDATE accounts "
             "SET phone_busy_until = NULL, warmup_task_id = NULL "
             "WHERE id = :aid"),
        {"aid": account_id},
    )
    session.commit()


def mark_warmup_task_id(session, account_id: int, task_id: str) -> None:
    """Set warmup_task_id to the given value (sentinel 'pending' or real id)."""
    from sqlalchemy import text
    session.execute(
        text("UPDATE accounts SET warmup_task_id = :tid WHERE id = :aid"),
        {"aid": account_id, "tid": task_id},
    )
    session.commit()


def clear_warmup_task_id(session, account_id: int) -> None:
    from sqlalchemy import text
    session.execute(
        text("UPDATE accounts SET warmup_task_id = NULL WHERE id = :aid"),
        {"aid": account_id},
    )
    session.commit()
```

- [ ] **Step 4: Run the test, confirm all pass**

Run:
```bash
uv run pytest tests/test_phone_lease.py -v 2>&1 | tail -15
```
Expected: 5 PASSED.

- [ ] **Step 5: Run the full repository test suite for regression**

Run:
```bash
uv run pytest tests/test_accounts_repo.py tests/test_phone_lease.py -q 2>&1 | tail -10
```
Expected: no failures.

- [ ] **Step 6: Commit**

```bash
git add app/repositories/accounts.py tests/test_phone_lease.py
git commit -m "feat(repo): phone lease primitives on accounts"
```

---

## Task 4: `warmup_service.start_warmup_one` — T1→T3 sequence

**Files:**
- Modify: `app/services/warmup_service.py` (full rewrite — backup old content first)
- Test: `tests/test_warmup_lease_service.py`

- [ ] **Step 1: Inspect current warmup_service to migrate any used callers**

Run:
```bash
grep -rn "warmup_service\." app/ tests/ 2>&1 | grep -v "warmup_service.py"
```
Note every caller — they will need updating in Task 8 and Task 12.

- [ ] **Step 2: Write the failing test for the happy path**

Create `tests/test_warmup_lease_service.py`:

```python
"""Test the T1→T3 start sequence under both happy and crash paths."""
from datetime import datetime, timedelta

import pytest

from app.repositories import accounts as acc_repo
from app.services import warmup_service


class FakeClient:
    def __init__(self, *, fail_at: str | None = None):
        self.started, self.stopped, self.created = [], [], []
        self.fail_at = fail_at
        self.next_task_id = "gtid-1"

    def start_phones(self, ids):
        self.started.extend(ids)
        if self.fail_at == "start_phones":
            raise RuntimeError("simulated start_phones failure")

    def create_warmup_task(self, env_id, duration):
        self.created.append((env_id, duration))
        if self.fail_at == "create_warmup_task":
            raise RuntimeError("simulated create_warmup_task failure")
        return self.next_task_id

    def stop_phones(self, ids):
        self.stopped.extend(ids)


def test_start_warmup_one_happy_path(test_session, warmup_eligible_account):
    aid = warmup_eligible_account["id"]
    client = FakeClient()
    result = warmup_service.start_warmup_one(test_session, client, aid)
    assert result == "gtid-1"
    a = acc_repo.get(test_session, aid)
    assert a["warmup_task_id"] == "gtid-1"
    assert a["phone_busy_until"] is not None
    assert a["phone_busy_until"] > datetime.utcnow()
    assert client.started == [a["geelark_phone_id"]]
    assert client.stopped == []  # phone stays up between submit and check


def test_start_warmup_one_phone_busy_raises(
        test_session, warmup_eligible_account):
    aid = warmup_eligible_account["id"]
    acc_repo.try_acquire_phone_lease(test_session, aid, 60)
    with pytest.raises(warmup_service.PhoneBusy):
        warmup_service.start_warmup_one(test_session, FakeClient(), aid)


def test_start_warmup_one_start_failure_releases_lease(
        test_session, warmup_eligible_account):
    aid = warmup_eligible_account["id"]
    client = FakeClient(fail_at="start_phones")
    with pytest.raises(RuntimeError):
        warmup_service.start_warmup_one(test_session, client, aid)
    a = acc_repo.get(test_session, aid)
    assert a["phone_busy_until"] is None
    assert a["warmup_task_id"] is None
    assert a["last_warm_result"] == "Failed"


def test_start_warmup_one_create_task_failure_releases_lease(
        test_session, warmup_eligible_account):
    aid = warmup_eligible_account["id"]
    client = FakeClient(fail_at="create_warmup_task")
    with pytest.raises(RuntimeError):
        warmup_service.start_warmup_one(test_session, client, aid)
    a = acc_repo.get(test_session, aid)
    assert a["phone_busy_until"] is None
    assert a["warmup_task_id"] is None
    assert client.stopped == [a["geelark_phone_id"]]
```

If `warmup_eligible_account` is not already a fixture, add one to `tests/conftest.py`:

```python
@pytest.fixture
def warmup_eligible_account(test_session):
    from app.models import Account
    a = Account(
        geelark_phone_id="phone-test-1",
        profile_name="warmup-test",
        tiktok_logged_in=1,
        pic_status="done",
        warmed_up=0,
        warmup_count=0,
        target_warmup_count=10,
        warmup_mode="daily",
    )
    test_session.add(a)
    test_session.commit()
    test_session.refresh(a)
    from app.repositories import accounts as acc_repo
    return acc_repo.get(test_session, a.id)
```

- [ ] **Step 3: Run the test, confirm all four fail**

Run:
```bash
uv run pytest tests/test_warmup_lease_service.py -v 2>&1 | tail -25
```
Expected: 4 FAILED — `PhoneBusy` doesn't exist or `start_warmup_one` doesn't exist.

- [ ] **Step 4: Rewrite `app/services/warmup_service.py`**

Replace the entire contents of `app/services/warmup_service.py` with:

```python
"""Warmup service — DB-lease–based phone occupancy.

start_warmup_one: T1 (acquire lease + 'pending' sentinel) → T2 (start phone +
create GeeLark task) → T3 (write real task_id). Fire-and-forget — polling
lives in the procrastinate task layer.

advance_warmup_one: one GeeLark poll → DB transition (count/lease cleanup
on terminal).

reclaim_in_flight_one: cron backstop. Either clears a 'pending' sentinel
(T1↔T3 crash, accepted cost) or hands off to advance_warmup_one.
"""
import logging
import random
from datetime import datetime

from app.repositories import accounts as acc_repo

logger = logging.getLogger(__name__)

LEASE_TTL_SECONDS = 20 * 60   # 20 minutes — covers 10-min worst-case warmup +
                              #              20-min poll chain with safety margin


class PhoneBusy(RuntimeError):
    """The phone lease is already held by another in-flight task."""


def _utcnow_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def start_warmup_one(session, client, account_id: int,
                      duration_minutes: int | None = None) -> str:
    """Submit one warmup task to GeeLark. Returns the GeeLark task_id on
    success. Raises PhoneBusy if the lease is already held. Raises other
    exceptions on GeeLark failure (lease + phone released first)."""
    a = acc_repo.get(session, account_id)
    if a is None:
        return "skipped: account not found"
    if not (a.get("tiktok_logged_in") and a.get("geelark_phone_id")):
        return "skipped: not eligible"
    if a.get("warmup_mode") != "daily" and a.get("warmed_up"):
        return "skipped: already warmed (count mode)"

    # T1: acquire lease, then sentinel
    if not acc_repo.try_acquire_phone_lease(session, account_id, LEASE_TTL_SECONDS):
        raise PhoneBusy(f"phone lease busy for account {account_id}")
    acc_repo.mark_warmup_task_id(session, account_id, "pending")

    pid = a["geelark_phone_id"]
    dur = duration_minutes or random.randint(2, 5)
    try:
        # T2: start phone + submit GeeLark task
        client.start_phones([pid])
        gtid = client.create_warmup_task(pid, dur)
    except Exception as exc:
        try:
            client.stop_phones([pid])
        except Exception:
            pass
        acc_repo.update(session, account_id, {
            "last_warm_at": _utcnow_str(),
            "last_warm_result": "Failed",
            "last_warm_notes": f"start_warmup error: {exc}"})
        acc_repo.release_phone_lease(session, account_id)
        raise

    # T3: replace 'pending' sentinel with real id
    acc_repo.mark_warmup_task_id(session, account_id, gtid)
    return gtid


def advance_warmup_one(session, client, account_id: int, task_id: str) -> str:
    """One poll cycle. Returns one of:
      'running'  — GeeLark says non-terminal; caller should reschedule
      'success'  — GeeLark says status=3; warmup_count incremented, lease cleared
      'failure'  — GeeLark says status=4/7; lease cleared
      'missing'  — account vanished; lease left as-is for cron to clean
    """
    res = client.query_tasks([task_id])
    status = res[0].get("status") if res else None
    if status not in (3, 4, 7):
        return "running"

    a = acc_repo.get(session, account_id)
    if a is None:
        return "missing"

    pid = a.get("geelark_phone_id")
    if pid:
        try:
            client.stop_phones([pid])
        except Exception:
            pass

    if status == 3:
        count = int(a.get("warmup_count") or 0) + 1
        target = int(a.get("target_warmup_count") or 10)
        mode = a.get("warmup_mode") or "count"
        patch = {"warmup_count": count,
                 "last_warm_at": _utcnow_str(),
                 "last_warm_result": "Success",
                 "last_warm_notes": None}
        if mode == "count" and count >= target:
            patch["warmed_up"] = 1
        acc_repo.update(session, account_id, patch)
        acc_repo.release_phone_lease(session, account_id)
        return "success"

    acc_repo.update(session, account_id, {
        "last_warm_at": _utcnow_str(),
        "last_warm_result": "Failed",
        "last_warm_notes": f"task status {status}"})
    acc_repo.release_phone_lease(session, account_id)
    return "failure"


def reclaim_in_flight_one(session, client, account_id: int) -> str:
    """Cron-side reclaim. Returns:
      'pending_cleared' — sentinel removed, lease released (T1↔T3 crash)
      'resume:<task_id>' — caller should defer a check_warmup_status_task
      'no_task'         — already cleaned up by someone else
      'missing'         — account vanished
    """
    a = acc_repo.get(session, account_id)
    if a is None:
        return "missing"
    task_id = a.get("warmup_task_id")
    if not task_id:
        return "no_task"
    if task_id == "pending":
        pid = a.get("geelark_phone_id")
        if pid:
            try:
                client.stop_phones([pid])
            except Exception:
                pass
        acc_repo.release_phone_lease(session, account_id)
        logger.warning(
            "reclaim: cleared 'pending' sentinel for account %d "
            "(T1↔T3 crash window)", account_id)
        return "pending_cleared"
    # Re-extend lease so the next cron tick doesn't double-defer
    acc_repo.try_acquire_phone_lease(session, account_id, LEASE_TTL_SECONDS)
    return f"resume:{task_id}"
```

- [ ] **Step 5: Run the test, confirm all four pass**

Run:
```bash
uv run pytest tests/test_warmup_lease_service.py -v 2>&1 | tail -15
```
Expected: 4 PASSED.

- [ ] **Step 6: Run the full test suite to spot regressions in callers of removed functions**

Run:
```bash
uv run pytest -q 2>&1 | tail -20
```
Expected: failures only in tests that still reference `warmup_service._warm_one` / `run_warmup_for_accounts` / `run_warmup`. These will be addressed in Task 8 (caller redirect) and Task 12 (test cleanup).

- [ ] **Step 7: Commit**

```bash
git add app/services/warmup_service.py tests/test_warmup_lease_service.py tests/conftest.py
git commit -m "feat(warmup): start_warmup_one + advance_warmup_one + reclaim_in_flight_one"
```

---

## Task 5: `start_warmup_task` procrastinate task

**Files:**
- Modify: `app/tasks/warmup.py` (start of rewrite — Task 6 and 7 finish it; full replacement at end of Task 7)
- Test: `tests/test_warmup_tasks_lease.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_warmup_tasks_lease.py`:

```python
"""Procrastinate task wiring tests using procrastinate's InMemoryConnector."""
from unittest.mock import patch

import pytest

from app.services import warmup_service


@pytest.mark.asyncio
async def test_start_warmup_task_defers_check_on_success(
        in_memory_procrastinate, warmup_eligible_account, monkeypatch):
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account["id"]

    def fake_start(session, client, account_id, duration_minutes=None):
        from app.repositories import accounts as acc_repo
        acc_repo.try_acquire_phone_lease(session, account_id, 60)
        acc_repo.mark_warmup_task_id(session, account_id, "fake-gtid-7")
        return "fake-gtid-7"

    monkeypatch.setattr(warmup_service, "start_warmup_one", fake_start)
    result = await warmup_tasks.start_warmup_task(account_id=aid)
    assert "fake-gtid-7" in result
    queued = in_memory_procrastinate.jobs
    assert any(j["task_name"] == "check_warmup_status" for j in queued.values())


@pytest.mark.asyncio
async def test_start_warmup_task_phone_busy_returns_skipped(
        in_memory_procrastinate, warmup_eligible_account, monkeypatch):
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account["id"]

    def busy(session, client, account_id, duration_minutes=None):
        raise warmup_service.PhoneBusy("busy")

    monkeypatch.setattr(warmup_service, "start_warmup_one", busy)
    result = await warmup_tasks.start_warmup_task(account_id=aid)
    assert "skipped" in result.lower()
    queued = in_memory_procrastinate.jobs
    assert not any(j["task_name"] == "check_warmup_status" for j in queued.values())
```

(`in_memory_procrastinate` fixture should already exist if other procrastinate task tests do. If not, look at how `test_publish_task.py` or similar sets up its `InMemoryConnector` and replicate.)

- [ ] **Step 2: Run the test, confirm both fail**

Run:
```bash
uv run pytest tests/test_warmup_tasks_lease.py -v 2>&1 | tail -15
```
Expected: 2 FAILED — `app.tasks.warmup` has no `start_warmup_task` attribute.

- [ ] **Step 3: Begin the rewrite of `app/tasks/warmup.py`**

Replace the entire contents of `app/tasks/warmup.py` with the snippet below. (Tasks 6 + 7 add the remaining task functions to this same file; this step gets `start_warmup_task` working.)

```python
"""Warmup procrastinate tasks — lease-based.

Three tasks make up the pipeline:
  start_warmup_task         queue=phones,  no lock=    — T1→T3 + chain check
  check_warmup_status_task  queue=default, no lock=    — one poll → maybe self-defer
  reclaim_in_flight_warmup_task  cron */5  — sweep stale lease + dispatch

Per-task notes live in app/services/warmup_service.py.
"""
import asyncio

from app.db import session_scope
from app.services import warmup_service
from app.services.geelark_service import make_client
from app.tasks import procrastinate_app

CHECK_INTERVAL_SECONDS = 30
MAX_CHECK_ATTEMPTS = 40


def _start_warmup_sync(account_id: int) -> str:
    with session_scope() as session:
        return warmup_service.start_warmup_one(
            session, make_client(), account_id)


@procrastinate_app.task(queue="phones", name="start_warmup")
async def start_warmup_task(account_id: int) -> str:
    try:
        gtid = await asyncio.to_thread(_start_warmup_sync, account_id)
    except warmup_service.PhoneBusy as exc:
        return f"skipped: {exc}"
    if gtid.startswith("skipped"):
        return gtid
    await check_warmup_status_task.configure(
        schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
    ).defer_async(account_id=account_id, task_id=gtid,
                  attempts_remaining=MAX_CHECK_ATTEMPTS)
    return f"INITIATED task {gtid}"


# check_warmup_status_task added in Task 6
# reclaim_in_flight_warmup_task added in Task 7
```

This file won't import cleanly yet — `check_warmup_status_task` is referenced in `defer_async` but not defined. Tasks 6 finishes it.

- [ ] **Step 4: Add stub `check_warmup_status_task` so the file imports**

Append to `app/tasks/warmup.py`:

```python
@procrastinate_app.task(queue="default", name="check_warmup_status")
async def check_warmup_status_task(account_id: int, task_id: str,
                                    attempts_remaining: int) -> str:
    return "stub"  # filled in Task 6
```

- [ ] **Step 5: Run the test, confirm both pass**

Run:
```bash
uv run pytest tests/test_warmup_tasks_lease.py -v 2>&1 | tail -15
```
Expected: 2 PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/tasks/warmup.py tests/test_warmup_tasks_lease.py
git commit -m "feat(warmup): start_warmup_task procrastinate task"
```

---

## Task 6: `check_warmup_status_task` procrastinate task

**Files:**
- Modify: `app/tasks/warmup.py` (replace the stub)
- Test: `tests/test_warmup_tasks_lease.py` (extend)

- [ ] **Step 1: Write the failing test for the three branches**

Append to `tests/test_warmup_tasks_lease.py`:

```python
@pytest.mark.asyncio
async def test_check_warmup_status_self_defers_on_running(
        in_memory_procrastinate, monkeypatch):
    from app.tasks import warmup as warmup_tasks
    monkeypatch.setattr(warmup_service, "advance_warmup_one",
                        lambda s, c, a, t: "running")
    out = await warmup_tasks.check_warmup_status_task(
        account_id=1, task_id="gt-1", attempts_remaining=5)
    assert out == "running"
    queued = in_memory_procrastinate.jobs
    assert sum(1 for j in queued.values()
               if j["task_name"] == "check_warmup_status") == 1


@pytest.mark.asyncio
async def test_check_warmup_status_stops_chain_on_terminal(
        in_memory_procrastinate, monkeypatch):
    from app.tasks import warmup as warmup_tasks
    monkeypatch.setattr(warmup_service, "advance_warmup_one",
                        lambda s, c, a, t: "success")
    out = await warmup_tasks.check_warmup_status_task(
        account_id=1, task_id="gt-1", attempts_remaining=5)
    assert out == "success"
    queued = in_memory_procrastinate.jobs
    assert not any(j["task_name"] == "check_warmup_status" for j in queued.values())


@pytest.mark.asyncio
async def test_check_warmup_status_stops_chain_at_zero_attempts(
        in_memory_procrastinate, monkeypatch):
    from app.tasks import warmup as warmup_tasks
    monkeypatch.setattr(warmup_service, "advance_warmup_one",
                        lambda s, c, a, t: "running")
    out = await warmup_tasks.check_warmup_status_task(
        account_id=1, task_id="gt-1", attempts_remaining=0)
    assert out == "running"
    queued = in_memory_procrastinate.jobs
    assert not any(j["task_name"] == "check_warmup_status" for j in queued.values())
```

- [ ] **Step 2: Run the new tests, confirm they fail (stub returns "stub")**

Run:
```bash
uv run pytest tests/test_warmup_tasks_lease.py -v -k "check_warmup_status" 2>&1 | tail -15
```
Expected: 3 FAILED with `AssertionError` on result mismatch.

- [ ] **Step 3: Replace the stub with the real implementation**

Edit `app/tasks/warmup.py`. Find the stub:

```python
@procrastinate_app.task(queue="default", name="check_warmup_status")
async def check_warmup_status_task(account_id: int, task_id: str,
                                    attempts_remaining: int) -> str:
    return "stub"  # filled in Task 6
```

Replace with:

```python
def _advance_warmup_sync(account_id: int, task_id: str) -> str:
    with session_scope() as session:
        return warmup_service.advance_warmup_one(
            session, make_client(), account_id, task_id)


@procrastinate_app.task(queue="default", name="check_warmup_status")
async def check_warmup_status_task(account_id: int, task_id: str,
                                    attempts_remaining: int) -> str:
    outcome = await asyncio.to_thread(_advance_warmup_sync, account_id, task_id)
    if outcome == "running" and attempts_remaining > 0:
        await check_warmup_status_task.configure(
            schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
        ).defer_async(account_id=account_id, task_id=task_id,
                      attempts_remaining=attempts_remaining - 1)
    return outcome
```

- [ ] **Step 4: Run the tests, confirm all check_warmup_status branches pass**

Run:
```bash
uv run pytest tests/test_warmup_tasks_lease.py -v -k "check_warmup_status" 2>&1 | tail -15
```
Expected: 3 PASSED.

- [ ] **Step 5: Re-run the full file to confirm Task 5's tests still pass**

Run:
```bash
uv run pytest tests/test_warmup_tasks_lease.py -v 2>&1 | tail -15
```
Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/tasks/warmup.py tests/test_warmup_tasks_lease.py
git commit -m "feat(warmup): check_warmup_status_task polls + chains"
```

---

## Task 7: `reclaim_in_flight_warmup_task` cron + test

**Files:**
- Modify: `app/tasks/warmup.py` (append)
- Test: `tests/test_reclaim_in_flight_warmup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reclaim_in_flight_warmup.py`:

```python
"""Cron reclaim behaviour."""
from datetime import datetime, timedelta

import pytest

from app.repositories import accounts as acc_repo
from app.services import warmup_service


@pytest.mark.asyncio
async def test_reclaim_clears_pending_sentinel(
        in_memory_procrastinate, test_session, warmup_eligible_account):
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account["id"]
    # Simulate T1↔T3 crash: lease expired + warmup_task_id='pending'
    acc_repo.update(test_session, aid, {
        "phone_busy_until": datetime.utcnow() - timedelta(minutes=30),
        "warmup_task_id": "pending"})
    test_session.commit()
    out = await warmup_tasks.reclaim_in_flight_warmup_task(timestamp=0)
    assert "cleared=1" in out
    a = acc_repo.get(test_session, aid)
    assert a["warmup_task_id"] is None
    assert a["phone_busy_until"] is None


@pytest.mark.asyncio
async def test_reclaim_resumes_real_task(
        in_memory_procrastinate, test_session, warmup_eligible_account):
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account["id"]
    acc_repo.update(test_session, aid, {
        "phone_busy_until": datetime.utcnow() - timedelta(minutes=30),
        "warmup_task_id": "real-task-xyz"})
    test_session.commit()
    out = await warmup_tasks.reclaim_in_flight_warmup_task(timestamp=0)
    assert "resumed=1" in out
    queued = in_memory_procrastinate.jobs
    check_jobs = [j for j in queued.values() if j["task_name"] == "check_warmup_status"]
    assert len(check_jobs) == 1
    assert check_jobs[0]["args"]["task_id"] == "real-task-xyz"


@pytest.mark.asyncio
async def test_reclaim_ignores_active_lease(
        in_memory_procrastinate, test_session, warmup_eligible_account):
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account["id"]
    acc_repo.update(test_session, aid, {
        "phone_busy_until": datetime.utcnow() + timedelta(minutes=10),
        "warmup_task_id": "real-task-xyz"})
    test_session.commit()
    out = await warmup_tasks.reclaim_in_flight_warmup_task(timestamp=0)
    assert "cleared=0" in out and "resumed=0" in out
```

- [ ] **Step 2: Run the test, confirm all three fail**

Run:
```bash
uv run pytest tests/test_reclaim_in_flight_warmup.py -v 2>&1 | tail -15
```
Expected: 3 FAILED — `reclaim_in_flight_warmup_task` doesn't exist.

- [ ] **Step 3: Append the cron implementation to `app/tasks/warmup.py`**

```python
from datetime import datetime
from sqlalchemy import text


def _list_stranded_warmups_sync() -> list[int]:
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT id FROM accounts "
                "WHERE warmup_task_id IS NOT NULL "
                "  AND phone_busy_until < :now"
            ),
            {"now": datetime.utcnow()},
        ).fetchall()
    return [r[0] for r in rows]


def _reclaim_one_sync(account_id: int) -> str:
    with session_scope() as session:
        return warmup_service.reclaim_in_flight_one(
            session, make_client(), account_id)


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(queue="default", name="reclaim_in_flight_warmup",
                        queueing_lock="reclaim_in_flight_warmup")
async def reclaim_in_flight_warmup_task(timestamp: int) -> str:
    candidates = await asyncio.to_thread(_list_stranded_warmups_sync)
    cleared, resumed = 0, 0
    for aid in candidates:
        outcome = await asyncio.to_thread(_reclaim_one_sync, aid)
        if outcome == "pending_cleared":
            cleared += 1
        elif outcome.startswith("resume:"):
            task_id = outcome.split(":", 1)[1]
            await check_warmup_status_task.configure(
                schedule_in={"seconds": 0},
            ).defer_async(account_id=aid, task_id=task_id,
                          attempts_remaining=MAX_CHECK_ATTEMPTS)
            resumed += 1
    return f"reclaim_in_flight_warmup: cleared={cleared}, resumed={resumed}"
```

- [ ] **Step 4: Run the tests, confirm all three pass**

Run:
```bash
uv run pytest tests/test_reclaim_in_flight_warmup.py -v 2>&1 | tail -15
```
Expected: 3 PASSED.

- [ ] **Step 5: Run all warmup-related tests together to confirm no regression**

Run:
```bash
uv run pytest tests/test_warmup_tasks_lease.py tests/test_reclaim_in_flight_warmup.py tests/test_warmup_lease_service.py tests/test_phone_lease.py -q 2>&1 | tail -10
```
Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/tasks/warmup.py tests/test_reclaim_in_flight_warmup.py
git commit -m "feat(warmup): reclaim_in_flight_warmup cron backstop"
```

---

## Task 8: `daily_warmup_scan` — add lease filter + defer `start_warmup_task`

**Files:**
- Modify: `app/tasks/daily_warmup.py`
- Test: `tests/test_daily_warmup_scan_lease.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_daily_warmup_scan_lease.py`:

```python
"""daily_warmup_scan must skip accounts whose phone lease is held."""
from datetime import datetime, timedelta

import pytest

from app.repositories import accounts as acc_repo


@pytest.mark.asyncio
async def test_scan_skips_lease_held_account(
        in_memory_procrastinate, test_session, warmup_eligible_account):
    from app.tasks.daily_warmup import daily_warmup_scan
    aid = warmup_eligible_account["id"]
    # Make it eligible by cutoff
    acc_repo.update(test_session, aid, {
        "last_warm_at": datetime.utcnow() - timedelta(hours=12),
        "phone_busy_until": datetime.utcnow() + timedelta(minutes=10)})
    test_session.commit()
    summary = await daily_warmup_scan(timestamp=0)
    assert "queued 0" in summary


@pytest.mark.asyncio
async def test_scan_picks_up_lease_expired_account(
        in_memory_procrastinate, test_session, warmup_eligible_account):
    from app.tasks.daily_warmup import daily_warmup_scan
    aid = warmup_eligible_account["id"]
    acc_repo.update(test_session, aid, {
        "last_warm_at": datetime.utcnow() - timedelta(hours=12),
        "phone_busy_until": datetime.utcnow() - timedelta(minutes=30)})
    test_session.commit()
    summary = await daily_warmup_scan(timestamp=0)
    assert "queued 1" in summary
    queued = in_memory_procrastinate.jobs
    assert any(j["task_name"] == "start_warmup" for j in queued.values())
```

- [ ] **Step 2: Run the test, confirm both fail**

Run:
```bash
uv run pytest tests/test_daily_warmup_scan_lease.py -v 2>&1 | tail -15
```
Expected: 2 FAILED (the existing scan still defers `warmup_account_task` and has no lease filter).

- [ ] **Step 3: Replace `app/tasks/daily_warmup.py`**

```python
"""Periodic scan that re-defers start_warmup_task for daily-mode accounts
whose last_warm_at is older than 24h / daily_warmup_count AND whose phone
lease is free.

Lease check (phone_busy_until) is the new gate. Procrastinate locks are gone;
mutual exclusion lives entirely on the lease.
"""
import random
from datetime import datetime, timedelta

from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import text

from app.db import session_scope
from app.repositories import settings as srepo
from app.tasks import procrastinate_app
from app.tasks.warmup import start_warmup_task


@procrastinate_app.periodic(cron="*/10 * * * *")
@procrastinate_app.task(queue="default", name="daily_warmup_scan",
                        queueing_lock="daily_warmup_scan")
async def daily_warmup_scan(timestamp: int) -> str:
    with session_scope() as session:
        n = int(srepo.get_all(session).get("daily_warmup_count") or 4)
        interval_seconds = max(1, 86400 // max(n, 1))
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=interval_seconds)
        rows = session.execute(
            text(
                "SELECT id FROM accounts "
                "WHERE warmup_mode = 'daily' "
                "AND tiktok_logged_in = 1 "
                "AND geelark_phone_id IS NOT NULL "
                "AND (last_warm_at IS NULL OR last_warm_at < :cutoff) "
                "AND (phone_busy_until IS NULL OR phone_busy_until < :now)"
            ),
            {"cutoff": cutoff, "now": now},
        ).fetchall()
        candidate_ids = [r[0] for r in rows]

    deferred = 0
    for aid in candidate_ids:
        try:
            await start_warmup_task.configure(
                queueing_lock=f"start_warmup_{aid}",
                schedule_in={"seconds": random.randint(0, 1800)},
            ).defer_async(account_id=aid)
            deferred += 1
        except AlreadyEnqueued:
            pass
    return f"daily_warmup_scan: queued {deferred} of {len(candidate_ids)}"
```

- [ ] **Step 4: Run the test, confirm both pass**

Run:
```bash
uv run pytest tests/test_daily_warmup_scan_lease.py -v 2>&1 | tail -15
```
Expected: 2 PASSED.

- [ ] **Step 5: Run the full warmup test set**

Run:
```bash
uv run pytest tests/ -q -k "warmup or lease" 2>&1 | tail -15
```
Expected: all PASSED. Note any pre-existing tests that still rely on `warmup_account_task` — they belong in Task 12's cleanup.

- [ ] **Step 6: Commit**

```bash
git add app/tasks/daily_warmup.py tests/test_daily_warmup_scan_lease.py
git commit -m "feat(warmup): daily_warmup_scan gates on phone_busy_until + defers start_warmup"
```

---

## Task 9: `publish_publication_task` — acquire phone lease + drop procrastinate lock

**Files:**
- Modify: `app/services/posting_service.py` (around line 686)
- Modify: `app/tasks/publish.py:73-117`
- Test: `tests/test_publish_lease.py`

- [ ] **Step 1: Read the existing flow**

Run:
```bash
sed -n '655,700p' app/services/posting_service.py
sed -n '73,120p' app/tasks/publish.py
```
Confirm `submit_publication_to_geelark` raises `PhoneBusy` when `account_has_inflight` is true. We're going to replace that check with the lease.

- [ ] **Step 2: Write the failing test**

Create `tests/test_publish_lease.py`:

```python
"""Publish pipeline must acquire phone lease before GeeLark submit and
release it on terminal outcome. Lease is account-scoped, so it serializes
warmup vs publish on the same phone."""
from datetime import datetime, timedelta

import pytest

from app.repositories import accounts as acc_repo
from app.services import posting_service


def test_submit_publication_raises_phone_busy_when_lease_held(
        test_session, sample_publication_with_account):
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    acc_repo.try_acquire_phone_lease(test_session, aid, 60)
    with pytest.raises(posting_service.PhoneBusy):
        posting_service.submit_publication_to_geelark(
            test_session, _fake_client(), publication_id=pub_id)


def test_submit_publication_acquires_lease_on_success(
        test_session, sample_publication_with_account):
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    posting_service.submit_publication_to_geelark(
        test_session, _fake_client(), publication_id=pub_id)
    a = acc_repo.get(test_session, aid)
    assert a["phone_busy_until"] is not None
    assert a["phone_busy_until"] > datetime.utcnow()


def _fake_client():
    class C:
        def start_phones(self, ids): pass
        def stop_phones(self, ids): pass
        def upload_file(self, *a, **kw): return "https://fake/resource"
        def create_video_post_task(self, **kw): return "geelark-task-pub-1"
        def create_youtube_post_task(self, **kw): return "geelark-task-pub-1"
        def create_instagram_post_task(self, **kw): return "geelark-task-pub-1"
        def create_facebook_post_task(self, **kw): return "geelark-task-pub-1"
        def query_tasks(self, ids): return [{"status": 2}]
    return C()
```

Add the fixture to `tests/conftest.py`:

```python
@pytest.fixture
def sample_publication_with_account(test_session, warmup_eligible_account):
    from app.models import Video, Publication
    from app.repositories import publications as pub_repo
    v = Video(stored_path="/tmp/test.mp4", source="local", status="ready")
    test_session.add(v); test_session.commit(); test_session.refresh(v)
    p = Publication(
        video_id=v.id, account_id=warmup_eligible_account["id"],
        platform="tiktok", result="PENDING")
    test_session.add(p); test_session.commit(); test_session.refresh(p)
    return {"publication_id": p.id, "account_id": warmup_eligible_account["id"]}
```

- [ ] **Step 3: Run the test, confirm both fail**

Run:
```bash
uv run pytest tests/test_publish_lease.py -v 2>&1 | tail -15
```
Expected: 2 FAILED — `posting_service.submit_publication_to_geelark` doesn't yet check the lease.

- [ ] **Step 4: Add lease acquire to `submit_publication_to_geelark`**

In `app/services/posting_service.py`, at line 686 (right after the `raise PhoneBusy(f"account {account_id} already has an active task")` block), insert:

```python
    from app.services.warmup_service import LEASE_TTL_SECONDS
    if not acc_repo.try_acquire_phone_lease(
            session, account_id, LEASE_TTL_SECONDS):
        pub_repo.unclaim(session, publication_id)
        raise PhoneBusy(
            f"account {account_id} phone lease already held")
```

Then add `acc_repo.release_phone_lease(session, account_id)` before each of the three post-lease catch blocks (preflight checks above line 686 are pre-lease and don't need release). The exact insertions:

**At line 690 (`caption` failure):**

```python
    try:
        caption = _caption_for_publication(session, pub, v["stored_path"], acc)
    except Exception as e:
        message = _exception_message(e)
        acc_repo.release_phone_lease(session, account_id)   # NEW
        _record_attempt_and_fail(
            session, publication_id,
            stage="caption", source="gemini",
            message=message, notes=f"caption failed: {message}",
        )
        raise
```

**At line 704 (`upload` failure):**

```python
    try:
        res_url = client.upload_file(v["stored_path"], ext)
    except Exception as e:
        message = _exception_message(e)
        acc_repo.release_phone_lease(session, account_id)   # NEW
        _record_attempt_and_fail(
            session, publication_id,
            stage="upload", source="geelark",
            message=message, notes=f"upload failed: {message}",
        )
        raise
```

**At line 718 (`submit` failure):**

```python
    try:
        task_id = _submit(client, pub["platform"],
                          acc["geelark_phone_id"], res_url, caption)
    except Exception as e:
        message = _exception_message(e)
        acc_repo.release_phone_lease(session, account_id)   # NEW
        _record_attempt_and_fail(
            session, publication_id,
            stage="submit", source="geelark",
            message=message, notes=f"submit failed: {message}",
        )
        raise
```

On the success path (after line 727's successful `_submit` return → `pub_repo.mark_initiated` on line 728), do NOT release the lease — `check_publication_status_task` releases it at terminal in Task 10.

- [ ] **Step 5: Run the test, confirm both pass**

Run:
```bash
uv run pytest tests/test_publish_lease.py -v 2>&1 | tail -15
```
Expected: 2 PASSED.

- [ ] **Step 6: Strip `lock=_phone_lock(account_id)` from `publish.py`**

Edit `app/tasks/publish.py`. Find lines around 88-96:

```python
    except posting_service.PhoneBusy as e:
        if submit_attempts_remaining > 0:
            opts = {"schedule_in": {"seconds": SUBMIT_RETRY_INTERVAL_SECONDS}}
            if account_id is not None:
                opts["lock"] = _phone_lock(account_id)
            await publish_publication_task.configure(**opts).defer_async(
                publication_id=publication_id,
                submit_attempts_remaining=submit_attempts_remaining - 1,
            )
            return f"QUEUED: {e}"
        return f"SKIPPED: {e}"
```

Replace with:

```python
    except posting_service.PhoneBusy as e:
        if submit_attempts_remaining > 0:
            await publish_publication_task.configure(
                schedule_in={"seconds": SUBMIT_RETRY_INTERVAL_SECONDS},
            ).defer_async(
                publication_id=publication_id,
                submit_attempts_remaining=submit_attempts_remaining - 1,
            )
            return f"QUEUED: {e}"
        return f"SKIPPED: {e}"
```

Also remove the `from app.tasks.warmup import _phone_lock` import at the top of the file.

Also remove the unused `account_id = await asyncio.to_thread(_read_publication_account_id, publication_id,)` block right before the try — `account_id` was only used for the lock. Leave the helper `_read_publication_account_id` if other code uses it; otherwise delete it.

Run:
```bash
grep -n "_read_publication_account_id\|_phone_lock" app/
```
Delete any newly-orphaned references.

- [ ] **Step 7: Run the publish test set**

Run:
```bash
uv run pytest tests/ -q -k "publish" 2>&1 | tail -15
```
Expected: all PASSED.

- [ ] **Step 8: Commit**

```bash
git add app/services/posting_service.py app/tasks/publish.py tests/test_publish_lease.py tests/conftest.py
git commit -m "feat(publish): acquire phone lease + drop procrastinate lock"
```

---

## Task 10: `check_publication_status_task` releases the phone lease on terminal

**Files:**
- Modify: `app/tasks/publish.py:131-157` OR `app/services/posting_service.py:check_publication_one`
- Test: `tests/test_publish_lease.py` (extend)

- [ ] **Step 1: Decide where the release goes**

Run:
```bash
grep -n "def check_publication_one" app/services/posting_service.py
```
This service function does the GeeLark poll and DB transition. Adding `release_phone_lease` here keeps it next to the rest of the terminal-state handling.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_publish_lease.py`:

```python
def test_check_publication_releases_lease_on_success(
        test_session, sample_publication_with_account, monkeypatch):
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    acc_repo.try_acquire_phone_lease(test_session, aid, 60)
    # Force GeeLark to report success
    class C:
        def query_tasks(self, ids): return [{"status": 3, "shareLink": "https://x"}]
        def stop_phones(self, ids): pass
        def get_task_detail(self, tid): return {"status": 3}
    posting_service.check_publication_one(test_session, C(), pub_id)
    a = acc_repo.get(test_session, aid)
    assert a["phone_busy_until"] is None


def test_check_publication_releases_lease_on_failure(
        test_session, sample_publication_with_account, monkeypatch):
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    acc_repo.try_acquire_phone_lease(test_session, aid, 60)
    class C:
        def query_tasks(self, ids): return [{"status": 4, "failDesc": "boom"}]
        def stop_phones(self, ids): pass
        def get_task_detail(self, tid): return {"status": 4, "failDesc": "boom"}
    posting_service.check_publication_one(test_session, C(), pub_id)
    a = acc_repo.get(test_session, aid)
    assert a["phone_busy_until"] is None
```

- [ ] **Step 3: Run, confirm both fail**

Run:
```bash
uv run pytest tests/test_publish_lease.py::test_check_publication_releases_lease_on_success tests/test_publish_lease.py::test_check_publication_releases_lease_on_failure -v 2>&1 | tail -15
```
Expected: 2 FAILED.

- [ ] **Step 4: Add `release_phone_lease` at the terminal branches**

In `app/services/posting_service.py`, `check_publication_one` has three terminal `pub_repo.set_result` calls at lines 749, 757, 773. After each, add a lease release. The `pub` dict is loaded at line 737 (`pub = pub_repo.get(session, publication_id)`), so `pub["account_id"]` is in scope.

**Line 749 (FAILURE — task not found):**

```python
    if not res:
        pub_repo.set_result(session, publication_id, "FAILURE",
                            "task not found")
        acc_repo.release_phone_lease(session, pub["account_id"])   # NEW
        return "failure"
```

**Line 757 (SUCCESS):**

```python
    if status == 3:
        share_link = (res[0].get("shareLink") or "").strip()
        if share_link:
            pub_repo.set_share_link(session, publication_id, share_link)
        pub_repo.set_result(session, publication_id, "SUCCESS", "completed")
        acc_repo.release_phone_lease(session, pub["account_id"])   # NEW
        _maybe_cleanup_video(session, pub["video_id"])
        return "success"
```

**Line 773 (FAILURE — GeeLark status 4 or 7):**

```python
        pub_repo.set_result(session, publication_id, "FAILURE", reason,
                            fail_code=code, fail_screenshot=shot)
        acc_repo.release_phone_lease(session, pub["account_id"])   # NEW
        _maybe_cleanup_video(session, pub["video_id"])
        return "failure"
```

Do NOT release on the `return "running"` path at line 777 — the polling chain isn't terminal yet.

- [ ] **Step 5: Run, confirm both pass**

Run:
```bash
uv run pytest tests/test_publish_lease.py -v 2>&1 | tail -15
```
Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/services/posting_service.py tests/test_publish_lease.py
git commit -m "feat(publish): release phone lease on terminal status"
```

---

## Task 11: `reclaim_in_flight_publish_task` cron

**Files:**
- Modify: `app/tasks/publish.py` (append)
- Test: `tests/test_publish_lease.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_publish_lease.py`:

```python
@pytest.mark.asyncio
async def test_reclaim_in_flight_publish_defers_check(
        in_memory_procrastinate, test_session, sample_publication_with_account):
    from app.tasks.publish import reclaim_in_flight_publish_task
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    from app.repositories import publications as pub_repo
    pub_repo.update(test_session, pub_id, {
        "result": "INITIATED",
        "geelark_task_id": "stranded-pub-task"})
    acc_repo.update(test_session, aid, {
        "phone_busy_until": datetime.utcnow() - timedelta(minutes=30)})
    test_session.commit()
    out = await reclaim_in_flight_publish_task(timestamp=0)
    assert "1" in out
    queued = in_memory_procrastinate.jobs
    assert any(j["task_name"] == "check_publication_status" for j in queued.values())
```

- [ ] **Step 2: Run, confirm fail**

Run:
```bash
uv run pytest tests/test_publish_lease.py::test_reclaim_in_flight_publish_defers_check -v 2>&1 | tail -15
```
Expected: 1 FAILED.

- [ ] **Step 3: Append the reclaim cron to `app/tasks/publish.py`**

```python
from datetime import datetime
from sqlalchemy import text


def _list_stranded_publish_sync() -> list[tuple[int, str]]:
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT p.id, p.geelark_task_id "
                "FROM publications p JOIN accounts a ON a.id = p.account_id "
                "WHERE p.result = 'INITIATED' "
                "  AND p.geelark_task_id IS NOT NULL "
                "  AND p.geelark_task_id <> 'pending' "
                "  AND a.phone_busy_until < :now"
            ),
            {"now": datetime.utcnow()},
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(queue="default", name="reclaim_in_flight_publish",
                        queueing_lock="reclaim_in_flight_publish")
async def reclaim_in_flight_publish_task(timestamp: int) -> str:
    stranded = await asyncio.to_thread(_list_stranded_publish_sync)
    for pub_id, _gtid in stranded:
        await check_publication_status_task.configure(
            schedule_in={"seconds": 0},
        ).defer_async(publication_id=pub_id, attempts_remaining=MAX_CHECK_RETRIES)
    return f"reclaim_in_flight_publish: resumed {len(stranded)}"
```

- [ ] **Step 4: Run, confirm pass**

Run:
```bash
uv run pytest tests/test_publish_lease.py -v 2>&1 | tail -15
```
Expected: all PASSED.

- [ ] **Step 5: Run the full test suite**

Run:
```bash
uv run pytest -q 2>&1 | tail -15
```
Expected: 385+ passed; new failures only from Task 12's pending cleanup targets.

- [ ] **Step 6: Commit**

```bash
git add app/tasks/publish.py tests/test_publish_lease.py
git commit -m "feat(publish): reclaim_in_flight_publish cron backstop"
```

---

## Task 12: Delete old `warmup_account_task`, `_phone_lock`, `_should_requeue_after_success`

**Files:**
- Modify: `app/tasks/warmup.py` (purge)
- Modify: `app/services/warmup_service.py` (delete any straggler exports)
- Modify: callers (services / api / tests)

- [ ] **Step 1: Find every caller of the doomed symbols**

Run:
```bash
grep -rn "warmup_account_task\|_phone_lock\|_should_requeue_after_success\|enqueue_warmup_for_accounts\|queue_warmup_for_eligible_accounts_sync\|run_warmup_for_accounts\b" app/ tests/ 2>&1 | grep -v "^Binary"
```
Make a list — each call site needs to be either deleted or redirected to `start_warmup_task.defer_async(account_id=...)`.

- [ ] **Step 2: Redirect each caller**

For each caller in the list from Step 1:
- If the caller is "enqueue warmup for account X" → replace with:
  ```python
  await start_warmup_task.configure(
      queueing_lock=f"start_warmup_{aid}",
  ).defer_async(account_id=aid)
  ```
  (or the sync equivalent if the caller is sync)
- If the caller is a removed helper itself (`enqueue_warmup_for_accounts`, `queue_warmup_for_eligible_accounts_sync`) → delete the helper and let its callers do the defer directly
- If the caller is a test of the deleted task → delete the test (the lease tests cover the new behavior)

- [ ] **Step 3: Purge the dead symbols from `app/tasks/warmup.py`**

Confirm the file no longer has any of: `warmup_account_task`, `_phone_lock`, `_should_requeue_after_success`, `_run_warmup_account_sync`, `_run_warmup_all_sync`, `warmup_task` (the old manual scan), `enqueue_warmup_for_accounts`, `queue_warmup_for_eligible_accounts_sync`, `_eligible_warmup_account_ids`. The final file is just: imports, `_start_warmup_sync`, `start_warmup_task`, `_advance_warmup_sync`, `check_warmup_status_task`, `_list_stranded_warmups_sync`, `_reclaim_one_sync`, `reclaim_in_flight_warmup_task`, `_wake_auto_publish_scan`.

Keep `_wake_auto_publish_scan` and the `start_warmup_task` / `check_warmup_status_task` calls to it where they previously fired.

- [ ] **Step 4: Purge dead service functions**

In `app/services/warmup_service.py`, ensure only `start_warmup_one`, `advance_warmup_one`, `reclaim_in_flight_one`, `_utcnow_str`, `LEASE_TTL_SECONDS`, `PhoneBusy` remain. Delete `_warm_one`, `run_warmup_for_accounts`, `run_warmup`.

- [ ] **Step 5: Run the full test suite — must come back green**

Run:
```bash
uv run pytest -q 2>&1 | tail -15
```
Expected: 385+ passed, 0 failed. (Pre-existing baseline.)

- [ ] **Step 6: Commit**

```bash
git add -- app/tasks/warmup.py app/services/warmup_service.py $(git diff --name-only)
git commit -m "refactor: drop warmup_account_task + _phone_lock + sync helpers"
```

---

## Task 13: Delete `reset_stale_doing_jobs` + systemd ExecStartPre cleanup

**Files:**
- Modify: `app/tasks/__init__.py`
- Modify: `docs/production-deploy.md`

- [ ] **Step 1: Delete the function**

In `app/tasks/__init__.py`, delete:
- The `_STALLED_WORKER_HEARTBEAT_SECONDS` constant
- The entire `def reset_stale_doing_jobs() -> None:` function and its imports (lines roughly 41-101 in the current file)

- [ ] **Step 2: Grep for any remaining caller**

Run:
```bash
grep -rn "reset_stale_doing_jobs" .
```
Expected: only the systemd unit on prod references it. There should be NO Python caller.

- [ ] **Step 3: Document the deploy-time systemd edit**

Append to `docs/production-deploy.md`:

```markdown
## Phone-lease migration (2026-06-09)

The first deploy of this migration drops Procrastinate's `lock=phone:acc{id}`
in favour of a per-account DB lease. After upgrading the code:

1. Edit `/etc/systemd/system/mkt-worker.service` on the prod box.
   Remove this line:
   ```
   ExecStartPre=/opt/mkt/.venv/bin/python -c 'from app.tasks import reset_stale_doing_jobs; reset_stale_doing_jobs()'
   ```
2. `systemctl daemon-reload && systemctl restart mkt-worker`.

The lease layer makes the stale-doing cleanup unnecessary; leaving the
ExecStartPre line in place is harmless after the function is removed
(it errors out and systemd continues), but cleaner to drop it.
```

- [ ] **Step 4: Run the full test suite for regression**

Run:
```bash
uv run pytest -q 2>&1 | tail -10
```
Expected: 385+ passed.

- [ ] **Step 5: Commit**

```bash
git add app/tasks/__init__.py docs/production-deploy.md
git commit -m "refactor: drop reset_stale_doing_jobs (superseded by phone lease)"
```

---

## Task 14: Deploy + production cleanup

**Files:**
- (none — runbook-only)

- [ ] **Step 1: Pre-flight verification**

Run:
```bash
uv run pytest -q 2>&1 | tail -10
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build && cd ..
```
Expected: tests green, `frontend/dist/` rebuilt clean.

- [ ] **Step 2: Take the PG backup (per project convention for migrations)**

Run:
```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 \
  "docker exec svm-postgres pg_dump -U postgres mkt | gzip > /root/backups/mkt-pre-lease-$(date +%Y%m%d-%H%M).sql.gz"
ssh -i ~/.ssh/svm_server root@139.196.52.92 "ls -lh /root/backups/ | tail -3"
```
Expected: backup file shows up with non-zero size.

- [ ] **Step 3: Package and ship**

Run the exact tarball + scp + extract + alembic upgrade + restart sequence in `docs/production-deploy.md`. Critically: this deploy **must restart both `mkt` AND `mkt-worker`**.

- [ ] **Step 4: Cleanup the 12 stranded doing jobs**

After the new code is live and the migration is applied:

```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 \
  "docker exec svm-postgres psql -U postgres -d mkt -c \"
UPDATE procrastinate_jobs
SET status = 'cancelled'::procrastinate_job_status
WHERE status = 'doing' AND task_name = 'warmup_account'
  AND scheduled_at < now() - interval '1 hour';
\""
```
Expected: `UPDATE 12` (or whatever the current count is).

- [ ] **Step 5: Remove the systemd ExecStartPre line**

```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92
sed -i '/reset_stale_doing_jobs/d' /etc/systemd/system/mkt-worker.service
systemctl daemon-reload
systemctl restart mkt-worker
systemctl status mkt-worker --no-pager | head -10
exit
```
Expected: worker active (running); no ExecStartPre line in `systemctl cat mkt-worker`.

- [ ] **Step 6: Verify lease behavior for ~10 minutes**

Run periodically:
```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 \
  "docker exec svm-postgres psql -U postgres -d mkt -c \"
SELECT id, profile_name, warmup_count, last_warm_at, phone_busy_until, warmup_task_id
FROM accounts WHERE warmup_mode = 'daily' AND warmup_count < 14
ORDER BY warmup_count, last_warm_at;\""
```

Expected over time:
- Within 10 minutes: `phone_busy_until` populates for stranded accounts as `start_warmup_task` picks them up
- Within 30 minutes: `warmup_count` advances on previously-stuck accounts
- No row remains permanently stuck (lease either active and counting toward expiry, or NULL after terminal)

- [ ] **Step 7: Final smoke**

```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 \
  "docker exec svm-postgres psql -U postgres -d mkt -c \"
SELECT status, count(*) FROM procrastinate_jobs
WHERE task_name IN ('start_warmup', 'check_warmup_status',
                    'reclaim_in_flight_warmup', 'reclaim_in_flight_publish')
GROUP BY status;\""
```
Expected: no `doing` rows older than 25 minutes; healthy mix of `succeeded` / `todo`.

---

## Self-Review

After completing all 14 tasks, the following spec requirements should each map to a task:

| Spec section | Implemented in |
|---|---|
| `accounts.phone_busy_until`, `warmup_task_id` columns + partial index | Task 1 |
| Account model fields | Task 2 |
| Lease primitives (`try_acquire`, `release`, `mark`, `clear`) | Task 3 |
| `start_warmup_one` T1→T3 + crash window release | Task 4 |
| `advance_warmup_one` poll + terminal handling | Task 4 |
| `reclaim_in_flight_one` (service-layer) | Task 4 |
| `start_warmup_task` procrastinate wiring | Task 5 |
| `check_warmup_status_task` chain + terminate | Task 6 |
| `reclaim_in_flight_warmup_task` cron */5 | Task 7 |
| `daily_warmup_scan` lease filter + new defer target | Task 8 |
| `publish_publication_task` lease acquire + drop `lock=` | Task 9 |
| `check_publication_status_task` release on terminal | Task 10 |
| `reclaim_in_flight_publish_task` cron */5 | Task 11 |
| Delete `warmup_account_task`, `_phone_lock`, `_should_requeue_after_success`, `enqueue_warmup_for_accounts`, `queue_warmup_for_eligible_accounts_sync`, `_warm_one`, `run_warmup_for_accounts`, `run_warmup` | Task 12 |
| Delete `reset_stale_doing_jobs` + systemd ExecStartPre | Task 13 |
| Deploy + 12-job cleanup + observation | Task 14 |
