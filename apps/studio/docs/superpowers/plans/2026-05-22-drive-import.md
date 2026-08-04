# Google Drive Video Import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let dashboard operators import videos from a public Google Drive folder into the mkt app, relayed through an overseas proxy because the app's host (China) cannot reach Google.

**Architecture:** mkt (server A) fetches Drive content with `gdown`, routing that traffic through a dedicated HTTP proxy on server B (overseas). Imports run in a background thread; imported videos become normal `PENDING` rows in the existing `videos` table. No video file is stored on server B.

**Tech Stack:** FastAPI, SQLite, APScheduler, `gdown` (new dependency), React 19 + Vite, `tinyproxy` (server B).

Design spec: `docs/superpowers/specs/2026-05-22-drive-import-design.md`.

---

## File Structure

**Created:**
- `app/services/drive_import.py` — Drive import service: URL parsing, folder listing, video filtering, download orchestration, in-process progress state, background launcher.
- `tests/test_drive_import.py` — unit tests for the import service.

**Modified:**
- `app/db.py` — add `drive_file_id` column to the `videos` schema + idempotent migration.
- `app/repositories/videos.py` — `create()` accepts `drive_file_id`; add `existing_drive_file_ids()`.
- `app/config.py` — add `DRIVE_IMPORT_PROXY`.
- `app/api/videos.py` — add `POST /import` and `GET /import/status`.
- `requirements.txt` — add `gdown`.
- `.env.example` — add `DRIVE_IMPORT_PROXY`.
- `frontend/src/api.ts` — add `importDrive()` and `importStatus()`.
- `frontend/src/pages/Videos.tsx` — add "Import from Drive" button, modal, progress panel.
- `tests/test_repositories.py`, `tests/test_db.py`, `tests/test_api.py` — appended tests.

**Ops (no repo files):**
- Server B (`107.174.1.82`): install + configure `tinyproxy`.
- Server A (`1.15.155.204`): redeploy mkt with the new code + config.

All Python test commands use the repo venv: `./.venv/bin/pytest`.

---

## Task 1: `drive_file_id` column, migration, repository support

**Files:**
- Modify: `app/db.py`
- Modify: `app/repositories/videos.py`
- Test: `tests/test_db.py`, `tests/test_repositories.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
def test_videos_table_has_drive_file_id_column(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "t.db")
    conn = get_connection()
    init_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    assert "drive_file_id" in cols

def test_migration_adds_column_to_legacy_table_and_is_idempotent(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "legacy.db")
    conn = get_connection()
    conn.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, original_filename TEXT)")
    conn.commit()
    init_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    assert "drive_file_id" in cols
    init_schema(conn)  # second run must not raise
```

Append to `tests/test_repositories.py`:

```python
def test_video_create_with_drive_file_id(db):
    vid = vid_repo.create(db, "g.mp4", "/x/g.mp4", drive_file_id="DRIVEID123")
    assert vid_repo.get(db, vid)["drive_file_id"] == "DRIVEID123"

def test_video_create_without_drive_file_id_is_null(db):
    vid = vid_repo.create(db, "h.mp4", "/x/h.mp4")
    assert vid_repo.get(db, vid)["drive_file_id"] is None

def test_existing_drive_file_ids(db):
    vid_repo.create(db, "a.mp4", "/x/a.mp4", drive_file_id="ID_A")
    vid_repo.create(db, "b.mp4", "/x/b.mp4", drive_file_id="ID_B")
    vid_repo.create(db, "c.mp4", "/x/c.mp4")  # manual upload, no drive id
    assert vid_repo.existing_drive_file_ids(db) == {"ID_A", "ID_B"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_db.py tests/test_repositories.py -q`
Expected: FAIL — `create()` rejects `drive_file_id` kwarg; `existing_drive_file_ids` missing; `drive_file_id` column absent.

- [ ] **Step 3: Add the column to the schema + migration in `app/db.py`**

In the `SCHEMA` string, in the `CREATE TABLE IF NOT EXISTS videos` block, add `drive_file_id TEXT` immediately after the `notes TEXT,` line:

```python
  notes TEXT,
  drive_file_id TEXT,
  uploaded_at TEXT DEFAULT (datetime('now')),
```

Add this function above `init_schema`:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for databases created before a column existed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "drive_file_id" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN drive_file_id TEXT")
```

Change `init_schema` to call `_migrate` after `executescript`:

```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    for k, v in config.SETTINGS_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()
```

- [ ] **Step 4: Update `app/repositories/videos.py`**

Replace the `create` function and add `existing_drive_file_ids`:

```python
def create(conn: sqlite3.Connection, filename: str, stored_path: str,
           drive_file_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO videos (original_filename, stored_path, result, drive_file_id) "
        "VALUES (?, ?, 'PENDING', ?)", (filename, stored_path, drive_file_id))
    conn.commit()
    return cur.lastrowid

def existing_drive_file_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT drive_file_id FROM videos WHERE drive_file_id IS NOT NULL")
    return {r["drive_file_id"] for r in rows}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_db.py tests/test_repositories.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `./.venv/bin/pytest -q`
Expected: all tests pass (38 existing + 5 new = 43).

- [ ] **Step 7: Commit**

```bash
git add app/db.py app/repositories/videos.py tests/test_db.py tests/test_repositories.py
git commit -m "feat: add drive_file_id column and video dedup helpers"
```

---

## Task 2: `drive_import.py` skeleton — config, helpers, progress state

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Create: `app/services/drive_import.py`
- Test: `tests/test_drive_import.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_drive_import.py`:

```python
import pytest
from app.services import drive_import


def test_parse_folder_id_from_standard_url():
    url = "https://drive.google.com/drive/folders/1AbC_def-GHI123456789"
    assert drive_import.parse_folder_id(url) == "1AbC_def-GHI123456789"


def test_parse_folder_id_with_query_string():
    url = "https://drive.google.com/drive/folders/1AbC_def-GHI123456789?usp=sharing"
    assert drive_import.parse_folder_id(url) == "1AbC_def-GHI123456789"


def test_parse_folder_id_with_user_segment():
    url = "https://drive.google.com/drive/u/0/folders/1AbC_def-GHI123456789"
    assert drive_import.parse_folder_id(url) == "1AbC_def-GHI123456789"


def test_parse_folder_id_accepts_bare_id():
    assert drive_import.parse_folder_id("1AbC_def-GHI123456789xyz") \
        == "1AbC_def-GHI123456789xyz"


def test_parse_folder_id_rejects_non_folder_url():
    with pytest.raises(ValueError):
        drive_import.parse_folder_id("https://drive.google.com/file/d/abc/view")


def test_parse_folder_id_rejects_empty():
    with pytest.raises(ValueError):
        drive_import.parse_folder_id("")


def test_is_video():
    assert drive_import.is_video("clip.MP4") is True
    assert drive_import.is_video("a.mov") is True
    assert drive_import.is_video("notes.txt") is False
    assert drive_import.is_video("noext") is False


def test_unique_path_uses_original_name_when_free(tmp_path):
    assert drive_import.unique_path(tmp_path, "v.mp4", "FID1") == tmp_path / "v.mp4"


def test_unique_path_disambiguates_on_collision(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"x")
    assert drive_import.unique_path(tmp_path, "v.mp4", "FID1") == tmp_path / "v_FID1.mp4"


def test_get_state_returns_snapshot_with_expected_keys():
    s = drive_import.get_state()
    assert set(s) >= {"state", "total", "done", "skipped", "failed",
                      "current", "errors", "started_at", "finished_at"}
    s["errors"].append("mutating the copy must not affect module state")
    assert drive_import.get_state()["errors"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_drive_import.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.drive_import'`.

- [ ] **Step 3: Add config**

In `app/config.py`, add after the `SESSION_SECRET` line:

```python
DRIVE_IMPORT_PROXY = os.getenv("DRIVE_IMPORT_PROXY", "")
```

In `.env.example`, add a final line:

```
DRIVE_IMPORT_PROXY=
```

- [ ] **Step 4: Create `app/services/drive_import.py`**

```python
"""Import videos from a public Google Drive folder.

Google Drive is unreachable from the app's host. gdown traffic is routed
through an HTTP proxy on an overseas server (config.DRIVE_IMPORT_PROXY).
"""
import re
import threading
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


class ProxyNotConfigured(Exception):
    """DRIVE_IMPORT_PROXY is not set."""


class ImportAlreadyRunning(Exception):
    """An import was requested while one is already running."""


def parse_folder_id(url: str) -> str:
    """Extract the Drive folder ID from a folder URL (or a bare ID).

    Raises ValueError if the input is not a recognizable Drive folder.
    """
    text = (url or "").strip()
    if not text:
        raise ValueError("folder URL is empty")
    m = _FOLDER_RE.search(text)
    if m:
        return m.group(1)
    if _BARE_ID_RE.match(text):
        return text
    raise ValueError("not a Google Drive folder URL")


def is_video(name: str) -> bool:
    """True if the filename has a recognized video extension."""
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def unique_path(upload_dir, name: str, file_id: str) -> Path:
    """A non-colliding destination path inside upload_dir.

    Uses the original name; if taken, disambiguates with the Drive file ID
    (globally unique).
    """
    upload_dir = Path(upload_dir)
    safe = Path(name).name or f"{file_id}.mp4"
    dest = upload_dir / safe
    if dest.exists():
        dest = upload_dir / f"{Path(safe).stem}_{file_id}{Path(safe).suffix}"
    return dest


_state_lock = threading.Lock()
_import_state = {
    "state": "idle",        # idle | running | done | error
    "total": 0,
    "done": 0,
    "skipped": 0,
    "failed": 0,
    "current": None,
    "errors": [],
    "started_at": None,
    "finished_at": None,
}


def get_state() -> dict:
    """A snapshot copy of the current import progress state."""
    with _state_lock:
        snap = dict(_import_state)
        snap["errors"] = list(_import_state["errors"])
        return snap


def _update(**fields) -> None:
    with _state_lock:
        _import_state.update(fields)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_drive_import.py -q`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add app/config.py .env.example app/services/drive_import.py tests/test_drive_import.py
git commit -m "feat: drive_import helpers, config, and progress state"
```

---

## Task 3: `drive_import.py` — `run_import` engine

**Files:**
- Modify: `requirements.txt`
- Modify: `app/services/drive_import.py`
- Test: `tests/test_drive_import.py`

- [ ] **Step 1: Add the `gdown` dependency and install it**

Append to `requirements.txt`:

```
gdown>=5.2.0
```

Run: `./.venv/bin/pip install 'gdown>=5.2.0'`
Expected: installs gdown and its deps (`beautifulsoup4`, `filelock`, `tqdm`).

- [ ] **Step 2: Write failing tests**

Append to `tests/test_drive_import.py`:

```python
from collections import namedtuple

FakeFile = namedtuple("FakeFile", ["id", "path", "local_path"])


def test_run_import_downloads_new_videos(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://u:p@host:8118")
    listing = [
        FakeFile("ID1", "folder/a.mp4", "folder/a.mp4"),
        FakeFile("ID2", "folder/b.mov", "folder/b.mov"),
        FakeFile("ID3", "folder/readme.txt", "folder/readme.txt"),
    ]
    monkeypatch.setattr(drive_import.gdown, "download_folder", lambda **kw: listing)

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"video-bytes")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)

    drive_import.run_import("https://drive.google.com/drive/folders/ID")

    vids = vid_repo.list_all(db)
    assert len(vids) == 2  # the .txt is skipped
    assert {v["drive_file_id"] for v in vids} == {"ID1", "ID2"}
    assert all(v["result"] == "PENDING" for v in vids)
    st = drive_import.get_state()
    assert st["state"] == "done"
    assert st["done"] == 2 and st["skipped"] == 1 and st["failed"] == 0


def test_run_import_skips_already_imported(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    vid_repo.create(db, "old.mp4", str(tmp_path / "old.mp4"), drive_file_id="ID1")
    listing = [FakeFile("ID1", "f/old.mp4", "x"), FakeFile("ID2", "f/new.mp4", "x")]
    monkeypatch.setattr(drive_import.gdown, "download_folder", lambda **kw: listing)

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)

    drive_import.run_import("https://drive.google.com/drive/folders/ID")

    st = drive_import.get_state()
    assert st["done"] == 1 and st["skipped"] == 1


def test_run_import_isolates_per_file_failure(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    listing = [FakeFile("OK", "f/good.mp4", "x"), FakeFile("BAD", "f/bad.mp4", "x")]
    monkeypatch.setattr(drive_import.gdown, "download_folder", lambda **kw: listing)

    def fake_download(id=None, output=None, **kw):
        if id == "BAD":
            raise RuntimeError("network error")
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)

    drive_import.run_import("https://drive.google.com/drive/folders/ID")

    vids = vid_repo.list_all(db)
    assert len(vids) == 1 and vids[0]["drive_file_id"] == "OK"
    st = drive_import.get_state()
    assert st["done"] == 1 and st["failed"] == 1
    assert st["state"] == "done"  # the import as a whole still completes
    assert any("bad.mp4" in e for e in st["errors"])


def test_run_import_marks_error_when_listing_fails(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import job_runs as jobs_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")

    def boom(**kw):
        raise RuntimeError("403 private folder")

    monkeypatch.setattr(drive_import.gdown, "download_folder", boom)

    drive_import.run_import("https://drive.google.com/drive/folders/ID")

    assert drive_import.get_state()["state"] == "error"
    assert jobs_repo.recent(db, 1)[0]["status"] == "error"


def test_run_import_writes_job_run(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import job_runs as jobs_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    monkeypatch.setattr(drive_import.gdown, "download_folder",
                        lambda **kw: [FakeFile("Z", "f/z.mp4", "x")])

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)

    drive_import.run_import("https://drive.google.com/drive/folders/ID")

    jr = jobs_repo.recent(db, 1)[0]
    assert jr["job_type"] == "drive_import"
    assert jr["status"] == "ok"
    assert "1 imported" in jr["summary"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_drive_import.py -k run_import -q`
Expected: FAIL — `drive_import` has no attribute `gdown` / `run_import`.

- [ ] **Step 4: Implement `run_import`**

At the top of `app/services/drive_import.py`, extend the imports:

```python
import re
import time
import threading
from pathlib import Path

import gdown

from app import config
from app.db import get_connection
from app.repositories import videos as vid_repo
from app.repositories import job_runs as jobs_repo
```

Add at the end of the file:

```python
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def run_import(folder_url: str) -> None:
    """Background worker: list the folder, download new videos, record them.

    Opens its own DB connection (runs in a background thread). Updates the
    in-process progress state and writes one job_runs row.
    """
    _update(state="running", total=0, done=0, skipped=0, failed=0,
            current=None, errors=[], started_at=_now(), finished_at=None)
    conn = get_connection()
    jid = jobs_repo.start(conn, "drive_import")
    errors: list[str] = []
    done = skipped = failed = 0
    try:
        proxy = config.DRIVE_IMPORT_PROXY or None
        listing = gdown.download_folder(
            url=folder_url, skip_download=True, quiet=True, proxy=proxy)
        if not listing:
            raise RuntimeError("folder is empty or could not be listed")
        existing = vid_repo.existing_drive_file_ids(conn)
        wanted = []
        for f in listing:
            name = Path(f.path).name
            if not is_video(name) or f.id in existing:
                skipped += 1
            else:
                wanted.append((f.id, name))
        _update(total=len(wanted), skipped=skipped)
        for file_id, name in wanted:
            _update(current=name)
            dest = unique_path(config.UPLOAD_DIR, name, file_id)
            try:
                gdown.download(id=file_id, output=str(dest),
                               quiet=True, proxy=proxy)
                vid_repo.create(conn, name, str(dest), drive_file_id=file_id)
                done += 1
            except Exception as e:  # per-file isolation: skip and continue
                if dest.exists():
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                failed += 1
                errors.append(f"{name}: {e}")
            _update(done=done, failed=failed, errors=list(errors))
        summary = f"{done} imported, {skipped} skipped, {failed} failed"
        jobs_repo.finish(conn, jid, "ok" if failed == 0 else "error", summary)
        _update(state="done", current=None, finished_at=_now())
    except Exception as e:  # whole-import failure (listing/proxy/etc.)
        jobs_repo.finish(conn, jid, "error", str(e))
        _update(state="error", current=None,
                errors=list(errors) + [str(e)], finished_at=_now())
    finally:
        conn.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_drive_import.py -q`
Expected: PASS (15 tests).

- [ ] **Step 6: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: all pass (58 total).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/services/drive_import.py tests/test_drive_import.py
git commit -m "feat: drive_import run_import engine (list, dedup, download)"
```

---

## Task 4: `start_import` launcher + API endpoints

**Files:**
- Modify: `app/services/drive_import.py`
- Modify: `app/api/videos.py`
- Test: `tests/test_drive_import.py`, `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_drive_import.py`:

```python
def test_start_import_without_proxy_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    with pytest.raises(drive_import.ProxyNotConfigured):
        drive_import.start_import("https://drive.google.com/drive/folders/ABCDEFGHIJ")


def test_start_import_bad_url_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    with pytest.raises(ValueError):
        drive_import.start_import("nonsense")


def test_start_import_rejects_concurrent(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    monkeypatch.setitem(drive_import._import_state, "state", "running")
    with pytest.raises(drive_import.ImportAlreadyRunning):
        drive_import.start_import("https://drive.google.com/drive/folders/ABCDEFGHIJ")


def test_start_import_launches_background_thread(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    monkeypatch.setitem(drive_import._import_state, "state", "idle")
    called = {}
    monkeypatch.setattr(drive_import, "run_import",
                        lambda url: called.update(url=url))
    t = drive_import.start_import("https://drive.google.com/drive/folders/ABCDEFGHIJ")
    t.join(timeout=2)
    assert called.get("url", "").endswith("ABCDEFGHIJ")
```

Append to `tests/test_api.py`:

```python
def test_import_without_proxy_returns_503(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import",
                    json={"folder_url": "https://drive.google.com/drive/folders/X123456789"})
    assert r.status_code == 503


def test_import_bad_url_returns_400(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import", json={"folder_url": "not-a-url"})
    assert r.status_code == 400


def test_import_already_running_returns_409(client, monkeypatch):
    import app.config as cfg
    from app.services import drive_import
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    client.post("/api/login", json={"password": "pw"})
    monkeypatch.setitem(drive_import._import_state, "state", "running")
    r = client.post("/api/videos/import",
                    json={"folder_url": "https://drive.google.com/drive/folders/X123456789"})
    assert r.status_code == 409


def test_import_valid_request_starts(client, monkeypatch):
    import app.config as cfg
    from app.services import drive_import
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    started = {}
    monkeypatch.setattr(drive_import, "start_import",
                        lambda url: started.update(url=url))
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import",
                    json={"folder_url": "https://drive.google.com/drive/folders/X123456789"})
    assert r.status_code == 200 and r.json()["started"] is True
    assert started["url"].endswith("X123456789")


def test_import_status_returns_state(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.get("/api/videos/import/status")
    assert r.status_code == 200
    assert "state" in r.json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_drive_import.py tests/test_api.py -k "import" -q`
Expected: FAIL — `start_import` missing; `/api/videos/import` routes return 404.

- [ ] **Step 3: Add `start_import` to `app/services/drive_import.py`**

Append at the end of the file:

```python
def start_import(folder_url: str) -> threading.Thread:
    """Validate input and launch a background import.

    Raises ProxyNotConfigured, ValueError (bad URL), or ImportAlreadyRunning.
    Returns the started daemon Thread.
    """
    if not config.DRIVE_IMPORT_PROXY:
        raise ProxyNotConfigured()
    parse_folder_id(folder_url)  # raises ValueError on a bad URL
    with _state_lock:
        if _import_state["state"] == "running":
            raise ImportAlreadyRunning()
        _import_state["state"] = "running"
    t = threading.Thread(target=run_import, args=(folder_url,), daemon=True)
    t.start()
    return t
```

- [ ] **Step 4: Add the API endpoints to `app/api/videos.py`**

Append at the end of the file:

```python
@router.post("/import")
def import_drive(body: dict):
    from app.services import drive_import
    url = (body or {}).get("folder_url", "")
    try:
        drive_import.start_import(url)
    except drive_import.ProxyNotConfigured:
        raise HTTPException(status_code=503,
                            detail="DRIVE_IMPORT_PROXY is not configured")
    except drive_import.ImportAlreadyRunning:
        raise HTTPException(status_code=409,
                            detail="an import is already running")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"started": True}


@router.get("/import/status")
def import_status():
    from app.services import drive_import
    return drive_import.get_state()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_drive_import.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: all pass (67 total).

- [ ] **Step 7: Commit**

```bash
git add app/services/drive_import.py app/api/videos.py tests/test_drive_import.py tests/test_api.py
git commit -m "feat: drive import API endpoints and background launcher"
```

---

## Task 5: Frontend — import button, modal, progress panel

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/Videos.tsx`

There is no frontend test framework; verification is a successful TypeScript build.

- [ ] **Step 1: Add API methods in `frontend/src/api.ts`**

Inside the `api` object, after the `deleteVideo` line, add:

```typescript
  importDrive: (folderUrl: string) =>
    req('/videos/import', { method: 'POST', body: JSON.stringify({ folder_url: folderUrl }) }),
  importStatus: () => req('/videos/import/status'),
```

- [ ] **Step 2: Update the React import in `Videos.tsx`**

Replace line 1:

```typescript
import { useEffect, useRef, useState, type DragEvent } from 'react'
```

with:

```typescript
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
```

- [ ] **Step 3: Add `Card`, `Modal`, `ProgressBar`, `TextField` to the ui import in `Videos.tsx`**

Replace the `from '../components/ui'` import block (lines 4-17) with:

```typescript
import {
  Badge,
  Button,
  Card,
  EmptyState,
  LogPanel,
  Modal,
  PageTitle,
  ProgressBar,
  SectionTitle,
  TBody,
  THead,
  Table,
  Td,
  TextField,
  Th,
  Tr,
} from '../components/ui'
```

- [ ] **Step 4: Add the `ImportState` type in `Videos.tsx`**

After the `type Video = { ... }` block (after line 29), add:

```typescript
type ImportState = {
  state: string
  total: number
  done: number
  skipped: number
  failed: number
  current: string | null
  errors: string[]
}
```

- [ ] **Step 5: Add import state hooks in `Videos.tsx`**

After the `const inputRef = useRef<HTMLInputElement | null>(null)` line, add:

```typescript
  const [importOpen, setImportOpen] = useState(false)
  const [importUrl, setImportUrl] = useState('')
  const [importing, setImporting] = useState(false)
  const [importProg, setImportProg] = useState<ImportState | null>(null)
```

- [ ] **Step 6: Add the polling effect and submit handler in `Videos.tsx`**

After the existing `useEffect` that calls `load()` (after line 61), add:

```typescript
  useEffect(() => {
    if (!importing) return
    const tick = () =>
      api
        .importStatus()
        .then((s: ImportState) => {
          setImportProg(s)
          if (s.state === 'done' || s.state === 'error') {
            setImporting(false)
            onToast(
              s.state === 'done'
                ? {
                    msg: `Imported ${s.done}, skipped ${s.skipped}, failed ${s.failed}`,
                    tone: s.failed ? 'orange' : 'green',
                  }
                : { msg: 'Drive import failed', tone: 'red' },
            )
            load()
          }
        })
        .catch(() => {})
    tick()
    const t = setInterval(tick, 2000)
    return () => clearInterval(t)
  }, [importing])

  const startImport = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const url = importUrl.trim()
    if (!url) return
    try {
      const r = await api.importDrive(url)
      if (r?.started) {
        setImportOpen(false)
        setImportUrl('')
        setImportProg(null)
        setImporting(true)
      } else {
        onToast({ msg: r?.detail || 'Could not start import', tone: 'red' })
      }
    } catch {
      onToast({ msg: 'Could not start import', tone: 'red' })
    }
  }
```

- [ ] **Step 7: Add the "Import from Drive" button to the page header**

Replace the `right={ ... }` prop of `<PageTitle>` (lines 124-136) with:

```tsx
        right={
          <>
            <Button variant="secondary" onClick={() => setImportOpen(true)}>
              Import from Drive
            </Button>
            <Button
              variant="primary"
              onClick={publish}
              disabled={running || pending.length === 0}
            >
              {running
                ? 'Publishing…'
                : pending.length
                  ? `Publish ${pending.length}`
                  : 'Publish'}
            </Button>
          </>
        }
```

- [ ] **Step 8: Add the import progress panel and the modal**

Immediately after the closing `</div>` of the drop-zone block (after line 174, before the `{pending.length > 0 && (` section), add:

```tsx
      {importProg && (importing || importProg.state !== 'idle') && (
        <section>
          <SectionTitle>Drive import</SectionTitle>
          <Card className="p-4 flex flex-col gap-3">
            <ProgressBar value={importProg.done} total={importProg.total} />
            <div className="text-[12.5px] text-[var(--color-ink-3)]">
              {importing
                ? importProg.current
                  ? `Downloading ${importProg.current}…`
                  : 'Listing folder…'
                : `Done — ${importProg.done} imported, ${importProg.skipped} skipped, ${importProg.failed} failed`}
            </div>
            {importProg.errors.length > 0 && (
              <div className="text-[12px] text-[var(--color-red)] flex flex-col gap-0.5">
                {importProg.errors.slice(0, 5).map((er, i) => (
                  <div key={i}>{er}</div>
                ))}
              </div>
            )}
          </Card>
        </section>
      )}
```

Immediately before the final closing `</div>` of the component's returned JSX (before line 283), add:

```tsx
      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title="Import from Google Drive"
        onSubmit={startImport}
        submitLabel="Start import"
      >
        <TextField
          label="Public Drive folder link"
          placeholder="https://drive.google.com/drive/folders/…"
          hint="The folder must be shared as 'anyone with the link'."
          value={importUrl}
          onChange={(e) => setImportUrl(e.target.value)}
          autoFocus
        />
      </Modal>
```

- [ ] **Step 9: Build the frontend to verify it compiles**

Run: `export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" && npm --prefix frontend run build`
Expected: `tsc -b` passes with no type errors and `vite build` completes, writing `frontend/dist/`.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/Videos.tsx
git commit -m "feat: Drive import UI on the Videos page"
```

---

## Task 6: Server B — dedicated import proxy (`tinyproxy`)

**Server:** `107.174.1.82` (root). This is an ops task; "tests" are reachability checks.

- [ ] **Step 1: Install tinyproxy**

SSH to `107.174.1.82` and run:

```bash
apt-get update -qq && apt-get install -y tinyproxy
```

- [ ] **Step 2: Generate a proxy password and configure tinyproxy**

Run on server B (replace nothing — the command generates and prints the password):

```bash
PROXY_PW=$(python3 -c "import secrets,string; a=string.ascii_letters+string.digits; print(''.join(secrets.choice(a) for _ in range(20)))")
echo "PROXY PASSWORD: $PROXY_PW"
sed -i 's/^Port .*/Port 8118/' /etc/tinyproxy/tinyproxy.conf
grep -q '^Allow 1.15.155.204' /etc/tinyproxy/tinyproxy.conf \
  || echo 'Allow 1.15.155.204' >> /etc/tinyproxy/tinyproxy.conf
grep -q '^BasicAuth ' /etc/tinyproxy/tinyproxy.conf \
  || echo "BasicAuth mkt $PROXY_PW" >> /etc/tinyproxy/tinyproxy.conf
```

Record the printed password — it is needed for `DRIVE_IMPORT_PROXY` in Task 7.

- [ ] **Step 3: Restart tinyproxy and enable on boot**

```bash
systemctl restart tinyproxy && systemctl enable tinyproxy
systemctl is-active tinyproxy
ss -tlnp | grep ':8118'
```

Expected: `active`; a listener on `:8118`.

- [ ] **Step 4: Verify the proxy works from server B**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -x "http://mkt:$PROXY_PW@127.0.0.1:8118" https://drive.google.com
```

Expected: `200` or `30x` (drive.google.com is reachable through the proxy).
Note: `127.0.0.1` is in tinyproxy's default `Allow` list, so this local check works.

- [ ] **Step 5: Verify reachability from server A**

SSH to `1.15.155.204` and run (substitute the recorded password):

```bash
curl -s -o /dev/null -w "%{http_code}\n" -x "http://mkt:<PROXY_PW>@107.174.1.82:8118" https://www.google.com
```

Expected: `200`. If it times out, port `8118` is blocked by a provider firewall on server B — open it for inbound TCP from `1.15.155.204`, then re-test.

---

## Task 7: Deploy updated mkt to Server A + end-to-end verification

**Server:** `1.15.155.204` (root), app at `/opt/mkt`, systemd service `mkt`. Ops task.

- [ ] **Step 1: Confirm the full test suite passes locally**

Run: `./.venv/bin/pytest -q`
Expected: all pass (67).

- [ ] **Step 2: Build the frontend**

Run: `export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" && npm --prefix frontend run build`
Expected: `frontend/dist/` rebuilt.

- [ ] **Step 3: Package and upload the updated app**

Create a tarball of the backend + built frontend and copy it to server A:

```bash
cd /Users/Zhuanz/Documents/mkt
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-update.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app geelark_client.py generate_caption.py requirements.txt frontend/dist
```

Copy `/tmp/mkt-update.tar.gz` to `/tmp/` on `1.15.155.204` (scp).

- [ ] **Step 4: Add `DRIVE_IMPORT_PROXY` to the server's `.env`**

On server A, append to `/opt/mkt/.env` (substitute the Task 6 password):

```
DRIVE_IMPORT_PROXY=http://mkt:<PROXY_PW>@107.174.1.82:8118
```

- [ ] **Step 5: Unpack, install the new dependency, restart**

On server A:

```bash
tar -xzf /tmp/mkt-update.tar.gz -C /opt/mkt
/opt/mkt/.venv/bin/pip install -q -i https://mirrors.cloud.tencent.com/pypi/simple/ \
  --trusted-host mirrors.cloud.tencent.com 'gdown>=5.2.0'
systemctl restart mkt
sleep 5
systemctl is-active mkt
```

Expected: `active`. The `drive_file_id` migration runs automatically on startup via `init_schema`.

- [ ] **Step 6: Verify the import status endpoint**

On server A:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/videos/import/status
```

Expected: `401` (endpoint exists, behind auth) — confirms the new route is live. (A `404` means the new code did not deploy.)

- [ ] **Step 7: End-to-end test with a real public Drive folder**

In a browser, log in to `http://1.15.155.204:8080`, open the Videos page, click **Import from Drive**, paste a small public test folder link, and start the import. Confirm: the progress panel advances, the toast reports the result, and the imported videos appear in the Pending queue as `PENDING`. Re-run the same import and confirm all files are reported as skipped (dedup works).

- [ ] **Step 8: Clean up**

On server A: `rm -f /tmp/mkt-update.tar.gz`. Locally: `rm -f /tmp/mkt-update.tar.gz`.

---

## Self-Review Notes

- **Spec coverage:** Server B proxy (Task 6); `gdown` dependency + `DRIVE_IMPORT_PROXY` (Tasks 2-3, 7); `drive_import.py` parse/list/filter/download (Tasks 2-3); background job model + no `OPERATION_LOCK` + `job_runs` audit (Tasks 3-4); API `POST /import` + `GET /import/status` (Task 4); frontend button/modal/progress (Task 5); `drive_file_id` column + idempotent migration (Task 1); error-handling matrix (Tasks 3-4); testing (every backend task). All spec sections map to a task.
- **Type consistency:** `run_import(folder_url)` / `start_import(folder_url)` / `parse_folder_id` / `is_video` / `unique_path` / `get_state` / `_update` are used with consistent signatures across service, API, and tests. `ImportState` (frontend) mirrors the `get_state()` dict keys.
- **No placeholders:** every step contains the actual code or command.
