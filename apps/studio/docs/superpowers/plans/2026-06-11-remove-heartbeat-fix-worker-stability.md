# Remove Heartbeat + Fix Worker Stability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the healthchecks/heartbeat code path entirely, wrap `auto_publish_scan` in `asyncio.to_thread` so it stops blocking the worker event loop, and give `mkt-worker.service` a graceful-shutdown configuration so deploys stop being killed with SIGKILL.

**Architecture:** Three independent commits inside one deploy. (A) strip heartbeat from code/tests/docs/.env; (B) refactor `auto_publish_scan_task` to the same `_sync()` + `asyncio.to_thread` shape that `idle_phone_sweeper` already uses; (C) commit `deploy/mkt-worker.service` as the canonical systemd unit with `KillSignal=SIGTERM` + `TimeoutStopSec=300` + procrastinate's `--shutdown-graceful-timeout=240`, then sync it to prod and refresh the deploy doc.

**Tech Stack:** FastAPI · Procrastinate 3.8.1 · PostgreSQL · systemd · uv/pytest.

**Spec:** [docs/superpowers/specs/2026-06-11-remove-heartbeat-fix-worker-stability-design.md](../specs/2026-06-11-remove-heartbeat-fix-worker-stability-design.md)

---

## Pre-flight (do once, before Task 1)

- [ ] **Step 0.1: Confirm clean working tree**

```bash
git status -s
```
Expected: empty output (the spec commit `23a7be0` already landed).

- [ ] **Step 0.2: Establish test baseline**

```bash
uv run pytest -q 2>&1 | tail -5
```
Expected: green run, ≥ 385 passed. Note the exact count — Task 2 and Task 4 will move it (Task 2 deletes 1 assertion + adds 1; Task 4 adds 2 tests).

- [ ] **Step 0.3: Verify the live prod unit really lacks `ExecStartPre`** (informs Task 5/6)

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'systemctl cat mkt-worker.service'
```
Expected: no `ExecStartPre=` line. (If one is still there, surface it and pause — the plan assumes commit `04729f6` already cleaned it.)

---

## Task 1: Delete the heartbeat module and unregister it

**Files:**
- Delete: `app/tasks/heartbeat.py`
- Modify: `app/tasks/__init__.py:40` (drop one line from `import_paths`)
- Modify: `app/config.py:44` (drop the `HEALTHCHECK_URL` constant)

- [ ] **Step 1.1: Delete `app/tasks/heartbeat.py`**

```bash
git rm app/tasks/heartbeat.py
```

- [ ] **Step 1.2: Edit `app/tasks/__init__.py` — remove `"app.tasks.heartbeat",` from `import_paths`**

Open `app/tasks/__init__.py`. Inside the `import_paths=[...]` list, delete exactly this line:

```python
        "app.tasks.heartbeat",
```

After the edit, the list reads (lines 33-46):

```python
    import_paths=[
        "app.tasks.warmup",
        "app.tasks.pic_check",
        "app.tasks.proxy_check",
        "app.tasks.publish",  # added in Task 5
        "app.tasks.auto_publish",
        "app.tasks.daily_warmup",
        "app.tasks.engagement",
        "app.tasks.backfill_thumbnails",
        "app.tasks.discover_tiktok",
        "app.tasks.idle_phone_sweeper",
        "app.tasks.sync_transactions",
    ],
```

- [ ] **Step 1.3: Edit `app/config.py` — remove the `HEALTHCHECK_URL` line**

Delete this line at `app/config.py:44`:

```python
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "")  # empty = skip ping (dev/test default)
```

Result: line 43 (`PROBE_TOKEN = ...`) is now followed directly by the blank line and then `SETTINGS_DEFAULTS = {`.

- [ ] **Step 1.4: Confirm there are zero remaining `HEALTHCHECK_URL` / `heartbeat` references in app code**

```bash
grep -rn "HEALTHCHECK_URL\|heartbeat_task\|tasks.heartbeat\|tasks/heartbeat" app/ 2>/dev/null
```
Expected: empty output. (Hits in `tests/`, `docs/`, `.env.example`, `CLAUDE.md`, `AGENTS.md` are addressed in later tasks — only `app/` must be clean here.)

- [ ] **Step 1.5: Sanity-check the procrastinate App still imports**

```bash
uv run python -c "from app.tasks import procrastinate_app; \
print(sorted(procrastinate_app.tasks.keys()))" 2>&1 | tr ',' '\n' | grep -i heartbeat
```
Expected: empty output (no task named heartbeat).

If you instead see an `ImportError`, you removed the wrong line — revert and re-do Step 1.2.

---

## Task 2: Update `tests/test_tasks_periodic.py` for the deletion

**Files:**
- Modify: `tests/test_tasks_periodic.py` (3 places)

There are three tests that import or reference `heartbeat`:
- `test_periodic_registry_matches_current_worker_schedule` (line 7 imports it, line 23 asserts cron)
- `test_read_only_periodic_tasks_do_not_take_the_phones_lock` (line 57 imports it, line 64 asserts it's in the default queue)
- (No third — those are the two.)

Plus we add one **regression guard** so heartbeat can't be silently re-added.

- [ ] **Step 2.1: Write the failing regression test FIRST (TDD)**

Insert this new test at the very end of `tests/test_tasks_periodic.py`:

```python
def test_heartbeat_module_is_not_registered():
    """Regression guard: heartbeat was removed 2026-06-11 (see spec
    2026-06-11-remove-heartbeat-fix-worker-stability-design.md).
    Re-adding it to import_paths brings back the noisy down/up emails."""
    from app.tasks import procrastinate_app
    assert "app.tasks.heartbeat" not in procrastinate_app.import_paths
```

- [ ] **Step 2.2: Run the new test, confirm it now passes**

```bash
uv run pytest tests/test_tasks_periodic.py::test_heartbeat_module_is_not_registered -v
```
Expected: PASS. (It already passes because Task 1 already removed the import. This step verifies Task 1 actually landed before we touch the other tests.)

- [ ] **Step 2.3: Delete the stale heartbeat references in the two existing tests**

In `test_periodic_registry_matches_current_worker_schedule` (around lines 4-24):

Delete the import:
```python
    import app.tasks.heartbeat  # noqa: F401
```

Delete the cron assertion:
```python
    assert crons["app.tasks.heartbeat.heartbeat_task"] == "*/5 * * * *"
```

In `test_read_only_periodic_tasks_do_not_take_the_phones_lock` (around lines 54-69):

Delete the import:
```python
    from app.tasks.heartbeat import heartbeat_task
```

Delete `heartbeat_task,` from the `for task in (...)` tuple, so it becomes:
```python
    for task in (
        auto_publish_scan_task,
        daily_warmup_scan,
        proxy_check_task,
        check_publication_status_task,
    ):
        assert task.queue == "default"
        assert task.lock is None
```

- [ ] **Step 2.4: Run the periodic-tasks test file**

```bash
uv run pytest tests/test_tasks_periodic.py -v
```
Expected: all tests in this file PASS, including the new `test_heartbeat_module_is_not_registered`. If any test errors with `ModuleNotFoundError: app.tasks.heartbeat`, you missed a reference — re-grep `tests/test_tasks_periodic.py` for `heartbeat`.

- [ ] **Step 2.5: Commit the heartbeat-removal slice (Task 1 + 2 together)**

```bash
git status -s
git add -- \
  app/tasks/__init__.py \
  app/config.py \
  tests/test_tasks_periodic.py
# heartbeat.py is already staged for deletion by `git rm` in Step 1.1
git status -s
git commit -m "$(cat <<'EOF'
refactor(tasks): remove healthchecks heartbeat path

The dead-man's-switch ping became unreliable because it shared the worker
event loop with blocking sync tasks; 24h showed 233/288 ConnectTimeout
failures while healthchecks.io itself was reachable in 181ms. Decision:
delete the heartbeat entirely (no replacement monitor). Followups in
this branch fix the two underlying worker bugs the noise was masking.
EOF
)"
```

Expected: one commit touching 4 paths (heartbeat.py deleted; `__init__.py`, `config.py`, test file modified).

---

## Task 3: Strip the docs and `.env.example`

**Files:**
- Modify: `.env.example:30-32`
- Modify: `CLAUDE.md:146`
- Modify: `AGENTS.md:146`

Historical specs under `docs/superpowers/specs/` and `docs/superpowers/plans/` keep their `HEALTHCHECK_URL` references — they are snapshots, not living docs.

- [ ] **Step 3.1: Delete the healthchecks section from `.env.example`**

Open `.env.example`. Delete these three lines (currently lines 30-32):

```
# healthchecks.io heartbeat URL (Phase 3+). Worker pings every 5 minutes.
# Missed pings trigger email alerts. Leave empty in dev.
HEALTHCHECK_URL=
```

After the edit, line 28 (`DATABASE_URL=`) is followed by a single blank line, then the `# --- Legacy: only used by generate_caption.py CLI mode ---` comment.

- [ ] **Step 3.2: Delete the stale `HEALTHCHECKS_PING_URL` table row from `CLAUDE.md`**

Open `CLAUDE.md`. Delete exactly line 146:

```markdown
| `HEALTHCHECKS_PING_URL` | Optional | Heartbeat target for `tasks/heartbeat.py` |
```

(The variable name was already wrong — code reads `HEALTHCHECK_URL`, not `HEALTHCHECKS_PING_URL`. Either way, both go.)

- [ ] **Step 3.3: Delete the matching row from `AGENTS.md`**

Open `AGENTS.md`. Delete the same line at `AGENTS.md:146`:

```markdown
| `HEALTHCHECKS_PING_URL` | Optional | Heartbeat target for `tasks/heartbeat.py` |
```

- [ ] **Step 3.4: Verify nothing in the live docs still mentions HEALTHCHECK**

```bash
grep -rn "HEALTHCHECK\|heartbeat" .env.example CLAUDE.md AGENTS.md README.md 2>/dev/null
```
Expected: empty output. (Hits inside `docs/superpowers/specs/` or `docs/superpowers/plans/` are fine — historical snapshots.)

- [ ] **Step 3.5: Commit the docs slice**

```bash
git add .env.example CLAUDE.md AGENTS.md
git commit -m "docs: drop healthchecks references after heartbeat removal"
```

---

## Task 4: Refactor `auto_publish_scan_task` to use `asyncio.to_thread`

**Files:**
- Modify: `app/tasks/auto_publish.py` (replace lines 16-48)
- Modify: `tests/test_tasks_periodic.py` (extend the existing `test_auto_publish_scan_skips_videos_without_group_id` is fine; we ALSO add a sync-shape unit test)

**Reference pattern:** `app/tasks/idle_phone_sweeper.py` — the canonical `_sync()` + `await asyncio.to_thread(...)` shape this task adopts.

**Contract preserved:**
- Cron `0 * * * *`, queue `default`, name `auto_publish_scan`, `queueing_lock="auto_publish_scan"` — unchanged.
- Return string still `f"{N} publication(s) deferred"`.
- `wake_auto_publish_scan()` callers (in `app/tasks/publish.py`) keep working — that function is not touched.
- Existing test `test_auto_publish_scan_skips_videos_without_group_id` must still pass without modification.

- [ ] **Step 4.1: Write the new failing unit test for `_auto_publish_scan_sync`**

Append to `tests/test_tasks_periodic.py`:

```python
def test_auto_publish_scan_sync_returns_empty_when_no_publications(session, monkeypatch):
    """The sync helper must be callable from a plain thread, returning
    (pub_ids=[], next_ready=None) when there is nothing to publish and no
    cooled-down candidate to wait on."""
    from app.tasks import auto_publish as ap_module
    from app.services import posting_service

    monkeypatch.setattr(ap_module, "session_scope", lambda: _ScopeCtx(session))
    monkeypatch.setattr(
        posting_service, "check_initiated", lambda s, c: "0 pending")
    monkeypatch.setattr(
        posting_service, "plan_assignments",
        lambda s, *, platform, cooldown_hours: [])
    monkeypatch.setattr(
        posting_service, "next_publish_scan_ready_at",
        lambda s, *, cooldown_hours: None)
    # geelark_service.make_client is called but its return is opaque to the
    # mocked posting_service stubs above.
    from app.services import geelark_service
    monkeypatch.setattr(geelark_service, "make_client", lambda: object())

    pub_ids, next_ready = ap_module._auto_publish_scan_sync()

    assert pub_ids == []
    assert next_ready is None


class _ScopeCtx:
    """Tiny `with` context that yields a pre-built SQLAlchemy session.
    `session_scope()` is the canonical contextmanager in app.db; tests
    that want to use a fixture-built session swap it via monkeypatch."""
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *exc):
        return False
```

- [ ] **Step 4.2: Run the new test and watch it fail with `AttributeError`**

```bash
uv run pytest tests/test_tasks_periodic.py::test_auto_publish_scan_sync_returns_empty_when_no_publications -v
```
Expected: FAIL with `AttributeError: module 'app.tasks.auto_publish' has no attribute '_auto_publish_scan_sync'`. (This proves the test really exercises the new helper.)

- [ ] **Step 4.3: Refactor `app/tasks/auto_publish.py` — write the new version**

Replace the **entire body** of `app/tasks/auto_publish.py` with this. The pre-existing `wake_auto_publish_scan` is preserved verbatim.

```python
"""Periodic scan that finds PENDING publications, claims them with
plan_assignments (PENDING -> QUEUED + account_id), and defers one
publish_publication_task per pairing. Cron: top of every hour.

The sync main body runs in a worker thread via asyncio.to_thread so a
slow GeeLark API call inside check_initiated/plan_assignments cannot
block the procrastinate event loop. Modeled on
app/tasks/idle_phone_sweeper.py. Before the refactor (2026-06-11), this
task was an `async def` that called the sync services directly,
freezing the loop for ~90s/run and starving every other periodic task
(heartbeat, reclaim_*, idle_phone_sweeper) on the same tick."""
import asyncio
import time
from datetime import datetime

from procrastinate.exceptions import AlreadyEnqueued, AppNotOpen

from app.tasks import procrastinate_app
from app.tasks.publish import (
    _defer_auto_publish_scan_at,
    publish_publication_task,
)
from app.db import session_scope


def _auto_publish_scan_sync() -> tuple[list[int], datetime | None]:
    """Synchronous main body.

    Returns:
        (pub_ids, next_ready)
        - pub_ids: publication ids to defer to publish_publication_task.
        - next_ready: if no pub_ids found, the soonest time a cooled-down
          account would be eligible; else None.
    """
    from app.services.platforms import PLATFORMS
    from app.services import geelark_service, posting_service
    from app.repositories import settings as srepo

    with session_scope() as session:
        posting_service.check_initiated(
            session, geelark_service.make_client(),
        )

    pub_ids: list[int] = []
    cooldown_hours = 2.0
    for platform in PLATFORMS:
        with session_scope() as session:
            cooldown_hours = float(
                srepo.get_all(session).get("post_cooldown_hours") or 2
            )
            pub_ids.extend(posting_service.plan_assignments(
                session, platform=platform, cooldown_hours=cooldown_hours,
            ))

    next_ready: datetime | None = None
    if not pub_ids:
        with session_scope() as session:
            next_ready = posting_service.next_publish_scan_ready_at(
                session, cooldown_hours=cooldown_hours,
            )
    return pub_ids, next_ready


@procrastinate_app.periodic(cron="0 * * * *")
@procrastinate_app.task(queue="default", name="auto_publish_scan",
                         queueing_lock="auto_publish_scan")
async def auto_publish_scan_task(timestamp: int):
    pub_ids, next_ready = await asyncio.to_thread(_auto_publish_scan_sync)
    for pid in pub_ids:
        await publish_publication_task.defer_async(publication_id=pid)
    if not pub_ids and next_ready is not None:
        await _defer_auto_publish_scan_at(next_ready)
    return f"{len(pub_ids)} publication(s) deferred"


async def wake_auto_publish_scan() -> bool:
    """Queue one immediate scan, deduped by auto_publish_scan's queueing lock."""
    async def defer_once() -> bool:
        try:
            await auto_publish_scan_task.defer_async(timestamp=int(time.time()))
        except AlreadyEnqueued:
            return True
        return True

    try:
        return await defer_once()
    except AppNotOpen:
        try:
            await procrastinate_app.connector.open_async()
            return await defer_once()
        except Exception:
            return False
    except Exception:
        return False
```

Notes for the reviewer (these are intentional, not omissions):
- The `with session_scope() as session: posting_service.check_initiated(...)` block stays in the sync helper — `check_initiated` itself is `def`, not `async def`.
- `cooldown_hours` is read inside the `for platform` loop because the original code did the same; the value is also reused by `next_publish_scan_ready_at` after the loop.
- `geelark_service.make_client()` is called once per `session_scope()` block, matching the original.

- [ ] **Step 4.4: Re-run the new sync-shape test**

```bash
uv run pytest tests/test_tasks_periodic.py::test_auto_publish_scan_sync_returns_empty_when_no_publications -v
```
Expected: PASS.

- [ ] **Step 4.5: Re-run the pre-existing integration test that exercises the public task**

```bash
uv run pytest tests/test_tasks_periodic.py::test_auto_publish_scan_skips_videos_without_group_id -v
```
Expected: PASS unchanged. (If it errors with `RuntimeError: no running event loop` from `asyncio.to_thread`, you broke the outer `async def` signature — re-check Step 4.3.)

- [ ] **Step 4.6: Run the full test file to confirm no collateral damage**

```bash
uv run pytest tests/test_tasks_periodic.py -v
```
Expected: every test in the file passes.

- [ ] **Step 4.7: Commit the auto_publish refactor slice**

```bash
git add app/tasks/auto_publish.py tests/test_tasks_periodic.py
git commit -m "$(cat <<'EOF'
fix(auto_publish): run scan body in to_thread to unblock event loop

auto_publish_scan_task was async def calling sync posting_service.*
directly, freezing the procrastinate loop ~90s per tick. Same shape now
as idle_phone_sweeper: _auto_publish_scan_sync() does the I/O, the task
body awaits asyncio.to_thread + defer_async only. Public contract
unchanged (cron, queue, name, queueing_lock, return string).
EOF
)"
```

---

## Task 5: Commit `deploy/mkt-worker.service` as the canonical systemd unit

**Files:**
- Create: `deploy/mkt-worker.service`

The repo currently has no version-controlled copy of the prod unit. Adding it removes drift and makes the graceful-shutdown changes auditable.

- [ ] **Step 5.1: Create the `deploy/` directory and the unit file**

```bash
mkdir -p deploy
```

Then write `deploy/mkt-worker.service` with this exact content:

```ini
[Unit]
Description=mkt worker — procrastinate consumer
After=docker.service mkt.service
Requires=docker.service

[Service]
WorkingDirectory=/opt/mkt
EnvironmentFile=/opt/mkt/.env
Environment=PYTHONPATH=/opt/mkt
ExecStart=/opt/mkt/.venv/bin/python -m procrastinate \
    -a app.tasks.procrastinate_app worker \
    --queues phones,default \
    --concurrency=16 \
    --shutdown-graceful-timeout=240
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=300
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

What changed vs. the live prod unit captured in Pre-flight Step 0.3:
- Added `--shutdown-graceful-timeout=240` to `ExecStart` (procrastinate stops accepting new jobs and waits up to 240 s for running jobs).
- Added `KillSignal=SIGTERM` (explicit; matches systemd default but pins it so future systemd version changes can't surprise us).
- Added `TimeoutStopSec=300` (gives systemd 5 minutes before it escalates to SIGKILL — leaves 60 s of headroom over procrastinate's 240 s).

- [ ] **Step 5.2: Validate the unit syntax locally**

```bash
systemd-analyze verify deploy/mkt-worker.service 2>&1 || true
```
On macOS `systemd-analyze` isn't installed — that's fine, the verify just won't run. The real check happens server-side in Task 8 via `systemctl daemon-reload`.

If you do have `systemd-analyze` and it complains about WorkingDirectory/EnvironmentFile not existing, ignore — those paths exist on the server, not here.

- [ ] **Step 5.3: Commit the unit file**

```bash
git add deploy/mkt-worker.service
git commit -m "$(cat <<'EOF'
chore(deploy): version mkt-worker.service with graceful shutdown

Adds --shutdown-graceful-timeout=240, KillSignal=SIGTERM, and
TimeoutStopSec=300 so procrastinate gets up to 4 minutes to finish
in-flight jobs before systemd escalates to SIGKILL. Prod was hitting
the default 90s timeout ~5 times/24h, killing publish/warmup mid-run.
EOF
)"
```

---

## Task 6: Rewrite the "Worker startup cleanup" section of `docs/production-deploy.md`

**Files:**
- Modify: `docs/production-deploy.md` (replace the section spanning lines 160-209)

The current section describes an `ExecStartPre` that was deleted in commit `04729f6 refactor: drop reset_stale_doing_jobs (superseded by phone lease)`. It is misleading. Replace it with a current reference plus the unit-sync deploy step.

- [ ] **Step 6.1: Replace the section**

Open `docs/production-deploy.md`. Locate the section starting at `## Worker startup cleanup (mkt-worker.service)` (around line 160) and ending immediately before `## Phone-lease migration (2026-06-09)` (around line 210).

Replace the entire block with this:

````markdown
## mkt-worker.service (canonical unit)

The authoritative systemd unit for `mkt-worker` lives at
[`deploy/mkt-worker.service`](../deploy/mkt-worker.service) in this
repo. The prod copy at `/etc/systemd/system/mkt-worker.service` must
match it byte-for-byte. Drift = an undetectable risk surface: a deploy
that crashes mid-job because the prod unit silently lost
`--shutdown-graceful-timeout`.

Key invariants the unit guarantees:

- `KillSignal=SIGTERM` + `TimeoutStopSec=300` + procrastinate's
  `--shutdown-graceful-timeout=240` give the worker up to 4 minutes to
  finish in-flight jobs before systemd escalates to SIGKILL. Before
  this was added (2026-06-11), prod hit the default 90 s
  `TimeoutStopSec` and saw ~5 `status=9/KILL` events per day,
  hard-aborting whatever publish/warmup job was in flight.
- `--concurrency=16` paired with the PsycopgConnector pool sized
  `min_size=5, max_size=25` in `app/tasks/__init__.py` — see
  `~/.claude/projects/.../memory/procrastinate-pool-exhaustion.md` for
  the 2026-06-10 incident that forced those numbers.

### Syncing the unit to prod (run on every deploy that changes it)

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  deploy/mkt-worker.service \
  root@139.196.52.92:/etc/systemd/system/mkt-worker.service

ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  systemctl daemon-reload
  systemctl restart mkt-worker
  sleep 5
  systemctl is-active mkt-worker
'
```

If the unit file did NOT change this deploy, skip the scp/daemon-reload
— the normal Extract And Restart step already restarts `mkt-worker`
with the existing unit.
````

- [ ] **Step 6.2: Verify the rewritten doc parses as Markdown and points at the new file**

```bash
grep -n "deploy/mkt-worker.service\|## mkt-worker.service\|reset_stale_doing_jobs" docs/production-deploy.md
```
Expected:
- `## mkt-worker.service (canonical unit)` shows up once.
- `deploy/mkt-worker.service` appears at least twice (link + scp).
- `reset_stale_doing_jobs` no longer appears.

- [ ] **Step 6.3: Commit the doc rewrite**

```bash
git add docs/production-deploy.md
git commit -m "docs(deploy): replace stale ExecStartPre section with canonical unit reference"
```

---

## Task 7: Full local verification before deploying

- [ ] **Step 7.1: Full pytest run**

```bash
uv run pytest -q 2>&1 | tail -10
```
Expected: same green count as the Pre-flight baseline `+1` (the new `test_heartbeat_module_is_not_registered`) `+1` (the new `test_auto_publish_scan_sync_returns_empty_when_no_publications`) `-1` (only the `crons["app.tasks.heartbeat.heartbeat_task"]` assertion line was removed; tests themselves remain — net `+2`).

If a test elsewhere fails, do NOT proceed to deploy. Stop and triage. Most likely culprits:
- Some other module imports `from app.config import HEALTHCHECK_URL` — grep and fix.
- A test imports `app.tasks.heartbeat` — search `tests/` and remove.

- [ ] **Step 7.2: Frontend build (deploy preconditions require fresh dist)**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
cd ..
ls -la frontend/dist/index.html
```
Expected: `index.html` mtime within the last minute.

- [ ] **Step 7.3: Confirm the five-commit slice**

```bash
git log --oneline main..HEAD
```
Expected: exactly 5 commits since the spec commit, in this order from top (latest) to bottom (Task 1+2 share one commit; Tasks 3, 4, 5, 6 each get their own):

1. `docs(deploy): replace stale ExecStartPre section with canonical unit reference`  (Task 6)
2. `chore(deploy): version mkt-worker.service with graceful shutdown`  (Task 5)
3. `fix(auto_publish): run scan body in to_thread to unblock event loop`  (Task 4)
4. `docs: drop healthchecks references after heartbeat removal`  (Task 3)
5. `refactor(tasks): remove healthchecks heartbeat path`  (Task 1+2)

---

## Task 8: Deploy to production

Follow `docs/production-deploy.md` standard flow. The systemd unit change is the only non-routine part — handle it in Step 8.4.

- [ ] **Step 8.1: Package the tarball**

```bash
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-deploy.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini geelark_client.py generate_caption.py generate_comment.py \
  pyproject.toml uv.lock frontend/dist
ls -lh /tmp/mkt-deploy.tar.gz
```
Expected: tarball produced, ~tens of MB.

- [ ] **Step 8.2: Upload the tarball**

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-deploy.tar.gz root@139.196.52.92:/tmp/mkt-deploy.tar.gz
```

- [ ] **Step 8.3: Extract + restart (no schema migration; safe)**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/root/.local/bin:$PATH"
  cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini frontend/dist geelark_client.py generate_caption.py generate_comment.py pyproject.toml uv.lock
  uv sync --no-dev
  uv run alembic upgrade head
  systemctl restart mkt mkt-worker
  sleep 5
  systemctl is-active mkt mkt-worker
  rm -f /tmp/mkt-deploy.tar.gz
'
```
Expected last two lines:
```
active
active
```

- [ ] **Step 8.4: Sync the systemd unit (this deploy DID change it)**

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  deploy/mkt-worker.service \
  root@139.196.52.92:/etc/systemd/system/mkt-worker.service

ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  systemctl daemon-reload
  systemctl restart mkt-worker
  sleep 5
  systemctl is-active mkt-worker
'
```
Expected last line: `active`.

- [ ] **Step 8.5: Optional — strip `HEALTHCHECK_URL` from prod `.env`**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'sed -i.bak "/^HEALTHCHECK_URL=/d" /opt/mkt/.env && grep -c HEALTHCHECK_URL /opt/mkt/.env'
```
Expected: `0` (zero remaining matches). The `.bak` file is left in `/opt/mkt/.env.bak` as a manual rollback.

Skipping this step is harmless — the code no longer reads the variable.

---

## Task 9: Production verification

- [ ] **Step 9.1: heartbeat_task is gone from the worker**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'journalctl -u mkt-worker --since "5 min ago" --no-pager | grep -c heartbeat_task'
```
Expected: `0`.

- [ ] **Step 9.2: Event-loop stalls are gone (or at least dramatically reduced)**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'journalctl -u mkt-worker --since "10 min ago" --no-pager | grep -E "lasted 9[0-9]\." | wc -l'
```
Expected: ideally `0`. Non-zero is acceptable only if those entries are `publish_publication` (its own GeeLark API can legitimately run minutes) AND no two different task names share the same `lasted` value to two decimals.

If `auto_publish_scan` itself still reports `lasted 9X`, the refactor didn't take — re-check that the deployed `app/tasks/auto_publish.py` matches Step 4.3.

- [ ] **Step 9.3: Force a graceful restart and confirm no SIGKILL**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  systemctl restart mkt-worker
  sleep 10
  journalctl -u mkt-worker --since "30 sec ago" --no-pager \
    | grep -E "status=9/KILL|Failed with result|Stopped worker on queues" \
    | tail -10
'
```
Expected:
- At least one `Stopped worker on queues phones, default` line (procrastinate's clean-exit log).
- ZERO `status=9/KILL` lines.
- ZERO `Failed with result 'timeout'` lines.

If you see KILL or timeout, the unit didn't pick up `TimeoutStopSec=300` — `systemctl show mkt-worker.service -p TimeoutStopUSec` should report `5min`. If it reports `1min 30s`, Step 8.4 didn't `daemon-reload`; re-run it.

- [ ] **Step 9.4: Web tier still healthy**

```bash
curl -sS -m 5 http://139.196.52.92:8080/healthz | head -c 500
```
Expected: HTTP 200 with a JSON body that includes `"db":` and `"worker":` keys reporting `ok`.

- [ ] **Step 9.5: Mark the upstream healthchecks check pause/delete (manual)**

This step is **not code** — open https://healthchecks.io in the browser, find the check with UUID `b65f6e53-71de-4736-9a5b-a3c55c36f592` (the one written into `/opt/mkt/.env` historically), and either pause or delete it so it stops emailing.

If you delete it, the prod `.env` variable is also pointless — Step 8.5 already removed it.

---

## Self-Review (run by the plan author before handoff)

- **Spec coverage:** Every section of `docs/superpowers/specs/2026-06-11-remove-heartbeat-fix-worker-stability-design.md` is addressed:
  - Section "改动清单 A": Tasks 1, 2, 3.
  - Section "改动清单 B": Task 4.
  - Section "改动清单 C": Tasks 5, 6.
  - Section "测试策略": Tasks 2, 4, 7.
  - Section "生产验证": Task 9.
  - Section "回滚": each task is its own commit; the slice in Step 7.3 lists them in order for `git revert` selection.
- **Placeholder scan:** none — every code block is concrete, every command names exact paths.
- **Type consistency:** `_auto_publish_scan_sync` signature `() -> tuple[list[int], datetime | None]` matches the test in Step 4.1 and the caller in Step 4.3. `next_ready` is the same variable name in the helper and the task body.
- **Out-of-scope creep guard:** Task 6 touches one deploy-doc section that is directly invalidated by Task 5; not a refactor. No other doc edits.
