# Phase 1: SQLite → PostgreSQL + SQLModel + Alembic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate persistence from SQLite (single file) to PostgreSQL (`mkt` database on existing production PG instance), replace hand-written SQL repositories with SQLModel, replace hand-written `_migrate()` with Alembic. Business behavior unchanged; 297 tests still pass.

**Architecture:** Bottom-up swap of the data layer. `app/models.py` (new) defines SQLModel tables = Pydantic models = SQL schema, single source of truth. `app/db.py` (rewritten) uses SQLAlchemy engine + session factory. Repositories keep their existing public API (signatures change `sqlite3.Connection` → `Session`) but internals become `session.exec(select(...))`. Alembic owns schema migrations. APScheduler stays in-memory (Phase 2 replaces it; this phase explicitly keeps it).

**Tech Stack:** Python 3.11+ (existing), FastAPI (existing), **SQLModel** (new ORM/Pydantic), **psycopg[binary]>=3** (new PG driver), **Alembic** (new migration tool), **PostgreSQL 16** (production already runs Postgres docker on `:5432`).

**Spec:** `docs/superpowers/specs/2026-05-28-optimize-existing-design.md` — Phase 1 section.

**Predecessor:** Phase 0 (`docs/superpowers/plans/2026-05-28-phase-0-tooling.md`) — completed and deployed; uv + pyproject.toml in place.

---

## File Structure

**Create:**
- `app/models.py` — SQLModel definitions for all 5 tables (`Account`, `Video`, `Setting`, `JobRun`, `Group`)
- `alembic.ini` — Alembic config
- `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/<rev>_baseline.py` — Alembic infrastructure + first migration
- `scripts/migrate_sqlite_to_pg.py` — one-time data copy script
- `tests/conftest.py` — pytest fixtures for PG test database (or update existing conftest)

**Rewrite:**
- `app/db.py` — SQLAlchemy engine + `get_session()` dep; drops sqlite3 connection logic, PRAGMA, hand-written `_migrate()`
- `app/repositories/accounts.py` — SQLModel `session.exec()` instead of `?`-placeholder SQL
- `app/repositories/videos.py` — same
- `app/repositories/settings.py` — same
- `app/repositories/job_runs.py` — same
- `app/repositories/groups.py` — same
- `app/main.py` — `db_conn()` thread-local pattern removed in favor of FastAPI `Depends(get_session)`

**Modify:**
- `pyproject.toml` — add sqlmodel, psycopg[binary]>=3, alembic to `[project].dependencies`
- `.env` — add `DATABASE_URL=postgresql+psycopg://mkt:<pwd>@127.0.0.1:5432/mkt`
- `.env.example` — same with placeholder value
- `app/config.py` — add `DATABASE_URL` env reading
- `app/api/*.py` — change route signatures: `conn: sqlite3.Connection = Depends(db_conn)` → `session: Session = Depends(get_session)` (mostly mechanical)
- `app/services/*.py` — same signature change; SQL queries that take `conn` parameter take `session` instead
- `README.md` — add "Local PostgreSQL setup" section
- `CLAUDE.md` — note PG dependency
- `docs/production-deploy.md` — add DB migration + alembic upgrade steps

**Unchanged:**
- All `frontend/` code
- `geelark_client.py`, `generate_caption.py` (don't touch DB)
- `app/scheduler.py` (APScheduler stays; Phase 2's problem)
- `app/locks.py` (`OPERATION_LOCK` stays; Phase 2's problem)
- All `tests/test_*.py` (assertions stay; fixtures change to PG-backed)

**Delete:**
- After Phase 1 verified: `data/app.db` (the old SQLite file). NOT in this plan — kept for 7 days post-deploy per spec rollback window.

---

## Task 1: Add dependencies and verify resolution

**Files:**
- Modify: `pyproject.toml` (add 3 deps)

- [ ] **Step 1: Edit pyproject.toml**

Add to `[project].dependencies` (preserve existing order):
```toml
    "sqlmodel>=0.0.22",
    "psycopg[binary]>=3.2",
    "alembic>=1.13",
```

Place them after `"httpx>=0.27.0"` and before `"itsdangerous>=2.0.0"` for alphabetical adjacency to existing entries.

- [ ] **Step 2: Sync**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv sync
```

Expected: `Resolved N packages` (N grows by ~10 — sqlmodel pulls SQLAlchemy 2.x, psycopg adds binary wheel, alembic adds Mako). No errors.

- [ ] **Step 3: Verify nothing broke**

```bash
uv run pytest -q
```

Expected: `297 passed`. (We haven't used the new deps yet; this verifies no transitive conflict broke SQLite tests.)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
chore(deps): add sqlmodel, psycopg, alembic for phase 1

Three new prod deps for the PG migration. No code uses them yet.
297 tests still pass (sanity check that nothing transitive broke).
EOF
)"
```

---

## Task 2: Prepare PostgreSQL — create database, user, grants

**Files:** none (server-side SQL only)

This task creates the production target PG database. It also creates a local `mkt_dev` database for development and a `mkt_test` database for pytest. We do this BEFORE writing any code so that subsequent tasks can connect to real Postgres.

- [ ] **Step 1: Verify production PG is reachable**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'docker exec $(docker ps --filter ancestor=postgres -q | head -1) psql -U postgres -c "SELECT version();"'
```

Expected: version output like `PostgreSQL 16.x ...`. If `docker ps` shows no postgres container, escalate — memory says one runs at `:5432` via docker-proxy, verify with `ss -tlnp | grep 5432`.

- [ ] **Step 2: Generate strong password for mkt user**

Run locally:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output. Use it in steps 3 and the `.env` later. **Do not commit this password.**

- [ ] **Step 3: Create production `mkt` database + user**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  "docker exec -i \$(docker ps --filter ancestor=postgres -q | head -1) psql -U postgres" <<'SQL'
CREATE DATABASE mkt;
CREATE USER mkt WITH PASSWORD '<PASTE_PASSWORD_HERE>';
GRANT ALL PRIVILEGES ON DATABASE mkt TO mkt;
\c mkt
GRANT ALL ON SCHEMA public TO mkt;
SQL
```

Replace `<PASTE_PASSWORD_HERE>` with the password from Step 2. Expected output: `CREATE DATABASE`, `CREATE ROLE`, `GRANT`, then `GRANT` again after `\c mkt`.

- [ ] **Step 4: Verify connection as `mkt` user**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  "docker exec \$(docker ps --filter ancestor=postgres -q | head -1) psql -U mkt -d mkt -c 'SELECT current_user, current_database();'"
```

Expected: `mkt | mkt`.

- [ ] **Step 5: Set local `DATABASE_URL` in `.env`**

This is local-dev only. For local dev we'll use the production PG via SSH tunnel — but actually no, that's surgery on production. Instead, install PG locally:

```bash
brew install postgresql@16
brew services start postgresql@16
createdb mkt_dev
createdb mkt_test
psql mkt_dev -c "SELECT version();"
```

If `brew` is not on PATH: `export PATH="/opt/homebrew/bin:$PATH"`.

Then edit `.env` (gitignored) — add:
```
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_dev
```
(macOS Homebrew PostgreSQL creates a superuser matching your username; no password needed locally.)

- [ ] **Step 6: Document the test database URL**

Update `.env.example` (this is committed). Add at the bottom:
```
# PostgreSQL connection (Phase 1+).
# Local dev: postgresql+psycopg://<user>@127.0.0.1:5432/mkt_dev
# Production: postgresql+psycopg://mkt:<password>@127.0.0.1:5432/mkt
DATABASE_URL=
```

Commit `.env.example`:
```bash
git add .env.example
git commit -m "$(cat <<'EOF'
chore(env): document DATABASE_URL placeholder (phase 1 prep)

PG connection string format documented in .env.example. The actual
DATABASE_URL value is added to .env (gitignored).
EOF
)"
```

---

## Task 3: Define SQLModel models

**Files:**
- Create: `app/models.py`

- [ ] **Step 1: Read current schema from app/db.py**

Read `/Users/Zhuanz/Documents/geelark-studio/app/db.py` lines 4-65 (the `SCHEMA` constant). Map each column to a SQLModel field.

- [ ] **Step 2: Write app/models.py**

Create the file with content:

```python
"""SQLModel definitions for the mkt persistence layer.

Generated from the original SQLite schema in app/db.py (now removed).
All datetime fields are stored as PostgreSQL TIMESTAMP WITHOUT TIME ZONE
(matches SQLite TEXT 'YYYY-MM-DD HH:MM:SS' semantics — datetime.utcnow()).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer, text
from sqlmodel import Field, SQLModel


def _utcnow_col():
    """Server-side default for created_at / updated_at columns."""
    return {"server_default": text("CURRENT_TIMESTAMP")}


class Group(SQLModel, table=True):
    __tablename__ = "groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    target_phone_count: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    archived_at: Optional[datetime] = None


class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    geelark_phone_id: Optional[str] = Field(default=None, unique=True, index=True)
    profile_name: Optional[str] = Field(default=None, unique=True, index=True)
    email: Optional[str] = None
    tiktok_username: Optional[str] = None
    proxy: Optional[str] = None
    profile_pic_url: Optional[str] = None
    profile_pic_path: Optional[str] = None
    group_name: Optional[str] = None  # legacy, kept for backfill; group_id is the truth
    group_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("groups.id", ondelete="SET NULL")),
    )
    tiktok_logged_in: int = Field(default=0)
    pic_status: str = Field(default="none")
    pic_task_id: Optional[str] = None
    warmed_up: int = Field(default=0)
    time_spent_warming: int = Field(default=0)
    max_warm_time: int = Field(default=500)
    last_warm_at: Optional[datetime] = None
    last_warm_result: Optional[str] = None
    last_warm_notes: Optional[str] = None
    last_posted_at: Optional[datetime] = None
    proxy_status: Optional[str] = None
    proxy_checked_at: Optional[datetime] = None
    proxy_exit_ip: Optional[str] = None
    password: Optional[str] = None
    tiktok_password: Optional[str] = None
    recovery_email: Optional[str] = None
    mail_inbox_url: Optional[str] = None
    raw_credentials: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class Video(SQLModel, table=True):
    __tablename__ = "videos"

    id: Optional[int] = Field(default=None, primary_key=True)
    original_filename: Optional[str] = None
    stored_path: Optional[str] = None
    caption: Optional[str] = None
    account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL")),
    )
    group_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("groups.id", ondelete="SET NULL")),
    )
    result: str = Field(default="PENDING")
    geelark_task_id: Optional[str] = None
    notes: Optional[str] = None
    drive_file_id: Optional[str] = None
    uploaded_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    posted_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: Optional[str] = None


class JobRun(SQLModel, table=True):
    __tablename__ = "job_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_type: Optional[str] = None
    started_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    finished_at: Optional[datetime] = None
    status: str = Field(default="running")
    summary: Optional[str] = None
```

- [ ] **Step 3: Verify model import works**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -c "from app.models import Account, Video, Setting, JobRun, Group; print('OK:', [t.__tablename__ for t in [Group, Account, Video, Setting, JobRun]])"
```

Expected: `OK: ['groups', 'accounts', 'videos', 'settings', 'job_runs']`

- [ ] **Step 4: Commit**

```bash
git add app/models.py
git commit -m "$(cat <<'EOF'
feat(db): add SQLModel definitions for 5 tables (phase 1)

Mirror existing SQLite schema in app/db.py SCHEMA constant.
All datetime defaults use CURRENT_TIMESTAMP server-side (PG-compatible).
FK ondelete=SET NULL matches existing SQLite REFERENCES ... ON DELETE SET NULL.

Models not yet wired into app/db.py — that's the next step.
EOF
)"
```

---

## Task 4: Rewrite app/db.py — SQLAlchemy engine + get_session dependency

**Files:**
- Modify (rewrite): `app/db.py`
- Modify: `app/config.py` (add `DATABASE_URL`)

- [ ] **Step 1: Add DATABASE_URL to app/config.py**

Read current `app/config.py`. Add (placement: near existing env var reads):
```python
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL must be set in .env. "
        "Format: postgresql+psycopg://user:pass@host:port/dbname"
    )
```

If `app/config.py` already imports os, no extra import needed. Otherwise add `import os` at top.

- [ ] **Step 2: Rewrite app/db.py**

Replace entire content of `app/db.py` with:

```python
"""SQLAlchemy engine + session factory for the mkt persistence layer.

Schema is owned by Alembic (see alembic/). This module exposes:
- `engine` — SQLAlchemy engine (process-wide)
- `get_session()` — FastAPI dependency yielding a Session

Imported by both API routes (via Depends) and background tasks
(call get_session() directly as a contextmanager).
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import DATABASE_URL

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=5,
    # echo=True for SQL logging during debugging
)


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Yields a session; closes after request."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """For non-request callers (scheduler jobs, scripts). Same semantics
    as get_session but usable in a `with` block instead of `Depends`."""
    with Session(engine) as session:
        yield session
```

- [ ] **Step 3: Verify imports**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run python -c "from app.db import engine, get_session, session_scope; print('OK:', engine.dialect.name)"
```

Expected: `OK: postgresql`. (Assuming `DATABASE_URL` is set in `.env`.)

- [ ] **Step 4: Tests will fail now — that's expected**

```bash
uv run pytest -q 2>&1 | tail -10
```

Expected: errors, not 297 passed. Repositories still use the old `sqlite3.Connection` from the deleted parts of `db.py`. We'll fix in Tasks 5+. Just confirm the failures are about missing `init_schema`, `get_connection`, etc., not about Python syntax errors.

- [ ] **Step 5: Commit (broken state — intentional)**

```bash
git add app/db.py app/config.py
git commit -m "$(cat <<'EOF'
refactor(db): SQLAlchemy engine + session factory (phase 1)

Drops sqlite3.Connection, PRAGMA, hand-written _migrate(), and
init_schema. Tests fail at this commit — repositories still import
the deleted functions; fixed in subsequent commits within phase 1.

This is a known intermediate state, not a deployable commit.
Production stays on the prior commit until phase 1 is fully merged.
EOF
)"
```

**Important**: do NOT push this commit alone. Keep all phase 1 commits local until the phase is verified end-to-end, then push as a batch.

---

## Task 5: Rewrite repositories — all 5, one commit per repo

Five repository files to rewrite. Strategy per file:
1. Read current SQL file (e.g. `app/repositories/accounts.py`).
2. Find every function: identify intent (SELECT / INSERT / UPDATE / DELETE).
3. Rewrite using SQLModel `session.exec(select(...))` for SELECTs, model construction for INSERTs, attribute assignment + commit for UPDATEs, `session.delete()` for DELETEs.
4. Keep the public function signatures EXCEPT replace `conn: sqlite3.Connection` with `session: Session`.
5. Run tests for just that repo (`pytest tests/test_repos_<name>.py -v` if exists, or full suite skipping known-broken).
6. Commit.

Each file gets its own task subsection below.

### Task 5a: app/repositories/groups.py (smallest, do first)

**Files:**
- Rewrite: `app/repositories/groups.py`

- [ ] **Step 1: Read current file** (38 lines)

- [ ] **Step 2: Rewrite using SQLModel**

The file's public functions (from Phase 0 inventory): `list_all`, `create`, `archive`, etc. Replace each with SQLModel equivalents. Example pattern:

```python
from sqlmodel import Session, select
from app.models import Group

def list_active(session: Session) -> list[Group]:
    stmt = select(Group).where(Group.archived_at.is_(None)).order_by(Group.name)
    return list(session.exec(stmt).all())

def create(session: Session, name: str, target: int | None = None, notes: str | None = None) -> Group:
    g = Group(name=name, target_phone_count=target, notes=notes)
    session.add(g)
    session.commit()
    session.refresh(g)
    return g

def archive(session: Session, group_id: int) -> None:
    g = session.get(Group, group_id)
    if g is None:
        return
    g.archived_at = datetime.utcnow()
    session.add(g)
    session.commit()
```

Match function names to whatever the original file exposed. Do NOT add new functions or remove existing ones (signature/behavior preservation).

- [ ] **Step 3: Run any tests that exercise groups**

```bash
uv run pytest -q -k "group" 2>&1 | tail -10
```

Expected: passing for tests that don't depend on other broken repos. If all `group` tests pass, proceed.

- [ ] **Step 4: Commit**

```bash
git add app/repositories/groups.py
git commit -m "refactor(repo): groups uses SQLModel (phase 1)"
```

### Task 5b: app/repositories/settings.py

**Files:**
- Rewrite: `app/repositories/settings.py`

- [ ] **Step 1: Read current file** (13 lines)

- [ ] **Step 2: Rewrite**

The current API has `get_all(conn)` returning `dict[str, str]` and an upsert. SQLModel equivalent:

```python
from sqlmodel import Session, select
from app.models import Setting

def get_all(session: Session) -> dict[str, str]:
    rows = session.exec(select(Setting)).all()
    return {r.key: r.value for r in rows if r.value is not None}

def upsert(session: Session, key: str, value: str) -> None:
    existing = session.get(Setting, key)
    if existing is None:
        session.add(Setting(key=key, value=value))
    else:
        existing.value = value
        session.add(existing)
    session.commit()
```

- [ ] **Step 3: Verify**

```bash
uv run pytest -q -k "setting" 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add app/repositories/settings.py
git commit -m "refactor(repo): settings uses SQLModel (phase 1)"
```

### Task 5c: app/repositories/job_runs.py

**Files:**
- Rewrite: `app/repositories/job_runs.py`

- [ ] **Step 1: Read current file** (19 lines)

- [ ] **Step 2: Rewrite**

API per Phase 0 inventory: `start(conn, job_type) -> int` and `finish(conn, jid, status, summary)`. SQLModel:

```python
from datetime import datetime
from sqlmodel import Session
from app.models import JobRun

def start(session: Session, job_type: str) -> int:
    j = JobRun(job_type=job_type, status="running")
    session.add(j)
    session.commit()
    session.refresh(j)
    return j.id

def finish(session: Session, jid: int, status: str, summary: str | None) -> None:
    j = session.get(JobRun, jid)
    if j is None:
        return
    j.finished_at = datetime.utcnow()
    j.status = status
    j.summary = summary
    session.add(j)
    session.commit()
```

- [ ] **Step 3: Verify + Step 4: Commit**

```bash
uv run pytest -q -k "job" 2>&1 | tail -10
git add app/repositories/job_runs.py
git commit -m "refactor(repo): job_runs uses SQLModel (phase 1)"
```

### Task 5d: app/repositories/videos.py

**Files:**
- Rewrite: `app/repositories/videos.py`

- [ ] **Step 1: Read current file** (76 lines)

Map all functions. Common operations: `list_all`, `get`, `create`, `mark_initiated`, `mark_success`, `mark_failure`, `mark_published`, `pending_initiated`, etc.

- [ ] **Step 2: Rewrite**

Pattern for each function. Examples:

```python
from datetime import datetime
from sqlmodel import Session, select
from app.models import Video

def list_all(session: Session) -> list[Video]:
    return list(session.exec(select(Video).order_by(Video.id.desc())).all())

def get(session: Session, vid: int) -> Video | None:
    return session.get(Video, vid)

def create(session: Session, **kwargs) -> Video:
    v = Video(**kwargs)
    session.add(v)
    session.commit()
    session.refresh(v)
    return v

def mark_initiated(session: Session, vid: int, geelark_task_id: str, notes: str | None) -> None:
    v = session.get(Video, vid)
    if v is None: return
    v.result = "INITIATED"
    v.geelark_task_id = geelark_task_id
    v.notes = notes
    v.posted_at = datetime.utcnow()
    session.add(v)
    session.commit()

def mark_result(session: Session, vid: int, result: str, notes: str | None) -> None:
    v = session.get(Video, vid)
    if v is None: return
    v.result = result
    v.notes = notes
    v.last_checked_at = datetime.utcnow()
    session.add(v)
    session.commit()

def pending_initiated(session: Session) -> list[Video]:
    stmt = select(Video).where(Video.result == "INITIATED")
    return list(session.exec(stmt).all())
```

Match function names exactly to the original — services call these by name.

- [ ] **Step 3: Verify + Step 4: Commit**

```bash
uv run pytest -q -k "video" 2>&1 | tail -10
git add app/repositories/videos.py
git commit -m "refactor(repo): videos uses SQLModel (phase 1)"
```

### Task 5e: app/repositories/accounts.py (largest, 161 lines)

**Files:**
- Rewrite: `app/repositories/accounts.py`

- [ ] **Step 1: Read current file**

Inventory all functions and their SQL.

- [ ] **Step 2: Rewrite**

Same pattern as 5d. For the dynamic UPDATE that builds `SET <col>=?, ...` from a dict (around line 60-64 of the original), use:

```python
def update_fields(session: Session, account_id: int, fields: dict) -> None:
    a = session.get(Account, account_id)
    if a is None: return
    for k, v in fields.items():
        if hasattr(a, k):
            setattr(a, k, v)
    a.updated_at = datetime.utcnow()
    session.add(a)
    session.commit()
```

For batch UPDATE WHERE id IN (...) (around line 140), use:
```python
def bulk_update_field(session: Session, ids: list[int], field: str, value) -> None:
    stmt = select(Account).where(Account.id.in_(ids))
    rows = session.exec(stmt).all()
    for a in rows:
        setattr(a, field, value)
        a.updated_at = datetime.utcnow()
        session.add(a)
    session.commit()
```

- [ ] **Step 3: Verify**

```bash
uv run pytest -q -k "account" 2>&1 | tail -15
```

Expected: most account tests pass. Some may still fail if services that call them haven't been touched yet (next task).

- [ ] **Step 4: Commit**

```bash
git add app/repositories/accounts.py
git commit -m "refactor(repo): accounts uses SQLModel (phase 1)"
```

---

## Task 6: Update services and routes to pass Session instead of Connection

**Files:**
- Modify: `app/api/*.py` (all route files using DB)
- Modify: `app/services/*.py` (all service files calling repos)
- Modify: `app/main.py` (drop `db_conn` thread-local, replace with `get_session` dep)
- Modify: `app/scheduler.py` (its `_run` wrapper opens a session)

- [ ] **Step 1: Survey impact**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
grep -rn "sqlite3.Connection\|db_conn\|get_connection" app/ | head -30
```

This shows every spot needing the signature change.

- [ ] **Step 2: Replace `db_conn` in `app/main.py`**

Find the function (around line that defines `def db_conn():` returning a thread-local sqlite3.Connection). Remove it entirely. Update any `Depends(db_conn)` in main.py to `Depends(get_session)`.

- [ ] **Step 3: Update each route file (`app/api/*.py`)**

For each file, find function signatures like:
```python
def list_accounts(conn: sqlite3.Connection = Depends(db_conn)):
```
Change to:
```python
def list_accounts(session: Session = Depends(get_session)):
```

Add the import: `from sqlmodel import Session` + `from app.db import get_session`.
Update calls inside: `accounts_repo.list_all(conn)` → `accounts_repo.list_all(session)`.

- [ ] **Step 4: Update each service file (`app/services/*.py`)**

Same pattern. Function signatures that previously took `conn: sqlite3.Connection` now take `session: Session`. Internal calls pass `session` to repo functions.

- [ ] **Step 5: Update `app/scheduler.py`**

Find the `_run` function (Phase 0 inventory shows it does `conn = get_connection(); try: ... finally: conn.close()`). Change to:
```python
from app.db import session_scope

def _run(job_type, fn, *, lock=True):
    with session_scope() as session:
        jid = jobs_repo.start(session, job_type)
        try:
            if lock:
                from app.locks import OPERATION_LOCK
                with OPERATION_LOCK:
                    summary = fn(session)
            else:
                summary = fn(session)
            jobs_repo.finish(session, jid, "ok", summary)
        except Exception as e:
            jobs_repo.finish(session, jid, "error", str(e))
```

- [ ] **Step 6: Verify imports clean**

```bash
uv run python -c "from app.main import app; print('OK')"
```

Expected: `OK`. If ImportError, fix the missing import and retry.

- [ ] **Step 7: Run tests (PG-backed)**

Before running, ensure local `DATABASE_URL` points at `mkt_test` (an empty DB). Then:

```bash
uv run pytest -q 2>&1 | tail -15
```

Expected: most tests fail because tables don't exist yet (Alembic baseline is Task 7). That's expected. Verify the failures are "relation does not exist" (PG telling you tables aren't there), NOT Python errors.

- [ ] **Step 8: Commit**

```bash
git add app/api/ app/services/ app/main.py app/scheduler.py
git commit -m "$(cat <<'EOF'
refactor(db): routes and services use SQLModel Session (phase 1)

Replaces sqlite3.Connection Depends(db_conn) with Session
Depends(get_session) everywhere. Internal: services pass session
to repo functions. Scheduler uses session_scope().

Tests fail with "relation does not exist" — tables are created
by Alembic in the next task.
EOF
)"
```

---

## Task 7: Alembic — initialize, baseline migration, apply

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`
- Create: `alembic/versions/0001_baseline.py`

- [ ] **Step 1: Initialize Alembic**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
uv run alembic init alembic
```

This creates `alembic.ini` + `alembic/` directory. Verify with `ls alembic/`.

- [ ] **Step 2: Configure alembic.ini**

Edit `alembic.ini`. Change:
```
sqlalchemy.url = driver://user:pass@localhost/dbname
```
To use env var resolution at runtime (we'll set this in env.py instead):
```
sqlalchemy.url =
```
Keep empty; env.py reads from app.config.

- [ ] **Step 3: Configure alembic/env.py**

Replace the body of `alembic/env.py` with:

```python
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Import our models so Alembic sees their metadata
from app.config import DATABASE_URL
from app.models import SQLModel  # SQLModel.metadata holds all tables

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate baseline migration**

```bash
uv run alembic revision --autogenerate -m "baseline"
```

Expected: creates `alembic/versions/<hex>_baseline.py` with `op.create_table()` calls for all 5 tables. Open it and sanity-check the generated SQL.

- [ ] **Step 5: Apply baseline to local mkt_dev**

```bash
uv run alembic upgrade head
```

Expected: output like `INFO  [alembic.runtime.migration] Running upgrade  -> <hex>, baseline`.

Verify tables exist:
```bash
psql mkt_dev -c "\dt"
```
Expected: lists `accounts`, `videos`, `settings`, `job_runs`, `groups`, `alembic_version`.

- [ ] **Step 6: Apply to mkt_test too**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run alembic upgrade head
```

- [ ] **Step 7: Run tests against mkt_test**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -15
```

Expected: many more tests pass now (tables exist). Some may still fail due to conftest needing PG fixtures (Task 8 fixes).

- [ ] **Step 8: Commit**

```bash
git add alembic.ini alembic/
git commit -m "$(cat <<'EOF'
feat(db): alembic baseline migration (phase 1)

Initializes Alembic against SQLModel.metadata. The baseline migration
creates all 5 tables matching app/models.py. Production runs
`uv run alembic upgrade head` during deploy.
EOF
)"
```

---

## Task 8: Update test infrastructure for PG

**Files:**
- Modify: `tests/conftest.py` (or create if absent)
- Possibly modify: a few tests using SQLite-specific syntax

- [ ] **Step 1: Inspect current conftest**

```bash
ls tests/conftest.py 2>&1
cat tests/conftest.py 2>&1 | head -50
```

If it doesn't exist, create it. If it exists, identify the DB fixture pattern (likely creates a SQLite file).

- [ ] **Step 2: Write/rewrite conftest.py for PG**

```python
"""Pytest fixtures for PG-backed tests.

Each test runs against TRUNCATEd tables (faster than DROP/CREATE).
DATABASE_URL env var must point to a dedicated test DB
(e.g. mkt_test). NEVER point at production.
"""

import os
import pytest
from sqlalchemy import text
from sqlmodel import Session

# Sanity check: refuse to run against prod
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if "mkt_test" not in DATABASE_URL and "test" not in DATABASE_URL:
    raise RuntimeError(
        f"DATABASE_URL ({DATABASE_URL!r}) doesn't look like a test DB. "
        "Tests refuse to run. Set DATABASE_URL=postgresql+psycopg://...mkt_test"
    )

# Import after the env check
from app.db import engine
from app.models import SQLModel


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Once per pytest session: ensure schema exists (idempotent)."""
    SQLModel.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _truncate_tables():
    """Before each test: clear all rows. Faster than dropping + recreating."""
    with engine.begin() as conn:
        # Order doesn't matter with TRUNCATE ... CASCADE
        conn.execute(text(
            "TRUNCATE TABLE "
            "videos, accounts, groups, settings, job_runs "
            "RESTART IDENTITY CASCADE"
        ))
    yield


@pytest.fixture
def session():
    """Yields a session bound to the test DB."""
    with Session(engine) as s:
        yield s
```

- [ ] **Step 3: Update tests using old fixture name**

If tests previously used a fixture named `conn` or similar:
```bash
grep -rn "def test_.*(conn)" tests/ | head
```

Rename `conn` → `session` in test signatures. If many, batch with sed:
```bash
# Mac sed (BSD)
find tests -name "test_*.py" -exec sed -i '' 's/def test_\([a-z_]*\)(conn)/def test_\1(session)/g' {} \;
find tests -name "test_*.py" -exec sed -i '' 's/(conn,/(session,/g' {} \;
```

Inspect git diff to confirm sane changes:
```bash
git diff tests/ | head -50
```

- [ ] **Step 4: Run full suite**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q 2>&1 | tail -15
```

Expected: 297 passed (back to the green baseline). If some still fail, investigate per-test — common causes: SQLite-specific functions (`datetime('now')` used in test assertions instead of `datetime.utcnow()`), or order-dependent assertions (autoincrement IDs may differ between SQLite and PG sequences — both start at 1 but PG sequences may be off if RESTART IDENTITY wasn't run).

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
test(db): conftest fixtures for PG test database (phase 1)

Schema is created once per session; each test starts with TRUNCATEd
tables (RESTART IDENTITY CASCADE). Fixture refuses to run if
DATABASE_URL doesn't contain "test".

All 297 tests pass against mkt_test PG database.
EOF
)"
```

---

## Task 9: SQLite → PG data migration script

**Files:**
- Create: `scripts/migrate_sqlite_to_pg.py`

This script is run ONCE during production deploy: read SQLite, write PG. Local dev never needs it (mkt_dev starts empty).

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""One-time data migration from SQLite to PostgreSQL.

Reads /opt/mkt/data/app.db (SQLite), writes via SQLModel into
the configured DATABASE_URL (PG). Run on the production server
between `alembic upgrade head` and `systemctl restart mkt`.

Tables migrated in FK order:
  groups -> accounts -> videos -> settings -> job_runs
"""

import sqlite3
import sys
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from app.db import engine
from app.models import Account, Group, JobRun, Setting, Video

SQLITE_PATH = Path("/opt/mkt/data/app.db")  # adjust for local runs
# For local testing: pass --sqlite=<path> via argv

MODELS_IN_FK_ORDER = [
    ("groups", Group),
    ("accounts", Account),
    ("videos", Video),
    ("settings", Setting),
    ("job_runs", JobRun),
]


def migrate(sqlite_path: Path) -> None:
    if not sqlite_path.exists():
        print(f"ERROR: SQLite file not found at {sqlite_path}")
        sys.exit(1)

    sconn = sqlite3.connect(str(sqlite_path))
    sconn.row_factory = sqlite3.Row

    with Session(engine) as session:
        for table, model_cls in MODELS_IN_FK_ORDER:
            count = _migrate_table(sconn, session, table, model_cls)
            print(f"  {table}: {count} rows migrated")
        session.commit()

    # Re-sync PG sequences so future INSERTs don't collide with migrated IDs
    print("Resetting PG sequences...")
    with engine.begin() as conn:
        for table, model_cls in MODELS_IN_FK_ORDER:
            if hasattr(model_cls, "id"):
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
                ))

    # Verify row counts match
    print("\nVerification (SQLite count vs PG count):")
    with Session(engine) as session:
        for table, model_cls in MODELS_IN_FK_ORDER:
            sqlite_n = sconn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            pg_n = session.exec(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            ok = "OK" if sqlite_n == pg_n else "MISMATCH"
            print(f"  {table}: sqlite={sqlite_n}  pg={pg_n}  [{ok}]")
            if sqlite_n != pg_n:
                print("  ABORT: row count mismatch")
                sys.exit(2)

    sconn.close()
    print("\nMigration complete.")


def _migrate_table(sconn, session: Session, table: str, model_cls) -> int:
    rows = sconn.execute(f"SELECT * FROM {table}").fetchall()
    count = 0
    for row in rows:
        # Construct model from row, only using columns that exist in the model
        data = {k: row[k] for k in row.keys() if hasattr(model_cls, k)}
        obj = model_cls(**data)
        session.add(obj)
        count += 1
    return count


if __name__ == "__main__":
    path = SQLITE_PATH
    for arg in sys.argv[1:]:
        if arg.startswith("--sqlite="):
            path = Path(arg.split("=", 1)[1])
    migrate(path)
```

- [ ] **Step 2: Test against a local copy of production data**

Copy production SQLite locally for testing:
```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  root@139.196.52.92:/opt/mkt/data/app.db /tmp/prod-app.db
```

Reset local mkt_dev:
```bash
psql mkt_dev -c "TRUNCATE accounts, videos, settings, job_runs, groups RESTART IDENTITY CASCADE;"
```

Run migration script:
```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_dev \
  uv run python scripts/migrate_sqlite_to_pg.py --sqlite=/tmp/prod-app.db
```

Expected: all 5 tables migrated, verification shows matched counts. If any "MISMATCH" appears, abort and investigate the script.

- [ ] **Step 3: Smoke-test the app against migrated data**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_dev \
  uv run uvicorn app.main:app --port 8083 --workers 1 &
SERVER_PID=$!
sleep 3
curl -s -o /dev/null -w "/ -> %{http_code}\n" http://127.0.0.1:8083/
kill $SERVER_PID 2>/dev/null
```

Expected: 200. (If 500, the migration produced data the app can't read.)

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_sqlite_to_pg.py
git commit -m "$(cat <<'EOF'
feat(db): one-time SQLite -> PG migration script (phase 1)

scripts/migrate_sqlite_to_pg.py reads /opt/mkt/data/app.db and writes
into the configured DATABASE_URL via SQLModel. FK order:
groups -> accounts -> videos -> settings -> job_runs.

Resets PG sequences after migration so future INSERTs don't collide
with migrated IDs. Verifies row counts; aborts on mismatch.
EOF
)"
```

---

## Task 10: Production deploy

**Files:** none locally; this task runs deploy commands.

This task is the production rollout. Treat as high-risk — production already serves traffic.

- [ ] **Step 1: Final local verification**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest -q
```

Expected: 297 passed.

- [ ] **Step 2: Push commits to origin**

```bash
git push origin main
```

- [ ] **Step 3: Backup production app.db (rollback path)**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'cp /opt/mkt/data/app.db /opt/mkt/data/app.db.bak.phase1-$(date +%Y%m%d-%H%M%S) && ls -lh /opt/mkt/data/app.db*'
```

Expected: two files visible, both ~540K.

- [ ] **Step 4: Stop mkt service**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 'systemctl stop mkt'
```

(From this point until Step 11, the dashboard is OFFLINE. Plan for a deploy window.)

- [ ] **Step 5: Add DATABASE_URL to production .env**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'echo "" >> /opt/mkt/.env && echo "DATABASE_URL=postgresql+psycopg://mkt:<PASSWORD_FROM_TASK_2>@127.0.0.1:5432/mkt" >> /opt/mkt/.env'
```

Replace `<PASSWORD_FROM_TASK_2>` with the actual password generated in Task 2 Step 2.

- [ ] **Step 6: Build + tarball locally**

```bash
cd /Users/Zhuanz/Documents/geelark-studio
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build && cd ..
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-phase1.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app geelark_client.py generate_caption.py \
  pyproject.toml uv.lock \
  alembic.ini alembic \
  scripts \
  frontend/dist
ls -lh /tmp/mkt-phase1.tar.gz
```

- [ ] **Step 7: Upload tarball**

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-phase1.tar.gz root@139.196.52.92:/tmp/mkt-phase1.tar.gz
```

- [ ] **Step 8: Extract, sync deps, run alembic, migrate data, restart**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/usr/local/bin:/root/.local/bin:$PATH"
  cd /opt/mkt
  tar -xzf /tmp/mkt-phase1.tar.gz
  chown -R root:root app alembic alembic.ini scripts geelark_client.py generate_caption.py pyproject.toml uv.lock frontend/dist
  uv sync --no-dev
  echo "=== alembic upgrade head ==="
  uv run alembic upgrade head
  echo "=== migrate data ==="
  uv run python scripts/migrate_sqlite_to_pg.py
  echo "=== restart service ==="
  systemctl start mkt
  sleep 5
  systemctl is-active mkt
  rm -f /tmp/mkt-phase1.tar.gz
'
```

Expected: `alembic upgrade head` succeeds, migration script reports matched counts for all 5 tables, service is `active`.

- [ ] **Step 9: Smoke test endpoints**

```bash
curl -sS -o /dev/null -w "/ -> %{http_code}\n" http://139.196.52.92:8080/
curl -sS -o /dev/null -w "/accounts -> %{http_code}\n" http://139.196.52.92:8080/accounts
curl -sS -o /dev/null -w "/api/accounts -> %{http_code}\n" http://139.196.52.92:8080/api/accounts
```

Expected: 200 / 200 / 401.

- [ ] **Step 10: Manual UI verification**

Open `http://139.196.52.92:8080/` in browser, log in, visit Accounts/Videos/Settings tabs. Spot-check that data appears (the same data as before — migration should have preserved everything).

- [ ] **Step 11: Verify scheduler still runs**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'journalctl -u mkt -n 30 --no-pager | grep -i "scheduler\|job\|warmup\|pic_check\|status_check\|proxy_check"'
```

Wait at least 1 minute after service start; you should see status_check or pic_check fire (their cadence is 1 min and 10 min respectively). If nothing fires within 5 minutes, escalate.

- [ ] **Step 12: Document and close phase**

The old `data/app.db` SQLite file is now stale but preserved as `data/app.db.bak.phase1-<timestamp>` for 7 days. Calendar a reminder to delete it after the rollback window.

No commit needed for this task — it's a deploy. Push of code happened in Step 2.

---

## Phase 1 Completion Checklist

- [ ] All 5 SQLModel models defined in `app/models.py`
- [ ] `app/db.py` uses SQLAlchemy engine + Session
- [ ] All 5 repositories use SQLModel (no `sqlite3.Connection` left in app code)
- [ ] All routes/services use `Session = Depends(get_session)`
- [ ] Alembic baseline migration exists and applies cleanly
- [ ] `tests/conftest.py` uses PG fixtures; refuses non-test DB
- [ ] 297 tests pass against `mkt_test`
- [ ] `scripts/migrate_sqlite_to_pg.py` runs cleanly against a copy of production data
- [ ] Production `mkt` database created, populated, app running
- [ ] Smoke tests + manual UI verification + scheduler activity confirmed
- [ ] Old SQLite file preserved as `.bak.phase1-<ts>` for 7-day rollback window

## Rollback (within 24h of deploy)

Per spec: "阶段 1 上线后 24h 内回滚可接受丢一天数据".

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  systemctl stop mkt
  cd /opt/mkt
  # Restore code to pre-phase-1 (last phase-0 commit)
  git checkout 13434a6  # would require git deploy mode; current tarball deploy has no checkout
  # Alternative for tarball deploy: re-extract a saved phase-0 tarball
  # Remove DATABASE_URL from .env so config.py raises before connecting
  sed -i "/^DATABASE_URL=/d" .env
  systemctl start mkt
'
```

The rollback window is 24h. Past that, any new data in PG would be lost on revert; the cost grows daily.

## After Phase 1 lands

Pause point 2 per spec:
- Is PG stable? Any unexpected query slowness?
- Did the migration preserve all rows exactly?
- Any scheduler / threading.Lock interactions surfaced now that we're on PG (the GIL no longer protects DB writes)?

If yes → Phase 2 plan (Procrastinate + worker split).
If no → stop here; Phase 1 is independently valuable (PG-backed, SQLModel, Alembic).
