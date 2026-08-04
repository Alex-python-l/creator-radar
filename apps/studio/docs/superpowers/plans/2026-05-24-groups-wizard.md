# Groups Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end "new group" workflow — proxies → phones → accounts → login → warmup → publish — wrapped in a 4-step wizard, backed by a first-class `groups` entity.

**Architecture:** Add a `groups` table (and `group_id` FK on `accounts`/`videos`). Existing services (`warmup`, `posting`) gain an optional `group_id` filter without breaking today's behavior. New services (`credentials_parser`, `proxy_intake`, `groups_service`, `geelark_service.create_profiles_for_group`) cover the new capabilities. Frontend adds a Groups tab with a list page, detail page, and 4-step wizard modal.

**Tech Stack:** FastAPI + SQLite (WAL, single process), APScheduler, React 19 + Vite, existing `geelark_client.py`, existing `proxy_check_service` (probe).

**Reference spec:** `docs/superpowers/specs/2026-05-24-groups-wizard-design.md`

---

## File map

**New backend files**
- `app/repositories/groups.py` — CRUD + counts aggregation
- `app/services/groups_service.py` — thin orchestration over repo
- `app/services/proxy_intake.py` — parse + batch-check
- `app/services/credentials_parser.py` — per-line field-by-shape parser
- `app/api/groups.py` — all `/api/groups/*` routes + `bulk-mark-logged-in`

**Modified backend files**
- `app/db.py` — migration: groups table + new columns + backfill from `group_name`
- `app/repositories/accounts.py` — extend `_COLUMNS` with new fields; add `list_by_group`, `bulk_set_logged_in`, `count_for_group`
- `app/repositories/videos.py` — accept `group_id` in `create`; add `count_for_group`
- `app/services/geelark_service.py` — add `create_profiles_for_group`
- `app/services/warmup_service.py` — accept `group_id` kwarg
- `app/services/posting_service.py` — accept `group_id` kwarg; filter videos + pool
- `app/api/accounts.py` — wire `bulk-mark-logged-in` (forward to groups module not needed; put in accounts API since it operates on accounts)
- `app/api/videos.py` — accept `group_id` on upload + drive import
- `app/main.py` — mount `groups` router

**New test files**
- `tests/test_groups_repository.py`
- `tests/test_groups_service.py`
- `tests/test_credentials_parser.py`
- `tests/test_proxy_intake.py`
- `tests/test_geelark_service_create_for_group.py`

**Extended test files**
- `tests/test_db.py` — verify migration adds new columns & groups table
- `tests/test_repositories.py` — `bulk_set_logged_in`, `list_by_group`
- `tests/test_services.py` — `run_warmup(group_id=...)`, `publish(group_id=...)`
- `tests/test_api.py` — new endpoints

**New frontend files**
- `frontend/src/pages/Groups.tsx`
- `frontend/src/pages/GroupDetail.tsx`
- `frontend/src/components/wizard/GroupWizard.tsx`
- `frontend/src/components/wizard/Step1Proxies.tsx`
- `frontend/src/components/wizard/Step2Phones.tsx`
- `frontend/src/components/wizard/Step3Accounts.tsx`
- `frontend/src/components/wizard/Step4Login.tsx`

**Modified frontend files**
- `frontend/src/App.tsx` — add Groups tab + route
- `frontend/src/pages/Accounts.tsx` — group filter chips
- `frontend/src/pages/Videos.tsx` — group filter chips + required group on upload
- `frontend/src/i18n/locales/en.json` + `zh.json` — new strings

---

## Task 1: DB migration — groups table + new columns

**Files:**
- Modify: `app/db.py:70-100` (the `_migrate` function and `SCHEMA`)
- Test: `tests/test_db.py` (extend)

- [ ] **Step 1.1: Write the failing migration test**

Append to `tests/test_db.py`:

```python
def test_migrate_creates_groups_table_and_columns(db):
    cols_accounts = {r["name"] for r in db.execute("PRAGMA table_info(accounts)")}
    cols_videos = {r["name"] for r in db.execute("PRAGMA table_info(videos)")}
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    assert "groups" in tables
    for col in ("group_id", "password", "tiktok_password",
                "recovery_email", "mail_inbox_url", "raw_credentials"):
        assert col in cols_accounts, f"missing accounts.{col}"
    assert "group_id" in cols_videos


def test_migrate_backfills_group_id_from_group_name(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "bf.db")
    from app.db import get_connection, init_schema
    conn = get_connection()
    # Bootstrap schema once (creates groups table) but then insert account
    # with the legacy group_name set and group_id NULL.
    init_schema(conn)
    conn.execute("INSERT INTO accounts (profile_name, group_name) "
                 "VALUES ('M1', 'project-jp')")
    conn.execute("INSERT INTO accounts (profile_name, group_name) "
                 "VALUES ('M2', 'project-jp')")
    conn.execute("UPDATE accounts SET group_id=NULL")
    # Re-run migration: should backfill.
    from app.db import _migrate
    _migrate(conn)
    rows = conn.execute(
        "SELECT a.profile_name, g.name FROM accounts a "
        "LEFT JOIN groups g ON a.group_id = g.id "
        "ORDER BY a.id").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("M1", "project-jp"),
                                             ("M2", "project-jp")]
    conn.close()
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_db.py -v
```

Expected: `test_migrate_creates_groups_table_and_columns` and `test_migrate_backfills_group_id_from_group_name` FAIL (table/columns missing).

- [ ] **Step 1.3: Add groups CREATE to SCHEMA + new ALTERs to `_migrate`**

In `app/db.py`, append to `SCHEMA` (inside the triple-quoted string, before the final `"""`):

```sql
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  target_phone_count INTEGER,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  archived_at TEXT
);
```

Replace the `_migrate` body with one that adds every new column + does the backfill. Full new function:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for databases created before a column existed.

    ALTER TABLE is DDL and autocommits in SQLite; init_schema's later commit
    covers the settings rows.
    """
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "drive_file_id" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN drive_file_id TEXT")
    if "group_id" not in vcols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN group_id INTEGER "
            "REFERENCES groups(id) ON DELETE SET NULL")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    if "profile_pic_path" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN profile_pic_path TEXT")
    if "proxy_status" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN proxy_status TEXT")
    if "proxy_checked_at" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN proxy_checked_at TEXT")
    if "proxy_exit_ip" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN proxy_exit_ip TEXT")
    if "tiktok_username" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN tiktok_username TEXT")
    # Groups-wizard additions (2026-05-24 spec).
    if "group_id" not in acols:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN group_id INTEGER "
            "REFERENCES groups(id) ON DELETE SET NULL")
    for col in ("password", "tiktok_password", "recovery_email",
                "mail_inbox_url", "raw_credentials"):
        if col not in acols:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} TEXT")
    # One-time backfill: lift each distinct accounts.group_name into a
    # groups row, then point accounts.group_id at it. Safe to re-run because
    # INSERT OR IGNORE is no-op on the unique name, and rows that already
    # have group_id set are left alone by the WHERE clause.
    if "group_name" in acols:
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT group_name FROM accounts "
            "WHERE group_name IS NOT NULL AND group_name != '' "
            "AND group_id IS NULL")]
        for n in names:
            conn.execute("INSERT OR IGNORE INTO groups(name) VALUES (?)", (n,))
        conn.execute(
            "UPDATE accounts SET group_id = "
            "(SELECT id FROM groups WHERE groups.name = accounts.group_name) "
            "WHERE group_id IS NULL AND group_name IS NOT NULL "
            "AND group_name != ''")
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_db.py -v
```

Expected: both new tests PASS, all existing tests still pass.

- [ ] **Step 1.5: Run full suite to confirm no regressions**

```bash
./.venv/bin/pytest -q
```

Expected: at least 38 passed (existing baseline) + 2 new = 40+ passed.

- [ ] **Step 1.6: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat(db): add groups table + per-account credential columns + backfill"
```

---

## Task 2: accounts repo — recognize new columns + helpers

**Files:**
- Modify: `app/repositories/accounts.py:4-11` (`_COLUMNS` set)
- Modify: `app/repositories/accounts.py` (add `list_by_group`, `bulk_set_logged_in`, `count_for_group`)
- Test: `tests/test_repositories.py` (extend)

- [ ] **Step 2.1: Write failing tests for the new helpers**

Append to `tests/test_repositories.py`:

```python
def test_accounts_columns_include_new_credential_fields(db):
    from app.repositories import accounts as ar
    aid = ar.create(db, {"profile_name": "M1", "password": "pw",
                          "tiktok_password": "tkpw", "recovery_email": "r@x.io",
                          "mail_inbox_url": "https://inbox.example/me",
                          "raw_credentials": "raw line",
                          "group_id": None})
    row = ar.get(db, aid)
    assert row["password"] == "pw"
    assert row["tiktok_password"] == "tkpw"
    assert row["recovery_email"] == "r@x.io"
    assert row["mail_inbox_url"] == "https://inbox.example/me"
    assert row["raw_credentials"] == "raw line"


def test_list_by_group_returns_only_that_group(db):
    from app.repositories import accounts as ar
    db.execute("INSERT INTO groups(name) VALUES ('g1'), ('g2')")
    a1 = ar.create(db, {"profile_name": "P1", "group_id": 1})
    a2 = ar.create(db, {"profile_name": "P2", "group_id": 1})
    ar.create(db, {"profile_name": "P3", "group_id": 2})
    rows = ar.list_by_group(db, 1)
    assert {r["id"] for r in rows} == {a1, a2}


def test_bulk_set_logged_in_only_updates_named_ids(db):
    from app.repositories import accounts as ar
    a1 = ar.create(db, {"profile_name": "X1"})
    a2 = ar.create(db, {"profile_name": "X2"})
    a3 = ar.create(db, {"profile_name": "X3"})
    n = ar.bulk_set_logged_in(db, [a1, a3])
    assert n == 2
    assert ar.get(db, a1)["tiktok_logged_in"] == 1
    assert ar.get(db, a2)["tiktok_logged_in"] == 0
    assert ar.get(db, a3)["tiktok_logged_in"] == 1


def test_bulk_set_logged_in_empty_list_is_noop(db):
    from app.repositories import accounts as ar
    a1 = ar.create(db, {"profile_name": "Y1"})
    assert ar.bulk_set_logged_in(db, []) == 0
    assert ar.get(db, a1)["tiktok_logged_in"] == 0


def test_count_for_group_aggregates_states(db):
    from app.repositories import accounts as ar
    db.execute("INSERT INTO groups(name) VALUES ('g1')")
    ar.create(db, {"profile_name": "P1", "group_id": 1,
                    "geelark_phone_id": "ph1",
                    "email": "a@b", "tiktok_logged_in": 1, "warmed_up": 1})
    ar.create(db, {"profile_name": "P2", "group_id": 1,
                    "geelark_phone_id": "ph2",
                    "email": "c@d", "tiktok_logged_in": 1})
    ar.create(db, {"profile_name": "P3", "group_id": 1,
                    "geelark_phone_id": "ph3"})
    c = ar.count_for_group(db, 1)
    assert c == {"phones": 3, "assigned": 2, "logged_in": 2, "warmed": 1}
```

- [ ] **Step 2.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_repositories.py -v -k "new_credential or by_group or bulk_set or count_for"
```

Expected: FAIL (`list_by_group`, `bulk_set_logged_in`, `count_for_group` don't exist; `password`/etc. silently dropped by `_filter`).

- [ ] **Step 2.3: Extend `_COLUMNS` + add helpers**

In `app/repositories/accounts.py`, replace the `_COLUMNS` literal (lines 4-11) with:

```python
_COLUMNS = {
    "geelark_phone_id", "profile_name", "email", "tiktok_username", "proxy",
    "profile_pic_url", "profile_pic_path",
    "group_name", "group_id", "tiktok_logged_in", "pic_status", "pic_task_id",
    "warmed_up", "time_spent_warming", "max_warm_time", "last_warm_at",
    "last_warm_result", "last_warm_notes", "last_posted_at",
    "proxy_status", "proxy_checked_at", "proxy_exit_ip",
    "password", "tiktok_password", "recovery_email", "mail_inbox_url",
    "raw_credentials",
}
```

Append three new functions at the bottom of `app/repositories/accounts.py`:

```python
def list_by_group(conn: sqlite3.Connection, group_id: int) -> list[dict]:
    return [with_avatar_src(dict(r)) for r in conn.execute(
        "SELECT * FROM accounts WHERE group_id=? ORDER BY id", (group_id,))]


def bulk_set_logged_in(conn: sqlite3.Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE accounts SET tiktok_logged_in=1, "
        f"updated_at=datetime('now') WHERE id IN ({ph})", ids)
    conn.commit()
    return len(ids)


def count_for_group(conn: sqlite3.Connection, group_id: int) -> dict:
    """{phones, assigned, logged_in, warmed} for one group.

    `assigned` = has email set (i.e. credentials parsed in and applied).
    """
    row = conn.execute(
        "SELECT "
        " COUNT(*) AS phones, "
        " SUM(CASE WHEN email IS NOT NULL AND email != '' THEN 1 ELSE 0 END) AS assigned, "
        " SUM(CASE WHEN tiktok_logged_in=1 THEN 1 ELSE 0 END) AS logged_in, "
        " SUM(CASE WHEN warmed_up=1 THEN 1 ELSE 0 END) AS warmed "
        "FROM accounts WHERE group_id=?", (group_id,)).fetchone()
    return {"phones": row["phones"] or 0,
            "assigned": row["assigned"] or 0,
            "logged_in": row["logged_in"] or 0,
            "warmed": row["warmed"] or 0}
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_repositories.py -v
```

Expected: PASS.

- [ ] **Step 2.5: Commit**

```bash
git add app/repositories/accounts.py tests/test_repositories.py
git commit -m "feat(accounts repo): credential columns + list_by_group/bulk_set_logged_in/count_for_group"
```

---

## Task 3: videos repo — accept group_id; add count_for_group

**Files:**
- Modify: `app/repositories/videos.py:3-9` (`create` signature)
- Modify: `app/repositories/videos.py` (append `count_for_group`)
- Test: `tests/test_repositories.py` (extend)

- [ ] **Step 3.1: Write failing test**

Append to `tests/test_repositories.py`:

```python
def test_videos_create_accepts_group_id(db):
    from app.repositories import videos as vr
    db.execute("INSERT INTO groups(name) VALUES ('g1')")
    vid = vr.create(db, "v.mp4", "/tmp/v.mp4", group_id=1)
    row = vr.get(db, vid)
    assert row["group_id"] == 1


def test_videos_count_for_group(db):
    from app.repositories import videos as vr
    db.execute("INSERT INTO groups(name) VALUES ('g1')")
    vid_a = vr.create(db, "a.mp4", "/tmp/a.mp4", group_id=1)
    vid_b = vr.create(db, "b.mp4", "/tmp/b.mp4", group_id=1)
    vr.create(db, "c.mp4", "/tmp/c.mp4", group_id=1)
    vr.set_result(db, vid_a, "SUCCESS", "ok")
    vr.set_result(db, vid_b, "FAILURE", "err")
    c = vr.count_for_group(db, 1)
    assert c == {"published": 1, "pending": 1, "failed": 1}
```

- [ ] **Step 3.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_repositories.py -v -k "videos_create_accepts_group_id or videos_count_for_group"
```

Expected: FAIL.

- [ ] **Step 3.3: Extend `create` + add `count_for_group`**

Replace the existing `create` in `app/repositories/videos.py:3-9` with:

```python
def create(conn: sqlite3.Connection, filename: str, stored_path: str,
           drive_file_id: str | None = None,
           group_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO videos (original_filename, stored_path, result, "
        "drive_file_id, group_id) VALUES (?, ?, 'PENDING', ?, ?)",
        (filename, stored_path, drive_file_id, group_id))
    conn.commit()
    return cur.lastrowid
```

Append to the same file:

```python
def count_for_group(conn: sqlite3.Connection, group_id: int) -> dict:
    """{published (SUCCESS), pending (PENDING+INITIATED), failed (FAILURE)}."""
    row = conn.execute(
        "SELECT "
        " SUM(CASE WHEN result='SUCCESS' THEN 1 ELSE 0 END) AS published, "
        " SUM(CASE WHEN result IN ('PENDING','INITIATED') THEN 1 ELSE 0 END) AS pending, "
        " SUM(CASE WHEN result='FAILURE' THEN 1 ELSE 0 END) AS failed "
        "FROM videos WHERE group_id=?", (group_id,)).fetchone()
    return {"published": row["published"] or 0,
            "pending": row["pending"] or 0,
            "failed": row["failed"] or 0}
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_repositories.py -v
```

Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories.py
git commit -m "feat(videos repo): group_id on create + count_for_group"
```

---

## Task 4: groups repository

**Files:**
- Create: `app/repositories/groups.py`
- Test: `tests/test_groups_repository.py`

- [ ] **Step 4.1: Write failing tests**

Create `tests/test_groups_repository.py`:

```python
import pytest


def test_create_returns_id_and_persists_fields(db):
    from app.repositories import groups as gr
    gid = gr.create(db, name="project-jp",
                    target_phone_count=10, notes="JP batch")
    row = gr.get(db, gid)
    assert row["name"] == "project-jp"
    assert row["target_phone_count"] == 10
    assert row["notes"] == "JP batch"
    assert row["archived_at"] is None


def test_create_duplicate_name_raises(db):
    from app.repositories import groups as gr
    gr.create(db, name="dup")
    with pytest.raises(Exception):
        gr.create(db, name="dup")


def test_list_all_excludes_archived_by_default(db):
    from app.repositories import groups as gr
    a = gr.create(db, name="a")
    b = gr.create(db, name="b")
    gr.archive(db, b)
    names = [r["name"] for r in gr.list_all(db)]
    assert names == ["a"]
    all_names = [r["name"] for r in gr.list_all(db, include_archived=True)]
    assert set(all_names) == {"a", "b"}


def test_archive_sets_timestamp(db):
    from app.repositories import groups as gr
    gid = gr.create(db, name="x")
    gr.archive(db, gid)
    assert gr.get(db, gid)["archived_at"] is not None


def test_get_by_name(db):
    from app.repositories import groups as gr
    gid = gr.create(db, name="lookup-me")
    row = gr.get_by_name(db, "lookup-me")
    assert row is not None and row["id"] == gid
    assert gr.get_by_name(db, "nope") is None
```

- [ ] **Step 4.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_groups_repository.py -v
```

Expected: FAIL with ImportError.

- [ ] **Step 4.3: Implement `app/repositories/groups.py`**

Create `app/repositories/groups.py`:

```python
import sqlite3


def create(conn: sqlite3.Connection, name: str,
           target_phone_count: int | None = None,
           notes: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO groups (name, target_phone_count, notes) "
        "VALUES (?, ?, ?)", (name, target_phone_count, notes))
    conn.commit()
    return cur.lastrowid


def get(conn: sqlite3.Connection, group_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    return dict(r) if r else None


def get_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    r = conn.execute("SELECT * FROM groups WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def list_all(conn: sqlite3.Connection,
             include_archived: bool = False) -> list[dict]:
    if include_archived:
        rows = conn.execute("SELECT * FROM groups ORDER BY id DESC")
    else:
        rows = conn.execute(
            "SELECT * FROM groups WHERE archived_at IS NULL ORDER BY id DESC")
    return [dict(r) for r in rows]


def archive(conn: sqlite3.Connection, group_id: int) -> None:
    conn.execute(
        "UPDATE groups SET archived_at=datetime('now') WHERE id=?",
        (group_id,))
    conn.commit()
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_groups_repository.py -v
```

Expected: PASS.

- [ ] **Step 4.5: Commit**

```bash
git add app/repositories/groups.py tests/test_groups_repository.py
git commit -m "feat(groups repo): create/get/list/archive"
```

---

## Task 5: groups_service (counts orchestrator)

**Files:**
- Create: `app/services/groups_service.py`
- Test: `tests/test_groups_service.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/test_groups_service.py`:

```python
def test_counts_for_combines_accounts_and_videos(db):
    from app.services import groups_service as gs
    from app.repositories import accounts as ar
    from app.repositories import videos as vr

    db.execute("INSERT INTO groups(name) VALUES ('g1')")
    ar.create(db, {"profile_name": "P1", "group_id": 1,
                    "email": "a@b", "tiktok_logged_in": 1, "warmed_up": 1})
    ar.create(db, {"profile_name": "P2", "group_id": 1,
                    "email": "c@d", "tiktok_logged_in": 1})
    ar.create(db, {"profile_name": "P3", "group_id": 1})
    vid = vr.create(db, "v.mp4", "/tmp/v.mp4", group_id=1)
    vr.set_result(db, vid, "SUCCESS", "ok")
    vr.create(db, "p.mp4", "/tmp/p.mp4", group_id=1)

    c = gs.counts_for(db, 1)
    assert c == {"phones": 3, "assigned": 2, "logged_in": 2, "warmed": 1,
                  "published": 1, "pending": 1, "failed": 0}


def test_list_all_attaches_counts(db):
    from app.services import groups_service as gs
    from app.repositories import accounts as ar
    gid = gs.create(db, name="g1", target_phone_count=2)
    ar.create(db, {"profile_name": "p", "group_id": gid})
    rows = gs.list_all(db)
    assert len(rows) == 1
    assert rows[0]["name"] == "g1"
    assert rows[0]["counts"]["phones"] == 1


def test_create_passes_through_to_repo(db):
    from app.services import groups_service as gs
    from app.repositories import groups as gr
    gid = gs.create(db, name="x", target_phone_count=5, notes="note")
    row = gr.get(db, gid)
    assert row["target_phone_count"] == 5
    assert row["notes"] == "note"
```

- [ ] **Step 5.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_groups_service.py -v
```

Expected: FAIL with ImportError.

- [ ] **Step 5.3: Implement `app/services/groups_service.py`**

Create `app/services/groups_service.py`:

```python
"""Thin orchestration over the groups repository.

`counts_for` is the single source of truth for per-group progress (we never
store a state machine; progress is always aggregated from accounts/videos)."""
import sqlite3
from app.repositories import accounts as acc_repo
from app.repositories import videos as vid_repo
from app.repositories import groups as grp_repo


def create(conn: sqlite3.Connection, name: str,
           target_phone_count: int | None = None,
           notes: str | None = None) -> int:
    return grp_repo.create(conn, name=name,
                            target_phone_count=target_phone_count,
                            notes=notes)


def get(conn: sqlite3.Connection, group_id: int) -> dict | None:
    row = grp_repo.get(conn, group_id)
    if row is None:
        return None
    row["counts"] = counts_for(conn, group_id)
    return row


def list_all(conn: sqlite3.Connection,
             include_archived: bool = False) -> list[dict]:
    rows = grp_repo.list_all(conn, include_archived=include_archived)
    for r in rows:
        r["counts"] = counts_for(conn, r["id"])
    return rows


def archive(conn: sqlite3.Connection, group_id: int) -> None:
    grp_repo.archive(conn, group_id)


def counts_for(conn: sqlite3.Connection, group_id: int) -> dict:
    acc = acc_repo.count_for_group(conn, group_id)
    vid = vid_repo.count_for_group(conn, group_id)
    return {**acc, **vid}
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_groups_service.py -v
```

Expected: PASS.

- [ ] **Step 5.5: Commit**

```bash
git add app/services/groups_service.py tests/test_groups_service.py
git commit -m "feat(groups service): create/list/archive + counts aggregation"
```

---

## Task 6: credentials_parser

**Files:**
- Create: `app/services/credentials_parser.py`
- Test: `tests/test_credentials_parser.py`

- [ ] **Step 6.1: Write failing tests covering each rule**

Create `tests/test_credentials_parser.py`:

```python
import pytest
from app.services import credentials_parser as cp


def _one(text: str) -> dict:
    out = cp.parse_lines(text)
    assert len(out) == 1
    return out[0]


def test_email_password_in_order():
    r = _one("alice@example.com  hunter2")
    assert r["email"] == "alice@example.com"
    assert r["password"] == "hunter2"
    assert r["tiktok_username"] is None
    assert r["warnings"] == []
    assert r["raw"] == "alice@example.com  hunter2"


def test_email_password_reversed():
    r = _one("hunter2 alice@example.com")
    assert r["email"] == "alice@example.com"
    assert r["password"] == "hunter2"


def test_full_record_random_order():
    r = _one("hunter2 @alice_tk tkpw alice@example.com r@y.io "
              "https://inbox.example/alice")
    assert r["email"] == "alice@example.com"
    assert r["password"] == "hunter2"
    assert r["tiktok_username"] == "@alice_tk"
    assert r["tiktok_password"] == "tkpw"
    assert r["recovery_email"] == "r@y.io"
    assert r["mail_inbox_url"] == "https://inbox.example/alice"


def test_totp_code_ignored():
    r = _one("alice@example.com hunter2 123456")
    # 6-digit code dropped, not stored as tiktok_password
    assert r["tiktok_password"] is None


def test_missing_fields_only_email_password():
    r = _one("alice@example.com hunter2")
    assert r["tiktok_username"] is None
    assert r["recovery_email"] is None
    assert r["mail_inbox_url"] is None


def test_warning_for_extra_unclassified_tokens():
    r = _one("alice@example.com hunter2 tkpw extra_token1 extra_token2")
    # First non-email -> password, second -> tiktok_password,
    # third+ generate warnings.
    assert r["password"] == "hunter2"
    assert r["tiktok_password"] == "tkpw"
    assert any("extra" in w for w in r["warnings"])


def test_does_not_guess_username_from_leftover():
    # Spec rule: parser does NOT promote a leftover to tiktok_username.
    r = _one("alice@example.com hunter2 maybeusername")
    assert r["tiktok_username"] is None
    assert r["tiktok_password"] == "maybeusername"


def test_separator_variants():
    out = cp.parse_lines(
        "a@x.com,pw\n"
        "b@x.com;pw\n"
        "c@x.com|pw\n"
        "d@x.com\tpw")
    assert [r["email"] for r in out] == [
        "a@x.com", "b@x.com", "c@x.com", "d@x.com"]
    assert all(r["password"] == "pw" for r in out)


def test_blank_lines_skipped():
    out = cp.parse_lines("\n\nalice@x.com pw\n\n")
    assert len(out) == 1


def test_raw_preserved_on_parse_failure():
    r = _one("totally garbage 4242 4242 1234 5678")
    assert r["email"] is None
    assert r["raw"] == "totally garbage 4242 4242 1234 5678"
    assert r["warnings"]  # non-empty


def test_two_emails_second_is_recovery():
    r = _one("alice@x.com hunter2 backup@y.io")
    assert r["email"] == "alice@x.com"
    assert r["recovery_email"] == "backup@y.io"
```

- [ ] **Step 6.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_credentials_parser.py -v
```

Expected: FAIL with ImportError.

- [ ] **Step 6.3: Implement `app/services/credentials_parser.py`**

Create `app/services/credentials_parser.py`:

```python
"""Per-line credential parser. Field order is not fixed — classify each
non-empty token by *shape*, not by position.

Field-by-shape rules (spec 2026-05-24 §"Credentials parser rules"):
  email regex     -> first match: email; second: recovery_email
  starts with @   -> tiktok_username
  starts with URL -> mail_inbox_url
  6-8 digits      -> drop (likely a 2FA code)
  anything else   -> first: password; second: tiktok_password; rest -> warnings

The parser never *guesses* tiktok_username from a leftover token (too easily
confused with a password). If no '@' handle is in the line, tiktok_username
stays None and the UI lets the user fill it in.
"""
from __future__ import annotations
import re
from typing import TypedDict


class ParsedAccount(TypedDict):
    email: str | None
    password: str | None
    tiktok_username: str | None
    tiktok_password: str | None
    recovery_email: str | None
    mail_inbox_url: str | None
    raw: str
    warnings: list[str]


_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}$")
_HANDLE_RE = re.compile(r"^@\w{3,}$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_TOTP_RE = re.compile(r"^\d{6,8}$")
_SEP_RE = re.compile(r"[\s,;|\t]+")


def _empty() -> ParsedAccount:
    return {"email": None, "password": None, "tiktok_username": None,
            "tiktok_password": None, "recovery_email": None,
            "mail_inbox_url": None, "raw": "", "warnings": []}


def parse_lines(text: str) -> list[ParsedAccount]:
    out: list[ParsedAccount] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        out.append(_parse_one(stripped))
    return out


def _parse_one(line: str) -> ParsedAccount:
    rec = _empty()
    rec["raw"] = line
    tokens = [t for t in _SEP_RE.split(line) if t]
    extras: list[str] = []
    for tok in tokens:
        if _EMAIL_RE.match(tok):
            if rec["email"] is None:
                rec["email"] = tok
            elif rec["recovery_email"] is None:
                rec["recovery_email"] = tok
            else:
                extras.append(tok)
            continue
        if _URL_RE.match(tok):
            if rec["mail_inbox_url"] is None:
                rec["mail_inbox_url"] = tok
            else:
                extras.append(tok)
            continue
        if _HANDLE_RE.match(tok):
            if rec["tiktok_username"] is None:
                rec["tiktok_username"] = tok
            else:
                extras.append(tok)
            continue
        if _TOTP_RE.match(tok):
            # Probably a one-time code, not a secret. Drop.
            continue
        # Anything else: secret-shaped.
        if rec["password"] is None:
            rec["password"] = tok
        elif rec["tiktok_password"] is None:
            rec["tiktok_password"] = tok
        else:
            extras.append(tok)
    if extras:
        rec["warnings"].append(
            f"unrecognized extra tokens: {', '.join(extras)}")
    if rec["email"] is None and rec["password"] is None:
        rec["warnings"].append("no email or password recognized")
    return rec
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_credentials_parser.py -v
```

Expected: PASS.

- [ ] **Step 6.5: Commit**

```bash
git add app/services/credentials_parser.py tests/test_credentials_parser.py
git commit -m "feat(credentials parser): field-by-shape per-line parser"
```

---

## Task 7: proxy_intake

**Files:**
- Create: `app/services/proxy_intake.py`
- Test: `tests/test_proxy_intake.py`

- [ ] **Step 7.1: Write failing tests**

Create `tests/test_proxy_intake.py`:

```python
import pytest


def test_parse_lines_valid_url():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("socks5://u:p@1.2.3.4:1080")
    assert out == [{"line_no": 1, "proxy": "socks5://u:p@1.2.3.4:1080",
                    "error": None}]


def test_parse_lines_blank_lines_skipped():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("\n\nhttp://1.1.1.1:8080\n\n")
    assert len(out) == 1
    assert out[0]["line_no"] == 3


def test_parse_lines_invalid_url_returns_error_not_raises():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("not-a-url")
    assert out[0]["proxy"] == "not-a-url"
    assert out[0]["error"] is not None


def test_parse_lines_accepts_no_userinfo():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("socks5://10.0.0.1:1080")
    assert out[0]["error"] is None


def test_check_batch_calls_check_one_per_proxy():
    from app.services import proxy_intake as pi
    calls = []

    def fake_check(proxy):
        calls.append(proxy)
        return {"ok": True, "exit_ip": "9.9.9.9",
                "latency_ms": 100, "error": None}

    out = pi.check_batch(["socks5://a:1080", "socks5://b:1080"],
                          check_one=fake_check)
    assert len(out) == 2
    assert all(r["ok"] for r in out)
    assert sorted(calls) == ["socks5://a:1080", "socks5://b:1080"]


def test_check_batch_per_proxy_failure_is_isolated():
    from app.services import proxy_intake as pi

    def fake_check(proxy):
        if "b" in proxy:
            raise RuntimeError("blew up")
        return {"ok": True, "exit_ip": "1.1.1.1",
                "latency_ms": 50, "error": None}

    out = pi.check_batch(["socks5://a:1080", "socks5://b:1080"],
                          check_one=fake_check)
    by_proxy = {r["proxy"]: r for r in out}
    assert by_proxy["socks5://a:1080"]["ok"] is True
    assert by_proxy["socks5://b:1080"]["ok"] is False
    assert "blew up" in by_proxy["socks5://b:1080"]["error"]
```

- [ ] **Step 7.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_proxy_intake.py -v
```

Expected: FAIL with ImportError.

- [ ] **Step 7.3: Implement `app/services/proxy_intake.py`**

Create `app/services/proxy_intake.py`:

```python
"""Validate and probe proxy URLs provided by the wizard.

Splits user-pasted text into lines, validates each as a proxy URL, and
fans probe calls out concurrently. Per-line and per-probe failures are
returned as data so the UI can present them; nothing here raises on a
bad line.
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypedDict

from app.services import proxy_check_service as pcs


# scheme://[user[:pass]@]host:port — host is dotted or alphanumeric;
# port required. socks5 / socks5h / http / https accepted.
_URL_RE = re.compile(
    r"^(socks5h?|https?)://"
    r"(?:[^:@/\s]+(?::[^@/\s]+)?@)?"
    r"[A-Za-z0-9.\-]+:\d{2,5}$"
)

_CONCURRENCY = 8


class ParsedLine(TypedDict):
    line_no: int
    proxy: str
    error: str | None


def parse_lines(text: str) -> list[ParsedLine]:
    """One result per non-empty line. error=None means the URL parses."""
    out: list[ParsedLine] = []
    for idx, line in enumerate((text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if _URL_RE.match(stripped):
            out.append({"line_no": idx, "proxy": stripped, "error": None})
        else:
            out.append({"line_no": idx, "proxy": stripped,
                        "error": "not a valid proxy URL "
                                 "(expected scheme://[user:pass@]host:port)"})
    return out


def check_batch(proxies: list[str],
                check_one: Callable[[str], dict] | None = None) -> list[dict]:
    """Probe each proxy URL in parallel. Returns one dict per input proxy
    with keys: proxy, ok, exit_ip, latency_ms, error.

    `check_one` defaults to proxy_check_service.check_one; tests inject a
    fake to avoid network."""
    fn = check_one or pcs.check_one
    results: list[dict] = [None] * len(proxies)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        futures = {ex.submit(fn, p): i for i, p in enumerate(proxies)}
        for fut, i in futures.items():
            proxy = proxies[i]
            try:
                r = fut.result()
            except Exception as e:  # pragma: no cover - defensive
                r = {"ok": False, "exit_ip": None, "latency_ms": None,
                      "error": f"{type(e).__name__}: {e}"}
            results[i] = {"proxy": proxy, **r}
    return results
```

- [ ] **Step 7.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_proxy_intake.py -v
```

Expected: PASS.

- [ ] **Step 7.5: Commit**

```bash
git add app/services/proxy_intake.py tests/test_proxy_intake.py
git commit -m "feat(proxy intake): URL validation + concurrent probe fan-out"
```

---

## Task 8: geelark_service.create_profiles_for_group

**Files:**
- Modify: `app/services/geelark_service.py` (append function)
- Test: `tests/test_geelark_service_create_for_group.py`

- [ ] **Step 8.1: Write failing tests**

Create `tests/test_geelark_service_create_for_group.py`:

```python
def _fake_client(install_ok=True):
    """Fake GeeLarkClient that records calls. Use one instance per test."""
    calls = {"create_phones": [], "get_installable_apps": [],
              "install_app": []}

    class _C:
        def create_phones(self, items):
            calls["create_phones"].append(items)
            return {"details": [{"id": f"phone-{i}"}
                                  for i, _ in enumerate(items, start=1)]}

        def get_installable_apps(self, phone_id, name=None):
            calls["get_installable_apps"].append((phone_id, name))
            return {"items": [{"appName": "TikTok", "appVersionId": "v1"}]}

        def install_app(self, phone_id, app_version_id):
            calls["install_app"].append((phone_id, app_version_id))
            return {}

    return _C(), calls


def test_create_profiles_for_group_makes_one_phone_per_proxy(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="batch-1")
    client, calls = _fake_client()

    log = list(gs.create_profiles_for_group(db, client, gid, [
        "socks5://u:p@1.1.1.1:1080",
        "socks5://u:p@2.2.2.2:1080",
    ]))

    # One create_phones call per proxy (sequential, matches existing pattern)
    assert len(calls["create_phones"]) == 2
    first = calls["create_phones"][0][0]
    assert first["proxy"] == "socks5://u:p@1.1.1.1:1080"
    assert first["groupName"] == "batch-1"
    assert first["serialName"]
    # accounts rows written back with phone_id + group_id + proxy
    rows = db.execute(
        "SELECT geelark_phone_id, proxy, group_id FROM accounts "
        "WHERE group_id=? ORDER BY id", (gid,)).fetchall()
    assert len(rows) == 2
    assert rows[0]["geelark_phone_id"] == "phone-1"
    assert rows[0]["proxy"] == "socks5://u:p@1.1.1.1:1080"
    assert rows[0]["group_id"] == gid
    # TikTok install attempted for each
    assert {c[0] for c in calls["install_app"]} == {"phone-1", "phone-2"}
    # Log mentions both
    joined = "\n".join(log)
    assert "phone-1" in joined and "phone-2" in joined


def test_create_profiles_for_group_continues_on_per_phone_failure(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="b2")

    class _C:
        def __init__(self):
            self.n = 0
        def create_phones(self, items):
            self.n += 1
            if self.n == 1:
                return {"details": []}  # API returned nothing -> failure
            return {"details": [{"id": "phone-OK"}]}
        def get_installable_apps(self, phone_id, name=None):
            return {"items": [{"appName": "TikTok", "appVersionId": "v"}]}
        def install_app(self, phone_id, vid):
            return {}

    log = list(gs.create_profiles_for_group(db, _C(), gid,
        ["socks5://x:1080", "socks5://y:1080"]))
    rows = db.execute(
        "SELECT geelark_phone_id FROM accounts WHERE group_id=?",
        (gid,)).fetchall()
    # Only the second proxy's phone created successfully
    assert {r["geelark_phone_id"] for r in rows if r["geelark_phone_id"]} == \
        {"phone-OK"}
    assert any("FAILED" in line for line in log)
```

- [ ] **Step 8.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_geelark_service_create_for_group.py -v
```

Expected: FAIL (`create_profiles_for_group` does not exist).

- [ ] **Step 8.3: Append `create_profiles_for_group` to `app/services/geelark_service.py`**

At the bottom of `app/services/geelark_service.py`:

```python
def create_profiles_for_group(conn, client, group_id: int,
                              proxies: list[str]):
    """Generator yielding log lines. For each proxy: insert an accounts row
    (group_id + proxy set), create one GeeLark phone bound to that proxy
    inside the GeeLark group with the same name, write back the phone id,
    install TikTok.

    On per-phone failure we log the failure and move on — partial success
    is the desired behavior (other phones for the same batch can still
    work, and the wizard can offer per-row retry)."""
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as grp_repo

    group = grp_repo.get(conn, group_id)
    if group is None:
        yield f"FAILED: group {group_id} not found"
        return
    group_name = group["name"]

    for idx, proxy in enumerate(proxies, start=1):
        # Reserve an accounts row up front so the user can see the slot
        # exists even if phone creation fails mid-batch.
        serial = f"{group_name}-{idx:02d}"
        # Disambiguate profile_name within the unique constraint.
        from app.repositories.accounts import unique_profile_name
        candidate = unique_profile_name(conn, serial, "")
        aid = acc_repo.create(conn, {
            "profile_name": candidate,
            "group_id": group_id,
            "proxy": proxy,
        })
        yield f"Creating phone for {candidate} (proxy {proxy})..."
        try:
            data = client.create_phones([{
                "serialName": candidate,
                "proxy": proxy,
                "groupName": group_name,
            }])
        except Exception as e:
            yield f"  FAILED: create_phones raised: {e}"
            continue
        details = data.get("details") or data.get("list") or []
        if not details:
            yield f"  FAILED: no phone returned for {candidate}"
            continue
        phone_id = details[0].get("id") or details[0].get("envId")
        if not phone_id:
            yield f"  FAILED: phone created but no id for {candidate}"
            continue
        acc_repo.update(conn, aid, {"geelark_phone_id": phone_id})
        yield f"  phone {phone_id} created, installing TikTok..."
        try:
            apps = client.get_installable_apps(phone_id, name="TikTok")
            items = apps.get("items", [])
            tt = next((i for i in items
                       if "tiktok" in (i.get("appName", "").lower())), None)
            if tt:
                client.install_app(phone_id, tt["appVersionId"])
                yield f"  TikTok install requested for {candidate}"
            else:
                yield f"  WARNING: TikTok not in installable list for {candidate}"
        except Exception as e:
            yield f"  WARNING: TikTok auto-install failed for {candidate}: {e}"
    yield "Done."
```

- [ ] **Step 8.4: Run tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_geelark_service_create_for_group.py -v
```

Expected: PASS.

- [ ] **Step 8.5: Commit**

```bash
git add app/services/geelark_service.py tests/test_geelark_service_create_for_group.py
git commit -m "feat(geelark service): create_profiles_for_group binds proxy+group per phone"
```

---

## Task 9: warmup_service — group_id filter

**Files:**
- Modify: `app/services/warmup_service.py` (signature + filter)
- Test: `tests/test_services.py` (extend)

- [ ] **Step 9.1: Write failing test**

Append to `tests/test_services.py`:

```python
def test_run_warmup_filters_by_group_id(db, monkeypatch):
    from app.services import warmup_service as ws
    from app.repositories import accounts as ar

    db.execute("INSERT INTO groups(name) VALUES ('g1'),('g2')")
    ar.create(db, {"profile_name": "A", "group_id": 1,
                    "geelark_phone_id": "p-a",
                    "tiktok_logged_in": 1, "warmed_up": 0})
    ar.create(db, {"profile_name": "B", "group_id": 2,
                    "geelark_phone_id": "p-b",
                    "tiktok_logged_in": 1, "warmed_up": 0})

    called = {"start": [], "create_warmup": [], "stop": [], "query": []}

    class _C:
        def start_phones(self, ids): called["start"].extend(ids)
        def create_warmup_task(self, pid, dur):
            called["create_warmup"].append((pid, dur))
            return "tid"
        def query_tasks(self, ids):
            called["query"].append(ids)
            return [{"status": 3}]
        def stop_phones(self, ids): called["stop"].extend(ids)

    summary = ws.run_warmup(db, _C(), duration_minutes=1,
                              wait=True, group_id=1)
    assert "p-a" in called["start"]
    assert "p-b" not in called["start"]
    assert "1 accounts warmed" in summary
```

- [ ] **Step 9.2: Run test to confirm it fails**

```bash
./.venv/bin/pytest tests/test_services.py::test_run_warmup_filters_by_group_id -v
```

Expected: FAIL (`group_id` is an unexpected keyword argument).

- [ ] **Step 9.3: Modify `run_warmup`**

In `app/services/warmup_service.py`, replace the function signature and `targets =` line:

```python
def run_warmup(conn, client, duration_minutes: int | None = None,
               wait: bool = True, *, group_id: int | None = None) -> str:
    targets = [a for a in acc_repo.list_all(conn)
               if a.get("tiktok_logged_in") and not a.get("warmed_up")
               and a.get("geelark_phone_id")
               and (group_id is None or a.get("group_id") == group_id)]
```

(Everything below `targets = …` stays as-is.)

- [ ] **Step 9.4: Run tests to verify**

```bash
./.venv/bin/pytest tests/test_services.py -v -k warmup
```

Expected: all warmup tests PASS (existing default-call behavior unchanged because `group_id` defaults to None).

- [ ] **Step 9.5: Commit**

```bash
git add app/services/warmup_service.py tests/test_services.py
git commit -m "feat(warmup): optional group_id filter"
```

---

## Task 10: posting_service — group_id filter

**Files:**
- Modify: `app/services/posting_service.py:42-85` (`publish` function)
- Test: `tests/test_services.py` (extend)

- [ ] **Step 10.1: Write failing test**

Append to `tests/test_services.py`:

```python
def test_publish_with_group_id_only_posts_that_group(db, monkeypatch):
    from app.services import posting_service as ps
    from app.repositories import accounts as ar
    from app.repositories import videos as vr

    db.execute("INSERT INTO groups(name) VALUES ('g1'),('g2')")
    a1 = ar.create(db, {"profile_name": "A1", "group_id": 1,
                          "geelark_phone_id": "p1", "warmed_up": 1})
    ar.create(db, {"profile_name": "B1", "group_id": 2,
                    "geelark_phone_id": "p2", "warmed_up": 1})
    v1 = vr.create(db, "g1.mp4", "/tmp/g1.mp4", group_id=1)
    v2 = vr.create(db, "g2.mp4", "/tmp/g2.mp4", group_id=2)

    monkeypatch.setattr(ps, "_caption", lambda p: "cap")
    posts = []

    class _C:
        def upload_file(self, path, ext): return f"http://cdn/{path}"
        def create_video_post_task(self, pid, url, video_desc=None):
            posts.append((pid, url)); return "tid"

    log = list(ps.publish(db, _C(), group_id=1))
    assert any("g1.mp4" in line for line in log)
    assert all("g2.mp4" not in line for line in log)
    assert posts and posts[0][0] == "p1"
    # v2 stays PENDING because it's in another group
    assert vr.get(db, v2)["result"] == "PENDING"
```

- [ ] **Step 10.2: Run test to confirm it fails**

```bash
./.venv/bin/pytest tests/test_services.py::test_publish_with_group_id_only_posts_that_group -v
```

Expected: FAIL (unexpected kwarg).

- [ ] **Step 10.3: Modify `publish` signature + filters**

In `app/services/posting_service.py`, replace the existing `publish` body (lines 42-85) with:

```python
def publish(conn, client, cooldown_hours: float = 2, force: bool = False,
            *, group_id: int | None = None):
    """Generator of log lines. Videos with a manually-set account_id post to
    that account (warmup + cooldown guarded unless force=True); videos with no
    account_id auto-assign via build_pool/assign as before.

    If group_id is provided, both pending videos and the assignment pool are
    restricted to that group."""
    pend = vid_repo.list_pending(conn)
    if group_id is not None:
        pend = [v for v in pend if v.get("group_id") == group_id]
    if not pend:
        yield "No videos to post."
        return
    manual = [v for v in pend if v.get("account_id")]
    auto = [v for v in pend if not v.get("account_id")]
    accounts = acc_repo.list_all(conn)
    if group_id is not None:
        accounts = [a for a in accounts if a.get("group_id") == group_id]
    by_id = {a["id"]: a for a in accounts}
    cutoff = time.time() - cooldown_hours * 3600

    for v in manual:
        acc = by_id.get(v["account_id"])
        if acc is None:
            continue  # account deleted; FK already nulled — defensive
        label = (acc.get("profile_name") or "").strip()
        yield f"'{v['original_filename']}' -> {label} (manual)"
        if not force:
            if not acc.get("warmed_up"):
                yield f"  SKIPPED: {label} not warmed up"
                continue
            last = _epoch(acc.get("last_posted_at"))
            if last is not None and last >= cutoff:
                yield f"  SKIPPED: {label} in cooldown"
                continue
        yield from _post_one(conn, client, v, acc)

    if auto:
        fresh = acc_repo.list_all(conn)
        if group_id is not None:
            fresh = [a for a in fresh if a.get("group_id") == group_id]
        pool = build_pool([{**a, "last_posted_at": _epoch(a.get("last_posted_at"))}
                            for a in fresh])
        by_name = {(a["profile_name"] or "").strip(): a for a in fresh}
        plan = assign(auto, pool, cooldown_hours=cooldown_hours)
        if not plan and not manual:
            yield "No warmed-up accounts available."
        for item in plan:
            v = item["video"]
            acc = by_name[item["profile_name"]]
            yield f"'{v['original_filename']}' -> {acc['profile_name']} (auto)"
            yield from _post_one(conn, client, v, acc)
    yield "Done."
```

- [ ] **Step 10.4: Run all service tests**

```bash
./.venv/bin/pytest tests/test_services.py -v
```

Expected: all PASS, including the new test and pre-existing publish tests (default behavior unchanged because `group_id=None`).

- [ ] **Step 10.5: Commit**

```bash
git add app/services/posting_service.py tests/test_services.py
git commit -m "feat(posting): optional group_id filter on videos + pool"
```

---

## Task 11: API — groups CRUD + counts + archive

**Files:**
- Create: `app/api/groups.py`
- Modify: `app/main.py:49-54` (include router)
- Test: `tests/test_api.py` (extend)

- [ ] **Step 11.1: Look at how an existing router is mounted**

```bash
sed -n '47,60p' app/main.py
```

(Confirm the pattern: `from app.api import ...; app.include_router(..., dependencies=[Depends(require_auth)])`.)

- [ ] **Step 11.2: Write failing tests for the CRUD endpoints**

Append to `tests/test_api.py`:

```python
def test_create_group_and_list(client):
    r = client.post("/api/groups",
                     json={"name": "jp-1", "target_phone_count": 10,
                            "notes": "first batch"})
    assert r.status_code == 200
    gid = r.json()["id"]

    r = client.get("/api/groups")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "jp-1"
    assert rows[0]["counts"]["phones"] == 0


def test_get_group_includes_counts(client):
    r = client.post("/api/groups", json={"name": "g1"})
    gid = r.json()["id"]
    r = client.get(f"/api/groups/{gid}")
    assert r.status_code == 200
    assert "counts" in r.json()


def test_get_unknown_group_404(client):
    r = client.get("/api/groups/9999")
    assert r.status_code == 404


def test_archive_group_hides_from_list(client):
    r = client.post("/api/groups", json={"name": "tmp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/archive")
    assert r.status_code == 200
    rows = client.get("/api/groups").json()
    assert all(g["id"] != gid for g in rows)
```

- [ ] **Step 11.3: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_api.py -v -k group
```

Expected: FAIL with 404s (no routes mounted yet).

- [ ] **Step 11.4: Create `app/api/groups.py` with CRUD routes**

Create `app/api/groups.py`:

```python
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/groups")


def _db():
    from app.main import db_conn
    return db_conn()


@router.post("")
def create_group(body: dict):
    from app.services import groups_service as gs
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        gid = gs.create(_db(), name=name,
                         target_phone_count=body.get("target_phone_count"),
                         notes=body.get("notes"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": gid}


@router.get("")
def list_groups():
    from app.services import groups_service as gs
    return gs.list_all(_db())


@router.get("/{group_id}")
def get_group(group_id: int):
    from app.services import groups_service as gs
    row = gs.get(_db(), group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="group not found")
    return row


@router.post("/{group_id}/archive")
def archive_group(group_id: int):
    from app.services import groups_service as gs
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    gs.archive(_db(), group_id)
    return {"ok": True}
```

- [ ] **Step 11.5: Mount the router in `app/main.py`**

In `app/main.py`, modify the `from app.api import ...` line (line ~49) to add `groups`:

```python
from app.api import accounts, videos, dashboard, settings, phones, groups
app.include_router(accounts.router, dependencies=[Depends(require_auth)])
app.include_router(videos.router, dependencies=[Depends(require_auth)])
app.include_router(dashboard.router, dependencies=[Depends(require_auth)])
app.include_router(settings.router, dependencies=[Depends(require_auth)])
app.include_router(phones.router, dependencies=[Depends(require_auth)])
app.include_router(groups.router, dependencies=[Depends(require_auth)])
```

- [ ] **Step 11.6: Run API tests**

```bash
./.venv/bin/pytest tests/test_api.py -v -k group
```

Expected: PASS.

- [ ] **Step 11.7: Commit**

```bash
git add app/api/groups.py app/main.py tests/test_api.py
git commit -m "feat(api): /api/groups CRUD + counts + archive"
```

---

## Task 12: API — proxies/check + phones/create (SSE)

**Files:**
- Modify: `app/api/groups.py` (append routes)
- Test: `tests/test_api.py` (extend)

- [ ] **Step 12.1: Write failing tests**

Append to `tests/test_api.py`:

```python
def test_proxies_check_returns_per_proxy_results(client, monkeypatch):
    from app.services import proxy_intake as pi

    def fake_check_batch(proxies, check_one=None):
        return [{"proxy": p, "ok": True, "exit_ip": "1.1.1.1",
                  "latency_ms": 100, "error": None} for p in proxies]

    monkeypatch.setattr(pi, "check_batch", fake_check_batch)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/proxies/check",
                     json={"proxies": ["socks5://a:1080", "socks5://b:1080"]})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["ok"] for row in rows)


def test_phones_create_stream_invokes_geelark_service(client, monkeypatch):
    from app.services import geelark_service as gs

    captured = {}
    def fake_make_client(): return object()
    def fake_create_for_group(conn, client, group_id, proxies):
        captured["group_id"] = group_id
        captured["proxies"] = list(proxies)
        yield "log line A"
        yield "log line B"
    monkeypatch.setattr(gs, "make_client", fake_make_client)
    monkeypatch.setattr(gs, "create_profiles_for_group",
                          fake_create_for_group)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                     json={"proxies": ["socks5://a:1080"]})
    assert r.status_code == 200
    body = r.text
    assert "log line A" in body
    assert "log line B" in body
    assert captured["group_id"] == gid
    assert captured["proxies"] == ["socks5://a:1080"]
```

- [ ] **Step 12.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_api.py -v -k "proxies_check or phones_create"
```

Expected: FAIL with 404.

- [ ] **Step 12.3: Add routes to `app/api/groups.py`**

Append to `app/api/groups.py`:

```python
@router.post("/{group_id}/proxies/check")
def check_proxies(group_id: int, body: dict):
    from app.services import groups_service as gs
    from app.services import proxy_intake as pi
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    proxies = body.get("proxies") or []
    if not isinstance(proxies, list):
        raise HTTPException(status_code=400, detail="proxies must be a list")
    return pi.check_batch(proxies)


@router.post("/{group_id}/phones/create")
def create_phones(group_id: int, body: dict):
    from app.services import groups_service as gs
    from app.services import geelark_service
    from app.sse import sse_from_generator, locked_generator
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    proxies = body.get("proxies") or []
    if not isinstance(proxies, list) or not proxies:
        raise HTTPException(status_code=400,
                            detail="proxies must be a non-empty list")
    return sse_from_generator(locked_generator(
        geelark_service.create_profiles_for_group(
            _db(), geelark_service.make_client(), group_id, proxies)))
```

- [ ] **Step 12.4: Run tests**

```bash
./.venv/bin/pytest tests/test_api.py -v -k "proxies_check or phones_create"
```

Expected: PASS.

- [ ] **Step 12.5: Commit**

```bash
git add app/api/groups.py tests/test_api.py
git commit -m "feat(api): groups proxies/check + phones/create (SSE)"
```

---

## Task 13: API — accounts/parse + accounts/assign + bulk-mark-logged-in

**Files:**
- Modify: `app/api/groups.py` (append)
- Modify: `app/api/accounts.py` (append `bulk-mark-logged-in`)
- Test: `tests/test_api.py` (extend)

- [ ] **Step 13.1: Write failing tests**

Append to `tests/test_api.py`:

```python
def test_accounts_parse_returns_preview(client):
    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/accounts/parse",
                     json={"text": "alice@x.com pw\nbob@x.com pw2"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert rows[0]["email"] == "alice@x.com"
    assert rows[1]["email"] == "bob@x.com"


def test_accounts_assign_writes_to_unassigned_group_rows(client):
    from app.repositories import accounts as ar
    # Set up: create a group + 3 phones (no credentials yet)
    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    # Simulate phones already created by inserting accounts directly
    from app.main import db_conn
    conn = db_conn()
    a1 = ar.create(conn, {"profile_name": "P1", "group_id": gid,
                            "geelark_phone_id": "ph1"})
    a2 = ar.create(conn, {"profile_name": "P2", "group_id": gid,
                            "geelark_phone_id": "ph2"})
    a3 = ar.create(conn, {"profile_name": "P3", "group_id": gid,
                            "geelark_phone_id": "ph3"})

    r = client.post(f"/api/groups/{gid}/accounts/assign", json={"accounts": [
        {"email": "a@x.com", "password": "p1", "tiktok_username": None,
          "tiktok_password": None, "recovery_email": None,
          "mail_inbox_url": None, "raw": "a@x.com p1", "warnings": []},
        {"email": "b@x.com", "password": "p2", "tiktok_username": "@b",
          "tiktok_password": None, "recovery_email": None,
          "mail_inbox_url": None, "raw": "b@x.com p2 @b", "warnings": []},
    ]})
    assert r.status_code == 200
    assert r.json()["assigned"] == 2
    assert ar.get(conn, a1)["email"] == "a@x.com"
    assert ar.get(conn, a2)["email"] == "b@x.com"
    assert ar.get(conn, a3)["email"] is None  # only 2 parsed; 3rd untouched


def test_bulk_mark_logged_in(client):
    from app.repositories import accounts as ar
    from app.main import db_conn
    conn = db_conn()
    a1 = ar.create(conn, {"profile_name": "Q1"})
    a2 = ar.create(conn, {"profile_name": "Q2"})
    r = client.post("/api/accounts/bulk-mark-logged-in",
                     json={"ids": [a1, a2]})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    assert ar.get(conn, a1)["tiktok_logged_in"] == 1
    assert ar.get(conn, a2)["tiktok_logged_in"] == 1
```

- [ ] **Step 13.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_api.py -v -k "accounts_parse or accounts_assign or bulk_mark"
```

Expected: FAIL.

- [ ] **Step 13.3: Add `accounts/parse` and `accounts/assign` to `app/api/groups.py`**

Append:

```python
@router.post("/{group_id}/accounts/parse")
def parse_accounts(group_id: int, body: dict):
    from app.services import groups_service as gs
    from app.services import credentials_parser as cp
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    return cp.parse_lines(body.get("text") or "")


@router.post("/{group_id}/accounts/assign")
def assign_accounts(group_id: int, body: dict):
    """Apply parsed credentials to this group's accounts rows that have no
    email yet, oldest id first. Skips any input row with no email AND no
    password (nothing to apply)."""
    from app.services import groups_service as gs
    from app.repositories import accounts as acc_repo
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    parsed = body.get("accounts") or []
    targets = [a for a in acc_repo.list_by_group(_db(), group_id)
               if not (a.get("email") or "").strip()]
    assigned = 0
    skipped = 0
    for src in parsed:
        if not targets:
            break
        if not (src.get("email") or src.get("password")
                or src.get("tiktok_username")):
            skipped += 1
            continue
        slot = targets.pop(0)
        acc_repo.update(_db(), slot["id"], {
            "email": src.get("email"),
            "password": src.get("password"),
            "tiktok_username": src.get("tiktok_username"),
            "tiktok_password": src.get("tiktok_password"),
            "recovery_email": src.get("recovery_email"),
            "mail_inbox_url": src.get("mail_inbox_url"),
            "raw_credentials": src.get("raw"),
        })
        assigned += 1
    return {"assigned": assigned, "skipped": skipped}
```

- [ ] **Step 13.4: Add `bulk-mark-logged-in` to `app/api/accounts.py`**

Append to `app/api/accounts.py`:

```python
@router.post("/bulk-mark-logged-in")
def bulk_mark_logged_in(body: dict):
    from app.repositories import accounts as acc_repo
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=400,
                            detail="ids must be a list of ints")
    n = acc_repo.bulk_set_logged_in(_db(), ids)
    return {"updated": n}
```

- [ ] **Step 13.5: Run tests**

```bash
./.venv/bin/pytest tests/test_api.py -v
```

Expected: all PASS.

- [ ] **Step 13.6: Commit**

```bash
git add app/api/groups.py app/api/accounts.py tests/test_api.py
git commit -m "feat(api): accounts parse+assign per group, bulk-mark-logged-in"
```

---

## Task 14: API — warmup/start + publish (SSE)

**Files:**
- Modify: `app/api/groups.py` (append)
- Test: `tests/test_api.py` (extend)

- [ ] **Step 14.1: Write failing tests**

Append to `tests/test_api.py`:

```python
def test_warmup_start_streams_and_passes_group_id(client, monkeypatch):
    from app.services import warmup_service as ws
    from app.services import geelark_service as gs

    monkeypatch.setattr(gs, "make_client", lambda: object())
    seen = {}
    def fake_run(conn, client, *, group_id=None, **kw):
        seen["group_id"] = group_id
        return f"summary for {group_id}"
    monkeypatch.setattr(ws, "run_warmup", fake_run)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/warmup/start")
    assert r.status_code == 200
    assert f"summary for {gid}" in r.text
    assert seen["group_id"] == gid


def test_publish_start_streams_and_passes_group_id(client, monkeypatch):
    from app.services import posting_service as ps
    from app.services import geelark_service as gs

    monkeypatch.setattr(gs, "make_client", lambda: object())
    seen = {}
    def fake_publish(conn, client, *, group_id=None, **kw):
        seen["group_id"] = group_id
        yield f"posting for {group_id}"
        yield "Done."
    monkeypatch.setattr(ps, "publish", fake_publish)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/publish")
    assert r.status_code == 200
    assert "Done." in r.text
    assert seen["group_id"] == gid
```

- [ ] **Step 14.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_api.py -v -k "warmup_start or publish_start"
```

Expected: FAIL.

- [ ] **Step 14.3: Add routes**

Append to `app/api/groups.py`:

```python
@router.post("/{group_id}/warmup/start")
def warmup_start(group_id: int):
    from app.services import groups_service as gs
    from app.services import warmup_service as ws
    from app.services import geelark_service
    from app.sse import sse_from_generator, locked_generator
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")

    def _gen():
        yield f"warmup starting for group {group_id}..."
        summary = ws.run_warmup(_db(), geelark_service.make_client(),
                                  group_id=group_id)
        yield summary
        yield "Done."
    return sse_from_generator(locked_generator(_gen()))


@router.post("/{group_id}/publish")
def publish_start(group_id: int):
    from app.services import groups_service as gs
    from app.services import posting_service as ps
    from app.services import geelark_service
    from app.sse import sse_from_generator, locked_generator
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    return sse_from_generator(locked_generator(
        ps.publish(_db(), geelark_service.make_client(), group_id=group_id)))
```

- [ ] **Step 14.4: Run tests**

```bash
./.venv/bin/pytest tests/test_api.py -v
```

Expected: PASS. Run full suite:

```bash
./.venv/bin/pytest -q
```

All green.

- [ ] **Step 14.5: Commit**

```bash
git add app/api/groups.py tests/test_api.py
git commit -m "feat(api): groups warmup/start + publish (SSE)"
```

---

## Task 15: API — videos upload + drive import accept group_id

**Files:**
- Modify: `app/api/videos.py` (`upload` + `import_drive`)
- Modify: `app/services/drive_import.py` (`start_import` + `run_import` + thread)
- Test: `tests/test_api.py` + `tests/test_drive_import.py` (extend)

Note: the current `/upload` accepts a **list** of files; the change keeps that
shape and adds a single `group_id` form field that applies to every uploaded
file in the request.

- [ ] **Step 15.1: Write failing test**

Append to `tests/test_api.py`:

```python
def test_video_upload_persists_group_id(client):
    from app.repositories import videos as vr
    from app.main import db_conn

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    file_bytes = b"fake-mp4-bytes"
    r = client.post(
        "/api/videos/upload",
        data={"group_id": str(gid)},
        files=[("files", ("clip.mp4", file_bytes, "video/mp4"))],
    )
    assert r.status_code == 200
    assert r.json()["uploaded"] == 1
    rows = vr.list_all(db_conn())
    assert rows[0]["original_filename"] == "clip.mp4"
    assert rows[0]["group_id"] == gid


def test_drive_import_passes_group_id(client, monkeypatch):
    from app.services import drive_import as di

    captured = {}
    def fake_start_import(files, group_id=None):
        captured["files"] = files
        captured["group_id"] = group_id
        import threading
        return threading.Thread(target=lambda: None)
    monkeypatch.setattr(di, "start_import", fake_start_import)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post("/api/videos/import",
                     json={"files": [{"id": "f1", "name": "a.mp4"}],
                            "group_id": gid})
    assert r.status_code == 200
    assert captured["group_id"] == gid
```

- [ ] **Step 15.2: Run tests to confirm they fail**

```bash
./.venv/bin/pytest tests/test_api.py -v -k "upload_persists_group or drive_import_passes_group"
```

Expected: FAIL — `group_id` not accepted yet.

- [ ] **Step 15.3: Modify `app/api/videos.py` — upload + import_drive**

Replace the existing `upload` handler:

```python
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
# (keep the existing imports at the top; just add Form)

@router.post("/upload")
async def upload(files: list[UploadFile] = File(...),
                 group_id: int | None = Form(None)):
    n = 0
    for f in files:
        if not f.filename:
            raise HTTPException(status_code=400, detail="filename required")
        safe_name = Path(f.filename).name
        if not safe_name:
            raise HTTPException(status_code=400, detail="invalid filename")
        dest = Path(config.UPLOAD_DIR) / safe_name
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        vid_repo.create(_db(), safe_name, str(dest), group_id=group_id)
        n += 1
    return {"uploaded": n}
```

Replace the existing `import_drive` handler:

```python
@router.post("/import")
def import_drive(body: dict):
    from app.services import drive_import
    files = (body or {}).get("files") or []
    group_id = (body or {}).get("group_id")
    try:
        drive_import.start_import(files, group_id=group_id)
    except drive_import.ProxyNotConfigured:
        raise HTTPException(status_code=503,
                            detail="DRIVE_IMPORT_PROXY is not configured")
    except drive_import.ImportAlreadyRunning:
        raise HTTPException(status_code=409,
                            detail="an import is already running")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"started": True}
```

- [ ] **Step 15.4: Modify `app/services/drive_import.py` — thread the group_id through**

Find `def run_import(files: list[dict]) -> None:` and change to:

```python
def run_import(files: list[dict], group_id: int | None = None) -> None:
```

Inside `run_import`, change the `vid_repo.create(...)` call to pass `group_id`:

```python
vid_repo.create(conn, name, str(dest), drive_file_id=file_id,
                group_id=group_id)
```

Find `def start_import(files: list[dict]) -> threading.Thread:` and change to:

```python
def start_import(files: list[dict],
                 group_id: int | None = None) -> threading.Thread:
```

Inside `start_import`, change the `Thread(target=run_import, args=(files,), ...)`
to:

```python
t = threading.Thread(target=run_import, args=(files, group_id), daemon=True)
```

- [ ] **Step 15.5: Run tests**

```bash
./.venv/bin/pytest tests/test_api.py -v -k "upload or drive_import"
./.venv/bin/pytest tests/test_drive_import.py -v
```

Expected: all PASS. Existing drive tests still work because `group_id` defaults
to `None` and is forwarded as `None` to `vid_repo.create`.

- [ ] **Step 15.6: Commit**

```bash
git add app/api/videos.py app/services/drive_import.py tests/test_api.py
git commit -m "feat(videos): accept group_id on upload + drive import"
```

---

## Task 16: Frontend — Groups tab + list page (no wizard yet)

**Files:**
- Modify: `frontend/src/App.tsx` (add Groups tab/route)
- Modify: `frontend/src/components/Shell.tsx` (add nav entry — check existing tabs first)
- Create: `frontend/src/pages/Groups.tsx`
- Modify: `frontend/src/i18n/locales/en.json` + `zh.json` (add `groups.*` keys)

**Existing layout (already inspected):**
- `frontend/src/components/Shell.tsx` exports a `type Tab = 'dashboard' | 'accounts' | 'videos' | 'settings'` and a `NAV` array of `{id, path, end?, icon}`. Tab labels resolve via `t('shell.${id}')`.
- `frontend/src/App.tsx` mounts top-level `<Route>`s inside a `<Routes>` block. Unknown URLs `<Navigate to="/" replace />`.

- [ ] **Step 16.1: Create the page skeleton**

Create `frontend/src/pages/Groups.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

type GroupRow = {
  id: number
  name: string
  target_phone_count: number | null
  notes: string | null
  created_at: string
  counts: {
    phones: number
    assigned: number
    logged_in: number
    warmed: number
    published: number
    pending: number
    failed: number
  }
}

export default function Groups() {
  const { t } = useTranslation()
  const [rows, setRows] = useState<GroupRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function reload() {
    setError(null)
    try {
      const r = await fetch('/api/groups')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setRows(await r.json())
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => { reload() }, [])

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{t('groups.title')}</h1>
        {/* New-group button will open the wizard in Task 18 */}
        <button
          className="px-3 py-1.5 rounded bg-blue-600 text-white"
          onClick={() => window.alert('wizard coming in Task 18')}
        >
          + {t('groups.new')}
        </button>
      </div>
      {error && <div className="text-red-600">{error}</div>}
      {rows === null && !error && <div>{t('common.loading')}</div>}
      {rows && rows.length === 0 && <div>{t('groups.empty')}</div>}
      {rows && rows.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-gray-500">
            <tr>
              <th>{t('groups.col.name')}</th>
              <th>{t('groups.col.created')}</th>
              <th>{t('groups.col.phones')}</th>
              <th>{t('groups.col.logged_in')}</th>
              <th>{t('groups.col.warmed')}</th>
              <th>{t('groups.col.published')}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(g => (
              <tr key={g.id} className="border-t">
                <td>
                  <Link to={`/groups/${g.id}`}
                        className="text-blue-600 hover:underline">
                    {g.name}
                  </Link>
                </td>
                <td>{g.created_at}</td>
                <td>{g.counts.phones}{g.target_phone_count
                    ? ` / ${g.target_phone_count}` : ''}</td>
                <td>{g.counts.logged_in} / {g.counts.assigned}</td>
                <td>{g.counts.warmed} / {g.counts.logged_in}</td>
                <td>{g.counts.published}</td>
                <td>
                  <button
                    className="text-red-600 text-xs"
                    onClick={async () => {
                      if (!confirm(t('groups.archive_confirm'))) return
                      await fetch(`/api/groups/${g.id}/archive`,
                                  { method: 'POST' })
                      reload()
                    }}>
                    {t('groups.archive')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
```

- [ ] **Step 16.2: Add route in `frontend/src/App.tsx`**

After the `import Accounts from './pages/Accounts'` line, add:

```tsx
import Groups from './pages/Groups'
```

In the `<Routes>` block, add a `<Route>` for groups right after the Dashboard route:

```tsx
<Route path="/groups" element={<Groups />} />
```

- [ ] **Step 16.3: Add Groups to Shell nav**

In `frontend/src/components/Shell.tsx`, change the `Tab` type to include `'groups'`:

```tsx
type Tab = 'dashboard' | 'groups' | 'accounts' | 'videos' | 'settings'
```

In the `NAV` array, insert a Groups entry between Dashboard and Accounts. Reuse the `AccountsIcon` for now (or copy `AccountsIcon` and rename to `GroupsIcon` — visual polish can come later):

```tsx
const NAV: { id: Tab; path: string; end?: boolean; icon: ReactNode }[] = [
  { id: 'dashboard', path: '/', end: true, icon: <DashboardIcon /> },
  { id: 'groups', path: '/groups', icon: <AccountsIcon /> },
  { id: 'accounts', path: '/accounts', icon: <AccountsIcon /> },
  { id: 'videos', path: '/videos', icon: <VideosIcon /> },
  { id: 'settings', path: '/settings', icon: <SettingsIcon /> },
]
```

The label resolves via `t('shell.groups')` — add the i18n key in the next step.

- [ ] **Step 16.4: Add i18n strings**

In `frontend/src/i18n/locales/en.json`, add to the existing `shell` block: `"groups": "Groups"`. Then add a top-level `groups` block:

```json
"groups": {
  "title": "Groups",
  "new": "New group",
  "empty": "No groups yet — click \"New group\" to start a batch.",
  "archive": "Archive",
  "archive_confirm": "Archive this group? It stays in the DB but disappears from the list.",
  "col": {
    "name": "Name",
    "created": "Created",
    "phones": "Phones",
    "logged_in": "Logged in",
    "warmed": "Warmed",
    "published": "Published"
  }
}
```

In `frontend/src/i18n/locales/zh.json`, add to the existing `shell` block: `"groups": "分组"`. Then add a top-level `groups` block:

```json
"groups": {
  "title": "分组",
  "new": "新建分组",
  "empty": "还没有分组 — 点 \"新建分组\" 开始一批账号。",
  "archive": "归档",
  "archive_confirm": "归档这个分组？记录保留在数据库，但不再出现在列表中。",
  "col": {
    "name": "名称",
    "created": "创建时间",
    "phones": "手机",
    "logged_in": "已登录",
    "warmed": "已暖机",
    "published": "已发布"
  }
}
```

- [ ] **Step 16.5: Build the frontend**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: build succeeds with no TS errors. Smoke-test by `./run.sh` and navigating to `/groups` — should see empty state.

- [ ] **Step 16.6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Shell.tsx \
        frontend/src/pages/Groups.tsx \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(ui): Groups tab + list page"
```

---

## Task 17: Frontend — Group detail page

**Files:**
- Create: `frontend/src/pages/GroupDetail.tsx`
- Modify: `frontend/src/App.tsx` (add `/groups/:id` route)
- Modify: `frontend/src/i18n/locales/*.json` (add detail strings)

- [ ] **Step 17.1: Create page skeleton**

Create `frontend/src/pages/GroupDetail.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

type Group = {
  id: number; name: string; target_phone_count: number | null;
  notes: string | null; created_at: string;
  counts: { phones: number; assigned: number; logged_in: number;
            warmed: number; published: number; pending: number;
            failed: number }
}

export default function GroupDetail() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const [group, setGroup] = useState<Group | null>(null)
  const [streamLog, setStreamLog] = useState<string>('')
  const [running, setRunning] = useState<string | null>(null)

  async function reload() {
    const r = await fetch(`/api/groups/${id}`)
    if (r.ok) setGroup(await r.json())
  }
  useEffect(() => { reload() }, [id])

  async function runStream(path: string, label: string) {
    setRunning(label)
    setStreamLog('')
    const r = await fetch(path, { method: 'POST' })
    const reader = r.body!.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      setStreamLog(buf)
    }
    setRunning(null)
    reload()
  }

  if (!group) return <div className="p-4">{t('common.loading')}</div>

  const c = group.counts
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-2xl font-semibold">{group.name}</h1>
      <div className="text-sm text-gray-500">
        {t('groups.detail.created')}: {group.created_at}
      </div>

      <section className="border rounded p-3">
        <h2 className="font-medium mb-2">{t('groups.detail.progress')}</h2>
        <ul className="text-sm space-y-1">
          <li>{t('groups.col.phones')}: {c.phones}
              {group.target_phone_count ? ` / ${group.target_phone_count}` : ''}</li>
          <li>{t('groups.detail.assigned')}: {c.assigned} / {c.phones}</li>
          <li>{t('groups.col.logged_in')}: {c.logged_in} / {c.assigned}</li>
          <li>{t('groups.col.warmed')}: {c.warmed} / {c.logged_in}</li>
          <li>{t('groups.col.published')}: {c.published}
              {c.pending ? ` (${c.pending} ${t('groups.detail.pending')})` : ''}</li>
        </ul>
      </section>

      <section className="flex gap-2">
        <button disabled={!!running}
                className="px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-50"
                onClick={() => runStream(
                  `/api/groups/${id}/warmup/start`, 'warmup')}>
          ▶ {t('groups.detail.start_warmup')}
        </button>
        <button disabled={!!running}
                className="px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-50"
                onClick={() => runStream(
                  `/api/groups/${id}/publish`, 'publish')}>
          ▶ {t('groups.detail.publish_now')}
        </button>
      </section>

      {streamLog && (
        <pre className="border rounded p-2 text-xs whitespace-pre-wrap
                       max-h-64 overflow-auto bg-gray-50">{streamLog}</pre>
      )}
    </div>
  )
}
```

- [ ] **Step 17.2: Add route**

In `frontend/src/App.tsx`:

```tsx
import GroupDetail from './pages/GroupDetail'
// ...
<Route path="/groups/:id" element={<GroupDetail />} />
```

- [ ] **Step 17.3: Add i18n keys**

To `groups` block in both en.json and zh.json:

en:
```json
"detail": {
  "created": "Created",
  "progress": "Progress",
  "assigned": "Accounts assigned",
  "pending": "pending",
  "start_warmup": "Warmup this group",
  "publish_now": "Publish this group"
}
```

zh:
```json
"detail": {
  "created": "创建时间",
  "progress": "进度",
  "assigned": "已分配账号",
  "pending": "待发",
  "start_warmup": "暖机此组",
  "publish_now": "发布此组"
}
```

- [ ] **Step 17.4: Build and smoke-test**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: success. Click into a group from the list and verify the detail page loads.

- [ ] **Step 17.5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/pages/GroupDetail.tsx \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(ui): group detail page with warmup/publish actions"
```

---

## Task 18: Frontend — Wizard shell + Step 1 (Name + Proxies)

**Files:**
- Create: `frontend/src/components/wizard/GroupWizard.tsx`
- Create: `frontend/src/components/wizard/Step1Proxies.tsx`
- Modify: `frontend/src/pages/Groups.tsx` (replace alert with modal open)

- [ ] **Step 18.1: Create the wizard shell**

Create `frontend/src/components/wizard/GroupWizard.tsx`:

```tsx
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Step1Proxies from './Step1Proxies'
// Steps 2-4 added in Tasks 19-21.

export type WizardState = {
  name: string
  notes: string
  proxies: string[]                // proxies user chose to use
  groupId: number | null
  createdPhones: { id: number; name: string }[]
}

export default function GroupWizard({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const [step, setStep] = useState(1)
  const [state, setState] = useState<WizardState>({
    name: '', notes: '', proxies: [],
    groupId: null, createdPhones: [],
  })

  function patch(p: Partial<WizardState>) {
    setState(s => ({ ...s, ...p }))
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg w-[800px] max-h-[90vh] overflow-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold">
            {t('wizard.title')} — {t(`wizard.step.${step}`)}
          </h2>
          <button onClick={onClose}
                  className="text-gray-500 hover:text-black">✕</button>
        </div>
        {step === 1 && (
          <Step1Proxies state={state} patch={patch}
                        onNext={() => setStep(2)} onCancel={onClose} />
        )}
        {step === 2 && <div>Step 2 — coming in Task 19</div>}
        {step === 3 && <div>Step 3 — coming in Task 20</div>}
        {step === 4 && <div>Step 4 — coming in Task 21</div>}
      </div>
    </div>
  )
}
```

- [ ] **Step 18.2: Create Step 1 component**

Create `frontend/src/components/wizard/Step1Proxies.tsx`:

```tsx
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { WizardState } from './GroupWizard'

type ProbeRow = { proxy: string; ok: boolean; exit_ip?: string | null;
                  latency_ms?: number | null; error?: string | null }

export default function Step1Proxies({
  state, patch, onNext, onCancel,
}: {
  state: WizardState
  patch: (p: Partial<WizardState>) => void
  onNext: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const [checking, setChecking] = useState(false)
  const [results, setResults] = useState<ProbeRow[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function runCheck() {
    setErr(null)
    const proxies = text.split('\n').map(l => l.trim()).filter(Boolean)
    if (!proxies.length) { setErr(t('wizard.s1.no_proxies')); return }
    if (!state.name.trim()) { setErr(t('wizard.s1.no_name')); return }
    setChecking(true)
    // Create group first if not already; we need group_id to check.
    let gid = state.groupId
    if (gid === null) {
      const r = await fetch('/api/groups', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: state.name.trim(),
                                notes: state.notes || null,
                                target_phone_count: proxies.length }),
      })
      if (!r.ok) { setErr(await r.text()); setChecking(false); return }
      gid = (await r.json()).id
      patch({ groupId: gid })
    }
    const r2 = await fetch(`/api/groups/${gid}/proxies/check`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxies }),
    })
    if (!r2.ok) { setErr(await r2.text()); setChecking(false); return }
    const data: ProbeRow[] = await r2.json()
    setResults(data)
    setSelected(new Set(data.filter(r => r.ok).map(r => r.proxy)))
    setChecking(false)
  }

  function toggle(proxy: string) {
    const next = new Set(selected)
    next.has(proxy) ? next.delete(proxy) : next.add(proxy)
    setSelected(next)
  }

  function proceed() {
    const chosen = (results || []).filter(r => selected.has(r.proxy))
                                    .map(r => r.proxy)
    if (!chosen.length) { setErr(t('wizard.s1.select_at_least_one')); return }
    setSubmitting(true)
    patch({ proxies: chosen })
    onNext()
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium mb-1">
          {t('wizard.s1.name')}
        </label>
        <input
          className="border rounded w-full px-2 py-1"
          value={state.name}
          onChange={e => patch({ name: e.target.value })}
          placeholder="project-jp"
        />
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">
          {t('wizard.s1.notes')}
        </label>
        <input
          className="border rounded w-full px-2 py-1"
          value={state.notes}
          onChange={e => patch({ notes: e.target.value })}
        />
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">
          {t('wizard.s1.proxies')}
        </label>
        <textarea
          className="border rounded w-full px-2 py-1 font-mono text-xs h-40"
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="socks5://user:pass@host:port"
        />
      </div>
      <div className="flex gap-2">
        <button onClick={runCheck} disabled={checking}
                className="px-3 py-1.5 rounded bg-gray-200">
          {checking ? t('common.loading') : t('wizard.s1.check')}
        </button>
      </div>
      {results && (
        <div className="border rounded p-2 text-xs max-h-48 overflow-auto">
          {results.map((r, i) => (
            <label key={i} className="flex items-center gap-2 py-0.5">
              <input type="checkbox" checked={selected.has(r.proxy)}
                     onChange={() => toggle(r.proxy)} />
              <span className={r.ok ? 'text-green-600' : 'text-red-600'}>
                {r.ok ? '🟢' : '🔴'}
              </span>
              <code className="flex-1 truncate">{r.proxy}</code>
              {r.ok
                ? <span className="text-gray-500">
                    {r.exit_ip} · {r.latency_ms}ms
                  </span>
                : <span className="text-red-600">{r.error}</span>}
            </label>
          ))}
        </div>
      )}
      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="px-3 py-1.5">
          {t('common.cancel')}
        </button>
        <button onClick={proceed}
                disabled={!results || submitting}
                className="px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-50">
          {t('common.next')} →
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 18.3: Wire wizard into Groups page**

In `frontend/src/pages/Groups.tsx`, replace the `window.alert` button with state-driven open + render:

```tsx
import GroupWizard from '../components/wizard/GroupWizard'
// ... in component:
const [wizardOpen, setWizardOpen] = useState(false)
// replace the button onClick:
onClick={() => setWizardOpen(true)}
// before closing </div>:
{wizardOpen && <GroupWizard onClose={() => {
  setWizardOpen(false)
  reload()
}} />}
```

- [ ] **Step 18.4: Add wizard i18n keys**

en.json — add a `wizard` block:
```json
"wizard": {
  "title": "New group",
  "step": {
    "1": "Proxies",
    "2": "Create phones",
    "3": "Accounts",
    "4": "Login & warmup"
  },
  "s1": {
    "name": "Group name",
    "notes": "Notes (optional)",
    "proxies": "Proxies (one URL per line)",
    "check": "Validate proxies",
    "no_proxies": "Paste at least one proxy URL.",
    "no_name": "Group name is required.",
    "select_at_least_one": "Select at least one proxy to continue."
  }
}
```

zh.json:
```json
"wizard": {
  "title": "新建分组",
  "step": {
    "1": "代理",
    "2": "创建手机",
    "3": "账号",
    "4": "登录与暖机"
  },
  "s1": {
    "name": "分组名称",
    "notes": "备注（可选）",
    "proxies": "代理（每行一个 URL）",
    "check": "校验代理",
    "no_proxies": "请至少粘贴一行代理 URL。",
    "no_name": "分组名称必填。",
    "select_at_least_one": "请至少勾选一个代理继续。"
  }
}
```

Make sure `common.next` and `common.cancel` exist; add if missing.

- [ ] **Step 18.5: Build and smoke-test**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Verify in browser: click "New group", enter name, paste a proxy, click Validate, see 🟢/🔴 rows.

- [ ] **Step 18.6: Commit**

```bash
git add frontend/src/components/wizard frontend/src/pages/Groups.tsx \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(ui): group wizard shell + step 1 (proxies)"
```

---

## Task 19: Wizard Step 2 (Create phones, SSE)

**Files:**
- Create: `frontend/src/components/wizard/Step2Phones.tsx`
- Modify: `frontend/src/components/wizard/GroupWizard.tsx` (render Step 2)
- Modify: i18n

- [ ] **Step 19.1: Create Step 2 component**

Create `frontend/src/components/wizard/Step2Phones.tsx`:

```tsx
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { WizardState } from './GroupWizard'

export default function Step2Phones({
  state, patch, onNext, onBack,
}: {
  state: WizardState
  patch: (p: Partial<WizardState>) => void
  onNext: () => void
  onBack: () => void
}) {
  const { t } = useTranslation()
  const [log, setLog] = useState('')
  const [running, setRunning] = useState(false)
  const [done, setDone] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function start() {
    setRunning(true); setErr(null); setLog('')
    const r = await fetch(`/api/groups/${state.groupId}/phones/create`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxies: state.proxies }),
    })
    if (!r.ok) { setErr(await r.text()); setRunning(false); return }
    const reader = r.body!.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { value, done: streamDone } = await reader.read()
      if (streamDone) break
      buf += dec.decode(value, { stream: true })
      setLog(buf)
    }
    setRunning(false); setDone(true)
    // Pull fresh accounts for next step
    const r2 = await fetch(`/api/accounts`)
    if (r2.ok) {
      const all = await r2.json()
      const own = all.filter((a: any) => a.group_id === state.groupId)
      patch({ createdPhones: own.map((a: any) =>
                ({ id: a.id, name: a.profile_name })) })
    }
  }

  return (
    <div className="space-y-4">
      <div className="text-sm">
        {t('wizard.s2.summary', { count: state.proxies.length,
                                    name: state.name })}
      </div>
      <button onClick={start} disabled={running || done}
              className="px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-50">
        {running ? t('wizard.s2.running')
                  : done ? t('wizard.s2.done')
                          : t('wizard.s2.start')}
      </button>
      {log && (
        <pre className="border rounded p-2 text-xs whitespace-pre-wrap
                       max-h-64 overflow-auto bg-gray-50">{log}</pre>
      )}
      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex justify-between">
        <button onClick={onBack} className="px-3 py-1.5">
          ← {t('common.back')}
        </button>
        <button onClick={onNext} disabled={!done}
                className="px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-50">
          {t('common.next')} →
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 19.2: Render Step 2 in shell**

In `GroupWizard.tsx`, replace the `Step 2 — coming...` line with:

```tsx
import Step2Phones from './Step2Phones'
// ...
{step === 2 && (
  <Step2Phones state={state} patch={patch}
               onNext={() => setStep(3)} onBack={() => setStep(1)} />
)}
```

- [ ] **Step 19.3: Add i18n**

en `wizard`:
```json
"s2": {
  "summary": "Will create {{count}} phones for \"{{name}}\", each bound to one proxy.",
  "start": "Start",
  "running": "Creating phones...",
  "done": "Done"
}
```
zh:
```json
"s2": {
  "summary": "将为 \"{{name}}\" 创建 {{count}} 台手机，每台绑一个代理。",
  "start": "开始创建",
  "running": "正在创建...",
  "done": "完成"
}
```

Ensure `common.back` exists.

- [ ] **Step 19.4: Build and smoke-test**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

- [ ] **Step 19.5: Commit**

```bash
git add frontend/src/components/wizard \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(ui): wizard step 2 (create phones, SSE)"
```

---

## Task 20: Wizard Step 3 (Parse + assign accounts)

**Files:**
- Create: `frontend/src/components/wizard/Step3Accounts.tsx`
- Modify: `GroupWizard.tsx` (render Step 3)
- Modify: i18n

- [ ] **Step 20.1: Create Step 3 component**

Create `frontend/src/components/wizard/Step3Accounts.tsx`:

```tsx
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { WizardState } from './GroupWizard'

type Parsed = {
  email: string | null; password: string | null;
  tiktok_username: string | null; tiktok_password: string | null;
  recovery_email: string | null; mail_inbox_url: string | null;
  raw: string; warnings: string[]
}

const FIELDS: (keyof Parsed)[] = [
  'email', 'password', 'tiktok_username', 'tiktok_password',
  'recovery_email', 'mail_inbox_url',
]

export default function Step3Accounts({
  state, onNext, onBack,
}: {
  state: WizardState
  onNext: () => void
  onBack: () => void
}) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState<Parsed[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function doParse() {
    setErr(null)
    const r = await fetch(`/api/groups/${state.groupId}/accounts/parse`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!r.ok) { setErr(await r.text()); return }
    setParsed(await r.json())
  }

  function updateCell(idx: number, field: keyof Parsed, value: string) {
    if (!parsed) return
    const copy = parsed.slice()
    copy[idx] = { ...copy[idx], [field]: value || null }
    setParsed(copy)
  }

  function dropRow(idx: number) {
    if (!parsed) return
    setParsed(parsed.filter((_, i) => i !== idx))
  }

  async function commit() {
    if (!parsed) return
    setSubmitting(true)
    const r = await fetch(`/api/groups/${state.groupId}/accounts/assign`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accounts: parsed }),
    })
    setSubmitting(false)
    if (!r.ok) { setErr(await r.text()); return }
    onNext()
  }

  return (
    <div className="space-y-4">
      <label className="block text-sm font-medium">
        {t('wizard.s3.paste')}
      </label>
      <textarea
        className="border rounded w-full px-2 py-1 font-mono text-xs h-40"
        value={text}
        onChange={e => setText(e.target.value)}
      />
      <button onClick={doParse}
              className="px-3 py-1.5 rounded bg-gray-200">
        {t('wizard.s3.parse')}
      </button>

      {parsed && (
        <div className="border rounded overflow-auto max-h-96">
          <table className="text-xs w-full">
            <thead className="bg-gray-100 sticky top-0">
              <tr>
                <th>#</th>
                {FIELDS.map(f => <th key={f}>{f}</th>)}
                <th>warnings</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {parsed.map((row, i) => (
                <tr key={i} className="border-t">
                  <td className="px-1">{i + 1}</td>
                  {FIELDS.map(f => (
                    <td key={f} className="px-1">
                      <input
                        className="border-0 bg-transparent w-full focus:bg-white"
                        value={(row[f] as string) || ''}
                        onChange={e => updateCell(i, f, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="text-red-600 px-1">
                    {row.warnings.join('; ')}
                  </td>
                  <td>
                    <button onClick={() => dropRow(i)}
                            className="text-red-600">✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-xs text-gray-500 p-2">
            {t('wizard.s3.footer', {
              n: Math.min(parsed.length, state.createdPhones.length)
            })}
          </div>
        </div>
      )}

      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex justify-between">
        <button onClick={onBack} className="px-3 py-1.5">
          ← {t('common.back')}
        </button>
        <button onClick={commit} disabled={!parsed || submitting}
                className="px-3 py-1.5 rounded bg-blue-600 text-white disabled:opacity-50">
          {t('wizard.s3.confirm')} →
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 20.2: Render Step 3**

In `GroupWizard.tsx`:

```tsx
import Step3Accounts from './Step3Accounts'
// ...
{step === 3 && (
  <Step3Accounts state={state}
                  onNext={() => setStep(4)} onBack={() => setStep(2)} />
)}
```

- [ ] **Step 20.3: Add i18n**

en:
```json
"s3": {
  "paste": "Paste account credentials (one row per account, fields in any order)",
  "parse": "Parse",
  "confirm": "Confirm assignment",
  "footer": "Will assign to the first {{n}} phones (sorted by id) that have no credentials yet."
}
```
zh:
```json
"s3": {
  "paste": "粘贴账号凭证（一行一个账号，字段顺序任意）",
  "parse": "解析",
  "confirm": "确认分配",
  "footer": "将分配给该组前 {{n}} 台未指派凭证的手机（按 id 升序）。"
}
```

- [ ] **Step 20.4: Build & smoke-test**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

- [ ] **Step 20.5: Commit**

```bash
git add frontend/src/components/wizard \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(ui): wizard step 3 (parse + assign credentials)"
```

---

## Task 21: Wizard Step 4 (Mark logged-in + start warmup)

**Files:**
- Create: `frontend/src/components/wizard/Step4Login.tsx`
- Modify: `GroupWizard.tsx` (render Step 4 + final close)
- Modify: i18n

- [ ] **Step 21.1: Create Step 4 component**

Create `frontend/src/components/wizard/Step4Login.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { WizardState } from './GroupWizard'

type Acc = {
  id: number; profile_name: string;
  geelark_phone_id: string | null;
  email: string | null; tiktok_logged_in: number
}

export default function Step4Login({
  state, onClose,
}: { state: WizardState; onClose: () => void }) {
  const { t } = useTranslation()
  const nav = useNavigate()
  const [rows, setRows] = useState<Acc[]>([])
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [log, setLog] = useState('')
  const [warmRunning, setWarmRunning] = useState(false)
  const [committed, setCommitted] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function reload() {
    const r = await fetch('/api/accounts')
    if (!r.ok) return
    const all = await r.json()
    const own = all.filter((a: any) => a.group_id === state.groupId)
    setRows(own)
    setChecked(new Set(own.filter((a: any) => a.tiktok_logged_in)
                          .map((a: any) => a.id)))
  }
  useEffect(() => { reload() }, [])

  function toggle(id: number) {
    const n = new Set(checked)
    n.has(id) ? n.delete(id) : n.add(id)
    setChecked(n)
  }
  function selectAll() { setChecked(new Set(rows.map(r => r.id))) }
  function invert() {
    setChecked(new Set(rows.filter(r => !checked.has(r.id)).map(r => r.id)))
  }

  async function commit() {
    setErr(null)
    const r = await fetch('/api/accounts/bulk-mark-logged-in', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [...checked] }),
    })
    if (!r.ok) { setErr(await r.text()); return }
    setCommitted(true)
    reload()
  }

  async function startWarmup() {
    setWarmRunning(true); setLog('')
    const r = await fetch(`/api/groups/${state.groupId}/warmup/start`,
                          { method: 'POST' })
    if (!r.ok) { setErr(await r.text()); setWarmRunning(false); return }
    const reader = r.body!.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      setLog(buf)
    }
    setWarmRunning(false)
  }

  function finish() {
    onClose()
    nav(`/groups/${state.groupId}`)
  }

  return (
    <div className="space-y-4">
      <div className="text-sm text-gray-600">
        {t('wizard.s4.instructions')}
      </div>
      <div className="flex gap-2 text-xs">
        <button onClick={selectAll} className="px-2 py-1 bg-gray-200 rounded">
          {t('wizard.s4.select_all')}
        </button>
        <button onClick={invert} className="px-2 py-1 bg-gray-200 rounded">
          {t('wizard.s4.invert')}
        </button>
      </div>
      <div className="border rounded max-h-64 overflow-auto">
        <table className="text-xs w-full">
          <thead className="bg-gray-100 sticky top-0">
            <tr>
              <th></th>
              <th>profile_name</th>
              <th>phone_id</th>
              <th>email</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="border-t">
                <td>
                  <input type="checkbox" checked={checked.has(r.id)}
                         onChange={() => toggle(r.id)} />
                </td>
                <td>{r.profile_name}</td>
                <td><code>{r.geelark_phone_id}</code></td>
                <td>{r.email}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2">
        <button onClick={commit}
                className="px-3 py-1.5 rounded bg-blue-600 text-white">
          {t('wizard.s4.confirm_login')}
        </button>
        <button onClick={startWarmup} disabled={!committed || warmRunning}
                className="px-3 py-1.5 rounded bg-green-600 text-white disabled:opacity-50">
          ▶ {warmRunning ? t('wizard.s4.warming') : t('wizard.s4.start_warmup')}
        </button>
      </div>
      {log && (
        <pre className="border rounded p-2 text-xs whitespace-pre-wrap
                       max-h-32 overflow-auto bg-gray-50">{log}</pre>
      )}
      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex justify-end">
        <button onClick={finish}
                className="px-3 py-1.5 rounded bg-gray-200">
          {t('wizard.s4.finish')}
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 21.2: Render Step 4 in shell**

In `GroupWizard.tsx`:

```tsx
import Step4Login from './Step4Login'
// ...
{step === 4 && (
  <Step4Login state={state} onClose={onClose} />
)}
```

- [ ] **Step 21.3: Add i18n**

en:
```json
"s4": {
  "instructions": "Log in on each phone using the GeeLark desktop app, then check the boxes here.",
  "select_all": "Select all",
  "invert": "Invert",
  "confirm_login": "Confirm logged-in status",
  "start_warmup": "Start warmup now",
  "warming": "Warming up...",
  "finish": "Finish"
}
```
zh:
```json
"s4": {
  "instructions": "请到 GeeLark 桌面端逐台登录，然后在此处勾选已登录的账号。",
  "select_all": "全选",
  "invert": "反选",
  "confirm_login": "确认登录状态",
  "start_warmup": "立即启动暖机",
  "warming": "暖机中...",
  "finish": "完成"
}
```

- [ ] **Step 21.4: Build & smoke-test full wizard**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Walk through all 4 steps end-to-end against a dev backend (fake GeeLark client is fine).

- [ ] **Step 21.5: Commit**

```bash
git add frontend/src/components/wizard \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(ui): wizard step 4 (mark logged-in + start warmup)"
```

---

## Task 22: Accounts + Videos pages — group filter chips (group_id)

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx` (existing `group_name`-based chips → `group_id`)
- Modify: `frontend/src/pages/Videos.tsx` (new chip strip + required group on upload + drive import)
- Modify: i18n

**Existing state (already inspected):**
- `Accounts.tsx:32` — `Account` type has `group_name: string | null`. We add `group_id: number | null` to it.
- `Accounts.tsx:472-498` — chip strip already exists, derived from `rows[*].group_name`. The chip values are group-name strings keyed by `ALL_GROUPS = '__all__'`.
- `Videos.tsx` — has no chip strip; the upload `FormData` is built around `frontend/src/pages/Videos.tsx:196`.

- [ ] **Step 22.1: Update `Account` type in `Accounts.tsx`**

Find the `Account` type declaration (around line 30) and add `group_id`:

```tsx
type Account = {
  // ... existing fields ...
  group_name: string | null
  group_id: number | null
  // ... rest ...
}
```

- [ ] **Step 22.2: Switch the existing chip strip to use `group_id`**

Replace the chip-deriving block (around lines 472-498) with a version that fetches `/api/groups` and uses ids:

```tsx
const [groups, setGroups] = useState<{id:number; name:string}[]>([])
useEffect(() => {
  fetch('/api/groups').then(r => r.ok ? r.json() : []).then(setGroups)
                       .catch(() => {})
}, [])

const ALL_GROUPS = -1
const [groupFilter, setGroupFilter] = useState<number>(ALL_GROUPS)

const visibleRows = groupFilter === ALL_GROUPS
  ? rows
  : rows.filter(r => r.group_id === groupFilter)
```

Update the chip JSX to render `groups` instead of `groupCounts`:

```tsx
<div className="flex gap-2 flex-wrap mb-2 text-xs">
  <button onClick={() => setGroupFilter(ALL_GROUPS)}
          className={'px-2 py-1 rounded ' + (groupFilter === ALL_GROUPS
            ? 'bg-blue-600 text-white' : 'bg-gray-200')}>
    {t('common.all')}
  </button>
  {groups.map(g => (
    <button key={g.id} onClick={() => setGroupFilter(g.id)}
            className={'px-2 py-1 rounded ' + (groupFilter === g.id
              ? 'bg-blue-600 text-white' : 'bg-gray-200')}>
      {g.name}
    </button>
  ))}
</div>
```

Use `visibleRows` wherever the table previously iterated `rows` (or whatever name the existing filtered variable had — search the file for the existing chip-applied variable, e.g. `filteredRows`, and replace its assignment with `visibleRows` above).

- [ ] **Step 22.3: Add chip strip + required group to `Videos.tsx`**

At the top of the component (after the existing `useState` block), add:

```tsx
const [groups, setGroups] = useState<{id:number; name:string}[]>([])
useEffect(() => {
  fetch('/api/groups').then(r => r.ok ? r.json() : []).then(setGroups)
                       .catch(() => {})
}, [])
const ALL_GROUPS = -1
const [groupFilter, setGroupFilter] = useState<number>(ALL_GROUPS)
const [uploadGroup, setUploadGroup] = useState<number | ''>('')
const [importGroup, setImportGroup] = useState<number | ''>('')

const visibleVids = groupFilter === ALL_GROUPS
  ? vids
  : vids.filter(v => (v as any).group_id === groupFilter)
```

Render the chip strip above the videos table (mirror the markup from Step 22.2). Use `visibleVids` in the table body.

Find the `FormData` construction at `Videos.tsx:196` (the upload flow). Append `group_id` before the request goes out:

```ts
const fd = new FormData()
for (const f of files) fd.append('files', f)
if (uploadGroup !== '') fd.append('group_id', String(uploadGroup))
```

Find the upload trigger UI (file input / drag-drop submit button). Add a required `<select>` next to it:

```tsx
<select value={uploadGroup}
        onChange={e => setUploadGroup(
          e.target.value ? parseInt(e.target.value) : '')}
        className="border rounded px-2 py-1 text-sm">
  <option value="">{t('videos.upload.pick_group')}</option>
  {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
</select>
```

Disable the upload submit button when `uploadGroup === ''`.

For the drive-import dialog (`importStep === 'pick'`), add the same `<select>` bound to `importGroup`, and include `group_id` in the request body sent to `/api/videos/import`:

```ts
body: JSON.stringify({
  files: pickedFiles,
  group_id: importGroup === '' ? null : importGroup,
}),
```

Disable the "Start import" button when `importGroup === ''`.

- [ ] **Step 22.4: Add i18n**

en `common`: `"all": "All"`
en `videos.upload`: `"pick_group": "Target group"`

zh `common`: `"all": "全部"`
zh `videos.upload`: `"pick_group": "目标分组"`

(Use existing block paths if these keys already exist; otherwise add at the appropriate nesting.)

- [ ] **Step 22.5: Build & smoke-test**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Verify in browser: chips work, can't upload a video without selecting a group, drive import requires group too.

- [ ] **Step 22.6: Commit**

```bash
git add frontend/src/pages/Accounts.tsx frontend/src/pages/Videos.tsx \
        frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(ui): group filter chips on Accounts/Videos + required group on upload"
```

---

## Task 23: Full-suite verification + run-script sanity check

**Files:** none (verification)

- [ ] **Step 23.1: Run the entire backend test suite**

```bash
./.venv/bin/pytest -q
```

Expected: original 38 + new tests ≈ 60+ passed, 0 failed.

- [ ] **Step 23.2: Build frontend from scratch**

```bash
export PATH="/opt/homebrew/bin:$PATH"
rm -rf frontend/dist
cd frontend && npm run build
```

Expected: build succeeds, no TS errors.

- [ ] **Step 23.3: Start the app and walk through the wizard**

```bash
./run.sh
```

In browser, open `/groups`:
1. Click `New group`
2. Step 1: name + paste 1-2 proxies + Validate + Next
3. Step 2: Start → watch SSE log → Next
4. Step 3: paste a few `email pw` lines → Parse → Confirm
5. Step 4: check all rows → Confirm logged-in → Start warmup → Finish
6. Land on Group detail page → counts populated correctly

If anything fails, fix inline and re-run tests for the affected area before continuing.

- [ ] **Step 23.4: Final commit (only if any docs/fixes needed)**

If you made any micro-fixes:

```bash
git add -A
git commit -m "chore: end-to-end wizard verification fixes"
```

---

## Self-Review Notes

- Spec sections all map to tasks:
  - "Data model" → Task 1
  - "Backend services" → Tasks 4–10
  - "API" → Tasks 11–14
  - "Frontend" → Tasks 16–22
  - "Testing" → embedded in each task (TDD)
  - "Migration" → Task 1 (`_migrate` extension + backfill)
  - "Implementation order" in spec is preserved across Tasks 1→22
- No placeholders in code blocks; every step shows the actual change
- Type names consistent: `ParsedAccount` in service + `Parsed` (alias) in UI; `GroupRow`/`Group` separate (list vs detail)
- `groups` table only created via `SCHEMA` (idempotent CREATE), not via _migrate — but migration depends on it existing, so SCHEMA must execute before _migrate. Existing `init_schema` already runs `executescript(SCHEMA)` then `_migrate(conn)` in that order — confirmed safe.
- Front-end strings live in i18n (English + Chinese both updated each task) per CLAUDE.md note that the dashboard already uses i18n.
