# Avatar Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Accounts drawer's "Picture URL" text field with an "Upload image" control. Server stores the file locally; the existing `set_pics` cron uploads it to GeeLark's CDN on each dispatch so the URL handed to `edit_tiktok_profile` is always fresh.

**Architecture:** Add `accounts.profile_pic_path` (filename under `data/avatars/`) as the source of truth. New API endpoints handle upload/get/delete of the local file. The dashboard fetches the avatar via a new GET endpoint, gated by a server-computed `avatar_src` field in every accounts row. The 3-day GeeLark CDN expiry is sidestepped because `set_pics` calls `client.upload_file` fresh at dispatch time.

**Tech Stack:** FastAPI + SQLite WAL · React 19 + Vite · existing `geelark_client.upload_file` (same two-step pattern videos already use). No new dependencies.

**Spec:** [docs/superpowers/specs/2026-05-23-avatar-upload-design.md](../specs/2026-05-23-avatar-upload-design.md)

---

## Conventions for this plan

- Working dir: `/Users/Zhuanz/Documents/mkt`.
- Backend tests live in `tests/test_*.py` (flat layout — do NOT create `tests/api/` or `tests/services/` subdirs). New backend test cases go into the existing `tests/test_api.py` (HTTP endpoints), `tests/test_repositories.py` (repo helpers), and `tests/test_services.py` (service functions).
- Each task ends with `./.venv/bin/pytest -q` green (current 100 passed → target ~108) and a commit.
- For frontend tasks, the gate is `cd frontend && npm run build`. If `npm` not on PATH: `export PATH="/opt/homebrew/bin:$PATH"`.
- Commit messages follow existing repo convention (`feat:`, `fix:`, `docs:`).

---

## Task 1: Schema migration + AVATAR_DIR config + `with_avatar_src` helper

Lays the groundwork: the new DB column, the avatars directory, the row-shaping helper that `list_all` and `get` apply so every API consumer sees `avatar_src`.

**Files:**
- Modify: `app/config.py`
- Modify: `app/db.py`
- Modify: `app/repositories/accounts.py`
- Modify: `tests/test_repositories.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write the failing test in `tests/test_repositories.py`**

Append this to `tests/test_repositories.py`:

```python
def test_with_avatar_src_uses_path_when_present(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    (tmp_path / "5.jpg").write_bytes(b"x")
    from app.repositories.accounts import with_avatar_src
    row = with_avatar_src({"id": 5, "profile_pic_path": "5.jpg",
                           "profile_pic_url": None})
    assert row["avatar_src"].startswith("/api/accounts/5/avatar?v=")

def test_with_avatar_src_falls_back_to_url(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    from app.repositories.accounts import with_avatar_src
    row = with_avatar_src({"id": 7, "profile_pic_path": None,
                           "profile_pic_url": "https://x.com/a.jpg"})
    assert row["avatar_src"] == "https://x.com/a.jpg"

def test_with_avatar_src_null_when_neither(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    from app.repositories.accounts import with_avatar_src
    row = with_avatar_src({"id": 8, "profile_pic_path": None,
                           "profile_pic_url": None})
    assert row["avatar_src"] is None
```

- [ ] **Step 2: Append a migration assertion to `tests/test_db.py`**

Find the existing test file. Append:

```python
def test_init_schema_creates_profile_pic_path_column(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "fresh.db")
    from app.db import get_connection, init_schema
    conn = get_connection()
    init_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    assert "profile_pic_path" in cols
    conn.close()

def test_migrate_adds_profile_pic_path_to_old_db(tmp_path, monkeypatch):
    import sqlite3, app.config as cfg
    db_path = tmp_path / "old.db"
    # Simulate an old DB created before profile_pic_path existed.
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE accounts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      profile_name TEXT
    )""")
    conn.commit()
    conn.close()
    # Run init via the real connection factory; _migrate should add the column.
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    from app.db import get_connection, init_schema
    conn = get_connection()
    init_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    assert "profile_pic_path" in cols
    conn.close()
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_repositories.py::test_with_avatar_src_uses_path_when_present \
  tests/test_repositories.py::test_with_avatar_src_falls_back_to_url \
  tests/test_repositories.py::test_with_avatar_src_null_when_neither \
  tests/test_db.py::test_init_schema_creates_profile_pic_path_column \
  tests/test_db.py::test_migrate_adds_profile_pic_path_to_old_db
```

Expected: all 5 fail (helper missing; column not in schema).

- [ ] **Step 4: Add `AVATAR_DIR` to `app/config.py`**

Find:
```python
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
```

Insert immediately after:
```python
AVATAR_DIR = Path(os.getenv("AVATAR_DIR", DATA_DIR / "avatars"))
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 5: Add the new column to the SCHEMA and the migration**

In `app/db.py`, find the `accounts` CREATE TABLE block:
```python
  profile_pic_url TEXT,
  group_name TEXT,
```
Replace with (adds `profile_pic_path` between):
```python
  profile_pic_url TEXT,
  profile_pic_path TEXT,
  group_name TEXT,
```

Then extend `_migrate` so old databases get the column too. Find:
```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for databases created before a column existed.

    ALTER TABLE is DDL and autocommits in SQLite; init_schema's later commit
    covers the settings rows.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "drive_file_id" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN drive_file_id TEXT")
```

Replace with:
```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for databases created before a column existed.

    ALTER TABLE is DDL and autocommits in SQLite; init_schema's later commit
    covers the settings rows.
    """
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "drive_file_id" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN drive_file_id TEXT")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    if "profile_pic_path" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN profile_pic_path TEXT")
```

- [ ] **Step 6: Add `profile_pic_path` to the repository's `_COLUMNS` and write `with_avatar_src`**

In `app/repositories/accounts.py`, find:
```python
import sqlite3

_COLUMNS = {
    "geelark_phone_id", "profile_name", "email", "proxy", "profile_pic_url",
    "group_name", "tiktok_logged_in", "pic_status", "pic_task_id",
    "warmed_up", "time_spent_warming", "max_warm_time", "last_warm_at",
    "last_warm_result", "last_warm_notes", "last_posted_at",
}
```

Replace with:
```python
import sqlite3
from app import config

_COLUMNS = {
    "geelark_phone_id", "profile_name", "email", "proxy",
    "profile_pic_url", "profile_pic_path",
    "group_name", "tiktok_logged_in", "pic_status", "pic_task_id",
    "warmed_up", "time_spent_warming", "max_warm_time", "last_warm_at",
    "last_warm_result", "last_warm_notes", "last_posted_at",
}


def with_avatar_src(row: dict) -> dict:
    """Compute a single `avatar_src` URL for the frontend.

    Prefers an uploaded local file (served via /api/accounts/{id}/avatar with
    an mtime cache-buster); falls back to the legacy `profile_pic_url` value;
    otherwise None. Mutates and returns the row for convenience.
    """
    if row.get("profile_pic_path"):
        path = config.AVATAR_DIR / row["profile_pic_path"]
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            mtime = 0
        row["avatar_src"] = f"/api/accounts/{row['id']}/avatar?v={mtime}"
    elif row.get("profile_pic_url"):
        row["avatar_src"] = row["profile_pic_url"]
    else:
        row["avatar_src"] = None
    return row
```

Then update `get` and `list_all` to apply `with_avatar_src` to each returned row. Find:
```python
def get(conn: sqlite3.Connection, account_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return dict(r) if r else None

def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM accounts ORDER BY id")]
```

Replace with:
```python
def get(conn: sqlite3.Connection, account_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return with_avatar_src(dict(r)) if r else None

def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [with_avatar_src(dict(r)) for r in conn.execute(
        "SELECT * FROM accounts ORDER BY id")]
```

- [ ] **Step 7: Re-run the new tests; they should pass**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_repositories.py::test_with_avatar_src_uses_path_when_present \
  tests/test_repositories.py::test_with_avatar_src_falls_back_to_url \
  tests/test_repositories.py::test_with_avatar_src_null_when_neither \
  tests/test_db.py::test_init_schema_creates_profile_pic_path_column \
  tests/test_db.py::test_migrate_adds_profile_pic_path_to_old_db
```

Expected: 5 passed.

- [ ] **Step 8: Run the full suite to confirm nothing broke**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q
```

Expected: 105 passed (100 baseline + 5 new).

- [ ] **Step 9: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add app/config.py app/db.py app/repositories/accounts.py \
        tests/test_repositories.py tests/test_db.py
git commit -m "feat: add profile_pic_path column + with_avatar_src + AVATAR_DIR"
```

---

## Task 2: `POST /api/accounts/{id}/avatar` — upload endpoint

**Files:**
- Modify: `app/api/accounts.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests in `tests/test_api.py`**

Append to `tests/test_api.py`:

```python
def test_upload_avatar_creates_file_and_clears_pic_status(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "AVU1"}).json()["id"]
    # Mark pic_status='done' so we can prove upload resets it.
    client.patch(f"/api/accounts/{aid}", json={"pic_status": "done",
                                               "pic_task_id": "x"})
    files = {"file": ("a.jpg", b"\xff\xd8\xffjpeg", "image/jpeg")}
    r = client.post(f"/api/accounts/{aid}/avatar", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile_pic_path"] == f"{aid}.jpg"
    assert body["pic_status"] == "none"
    assert body["pic_task_id"] is None
    assert (tmp_path / f"{aid}.jpg").exists()
    assert (tmp_path / f"{aid}.jpg").read_bytes() == b"\xff\xd8\xffjpeg"

def test_upload_avatar_replaces_prior_extension(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "AVU2"}).json()["id"]
    client.post(f"/api/accounts/{aid}/avatar",
                files={"file": ("a.jpg", b"j", "image/jpeg")})
    r = client.post(f"/api/accounts/{aid}/avatar",
                    files={"file": ("b.png", b"p", "image/png")})
    assert r.status_code == 200
    assert not (tmp_path / f"{aid}.jpg").exists()
    assert (tmp_path / f"{aid}.png").exists()
    assert r.json()["profile_pic_path"] == f"{aid}.png"

def test_upload_avatar_rejects_bad_mime(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "AVU3"}).json()["id"]
    r = client.post(f"/api/accounts/{aid}/avatar",
                    files={"file": ("a.txt", b"hi", "text/plain")})
    assert r.status_code == 400
    assert "format" in r.json()["detail"].lower()

def test_upload_avatar_rejects_oversize(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "AVU4"}).json()["id"]
    big = b"x" * (5 * 1024 * 1024 + 1)
    r = client.post(f"/api/accounts/{aid}/avatar",
                    files={"file": ("a.jpg", big, "image/jpeg")})
    assert r.status_code == 400
    assert "large" in r.json()["detail"].lower()

def test_upload_avatar_404_when_account_missing(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/accounts/9999/avatar",
                    files={"file": ("a.jpg", b"x", "image/jpeg")})
    assert r.status_code == 404
```

- [ ] **Step 2: Run them; expect 5 failures (endpoint doesn't exist yet → 404 / 405)**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_api.py::test_upload_avatar_creates_file_and_clears_pic_status \
  tests/test_api.py::test_upload_avatar_replaces_prior_extension \
  tests/test_api.py::test_upload_avatar_rejects_bad_mime \
  tests/test_api.py::test_upload_avatar_rejects_oversize \
  tests/test_api.py::test_upload_avatar_404_when_account_missing
```

Expected: 5 failed.

- [ ] **Step 3: Add the POST endpoint to `app/api/accounts.py`**

Find the imports at the top of `app/api/accounts.py`:
```python
from fastapi import APIRouter, HTTPException
from app.repositories import accounts as acc_repo
```

Replace with:
```python
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from app import config
from app.repositories import accounts as acc_repo

_AVATAR_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_AVATAR_MAX_BYTES = 5 * 1024 * 1024
```

Then add the upload endpoint. Append at the bottom of the file (after `create_profiles_stream`):

```python
@router.post("/{account_id}/avatar")
async def upload_avatar(account_id: int, file: UploadFile = File(...)):
    conn = _db()
    if acc_repo.get(conn, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    ext = _AVATAR_MIME_TO_EXT.get((file.content_type or "").lower())
    if ext is None:
        raise HTTPException(status_code=400,
                            detail="unsupported image format")
    # Stream-copy with a hard size cap; abort if it exceeds the limit before
    # touching the destination file.
    buf = await file.read(_AVATAR_MAX_BYTES + 1)
    if len(buf) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="image too large")
    # Sweep any pre-existing file under this account id (covers ext change).
    for old in config.AVATAR_DIR.glob(f"{account_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    dest = config.AVATAR_DIR / f"{account_id}.{ext}"
    dest.write_bytes(buf)
    acc_repo.update(conn, account_id, {
        "profile_pic_path": dest.name,
        "pic_status": "none",
        "pic_task_id": None,
    })
    return acc_repo.get(conn, account_id)
```

- [ ] **Step 4: Re-run the new tests**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_api.py::test_upload_avatar_creates_file_and_clears_pic_status \
  tests/test_api.py::test_upload_avatar_replaces_prior_extension \
  tests/test_api.py::test_upload_avatar_rejects_bad_mime \
  tests/test_api.py::test_upload_avatar_rejects_oversize \
  tests/test_api.py::test_upload_avatar_404_when_account_missing
```

Expected: 5 passed.

- [ ] **Step 5: Run full suite**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q
```

Expected: 110 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add app/api/accounts.py tests/test_api.py
git commit -m "feat: POST /api/accounts/{id}/avatar — multipart upload with size + mime guards"
```

---

## Task 3: `GET /api/accounts/{id}/avatar` — serve the file

**Files:**
- Modify: `app/api/accounts.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_get_avatar_serves_uploaded_file(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "GAV1"}).json()["id"]
    client.post(f"/api/accounts/{aid}/avatar",
                files={"file": ("a.jpg", b"hello-jpeg", "image/jpeg")})
    r = client.get(f"/api/accounts/{aid}/avatar")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers.get("cache-control") == "private, max-age=60"
    assert r.content == b"hello-jpeg"

def test_get_avatar_returns_404_when_none_uploaded(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "GAV2"}).json()["id"]
    r = client.get(f"/api/accounts/{aid}/avatar")
    assert r.status_code == 404
```

- [ ] **Step 2: Run them; expect both fail**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_api.py::test_get_avatar_serves_uploaded_file \
  tests/test_api.py::test_get_avatar_returns_404_when_none_uploaded
```

Expected: 2 failed.

- [ ] **Step 3: Add the GET endpoint to `app/api/accounts.py`**

First update the import line. Find:
```python
from fastapi import APIRouter, HTTPException, UploadFile, File
```
Replace with:
```python
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
```

Then add the constant for ext-to-mime lookup near the other avatar constants. Find:
```python
_AVATAR_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_AVATAR_MAX_BYTES = 5 * 1024 * 1024
```
Replace with (just adds a reverse map):
```python
_AVATAR_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_AVATAR_EXT_TO_MIME = {v: k for k, v in _AVATAR_MIME_TO_EXT.items()}
_AVATAR_MAX_BYTES = 5 * 1024 * 1024
```

Then append the GET endpoint at the bottom of the file:

```python
@router.get("/{account_id}/avatar")
def get_avatar(account_id: int):
    conn = _db()
    row = acc_repo.get(conn, account_id)
    if row is None or not row.get("profile_pic_path"):
        raise HTTPException(status_code=404, detail="no avatar")
    path = config.AVATAR_DIR / row["profile_pic_path"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="no avatar")
    ext = path.suffix.lstrip(".").lower()
    media_type = _AVATAR_EXT_TO_MIME.get(ext, "application/octet-stream")
    return FileResponse(
        path=str(path),
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=60"},
    )
```

- [ ] **Step 4: Re-run the new tests**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_api.py::test_get_avatar_serves_uploaded_file \
  tests/test_api.py::test_get_avatar_returns_404_when_none_uploaded
```

Expected: 2 passed.

- [ ] **Step 5: Full suite**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q
```

Expected: 112 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add app/api/accounts.py tests/test_api.py
git commit -m "feat: GET /api/accounts/{id}/avatar — serves uploaded file"
```

---

## Task 4: `DELETE /api/accounts/{id}/avatar` + cleanup on account delete

**Files:**
- Modify: `app/api/accounts.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_delete_avatar_clears_file_and_column(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "DAV1"}).json()["id"]
    client.post(f"/api/accounts/{aid}/avatar",
                files={"file": ("a.jpg", b"x", "image/jpeg")})
    assert (tmp_path / f"{aid}.jpg").exists()
    r = client.delete(f"/api/accounts/{aid}/avatar")
    assert r.status_code == 200
    assert not (tmp_path / f"{aid}.jpg").exists()
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    assert row["profile_pic_path"] is None
    assert row["avatar_src"] is None

def test_delete_avatar_when_already_empty_is_noop(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "DAV2"}).json()["id"]
    r = client.delete(f"/api/accounts/{aid}/avatar")
    assert r.status_code == 200  # idempotent

def test_delete_account_also_removes_avatar_file(client, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "DAV3"}).json()["id"]
    client.post(f"/api/accounts/{aid}/avatar",
                files={"file": ("a.jpg", b"x", "image/jpeg")})
    assert (tmp_path / f"{aid}.jpg").exists()
    r = client.delete(f"/api/accounts/{aid}")
    assert r.status_code == 200
    assert not (tmp_path / f"{aid}.jpg").exists()
```

- [ ] **Step 2: Run them; expect 3 failed (DELETE avatar endpoint doesn't exist; delete_account doesn't sweep file yet)**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_api.py::test_delete_avatar_clears_file_and_column \
  tests/test_api.py::test_delete_avatar_when_already_empty_is_noop \
  tests/test_api.py::test_delete_account_also_removes_avatar_file
```

Expected: 3 failed.

- [ ] **Step 3: Add the DELETE avatar endpoint and sweep on account delete in `app/api/accounts.py`**

First, change `delete_account` to sweep files. Find:
```python
@router.delete("/{account_id}")
def delete_account(account_id: int):
    acc_repo.delete(_db(), account_id)
    return {"ok": True}
```

Replace with:
```python
@router.delete("/{account_id}")
def delete_account(account_id: int):
    for old in config.AVATAR_DIR.glob(f"{account_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    acc_repo.delete(_db(), account_id)
    return {"ok": True}
```

Then append the DELETE avatar endpoint at the bottom of the file:

```python
@router.delete("/{account_id}/avatar")
def delete_avatar(account_id: int):
    conn = _db()
    row = acc_repo.get(conn, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="account not found")
    for old in config.AVATAR_DIR.glob(f"{account_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    acc_repo.update(conn, account_id, {"profile_pic_path": None})
    return {"ok": True}
```

- [ ] **Step 4: Re-run the new tests**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_api.py::test_delete_avatar_clears_file_and_column \
  tests/test_api.py::test_delete_avatar_when_already_empty_is_noop \
  tests/test_api.py::test_delete_account_also_removes_avatar_file
```

Expected: 3 passed.

- [ ] **Step 5: Full suite**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q
```

Expected: 115 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add app/api/accounts.py tests/test_api.py
git commit -m "feat: DELETE /api/accounts/{id}/avatar + cleanup on account delete"
```

---

## Task 5: `set_pics` dispatch from `profile_pic_path`

**Files:**
- Modify: `app/services/geelark_service.py`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_services.py`:

```python
def test_set_pics_dispatches_from_uploaded_path(db, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    (tmp_path / "1.jpg").write_bytes(b"local-bytes")

    from app.repositories import accounts as ar
    aid = ar.create(db, {
        "profile_name": "P_PATH",
        "geelark_phone_id": "phone-1",
        "tiktok_logged_in": 1,
        "profile_pic_path": "1.jpg",
    })

    captured = {"uploaded": None, "edit": None}

    class FakeClient:
        def upload_file(self, path, ext):
            captured["uploaded"] = (path, ext)
            return "https://cdn.geelark/abc.jpg"
        def edit_tiktok_profile(self, env_id, avatar=None):
            captured["edit"] = (env_id, avatar)
            return "task-99"
        def query_tasks(self, ids):
            return []

    from app.services import geelark_service
    geelark_service.set_pics(db, FakeClient())

    assert captured["uploaded"] is not None
    assert captured["uploaded"][0].endswith(f"{aid}.jpg".replace(f"{aid}", "1")) \
        or captured["uploaded"][0].endswith("1.jpg")
    assert captured["uploaded"][1] == "jpg"
    assert captured["edit"] == ("phone-1", "https://cdn.geelark/abc.jpg")

    row = ar.get(db, aid)
    assert row["pic_status"] == "initiated"
    assert row["pic_task_id"] == "task-99"


def test_set_pics_falls_back_to_url_when_path_file_missing(db, tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    # path is set in DB but file doesn't exist on disk; URL is also set.
    from app.repositories import accounts as ar
    aid = ar.create(db, {
        "profile_name": "P_FALLBACK",
        "geelark_phone_id": "phone-2",
        "tiktok_logged_in": 1,
        "profile_pic_path": "ghost.jpg",
        "profile_pic_url": "https://legacy/url.jpg",
    })

    captured = {"uploaded": False, "edit": None}

    class FakeClient:
        def upload_file(self, path, ext):
            captured["uploaded"] = True
            return "nope"
        def edit_tiktok_profile(self, env_id, avatar=None):
            captured["edit"] = (env_id, avatar)
            return "task-55"
        def query_tasks(self, ids):
            return []

    from app.services import geelark_service
    geelark_service.set_pics(db, FakeClient())
    assert captured["uploaded"] is False
    assert captured["edit"] == ("phone-2", "https://legacy/url.jpg")
    assert ar.get(db, aid)["pic_status"] == "initiated"


def test_set_pics_url_only_account_still_dispatches(db, tmp_path, monkeypatch):
    """Legacy URL-only accounts (no path) keep working unchanged."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "AVATAR_DIR", tmp_path)
    from app.repositories import accounts as ar
    ar.create(db, {
        "profile_name": "P_URL",
        "geelark_phone_id": "phone-3",
        "tiktok_logged_in": 1,
        "profile_pic_url": "https://legacy/only.jpg",
    })
    captured = {"uploaded": False, "edit": None}

    class FakeClient:
        def upload_file(self, path, ext):
            captured["uploaded"] = True
            return "nope"
        def edit_tiktok_profile(self, env_id, avatar=None):
            captured["edit"] = (env_id, avatar)
            return "task-7"
        def query_tasks(self, ids):
            return []

    from app.services import geelark_service
    geelark_service.set_pics(db, FakeClient())
    assert captured["uploaded"] is False
    assert captured["edit"] == ("phone-3", "https://legacy/only.jpg")
```

- [ ] **Step 2: Run them; expect 3 failures (current `set_pics` only checks `profile_pic_url`)**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_services.py::test_set_pics_dispatches_from_uploaded_path \
  tests/test_services.py::test_set_pics_falls_back_to_url_when_path_file_missing \
  tests/test_services.py::test_set_pics_url_only_account_still_dispatches
```

Expected: 3 failed.

- [ ] **Step 3: Update `set_pics` in `app/services/geelark_service.py`**

First check the file has a `from app import config` import near the top — if not, add it.

```bash
grep -n "from app import config\|import config" /Users/Zhuanz/Documents/mkt/app/services/geelark_service.py
```

If no `config` import exists, add `from app import config` to the imports block.

Then find the body of `set_pics`. The current code is:

```python
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
        if a.get("pic_status") in ("done", "failed"):
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

Replace it with:

```python
def set_pics(conn, client) -> None:
    """For each account with an avatar source (uploaded path or legacy URL),
    tiktok_logged_in, and a phone: resolve a prior task if pic_task_id is set,
    else upload the local file fresh to GeeLark and submit a new edit task.
    """
    for a in acc_repo.list_all(conn):
        has_source = bool(a.get("profile_pic_path") or a.get("profile_pic_url"))
        if not (has_source and a.get("tiktok_logged_in")
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
        if a.get("pic_status") in ("done", "failed"):
            continue
        # Resolve the URL we'll hand to edit_tiktok_profile. Prefer the
        # locally-uploaded file (re-upload to GeeLark CDN each dispatch so
        # the URL is always fresh); fall back to legacy URL otherwise.
        avatar_url = None
        if a.get("profile_pic_path"):
            path = config.AVATAR_DIR / a["profile_pic_path"]
            if path.exists():
                ext = path.suffix.lstrip(".") or "jpg"
                try:
                    avatar_url = client.upload_file(str(path), ext)
                except Exception as e:
                    acc_repo.update(conn, a["id"],
                                    {"pic_status": "failed",
                                     "last_warm_notes": str(e)})
                    continue
        if avatar_url is None and a.get("profile_pic_url"):
            avatar_url = a["profile_pic_url"]
        if avatar_url is None:
            # path set but file missing and no URL fallback — skip silently
            continue
        try:
            task_id = client.edit_tiktok_profile(
                a["geelark_phone_id"], avatar=avatar_url)
            acc_repo.update(conn, a["id"],
                            {"pic_status": "initiated", "pic_task_id": task_id})
        except Exception as e:
            acc_repo.update(conn, a["id"], {"pic_status": "failed",
                                            "last_warm_notes": str(e)})
```

- [ ] **Step 4: Re-run the new tests**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q \
  tests/test_services.py::test_set_pics_dispatches_from_uploaded_path \
  tests/test_services.py::test_set_pics_falls_back_to_url_when_path_file_missing \
  tests/test_services.py::test_set_pics_url_only_account_still_dispatches
```

Expected: 3 passed.

- [ ] **Step 5: Full suite**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q
```

Expected: 118 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add app/services/geelark_service.py tests/test_services.py
git commit -m "feat: set_pics uploads from local avatar file via geelark CDN per dispatch"
```

---

## Task 6: Frontend — drawer rewrite, api.ts, i18n

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/Accounts.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Add `uploadAvatar` / `deleteAvatar` to `frontend/src/api.ts`**

Find:
```ts
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
```

Replace with (inserts two new entries after `setPics`):
```ts
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
  uploadAvatar: (id: number, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch('/api/accounts/' + id + '/avatar', {
      method: 'POST', body: fd, credentials: 'include',
    }).then(async (r) => {
      if (r.status === 401) { window.location.hash = '#login'; throw new Error('unauthorized') }
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    })
  },
  deleteAvatar: (id: number) =>
    req(`/accounts/${id}/avatar`, { method: 'DELETE' }),
```

- [ ] **Step 2: Update i18n locale files**

In `frontend/src/i18n/locales/en.json`, find the `accounts.drawer` block:
```json
    "drawer": {
      "title": "Edit account",
      "identity": "Identity",
      "identityName": "Profile name",
      "identityPhone": "Phone",
      "identityNoPhone": "No phone linked",
      "avatar": "Avatar",
      "avatarUrl": "Picture URL",
      "avatarPreviewEmpty": "Paste a URL to preview the image.",
      "credentials": "Credentials",
```

Replace with:
```json
    "drawer": {
      "title": "Edit account",
      "identity": "Identity",
      "identityName": "Profile name",
      "identityPhone": "Phone",
      "identityNoPhone": "No phone linked",
      "avatar": "Avatar",
      "uploadImage": "Upload image",
      "uploading": "Uploading…",
      "removeImage": "Remove",
      "avatarHint": "Up to 5 MB · JPG, PNG, WebP",
      "credentials": "Credentials",
```

And in the `accounts.toast` block of `en.json`, find:
```json
      "saved": "Changes saved"
    },
```
Replace with:
```json
      "saved": "Changes saved",
      "avatarUpdated": "Avatar updated",
      "avatarFailed": "Avatar upload failed",
      "avatarTooLarge": "Image too large (max 5 MB)",
      "avatarBadFormat": "Unsupported image format"
    },
```

Mirror both in `frontend/src/i18n/locales/zh.json`:

`accounts.drawer` block — find:
```json
    "drawer": {
      "title": "编辑账号",
      "identity": "基础信息",
      "identityName": "账号名",
      "identityPhone": "手机",
      "identityNoPhone": "未关联手机",
      "avatar": "头像",
      "avatarUrl": "头像 URL",
      "avatarPreviewEmpty": "粘贴 URL 后将显示预览。",
      "credentials": "凭据",
```
Replace with:
```json
    "drawer": {
      "title": "编辑账号",
      "identity": "基础信息",
      "identityName": "账号名",
      "identityPhone": "手机",
      "identityNoPhone": "未关联手机",
      "avatar": "头像",
      "uploadImage": "上传图片",
      "uploading": "上传中…",
      "removeImage": "移除",
      "avatarHint": "≤ 5 MB · JPG / PNG / WebP",
      "credentials": "凭据",
```

`accounts.toast` block in zh.json — find:
```json
      "saved": "修改已保存"
    },
```
Replace with:
```json
      "saved": "修改已保存",
      "avatarUpdated": "头像已更新",
      "avatarFailed": "头像上传失败",
      "avatarTooLarge": "图片过大（最多 5 MB）",
      "avatarBadFormat": "图片格式不支持"
    },
```

- [ ] **Step 3: Validate both JSON files**

```bash
cd /Users/Zhuanz/Documents/mkt
python3 -c "import json; json.load(open('frontend/src/i18n/locales/en.json')); json.load(open('frontend/src/i18n/locales/zh.json')); print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Rewrite `frontend/src/pages/Accounts.tsx`**

Replace the entire file with this content (this drops the `profile_pic_url` text field, removes the `previewUrl` debounce, removes `profile_pic_url` from `EditForm`, adds upload/remove handlers, switches both `<Avatar>` call sites to `r.avatar_src` / `editing?.avatar_src`):

```tsx
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { api, streamPost } from '../api'
import type { ToastState } from '../App'
import { parseProxyDebug, parseProxyHost } from '../lib/proxy'
import {
  Avatar,
  Badge,
  Button,
  Drawer,
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
  Toggle,
  Tr,
} from '../components/ui'

type Account = {
  id: number
  profile_name: string
  email: string | null
  proxy: string | null
  profile_pic_url: string | null
  profile_pic_path: string | null
  avatar_src: string | null
  geelark_phone_id: string | null
  tiktok_logged_in: number
  warmed_up: number
  time_spent_warming: number
  max_warm_time: number
  pic_status: string
  last_warm_result: string | null
}

type EditForm = {
  profile_name: string
  email: string
  proxy: string
  max_warm_time: string
}

const toForm = (a: Account): EditForm => ({
  profile_name: a.profile_name,
  email: a.email ?? '',
  proxy: a.proxy ?? '',
  max_warm_time: String(a.max_warm_time ?? 500),
})

const formsEqual = (a: EditForm, b: EditForm) =>
  a.profile_name === b.profile_name &&
  a.email === b.email &&
  a.proxy === b.proxy &&
  a.max_warm_time === b.max_warm_time

const MAX_AVATAR_BYTES = 5 * 1024 * 1024
const AVATAR_MIME_TYPES = ['image/jpeg', 'image/png', 'image/webp']

export default function Accounts({ onToast }: { onToast: (t: ToastState) => void }) {
  const { t } = useTranslation()
  const [rows, setRows] = useState<Account[]>([])
  const [log, setLog] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [newName, setNewName] = useState('')

  // Drawer state
  const [editing, setEditing] = useState<Account | null>(null)
  const [form, setForm] = useState<EditForm | null>(null)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const load = () =>
    api
      .accounts()
      .then(setRows)
      .catch(() => {})
  useEffect(() => {
    load()
  }, [])

  // After an avatar upload/delete, refresh rows AND sync the open drawer's
  // snapshot so the preview reflects the new avatar_src.
  const refreshAndSyncEditing = async (accountId: number) => {
    const next: Account[] = await api.accounts()
    setRows(next)
    const updated = next.find((r) => r.id === accountId)
    if (updated) setEditing(updated)
  }

  const openDrawer = (a: Account) => {
    setEditing(a)
    setForm(toForm(a))
  }

  const isDirty = (): boolean => {
    if (!editing || !form) return false
    return !formsEqual(form, toForm(editing))
  }

  const discardAndClose = () => {
    setEditing(null)
    setForm(null)
    setSaving(false)
    setUploading(false)
  }

  const requestClose = () => {
    if (isDirty() && !confirm(t('accounts.drawer.unsavedConfirm'))) return
    discardAndClose()
  }

  const saveDrawer = async () => {
    if (!editing || !form) return
    const base = toForm(editing)
    if (!form.profile_name.trim()) {
      onToast({
        msg: t('accounts.toast.saveFailed', { field: 'profile_name' }),
        tone: 'red',
      })
      return
    }
    const diff: Record<string, string | number> = {}
    if (base.profile_name !== form.profile_name)
      diff.profile_name = form.profile_name.trim()
    if (base.email !== form.email) diff.email = form.email.trim()
    if (base.proxy !== form.proxy) diff.proxy = form.proxy.trim()
    if (base.max_warm_time !== form.max_warm_time) {
      const n = Number(form.max_warm_time)
      if (!Number.isFinite(n) || n < 0) {
        onToast({
          msg: t('accounts.toast.saveFailed', { field: 'max_warm_time' }),
          tone: 'red',
        })
        return
      }
      diff.max_warm_time = n
    }
    if (Object.keys(diff).length === 0) {
      discardAndClose()
      return
    }
    setSaving(true)
    try {
      await api.updateAccount(editing.id, diff)
      onToast({ msg: t('accounts.toast.saved'), tone: 'green' })
      await load()
      discardAndClose()
    } catch {
      onToast({
        msg: t('accounts.toast.saveFailed', { field: 'account' }),
        tone: 'red',
      })
      setSaving(false)
    }
  }

  const deleteFromDrawer = async () => {
    if (!editing) return
    if (!confirm(t('accounts.deleteConfirm', { name: editing.profile_name }))) return
    try {
      await api.deleteAccount(editing.id)
      onToast({
        msg: t('accounts.toast.removed', { name: editing.profile_name }),
        tone: 'orange',
      })
      await load()
      discardAndClose()
    } catch {
      onToast({ msg: t('accounts.toast.deleteFailed'), tone: 'red' })
    }
  }

  const pickFile = () => fileInputRef.current?.click()

  const onFileChosen = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = '' // reset so picking the same file again still fires onChange
    if (!file || !editing) return
    if (file.size > MAX_AVATAR_BYTES) {
      onToast({ msg: t('accounts.toast.avatarTooLarge'), tone: 'red' })
      return
    }
    if (!AVATAR_MIME_TYPES.includes(file.type)) {
      onToast({ msg: t('accounts.toast.avatarBadFormat'), tone: 'red' })
      return
    }
    setUploading(true)
    try {
      await api.uploadAvatar(editing.id, file)
      onToast({ msg: t('accounts.toast.avatarUpdated'), tone: 'green' })
      await refreshAndSyncEditing(editing.id)
    } catch {
      onToast({ msg: t('accounts.toast.avatarFailed'), tone: 'red' })
    } finally {
      setUploading(false)
    }
  }

  const removeAvatar = async () => {
    if (!editing) return
    setUploading(true)
    try {
      await api.deleteAvatar(editing.id)
      onToast({ msg: t('accounts.toast.avatarUpdated'), tone: 'green' })
      await refreshAndSyncEditing(editing.id)
    } catch {
      onToast({ msg: t('accounts.toast.avatarFailed'), tone: 'red' })
    } finally {
      setUploading(false)
    }
  }

  const importGeelark = async () => {
    try {
      const r = await api.importGeelark()
      onToast({
        msg: t('accounts.toast.imported', { count: r.imported ?? 0 }),
        tone: 'green',
      })
      load()
    } catch {
      onToast({ msg: t('accounts.toast.importFailed'), tone: 'red' })
    }
  }

  const setPics = async () => {
    try {
      await api.setPics()
      onToast({ msg: t('accounts.toast.picsDispatched'), tone: 'blue' })
      load()
    } catch {
      onToast({ msg: t('accounts.toast.picsFailed'), tone: 'red' })
    }
  }

  const createProfiles = () => {
    setLog([])
    setRunning(true)
    streamPost('/accounts/create-profiles-stream', (l) => setLog((p) => [...p, l]))
      .then(() => {
        onToast({ msg: t('accounts.toast.createProfilesDone'), tone: 'green' })
        load()
      })
      .catch(() => onToast({ msg: t('accounts.toast.streamFailed'), tone: 'red' }))
      .finally(() => setRunning(false))
  }

  const addAccount = async (e: FormEvent) => {
    e.preventDefault()
    const name = newName.trim()
    if (!name) return
    try {
      await api.createAccount({ profile_name: name })
      setNewName('')
      setAddOpen(false)
      onToast({ msg: t('accounts.toast.added', { name }), tone: 'blue' })
      load()
    } catch {
      onToast({ msg: t('accounts.toast.nameUnique'), tone: 'red' })
    }
  }

  const loggedIn = rows.filter((r) => r.tiktok_logged_in).length
  const warmed = rows.filter((r) => r.warmed_up).length

  return (
    <div className="flex flex-col gap-7">
      <PageTitle
        title={t('accounts.title')}
        subtitle={t('accounts.subtitle', {
          total: rows.length,
          loggedIn,
          warmed,
        })}
        right={
          <>
            <Button variant="ghost" onClick={importGeelark}>
              {t('accounts.importFromGeelark')}
            </Button>
            <Button variant="ghost" onClick={setPics}>
              {t('accounts.setPictures')}
            </Button>
            <Button variant="secondary" onClick={createProfiles} disabled={running}>
              {running ? t('accounts.creating') : t('accounts.createProfiles')}
            </Button>
            <Button variant="primary" onClick={() => setAddOpen(true)}>
              {t('accounts.addAccount')}
            </Button>
          </>
        }
      />

      {(running || log.length > 0) && (
        <section>
          <SectionTitle>{t('accounts.createProfilesLive')}</SectionTitle>
          <LogPanel lines={log} active={running} />
        </section>
      )}

      <section>
        <SectionTitle>{t('accounts.inventoryHeading')}</SectionTitle>
        <Table>
          <THead>
            <Tr className="hover:bg-transparent">
              <Th>{t('accounts.col.account')}</Th>
              <Th>{t('accounts.col.email')}</Th>
              <Th>{t('accounts.col.proxy')}</Th>
              <Th align="right">{t('accounts.col.maxWarm')}</Th>
              <Th align="center">{t('accounts.col.loggedIn')}</Th>
              <Th>{t('accounts.col.warmProgress')}</Th>
              <Th align="center">{t('accounts.col.lastWarm')}</Th>
              <Th align="right" className="w-[24px]" />
            </Tr>
          </THead>
          <TBody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={8}>
                  <EmptyState title={t('accounts.emptyTitle')}>
                    <Trans
                      i18nKey="accounts.emptyBody"
                      components={{
                        a: (
                          <span className="text-[var(--color-blue)] font-medium">
                            {t('accounts.addAccount')}
                          </span>
                        ),
                        b: (
                          <span className="text-[var(--color-blue)] font-medium">
                            {t('accounts.importFromGeelark')}
                          </span>
                        ),
                      }}
                    />
                  </EmptyState>
                </td>
              </tr>
            ) : (
              rows.map((r) => {
                const proxyShort = parseProxyHost(r.proxy)
                const proxyDebug = parseProxyDebug(r.proxy)
                return (
                  <Tr
                    key={r.id}
                    className="cursor-pointer group"
                    onClick={() => openDrawer(r)}
                  >
                    <Td>
                      <div className="flex items-center gap-3">
                        <Avatar
                          url={r.avatar_src}
                          name={r.profile_name}
                          size={32}
                        />
                        <div className="min-w-0">
                          <div className="text-[var(--color-ink)] font-medium truncate">
                            {r.profile_name}
                          </div>
                          <div className="text-[11.5px] text-[var(--color-ink-4)] truncate">
                            {r.geelark_phone_id
                              ? t('accounts.phoneLabel', { id: r.geelark_phone_id })
                              : t('accounts.noPhone')}
                          </div>
                        </div>
                      </div>
                    </Td>
                    <Td>
                      {r.email ? (
                        <span
                          title={r.email}
                          className="block truncate max-w-[220px]"
                        >
                          {r.email}
                        </span>
                      ) : (
                        <span className="text-[var(--color-ink-5)]">—</span>
                      )}
                    </Td>
                    <Td>
                      {proxyShort ? (
                        <span
                          title={proxyDebug ?? ''}
                          className="block truncate max-w-[180px] font-mono text-[12.5px] text-[var(--color-ink-2)]"
                        >
                          {proxyShort}
                        </span>
                      ) : (
                        <span className="text-[var(--color-ink-5)]">—</span>
                      )}
                    </Td>
                    <Td align="right" className="tabular-nums">
                      {r.max_warm_time ?? 500}
                    </Td>
                    <Td align="center">
                      <div
                        className="inline-flex justify-center w-full"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Toggle
                          checked={!!r.tiktok_logged_in}
                          ariaLabel={`${t('accounts.col.loggedIn')} ${r.profile_name}`}
                          onChange={() => api.loginToggle(r.id).then(load)}
                        />
                      </div>
                    </Td>
                    <Td>
                      {r.warmed_up ? (
                        <Badge tone="green">{t('accounts.warmedBadge')}</Badge>
                      ) : (
                        <ProgressBar
                          value={r.time_spent_warming}
                          total={r.max_warm_time || 500}
                        />
                      )}
                    </Td>
                    <Td align="center">
                      {r.last_warm_result === 'Success' ? (
                        <Badge tone="green">{t('accounts.warmOk')}</Badge>
                      ) : r.last_warm_result === 'Failed' ? (
                        <Badge tone="red">{t('accounts.warmFailed')}</Badge>
                      ) : (
                        <span className="text-[var(--color-ink-4)]">—</span>
                      )}
                    </Td>
                    <Td align="right">
                      <span className="text-[var(--color-ink-5)] opacity-0 group-hover:opacity-100 transition-opacity">
                        →
                      </span>
                    </Td>
                  </Tr>
                )
              })
            )}
          </TBody>
        </Table>
      </section>

      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title={t('accounts.modal.title')}
        submitLabel={t('accounts.modal.submit')}
        onSubmit={addAccount}
      >
        <p className="text-[13.5px] text-[var(--color-ink-2)] mb-4 leading-relaxed">
          {t('accounts.modal.body')}
        </p>
        <TextField
          label={t('accounts.modal.profileName')}
          value={newName}
          autoFocus
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t('accounts.modal.namePlaceholder')}
        />
      </Modal>

      <Drawer
        open={!!editing}
        onClose={requestClose}
        title={t('accounts.drawer.title')}
        subtitle={editing?.profile_name}
        footer={
          editing && form ? (
            <div className="flex justify-between items-center">
              <Button
                variant="ghost"
                className="text-[var(--color-red)] hover:bg-[var(--color-red-bg)] hover:text-[var(--color-red)]"
                disabled={saving || uploading}
                onClick={deleteFromDrawer}
              >
                {t('accounts.drawer.deleteAccount')}
              </Button>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={requestClose}>
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="primary"
                  disabled={saving || !isDirty()}
                  onClick={saveDrawer}
                >
                  {saving ? t('common.loading') : t('accounts.drawer.saveChanges')}
                </Button>
              </div>
            </div>
          ) : null
        }
      >
        {form ? (
          <div className="flex flex-col gap-6">
            <section>
              <SectionTitle>{t('accounts.drawer.identity')}</SectionTitle>
              <div className="flex flex-col gap-3">
                <TextField
                  label={t('accounts.drawer.identityName')}
                  value={form.profile_name}
                  onChange={(e) =>
                    setForm({ ...form, profile_name: e.target.value })
                  }
                />
                <div>
                  <span className="block text-[12.5px] font-medium text-[var(--color-ink-2)] mb-1.5">
                    {t('accounts.drawer.identityPhone')}
                  </span>
                  <span className="text-[13.5px] text-[var(--color-ink-3)] font-mono">
                    {editing?.geelark_phone_id ??
                      t('accounts.drawer.identityNoPhone')}
                  </span>
                </div>
              </div>
            </section>

            <section>
              <SectionTitle>{t('accounts.drawer.avatar')}</SectionTitle>
              <div className="flex items-center gap-4">
                <Avatar
                  url={editing?.avatar_src ?? null}
                  name={form.profile_name || '?'}
                  size={80}
                />
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-3">
                    <Button
                      variant="secondary"
                      disabled={uploading}
                      onClick={pickFile}
                    >
                      {uploading
                        ? t('accounts.drawer.uploading')
                        : t('accounts.drawer.uploadImage')}
                    </Button>
                    {editing?.avatar_src ? (
                      <button
                        type="button"
                        onClick={removeAvatar}
                        disabled={uploading}
                        className="text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)] disabled:opacity-50"
                      >
                        {t('accounts.drawer.removeImage')}
                      </button>
                    ) : null}
                  </div>
                  <span className="text-[12px] text-[var(--color-ink-4)]">
                    {t('accounts.drawer.avatarHint')}
                  </span>
                </div>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="hidden"
                onChange={onFileChosen}
              />
            </section>

            <section>
              <SectionTitle>{t('accounts.drawer.credentials')}</SectionTitle>
              <div className="flex flex-col gap-3">
                <TextField
                  label={t('accounts.drawer.email')}
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="user@example.com"
                />
                <TextField
                  label={t('accounts.drawer.proxy')}
                  value={form.proxy}
                  onChange={(e) => setForm({ ...form, proxy: e.target.value })}
                  placeholder={t('accounts.drawer.proxyPlaceholder')}
                  className="font-mono"
                />
              </div>
            </section>

            <section>
              <SectionTitle>{t('accounts.drawer.warmup')}</SectionTitle>
              <TextField
                label={t('accounts.drawer.maxWarm')}
                type="number"
                value={form.max_warm_time}
                onChange={(e) =>
                  setForm({ ...form, max_warm_time: e.target.value })
                }
              />
            </section>
          </div>
        ) : null}
      </Drawer>
    </div>
  )
}
```

- [ ] **Step 5: Build to confirm TypeScript is clean**

```bash
cd /Users/Zhuanz/Documents/mkt/frontend && npm run build
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 6: Sanity-check pytest still passes (no backend regression)**

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q
```

Expected: 118 passed (same as Task 5).

- [ ] **Step 7: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add frontend/src/api.ts frontend/src/pages/Accounts.tsx \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat: drawer avatar upload replaces URL paste"
```

---

## Task 7: Manual QA pass

**Files:** none modified unless QA surfaces a fix.

- [ ] **Step 1: Start the app**

```bash
cd /Users/Zhuanz/Documents/mkt && ./run.sh
```

Wait for `Uvicorn running on http://127.0.0.1:8080`. Open the URL in a browser and sign in.

- [ ] **Step 2: Walk through every item from the spec's manual QA checklist**

(From [the spec](../specs/2026-05-23-avatar-upload-design.md), "Testing strategy" → "Manual QA checklist".)

1. Open an account drawer. Click **Upload image**, pick a JPG ≤ 5 MB → 80px preview replaces within ~1 s; green "Avatar updated" toast; main-table row's 32px avatar updates after table reloads.
2. Click **Upload image**, pick a >5 MB file → red "Image too large (max 5 MB)" toast; no upload network call.
3. Click **Upload image**, pick a `.gif` (or `.txt` renamed) → red "Unsupported image format" toast.
4. Upload, then click **Remove** → both drawer preview and main-table avatar revert to monogram fallback.
5. Upload, then close the drawer without clicking **Save changes** → avatar persists (upload is immediate, not transactional with text-field Save).
6. Delete an account that has an uploaded avatar → file at `data/avatars/{id}.*` is gone.
7. Toggle the sidebar language to 中文 → every drawer/toast string for the avatar flow renders in Chinese.

Record each result. Any failure → fix in a follow-up commit before merging.

- [ ] **Step 3: Final commit (only if QA surfaced fixes)**

If the manual pass found adjustments, apply them and commit:

```bash
cd /Users/Zhuanz/Documents/mkt
git add -A
git commit -m "fix: avatar upload — visual QA adjustments"
```

Otherwise this task is complete at the end of Step 2 with no additional commit.

---

## Self-review notes

**Spec coverage:**

- Schema (`profile_pic_path` column) + migration → Task 1.
- `AVATAR_DIR` config + mkdir at startup → Task 1. (`mkdir(parents=True, exist_ok=True)` runs at import time as a side-effect of `app/config.py`, the same way `UPLOAD_DIR` already does — `app/main.py` does not need any additional code.)
- `with_avatar_src` helper applied to `list_all` and `get` → Task 1.
- POST / GET / DELETE avatar endpoints → Tasks 2 / 3 / 4.
- Account-delete file sweep → Task 4.
- `set_pics` dispatch from local file via `client.upload_file` → Task 5.
- Legacy `profile_pic_url`-only accounts keep working → Task 5 fallback branch covered by `test_set_pics_url_only_account_still_dispatches`.
- Drawer rewrite (no more URL TextField, upload button + remove link + preview) → Task 6.
- `Account` type and `EditForm` type updates → Task 6.
- New `uploadAvatar` / `deleteAvatar` api helpers → Task 6.
- i18n key add/remove → Task 6.
- Manual QA → Task 7.

**Type consistency:**

- `with_avatar_src(row: dict) -> dict` — definition and call sites both in `app/repositories/accounts.py` (Task 1). API responses pick it up automatically because they go through `acc_repo.list_all` / `acc_repo.get`.
- `Account.avatar_src: string | null` — declared in Task 6, used at both `<Avatar>` call sites in the same file.
- `EditForm` shape — dropped `profile_pic_url`; consistent across `toForm`, `formsEqual`, and `saveDrawer` (Task 6).
- `api.uploadAvatar(id: number, file: File)` and `api.deleteAvatar(id: number)` — declared in Task 6 step 1, consumed in `onFileChosen` and `removeAvatar` in step 4.

**Placeholder scan:** No `TBD`, `TODO`, or "similar to Task N" — every step lists the literal code to apply.
