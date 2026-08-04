# GeeLark Task-Done Callback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut "task done → stop_phones" latency from ~10–30s (polling) to ~1s by registering a GeeLark callback URL that pushes task-state changes to a new `/api/geelark/callback` endpoint. Keep polling as a fallback.

**Architecture:** Phase 0 ships a log-only endpoint to discover GeeLark's undocumented callback payload schema. Phase 1 adds parsing + a `finalize_task_callback` worker job (queue=phones) that reuses the existing warmup/publish/engagement finalize helpers — so callback and polling converge on identical bookkeeping and the second-arriver no-ops. Phase 2 loosens polling intervals to a fallback density. Phase 3 adds a heartbeat monitor.

**Tech Stack:** FastAPI + SQLModel + Procrastinate + PostgreSQL 16 (backend). Pytest. `requests` for the `/open/v1/callback/set` registration call.

**Spec:** [docs/superpowers/specs/2026-06-09-geelark-task-callback-design.md](../specs/2026-06-09-geelark-task-callback-design.md)

---

## Task 0: Pre-flight

**Files:** none modified.

- [ ] **Step 1: Confirm single Alembic head**

Run: `uv run alembic heads`
Expected: `566020ad4ad1 (head)` — exactly one line. If more than one, merge first with `uv run alembic merge heads -m "merge before callback" -n <slug>` and re-run.

- [ ] **Step 2: Record baseline test count**

Run: `uv run pytest -q --collect-only 2>&1 | tail -2`
Expected: `669 tests collected`. New tests added in this plan must end with ≥ 669 + (count of new tests) passing.

- [ ] **Step 3: Confirm `.env` has `GEELARK_TOKEN`**

Run: `grep -E '^GEELARK_TOKEN=' .env | wc -l`
Expected: `1`.

No commit for Task 0.

---

## Task 1: Settings — `GEELARK_CALLBACK_TOKEN`

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Add the env var to `app/config.py`**

Find the block where other GeeLark-related env vars are read (search for `GEELARK_TOKEN`). Add adjacent:

```python
GEELARK_CALLBACK_TOKEN = os.environ.get("GEELARK_CALLBACK_TOKEN", "")
```

Place it immediately after `GEELARK_TOKEN = os.environ.get("GEELARK_TOKEN", "")`.

- [ ] **Step 2: Document in `.env.example`**

Append to `.env.example` under the GeeLark section:

```
# Random 32+ char shared secret. The callback URL is appended ?token=<value>;
# /api/geelark/callback rejects requests without a matching token. Generate
# fresh per environment, e.g. `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
GEELARK_CALLBACK_TOKEN=
```

- [ ] **Step 3: Commit**

```bash
git add app/config.py .env.example
git commit -m "feat(callback): add GEELARK_CALLBACK_TOKEN env var"
```

---

## Task 2: Alembic migration — `geelark_callback_log`

**Files:**
- Create: `alembic/versions/<rev>_geelark_callback_log.py`
- Modify: `tests/conftest.py` (`_TABLES` list)

- [ ] **Step 1: Create the migration file**

File `alembic/versions/abcd1234ef56_geelark_callback_log.py`:

```python
"""geelark_callback_log table

Revision ID: abcd1234ef56
Revises: 566020ad4ad1
Create Date: 2026-06-09 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "abcd1234ef56"
down_revision: Union[str, Sequence[str], None] = "566020ad4ad1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geelark_callback_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("received_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("source_ip", sa.String(length=45)),
        sa.Column("headers_json", postgresql.JSONB(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("parsed_task_id", sa.String(length=32)),
        sa.Column("parsed_status", sa.Integer()),
        sa.Column("handled", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("handled_at", sa.DateTime()),
        sa.Column("handle_outcome", sa.Text()),
    )
    op.create_index("idx_gcl_task", "geelark_callback_log", ["parsed_task_id"])
    op.create_index(
        "idx_gcl_received", "geelark_callback_log",
        [sa.text("received_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_gcl_received", table_name="geelark_callback_log")
    op.drop_index("idx_gcl_task", table_name="geelark_callback_log")
    op.drop_table("geelark_callback_log")
```

- [ ] **Step 2: Apply locally + to test DB**

Run:
```bash
uv run alembic upgrade head
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run alembic upgrade head
```
Expected for both: `Running upgrade 566020ad4ad1 -> abcd1234ef56, geelark_callback_log table`.

- [ ] **Step 3: Extend the truncate list in `tests/conftest.py`**

Find the `_TABLES = (...)` literal (around line 52). Change it to:

```python
_TABLES = (
    "publication_attempts, engagement_runs, engagement_tasks, videos, "
    "accounts, groups, settings, job_runs, discovered_videos, "
    "geelark_transactions, geelark_callback_log"
)
```

- [ ] **Step 4: Confirm full suite still passes**

Run: `uv run pytest -q`
Expected: still `669 passed` (no new tests yet).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/abcd1234ef56_geelark_callback_log.py tests/conftest.py
git commit -m "feat(callback): geelark_callback_log table"
```

---

## Task 3: SQLModel + repository — callback log

**Files:**
- Modify: `app/models.py`
- Create: `app/repositories/callback_log.py`
- Create: `tests/test_repositories_callback_log.py`

- [ ] **Step 1: Add the SQLModel definition**

In `app/models.py`, just above the `class DiscoveredVideo(...)` definition (or any other natural spot in the file), add:

```python
class GeelarkCallbackLog(SQLModel, table=True):
    __tablename__ = "geelark_callback_log"
    __table_args__ = (
        Index("idx_gcl_task", "parsed_task_id"),
        Index("idx_gcl_received", text("received_at DESC")),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    received_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    source_ip: Optional[str] = Field(default=None, max_length=45)
    headers_json: dict = Field(
        sa_column=Column(JSON, nullable=False),
    )
    body_text: str = Field(sa_column=Column(Text, nullable=False))
    parsed_task_id: Optional[str] = Field(default=None, max_length=32)
    parsed_status: Optional[int] = None
    handled: int = Field(default=0, nullable=False)
    handled_at: Optional[datetime] = None
    handle_outcome: Optional[str] = Field(default=None, sa_column=Column(Text))
```

Note: `JSON` is already imported from `sqlalchemy` at the top of `models.py`; `text`, `Column`, `Index`, `Text` likewise. Use SQLModel `int` for the `handled` column (matches existing `tiktok_logged_in` style); the underlying PG column is boolean.

Actually use `bool` to keep the type accurate; SQLAlchemy maps it correctly:

```python
    handled: bool = Field(default=False, nullable=False)
```

- [ ] **Step 2: Write failing repository test**

File `tests/test_repositories_callback_log.py`:

```python
"""callback_log repo: insert raw payloads, look up by task_id, prune."""

from datetime import datetime, timedelta

from app.repositories import callback_log as cb_repo


def test_record_inserts_and_returns_id(db):
    rid = cb_repo.record(
        db,
        source_ip="1.2.3.4",
        headers={"x-foo": "bar"},
        body_text='{"taskId":"623"}',
    )
    assert isinstance(rid, int) and rid > 0
    row = cb_repo.get(db, rid)
    assert row["source_ip"] == "1.2.3.4"
    assert row["body_text"] == '{"taskId":"623"}'
    assert row["handled"] is False


def test_update_parsed_and_handled(db):
    rid = cb_repo.record(
        db, source_ip=None, headers={}, body_text="{}",
    )
    cb_repo.set_parsed(db, rid, task_id="task-x", status=3)
    cb_repo.mark_handled(db, rid, outcome="finalized publish")
    row = cb_repo.get(db, rid)
    assert row["parsed_task_id"] == "task-x"
    assert row["parsed_status"] == 3
    assert row["handled"] is True
    assert row["handle_outcome"] == "finalized publish"


def test_prune_older_than(db):
    rid_old = cb_repo.record(db, source_ip=None, headers={}, body_text="old")
    rid_new = cb_repo.record(db, source_ip=None, headers={}, body_text="new")
    # backdate the "old" row past the cutoff
    from sqlalchemy import text as _t
    db.execute(_t(
        "UPDATE geelark_callback_log SET received_at = :ts WHERE id = :id"
    ), {"ts": datetime.utcnow() - timedelta(days=10), "id": rid_old})
    db.commit()

    deleted = cb_repo.prune_older_than(db, datetime.utcnow() - timedelta(days=7))
    assert deleted == 1
    assert cb_repo.get(db, rid_old) is None
    assert cb_repo.get(db, rid_new) is not None
```

Run: `uv run pytest tests/test_repositories_callback_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.repositories.callback_log'`.

- [ ] **Step 3: Implement the repository**

File `app/repositories/callback_log.py`:

```python
"""Repository for `geelark_callback_log`.

Insert raw inbound bodies (Phase 0 just logs them), update parsed fields
after the parser handles a row, and prune anything older than the
retention window.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session

from app.models import GeelarkCallbackLog


def _dump(row: GeelarkCallbackLog) -> dict:
    return row.model_dump()


def record(
    session: Session,
    *,
    source_ip: Optional[str],
    headers: dict,
    body_text: str,
) -> int:
    row = GeelarkCallbackLog(
        source_ip=source_ip,
        headers_json=headers,
        body_text=body_text,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.id


def set_parsed(
    session: Session,
    row_id: int,
    *,
    task_id: Optional[str],
    status: Optional[int],
) -> None:
    session.execute(
        text(
            "UPDATE geelark_callback_log "
            "SET parsed_task_id = :tid, parsed_status = :st "
            "WHERE id = :id"
        ),
        {"tid": task_id, "st": status, "id": row_id},
    )
    session.commit()


def mark_handled(
    session: Session, row_id: int, *, outcome: str,
) -> None:
    session.execute(
        text(
            "UPDATE geelark_callback_log "
            "SET handled = TRUE, handled_at = :ts, handle_outcome = :out "
            "WHERE id = :id"
        ),
        {"ts": datetime.utcnow(), "out": outcome, "id": row_id},
    )
    session.commit()


def get(session: Session, row_id: int) -> Optional[dict]:
    row = session.get(GeelarkCallbackLog, row_id)
    return _dump(row) if row else None


def prune_older_than(session: Session, cutoff: datetime) -> int:
    result = session.execute(
        text(
            "DELETE FROM geelark_callback_log WHERE received_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    session.commit()
    return result.rowcount or 0
```

- [ ] **Step 4: Run repository tests**

Run: `uv run pytest tests/test_repositories_callback_log.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/repositories/callback_log.py tests/test_repositories_callback_log.py
git commit -m "feat(callback): GeelarkCallbackLog model + repo"
```

---

## Task 4: GeeLarkClient — callback set/get

**Files:**
- Modify: `geelark_client.py`
- Modify: `tests/test_geelark_client.py`

- [ ] **Step 1: Add the two methods to `GeeLarkClient`**

At the end of the class (after `list_billing_transactions`), append:

```python
    # ── Callback (task-done push) ────────────────────────────────────────────
    # The docs call this "webhook" but the live paths are /callback/*; the
    # /webhook/* paths return 404 (verified 2026-06-09). Single URL per
    # account; setting overwrites.

    def get_callback(self) -> dict:
        """Returns {'url': '<configured>'} or {'url': ''} when unset."""
        return self._post("/open/v1/callback/get", {})

    def set_callback(self, url: str) -> dict:
        """Register the URL GeeLark will POST task-done events to.
        Returns {} on success. Accepts plain HTTP."""
        return self._post("/open/v1/callback/set", {"url": url})
```

- [ ] **Step 2: Write a thin test verifying payload shape**

Append to `tests/test_geelark_client.py`:

```python
def test_callback_get_set_payloads(monkeypatch):
    """Verify the client posts to /callback/{get,set} with the right body."""
    seen = {}

    def fake_post(self, path, payload):
        seen[path] = payload
        if path.endswith("/callback/get"):
            return {"url": "http://x"}
        return {}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("t")

    assert c.get_callback() == {"url": "http://x"}
    assert seen["/open/v1/callback/get"] == {}

    assert c.set_callback("http://my.example/api/cb?token=xx") == {}
    assert seen["/open/v1/callback/set"] == {"url": "http://my.example/api/cb?token=xx"}
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_geelark_client.py::test_callback_get_set_payloads -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add geelark_client.py tests/test_geelark_client.py
git commit -m "feat(callback): GeeLarkClient.get_callback / set_callback"
```

---

## Task 5: API endpoint — `/api/geelark/callback` (log-only, Phase 0)

**Files:**
- Create: `app/api/geelark_callback.py`
- Modify: `app/main.py`
- Create: `tests/test_api_geelark_callback.py`

- [ ] **Step 1: Write failing endpoint test (token check + body capture)**

File `tests/test_api_geelark_callback.py`:

```python
"""Phase 0: endpoint logs raw inbound, requires shared token."""

import json

import pytest


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "GEELARK_CALLBACK_TOKEN", "secret-xyz")
    # the endpoint imports it freshly from the module each request
    import app.api.geelark_callback as cb_api
    monkeypatch.setattr(cb_api, "_TOKEN", "secret-xyz")


def test_callback_rejects_missing_token(client):
    r = client.post("/api/geelark/callback", json={})
    assert r.status_code == 401


def test_callback_rejects_wrong_token(client):
    r = client.post("/api/geelark/callback?token=nope", json={})
    assert r.status_code == 401


def test_callback_accepts_and_logs(client, db):
    body = {"taskId": "abc", "status": 3, "noise": [1, 2]}
    r = client.post(
        "/api/geelark/callback?token=secret-xyz",
        data=json.dumps(body),
        headers={"Content-Type": "application/json",
                 "X-GeeLark-Trace": "tr-1"},
    )
    assert r.status_code == 200
    # exactly one row in the log
    from app.models import GeelarkCallbackLog
    from sqlmodel import select
    rows = db.exec(select(GeelarkCallbackLog)).all()
    assert len(rows) == 1
    row = rows[0]
    assert json.loads(row.body_text) == body
    assert row.headers_json.get("x-geelark-trace") == "tr-1"


def test_callback_handles_non_json_body(client, db):
    r = client.post(
        "/api/geelark/callback?token=secret-xyz",
        data="not json at all",
        headers={"Content-Type": "text/plain"},
    )
    assert r.status_code == 200
    from app.models import GeelarkCallbackLog
    from sqlmodel import select
    rows = db.exec(select(GeelarkCallbackLog)).all()
    assert len(rows) == 1
    assert rows[0].body_text == "not json at all"
```

Run: `uv run pytest tests/test_api_geelark_callback.py -v`
Expected: FAIL — `app.api.geelark_callback` doesn't exist.

- [ ] **Step 2: Implement the endpoint (log-only)**

File `app/api/geelark_callback.py`:

```python
"""GeeLark task-done callback endpoint — Phase 0.

This phase ONLY logs the raw payload. Parsing and the actual stop_phones
chain land in a later commit, after we've observed a few real callbacks
to verify the payload schema (GeeLark does not document it).

The endpoint is intentionally outside the auth middleware — GeeLark
cannot send our HMAC cookie. Authentication is a shared-secret token
in the query string. Token is generated per-environment; see .env.example.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app import config
from app.db import session_scope
from app.repositories import callback_log as cb_repo

log = logging.getLogger(__name__)
router = APIRouter()

# Read at import time so tests can monkeypatch this module-level constant
# without round-tripping through config.
_TOKEN = config.GEELARK_CALLBACK_TOKEN


@router.post("/api/geelark/callback")
async def geelark_callback(
    request: Request, token: Optional[str] = Query(default=None),
):
    if not _TOKEN or token != _TOKEN:
        # Don't reveal which side is wrong. 401 across the board.
        raise HTTPException(status_code=401, detail="unauthorized")

    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")
    headers = {k.lower(): v for k, v in request.headers.items()}
    source_ip = request.client.host if request.client else None

    try:
        with session_scope() as session:
            cb_repo.record(
                session,
                source_ip=source_ip,
                headers=headers,
                body_text=body_text,
            )
    except Exception:
        log.exception("geelark_callback: failed to record")

    # Always 200 — we don't want GeeLark to retry on our bookkeeping
    # errors. Phase 1 will defer the finalize job in addition to logging.
    return {}
```

- [ ] **Step 3: Wire it into `app/main.py`**

In `app/main.py`, find the block where other no-auth routers are mounted (just below `app.include_router(health_api.router)`). Add:

```python
    from app.api import geelark_callback as geelark_callback_api
    app.include_router(geelark_callback_api.router)  # no auth — GeeLark cannot send cookies
```

- [ ] **Step 4: Run the API tests**

Run: `uv run pytest tests/test_api_geelark_callback.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/api/geelark_callback.py app/main.py tests/test_api_geelark_callback.py
git commit -m "feat(callback): log-only /api/geelark/callback endpoint (Phase 0)"
```

---

## Task 6: Registration script

**Files:**
- Create: `scripts/register_geelark_callback.py`

- [ ] **Step 1: Write the script**

File `scripts/register_geelark_callback.py`:

```python
"""Register (or refresh) the GeeLark callback URL.

Idempotent: the GeeLark endpoint just overwrites whatever was set.
Reads GEELARK_CALLBACK_URL (full URL incl. ?token=...) from env.

Usage:
    GEELARK_CALLBACK_URL='http://139.196.52.92:8080/api/geelark/callback?token=...' \\
      uv run python scripts/register_geelark_callback.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.geelark_service import make_client


def main() -> int:
    url = os.environ.get("GEELARK_CALLBACK_URL", "").strip()
    if not url:
        print("ERROR: set GEELARK_CALLBACK_URL", file=sys.stderr)
        return 2
    if "?token=" not in url:
        print("WARNING: URL has no ?token=...; the endpoint will 401",
              file=sys.stderr)

    client = make_client()
    prev = client.get_callback()
    print(f"before: {prev}")
    resp = client.set_callback(url)
    print(f"set_callback response: {resp}")
    now = client.get_callback()
    print(f"after:  {now}")
    if now.get("url") != url:
        print("ERROR: callback URL did not round-trip; got "
              f"{now.get('url')!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit**

```bash
git add scripts/register_geelark_callback.py
git commit -m "feat(callback): scripts/register_geelark_callback.py"
```

---

## Task 7: Phase 0 deploy — observe real callbacks

**Files:** none.

- [ ] **Step 1: Generate the token**

Run on your laptop:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Copy the output.

- [ ] **Step 2: Add token + URL to production `.env`**

SSH and edit `/opt/mkt/.env`:
```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'echo "GEELARK_CALLBACK_TOKEN=<paste-token-here>" >> /opt/mkt/.env && grep GEELARK_CALLBACK /opt/mkt/.env'
```
Expected: line printed.

- [ ] **Step 3: Package + ship the code (per `docs/production-deploy.md`)**

Run locally:
```bash
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-deploy.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini geelark_client.py generate_caption.py \
  generate_comment.py pyproject.toml uv.lock frontend/dist scripts
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-deploy.tar.gz root@139.196.52.92:/tmp/mkt-deploy.tar.gz
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e; export PATH="/root/.local/bin:$PATH"; cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini frontend/dist geelark_client.py \
    generate_caption.py generate_comment.py pyproject.toml uv.lock scripts
  uv sync --no-dev
  uv run alembic upgrade head
  systemctl restart mkt mkt-worker
  sleep 4
  systemctl is-active mkt mkt-worker
  rm -f /tmp/mkt-deploy.tar.gz
'
```
Expected: both services print `active`, migration line shows `Running upgrade 566020ad4ad1 -> abcd1234ef56`.

- [ ] **Step 4: Register the callback URL with GeeLark**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  export PATH="/root/.local/bin:$PATH"
  cd /opt/mkt
  GEELARK_CALLBACK_URL="http://139.196.52.92:8080/api/geelark/callback?token=$(grep ^GEELARK_CALLBACK_TOKEN= .env | cut -d= -f2)" \
    uv run python scripts/register_geelark_callback.py
'
```
Expected output: `before: {'url': ...}` then `set_callback response: {}` then `after: {'url': 'http://139.196.52.92:8080/api/geelark/callback?token=...'}`.

- [ ] **Step 5: Trigger a warmup so a callback fires**

Either wait for the next `daily_warmup_scan` (≤10 min) or pick any account from the dashboard. Then check the log table after a few minutes:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  docker exec svm-postgres psql -U mkt -d mkt -c "
    SELECT id, received_at, source_ip, left(body_text, 200) AS body_preview,
           headers_json
    FROM geelark_callback_log ORDER BY received_at DESC LIMIT 5;"
'
```
Expected: ≥ 1 row. Record:
- The exact field names in `body_text` that carry the task id and status.
- Whether GeeLark sets a signature-style header (e.g. `x-signature`, `x-sign`, `authorization`).
- The source IP range.

**Pause this plan here.** Task 8 needs the observed schema before it can be implemented.

No commit for Task 7 (deployment artifact, not code).

---

## Task 8: Callback payload parser

**Files:**
- Create: `app/services/callback_parser.py`
- Create: `tests/test_callback_parser.py`

- [ ] **Step 1: Write failing parser test with conservative field fallbacks**

GeeLark's response shapes in the OpenAPI list both `id` and `taskId` for similar concepts, and both `status` and `state`. The parser tries each in order. **Update this test with the actual fields observed in Task 7 before running.**

File `tests/test_callback_parser.py`:

```python
"""callback_parser: extract (task_id, status) from GeeLark payloads."""

import pytest

from app.services.callback_parser import parse


def test_parse_taskid_status_canonical():
    out = parse('{"taskId":"623100","status":3}')
    assert out == {"task_id": "623100", "status": 3}


def test_parse_alt_id_field():
    out = parse('{"id":"623100","status":4}')
    assert out == {"task_id": "623100", "status": 4}


def test_parse_alt_state_field():
    out = parse('{"taskId":"x","state":3}')
    assert out == {"task_id": "x", "status": 3}


def test_parse_nested_under_data():
    out = parse('{"data":{"taskId":"deep","status":3}}')
    assert out == {"task_id": "deep", "status": 3}


def test_parse_missing_task_id_returns_none_id():
    out = parse('{"status":3}')
    assert out == {"task_id": None, "status": 3}


def test_parse_garbage_returns_nones():
    out = parse("not json")
    assert out == {"task_id": None, "status": None}
```

Run: `uv run pytest tests/test_callback_parser.py -v`
Expected: FAIL — module not found.

- [ ] **Step 2: Implement defensively**

File `app/services/callback_parser.py`:

```python
"""Defensive parser for GeeLark callback payloads.

GeeLark publishes no schema for callback bodies. Observation in
production (Task 7 of the callback plan) shows the canonical shape,
but variants exist across task types. This parser tries multiple known
field names and falls back to `None` on each rather than raising — the
caller logs the raw body either way (Phase 0 endpoint) and a `None`
task_id just means the row is unactionable. Adding new field aliases
here is the right way to support a new payload shape; the parser must
never throw.
"""
from __future__ import annotations

import json
from typing import Any, Optional


_ID_KEYS = ("taskId", "task_id", "id", "ID")
_STATUS_KEYS = ("status", "state", "taskStatus")


def _scan(obj: Any, keys: tuple[str, ...]) -> Optional[Any]:
    """Recursive shallow scan: return the first matching value at the
    top level, or one level under common envelopes (`data`, `payload`,
    `event`). Anything deeper is intentional noise; if a future GeeLark
    schema nests it deeper, add the envelope name here."""
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    for envelope in ("data", "payload", "event"):
        inner = obj.get(envelope)
        if isinstance(inner, dict):
            for k in keys:
                if k in inner and inner[k] is not None:
                    return inner[k]
    return None


def parse(body_text: str) -> dict:
    """Return {'task_id': str|None, 'status': int|None}. Never raises."""
    try:
        obj = json.loads(body_text)
    except Exception:
        return {"task_id": None, "status": None}

    tid = _scan(obj, _ID_KEYS)
    raw_status = _scan(obj, _STATUS_KEYS)

    if tid is not None:
        tid = str(tid)
    if raw_status is not None:
        try:
            raw_status = int(raw_status)
        except (TypeError, ValueError):
            raw_status = None

    return {"task_id": tid, "status": raw_status}
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_callback_parser.py -v`
Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add app/services/callback_parser.py tests/test_callback_parser.py
git commit -m "feat(callback): defensive payload parser"
```

---

## Task 9: `find_task_owner` lookup helper

**Files:**
- Create: `app/services/callback_dispatch.py`
- Create: `tests/test_callback_dispatch.py`

- [ ] **Step 1: Write failing tests covering each task family**

File `tests/test_callback_dispatch.py`:

```python
"""find_task_owner: map a GeeLark task_id to the owning account + task family."""

from datetime import datetime

from app.models import (
    Account, EngagementRun, EngagementTask, Publication, Video,
)
from app.services.callback_dispatch import find_task_owner


def _account(db, *, phone="ph-1", task_id=None):
    a = Account(profile_name="acc", geelark_phone_id=phone,
                warmup_task_id=task_id)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_find_publication(db):
    a = _account(db)
    v = Video(original_filename="x", stored_path="/tmp/x")
    db.add(v); db.commit(); db.refresh(v)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="INITIATED", geelark_task_id="pub-task-7",
    )
    db.add(p); db.commit(); db.refresh(p)

    out = find_task_owner(db, "pub-task-7")
    assert out["kind"] == "publish"
    assert out["account_id"] == a.id
    assert out["geelark_phone_id"] == "ph-1"
    assert out["publication_id"] == p.id


def test_find_engagement_run(db):
    a = _account(db)
    et = EngagementTask(action="comment", source="manual")
    db.add(et); db.commit(); db.refresh(et)
    er = EngagementRun(
        engagement_task_id=et.id, account_id=a.id, target_link="x",
        action="comment", result="INITIATED", geelark_task_id="eng-task-9",
    )
    db.add(er); db.commit(); db.refresh(er)

    out = find_task_owner(db, "eng-task-9")
    assert out["kind"] == "engagement"
    assert out["account_id"] == a.id
    assert out["engagement_run_id"] == er.id


def test_find_warmup(db):
    a = _account(db, task_id="warm-task-3")
    out = find_task_owner(db, "warm-task-3")
    assert out["kind"] == "warmup"
    assert out["account_id"] == a.id
    assert out["geelark_phone_id"] == "ph-1"


def test_find_unknown_returns_none(db):
    _account(db)
    assert find_task_owner(db, "nope") is None
```

Run: `uv run pytest tests/test_callback_dispatch.py -v`
Expected: FAIL — module not found.

- [ ] **Step 2: Implement the helper**

File `app/services/callback_dispatch.py`:

```python
"""Look up the owner of a GeeLark task_id across publications,
engagement_runs, and accounts.warmup_task_id."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlmodel import Session


def find_task_owner(session: Session, task_id: str) -> Optional[dict]:
    """Return None if no table claims this task_id.

    Lookup order: publication → engagement_run → warmup (accounts).
    A task_id collision across tables is not expected (GeeLark task_ids
    are globally unique strings); first hit wins.
    """
    row = session.execute(text(
        "SELECT p.id AS publication_id, p.account_id, a.geelark_phone_id "
        "FROM publications p "
        "LEFT JOIN accounts a ON a.id = p.account_id "
        "WHERE p.geelark_task_id = :tid LIMIT 1"
    ), {"tid": task_id}).first()
    if row is not None:
        return {
            "kind": "publish",
            "publication_id": row.publication_id,
            "account_id": row.account_id,
            "geelark_phone_id": row.geelark_phone_id,
        }

    row = session.execute(text(
        "SELECT er.id AS engagement_run_id, er.account_id, "
        "       a.geelark_phone_id "
        "FROM engagement_runs er "
        "LEFT JOIN accounts a ON a.id = er.account_id "
        "WHERE er.geelark_task_id = :tid LIMIT 1"
    ), {"tid": task_id}).first()
    if row is not None:
        return {
            "kind": "engagement",
            "engagement_run_id": row.engagement_run_id,
            "account_id": row.account_id,
            "geelark_phone_id": row.geelark_phone_id,
        }

    row = session.execute(text(
        "SELECT id AS account_id, geelark_phone_id "
        "FROM accounts WHERE warmup_task_id = :tid LIMIT 1"
    ), {"tid": task_id}).first()
    if row is not None:
        return {
            "kind": "warmup",
            "account_id": row.account_id,
            "geelark_phone_id": row.geelark_phone_id,
        }

    return None
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_callback_dispatch.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add app/services/callback_dispatch.py tests/test_callback_dispatch.py
git commit -m "feat(callback): find_task_owner across publish/engagement/warmup"
```

---

## Task 10: `finalize_task_callback` procrastinate task

**Files:**
- Create: `app/tasks/finalize_callback.py`
- Modify: `app/tasks/__init__.py` (`import_paths` list)
- Create: `tests/test_tasks_finalize_callback.py`

- [ ] **Step 1: Write failing tests**

File `tests/test_tasks_finalize_callback.py`:

```python
"""finalize_task_callback: stop phone + reuse existing finalize helpers,
idempotent when state is already terminal."""

import asyncio
from unittest.mock import MagicMock

from app.models import (
    Account, EngagementRun, EngagementTask, Publication, Video,
)
from app.tasks import finalize_callback


class _FakeClient:
    def __init__(self):
        self.stop_calls: list[list[str]] = []
        self.query_tasks_return: list[dict] = []

    def stop_phones(self, ids):
        self.stop_calls.append(list(ids))
        return {}

    def query_tasks(self, ids):
        return self.query_tasks_return

    def get_task_detail(self, task_id):
        return {"failCode": 0, "failDesc": "", "resultImages": []}


def _publication(db, *, task_id, result="INITIATED"):
    a = Account(profile_name="acc", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    v = Video(original_filename="x", stored_path="/tmp/x")
    db.add(v); db.commit(); db.refresh(v)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result=result, geelark_task_id=task_id,
    )
    db.add(p); db.commit(); db.refresh(p)
    return a, p


def test_finalize_publish_stops_phone(db, monkeypatch):
    a, p = _publication(db, task_id="pub-1")
    fake = _FakeClient()
    fake.query_tasks_return = [{"id": "pub-1", "status": 3, "shareLink": ""}]
    monkeypatch.setattr(finalize_callback, "make_client", lambda: fake)

    msg = asyncio.run(finalize_callback.finalize_task_callback(
        task_id="pub-1", status=3))

    assert fake.stop_calls == [["ph-1"]]
    assert "publish" in msg.lower()


def test_finalize_idempotent_when_already_terminal(db, monkeypatch):
    a, p = _publication(db, task_id="pub-2", result="SUCCESS")
    fake = _FakeClient()
    monkeypatch.setattr(finalize_callback, "make_client", lambda: fake)

    msg = asyncio.run(finalize_callback.finalize_task_callback(
        task_id="pub-2", status=3))

    # No stop_phones call — finalize sees terminal state and no-ops.
    assert fake.stop_calls == []
    assert "noop" in msg.lower() or "already" in msg.lower()


def test_finalize_unknown_task_logs_and_returns(db, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(finalize_callback, "make_client", lambda: fake)
    msg = asyncio.run(finalize_callback.finalize_task_callback(
        task_id="nope", status=3))
    assert fake.stop_calls == []
    assert "unknown" in msg.lower()
```

Run: `uv run pytest tests/test_tasks_finalize_callback.py -v`
Expected: FAIL — module not found.

- [ ] **Step 2: Implement the procrastinate task**

File `app/tasks/finalize_callback.py`:

```python
"""Callback-driven task finalization.

A GeeLark callback (POST to /api/geelark/callback) defers one of these
jobs onto queue=phones. The job:
  1. Resolves task_id → owner (publish / engagement / warmup).
  2. Re-uses the same finalize helper the polling chain calls
     (`check_publication_one`, `check_engagement_one`,
     `advance_warmup_one`), so callback and polling produce identical
     bookkeeping.
  3. Helpers internally short-circuit when state is already terminal,
     making this idempotent — a callback retry arriving after polling
     already finished is a no-op.

queue=phones serializes against the same lease the polling path takes,
so callback + polling racing on the same task is safe.
"""
import asyncio
import logging

from app.db import session_scope
from app.services import callback_dispatch, posting_service, warmup_service
from app.services import engagement_service
from app.services.geelark_service import make_client
from app.tasks import procrastinate_app

log = logging.getLogger(__name__)


def _finalize_sync(task_id: str, status: int) -> str:
    with session_scope() as session:
        owner = callback_dispatch.find_task_owner(session, task_id)
        if owner is None:
            return f"unknown task_id={task_id}"

        client = make_client()
        kind = owner["kind"]
        if kind == "publish":
            outcome = posting_service.check_publication_one(
                session, client, owner["publication_id"])
            return f"publish {owner['publication_id']} → {outcome}"
        if kind == "engagement":
            outcome = engagement_service.check_engagement_run(
                session, client, owner["engagement_run_id"])
            return f"engagement {owner['engagement_run_id']} → {outcome}"
        if kind == "warmup":
            outcome = warmup_service.advance_warmup_one(
                session, client, owner["account_id"], task_id)
            if outcome in ("noop", "already_terminal", "completed",
                           "failure", "running"):
                return f"warmup acct={owner['account_id']} → {outcome}"
            return f"warmup acct={owner['account_id']} → {outcome}"
        return f"unhandled kind={kind}"


@procrastinate_app.task(queue="phones", name="finalize_task_callback")
async def finalize_task_callback(task_id: str, status: int) -> str:
    try:
        msg = await asyncio.to_thread(_finalize_sync, task_id, status)
    except Exception as exc:
        log.exception("finalize_task_callback failed task_id=%s", task_id)
        return f"error: {exc!r}"
    log.info("finalize_task_callback: %s", msg)
    return msg
```

- [ ] **Step 3: Register the task module**

In `app/tasks/__init__.py`, append `"app.tasks.finalize_callback"` to the `import_paths` list:

```python
        "app.tasks.idle_phone_sweeper",
        "app.tasks.sync_transactions",
        "app.tasks.finalize_callback",
    ],
```

- [ ] **Step 4: Verify `check_engagement_run` name actually exists**

Run: `grep -n 'def check_engagement_run\|def check_engagement_one' app/services/engagement_service.py`
- If `check_engagement_one` (or another name) is the real symbol, fix the call in `finalize_callback._finalize_sync` accordingly. The existing engagement polling task in `app/tasks/engagement.py` is the source of truth — read its `_check_sync` helper to see which service function it calls and mirror that call.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tasks_finalize_callback.py -v`
Expected: 3 passed. (If the engagement helper name needed adjustment in step 4, the `test_finalize_unknown_task_logs_and_returns` test should still pass; the publish test exercises the real path.)

- [ ] **Step 6: Commit**

```bash
git add app/tasks/finalize_callback.py app/tasks/__init__.py \
        tests/test_tasks_finalize_callback.py
git commit -m "feat(callback): finalize_task_callback worker job"
```

---

## Task 11: Wire callback endpoint to defer finalize (Phase 1)

**Files:**
- Modify: `app/api/geelark_callback.py`
- Modify: `tests/test_api_geelark_callback.py`

- [ ] **Step 1: Add failing test for the defer behavior**

Append to `tests/test_api_geelark_callback.py`:

```python
def test_callback_defers_finalize_when_taskid_known(
    client, db, in_memory_procrastinate,
):
    # Seed a publication that owns the task id
    from app.models import Account, Publication, Video
    a = Account(profile_name="acc", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    v = Video(original_filename="x", stored_path="/tmp/x")
    db.add(v); db.commit(); db.refresh(v)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="INITIATED", geelark_task_id="pub-callback-1",
    )
    db.add(p); db.commit(); db.refresh(p)

    r = client.post(
        "/api/geelark/callback?token=secret-xyz",
        json={"taskId": "pub-callback-1", "status": 3},
    )
    assert r.status_code == 200
    # finalize_task_callback was deferred to the phones queue
    jobs = list(in_memory_procrastinate.jobs.values())
    names = [j["task_name"] for j in jobs]
    assert "finalize_task_callback" in names
    finalize = next(j for j in jobs if j["task_name"] == "finalize_task_callback")
    assert finalize["task_kwargs"] == {"task_id": "pub-callback-1", "status": 3}


def test_callback_no_defer_when_taskid_missing(
    client, db, in_memory_procrastinate,
):
    r = client.post(
        "/api/geelark/callback?token=secret-xyz",
        json={"foo": "bar"},  # no taskId field
    )
    assert r.status_code == 200
    names = [j["task_name"] for j in in_memory_procrastinate.jobs.values()]
    assert "finalize_task_callback" not in names
```

Run: `uv run pytest tests/test_api_geelark_callback.py -v`
Expected: 2 new tests FAIL.

- [ ] **Step 2: Wire the parser + defer into the endpoint**

Edit `app/api/geelark_callback.py`. Replace the existing handler body with:

```python
@router.post("/api/geelark/callback")
async def geelark_callback(
    request: Request, token: Optional[str] = Query(default=None),
):
    if not _TOKEN or token != _TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")

    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")
    headers = {k.lower(): v for k, v in request.headers.items()}
    source_ip = request.client.host if request.client else None

    row_id: Optional[int] = None
    try:
        with session_scope() as session:
            row_id = cb_repo.record(
                session,
                source_ip=source_ip,
                headers=headers,
                body_text=body_text,
            )
    except Exception:
        log.exception("geelark_callback: failed to record")

    # Parse + defer, but never fail the response — Phase 0 logging is
    # still the safety net.
    try:
        from app.services.callback_parser import parse
        parsed = parse(body_text)
        if row_id is not None:
            with session_scope() as session:
                cb_repo.set_parsed(
                    session, row_id,
                    task_id=parsed["task_id"], status=parsed["status"],
                )
        if parsed["task_id"]:
            from app.tasks.finalize_callback import finalize_task_callback
            await finalize_task_callback.defer_async(
                task_id=parsed["task_id"],
                status=parsed["status"] if parsed["status"] is not None else 0,
            )
            if row_id is not None:
                with session_scope() as session:
                    cb_repo.mark_handled(session, row_id,
                                          outcome="deferred")
    except Exception:
        log.exception("geelark_callback: dispatch failed")

    return {}
```

- [ ] **Step 3: Run all callback API tests**

Run: `uv run pytest tests/test_api_geelark_callback.py -v`
Expected: 6 passed (4 existing + 2 new).

- [ ] **Step 4: Commit**

```bash
git add app/api/geelark_callback.py tests/test_api_geelark_callback.py
git commit -m "feat(callback): parse + defer finalize_task_callback (Phase 1)"
```

---

## Task 12: Loosen polling intervals (Phase 2)

**Files:**
- Modify: `app/tasks/publish.py`
- Modify: `app/tasks/engagement.py`

> **Do NOT run this task until at least 24h after Phase 1 deploy and you have confirmed callbacks are arriving and finalizing tasks correctly.** Tighten polling first, loosen it last.

- [ ] **Step 1: Halve publish polling interval**

In `app/tasks/publish.py`, change the constants near the top:

```python
MAX_CHECK_RETRIES = 40
CHECK_INTERVAL_SECONDS = 15   # was 30; callback handles happy path
```

- [ ] **Step 2: Halve engagement polling interval**

In `app/tasks/engagement.py`:

```python
MAX_CHECK_RETRIES = 40
CHECK_INTERVAL_SECONDS = 15   # was 30; callback handles happy path
```

- [ ] **Step 3: Confirm existing polling tests still pass**

Run: `uv run pytest tests/test_tasks_publish.py tests/test_tasks_engagement.py -v 2>/dev/null || uv run pytest -k "publish or engagement" -v`
Expected: all related tests pass. If any test pinned to the old 30s value fails, update its assertion to 15.

- [ ] **Step 4: Commit**

```bash
git add app/tasks/publish.py app/tasks/engagement.py
git commit -m "perf(callback): tighten publish/engagement polling 30s→15s (Phase 2)"
```

---

## Task 13: Retention prune cron (Phase 3 — part 1)

**Files:**
- Create: `app/tasks/prune_callback_log.py`
- Modify: `app/tasks/__init__.py`
- Create: `tests/test_tasks_prune_callback_log.py`

- [ ] **Step 1: Write failing test**

File `tests/test_tasks_prune_callback_log.py`:

```python
"""prune_callback_log: drops rows older than 7 days."""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import text

from app.repositories import callback_log as cb_repo
from app.tasks import prune_callback_log


def test_prune_drops_only_old_rows(db):
    rid_old = cb_repo.record(db, source_ip=None, headers={}, body_text="old")
    rid_new = cb_repo.record(db, source_ip=None, headers={}, body_text="new")
    db.execute(text(
        "UPDATE geelark_callback_log SET received_at = :ts WHERE id = :id"
    ), {"ts": datetime.utcnow() - timedelta(days=10), "id": rid_old})
    db.commit()

    msg = asyncio.run(prune_callback_log.prune_callback_log_task(timestamp=0))
    assert "deleted=1" in msg
    assert cb_repo.get(db, rid_old) is None
    assert cb_repo.get(db, rid_new) is not None
```

Run: `uv run pytest tests/test_tasks_prune_callback_log.py -v`
Expected: FAIL — module not found.

- [ ] **Step 2: Implement the cron task**

File `app/tasks/prune_callback_log.py`:

```python
"""Daily prune of geelark_callback_log rows older than 7 days."""
import asyncio
from datetime import datetime, timedelta

from app.db import session_scope
from app.repositories import callback_log as cb_repo
from app.tasks import procrastinate_app

RETENTION_DAYS = 7


def _prune_sync() -> int:
    with session_scope() as session:
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
        return cb_repo.prune_older_than(session, cutoff)


@procrastinate_app.periodic(cron="0 3 * * *")
@procrastinate_app.task(queue="default", name="prune_callback_log")
async def prune_callback_log_task(timestamp: int) -> str:
    deleted = await asyncio.to_thread(_prune_sync)
    return f"prune_callback_log: deleted={deleted}"
```

- [ ] **Step 3: Register the task module**

In `app/tasks/__init__.py`, append `"app.tasks.prune_callback_log"` to `import_paths`.

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_tasks_prune_callback_log.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/tasks/prune_callback_log.py app/tasks/__init__.py \
        tests/test_tasks_prune_callback_log.py
git commit -m "feat(callback): daily prune of geelark_callback_log (>7d)"
```

---

## Task 14: Callback heartbeat monitor (Phase 3 — part 2)

**Files:**
- Create: `app/tasks/callback_heartbeat.py`
- Modify: `app/tasks/__init__.py`
- Create: `tests/test_tasks_callback_heartbeat.py`

- [ ] **Step 1: Write failing test**

File `tests/test_tasks_callback_heartbeat.py`:

```python
"""callback_heartbeat: log a warning when no callbacks arrived recently
*and* we had outstanding tasks. Pure observation — never raises."""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import text

from app.models import (
    Account, EngagementRun, EngagementTask, Publication, Video,
)
from app.repositories import callback_log as cb_repo
from app.tasks import callback_heartbeat


def _seed_outstanding_publication(db):
    a = Account(profile_name="acc", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    v = Video(original_filename="x", stored_path="/tmp/x")
    db.add(v); db.commit(); db.refresh(v)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="INITIATED", geelark_task_id="t-out",
    )
    db.add(p); db.commit()


def test_heartbeat_ok_when_recent_callback(db):
    cb_repo.record(db, source_ip=None, headers={}, body_text="recent")
    msg = asyncio.run(callback_heartbeat.callback_heartbeat_task(timestamp=0))
    assert "ok" in msg


def test_heartbeat_warns_when_no_callback_and_outstanding_tasks(db):
    _seed_outstanding_publication(db)
    # No callback rows in the table at all.
    msg = asyncio.run(callback_heartbeat.callback_heartbeat_task(timestamp=0))
    assert "warn" in msg.lower()


def test_heartbeat_ok_when_no_outstanding_tasks(db):
    # No callbacks AND no outstanding tasks → nothing to warn about
    msg = asyncio.run(callback_heartbeat.callback_heartbeat_task(timestamp=0))
    assert "ok" in msg
```

Run: `uv run pytest tests/test_tasks_callback_heartbeat.py -v`
Expected: FAIL — module not found.

- [ ] **Step 2: Implement the cron task**

File `app/tasks/callback_heartbeat.py`:

```python
"""Every 30 min: if outstanding tasks exist but no callback has arrived
in the past 30 min, log a warning. Never raises. Polling fallback will
still complete the work; this is just an observability signal."""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db import session_scope
from app.tasks import procrastinate_app

log = logging.getLogger(__name__)

QUIET_WINDOW_MINUTES = 30


def _check_sync() -> str:
    with session_scope() as session:
        since = datetime.utcnow() - timedelta(minutes=QUIET_WINDOW_MINUTES)
        recent_callbacks = session.execute(text(
            "SELECT COUNT(*) FROM geelark_callback_log "
            "WHERE received_at >= :since"
        ), {"since": since}).scalar() or 0

        outstanding = session.execute(text(
            "SELECT COUNT(*) FROM publications "
            "WHERE result IN ('QUEUED', 'INITIATED') "
            "  AND geelark_task_id IS NOT NULL"
        )).scalar() or 0
        outstanding += session.execute(text(
            "SELECT COUNT(*) FROM engagement_runs "
            "WHERE result IN ('QUEUED', 'INITIATED') "
            "  AND geelark_task_id IS NOT NULL"
        )).scalar() or 0
        outstanding += session.execute(text(
            "SELECT COUNT(*) FROM accounts "
            "WHERE warmup_task_id IS NOT NULL"
        )).scalar() or 0

    if recent_callbacks == 0 and outstanding > 0:
        msg = (f"warn: 0 callbacks in {QUIET_WINDOW_MINUTES}m but "
               f"{outstanding} outstanding tasks — polling fallback "
               f"will still finalize; investigate GeeLark callback config")
        log.warning("callback_heartbeat: %s", msg)
        return msg
    return f"ok: callbacks={recent_callbacks} outstanding={outstanding}"


@procrastinate_app.periodic(cron="*/30 * * * *")
@procrastinate_app.task(queue="default", name="callback_heartbeat")
async def callback_heartbeat_task(timestamp: int) -> str:
    return await asyncio.to_thread(_check_sync)
```

- [ ] **Step 3: Register the task module**

In `app/tasks/__init__.py`, append `"app.tasks.callback_heartbeat"`.

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_tasks_callback_heartbeat.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/tasks/callback_heartbeat.py app/tasks/__init__.py \
        tests/test_tasks_callback_heartbeat.py
git commit -m "feat(callback): heartbeat warning when callbacks go silent"
```

---

## Task 15: Final regression + deploy (Phase 1 + 3)

**Files:** none modified.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: ≥ 669 + 18 (new tests across tasks 3, 4, 5, 8, 9, 10, 11, 13, 14) = ≥ 687 passing. Note actual count.

- [ ] **Step 2: Frontend rebuild (no UI changes, but build to keep dist current)**

Run: `cd frontend && npm run build`
Expected: `✓ built in <Nms>` with no errors.

- [ ] **Step 3: Package, ship, restart (Phase 1 + 3 deploy)**

```bash
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-deploy.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini geelark_client.py generate_caption.py \
  generate_comment.py pyproject.toml uv.lock frontend/dist scripts
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-deploy.tar.gz root@139.196.52.92:/tmp/mkt-deploy.tar.gz
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e; export PATH="/root/.local/bin:$PATH"; cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini frontend/dist geelark_client.py \
    generate_caption.py generate_comment.py pyproject.toml uv.lock scripts
  uv run alembic upgrade head
  systemctl restart mkt mkt-worker
  sleep 4
  systemctl is-active mkt mkt-worker
  rm -f /tmp/mkt-deploy.tar.gz
'
```
Expected: both services `active`. Migration is a no-op at this point (already applied at Task 7).

- [ ] **Step 4: Smoke check — trigger a warmup, watch the log table flip to handled**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  docker exec svm-postgres psql -U mkt -d mkt -c "
    SELECT id, received_at, parsed_task_id, parsed_status, handled,
           handle_outcome
    FROM geelark_callback_log
    ORDER BY received_at DESC LIMIT 10;"
'
```
Expected: recent rows have `parsed_task_id` non-null, `handled = true`, `handle_outcome` like `deferred` or the finalize message.

- [ ] **Step 5: Verify worker journals show finalize jobs running**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'journalctl -u mkt-worker -n 200 --no-pager | grep -i "finalize_task_callback"'
```
Expected: lines like `finalize_task_callback: publish 42 → SUCCESS`.

- [ ] **Step 6: Wait 24h before doing Task 12 (Phase 2 polling loosening)**

Track in the calendar — no automation. Re-run only after observing the callback path is stable.
