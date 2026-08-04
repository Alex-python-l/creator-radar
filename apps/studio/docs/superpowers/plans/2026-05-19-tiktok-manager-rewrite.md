# TikTok Manager Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Google-Sheet/Drive-driven script collection with a single-process FastAPI + SQLite + React SPA app where all TikTok-account operations happen in one web UI, with background scheduling for warmup and status polling.

**Architecture:** FastAPI backend layered as routes → services → reused `geelark_client.py`/`generate_caption.py` + SQLite (WAL). APScheduler runs in-process for warmup/status/pic polling. React+Vite SPA is built to static files and served by FastAPI on one port. Single worker, single user.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, SQLite (stdlib `sqlite3`, WAL), APScheduler, pytest + httpx, React 18 + Vite + TypeScript.

Reference spec: `docs/superpowers/specs/2026-05-19-tiktok-manager-rewrite-design.md`

---

## File Structure

```
mkt/
  geelark_client.py            # REUSED unchanged
  generate_caption.py          # REUSED (generate_caption_for_local_file used directly)
  requirements.txt             # REWRITTEN (drop google libs, add fastapi stack)
  app/
    __init__.py
    config.py                  # env loading, paths, settings defaults
    db.py                      # sqlite connect (WAL), schema init
    repositories/
      __init__.py
      accounts.py
      videos.py
      settings.py
      job_runs.py
    services/
      __init__.py
      assignment.py            # ported 2h-cooldown / oldest-first algorithm
      geelark_service.py       # create / import / pics / stop wrappers
      warmup_service.py        # warmup batch run
      posting_service.py       # upload->assign->caption->post
    scheduler.py               # APScheduler job registration
    auth.py                    # single-password session login
    sse.py                     # in-process job log pub/sub for SSE
    api/
      __init__.py
      accounts.py
      videos.py
      dashboard.py
      settings.py
      phones.py
      jobs.py
    main.py                    # FastAPI app, routers, static mount, startup
  frontend/                    # React + Vite + TS (built to frontend/dist)
  tests/
    conftest.py
    test_db.py
    test_repositories.py
    test_assignment.py
    test_services.py
    test_api.py
  data/                        # app.db lives here (gitignored)
```

Old files removed in the final cleanup task: `app.py`, `templates/`, `video_utils.py`, `post_videos.py`, `post_from_drive.py`, `create_profiles.py`, `warm_accounts.py`, `set_profile_pics.py`, `check_video_status.py`, `stop_all_phones.py`, `normalize_profiles.py`, `full_reset_normalize.py`, `recover_duplicates.py`, `import_geelark_profiles.py`, `sync_groups.py`, `verify_profiles.py`, `test_tiktok_login.py`, `docs/workflow/`.

---

## Phase 0 — Scaffolding

### Task 0: Project skeleton and dependencies

**Files:**
- Modify: `requirements.txt`
- Create: `app/__init__.py`, `app/repositories/__init__.py`, `app/services/__init__.py`, `app/api/__init__.py`, `tests/__init__.py`
- Create: `.gitignore` (append), `pytest.ini`

- [ ] **Step 1: Rewrite `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
APScheduler>=3.10.0
python-multipart>=0.0.9
python-dotenv>=1.0.0
requests>=2.31.0
google-genai>=1.0.0
pytest>=8.0.0
httpx>=0.27.0
```

(Google Sheets/Drive libs `gspread`, `google-auth`, `google-api-python-client` are dropped. `google-genai` stays for caption generation; `requests` stays for `geelark_client.py`.)

- [ ] **Step 2: Create empty package files**

Create `app/__init__.py`, `app/repositories/__init__.py`, `app/services/__init__.py`, `app/api/__init__.py`, `tests/__init__.py` each containing a single newline.

- [ ] **Step 3: Add `pytest.ini`**

```ini
[pytest]
testpaths = tests
filterwarnings = ignore::DeprecationWarning
```

- [ ] **Step 4: Append to `.gitignore`**

```
data/
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 5: Install and verify**

Run: `./.venv/bin/pip install -q -r requirements.txt && ./.venv/bin/python -c "import fastapi, apscheduler, httpx; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt app tests pytest.ini .gitignore
git commit -m "chore: scaffold FastAPI project, swap deps"
```

---

## Phase 1 — Database layer

### Task 1: SQLite connection with WAL + schema

**Files:**
- Create: `app/config.py`
- Create: `app/db.py`
- Test: `tests/conftest.py`, `tests/test_db.py`

- [ ] **Step 1: Create `app/config.py`**

```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "app.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

GEELARK_TOKEN = os.getenv("GEELARK_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")
SESSION_SECRET = os.getenv("DASHBOARD_SECRET_KEY", "local-dev-secret-change-me")

SETTINGS_DEFAULTS = {
    "default_group": "AU Accounts",
    "default_os": "Android 15",
    "default_max_warm_time": "500",
    "warmup_times": "09:00,14:00,20:00",
    "post_cooldown_hours": "2",
}
```

- [ ] **Step 2: Write failing test `tests/test_db.py`**

```python
from app.db import get_connection, init_schema

def test_wal_enabled(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "t.db")
    conn = get_connection()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"

def test_schema_tables_created(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "t.db")
    conn = get_connection()
    init_schema(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"accounts", "videos", "settings", "job_runs"} <= names
```

- [ ] **Step 3: Run test, verify it fails**

Run: `./.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 4: Create `app/db.py`**

```python
import sqlite3
from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  geelark_phone_id TEXT,
  profile_name TEXT UNIQUE,
  email TEXT,
  proxy TEXT,
  profile_pic_url TEXT,
  group_name TEXT,
  tiktok_logged_in INTEGER DEFAULT 0,
  pic_status TEXT DEFAULT 'none',
  pic_task_id TEXT,
  warmed_up INTEGER DEFAULT 0,
  time_spent_warming INTEGER DEFAULT 0,
  max_warm_time INTEGER DEFAULT 500,
  last_warm_at TEXT,
  last_warm_result TEXT,
  last_warm_notes TEXT,
  last_posted_at TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  original_filename TEXT,
  stored_path TEXT,
  caption TEXT,
  account_id INTEGER REFERENCES accounts(id),
  result TEXT DEFAULT 'PENDING',
  geelark_task_id TEXT,
  notes TEXT,
  uploaded_at TEXT DEFAULT (datetime('now')),
  posted_at TEXT,
  last_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT,
  started_at TEXT DEFAULT (datetime('now')),
  finished_at TEXT,
  status TEXT DEFAULT 'running',
  summary TEXT
);
"""

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for k, v in config.SETTINGS_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()
```

- [ ] **Step 5: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_db.py -v`
Expected: 2 passed

- [ ] **Step 6: Add `tests/conftest.py` shared fixture**

```python
import pytest

@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "test.db")
    from app.db import get_connection, init_schema
    conn = get_connection()
    init_schema(conn)
    yield conn
    conn.close()
```

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/db.py tests/test_db.py tests/conftest.py
git commit -m "feat: sqlite WAL connection and schema"
```

---

## Phase 2 — Repositories

### Task 2: Settings & job_runs repositories

**Files:**
- Create: `app/repositories/settings.py`, `app/repositories/job_runs.py`
- Test: `tests/test_repositories.py`

- [ ] **Step 1: Write failing tests `tests/test_repositories.py`**

```python
from app.repositories import settings as settings_repo
from app.repositories import job_runs as jobs_repo

def test_settings_get_all_and_update(db):
    s = settings_repo.get_all(db)
    assert s["default_group"] == "AU Accounts"
    settings_repo.update(db, {"default_group": "US Accounts"})
    assert settings_repo.get_all(db)["default_group"] == "US Accounts"

def test_job_run_lifecycle(db):
    jid = jobs_repo.start(db, "warmup")
    jobs_repo.finish(db, jid, "ok", "8 accounts warmed")
    recent = jobs_repo.recent(db, limit=5)
    assert recent[0]["status"] == "ok"
    assert recent[0]["summary"] == "8 accounts warmed"
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_repositories.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.repositories.settings'`

- [ ] **Step 3: Create `app/repositories/settings.py`**

```python
import sqlite3

def get_all(conn: sqlite3.Connection) -> dict:
    return {r["key"]: r["value"]
            for r in conn.execute("SELECT key, value FROM settings")}

def update(conn: sqlite3.Connection, values: dict) -> None:
    for k, v in values.items():
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, str(v)))
    conn.commit()
```

- [ ] **Step 4: Create `app/repositories/job_runs.py`**

```python
import sqlite3

def start(conn: sqlite3.Connection, job_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO job_runs(job_type, status) VALUES (?, 'running')",
        (job_type,))
    conn.commit()
    return cur.lastrowid

def finish(conn: sqlite3.Connection, job_id: int, status: str, summary: str) -> None:
    conn.execute(
        "UPDATE job_runs SET finished_at=datetime('now'), status=?, summary=? "
        "WHERE id=?", (status, summary, job_id))
    conn.commit()

def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 5: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_repositories.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add app/repositories/settings.py app/repositories/job_runs.py tests/test_repositories.py
git commit -m "feat: settings and job_runs repositories"
```

### Task 3: Accounts repository

**Files:**
- Create: `app/repositories/accounts.py`
- Test: append to `tests/test_repositories.py`

- [ ] **Step 1: Append failing tests**

```python
from app.repositories import accounts as acc_repo

def test_account_create_list_update_delete(db):
    aid = acc_repo.create(db, {"profile_name": "p1", "email": "a@b.com",
                               "proxy": "socks5://x", "max_warm_time": 500})
    assert acc_repo.get(db, aid)["profile_name"] == "p1"
    acc_repo.update(db, aid, {"email": "c@d.com", "tiktok_logged_in": 1})
    assert acc_repo.get(db, aid)["email"] == "c@d.com"
    assert acc_repo.get(db, aid)["tiktok_logged_in"] == 1
    acc_repo.delete(db, aid)
    assert acc_repo.get(db, aid) is None

def test_upsert_by_phone_id_for_import(db):
    acc_repo.upsert_by_phone_id(db, "ph1", {"profile_name": "imp1",
                                            "group_name": "G", "proxy": ""})
    acc_repo.upsert_by_phone_id(db, "ph1", {"profile_name": "imp1-renamed",
                                            "group_name": "G", "proxy": ""})
    rows = acc_repo.list_all(db)
    assert len(rows) == 1
    assert rows[0]["profile_name"] == "imp1-renamed"
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_repositories.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.repositories.accounts'`

- [ ] **Step 3: Create `app/repositories/accounts.py`**

```python
import sqlite3

_COLUMNS = {
    "geelark_phone_id", "profile_name", "email", "proxy", "profile_pic_url",
    "group_name", "tiktok_logged_in", "pic_status", "pic_task_id",
    "warmed_up", "time_spent_warming", "max_warm_time", "last_warm_at",
    "last_warm_result", "last_warm_notes", "last_posted_at",
}

def _filter(data: dict) -> dict:
    return {k: v for k, v in data.items() if k in _COLUMNS}

def create(conn: sqlite3.Connection, data: dict) -> int:
    d = _filter(data)
    cols = ", ".join(d)
    ph = ", ".join("?" for _ in d)
    cur = conn.execute(
        f"INSERT INTO accounts ({cols}) VALUES ({ph})", list(d.values()))
    conn.commit()
    return cur.lastrowid

def get(conn: sqlite3.Connection, account_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return dict(r) if r else None

def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM accounts ORDER BY id")]

def update(conn: sqlite3.Connection, account_id: int, data: dict) -> None:
    d = _filter(data)
    if not d:
        return
    sets = ", ".join(f"{k}=?" for k in d)
    conn.execute(
        f"UPDATE accounts SET {sets}, updated_at=datetime('now') WHERE id=?",
        [*d.values(), account_id])
    conn.commit()

def delete(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    conn.commit()

def upsert_by_phone_id(conn: sqlite3.Connection, phone_id: str, data: dict) -> None:
    row = conn.execute(
        "SELECT id FROM accounts WHERE geelark_phone_id=?", (phone_id,)).fetchone()
    if row:
        update(conn, row["id"], data)
    else:
        create(conn, {**data, "geelark_phone_id": phone_id})
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_repositories.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/repositories/accounts.py tests/test_repositories.py
git commit -m "feat: accounts repository"
```

### Task 4: Videos repository

**Files:**
- Create: `app/repositories/videos.py`
- Test: append to `tests/test_repositories.py`

- [ ] **Step 1: Append failing tests**

```python
from app.repositories import videos as vid_repo

def test_video_pending_and_status(db):
    vid = vid_repo.create(db, "clip.mp4", "/data/uploads/clip.mp4")
    assert vid_repo.list_pending(db)[0]["id"] == vid
    vid_repo.mark_initiated(db, vid, account_id=None, caption="cap",
                            task_id="t1", notes="submitted")
    assert vid_repo.list_pending(db) == []
    assert vid_repo.list_initiated(db)[0]["geelark_task_id"] == "t1"
    vid_repo.set_result(db, vid, "SUCCESS", "done")
    assert vid_repo.get(db, vid)["result"] == "SUCCESS"
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_repositories.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.repositories.videos'`

- [ ] **Step 3: Create `app/repositories/videos.py`**

```python
import sqlite3

def create(conn: sqlite3.Connection, filename: str, stored_path: str) -> int:
    cur = conn.execute(
        "INSERT INTO videos (original_filename, stored_path, result) "
        "VALUES (?, ?, 'PENDING')", (filename, stored_path))
    conn.commit()
    return cur.lastrowid

def get(conn: sqlite3.Connection, vid: int) -> dict | None:
    r = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    return dict(r) if r else None

def list_pending(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM videos WHERE result='PENDING' ORDER BY id")]

def list_initiated(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM videos WHERE result='INITIATED' "
        "AND geelark_task_id IS NOT NULL ORDER BY id")]

def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM videos ORDER BY id DESC")]

def mark_initiated(conn, vid, account_id, caption, task_id, notes) -> None:
    conn.execute(
        "UPDATE videos SET result='INITIATED', account_id=?, caption=?, "
        "geelark_task_id=?, notes=?, posted_at=datetime('now') WHERE id=?",
        (account_id, caption, task_id, notes, vid))
    conn.commit()

def mark_failure(conn, vid, account_id, notes) -> None:
    conn.execute(
        "UPDATE videos SET result='FAILURE', account_id=?, notes=?, "
        "posted_at=datetime('now') WHERE id=?", (account_id, notes, vid))
    conn.commit()

def set_result(conn, vid, result, notes) -> None:
    conn.execute(
        "UPDATE videos SET result=?, notes=?, last_checked_at=datetime('now') "
        "WHERE id=?", (result, notes, vid))
    conn.commit()

def clear_stored_path(conn, vid) -> None:
    conn.execute("UPDATE videos SET stored_path=NULL WHERE id=?", (vid,))
    conn.commit()

def delete(conn, vid) -> None:
    conn.execute("DELETE FROM videos WHERE id=?", (vid,))
    conn.commit()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_repositories.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories.py
git commit -m "feat: videos repository"
```

---

## Phase 3 — Domain services

### Task 5: Assignment algorithm (ported from video_utils)

**Files:**
- Create: `app/services/assignment.py`
- Test: `tests/test_assignment.py`

- [ ] **Step 1: Write failing test `tests/test_assignment.py`**

```python
import time
from app.services.assignment import build_pool, assign

def _acc(name, warmed, last_posted_epoch):
    return {"profile_name": name, "warmed_up": 1 if warmed else 0,
            "last_posted_at": last_posted_epoch}

def test_pool_excludes_unwarmed_and_sorts_oldest_first():
    accounts = [
        _acc("new", True, None),
        _acc("old", True, 1000),
        _acc("recent", True, 9_000_000_000),
        _acc("cold", False, None),
    ]
    pool = build_pool(accounts)
    assert [p["profile_name"] for p in pool] == ["new", "old", "recent"]

def test_assign_respects_cooldown_and_one_per_run():
    now = time.time()
    accounts = [
        _acc("free", True, None),
        _acc("hot", True, now - 600),   # posted 10 min ago -> excluded (2h)
    ]
    pool = build_pool(accounts)
    out = assign(["v1", "v2"], pool, cooldown_hours=2)
    assert out == [{"video": "v1", "profile_name": "free"}]
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_assignment.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.services.assignment'`

- [ ] **Step 3: Create `app/services/assignment.py`**

```python
"""Ported from the old video_utils build_warmed_profile_pool /
assign_videos_to_profiles. last_posted_at is an epoch float or None."""
import time

def build_pool(accounts: list[dict]) -> list[dict]:
    pool = []
    for a in accounts:
        if not a.get("warmed_up"):
            continue
        name = (a.get("profile_name") or "").strip()
        if not name:
            continue
        pool.append({"profile_name": name,
                     "last_posted_at": a.get("last_posted_at")})
    pool.sort(key=lambda p: p["last_posted_at"]
              if p["last_posted_at"] is not None else 0)
    return pool

def assign(videos: list, pool: list[dict], cooldown_hours: float = 2) -> list[dict]:
    cutoff = time.time() - cooldown_hours * 3600
    available = [p for p in pool
                 if p["last_posted_at"] is None or p["last_posted_at"] < cutoff]
    assignments = []
    for video in videos:
        if not available:
            break
        chosen = available.pop(0)
        assignments.append({"video": video,
                            "profile_name": chosen["profile_name"]})
    return assignments
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_assignment.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/assignment.py tests/test_assignment.py
git commit -m "feat: port video assignment algorithm"
```

### Task 6: GeeLark service (create / import / pics / stop)

**Files:**
- Create: `app/services/geelark_service.py`
- Test: `tests/test_services.py`

Note: `geelark_client.GeeLarkClient` is reused unchanged. Services take a client
instance so tests inject a fake. `last_posted_at` is stored in the DB as a
`datetime('now')` string; convert with `time.mktime(time.strptime(...))` when
building the pool — see Task 9 for the adapter.

- [ ] **Step 1: Write failing test `tests/test_services.py`**

```python
from app.services import geelark_service

class FakeClient:
    def __init__(self):
        self.stopped = False
    def list_phones(self, page=1, page_size=100):
        return {"total": 1, "items": [
            {"id": "ph1", "serialName": "R1", "group": {"name": "G1"}}]}
    def stop_phones(self, ids):
        self.stopped = True
        return {"successDetails": [{"id": i} for i in ids]}

def test_import_from_geelark_upserts(db):
    fake = FakeClient()
    n = geelark_service.import_from_geelark(db, fake)
    from app.repositories import accounts as acc_repo
    rows = acc_repo.list_all(db)
    assert n == 1
    assert rows[0]["geelark_phone_id"] == "ph1"
    assert rows[0]["profile_name"] == "R1"
    assert rows[0]["group_name"] == "G1"

def test_stop_all_phones_calls_client(db):
    fake = FakeClient()
    from app.repositories import accounts as acc_repo
    acc_repo.create(db, {"profile_name": "p", "geelark_phone_id": "ph1"})
    geelark_service.stop_all_phones(db, fake)
    assert fake.stopped is True
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.services.geelark_service'`

- [ ] **Step 3: Create `app/services/geelark_service.py`**

```python
"""Wraps the reused geelark_client.GeeLarkClient. All functions take an
explicit client so they are unit-testable with a fake."""
import sqlite3
from app.repositories import accounts as acc_repo

def make_client():
    from geelark_client import GeeLarkClient
    from app import config
    return GeeLarkClient(token=config.GEELARK_TOKEN)

def import_from_geelark(conn: sqlite3.Connection, client) -> int:
    """Pull every cloud phone and upsert an accounts row keyed by phone id.
    Only fields GeeLark actually returns are written; email/pic/warmup stay
    blank (documented migration tradeoff)."""
    imported = 0
    page = 1
    while True:
        data = client.list_phones(page=page, page_size=100)
        items = data.get("items", []) or data.get("list", [])
        if not items:
            break
        for it in items:
            phone_id = it.get("id") or it.get("envId")
            name = (it.get("serialName") or it.get("name") or "").strip()
            group = ""
            g = it.get("group")
            if isinstance(g, dict):
                group = g.get("name", "")
            elif isinstance(g, str):
                group = g
            proxy = it.get("proxy", "") if isinstance(it.get("proxy"), str) else ""
            acc_repo.upsert_by_phone_id(conn, phone_id, {
                "profile_name": name, "group_name": group, "proxy": proxy,
            })
            imported += 1
        if len(items) < 100:
            break
        page += 1
    return imported

def stop_all_phones(conn: sqlite3.Connection, client) -> dict:
    ids = [a["geelark_phone_id"] for a in acc_repo.list_all(conn)
           if a.get("geelark_phone_id")]
    if not ids:
        return {"stopped": 0}
    client.stop_phones(ids)
    return {"stopped": len(ids)}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/geelark_service.py tests/test_services.py
git commit -m "feat: geelark import and stop-all service"
```

### Task 7: Create-profiles & set-pics service

**Files:**
- Modify: `app/services/geelark_service.py`
- Test: append to `tests/test_services.py`

The old `create_profiles.py` flow: for each not-yet-created account, call
`client.create_phones([entry])`, then install TikTok via
`get_installable_apps`/`install_app`, then write `geelark_phone_id`. The old
`set_profile_pics.py` flow: if `pic_task_id` set, query it and resolve
done/failed; else submit `client.edit_tiktok_profile(env_id, avatar=url)` and
store task id with `pic_status='initiated'`.

- [ ] **Step 1: Append failing test**

```python
class FakeCreateClient:
    def __init__(self):
        self.created = []
        self.edited = []
        self.queried = []
    def create_phones(self, entries):
        self.created += entries
        return {"details": [{"id": "newph1", "serialName": entries[0]["serialName"]}]}
    def get_installable_apps(self, env_id, name=""):
        return {"items": [{"appVersionId": "tv1", "appName": "TikTok"}]}
    def install_app(self, env_id, app_version_id):
        return {}
    def edit_tiktok_profile(self, env_id, avatar=None, **kw):
        self.edited.append((env_id, avatar))
        return "pic_task_9"
    def query_tasks(self, ids):
        self.queried += ids
        return [{"id": ids[0], "status": 3}]

def test_create_profiles_sets_phone_id(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "R9", "proxy": "socks5://x"})
    fake = FakeCreateClient()
    log = list(geelark_service.create_profiles(db, fake, settings={
        "default_group": "G", "default_os": "Android 15"}))
    assert acc_repo.get(db, aid)["geelark_phone_id"] == "newph1"
    assert any("R9" in line for line in log)

def test_set_pics_submit_then_resolve(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "R9", "geelark_phone_id": "ph1",
                               "profile_pic_url": "http://x/a.jpg",
                               "tiktok_logged_in": 1})
    fake = FakeCreateClient()
    geelark_service.set_pics(db, fake)               # submit
    assert acc_repo.get(db, aid)["pic_status"] == "initiated"
    geelark_service.set_pics(db, fake)               # resolve (status 3 = done)
    assert acc_repo.get(db, aid)["pic_status"] == "done"
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: FAIL `AttributeError: module 'app.services.geelark_service' has no attribute 'create_profiles'`

- [ ] **Step 3: Append to `app/services/geelark_service.py`**

```python
def create_profiles(conn, client, settings: dict):
    """Generator yielding log lines. Creates a GeeLark phone for every account
    with no geelark_phone_id, installs TikTok, writes back the phone id."""
    pending = [a for a in acc_repo.list_all(conn)
               if not a.get("geelark_phone_id") and a.get("profile_name")]
    if not pending:
        yield "No accounts need creating."
        return
    for a in pending:
        name = a["profile_name"]
        yield f"Creating phone for {name}..."
        entry = {"serialName": name}
        if a.get("proxy"):
            entry["proxy"] = a["proxy"]
        if settings.get("default_group"):
            entry["groupName"] = settings["default_group"]
        data = client.create_phones([entry])
        details = data.get("details") or data.get("list") or []
        if not details:
            yield f"  FAILED: no phone returned for {name}"
            continue
        phone_id = details[0].get("id") or details[0].get("envId")
        acc_repo.update(conn, a["id"], {"geelark_phone_id": phone_id})
        yield f"  phone {phone_id} created, installing TikTok..."
        try:
            apps = client.get_installable_apps(phone_id, name="TikTok")
            items = apps.get("items", [])
            tt = next((i for i in items
                       if "tiktok" in (i.get("appName", "").lower())), None)
            if tt:
                client.install_app(phone_id, tt["appVersionId"])
                yield f"  TikTok install requested for {name}"
            else:
                yield f"  WARNING: TikTok not found in installable list for {name}"
        except Exception as e:
            yield f"  WARNING: TikTok auto-install failed for {name}: {e}"
    yield "Done."

def set_pics(conn, client) -> None:
    """For each account with a profile_pic_url, tiktok_logged_in, and a phone:
    resolve a prior task if pic_task_id set, else submit a new edit task."""
    for a in acc_repo.list_all(conn):
        if not (a.get("profile_pic_url") and a.get("tiktok_logged_in")
                and a.get("geelark_phone_id")):
            continue
        if a.get("pic_task_id"):
            try:
                res = client.query_tasks([a["pic_task_id"]])
            except Exception:
                continue
            if not res:
                continue
            status = res[0].get("status")
            if status == 3:
                acc_repo.update(conn, a["id"],
                                {"pic_status": "done", "pic_task_id": None})
            elif status in (4, 7):
                acc_repo.update(conn, a["id"],
                                {"pic_status": "failed", "pic_task_id": None})
            # status 2 -> still running, leave as-is
            continue
        if a.get("pic_status") == "done":
            continue
        try:
            task_id = client.edit_tiktok_profile(
                a["geelark_phone_id"], avatar=a["profile_pic_url"])
            acc_repo.update(conn, a["id"],
                            {"pic_status": "initiated", "pic_task_id": task_id})
        except Exception as e:
            acc_repo.update(conn, a["id"], {"pic_status": "failed",
                                            "last_warm_notes": str(e)})
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/geelark_service.py tests/test_services.py
git commit -m "feat: create-profiles and set-pics service"
```

### Task 8: Warmup service

**Files:**
- Create: `app/services/warmup_service.py`
- Test: append to `tests/test_services.py`

Old `warm_accounts.py` logic distilled: pick accounts where
`tiktok_logged_in` and not `warmed_up`; for each, start phone, create a
`browse video` task of random 2–5 min, wait, stop phone; credit
`time_spent_warming` on success; set `warmed_up=1` when it reaches
`max_warm_time`. For testability the service accepts a `client` and a
`duration_minutes` override (skip real sleeping in tests by injecting a fake
client whose `query_tasks` immediately returns completed).

- [ ] **Step 1: Append failing test**

```python
class FakeWarmClient:
    def __init__(self): self.started=[]; self.stopped=[]
    def start_phones(self, ids): self.started+=ids; return {}
    def get_phone_status(self, ids): return {"successDetails":[{"id":ids[0],"status":0}]}
    def create_warmup_task(self, env_id, duration_minutes): return "wt1"
    def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]
    def stop_phones(self, ids): self.stopped+=ids; return {}

def test_warmup_credits_time_and_marks_done(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name":"R1","geelark_phone_id":"ph1",
        "tiktok_logged_in":1,"max_warm_time":120,"time_spent_warming":60})
    fake = FakeWarmClient()
    summary = warmup_service.run_warmup(db, fake, duration_minutes=2,
                                        wait=False)
    a = acc_repo.get(db, aid)
    assert a["time_spent_warming"] >= 120
    assert a["warmed_up"] == 1
    assert "1" in summary
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: FAIL `NameError: name 'warmup_service' is not defined` (add `from app.services import warmup_service` at top of test file)

- [ ] **Step 3: Create `app/services/warmup_service.py`**

```python
import time, random
from app.repositories import accounts as acc_repo

def run_warmup(conn, client, duration_minutes: int | None = None,
               wait: bool = True) -> str:
    targets = [a for a in acc_repo.list_all(conn)
               if a.get("tiktok_logged_in") and not a.get("warmed_up")
               and a.get("geelark_phone_id")]
    if not targets:
        return "0 accounts to warm"
    warmed_now = 0
    for a in targets:
        dur = duration_minutes or random.randint(2, 5)
        pid = a["geelark_phone_id"]
        try:
            client.start_phones([pid])
            task_id = client.create_warmup_task(pid, dur)
            if wait:
                deadline = time.time() + dur * 60 + 300
                while time.time() < deadline:
                    res = client.query_tasks([task_id])
                    if res and res[0].get("status") in (3, 4, 7):
                        break
                    time.sleep(15)
        finally:
            try:
                client.stop_phones([pid])
            except Exception:
                pass
        spent = a.get("time_spent_warming", 0) + dur * 60
        patch = {"time_spent_warming": spent,
                 "last_warm_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "last_warm_result": "Success"}
        if spent >= a.get("max_warm_time", 500):
            patch["warmed_up"] = 1
            warmed_now += 1
        acc_repo.update(conn, a["id"], patch)
    return f"{len(targets)} accounts warmed, {warmed_now} reached target"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/warmup_service.py tests/test_services.py
git commit -m "feat: warmup service"
```

### Task 9: Posting service

**Files:**
- Create: `app/services/posting_service.py`
- Test: append to `tests/test_services.py`

Flow: take PENDING videos → build pool from warmed accounts → assign → for
each: caption via `generate_caption.generate_caption_for_local_file`, upload to
GeeLark via `client.upload_file`, post via `client.create_video_post_task`,
mark INITIATED; update account `last_posted_at`. Caption + client are injected
for testing. `last_posted_at` stored as `"%Y-%m-%d %H:%M:%S"` string; convert to
epoch in the pool adapter.

- [ ] **Step 1: Append failing test**

```python
def test_posting_assigns_and_marks_initiated(db, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    acc_repo.create(db, {"profile_name":"R1","geelark_phone_id":"ph1",
                         "warmed_up":1})
    f = tmp_path / "c.mp4"; f.write_bytes(b"x")
    vid_repo.create(db, "c.mp4", str(f))
    class FakePost:
        def upload_file(self, p, t): return "https://res/c.mp4"
        def create_video_post_task(self, env_id, video_url,
                                   video_desc=None, cover_url=None): return "pt1"
    monkeypatch.setattr(posting_service, "_caption",
                        lambda path: "my #IELTS caption")
    out = list(posting_service.publish(db, FakePost()))
    v = vid_repo.list_all(db)[0]
    assert v["result"] == "INITIATED"
    assert v["geelark_task_id"] == "pt1"
    assert acc_repo.list_all(db)[0]["last_posted_at"] is not None
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: FAIL `NameError: name 'posting_service' is not defined` (add `from app.services import posting_service` to test imports)

- [ ] **Step 3: Create `app/services/posting_service.py`**

```python
import os, time
from app.repositories import accounts as acc_repo
from app.repositories import videos as vid_repo
from app.services.assignment import build_pool, assign

def _caption(local_path: str) -> str:
    from generate_caption import generate_caption_for_local_file
    return generate_caption_for_local_file(local_path)

def _epoch(ts: str | None):
    if not ts:
        return None
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None

def publish(conn, client, cooldown_hours: float = 2):
    """Generator of log lines. One video per eligible warmed account per run."""
    pend = vid_repo.list_pending(conn)
    if not pend:
        yield "No videos to post."
        return
    accounts = acc_repo.list_all(conn)
    pool = build_pool([{**a, "last_posted_at": _epoch(a.get("last_posted_at"))}
                        for a in accounts])
    if not pool:
        yield "No warmed-up accounts available."
        return
    by_name = {a["profile_name"]: a for a in accounts}
    plan = assign(pend, pool, cooldown_hours=cooldown_hours)
    if not plan:
        yield "All accounts in cooldown. Nothing posted."
        return
    for item in plan:
        v = item["video"]
        acc = by_name[item["profile_name"]]
        yield f"'{v['original_filename']}' -> {acc['profile_name']}"
        try:
            caption = _caption(v["stored_path"])
        except Exception as e:
            vid_repo.mark_failure(conn, v["id"], acc["id"],
                                  f"caption failed: {e}")
            yield f"  FAILURE caption: {e}"
            continue
        try:
            ext = os.path.splitext(v["original_filename"])[1].lstrip(".") or "mp4"
            res_url = client.upload_file(v["stored_path"], ext)
            task_id = client.create_video_post_task(
                acc["geelark_phone_id"], res_url, video_desc=caption)
        except Exception as e:
            vid_repo.mark_failure(conn, v["id"], acc["id"],
                                  f"post failed: {e}")
            yield f"  FAILURE post: {e}"
            continue
        vid_repo.mark_initiated(conn, v["id"], acc["id"], caption,
                                task_id, "submitted to GeeLark")
        acc_repo.update(conn, acc["id"],
                        {"last_posted_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        yield f"  INITIATED task {task_id}"
    yield "Done."

def check_initiated(conn, client) -> str:
    """Poll INITIATED videos and resolve to SUCCESS/FAILURE."""
    rows = vid_repo.list_initiated(conn)
    if not rows:
        return "0 pending"
    ok = fail = 0
    for v in rows:
        try:
            res = client.query_tasks([v["geelark_task_id"]])
        except Exception:
            continue
        if not res:
            vid_repo.set_result(conn, v["id"], "FAILURE", "task not found")
            fail += 1
            continue
        status = res[0].get("status")
        if status == 3:
            vid_repo.set_result(conn, v["id"], "SUCCESS", "completed")
            if v.get("stored_path"):
                try:
                    os.remove(v["stored_path"])
                except OSError:
                    pass
                vid_repo.clear_stored_path(conn, v["id"])
            ok += 1
        elif status in (4, 7):
            vid_repo.set_result(conn, v["id"], "FAILURE", "task failed")
            fail += 1
    return f"{ok} success, {fail} failure"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/posting_service.py tests/test_services.py
git commit -m "feat: posting and status-check service"
```

---

## Phase 4 — API & auth

### Task 10: FastAPI app, auth, dependency wiring

**Files:**
- Create: `app/auth.py`, `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test `tests/test_api.py`**

```python
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setattr(cfg, "DASHBOARD_PASSWORD", "pw")
    from app.main import create_app
    app = create_app(start_scheduler=False)
    return TestClient(app)

def test_login_required_then_accounts_crud(client):
    assert client.get("/api/accounts").status_code == 401
    r = client.post("/api/login", json={"password": "pw"})
    assert r.status_code == 200
    r = client.post("/api/accounts", json={"profile_name": "A1",
                                           "email": "a@b.com"})
    assert r.status_code == 200
    aid = r.json()["id"]
    assert any(a["id"] == aid for a in client.get("/api/accounts").json())
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Create `app/auth.py`**

```python
from fastapi import Request, HTTPException
from itsdangerous import URLSafeSerializer
from app import config

_serializer = URLSafeSerializer(config.SESSION_SECRET, salt="auth")
COOKIE = "session"

def make_token() -> str:
    return _serializer.dumps({"ok": True})

def require_auth(request: Request):
    tok = request.cookies.get(COOKIE)
    try:
        if tok and _serializer.loads(tok).get("ok"):
            return
    except Exception:
        pass
    raise HTTPException(status_code=401, detail="unauthorized")
```

(Add `itsdangerous>=2.0.0` to `requirements.txt` and `pip install` it.)

- [ ] **Step 4: Create `app/main.py`**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from app import config
from app.db import get_connection, init_schema
from app.auth import require_auth

_conn = None

def db_conn():
    global _conn
    if _conn is None:
        _conn = get_connection()
        init_schema(_conn)
    return _conn

def create_app(start_scheduler: bool = True) -> FastAPI:
    global _conn
    _conn = None  # reset for tests with monkeypatched DB_PATH

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_conn()
        if start_scheduler:
            from app.scheduler import start
            start(db_conn())
        yield

    app = FastAPI(lifespan=lifespan)

    from app.api import accounts, videos, dashboard, settings, phones, jobs
    app.include_router(accounts.router, dependencies=[Depends(require_auth)])
    app.include_router(videos.router, dependencies=[Depends(require_auth)])
    app.include_router(dashboard.router, dependencies=[Depends(require_auth)])
    app.include_router(settings.router, dependencies=[Depends(require_auth)])
    app.include_router(phones.router, dependencies=[Depends(require_auth)])
    app.include_router(jobs.router, dependencies=[Depends(require_auth)])

    @app.post("/api/login")
    def login(body: dict):
        from app.auth import make_token, COOKIE
        if body.get("password") != config.DASHBOARD_PASSWORD:
            return JSONResponse({"error": "bad password"}, status_code=401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, make_token(), httponly=True, samesite="lax")
        return resp

    return app

app = create_app()
```

- [ ] **Step 5: Create minimal routers so imports resolve**

Create `app/api/accounts.py`:

```python
from fastapi import APIRouter
from app.repositories import accounts as acc_repo

router = APIRouter(prefix="/api/accounts")

def _db():
    from app.main import db_conn
    return db_conn()

@router.get("")
def list_accounts():
    return acc_repo.list_all(_db())

@router.post("")
def create_account(body: dict):
    aid = acc_repo.create(_db(), body)
    return {"id": aid}

@router.patch("/{account_id}")
def update_account(account_id: int, body: dict):
    acc_repo.update(_db(), account_id, body)
    return {"ok": True}

@router.delete("/{account_id}")
def delete_account(account_id: int):
    acc_repo.delete(_db(), account_id)
    return {"ok": True}

@router.patch("/{account_id}/login-toggle")
def login_toggle(account_id: int):
    cur = acc_repo.get(_db(), account_id)
    acc_repo.update(_db(), account_id,
                    {"tiktok_logged_in": 0 if cur["tiktok_logged_in"] else 1})
    return {"ok": True}
```

Create stub routers `app/api/videos.py`, `app/api/dashboard.py`,
`app/api/settings.py`, `app/api/phones.py`, `app/api/jobs.py` each:

```python
from fastapi import APIRouter
router = APIRouter()
```

- [ ] **Step 6: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_api.py -v`
Expected: 1 passed (install `itsdangerous` first if ImportError)

- [ ] **Step 7: Commit**

```bash
git add app/auth.py app/main.py app/api requirements.txt tests/test_api.py
git commit -m "feat: FastAPI app, cookie auth, accounts API"
```

### Task 11: Videos, dashboard, settings, phones API

**Files:**
- Modify: `app/api/videos.py`, `app/api/dashboard.py`, `app/api/settings.py`, `app/api/phones.py`
- Test: append to `tests/test_api.py`

- [ ] **Step 1: Append failing test**

```python
def test_video_upload_and_dashboard(client):
    client.post("/api/login", json={"password": "pw"})
    files = {"files": ("a.mp4", b"data", "video/mp4")}
    r = client.post("/api/videos/upload", files=files)
    assert r.status_code == 200 and r.json()["uploaded"] == 1
    assert client.get("/api/videos").json()[0]["original_filename"] == "a.mp4"
    d = client.get("/api/dashboard").json()
    assert d["stats"]["pending_videos"] == 1
    s = client.get("/api/settings").json()
    assert s["post_cooldown_hours"] == "2"
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL (404 on `/api/videos/upload`)

- [ ] **Step 3: Implement `app/api/videos.py`**

```python
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File
from app import config
from app.repositories import videos as vid_repo

router = APIRouter(prefix="/api/videos")

def _db():
    from app.main import db_conn
    return db_conn()

@router.get("")
def list_videos():
    return vid_repo.list_all(_db())

@router.post("/upload")
async def upload(files: list[UploadFile] = File(...)):
    n = 0
    for f in files:
        dest = Path(config.UPLOAD_DIR) / f.filename
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        vid_repo.create(_db(), f.filename, str(dest))
        n += 1
    return {"uploaded": n}

@router.delete("/{vid}")
def delete_video(vid: int):
    vid_repo.delete(_db(), vid)
    return {"ok": True}

@router.post("/publish")
def publish():
    from app.services import posting_service
    from app.services.geelark_service import make_client
    from app.repositories import settings as srepo
    cd = float(srepo.get_all(_db()).get("post_cooldown_hours", 2))
    log = list(posting_service.publish(_db(), make_client(), cooldown_hours=cd))
    return {"log": log}
```

- [ ] **Step 4: Implement `app/api/dashboard.py`**

```python
from fastapi import APIRouter
from app.repositories import accounts as acc_repo
from app.repositories import videos as vid_repo
from app.repositories import job_runs as jobs_repo

router = APIRouter(prefix="/api/dashboard")

def _db():
    from app.main import db_conn
    return db_conn()

@router.get("")
def dashboard():
    conn = _db()
    accs = acc_repo.list_all(conn)
    vids = vid_repo.list_all(conn)
    return {
        "stats": {
            "accounts": len(accs),
            "logged_in": sum(1 for a in accs if a["tiktok_logged_in"]),
            "warmed_up": sum(1 for a in accs if a["warmed_up"]),
            "pending_videos": sum(1 for v in vids if v["result"] == "PENDING"),
            "posted_today": sum(1 for v in vids if v["result"] == "SUCCESS"),
        },
        "recent_jobs": jobs_repo.recent(conn, 15),
    }
```

- [ ] **Step 5: Implement `app/api/settings.py` and `app/api/phones.py`**

`app/api/settings.py`:

```python
from fastapi import APIRouter
from app.repositories import settings as srepo

router = APIRouter(prefix="/api/settings")

def _db():
    from app.main import db_conn
    return db_conn()

@router.get("")
def get_settings():
    return srepo.get_all(_db())

@router.patch("")
def update_settings(body: dict):
    srepo.update(_db(), body)
    return srepo.get_all(_db())
```

`app/api/phones.py`:

```python
from fastapi import APIRouter
from app.services import geelark_service

router = APIRouter(prefix="/api/phones")

def _db():
    from app.main import db_conn
    return db_conn()

@router.post("/stop-all")
def stop_all():
    return geelark_service.stop_all_phones(_db(), geelark_service.make_client())
```

- [ ] **Step 6: Add create-profiles / set-pics / import endpoints to `app/api/accounts.py`**

Append:

```python
@router.post("/create-profiles")
def create_profiles():
    from app.services import geelark_service
    from app.repositories import settings as srepo
    s = srepo.get_all(_db())
    log = list(geelark_service.create_profiles(
        _db(), geelark_service.make_client(), s))
    return {"log": log}

@router.post("/set-pics")
def set_pics():
    from app.services import geelark_service
    geelark_service.set_pics(_db(), geelark_service.make_client())
    return {"ok": True}

@router.post("/import-from-geelark")
def import_geelark():
    from app.services import geelark_service
    n = geelark_service.import_from_geelark(
        _db(), geelark_service.make_client())
    return {"imported": n}
```

- [ ] **Step 7: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_api.py -v`
Expected: 2 passed

- [ ] **Step 8: Commit**

```bash
git add app/api tests/test_api.py
git commit -m "feat: videos/dashboard/settings/phones API"
```

---

## Phase 5 — Scheduler & SSE

### Task 12: APScheduler background jobs

**Files:**
- Create: `app/scheduler.py`
- Test: append to `tests/test_services.py`

- [ ] **Step 1: Append failing test (logic-only, no real scheduler)**

```python
def test_scheduled_warmup_writes_job_run(db, monkeypatch):
    from app import scheduler
    from app.repositories import accounts as acc_repo
    from app.repositories import job_runs as jobs_repo
    acc_repo.create(db, {"profile_name":"R1","geelark_phone_id":"ph1",
                         "tiktok_logged_in":1,"max_warm_time":60})
    monkeypatch.setattr(scheduler, "_make_client", lambda: FakeWarmClient())
    scheduler.job_warmup(db)
    jr = jobs_repo.recent(db, 1)[0]
    assert jr["job_type"] == "warmup" and jr["status"] == "ok"
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.scheduler'`

- [ ] **Step 3: Create `app/scheduler.py`**

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.repositories import settings as srepo
from app.repositories import job_runs as jobs_repo

def _make_client():
    from app.services.geelark_service import make_client
    return make_client()

def _run(conn, job_type, fn):
    jid = jobs_repo.start(conn, job_type)
    try:
        summary = fn()
        jobs_repo.finish(conn, jid, "ok", summary)
    except Exception as e:
        jobs_repo.finish(conn, jid, "error", str(e))

def job_warmup(conn):
    from app.services import warmup_service
    _run(conn, "warmup",
         lambda: warmup_service.run_warmup(conn, _make_client()))

def job_status_check(conn):
    from app.services import posting_service
    _run(conn, "status_check",
         lambda: posting_service.check_initiated(conn, _make_client()))

def job_pic_check(conn):
    from app.services import geelark_service
    def _do():
        geelark_service.set_pics(conn, _make_client())
        return "pic statuses refreshed"
    _run(conn, "pic_check", _do)

def start(conn):
    sched = BackgroundScheduler(daemon=True)
    times = srepo.get_all(conn).get("warmup_times", "09:00,14:00,20:00")
    for hhmm in [t.strip() for t in times.split(",") if t.strip()]:
        h, m = hhmm.split(":")
        sched.add_job(job_warmup, CronTrigger(hour=int(h), minute=int(m)),
                      args=[conn])
    sched.add_job(job_status_check, "interval", minutes=5, args=[conn])
    sched.add_job(job_pic_check, "interval", minutes=10, args=[conn])
    sched.start()
    return sched
```

- [ ] **Step 4: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_services.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add app/scheduler.py tests/test_services.py
git commit -m "feat: APScheduler warmup/status/pic jobs"
```

### Task 13: SSE live log streaming

**Files:**
- Create: `app/sse.py`
- Modify: `app/api/jobs.py`, `app/api/accounts.py` (`create-profiles` streams), `app/api/videos.py` (`publish` streams)
- Test: append to `tests/test_api.py`

- [ ] **Step 1: Append failing test**

```python
def test_publish_stream_returns_event_stream(client):
    client.post("/api/login", json={"password": "pw"})
    with client.stream("POST", "/api/videos/publish-stream") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(chunk for chunk in r.iter_text())
    assert "No videos to post" in body
```

- [ ] **Step 2: Run, verify fail**

Run: `./.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL (404 on `/api/videos/publish-stream`)

- [ ] **Step 3: Create `app/sse.py`**

```python
from fastapi.responses import StreamingResponse

def sse_from_generator(gen):
    def event_stream():
        for line in gen:
            yield f"data: {line}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: Add streaming endpoints**

Append to `app/api/videos.py`:

```python
from app.sse import sse_from_generator

@router.post("/publish-stream")
def publish_stream():
    from app.services import posting_service
    from app.services.geelark_service import make_client
    from app.repositories import settings as srepo
    cd = float(srepo.get_all(_db()).get("post_cooldown_hours", 2))
    return sse_from_generator(
        posting_service.publish(_db(), make_client(), cooldown_hours=cd))
```

Append to `app/api/accounts.py`:

```python
@router.post("/create-profiles-stream")
def create_profiles_stream():
    from app.services import geelark_service
    from app.sse import sse_from_generator
    from app.repositories import settings as srepo
    s = srepo.get_all(_db())
    return sse_from_generator(geelark_service.create_profiles(
        _db(), geelark_service.make_client(), s))
```

- [ ] **Step 5: Run tests, verify pass**

Run: `./.venv/bin/pytest tests/test_api.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add app/sse.py app/api/videos.py app/api/accounts.py tests/test_api.py
git commit -m "feat: SSE streaming for publish and create-profiles"
```

---

## Phase 6 — Frontend SPA

### Task 14: Vite React TS scaffold + auth + API client

**Files:**
- Create: `frontend/` (Vite scaffold), `frontend/src/api.ts`, `frontend/src/Login.tsx`, `frontend/src/App.tsx`, `frontend/vite.config.ts`

- [ ] **Step 1: Scaffold**

Run: `cd /Users/Zhuanz/Documents/mkt/frontend 2>/dev/null || (cd /Users/Zhuanz/Documents/mkt && npm create vite@latest frontend -- --template react-ts)` then `cd frontend && npm install`

- [ ] **Step 2: Configure dev proxy in `frontend/vite.config.ts`**

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
  server: { proxy: { '/api': 'http://localhost:8080' } },
})
```

- [ ] **Step 3: Create `frontend/src/api.ts`**

```ts
async function req(path: string, opts: RequestInit = {}) {
  const r = await fetch('/api' + path, { credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, ...opts })
  if (r.status === 401) { window.location.hash = '#login'; throw new Error('unauthorized') }
  return r.json()
}
export const api = {
  login: (password: string) =>
    req('/login', { method: 'POST', body: JSON.stringify({ password }) }),
  accounts: () => req('/accounts'),
  createAccount: (b: any) => req('/accounts', { method: 'POST', body: JSON.stringify(b) }),
  updateAccount: (id: number, b: any) =>
    req(`/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(b) }),
  deleteAccount: (id: number) => req(`/accounts/${id}`, { method: 'DELETE' }),
  loginToggle: (id: number) =>
    req(`/accounts/${id}/login-toggle`, { method: 'PATCH' }),
  importGeelark: () => req('/accounts/import-from-geelark', { method: 'POST' }),
  setPics: () => req('/accounts/set-pics', { method: 'POST' }),
  videos: () => req('/videos'),
  deleteVideo: (id: number) => req(`/videos/${id}`, { method: 'DELETE' }),
  dashboard: () => req('/dashboard'),
  settings: () => req('/settings'),
  updateSettings: (b: any) =>
    req('/settings', { method: 'PATCH', body: JSON.stringify(b) }),
  stopAll: () => req('/phones/stop-all', { method: 'POST' }),
}
export function streamPost(path: string, onLine: (s: string) => void) {
  return fetch('/api' + path, { method: 'POST', credentials: 'include' })
    .then(async (r) => {
      const reader = r.body!.getReader(); const dec = new TextDecoder()
      for (;;) { const { done, value } = await reader.read(); if (done) break
        dec.decode(value).split('\n\n').forEach((c) => {
          const m = c.replace(/^data: /, ''); if (m && m !== '[DONE]') onLine(m) }) }
    })
}
```

- [ ] **Step 4: Create `frontend/src/Login.tsx` and `App.tsx` shell with hash routing**

`Login.tsx`:

```tsx
import { useState } from 'react'
import { api } from './api'
export default function Login({ onOk }: { onOk: () => void }) {
  const [pw, setPw] = useState(''); const [err, setErr] = useState('')
  return (
    <div style={{ maxWidth: 320, margin: '15vh auto', fontFamily: 'system-ui' }}>
      <h2>TikTok Manager</h2>
      <input type="password" value={pw} placeholder="Password"
        onChange={(e) => setPw(e.target.value)} style={{ width: '100%', padding: 8 }} />
      <button style={{ marginTop: 10, width: '100%', padding: 8 }}
        onClick={() => api.login(pw).then(onOk).catch(() => setErr('Wrong password'))}>
        Sign in</button>
      {err && <p style={{ color: 'crimson' }}>{err}</p>}
    </div>
  )
}
```

`App.tsx`:

```tsx
import { useEffect, useState } from 'react'
import Login from './Login'
import Dashboard from './pages/Dashboard'
import Accounts from './pages/Accounts'
import Videos from './pages/Videos'
import Settings from './pages/Settings'
const TABS = ['dashboard', 'accounts', 'videos', 'settings'] as const
export default function App() {
  const [authed, setAuthed] = useState(false)
  const [tab, setTab] = useState<string>('dashboard')
  useEffect(() => { import('./api').then(({ api }) =>
    api.dashboard().then(() => setAuthed(true)).catch(() => {})) }, [])
  if (!authed) return <Login onOk={() => setAuthed(true)} />
  return (
    <div style={{ display: 'flex', fontFamily: 'system-ui' }}>
      <nav style={{ width: 160, padding: 16, borderRight: '1px solid #ddd',
        minHeight: '100vh' }}>
        {TABS.map((t) => (
          <div key={t} onClick={() => setTab(t)}
            style={{ padding: 8, cursor: 'pointer',
              fontWeight: tab === t ? 700 : 400 }}>{t}</div>))}
      </nav>
      <main style={{ flex: 1, padding: 24 }}>
        {tab === 'dashboard' && <Dashboard />}
        {tab === 'accounts' && <Accounts />}
        {tab === 'videos' && <Videos />}
        {tab === 'settings' && <Settings />}
      </main>
    </div>
  )
}
```

- [ ] **Step 5: Build to verify scaffold compiles**

Run: `cd /Users/Zhuanz/Documents/mkt/frontend && npm run build 2>&1 | tail -3`
Expected: build fails only on missing `./pages/*` imports (created next task)

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/vite.config.ts frontend/src/api.ts frontend/src/Login.tsx frontend/src/App.tsx frontend/index.html frontend/tsconfig*.json
git commit -m "feat: frontend scaffold, auth, api client"
```

### Task 15: Pages — Dashboard, Accounts, Videos, Settings

**Files:**
- Create: `frontend/src/pages/Dashboard.tsx`, `Accounts.tsx`, `Videos.tsx`, `Settings.tsx`

- [ ] **Step 1: `pages/Dashboard.tsx`** — stat cards + recent jobs timeline + Stop-All button

```tsx
import { useEffect, useState } from 'react'
import { api } from '../api'
export default function Dashboard() {
  const [d, setD] = useState<any>(null)
  const load = () => api.dashboard().then(setD)
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t) }, [])
  if (!d) return <p>Loading…</p>
  return (
    <div>
      <h2>Dashboard</h2>
      <div style={{ display: 'flex', gap: 12 }}>
        {Object.entries(d.stats).map(([k, v]: any) => (
          <div key={k} style={{ border: '1px solid #ddd', padding: 12, borderRadius: 8 }}>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{v}</div><div>{k}</div></div>))}
      </div>
      <button onClick={() => confirm('Stop ALL phones?') && api.stopAll().then(load)}
        style={{ marginTop: 16, background: 'crimson', color: '#fff', padding: 10 }}>
        🛑 Stop All Phones</button>
      <h3 style={{ marginTop: 24 }}>Recent background activity</h3>
      <ul>{d.recent_jobs.map((j: any) => (
        <li key={j.id}>{j.started_at} · {j.job_type} · {j.status} · {j.summary}</li>))}</ul>
    </div>
  )
}
```

- [ ] **Step 2: `pages/Accounts.tsx`** — editable table, add modal, toggle, action buttons

```tsx
import { useEffect, useState } from 'react'
import { api, streamPost } from '../api'
export default function Accounts() {
  const [rows, setRows] = useState<any[]>([])
  const [log, setLog] = useState<string[]>([])
  const load = () => api.accounts().then(setRows)
  useEffect(() => { load() }, [])
  const save = (id: number, field: string, value: any) =>
    api.updateAccount(id, { [field]: value }).then(load)
  return (
    <div>
      <h2>Accounts</h2>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <button onClick={() => api.importGeelark().then(load)}>Import from GeeLark</button>
        <button onClick={() => { setLog([]); streamPost('/accounts/create-profiles-stream',
          (l) => setLog((p) => [...p, l])).then(load) }}>▶ Create Profiles</button>
        <button onClick={() => api.setPics().then(load)}>🖼 Set Pics</button>
        <button onClick={() => { const n = prompt('profile_name?'); if (n)
          api.createAccount({ profile_name: n }).then(load) }}>+ Add</button>
      </div>
      <table border={1} cellPadding={4}><thead><tr>
        <th>name</th><th>email</th><th>proxy</th><th>pic_url</th><th>max_warm</th>
        <th>logged_in</th><th>warmed</th><th>warm_progress</th><th></th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id}>
            <td>{r.profile_name}</td>
            {['email', 'proxy', 'profile_pic_url', 'max_warm_time'].map((f) => (
              <td key={f}><input defaultValue={r[f] ?? ''}
                onBlur={(e) => save(r.id, f, e.target.value)} /></td>))}
            <td><input type="checkbox" checked={!!r.tiktok_logged_in}
              onChange={() => api.loginToggle(r.id).then(load)} /></td>
            <td>{r.warmed_up ? '✅' : '—'}</td>
            <td>{r.time_spent_warming}/{r.max_warm_time}</td>
            <td><button onClick={() => api.deleteAccount(r.id).then(load)}>del</button></td>
          </tr>))}</tbody></table>
      {log.length > 0 && <pre style={{ background: '#111', color: '#0f0', padding: 12 }}>
        {log.join('\n')}</pre>}
    </div>
  )
}
```

- [ ] **Step 3: `pages/Videos.tsx`** — upload, pending list, publish-stream, history

```tsx
import { useEffect, useState } from 'react'
import { api, streamPost } from '../api'
export default function Videos() {
  const [vids, setVids] = useState<any[]>([])
  const [log, setLog] = useState<string[]>([])
  const load = () => api.videos().then(setVids)
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t) }, [])
  const upload = async (files: FileList) => {
    const fd = new FormData(); Array.from(files).forEach((f) => fd.append('files', f))
    await fetch('/api/videos/upload', { method: 'POST', body: fd, credentials: 'include' })
    load()
  }
  return (
    <div>
      <h2>Videos</h2>
      <input type="file" multiple accept="video/*"
        onChange={(e) => e.target.files && upload(e.target.files)} />
      <button onClick={() => { setLog([]); streamPost('/videos/publish-stream',
        (l) => setLog((p) => [...p, l])).then(load) }}>▶ Publish</button>
      {log.length > 0 && <pre style={{ background: '#111', color: '#0f0', padding: 12 }}>
        {log.join('\n')}</pre>}
      <table border={1} cellPadding={4}><thead><tr>
        <th>file</th><th>result</th><th>account</th><th>caption</th><th>notes</th>
        <th></th></tr></thead><tbody>{vids.map((v) => (
        <tr key={v.id}><td>{v.original_filename}</td><td>{v.result}</td>
          <td>{v.account_id ?? '-'}</td><td>{v.caption ?? ''}</td><td>{v.notes ?? ''}</td>
          <td>{v.result === 'PENDING' &&
            <button onClick={() => api.deleteVideo(v.id).then(load)}>del</button>}</td>
        </tr>))}</tbody></table>
    </div>
  )
}
```

- [ ] **Step 4: `pages/Settings.tsx`** — editable settings form

```tsx
import { useEffect, useState } from 'react'
import { api } from '../api'
export default function Settings() {
  const [s, setS] = useState<any>({})
  useEffect(() => { api.settings().then(setS) }, [])
  return (
    <div>
      <h2>Settings</h2>
      {Object.keys(s).map((k) => (
        <div key={k} style={{ margin: 8 }}>
          <label style={{ display: 'inline-block', width: 200 }}>{k}</label>
          <input value={s[k]} onChange={(e) => setS({ ...s, [k]: e.target.value })} />
        </div>))}
      <button onClick={() => api.updateSettings(s).then(setS)}>Save</button>
      <p style={{ color: '#888' }}>GeeLark/Gemini keys live in .env (not editable here).</p>
    </div>
  )
}
```

- [ ] **Step 5: Build verify**

Run: `cd /Users/Zhuanz/Documents/mkt/frontend && npm run build 2>&1 | tail -3`
Expected: `built in` success line, `dist/` produced

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages
git commit -m "feat: dashboard/accounts/videos/settings pages"
```

---

## Phase 7 — Integration & cleanup

### Task 16: Serve built frontend from FastAPI; run script

**Files:**
- Modify: `app/main.py`
- Create: `run.sh`, `.env.example`

- [ ] **Step 1: Mount static dist in `app/main.py`**

Add before `return app` in `create_app`:

```python
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
```

- [ ] **Step 2: Create `run.sh`**

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
(cd frontend && npm run build)
exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
```

Run: `chmod +x run.sh`

- [ ] **Step 3: Create `.env.example`**

```
GEELARK_TOKEN=
GEMINI_API_KEY=
DASHBOARD_PASSWORD=changeme
DASHBOARD_SECRET_KEY=change-this-long-random-string
```

- [ ] **Step 4: Full test suite green**

Run: `./.venv/bin/pytest -q`
Expected: all tests pass

- [ ] **Step 5: Smoke test the server**

Run: `./.venv/bin/uvicorn app.main:app --port 8080 --workers 1 & sleep 3; curl -s -o /dev/null -w "%{http_code}" localhost:8080/api/dashboard; kill %1`
Expected: `401` (auth required, server boots cleanly)

- [ ] **Step 6: Commit**

```bash
git add app/main.py run.sh .env.example
git commit -m "feat: serve SPA from FastAPI, add run script"
```

### Task 17: Remove obsolete Google/script code & docs

**Files:**
- Delete: legacy files listed in File Structure
- Modify: `CLAUDE.md`, `README.md`, `GUIDE.md`

- [ ] **Step 1: Delete legacy code**

Run:

```bash
cd /Users/Zhuanz/Documents/mkt
git rm app.py video_utils.py post_videos.py post_from_drive.py \
  create_profiles.py warm_accounts.py set_profile_pics.py \
  check_video_status.py stop_all_phones.py normalize_profiles.py \
  full_reset_normalize.py recover_duplicates.py import_geelark_profiles.py \
  sync_groups.py verify_profiles.py test_tiktok_login.py
git rm -r templates docs/workflow
```

(Keep `geelark_client.py` and `generate_caption.py` — still imported.)

- [ ] **Step 2: Verify nothing still imports deleted modules**

Run: `grep -rn "video_utils\|import gspread\|from app import app" app/ tests/ || echo CLEAN`
Expected: `CLEAN`

- [ ] **Step 3: Rewrite `CLAUDE.md`, `README.md`, `GUIDE.md`**

Replace the architecture/commands sections to describe: FastAPI+SQLite+React app, `./run.sh` to start, single-worker constraint, no Google deps, background scheduler, `data/app.db` persistence and backup-by-copy. Remove all Google Sheet/Drive workflow instructions. Keep `GUIDE.md` as the marketer walkthrough but rewritten for the new single-page UI (import → add accounts → create profiles → manual TikTok login + toggle → set pics → auto-warmup → upload videos → publish).

- [ ] **Step 4: Full suite green again**

Run: `./.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove Google Sheet/Drive legacy code, update docs"
```

### Task 18: requesting-code-review

- [ ] **Step 1:** Use the superpowers:requesting-code-review skill to review the full branch against the spec before declaring done.

---

## Self-Review

**Spec coverage:**
- §3 tech stack / remove-keep → Task 0 (deps), Task 17 (deletions). ✓
- §4 data model (4 tables) → Task 1 schema; repos Tasks 2–4. ✓
- §5 API table (every endpoint) → Tasks 10/11/13 (accounts incl. login-toggle/create/set-pics/import; videos incl. upload/publish/delete; dashboard; settings; phones stop-all; jobs SSE). ✓
- §6 scheduler (warmup cron, 5-min status, 10-min pic) → Task 12. ✓
- §7 frontend 4 areas + SSE log + auth → Tasks 14–16. ✓
- §8 migration import-from-geelark + tradeoff → Task 6 (`import_from_geelark`, only GeeLark-returned fields). ✓
- §9 risks: stop on every exit path (Task 8 `finally`), no-retry of INITIATED (Task 9 only polls, never re-posts), WAL (Task 1), single worker (Task 16 `--workers 1`), disk cleanup (Task 9 `clear_stored_path`). ✓
- §10 acceptance → Task 16 smoke test + Task 18 review. ✓

**Placeholder scan:** No TBD/TODO; every code step has full code. Task 17 step 3 (doc rewrite) is descriptive prose, acceptable for a docs task (no code contract).

**Type consistency:** Repo function names consistent across tasks (`acc_repo.list_all`, `vid_repo.list_pending/list_initiated/mark_initiated/mark_failure/set_result/clear_stored_path`, `jobs_repo.start/finish/recent`, `srepo.get_all/update`). Services consistently take `(conn, client, ...)`. `last_posted_at` epoch conversion centralized in `posting_service._epoch` and used by `build_pool`. GeeLark task status codes (3=done, 4/7=fail, 2=running) consistent in warmup/posting/pics. SSE generator contract (`yield str` lines) consistent between services and `sse_from_generator`.

No gaps found.
