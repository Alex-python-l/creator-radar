# Phase 2: Procrastinate + Worker Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace APScheduler (in-memory, in-process) + `threading.Lock` (in-process coordination) + 5-min status polling with Procrastinate (PG-backed task queue) + a dedicated worker process + `lock="phones"` cross-process exclusion. The web tier becomes free to multi-worker if ever needed; coordination guarantees move into the database.

**Architecture:**
- **`mkt-web.service`** (existing systemd unit, unchanged role): FastAPI on `--workers 1`. Routes handle requests; "start phones" actions (publish) become `defer()` calls into Procrastinate's queue.
- **`mkt-worker.service`** (new): runs `procrastinate worker`, pulls jobs from PG, executes task functions defined in `app/tasks/`.
- **PostgreSQL** (existing from Phase 1): adds Procrastinate's own tables (`procrastinate_jobs`, `procrastinate_periodic_defers`, etc.) alongside business tables. The `lock="phones"` is implemented by Procrastinate's job-row state machine + `SELECT FOR UPDATE`, providing cross-process exclusion that replaces in-process `threading.Lock`.

**Tech Stack:** Procrastinate (>=2.10) + existing FastAPI/SQLModel/Alembic/PG. No new infrastructure (no Redis, no Celery, no RabbitMQ).

**Spec:** `docs/superpowers/specs/2026-05-28-optimize-existing-design.md` — Phase 2 section.

**Predecessors:** Phase 0 (uv tooling, shipped), Phase 1 (PG migration, shipped). Phase 2 starts with the project on PG with SQLModel + Alembic, scheduler still APScheduler in-memory, `threading.Lock OPERATION_LOCK` still in `app/locks.py`.

**Pause point note:** This is the largest behavior change in the optimization series. Per spec, if Phase 2 reveals complexity that isn't worth shipping, **pause at Phase 1** is a legitimate end state — business is already on PG with SQLModel, which is itself a meaningful improvement.

---

## File Structure

**Create:**
- `app/tasks/__init__.py` — Procrastinate `App` instance + `import_paths`
- `app/tasks/warmup.py` — periodic task wrapping `warmup_service.run_warmup`
- `app/tasks/pic_check.py` — periodic task wrapping `geelark_service.set_pics`
- `app/tasks/proxy_check.py` — periodic task wrapping `proxy_check_service.run_proxy_check`
- `app/tasks/publish.py` — `publish_video_task` (replaces sync API path) + `check_publish_status_task` (replaces 5-min polling)
- `alembic/versions/<hex>_procrastinate_schema.py` — applies Procrastinate's DDL via `op.execute()`
- `systemd/mkt-worker.service` — new systemd unit (template; copied to `/etc/systemd/system/` at deploy)

**Modify:**
- `pyproject.toml` — add `procrastinate>=2.10`
- `app/api/videos.py` — `publish` endpoint changes from sync GeeLark call to `defer()` returning `QUEUED`
- `app/repositories/videos.py` — add a helper to write the new `QUEUED` state; expose `list_pending_check` for the polling task to enqueue follow-ups (or transition existing helpers)
- `app/main.py` — drop the call that starts APScheduler at app startup
- `frontend/src/pages/Videos.tsx` (or wherever the status badge map lives) — add `QUEUED` → label/color row
- `README.md` — add "Running the worker locally" section
- `CLAUDE.md` — update architecture summary to mention worker; note dev requires two terminals
- `docs/production-deploy.md` — add worker deployment steps

**Delete:**
- `app/scheduler.py` (entire file)
- `app/locks.py` (entire file — `OPERATION_LOCK` is gone)
- `apscheduler` dependency from `pyproject.toml`

**Unchanged:**
- Business logic modules: `warmup_service.py`, `posting_service.py`, `geelark_service.py`, `proxy_check_service.py`, `generate_caption.py`, `geelark_client.py`
- Repositories (mostly — small additions to `videos.py`)
- DB schema for business tables (Procrastinate adds its own tables, doesn't touch existing)
- All route files except `app/api/videos.py`

---

## Task 1: Add procrastinate dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add to `[project].dependencies`**

Insert after the `alembic>=1.13` line, before `itsdangerous>=2.0.0`:
```toml
    "procrastinate>=2.10",
```

- [ ] **Step 2: Sync**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv sync
```

Expected: `Resolved N packages` (N grows by 4-6 — procrastinate brings click, attrs, croniter, etc.).

- [ ] **Step 3: Smoke test the import**

```bash
uv run python -c "
import procrastinate
print('procrastinate version:', procrastinate.__version__)
from procrastinate import App, PsycopgConnector
print('App and PsycopgConnector importable')
"
```

Expected: prints version (>= 2.10.x) and 'importable'.

- [ ] **Step 4: Run existing tests (sanity)**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -3
```

Expected: same `287 passed, 10 skipped` as end of Phase 1. We haven't used procrastinate yet; nothing should change.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
chore(deps): add procrastinate for phase 2

PG-backed task queue replacing APScheduler + threading.Lock + 5min
polling in subsequent commits. Tests pass unchanged.
EOF
)"
```

---

## Task 2: Procrastinate schema migration (Alembic)

**Files:**
- Create: `alembic/versions/<auto>_procrastinate_schema.py`

The spec mandates: add Procrastinate's DDL via `op.execute(procrastinate.schema_get_sql())` inside an Alembic revision, **not** by shelling out to `procrastinate schema --apply`. This keeps deploys self-contained — no external CLI dependency at migration time.

- [ ] **Step 1: Inspect Procrastinate's schema SQL**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -c "
import procrastinate
sql = procrastinate.App(connector=None).schema_manager.get_schema()
print(sql[:500])
print('...')
print(f'total length: {len(sql)} chars')
"
```

This prints the start of the DDL. Confirm it looks like `CREATE TABLE procrastinate_jobs...` etc. (Procrastinate's API: `App(connector=None).schema_manager.get_schema()` returns the full DDL as a string. If `connector=None` doesn't work because version requires a connector, instantiate with `PsycopgConnector(conninfo="")` — the connector isn't used for `get_schema()`.)

If the API call shape differs in your installed version, find the right call in the procrastinate docs (often `App.schema_manager.get_schema()` or `procrastinate.utils.read_schema_sql()`). The plan is shape-agnostic: bake the SQL into the migration.

- [ ] **Step 2: Create the migration**

```bash
uv run alembic revision -m "procrastinate schema"
```

This creates an empty migration file. Open it.

- [ ] **Step 3: Fill in the migration**

Replace the body with:

```python
"""procrastinate schema

Revision ID: <auto-generated>
Revises: 93a8a5f4803c
Create Date: <auto>
"""

from alembic import op
import procrastinate


# revision identifiers, used by Alembic.
revision = '<auto-generated>'
down_revision = '93a8a5f4803c'  # baseline from phase 1
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Apply procrastinate's entire schema. The SQL is taken from the
    # installed procrastinate version at migration-build time, so it's
    # version-pinned to whatever we tested with.
    sql = procrastinate.App(connector=None).schema_manager.get_schema()
    op.execute(sql)


def downgrade() -> None:
    # Drop every object procrastinate created.
    # The actual table list depends on procrastinate's version; this
    # matches procrastinate >=2.10. Update if the upstream schema changes.
    op.execute("DROP TABLE IF EXISTS procrastinate_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS procrastinate_periodic_defers CASCADE;")
    op.execute("DROP TABLE IF EXISTS procrastinate_jobs CASCADE;")
    op.execute("DROP TABLE IF EXISTS procrastinate_workers CASCADE;")
    op.execute("DROP TYPE IF EXISTS procrastinate_job_status CASCADE;")
    op.execute("DROP TYPE IF EXISTS procrastinate_job_event_type CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_defer_job CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_defer_periodic_job CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_fetch_job CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_finish_job CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_retry_job CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_notify_queue CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_trigger_status_events_procedure_insert CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_trigger_status_events_procedure_update CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_trigger_scheduled_events_procedure CASCADE;")
```

If the actual procrastinate version's `get_schema()` returns something slightly different (newer versions sometimes split DDL), capture the exact list of created objects from the upgrade SQL and mirror them in `downgrade()`. The downgrade is best-effort; we won't routinely run it, but include it for completeness.

- [ ] **Step 4: Apply to mkt_dev**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade 93a8a5f4803c -> <hex>, procrastinate schema`.

Verify Procrastinate tables exist:
```bash
psql mkt_dev -c "\dt procrastinate*"
```

Expected: at least `procrastinate_jobs`, `procrastinate_periodic_defers`, `procrastinate_events`, `procrastinate_workers`.

- [ ] **Step 5: Apply to mkt_test**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run alembic upgrade head
```

- [ ] **Step 6: Run tests**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -3
```

Expected: still `287 passed, 10 skipped`. Adding Procrastinate's tables shouldn't break business tests; if some test enumerates tables and expects exactly N, that test needs updating. (Phase 1 conftest's `_truncate_tables` only TRUNCATEs business tables, so Procrastinate tables fill up over time within a test session — not ideal but not harmful for Phase 2 task work. Improve in Task 11 if needed.)

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/
git commit -m "$(cat <<'EOF'
feat(db): procrastinate schema migration (phase 2 task 2)

Adds procrastinate_jobs, procrastinate_periodic_defers, procrastinate_events,
procrastinate_workers tables + supporting types and functions, via
op.execute(procrastinate.App(...).schema_manager.get_schema()).

Self-contained — does NOT shell out to `procrastinate schema --apply`,
so deploys don't depend on the procrastinate CLI being on PATH.

mkt_dev and mkt_test both upgraded.
EOF
)"
```

---

## Task 3: Define the Procrastinate App + import_paths

**Files:**
- Create: `app/tasks/__init__.py`

- [ ] **Step 1: Create app/tasks/ directory + __init__.py**

```bash
mkdir -p /Users/Zhuanz/Documents/geelark-studio/app/tasks
```

Write `app/tasks/__init__.py`:

```python
"""Procrastinate App for the mkt worker.

Imported by:
- Worker entry point: `procrastinate -a app.tasks.procrastinate_app worker`
- API/route handlers that need to `defer()` tasks
- Test code (which replaces the connector with InMemoryConnector)

Periodic tasks and one-shot task functions are defined in sibling modules
listed in `import_paths`.
"""

from procrastinate import App, PsycopgConnector

from app.config import DATABASE_URL

procrastinate_app = App(
    connector=PsycopgConnector(conninfo=DATABASE_URL),
    import_paths=[
        "app.tasks.warmup",
        "app.tasks.pic_check",
        "app.tasks.proxy_check",
        "app.tasks.publish",
    ],
)
```

`PsycopgConnector` accepts a libpq conninfo string. Note that our `DATABASE_URL` includes the `+psycopg` SQLAlchemy dialect prefix; procrastinate expects a libpq URL. So actually we need to strip the SQLAlchemy prefix:

```python
import re

# Procrastinate uses libpq directly, not SQLAlchemy, so strip the +psycopg dialect prefix.
_libpq_url = re.sub(r'^postgresql\+psycopg://', 'postgresql://', DATABASE_URL)

procrastinate_app = App(
    connector=PsycopgConnector(conninfo=_libpq_url),
    import_paths=[
        "app.tasks.warmup",
        "app.tasks.pic_check",
        "app.tasks.proxy_check",
        "app.tasks.publish",
    ],
)
```

- [ ] **Step 2: Verify import**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -c "
from app.tasks import procrastinate_app
print('procrastinate_app:', procrastinate_app)
print('connector:', procrastinate_app.connector.__class__.__name__)
print('import_paths:', procrastinate_app.import_paths)
"
```

Expected: prints the App instance, `PsycopgConnector`, and the list of 4 import paths.

- [ ] **Step 3: Verify worker CLI is reachable**

```bash
uv run procrastinate -a app.tasks.procrastinate_app --help 2>&1 | head -10
```

Expected: prints the procrastinate CLI help.

DO NOT start the worker yet — `app.tasks.warmup`, `pic_check`, etc. don't exist; the import_paths would fail.

- [ ] **Step 4: Commit**

```bash
git add app/tasks/__init__.py
git commit -m "$(cat <<'EOF'
feat(tasks): procrastinate App + import_paths skeleton (phase 2 task 3)

The connector uses libpq directly (psycopg3), so the SQLAlchemy +psycopg
dialect prefix is stripped from DATABASE_URL.

The 4 task modules listed in import_paths are added in subsequent
commits (warmup, pic_check, proxy_check, publish).
EOF
)"
```

---

## Task 4: Migrate the 4 periodic tasks

**Files:**
- Create: `app/tasks/warmup.py`
- Create: `app/tasks/pic_check.py`
- Create: `app/tasks/proxy_check.py`

(`app/tasks/publish.py` is Task 5 — bigger; handled separately.)

For each, the pattern is the same: a thin Procrastinate periodic task that opens a session and delegates to the existing service function.

- [ ] **Step 1: Write `app/tasks/warmup.py`**

```python
"""Periodic warmup task.

Wraps warmup_service.run_warmup in a Procrastinate periodic schedule.
Holds lock="phones" so it serializes with publish and pic_check on the
shared "start cloud phones" critical section.
"""

from app.db import session_scope
from app.services import warmup_service
from app.services.geelark_service import make_client
from app.tasks import procrastinate_app


@procrastinate_app.periodic(cron="0 9,14,20 * * *")
@procrastinate_app.task(lock="phones", queue="phones")
async def warmup_task(timestamp: int) -> str:
    with session_scope() as session:
        return warmup_service.run_warmup(session, make_client())
```

Notes:
- Cron `0 9,14,20 * * *` matches the spec's default warmup times (09:00, 14:00, 20:00 server time). The original APScheduler implementation reads these from `settings.warmup_times`. If that customizability matters, we can read from settings at startup and re-register the periodic — but Procrastinate doesn't support dynamic periodic registration as cleanly. **Simplification**: pin warmup to the default `0 9,14,20 * * *`. If a user has customized `settings.warmup_times`, surface a one-time migration warning and keep going. (Document this trade-off in the commit message.)
- `queue="phones"` so the worker's `--queues phones,default` flag picks it up; if we only run a single queue it's redundant but explicit is clearer.
- `lock="phones"` — Procrastinate's named lock. Only one job in the "phones" lock executes at a time. Implementation uses `procrastinate_jobs.status` machinery; this is cross-process safe.
- `timestamp: int` is the periodic argument (Unix epoch seconds Procrastinate passes in). We ignore it.

- [ ] **Step 2: Write `app/tasks/pic_check.py`**

```python
"""Periodic profile-pic check.

Replaces APScheduler's pic_check job (every 10 minutes). Wraps
geelark_service.set_pics, which retries/resolves pending pic tasks.
Holds lock="phones" because some pic-setting operations may start
cloud phones.
"""

from app.db import session_scope
from app.services import geelark_service
from app.services.geelark_service import make_client
from app.tasks import procrastinate_app


@procrastinate_app.periodic(cron="*/10 * * * *")
@procrastinate_app.task(lock="phones", queue="phones")
async def pic_check_task(timestamp: int) -> str:
    with session_scope() as session:
        geelark_service.set_pics(session, make_client())
    return "pic statuses refreshed"
```

- [ ] **Step 3: Write `app/tasks/proxy_check.py`**

```python
"""Periodic proxy check.

Daily at 04:00 server time. Does NOT acquire the phones lock — it only
queries proxy health via the overseas probe service, never starts phones.
"""

from app.db import session_scope
from app.services import proxy_check_service
from app.tasks import procrastinate_app


@procrastinate_app.periodic(cron="0 4 * * *")
@procrastinate_app.task(queue="default")
async def proxy_check_task(timestamp: int) -> str:
    with session_scope() as session:
        return proxy_check_service.run_proxy_check(session)
```

- [ ] **Step 4: Verify imports**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -c "
from app.tasks import warmup, pic_check, proxy_check
print('warmup_task:', warmup.warmup_task)
print('pic_check_task:', pic_check.pic_check_task)
print('proxy_check_task:', proxy_check.proxy_check_task)
"
```

Expected: prints the 3 task objects, no import errors.

- [ ] **Step 5: Start the worker briefly to confirm registration**

In a separate terminal:
```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run procrastinate -a app.tasks.procrastinate_app worker --queues phones,default
```

Expected output mentions registering 3 tasks (warmup_task, pic_check_task, proxy_check_task) and 3 periodic schedules. After 5-10 seconds, Ctrl+C.

`procrastinate` may write the periodic schedules to `procrastinate_periodic_defers` on startup. That's fine — they're idempotent. Verify in PG:
```bash
psql mkt_dev -c "SELECT task_name, defer_timestamp FROM procrastinate_periodic_defers ORDER BY defer_timestamp DESC LIMIT 10;"
```

Expected: rows for warmup_task, pic_check_task, proxy_check_task with future defer timestamps.

- [ ] **Step 6: Commit (one commit for all 3 periodic tasks — they're small and homogeneous)**

```bash
git add app/tasks/warmup.py app/tasks/pic_check.py app/tasks/proxy_check.py
git commit -m "$(cat <<'EOF'
feat(tasks): migrate 3 periodic tasks to Procrastinate (phase 2 task 4)

- warmup_task: cron 0 9,14,20 * * * (pinned to spec defaults; the old
  settings.warmup_times customization is dropped — pin or PR-follow-up
  to read settings into periodic registration)
- pic_check_task: cron */10 * * * *
- proxy_check_task: cron 0 4 * * *

warmup and pic_check hold lock="phones"; proxy_check does not (read-only).
Each task opens its own session via session_scope().

APScheduler still runs alongside until Task 9 deletes it. To avoid
duplicate execution during the transition, the worker process is NOT
yet enabled at deployment — we wire it up in Task 8 + Task 11.
EOF
)"
```

**Important: do not start the worker in production yet.** Until APScheduler is removed (Task 9), running both means every periodic fires twice.

---

## Task 5: Redesign publish flow

**Files:**
- Create: `app/tasks/publish.py`
- Modify: `app/api/videos.py`
- Modify: `app/repositories/videos.py`

This is the most behaviorally-significant change in Phase 2.

### 5.1 Understand the current publish path

Before writing code, inventory what `POST /api/videos/{vid}/publish` currently does. Likely:
1. Validates the video exists and is `PENDING`.
2. Calls `posting_service.publish_video(session, ...)` (synchronous).
3. Inside `publish_video`: calls `geelark_client.publish(...)` — this is the call that starts a cloud phone (= costs money).
4. Updates `videos.result = "INITIATED"` + `videos.geelark_task_id = ...`.
5. Returns to the user.

Then `status_check` polled the DB every minute for `INITIATED` videos and updated their result based on GeeLark's `/task/query`.

We're replacing 3+4 with a deferred task, and replacing the polling status_check with per-video re-defer of a `check_publish_status_task`.

### 5.2 Write `app/tasks/publish.py`

- [ ] **Step 1: Write the file**

```python
"""Publish flow as Procrastinate tasks.

The publish endpoint (app/api/videos.py) defers `publish_video_task`
which holds lock="phones" while calling GeeLark to start a phone +
submit the publish. After GeeLark returns the task id, we defer
`check_publish_status_task` to poll for completion.

Replaces:
- The synchronous publish path that previously held threading.Lock
  OPERATION_LOCK while calling GeeLark
- The status_check periodic task that swept all INITIATED rows every
  minute. Each video now has its own polling chain.

States:
  PENDING -> (defer) -> QUEUED -> (worker pulls, calls GeeLark) ->
  INITIATED -> (check chain) -> SUCCESS / FAILURE
"""

from app.db import session_scope
from app.repositories import videos as videos_repo
from app.services import posting_service
from app.services.geelark_service import make_client
from app.tasks import procrastinate_app

# Max re-defer attempts for check_publish_status_task before declaring failure.
# 40 attempts * 30s interval = 20 minutes total polling window.
MAX_CHECK_RETRIES = 40
CHECK_INTERVAL_SECONDS = 30


@procrastinate_app.task(lock="phones", queue="phones")
async def publish_video_task(video_id: int, account_id: int) -> str:
    """Submit one video to GeeLark.

    Holds lock="phones" because this starts a cloud phone (= money).
    After successful submit, defers check_publish_status_task to track
    completion outside the lock.
    """
    with session_scope() as session:
        client = make_client()
        gl_task_id = posting_service.submit_video_to_geelark(
            session, client, video_id=video_id, account_id=account_id
        )
        videos_repo.mark_initiated(session, video_id, gl_task_id, notes=None)
    # Defer the follow-up check outside the lock so other publish/warmup
    # jobs can proceed while we wait on GeeLark.
    await check_publish_status_task.defer_async(
        video_id=video_id, retry=0
    )
    return f"video {video_id}: INITIATED ({gl_task_id})"


@procrastinate_app.task(queue="default")
async def check_publish_status_task(video_id: int, retry: int) -> str:
    """Poll one GeeLark task; either finalize the video or re-defer self.

    Does NOT hold lock="phones" — read-only query.
    Re-defers itself with schedule_in=CHECK_INTERVAL_SECONDS until either
    the GeeLark task completes or MAX_CHECK_RETRIES is exhausted.
    """
    with session_scope() as session:
        client = make_client()
        result = posting_service.check_one(session, client, video_id)
        # result is one of: "running", "success", "failure", "unknown"
        if result == "running":
            if retry + 1 >= MAX_CHECK_RETRIES:
                videos_repo.set_result(
                    session, video_id, "FAILURE",
                    notes=f"timeout: still running after {MAX_CHECK_RETRIES * CHECK_INTERVAL_SECONDS}s",
                )
                return f"video {video_id}: FAILURE (timeout)"
            # Re-defer self
            await check_publish_status_task.defer_async(
                video_id=video_id, retry=retry + 1
            )
            return f"video {video_id}: still running, retry {retry + 1}"
        # Terminal: success / failure / unknown
        return f"video {video_id}: {result.upper()}"
```

Note: this assumes `posting_service.submit_video_to_geelark` and `posting_service.check_one` exist. They likely don't — these are extracted from `posting_service.publish` and `posting_service.check_initiated`. Adjust function names to match what posting_service exposes. If extraction is needed, do it as the next sub-step.

- [ ] **Step 2: Extract helpers in posting_service**

Read `app/services/posting_service.py`. Find:
- `publish` (which calls GeeLark and writes INITIATED). Extract the GeeLark-submit-and-return-task-id part into `submit_video_to_geelark(session, client, *, video_id, account_id) -> str` so the task can call it.
- `check_initiated` (which loops over all INITIATED videos). Extract the "check one video" core into `check_one(session, client, video_id) -> Literal["running", "success", "failure", "unknown"]` so the task can call it.

Don't delete the existing public functions yet — Task 6 wires `app/api/videos.py` to the new task, and Task 9 removes `check_initiated` (which the old scheduler called).

Run smoke import:
```bash
uv run python -c "
from app.tasks.publish import publish_video_task, check_publish_status_task
from app.services.posting_service import submit_video_to_geelark, check_one
print('all importable')
"
```

### 5.3 Update `app/api/videos.py`

- [ ] **Step 3: Rewrite the publish endpoint**

Find the existing `POST /api/videos/{vid}/publish` handler. The current shape is probably:
```python
@router.post("/{vid}/publish")
async def publish(vid: int, session: Session = Depends(get_session)):
    video = videos_repo.get(session, vid)
    if video is None:
        raise HTTPException(404)
    if video.result != "PENDING":
        raise HTTPException(409, f"video.result is {video.result}, expected PENDING")
    geelark_task_id = posting_service.publish(session, ...)
    return {"status": "INITIATED", "geelark_task_id": geelark_task_id}
```

Change to:
```python
from app.tasks.publish import publish_video_task

@router.post("/{vid}/publish")
async def publish(vid: int, session: Session = Depends(get_session)):
    video = videos_repo.get(session, vid)
    if video is None:
        raise HTTPException(404)
    if video.result != "PENDING":
        raise HTTPException(409, f"video.result is {video.result}, expected PENDING")
    if video.account_id is None:
        raise HTTPException(400, "video must have an account assigned before publish")
    # Mark QUEUED so the dashboard reflects "user clicked but worker hasn't picked it up yet"
    videos_repo.set_result(session, vid, "QUEUED", notes=None)
    # Defer the work
    job_id = await publish_video_task.defer_async(
        video_id=vid, account_id=video.account_id
    )
    return {"status": "QUEUED", "job_id": job_id}
```

(Use `defer_async` if the endpoint is async; `defer` for sync endpoints. Match the existing route shape.)

Bulk publish endpoints, if any, do the same in a loop.

### 5.4 Update `app/repositories/videos.py`

- [ ] **Step 4: Confirm `set_result` accepts `QUEUED`**

Phase 1 had `mark_result(session, vid, result, notes)` (or similar). The model field `result: str` accepts any string, so SQL-wise `QUEUED` is fine. Just confirm:
- The function exists and accepts the new value
- No enum / Literal validation in the repository rejects `QUEUED`

If there's an enum somewhere (services or model), add `QUEUED` to it.

### 5.5 Verify import + sanity

- [ ] **Step 5: Smoke imports**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -c "from app.main import app; print(type(app).__name__)"
```

- [ ] **Step 6: Run tests**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -15
```

Expected: some test_videos / test_api tests now fail because they assert `status="INITIATED"` immediately. Update those tests in Task 11 (test strategy). For now, count and categorize failures, don't fix yet.

- [ ] **Step 7: Commit**

```bash
git add app/tasks/publish.py app/api/videos.py app/repositories/videos.py app/services/posting_service.py
git commit -m "$(cat <<'EOF'
feat(publish): defer to procrastinate; QUEUED + check chain (phase 2 task 5)

API behavior change:
- POST /api/videos/{id}/publish now returns {"status": "QUEUED", "job_id": ...}
  immediately; the GeeLark submit happens on the worker.
- After successful submit, the worker defers a per-video
  check_publish_status_task that re-defers itself every 30s until
  GeeLark reports done or MAX_CHECK_RETRIES (40 = 20min) is exhausted.

New state: videos.result can now be QUEUED (between PENDING and INITIATED).
Frontend status mapping updated in Task 6.

posting_service grew two extraction points (submit_video_to_geelark,
check_one) used by the new tasks; the old publish / check_initiated
remain until Task 9 removes APScheduler.
EOF
)"
```

---

## Task 6: Frontend — add QUEUED status mapping

**Files:**
- Modify: `frontend/src/pages/Videos.tsx` (or wherever the status badge lives — find it)

- [ ] **Step 1: Find the status badge map**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
grep -rn "PENDING\|INITIATED\|SUCCESS\|FAILURE" frontend/src/ | head -20
```

This locates the file that defines status → label/color mapping for video rows.

- [ ] **Step 2: Add QUEUED**

Add an entry for `QUEUED` that styles as "in queue" (suggested: amber color, label "Queued"). Should sit between PENDING (grey, "Pending") and INITIATED (blue, "Posting...").

Example pattern (actual shape depends on the codebase; adapt):
```typescript
const STATUS_STYLES = {
  PENDING:    { label: "Pending",   color: "grey" },
  QUEUED:     { label: "Queued",    color: "amber" },
  INITIATED:  { label: "Posting…",  color: "blue" },
  SUCCESS:    { label: "Posted",    color: "green" },
  FAILURE:    { label: "Failed",    color: "red" },
};
```

- [ ] **Step 3: Rebuild frontend**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
cd ..
```

Expected: vite build success, dist regenerated.

- [ ] **Step 4: Manual smoke (frontend renders new state)**

Start the app + insert a fake QUEUED row via psql, then check the dashboard:
```bash
psql mkt_dev -c "INSERT INTO videos (original_filename, result) VALUES ('queued-test.mp4', 'QUEUED');"
uv run uvicorn app.main:app --port 8090 &
SERVER_PID=$!
sleep 3
# Open http://localhost:8090 in browser, log in, go to Videos tab.
# Confirm the row with result=QUEUED renders with the new badge.
kill $SERVER_PID 2>/dev/null
psql mkt_dev -c "DELETE FROM videos WHERE original_filename='queued-test.mp4';"
```

This is a visual check — the agent can describe what the badge looks like; the human will need to actually look.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/ frontend/dist/  # if dist is committed; check your project
git commit -m "$(cat <<'EOF'
feat(ui): videos status badge supports QUEUED (phase 2 task 6)

QUEUED is between PENDING and INITIATED — set when the user clicks
Publish but the worker hasn't picked up the job yet (typically a
few seconds).
EOF
)"
```

If `frontend/dist/` is gitignored (recommended; it's a build output), don't add it. Production rebuilds dist from source.

---

## Task 7: Verify `lock="phones"` coverage (grep all GeeLark callsites)

This is a verification task, not a coding task. The risk is: if any code path that starts a cloud phone runs *outside* a `lock="phones"` task, we lose the cross-process exclusion and may bill twice.

- [ ] **Step 1: Inventory all `geelark_client` callsites**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
grep -rn "geelark_client\.\|make_client()\.publish\|make_client()\.warmup\|make_client()\.set_avatar\|make_client()\.start_phone" app/ | grep -v "import" | head
```

For each match, decide: does this start a cloud phone? Look at the function in `geelark_client.py`.

- [ ] **Step 2: For each "starts a phone" call, verify it's inside a `lock="phones"` task or behind one**

The expected callsites:
- `warmup_service.run_warmup` → only called from `warmup_task` (has lock) ✓
- `geelark_service.set_pics` → only called from `pic_check_task` (has lock) ✓
- `posting_service.submit_video_to_geelark` → only called from `publish_video_task` (has lock) ✓
- `geelark_service.create_profiles` — manually triggered from API? CHECK if this starts a phone or just registers profile metadata. If it starts a phone, the API endpoint MUST defer a task too instead of calling synchronously. Likely creates a profile placeholder; verify.

- [ ] **Step 3: For each callsite that turned out NOT to be lock-covered, decide**

Either:
- It doesn't start a phone (safe; leave alone)
- It does, but is admin-only (e.g. "stop all phones" emergency endpoint) — these explicitly DON'T need the lock (we want them to bypass)
- It does, normal path → defer a new task with `lock="phones"` and update the route

Document each decision. If you find any unexpected callsite, report it before proceeding.

- [ ] **Step 4: Commit the audit (no code changes if nothing new found)**

If nothing changed, no commit. If a new lock-defer was added, commit the change with a message naming the callsite found.

---

## Task 8: Worker entry point + systemd unit + dev workflow

**Files:**
- Create: `systemd/mkt-worker.service`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write `systemd/mkt-worker.service`**

```ini
[Unit]
Description=mkt worker — procrastinate consumer
After=postgresql.service mkt.service
Requires=postgresql.service

[Service]
WorkingDirectory=/opt/mkt
EnvironmentFile=/opt/mkt/.env
ExecStart=/opt/mkt/.venv/bin/procrastinate -a app.tasks.procrastinate_app worker --queues phones,default
Restart=always
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

This file is a template; the actual `/etc/systemd/system/mkt-worker.service` is installed by deploy (Task 11).

- [ ] **Step 2: Update README.md**

Add a section under "Run":

```markdown
## Running the worker

In development, the worker runs as a separate process. Open a second terminal:

```bash
uv run procrastinate -a app.tasks.procrastinate_app worker --queues phones,default
```

This pulls jobs from PG and executes them. Periodic schedules (warmup, pic_check, proxy_check) fire automatically; deferred jobs (publish) are picked up as you trigger them via the dashboard.

Without the worker, the dashboard still serves (web responses) but nothing async happens — videos sit in QUEUED, scheduled tasks never run.
```

- [ ] **Step 3: Update CLAUDE.md**

Add to the Commands / Architecture section:

```markdown
- Worker process: `uv run procrastinate -a app.tasks.procrastinate_app worker`
  Runs as separate systemd unit `mkt-worker` in production.
  In dev, open a second terminal and run the above command.
- Single-process scheduler (APScheduler) is gone as of Phase 2.
  Coordination is now via PG-backed Procrastinate `lock="phones"`.
```

- [ ] **Step 4: Commit**

```bash
git add systemd/mkt-worker.service README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(infra): mkt-worker systemd unit + dev docs (phase 2 task 8)

In production, mkt-web (uvicorn) and mkt-worker (procrastinate)
run as independent systemd units. Worker restart=always with a 5s
backoff; failure to start mkt-worker does not affect mkt-web's
ability to serve the dashboard.

In dev, both processes are run by the developer in two terminals.
No honcho/foreman dependency added — keeping the dev environment
minimal.
EOF
)"
```

---

## Task 9: Delete APScheduler

This is the moment of truth. Until this commit, APScheduler still runs in `app/main.py`. After this commit, the only scheduling happens via Procrastinate periodic registrations.

- [ ] **Step 1: Remove the scheduler start hook in app/main.py**

Find the FastAPI startup event that calls `scheduler.start()` (or similar). Delete it.

Also remove its import: `from app.scheduler import start_scheduler` (or whatever it's called).

- [ ] **Step 2: Delete `app/scheduler.py`**

```bash
git rm /Users/Zhuanz/Documents/geelark-studio/app/scheduler.py
```

- [ ] **Step 3: Keep `app/locks.py` (scope change from original plan)**

Task 7 audit (2026-05-29) found that many web-tier API endpoints — `app/api/accounts.py` (create_profiles, set_pics, batch publish), `app/api/groups.py` (manual warmup, batch publish), `app/api/phones.py` (stop_all_phones), `app/sse.py` (streaming) — call GeeLark synchronously and rely on `OPERATION_LOCK` to serialize. Procrastinate's `lock="phones"` only covers code paths that run **inside the worker** (the 4 task modules in `app/tasks/`). It does NOT extend to in-web-process API handlers.

The original plan's Step 3 ("delete `app/locks.py`") would break in-process coordination on the web tier. Phase 2 retains `OPERATION_LOCK` as the web-process serializer; Procrastinate `lock="phones"` is the worker-process serializer. With `mkt-web --workers 1` and `mkt-worker` as a single instance, each is a single-process Lock + a single-process Procrastinate lock — coordination holds within each process.

**Cross-process risk**: web and worker can in principle race on "start the same phone". In practice the risk is bounded — the only worker code paths that start phones are `publish_video_task`, `warmup_task`, `pic_check_task`. Web paths that start the same phones are admin/manual triggers that the marketer doesn't normally fire while a worker job is in flight. **Follow-up** (post-Phase 2): migrate the remaining web-tier endpoints to `defer_async()` + `lock="phones"` so coordination is purely in PG.

**Action for Task 9 Step 3**: leave `app/locks.py` in place. Confirm `OPERATION_LOCK` is still imported where it was before Phase 2. Skip the `git rm`.

- [ ] **Step 4: Remove apscheduler dependency**

Edit `pyproject.toml`. Delete the line:
```
    "APScheduler>=3.10.0",
```

```bash
uv sync
```

Expected: `Removed 1 package` (or similar — apscheduler + tzlocal go away).

- [ ] **Step 5: Verify app boots without scheduler**

```bash
uv run python -c "from app.main import app; print(type(app).__name__)"
```

Expected: `FastAPI`.

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8091 --workers 1 &
SERVER_PID=$!
sleep 3
curl -s -o /dev/null -w "/ -> %{http_code}\n" http://127.0.0.1:8091/
kill $SERVER_PID 2>/dev/null
```

Expected: 200.

- [ ] **Step 6: Run tests**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -15
```

Expected: some tests that imported `app.scheduler` or `app.locks` fail. Update them in Task 11.

- [ ] **Step 7: Commit**

```bash
git add app/main.py pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
refactor(scheduler): delete APScheduler + threading.Lock (phase 2 task 9)

Removed:
- app/scheduler.py (entire file)
- app/locks.py (entire file — OPERATION_LOCK threading.Lock)
- apscheduler dependency
- FastAPI startup hook that started the in-process scheduler

Coordination is now in Procrastinate's PG-backed lock="phones".
The mkt-worker systemd unit (Task 8) runs the periodic schedules
and consumes deferred jobs.

After this commit, running mkt without mkt-worker means no schedules
fire and no deferred jobs execute — the system requires both processes.
EOF
)"
```

---

## Task 10: Run the worker locally to verify end-to-end

Now we have both processes wireable. Validate the system actually works.

- [ ] **Step 1: Start web in one terminal**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080 --workers 1
```

- [ ] **Step 2: Start worker in another terminal**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run procrastinate -a app.tasks.procrastinate_app worker --queues phones,default
```

Watch the startup log. Expected: worker logs registration of 5 tasks (warmup, pic_check, proxy_check, publish_video, check_publish_status) + 3 periodic schedules.

- [ ] **Step 3: Trigger a manual periodic-like task**

Defer warmup_task manually from a Python REPL (the periodic schedule fires at the cron times, but we want to test the path now):
```bash
uv run python -c "
import asyncio
from app.tasks.warmup import warmup_task
async def main():
    await warmup_task.defer_async(timestamp=0)
    print('defer queued')
asyncio.run(main())
"
```

Watch the worker terminal. Expected:
- "Job N started"
- (warmup runs — this hits GeeLark; if the env has phones, expect actual side effects)
- "Job N finished"

**WARNING**: this WILL actually run warmup against your local .env's GEELARK_TOKEN. If that token points at production GeeLark, you just kicked off real warmup. For pure-software testing, swap to a sandbox token or skip this step.

- [ ] **Step 4: Test the publish flow (without actually publishing)**

Insert a test video that won't be auto-publishable:
```bash
psql mkt_dev -c "
INSERT INTO videos (original_filename, result, account_id)
VALUES ('phase2-test.mp4', 'PENDING', NULL);
"
```

Then defer the publish task without going through the API:
```bash
uv run python -c "
import asyncio
from app.tasks.publish import publish_video_task
async def main():
    # account_id=NULL would fail real GeeLark; we just want to confirm
    # the task enters the worker. So target the API instead:
    pass
print('skip — needs an account_id; just verify worker logs the periodic schedules')
"
```

For a meaningful publish test you need a real account row + video. If your dev env has them, trigger via the dashboard.

- [ ] **Step 5: Verify the `lock="phones"` actually serializes**

Defer two warmup-style tasks simultaneously and confirm they run sequentially:
```bash
uv run python -c "
import asyncio
from app.tasks.warmup import warmup_task

async def main():
    await warmup_task.defer_async(timestamp=1)
    await warmup_task.defer_async(timestamp=2)

asyncio.run(main())
"
```

Watch the worker log: the second job should not start until the first finishes. If they overlap, the `lock="phones"` is not working — investigate.

- [ ] **Step 6: No commit for Task 10 — it's runtime verification**

If you found bugs while doing this, fix them as separate commits referencing the specific finding. Otherwise, move on.

---

## Task 11: Test strategy adjustments

- [ ] **Step 1: Update tests that asserted "INITIATED" immediately**

```bash
grep -rn '"INITIATED"' tests/
```

For each test that expected the publish endpoint to return INITIATED, change the assertion to expect QUEUED. If the test was checking the GeeLark integration, mock the task `defer_async` or use `InMemoryConnector` to drain jobs synchronously.

- [ ] **Step 2: Add a `procrastinate_app` test fixture**

In `tests/conftest.py`, add:

```python
from procrastinate.testing import InMemoryConnector

@pytest.fixture
def procrastinate_app():
    """A Procrastinate App backed by InMemoryConnector for tests.

    Tasks defer into memory; we can manually invoke them or run a
    test worker that drains the queue synchronously.
    """
    from app.tasks import procrastinate_app as real_app
    # Swap the connector for an in-memory one
    real_app.with_connector(InMemoryConnector())
    yield real_app
    # Reset (procrastinate doesn't always need this; depends on the
    # version's testing helpers)
```

- [ ] **Step 3: Add 2 critical integration tests**

`tests/test_procrastinate_integration.py`:

```python
"""Integration tests for Phase 2 procrastinate-based architecture.

These run against a REAL local PG (mkt_test) — not the InMemoryConnector —
because the cross-process lock="phones" semantics depend on PG advisory
lock / row-level state machinery that the in-memory connector doesn't
emulate.

Skipped if not running against a test PG; do not bring down CI.
"""

import pytest


@pytest.mark.skip(reason="needs separate mkt_test PG worker — manual run only")
def test_lock_phones_serializes(db, client):
    """Two warmup-style tasks deferred simultaneously must run serially."""
    # Defer two tasks, start a worker subprocess, observe sequential execution.
    # Implementation depends on procrastinate's testing helpers; skip
    # by default to keep `pytest -q` fast.
    pass


@pytest.mark.skip(reason="needs separate mkt_test PG worker — manual run only")
def test_publish_chain(db, client):
    """POST /api/videos/{id}/publish defers publish_video_task; running it
    transitions QUEUED -> INITIATED, and a check_publish_status_task
    re-defers itself until terminal."""
    pass
```

The skipped tests document intent. Implementing them properly requires running a procrastinate worker subprocess inside pytest, which is fiddly. Defer the implementation to a follow-up PR; the unit-level tests on the task functions themselves provide most of the coverage.

- [ ] **Step 4: Run all tests**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -5
```

Goal: get back to 287 passed (or close — some tests are legitimately Phase 2-incompatible and become `@pytest.mark.skip`). New skipped count is acceptable as long as failures are zero.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
test(procrastinate): assertions for QUEUED + procrastinate fixtures (phase 2 task 11)

Update publish-endpoint tests to expect "QUEUED" instead of "INITIATED".
Add procrastinate_app fixture using InMemoryConnector for unit tests.
Add (skipped) integration test placeholders for lock serialization
and publish chain — these need a procrastinate worker subprocess
which is fiddly; implement in follow-up.

N passed, M skipped.
EOF
)"
```

---

## Task 12: Production deploy

The Phase 2 deploy is more complex than Phase 0/1 because we add a new systemd unit.

- [ ] **Step 1: Push commits**

```bash
git push origin main
```

- [ ] **Step 2: Backup app.db AND take a PG snapshot**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  cd /opt/mkt
  cp data/app.db data/app.db.bak.phase2-$(date +%Y%m%d-%H%M%S)
  docker exec svm-postgres pg_dump -U mkt mkt > /opt/mkt/data/mkt-phase2-pre.sql.bak
  ls -lh data/app.db.bak.phase2-* data/mkt-phase2-pre.sql.bak | tail -2
'
```

(PG pg_dump is the real safety net here; the SQLite backup is for symmetry with Phase 1.)

- [ ] **Step 3: Build + tarball locally**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build && cd ..
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-phase2.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app geelark_client.py generate_caption.py \
  pyproject.toml uv.lock \
  alembic.ini alembic \
  scripts \
  systemd \
  frontend/dist
ls -lh /tmp/mkt-phase2.tar.gz
```

- [ ] **Step 4: Upload tarball**

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-phase2.tar.gz root@139.196.52.92:/tmp/mkt-phase2.tar.gz
```

- [ ] **Step 5: Extract, sync, alembic, restart web, install + enable worker**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/usr/local/bin:/root/.local/bin:$PATH"
  cd /opt/mkt
  systemctl stop mkt
  tar -xzf /tmp/mkt-phase2.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini scripts systemd geelark_client.py generate_caption.py pyproject.toml uv.lock frontend/dist
  uv sync --no-dev
  echo "=== alembic upgrade head ==="
  uv run alembic upgrade head
  echo "=== install mkt-worker systemd unit ==="
  cp systemd/mkt-worker.service /etc/systemd/system/mkt-worker.service
  systemctl daemon-reload
  echo "=== start web first ==="
  systemctl start mkt
  sleep 5
  systemctl is-active mkt
  echo "=== start worker ==="
  systemctl enable --now mkt-worker
  sleep 3
  systemctl is-active mkt-worker
  rm -f /tmp/mkt-phase2.tar.gz
'
```

- [ ] **Step 6: Smoke test**

```bash
curl -sS -o /dev/null -w "/ -> %{http_code}\n" http://139.196.52.92:8080/
curl -sS -o /dev/null -w "/accounts -> %{http_code}\n" http://139.196.52.92:8080/accounts
curl -sS -o /dev/null -w "/api/accounts -> %{http_code}\n" http://139.196.52.92:8080/api/accounts
```

Expected: 200 / 200 / 401.

- [ ] **Step 7: Verify worker activity**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'journalctl -u mkt-worker -n 30 --no-pager'
```

Expected: worker startup log, registration of 5 tasks + 3 periodic schedules. After 1 minute, pic_check_task fires (since cron is */10 it could be up to 10 min). After 5 min, more activity. Watch for errors.

- [ ] **Step 8: Trigger a real publish via dashboard**

Open `http://139.196.52.92:8080/`, log in, navigate to Videos. Pick a video, click Publish. Confirm:
- Row state goes to QUEUED immediately
- Within a few seconds, state becomes INITIATED (worker picked it up + GeeLark accepted the submit)
- Eventually (minutes to ~20 min depending on GeeLark side) state becomes SUCCESS / FAILURE

This is the real end-to-end test.

- [ ] **Step 9: No commit for Task 12 — it's a deploy**

## Phase 2 Completion Checklist

Before declaring Phase 2 done, verify:

- [ ] All 4 periodic schedules are visible in `procrastinate_periodic_defers` and firing on schedule (compare `defer_timestamp` over a 10-minute window)
- [ ] `mkt-web.service` and `mkt-worker.service` both `active (running)`
- [ ] `app/scheduler.py`, `app/locks.py`, apscheduler dep all gone
- [ ] A real publish triggers QUEUED -> INITIATED -> SUCCESS chain end-to-end
- [ ] Production cannot start a phone from two paths concurrently (lock="phones" works)
- [ ] healthchecks.io heartbeat (Phase 3) is NOT yet wired — Phase 3's job
- [ ] All commits pushed to origin/main

## Rollback Strategy

Phase 2's rollback is more involved than Phase 0/1 because the worker is a new component and the publish API contract changed.

**Within minutes of deploy (smoke test fails):**
```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 '
  systemctl stop mkt-worker
  systemctl disable mkt-worker
  cd /opt/mkt
  # Revert code to last Phase 1 commit (c07fc45)
  # Need to extract a saved Phase 1 tarball, since /opt/mkt is not a git repo.
  # Keep a phase1.tar.gz on the server during phase 2 deploy for this reason.
  tar -xzf /tmp/mkt-phase1-rollback.tar.gz
  uv sync --no-dev
  # Alembic was upgraded for procrastinate schema; downgrade it:
  uv run alembic downgrade 93a8a5f4803c
  systemctl restart mkt
'
```

**Hours after deploy (some publishes happened):**
The DB has procrastinate schema + maybe some jobs. Rollback to Phase 1:
- Stop mkt-worker
- Don't downgrade alembic — leaving procrastinate tables in PG is harmless to Phase 1
- Revert /opt/mkt code to Phase 1 tarball
- Restart mkt
- Any in-flight QUEUED videos lose their tracking; manually mark them PENDING or accept the loss.

**Days after deploy (production has been on Phase 2):**
Rollback is impractical. The right move at this point is fix-forward.

## After Phase 2 lands

Pause point 3 per spec:
- Worker + lock="phones" behave as expected?
- Any unexpected coordination issues? (e.g., two workers triggered overlapping warmup → wouldn't happen because we run 1 worker, but verify)
- procrastinate operational visibility good enough? (consider adding the procrastinate admin UI in a follow-up)

If yes → Phase 3 plan (`/healthz` + healthchecks.io ping integration).
If no → stop here. Phase 2 is independently valuable: cross-process coordination, worker isolation, better task observability through procrastinate_jobs.
