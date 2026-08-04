# Phase 3: /healthz + healthchecks.io Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add observability — a `/healthz` endpoint for live checks (DB + worker), and a periodic heartbeat task that pings healthchecks.io so a stalled worker triggers an email alert.

**Architecture:** Two small additions on top of the existing Phase 2 setup. `/healthz` lives in the web tier; `heartbeat_task` is a new Procrastinate periodic task (cron `*/5 * * * *`) that calls the healthchecks.io URL via `httpx`. The URL is provided via `HEALTHCHECK_URL` env var; empty means skip the ping (dev environments).

**Tech Stack:** Existing FastAPI + Procrastinate + httpx (already in deps from Phase 0). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-28-optimize-existing-design.md` — Phase 3 section.

**Predecessors:** Phase 0/1/2 all shipped. Phase 3 starts with two services running in production (`mkt-web`, `mkt-worker`) and PG hosting both business data and Procrastinate state.

**Spec deviations** (from earlier analysis):
- Spec mentions httpx swap "in new code". Production already runs sync `requests`-based code in `geelark_client.py`; Phase 3 does NOT touch it. The heartbeat task uses `httpx` because the periodic task body is async.
- Spec describes the heartbeat ping inside "`status_poll` periodic task every 5 min". Phase 2 didn't create a `status_poll` task (replaced by per-video `check_publish_status_task`). So Phase 3 introduces a dedicated `heartbeat_task` instead — cleaner separation, same effect.

---

## File Structure

**Create:**
- `app/api/health.py` — the `/healthz` endpoint
- `app/tasks/heartbeat.py` — periodic task pinging healthchecks.io

**Modify:**
- `app/main.py` — wire the new health router
- `app/tasks/__init__.py` — add `app.tasks.heartbeat` to `import_paths`
- `app/config.py` — read `HEALTHCHECK_URL` env (default empty string)
- `.env.example` — clean up dead variables, document the healthcheck URL placeholder
- `README.md` — document `/healthz` and HEALTHCHECK_URL

**Unchanged:**
- `app/clients/` — no new async client; spec's hypothetical `geelark_async.py` is YAGNI
- `geelark_client.py` (existing sync requests-based client stays)

---

## Task 1: `/healthz` endpoint

**Files:**
- Create: `app/api/health.py`
- Modify: `app/main.py`

The endpoint reports:
- `ok: bool` — overall health (always `true` if the function executes; non-200 is the actual failure signal)
- `db` — `"ok"` if `SELECT 1` works, `"fail"` otherwise; on fail the endpoint returns HTTP 503
- `worker_last_seen` — Unix timestamp of the most recent `procrastinate_workers` heartbeat, or `null` if none

### Step 1: Create `app/api/health.py`

```python
"""Liveness/readiness endpoint for production monitoring.

GET /healthz returns 200 with a JSON body summarizing DB + worker
state. Returns 503 if the DB itself is unreachable.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlmodel import Session

from app.db import get_session

router = APIRouter()


@router.get("/healthz")
def healthz(
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True}

    # DB ping
    try:
        session.exec(text("SELECT 1")).first()
        payload["db"] = "ok"
    except Exception as e:  # pragma: no cover — hard to trigger in tests
        payload["ok"] = False
        payload["db"] = f"fail: {type(e).__name__}"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return payload

    # Most recent procrastinate worker heartbeat (NULL if no worker yet)
    try:
        row = session.exec(text(
            "SELECT EXTRACT(EPOCH FROM MAX(last_heartbeat)) "
            "FROM procrastinate_workers"
        )).first()
        ts = row[0] if row else None
        payload["worker_last_seen"] = float(ts) if ts is not None else None
    except Exception:
        # If procrastinate tables aren't there (e.g. very stale env) just skip
        payload["worker_last_seen"] = None

    return payload
```

Note on the heartbeat column name: in procrastinate 3.x the column may be `last_heartbeat` or `last_seen`. Adjust based on `\d procrastinate_workers`:
```bash
psql mkt_dev -c "\d procrastinate_workers"
```

### Step 2: Wire the router in `app/main.py`

Find where other routers are included (likely `app.include_router(...)` calls). Add:
```python
from app.api import health as health_api
# ...
app.include_router(health_api.router)
```

### Step 3: Smoke test locally

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run uvicorn app.main:app --host 127.0.0.1 --port 8100 --workers 1 &
SERVER_PID=$!
sleep 4
curl -sS http://127.0.0.1:8100/healthz | python3 -m json.tool
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected JSON:
```json
{
  "ok": true,
  "db": "ok",
  "worker_last_seen": 1779987000.0  // or null if no worker has run yet
}
```

### Step 4: Run tests

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -3
```

Expected: 285 passed, 20 skipped (unchanged — we added an endpoint, didn't touch others).

Optionally, add a tiny test:
```python
# tests/test_healthz.py
def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["db"] == "ok"
    assert "worker_last_seen" in data  # null is fine in tests
```

### Step 5: Commit

```bash
git add app/api/health.py app/main.py tests/test_healthz.py
git commit -m "$(cat <<'EOF'
feat(health): /healthz endpoint for liveness checks (phase 3 task 1)

Returns:
- ok: bool
- db: "ok" or "fail: <err>" (503 if fail)
- worker_last_seen: Unix epoch of latest procrastinate_workers heartbeat, or null

Used by:
- Manual curl during incident response
- healthchecks.io heartbeat task (Task 2) for self-check before pinging
EOF
)"
```

---

## Task 2: heartbeat periodic task

**Files:**
- Create: `app/tasks/heartbeat.py`
- Modify: `app/tasks/__init__.py`
- Modify: `app/config.py`

### Step 1: Add `HEALTHCHECK_URL` to `app/config.py`

Read current config.py; add near the other env reads:
```python
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "")  # empty = skip ping (dev default)
```

(Don't raise on empty — heartbeat is optional. If `.env` doesn't set it, the task simply doesn't ping; the worker keeps running.)

### Step 2: Create `app/tasks/heartbeat.py`

```python
"""Heartbeat periodic task.

Every 5 minutes, ping the healthchecks.io URL configured in .env.
If HEALTHCHECK_URL is empty (dev/test environments), skip silently.

The mere fact that this task runs successfully implies:
- Worker process is alive and pulling from procrastinate_jobs
- PG is reachable
- httpx outbound is working

That's a strong "system OK" signal for healthchecks.io's dead-man's-switch
model: if it stops hearing pings, something is broken.
"""

import httpx

from app.config import HEALTHCHECK_URL
from app.tasks import procrastinate_app


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(queue="default")
async def heartbeat_task(timestamp: int) -> str:
    if not HEALTHCHECK_URL:
        return "heartbeat: skipped (no HEALTHCHECK_URL configured)"
    try:
        # Short timeout — heartbeat shouldn't block the worker
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(HEALTHCHECK_URL)
            r.raise_for_status()
        return f"heartbeat: ok ({r.status_code})"
    except Exception as e:
        # Don't raise — we don't want healthcheck-ping failures to retry
        # forever in the queue. Just log the failure and let the next
        # tick try again.
        return f"heartbeat: failed ({type(e).__name__}: {e})"
```

### Step 3: Add to `app/tasks/__init__.py`

Append to the `import_paths` list:
```python
"app.tasks.heartbeat",
```

So the list becomes 5 entries: warmup, pic_check, proxy_check, publish, heartbeat.

### Step 4: Smoke test the worker registers it

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -m procrastinate -a app.tasks.procrastinate_app worker --queues phones,default 2>&1 | head -15 &
WPID=$!
sleep 5
kill $WPID 2>/dev/null
wait $WPID 2>/dev/null
```

Expected: 4 periodic schedules in the log (warmup, pic_check, proxy_check, heartbeat). The `Periodic job` line for heartbeat should fire ~immediately (since `*/5` will hit within seconds of startup if the current minute is near a 5-min mark).

### Step 5: Verify the actual ping works (only if HEALTHCHECK_URL is set in .env)

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -c "
import os, asyncio, httpx
async def main():
    url = os.environ.get('HEALTHCHECK_URL', '')
    if not url:
        print('no URL set')
        return
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(url)
        print('status:', r.status_code, 'body:', r.text[:100])
asyncio.run(main())
"
```

Expected: 200 status from healthchecks.io. The check in their dashboard should show "received 1 ping" or similar.

### Step 6: Tests + smoke

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -3
```

Expected: 285 passed, 20 skipped (heartbeat task isn't tested directly — adding a test that needs an outbound HTTP mock is overkill for a 10-line ping).

### Step 7: Commit

```bash
git add app/tasks/heartbeat.py app/tasks/__init__.py app/config.py
git commit -m "$(cat <<'EOF'
feat(health): heartbeat periodic task → healthchecks.io (phase 3 task 2)

Every 5 minutes, the worker pings HEALTHCHECK_URL if configured.
Successful execution implies worker + PG + outbound HTTP all healthy
— exactly what healthchecks.io's dead-man's-switch expects.

If HEALTHCHECK_URL is empty (dev/test), the task silently skips.
Failures during ping are logged (return string) but don't raise,
so a transient network blip doesn't trigger a job retry storm.

5 periodic tasks now: warmup, pic_check, proxy_check, heartbeat,
plus the deferred publish chain.
EOF
)"
```

---

## Task 3: `.env.example` cleanup

**Files:**
- Modify: `.env.example`

The `.env.example` accumulated cruft over Phases 0-2. Clean it.

### Step 1: Read current `.env.example`

```bash
cat /Users/Zhuanz/Documents/geelark-studio/.env.example
```

### Step 2: Decide variables to keep vs drop

Keep (still used by current code):
- `GEELARK_TOKEN`
- `GEMINI_API_KEY`
- `DASHBOARD_PASSWORD` (with prominent warning that "changeme" must be replaced)
- `DASHBOARD_SECRET_KEY` (same warning)
- `DRIVE_IMPORT_PROXY`
- `PROBE_URL` and `PROBE_TOKEN`
- `DATABASE_URL` (Phase 1+)
- `HEALTHCHECK_URL` (Phase 3+)

Drop (no longer referenced anywhere in `app/`):
- `SHEET_URL`
- `SERVICE_ACCOUNT_PATH`
- `DRIVE_FOLDER_ID`

Verify the "drop" candidates are truly unused:
```bash
grep -rn "SHEET_URL\|SERVICE_ACCOUNT_PATH\|DRIVE_FOLDER_ID" /Users/Zhuanz/Documents/geelark-studio/app/ /Users/Zhuanz/Documents/geelark-studio/geelark_client.py /Users/Zhuanz/Documents/geelark-studio/generate_caption.py --include="*.py"
```

Expected: empty. If any match exists, decide per-case: either keep the var or delete the using code. (Almost certainly all empty since Phase 0 README cleanup already moved past these.)

### Step 3: Write the cleaned `.env.example`

```bash
# GeeLark — desktop app → avatar → Open API → copy bearer token
GEELARK_TOKEN=

# Gemini API key for caption generation
GEMINI_API_KEY=

# Dashboard login. MUST be changed before deploying to a non-localhost host.
DASHBOARD_PASSWORD=changeme
# Long random string used to sign session cookies. MUST be changed for production.
DASHBOARD_SECRET_KEY=change-this-long-random-string

# Local HTTP proxy for Drive imports (point at the overseas tunnel mouth)
DRIVE_IMPORT_PROXY=

# Overseas proxy-probe service (see probe/README.md). Leave empty to skip
# proxy health checks; the dashboard's dot will then stay grey.
PROBE_URL=
PROBE_TOKEN=

# PostgreSQL connection (Phase 1+).
# Local dev: postgresql+psycopg://<your-macos-user>@127.0.0.1:5432/mkt_dev
# Production: postgresql+psycopg://mkt:<password>@127.0.0.1:5432/mkt
DATABASE_URL=

# healthchecks.io heartbeat URL (Phase 3+). Worker pings every 5 minutes.
# Missed pings trigger email alerts from healthchecks.io. Leave empty in dev.
HEALTHCHECK_URL=
```

### Step 4: Verify `.env` and `.env.example` aren't out of sync structurally

```bash
diff <(grep "^[A-Z]" /Users/Zhuanz/Documents/geelark-studio/.env | cut -d= -f1 | sort) \
     <(grep "^[A-Z]" /Users/Zhuanz/Documents/geelark-studio/.env.example | cut -d= -f1 | sort)
```

If `.env` (gitignored) has extra variables that aren't in `.env.example`, that's a hint the dev environment uses something undocumented. Either add to `.env.example` (if it's a real config knob) or delete from `.env` (if it's vestigial).

### Step 5: Commit

```bash
git add .env.example
git commit -m "$(cat <<'EOF'
chore(env): clean .env.example (phase 3 task 3)

Removed dead variables (Google Sheets/Drive era, deleted long ago):
- SHEET_URL
- SERVICE_ACCOUNT_PATH
- DRIVE_FOLDER_ID

Added documentation for Phase 1/3 additions:
- DATABASE_URL with local + production format examples
- HEALTHCHECK_URL with the worker behavior described
EOF
)"
```

---

## Task 4: Production deploy

The Phase 3 deploy is short — small additions, no schema changes, no new systemd units.

### Step 1: Push commits

```bash
git push origin main
```

### Step 2: Verify production .env has HEALTHCHECK_URL set

Earlier, during planning (and again at deploy if needed), we wrote `HEALTHCHECK_URL=...` into `/opt/mkt/.env`. Verify it's still there:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'grep "^HEALTHCHECK_URL=" /opt/mkt/.env | sed "s/=.*/=<SET>/"'
```

Expected: `HEALTHCHECK_URL=<SET>` (the value is hidden but presence is confirmed).

If missing, add it:
```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'echo "HEALTHCHECK_URL=https://hc-ping.com/b65f6e53-71de-4736-9a5b-a3c55c36f592" >> /opt/mkt/.env'
```

### Step 3: Build + tarball

```bash
cd /Users/Zhuanz/Documents/geelark-studio
export PATH="/opt/homebrew/bin:$PATH"
(cd frontend && npm run build)
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-phase3.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app geelark_client.py generate_caption.py \
  pyproject.toml uv.lock \
  alembic.ini alembic \
  scripts \
  systemd \
  frontend/dist
ls -lh /tmp/mkt-phase3.tar.gz
```

### Step 4: SCP

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-phase3.tar.gz root@139.196.52.92:/tmp/mkt-phase3.tar.gz
```

### Step 5: SSH deploy

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/usr/local/bin:/root/.local/bin:$PATH"
  cd /opt/mkt
  tar -xzf /tmp/mkt-phase3.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini scripts systemd geelark_client.py generate_caption.py pyproject.toml uv.lock frontend/dist
  uv sync --no-dev
  # No alembic changes in Phase 3, but run anyway as a no-op safety:
  uv run alembic upgrade head
  systemctl restart mkt
  sleep 5
  systemctl is-active mkt
  systemctl restart mkt-worker
  sleep 5
  systemctl is-active mkt-worker
  rm -f /tmp/mkt-phase3.tar.gz
'
```

### Step 6: Smoke test `/healthz`

```bash
curl -sS http://139.196.52.92:8080/healthz | python3 -m json.tool
```

Expected JSON:
```json
{
  "ok": true,
  "db": "ok",
  "worker_last_seen": <recent epoch>
}
```

### Step 7: Verify heartbeat fires + ping reaches healthchecks.io

Wait up to 5 minutes, then:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'journalctl -u mkt-worker -n 30 --no-pager | grep -i heartbeat | tail -5'
```

Expected: one or more `heartbeat: ok (200)` lines.

Then open healthchecks.io dashboard and confirm the check is now in "up" state with a recent ping timestamp.

### Step 8: No commit for Task 4 — it's a deploy.

## Phase 3 Completion Checklist

- [ ] `/healthz` returns 200 with `db: "ok"` + `worker_last_seen: <recent>`
- [ ] `mkt-worker` log shows `heartbeat_task` registered + executed
- [ ] healthchecks.io dashboard shows the check as "up" with recent pings
- [ ] 285 passed / 20 skipped maintained
- [ ] `.env.example` cleaned (no SHEET_URL/SERVICE_ACCOUNT_PATH/DRIVE_FOLDER_ID)
- [ ] Documentation in README mentions `/healthz` and HEALTHCHECK_URL

## After Phase 3 lands

The 4-phase incremental optimization is **complete**. All deliverables in `docs/superpowers/specs/2026-05-28-optimize-existing-design.md` shipped (except the explicitly out-of-scope: React frontend, auth, business logic).

Phase 2 follow-up tasks remain (documented in plan amendment 71d2ee7):
- Migrate manual web-tier endpoints (create_profiles, batch publish, manual warmup) to defer to procrastinate tasks
- Drop `OPERATION_LOCK` after the above migration
- Settings-driven `warmup_times` (currently pinned to defaults) if marketing wants customization back

These are separate PRs, not part of this optimization series.
