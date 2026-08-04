# Multi-Platform Auto-Publish — Implementation Plan (PG + Procrastinate stack)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add YouTube Shorts, Instagram Reels and Facebook Reels publishing via GeeLark RPA, on top of the Phase 1 (PostgreSQL + SQLModel + Alembic) and Phase 2 (Procrastinate worker) stack.

**Architecture:** A `publications(video_id, platform, ...)` SQLModel table holds the per-platform publishing state. Existing TikTok rows on `Video` are migrated into `publications` via an Alembic revision; the old `Video.account_id / result / geelark_task_id / notes / posted_at / last_checked_at` columns are dropped. `Account` gains nine new columns (three per new platform). `posting_service` dispatches by platform string to four `geelark_client` methods. A new periodic Procrastinate task `auto_publish_scan_task` runs hourly, picks eligible publications, marks them QUEUED, and defers one `publish_publication_task(publication_id)` each. The per-publication task submits to GeeLark, then chains `check_publication_status_task` with 30s `schedule_in` (mirrors phase-2 pattern). Frontend gains a four-way platform multi-select on upload and three new YT/IG/FB columns on the Accounts page.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, SQLAlchemy 2 + psycopg3, PostgreSQL, Alembic, Procrastinate, pytest, React 19 + TypeScript + Vite.

**Spec:** `docs/superpowers/specs/2026-05-28-multi-platform-publish-design.md`

**Status:** Task 1 (`app/services/platforms.py`) is already committed on `feat/multi-platform-publish` as `3d74a8e`. This plan covers tasks 2–13.

**Pre-flight (do once before Task 2):**

```bash
# (Optional) backup the PG dev DB if you've been seeding data
pg_dump -U "$USER" -d mkt_dev > /tmp/mkt_dev.before-multi-platform.sql 2>/dev/null || true

# Confirm baseline is green
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest -q
# expect: 292 passed, 20 skipped

# Confirm you're on the right branch
git branch --show-current
# expect: feat/multi-platform-publish
```

---

### Task 2: SQLModel models + Alembic revision

**Files:**
- Modify: `app/models.py`
- Create: `alembic/versions/<new_revision>_publications_and_platform_columns.py`
- Modify: `tests/test_repositories.py` (will be extended in Task 4; here only the migration test runs)
- Create: `tests/test_migration_publications.py`

- [ ] **Step 2.1: Edit `app/models.py` — Account columns**

Open `app/models.py`. Find the `class Account(SQLModel, table=True):` block. After the `last_posted_at` field, add nine new fields (grouped by platform, two blank lines between groups for readability):

```python
    # YouTube (added 2026-05-28 multi-platform)
    youtube_logged_in: int = Field(default=0, nullable=False)
    youtube_username: Optional[str] = None
    last_posted_at_youtube: Optional[datetime] = None

    # Instagram (added 2026-05-28 multi-platform)
    instagram_logged_in: int = Field(default=0, nullable=False)
    instagram_username: Optional[str] = None
    last_posted_at_instagram: Optional[datetime] = None

    # Facebook (added 2026-05-28 multi-platform)
    facebook_logged_in: int = Field(default=0, nullable=False)
    facebook_username: Optional[str] = None
    last_posted_at_facebook: Optional[datetime] = None
```

If `datetime` and `Optional` are not yet imported at the top of the file, add the imports.

- [ ] **Step 2.2: Edit `app/models.py` — strip Video, add Publication**

Inside the `class Video(SQLModel, table=True):` block, DELETE these fields (they'll move to publications):

```
account_id, result, geelark_task_id, notes, posted_at, last_checked_at
```

The remaining `Video` should have only: `id`, `original_filename`, `stored_path`, `caption`, `drive_file_id`, `uploaded_at`, `group_id`.

Then, immediately after the `Video` class block, add the new `Publication` class:

```python
class Publication(SQLModel, table=True):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("video_id", "platform",
                          name="uq_publications_video_platform"),
        Index("idx_publications_status", "result", "platform"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(
        sa_column=Column(Integer,
                          ForeignKey("videos.id", ondelete="CASCADE"),
                          nullable=False))
    platform: str = Field(nullable=False)
    account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer,
                          ForeignKey("accounts.id", ondelete="SET NULL"),
                          nullable=True))
    result: str = Field(default="PENDING", nullable=False)
    caption: Optional[str] = None
    geelark_task_id: Optional[str] = None
    notes: Optional[str] = None
    posted_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(
        sa_column=Column(DateTime,
                          server_default=text("CURRENT_TIMESTAMP"),
                          nullable=False))
```

At the top of `app/models.py`, make sure these are imported (extend the existing import block):

```python
from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer,
                        UniqueConstraint, text)
```

- [ ] **Step 2.3: Generate a blank Alembic revision**

```bash
uv run alembic revision -m "publications table and platform columns"
```

This creates `alembic/versions/<short_hash>_publications_table_and_platform_columns.py` with empty `upgrade()` and `downgrade()` stubs and the right `down_revision` chained from the latest existing revision (should be `d7279ba4a07f` from the procrastinate schema). Open the new file.

- [ ] **Step 2.4: Fill in `upgrade()` and `downgrade()`**

Replace the body of the new revision file. Imports at top:

```python
"""publications table and platform columns

Revision ID: <leave as generated>
Revises: d7279ba4a07f
Create Date: <leave as generated>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic. (LEAVE as generated)
revision: str = "<generated>"
down_revision: Union[str, None] = "d7279ba4a07f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AS = sqlmodel.sql.sqltypes.AutoString
```

`upgrade()`:

```python
def upgrade() -> None:
    # 1) Nine new account columns.
    for col in ("youtube_logged_in", "instagram_logged_in",
                "facebook_logged_in"):
        op.add_column("accounts",
            sa.Column(col, sa.Integer(), nullable=False,
                       server_default="0"))
    for col in ("youtube_username", "instagram_username",
                "facebook_username"):
        op.add_column("accounts", sa.Column(col, _AS(), nullable=True))
    for col in ("last_posted_at_youtube", "last_posted_at_instagram",
                "last_posted_at_facebook"):
        op.add_column("accounts", sa.Column(col, sa.DateTime(),
                                              nullable=True))

    # 2) publications table + UNIQUE + INDEX.
    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(),
                   sa.ForeignKey("videos.id", ondelete="CASCADE"),
                   nullable=False),
        sa.Column("platform", _AS(), nullable=False),
        sa.Column("account_id", sa.Integer(),
                   sa.ForeignKey("accounts.id", ondelete="SET NULL"),
                   nullable=True),
        sa.Column("result", _AS(), nullable=False,
                   server_default="PENDING"),
        sa.Column("caption", _AS(), nullable=True),
        sa.Column("geelark_task_id", _AS(), nullable=True),
        sa.Column("notes", _AS(), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                   server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("video_id", "platform",
                              name="uq_publications_video_platform"),
    )
    op.create_index("idx_publications_status", "publications",
                     ["result", "platform"])

    # 3) Backfill TikTok rows from videos into publications.
    op.execute("""
        INSERT INTO publications
          (video_id, platform, account_id, result, caption,
           geelark_task_id, notes, posted_at, last_checked_at, created_at)
        SELECT id, 'tiktok', account_id, COALESCE(result, 'PENDING'),
               caption, geelark_task_id, notes, posted_at, last_checked_at,
               CURRENT_TIMESTAMP
        FROM videos
    """)

    # 4) Drop now-redundant videos columns.
    for col in ("last_checked_at", "posted_at", "notes",
                "geelark_task_id", "result", "account_id"):
        op.drop_column("videos", col)
```

`downgrade()`:

```python
def downgrade() -> None:
    op.add_column("videos",
        sa.Column("account_id", sa.Integer(), nullable=True))
    op.add_column("videos",
        sa.Column("result", _AS(), nullable=True,
                   server_default="PENDING"))
    op.add_column("videos",
        sa.Column("geelark_task_id", _AS(), nullable=True))
    op.add_column("videos",
        sa.Column("notes", _AS(), nullable=True))
    op.add_column("videos",
        sa.Column("posted_at", sa.DateTime(), nullable=True))
    op.add_column("videos",
        sa.Column("last_checked_at", sa.DateTime(), nullable=True))
    op.execute("""
        UPDATE videos v SET
          account_id = p.account_id,
          result = p.result,
          geelark_task_id = p.geelark_task_id,
          notes = p.notes,
          posted_at = p.posted_at,
          last_checked_at = p.last_checked_at
        FROM publications p
        WHERE p.video_id = v.id AND p.platform = 'tiktok'
    """)
    op.drop_index("idx_publications_status", table_name="publications")
    op.drop_table("publications")
    for col in ("last_posted_at_facebook", "last_posted_at_instagram",
                "last_posted_at_youtube",
                "facebook_username", "instagram_username", "youtube_username",
                "facebook_logged_in", "instagram_logged_in",
                "youtube_logged_in"):
        op.drop_column("accounts", col)
```

- [ ] **Step 2.5: Apply the migration locally and re-create the test DB**

`tests/conftest.py::_create_schema` uses `SQLModel.metadata.create_all`,
not Alembic. So for the test DB we only need the models to match; the
migration itself runs against `mkt_dev` (or whatever your interactive
DB is). Run:

```bash
# Apply to dev DB
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_dev \
  uv run alembic upgrade head

# Truncate + recreate test DB schema via conftest fixture
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run python -c "
from app.db import engine
from sqlmodel import SQLModel
# Drop everything (PG-safe) then re-create from current models
import sqlalchemy as sa
with engine.begin() as conn:
    conn.execute(sa.text(
      'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'))
SQLModel.metadata.create_all(engine)
print('OK')
"
```

Expected: no errors. `psql mkt_dev -c '\\d publications'` shows the new
table with FK constraints. `\\d accounts` shows nine new columns.

- [ ] **Step 2.6: Write a migration round-trip test**

Create `tests/test_migration_publications.py`:

```python
"""Verify the publications-and-platform-columns migration round-trips
cleanly: upgrade head -> all columns and table present; downgrade -1
-> publications gone, videos columns restored."""
import os
import pytest
from alembic import command
from alembic.config import Config

# Use the dev DB explicitly — this test mutates schema and shouldn't
# run against mkt_test (which truncates per-test).
DEV_URL = os.environ.get(
    "ALEMBIC_TEST_URL",
    "postgresql+psycopg://"+os.environ["USER"]+"@127.0.0.1:5432/mkt_dev_alembic")


def _cfg():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DEV_URL)
    return cfg


def _has_table(engine, name):
    import sqlalchemy as sa
    with engine.connect() as c:
        return c.execute(sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name=:n"), {"n": name}).first() is not None


def _has_column(engine, table, column):
    import sqlalchemy as sa
    with engine.connect() as c:
        return c.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=:t AND column_name=:c"),
            {"t": table, "c": column}).first() is not None


@pytest.fixture
def alembic_engine():
    """Fresh DB, run migrations to head once."""
    from sqlalchemy import create_engine
    import sqlalchemy as sa
    # Setup: drop+create the dedicated alembic test db
    admin = create_engine(
        DEV_URL.rsplit("/", 1)[0] + "/postgres",
        isolation_level="AUTOCOMMIT")
    db_name = DEV_URL.rsplit("/", 1)[1]
    with admin.connect() as c:
        c.execute(sa.text(
          f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
        c.execute(sa.text(f"CREATE DATABASE {db_name}"))
    admin.dispose()
    engine = create_engine(DEV_URL)
    command.upgrade(_cfg(), "head")
    yield engine
    engine.dispose()


def test_upgrade_head_creates_publications(alembic_engine):
    assert _has_table(alembic_engine, "publications")
    for col in ("youtube_logged_in", "youtube_username",
                "last_posted_at_youtube",
                "instagram_logged_in", "instagram_username",
                "last_posted_at_instagram",
                "facebook_logged_in", "facebook_username",
                "last_posted_at_facebook"):
        assert _has_column(alembic_engine, "accounts", col), col
    for dropped in ("result", "account_id", "geelark_task_id",
                    "notes", "posted_at", "last_checked_at"):
        assert not _has_column(alembic_engine, "videos", dropped), dropped


def test_downgrade_one_restores_videos_columns(alembic_engine):
    command.downgrade(_cfg(), "-1")
    assert not _has_table(alembic_engine, "publications")
    for restored in ("result", "account_id", "geelark_task_id",
                     "notes", "posted_at", "last_checked_at"):
        assert _has_column(alembic_engine, "videos", restored), restored


def test_idempotent_upgrade_then_downgrade_then_upgrade(alembic_engine):
    command.downgrade(_cfg(), "-1")
    command.upgrade(_cfg(), "head")
    assert _has_table(alembic_engine, "publications")
```

Note: this test creates its OWN database `mkt_dev_alembic` (separate
from `mkt_test`) so the test isn't polluted by conftest's
`_truncate_tables`. If creating a DB requires superuser privileges
locally, gate the test with
`pytest.importorskip("psycopg")` plus a `pytest.skip` if `CREATE
DATABASE` fails.

- [ ] **Step 2.7: Run tests; expect the migration test green and a fresh `292+3` baseline**

```bash
# Models changed but most tests don't read the dropped Video columns directly.
# Some test_services / test_repositories tests will fail because they
# still write Video.result / Video.account_id / etc. That's expected
# and gets fixed in Tasks 6 / 7. Just verify the migration tests pass.

DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_migration_publications.py tests/test_platforms.py -v
# expect: 4 passed (3 migration + first platforms test) or similar

DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest -q
# expect: ~30 failures spread across test_repositories / test_services /
# test_api / test_db, because they reference dropped Video columns.
# That's expected; we fix them in Tasks 3, 4, 6, 7, 9.
```

- [ ] **Step 2.8: Commit**

```bash
git add app/models.py alembic/versions/*_publications_table_and_platform_columns.py \
        tests/test_migration_publications.py
git commit -m "feat(db): publications table + platform columns via Alembic

New SQLModel Publication carries per-platform publishing state, keyed
by (video_id, platform) UNIQUE. Account gains nine columns (three per
new platform). Video loses account_id/result/geelark_task_id/notes/
posted_at/last_checked_at; existing TikTok state is migrated into
publications.

Spec: docs/superpowers/specs/2026-05-28-multi-platform-publish-design.md"
```

---

### Task 3: publications repository

**Files:**
- Create: `app/repositories/publications.py`
- Create: `tests/test_repositories_publications.py`

- [ ] **Step 3.1: Write failing tests**

Create `tests/test_repositories_publications.py`:

```python
"""Publications repository tests. Uses the conftest `session` fixture
(PG mkt_test, truncated before each test) and `_truncate_tables` autouse."""
from app.repositories import accounts as acc_repo
from app.repositories import videos as vid_repo
from app.repositories import publications as pub_repo


def _seed(session):
    aid = acc_repo.create(session, {"profile_name": "A1",
                                     "geelark_phone_id": "ph1"})
    vid = vid_repo.create(session, "v.mp4", "/x/v.mp4")
    return aid, vid


def test_create_and_list_for_video(session):
    _, vid = _seed(session)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    assert pid > 0
    rows = pub_repo.list_for_video(session, vid)
    assert len(rows) == 1
    assert rows[0]["platform"] == "tiktok"
    assert rows[0]["result"] == "PENDING"


def test_unique_video_id_platform(session):
    from sqlalchemy.exc import IntegrityError
    _, vid = _seed(session)
    pub_repo.create(session, video_id=vid, platform="tiktok")
    try:
        pub_repo.create(session, video_id=vid, platform="tiktok")
    except IntegrityError:
        session.rollback()
        return
    assert False, "expected IntegrityError on duplicate (video_id, platform)"


def test_create_for_each_platform(session):
    _, vid = _seed(session)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pub_repo.create(session, video_id=vid, platform=plat)
    rows = pub_repo.list_for_video(session, vid)
    assert sorted(r["platform"] for r in rows) == \
        ["facebook", "instagram", "tiktok", "youtube"]


def test_mark_initiated_and_set_result(session):
    aid, vid = _seed(session)
    pid = pub_repo.create(session, video_id=vid, platform="instagram")
    pub_repo.mark_initiated(session, pid, account_id=aid, caption="cap",
                             task_id="ig1", notes="submitted")
    row = pub_repo.get(session, pid)
    assert row["result"] == "INITIATED"
    assert row["account_id"] == aid
    assert row["geelark_task_id"] == "ig1"
    assert row["posted_at"] is not None
    pub_repo.set_result(session, pid, "SUCCESS", "done")
    row = pub_repo.get(session, pid)
    assert row["result"] == "SUCCESS"
    assert row["last_checked_at"] is not None


def test_mark_failure(session):
    aid, vid = _seed(session)
    pid = pub_repo.create(session, video_id=vid, platform="facebook")
    pub_repo.mark_failure(session, pid, account_id=aid, notes="upload failed")
    row = pub_repo.get(session, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == "upload failed"


def test_mark_queued(session):
    aid, vid = _seed(session)
    pid = pub_repo.create(session, video_id=vid, platform="youtube")
    pub_repo.mark_queued(session, pid, account_id=aid)
    row = pub_repo.get(session, pid)
    assert row["result"] == "QUEUED"
    assert row["account_id"] == aid


def test_list_pending_filtered_by_platform(session):
    _, vid = _seed(session)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pub_repo.create(session, video_id=vid, platform=plat)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        rows = pub_repo.list_pending(session, platform=plat)
        assert len(rows) == 1
        assert rows[0]["platform"] == plat
        # JOIN videos: stored_path comes through
        assert rows[0]["stored_path"] == "/x/v.mp4"
        assert rows[0]["original_filename"] == "v.mp4"


def test_list_initiated_returns_all_platforms(session):
    aid, vid = _seed(session)
    pids = {}
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pids[plat] = pub_repo.create(session, video_id=vid, platform=plat)
    pub_repo.mark_initiated(session, pids["tiktok"], aid, "c", "tk1", "n")
    pub_repo.mark_initiated(session, pids["facebook"], aid, "c", "fb1", "n")
    rows = pub_repo.list_initiated(session)
    assert sorted(r["id"] for r in rows) == sorted([pids["tiktok"],
                                                     pids["facebook"]])


def test_cascade_delete_when_video_deleted(session):
    _, vid = _seed(session)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pub_repo.create(session, video_id=vid, platform=plat)
    vid_repo.delete(session, vid)
    assert pub_repo.list_for_video(session, vid) == []


def test_set_account_null_on_account_delete(session):
    aid, vid = _seed(session)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, aid, "c", "t", "n")
    acc_repo.delete(session, aid)
    row = pub_repo.get(session, pid)
    assert row is not None
    assert row["account_id"] is None


def test_video_ids_all_terminal(session):
    _, v1 = _seed(session)
    v2 = vid_repo.create(session, "v2.mp4", "/x/v2.mp4")
    v3 = vid_repo.create(session, "v3.mp4", "/x/v3.mp4")  # no publications
    aid = acc_repo.create(session, {"profile_name": "B",
                                     "geelark_phone_id": "ph2"})
    p1 = pub_repo.create(session, video_id=v1, platform="tiktok")
    p2 = pub_repo.create(session, video_id=v1, platform="youtube")
    p3 = pub_repo.create(session, video_id=v2, platform="tiktok")
    pub_repo.set_result(session, p1, "SUCCESS", "ok")
    pub_repo.set_result(session, p2, "FAILURE", "err")
    pub_repo.mark_initiated(session, p3, aid, "c", "tk", "n")
    terminal = pub_repo.video_ids_all_terminal(session)
    assert v1 in terminal
    assert v2 not in terminal
    assert v3 not in terminal
```

- [ ] **Step 3.2: Run to verify failure**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_repositories_publications.py -v
```

Expected: ImportError — module doesn't exist yet.

- [ ] **Step 3.3: Implement `app/repositories/publications.py`**

```python
"""Publications repository — one row per (video_id, platform) publishing
action. See docs/superpowers/specs/2026-05-28-multi-platform-publish-design.md."""
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlmodel import Session

from app.models import Publication, Video


def create(session: Session, *, video_id: int, platform: str) -> int:
    pub = Publication(video_id=video_id, platform=platform)
    session.add(pub)
    session.commit()
    session.refresh(pub)
    return pub.id


def get(session: Session, pub_id: int) -> Optional[dict]:
    p = session.get(Publication, pub_id)
    return p.model_dump() if p else None


def list_for_video(session: Session, video_id: int) -> list[dict]:
    rows = session.exec(
        select(Publication)
        .where(Publication.video_id == video_id)
        .order_by(Publication.platform)
    ).scalars().all()
    return [p.model_dump() for p in rows]


def list_pending(session: Session, platform: str) -> list[dict]:
    """PENDING publications joined to Video for stored_path / filename / group_id."""
    rows = session.exec(
        select(Publication, Video.stored_path, Video.original_filename,
                Video.group_id)
        .join(Video, Video.id == Publication.video_id)
        .where(Publication.result == "PENDING")
        .where(Publication.platform == platform)
        .order_by(Publication.id)
    ).all()
    out = []
    for pub, stored_path, filename, group_id in rows:
        d = pub.model_dump()
        d["stored_path"] = stored_path
        d["original_filename"] = filename
        d["group_id"] = group_id
        out.append(d)
    return out


def list_initiated(session: Session) -> list[dict]:
    """INITIATED publications joined to Video for stored_path."""
    rows = session.exec(
        select(Publication, Video.stored_path)
        .join(Video, Video.id == Publication.video_id)
        .where(Publication.result == "INITIATED")
        .where(Publication.geelark_task_id.isnot(None))
        .order_by(Publication.id)
    ).all()
    out = []
    for pub, stored_path in rows:
        d = pub.model_dump()
        d["stored_path"] = stored_path
        out.append(d)
    return out


def mark_queued(session: Session, pub_id: int, account_id: int) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.result = "QUEUED"
    pub.account_id = account_id
    session.add(pub)
    session.commit()


def mark_initiated(session: Session, pub_id: int, account_id: int,
                   caption: str, task_id: str, notes: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.result = "INITIATED"
    pub.account_id = account_id
    pub.caption = caption
    pub.geelark_task_id = task_id
    pub.notes = notes
    pub.posted_at = datetime.utcnow()
    session.add(pub)
    session.commit()


def mark_failure(session: Session, pub_id: int, account_id: int,
                 notes: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.result = "FAILURE"
    pub.account_id = account_id
    pub.notes = notes
    pub.posted_at = datetime.utcnow()
    session.add(pub)
    session.commit()


def set_result(session: Session, pub_id: int, result: str,
               notes: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.result = result
    pub.notes = notes
    pub.last_checked_at = datetime.utcnow()
    session.add(pub)
    session.commit()


def video_ids_all_terminal(session: Session) -> set[int]:
    """Video IDs where at least one publication exists and none are
    PENDING/QUEUED/INITIATED. Used to gate local stored_path cleanup."""
    import sqlalchemy as sa
    rows = session.exec(sa.text("""
        SELECT v.id FROM videos v
        WHERE v.stored_path IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM publications p WHERE p.video_id = v.id
          )
          AND NOT EXISTS (
            SELECT 1 FROM publications p
            WHERE p.video_id = v.id
              AND p.result IN ('PENDING','QUEUED','INITIATED')
          )
    """)).all()
    return {r[0] for r in rows}
```

- [ ] **Step 3.4: Run to verify pass**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_repositories_publications.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add app/repositories/publications.py tests/test_repositories_publications.py
git commit -m "feat(repo): publications CRUD with PENDING/QUEUED/INITIATED transitions"
```

---

### Task 4: accounts repo — touch_last_posted + new columns

**Files:**
- Modify: `app/repositories/accounts.py`
- Modify: `tests/test_repositories.py`

- [ ] **Step 4.1: Write failing test**

Append to `tests/test_repositories.py`:

```python
def test_touch_last_posted_per_platform(session):
    from app.repositories import accounts as ar
    aid = ar.create(session, {"profile_name": "TP-multi"})
    expected_col = {
        "tiktok":    "last_posted_at",
        "youtube":   "last_posted_at_youtube",
        "instagram": "last_posted_at_instagram",
        "facebook":  "last_posted_at_facebook",
    }
    for plat, col in expected_col.items():
        ar.touch_last_posted(session, aid, plat)
        row = ar.get(session, aid)
        assert row[col] is not None, f"touch for {plat} did not set {col}"


def test_touch_last_posted_rejects_unknown_platform(session):
    import pytest
    from app.repositories import accounts as ar
    aid = ar.create(session, {"profile_name": "TPZ"})
    with pytest.raises(ValueError):
        ar.touch_last_posted(session, aid, "linkedin")


def test_accounts_columns_roundtrip_all_platform_fields(session):
    from app.repositories import accounts as ar
    payload = {"profile_name": "MULTI"}
    for plat in ("youtube", "instagram", "facebook"):
        payload[f"{plat}_logged_in"] = 1
        payload[f"{plat}_username"] = f"@{plat}user"
    aid = ar.create(session, payload)
    row = ar.get(session, aid)
    for plat in ("youtube", "instagram", "facebook"):
        assert row[f"{plat}_logged_in"] == 1
        assert row[f"{plat}_username"] == f"@{plat}user"
```

- [ ] **Step 4.2: Run to verify failure**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_repositories.py::test_touch_last_posted_per_platform \
                tests/test_repositories.py::test_touch_last_posted_rejects_unknown_platform \
                tests/test_repositories.py::test_accounts_columns_roundtrip_all_platform_fields -v
```

Expected: FAIL — `touch_last_posted` undefined.

- [ ] **Step 4.3: Implement**

Open `app/repositories/accounts.py`. The existing SQLModel-style `create` and `update` rely on `Account.__fields__` (or similar) to filter writable columns — verify by reading the file. If they use a `_COLUMNS` set, extend it with the nine new fields:

```python
_COLUMNS |= {
    "youtube_logged_in", "youtube_username", "last_posted_at_youtube",
    "instagram_logged_in", "instagram_username", "last_posted_at_instagram",
    "facebook_logged_in", "facebook_username", "last_posted_at_facebook",
}
```

If the repo uses dynamic field reflection (no explicit set), nothing
to add — SQLModel will pick them up from the model.

Then append:

```python
def touch_last_posted(session: Session, account_id: int,
                      platform: str) -> None:
    """Stamp the per-platform last-posted timestamp on an account."""
    from datetime import datetime
    from app.services.platforms import LAST_POSTED_COL, validate
    from app.models import Account

    col = LAST_POSTED_COL[validate(platform)]
    acc = session.get(Account, account_id)
    if acc is None:
        return
    setattr(acc, col, datetime.utcnow())
    acc.updated_at = datetime.utcnow()
    session.add(acc)
    session.commit()
```

- [ ] **Step 4.4: Run to verify pass**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_repositories.py -v
```

Expected: all `test_repositories.py` tests still pass, including the 3 new ones. Existing tests that read `Video.account_id` / `Video.result` etc. WILL STILL FAIL — that's Task 6's problem.

- [ ] **Step 4.5: Commit**

```bash
git add app/repositories/accounts.py tests/test_repositories.py
git commit -m "feat(accounts): touch_last_posted + 9 new platform columns roundtrip"
```

---

### Task 5: geelark_client — three new RPA methods

**Files:**
- Modify: `geelark_client.py`
- Modify: `tests/test_geelark_client.py`

- [ ] **Step 5.1: Write failing tests**

Append to `tests/test_geelark_client.py`:

```python
def test_create_youtube_post_task_payload_and_endpoint(monkeypatch):
    captured = {}
    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskId": "yt-1"}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_youtube_post_task("env-1", "https://res/v.mp4",
                                      title="My Short")
    assert tid == "yt-1"
    assert captured["path"] == "/open/v1/rpa/task/youtubePubShort"
    p = captured["payload"]
    assert p["id"] == "env-1"
    assert p["video"] == "https://res/v.mp4"
    assert p["title"] == "My Short"
    assert p["sameStyleVoice"] == 0
    assert p["originalVoice"] == 100
    assert p["isDisclosureMandatory"] is False
    assert isinstance(p["scheduleAt"], int)


def test_create_youtube_post_task_truncates_title(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "yt-2"})
    c = geelark_client.GeeLarkClient("token")
    c.create_youtube_post_task("env-2", "https://res/v.mp4",
                                title="x" * 250)
    assert captured["payload"]["title"] == "x" * 100


def test_create_youtube_post_task_raises_when_taskid_missing(monkeypatch):
    import pytest
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="taskId"):
        c.create_youtube_post_task("env-3", "https://res/v.mp4", title="hi")


def test_create_instagram_post_task_payload_and_endpoint(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(path=path, payload=payload)
                         or {"taskId": "ig-1"})
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_instagram_post_task("env-4", ["https://res/a.mp4"],
                                        description="my IG reel")
    assert tid == "ig-1"
    assert captured["path"] == "/open/v1/rpa/task/instagramPubReels"
    p = captured["payload"]
    assert p["id"] == "env-4"
    assert p["video"] == ["https://res/a.mp4"]
    assert p["description"] == "my IG reel"
    assert p["aiTag"] is False
    assert p["needShareLink"] is False
    assert isinstance(p["scheduleAt"], int)


def test_create_instagram_post_task_truncates_description(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "ig-2"})
    c = geelark_client.GeeLarkClient("token")
    c.create_instagram_post_task("env-5", ["https://res/a.mp4"],
                                  description="x" * 3000)
    assert captured["payload"]["description"] == "x" * 2200


def test_create_instagram_post_task_raises_when_taskid_missing(monkeypatch):
    import pytest
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="taskId"):
        c.create_instagram_post_task("env-6", ["https://res/a.mp4"],
                                      description="hi")


def test_create_facebook_post_task_payload_and_endpoint(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(path=path, payload=payload)
                         or {"taskId": "fb-1"})
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_facebook_post_task("env-7", "https://res/a.mp4",
                                       description="my FB reel")
    assert tid == "fb-1"
    assert captured["path"] == "/open/v1/rpa/task/faceBookPubReels"
    p = captured["payload"]
    assert p["id"] == "env-7"
    assert p["video"] == "https://res/a.mp4"
    assert p["description"] == "my FB reel"
    assert p["needShareLink"] is False
    assert "page" not in p


def test_create_facebook_post_task_truncates_description(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "fb-2"})
    c = geelark_client.GeeLarkClient("token")
    c.create_facebook_post_task("env-8", "https://res/a.mp4",
                                 description="y" * 800)
    assert captured["payload"]["description"] == "y" * 500


def test_create_facebook_post_task_raises_when_taskid_missing(monkeypatch):
    import pytest
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="taskId"):
        c.create_facebook_post_task("env-9", "https://res/a.mp4",
                                     description="hi")
```

- [ ] **Step 5.2: Run to verify failure**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_geelark_client.py -v
```

Expected: 9 new tests FAIL.

- [ ] **Step 5.3: Implement**

In `geelark_client.py`, after `create_video_post_task`, add three new methods (same body as the previous YouTube-only plan but now with IG and FB siblings):

```python
    def create_youtube_post_task(
        self,
        env_id: str,
        video_url: str,
        title: str,
    ) -> str:
        """Schedule a YouTube Shorts post. Returns the task ID.

        Defaults: sameStyleVoice=0, originalVoice=100,
        isDisclosureMandatory=False. Title truncated to 100 chars."""
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "title": title[:100],
            "video": video_url,
            "sameStyleVoice": 0,
            "originalVoice": 100,
            "isDisclosureMandatory": False,
        }
        data = self._post("/open/v1/rpa/task/youtubePubShort", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"youtube task created but no taskId: {data}")
        return task_id

    def create_instagram_post_task(
        self,
        env_id: str,
        video_urls: list[str],
        description: str,
    ) -> str:
        """Schedule an Instagram Reels post. Returns the task ID.

        `video_urls` is a list (API supports up to 10); single-clip use
        passes `[url]`. Defaults: aiTag=False, needShareLink=False.
        Description truncated to 2200 chars."""
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "description": description[:2200],
            "video": list(video_urls),
            "aiTag": False,
            "needShareLink": False,
        }
        data = self._post("/open/v1/rpa/task/instagramPubReels", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"instagram task created but no taskId: {data}")
        return task_id

    def create_facebook_post_task(
        self,
        env_id: str,
        video_url: str,
        description: str,
    ) -> str:
        """Schedule a Facebook Reels post. Returns the task ID.

        Defaults: needShareLink=False, page omitted. Description
        truncated to 500 chars."""
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "description": description[:500],
            "video": video_url,
            "needShareLink": False,
        }
        data = self._post("/open/v1/rpa/task/faceBookPubReels", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"facebook task created but no taskId: {data}")
        return task_id
```

- [ ] **Step 5.4: Run to verify pass**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_geelark_client.py -v
```

Expected: all PASS.

- [ ] **Step 5.5: Commit**

```bash
git add geelark_client.py tests/test_geelark_client.py
git commit -m "feat(geelark): YT/IG/FB Reels publishing endpoints with per-API truncation"
```

---

### Task 6: videos repo — drop publishing-state fns, embed publications

**Files:**
- Modify: `app/repositories/videos.py`
- Modify: `tests/test_repositories.py`

This task removes the publishing-state functions from `videos.py` and
rewrites `list_all` to embed the publications array. Posting service
still imports them — we accept that the test_services file is broken
until Task 7.

- [ ] **Step 6.1: Replace `app/repositories/videos.py`**

```python
"""Video repository — material metadata only.
Publishing state lives in publications (see app/repositories/publications.py)."""
from typing import Optional
from sqlalchemy import select
from sqlmodel import Session
from app.models import Video
from app.repositories import publications as pub_repo


def create(session: Session, filename: str, stored_path: str,
           drive_file_id: Optional[str] = None,
           group_id: Optional[int] = None) -> int:
    v = Video(
        original_filename=filename,
        stored_path=stored_path,
        drive_file_id=drive_file_id,
        group_id=group_id,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return v.id


def bulk_set_group(session: Session, ids: list[int],
                   group_id: Optional[int]) -> int:
    if not ids:
        return 0
    rows = session.exec(
        select(Video).where(Video.id.in_(ids))).scalars().all()
    for v in rows:
        v.group_id = group_id
        session.add(v)
    session.commit()
    return len(rows)


def existing_drive_file_ids(session: Session) -> set[str]:
    rows = session.exec(
        select(Video.drive_file_id)
        .where(Video.drive_file_id.isnot(None))
    ).all()
    return {r for r in rows if r}


def get(session: Session, vid: int) -> Optional[dict]:
    v = session.get(Video, vid)
    return v.model_dump() if v else None


def list_all(session: Session) -> list[dict]:
    """Each video carries an embedded `publications` array."""
    rows = session.exec(
        select(Video).order_by(Video.id.desc())
    ).scalars().all()
    out = []
    for v in rows:
        d = v.model_dump()
        d["publications"] = pub_repo.list_for_video(session, v.id)
        out.append(d)
    return out


def clear_stored_path(session: Session, vid: int) -> None:
    v = session.get(Video, vid)
    if v is None:
        return
    v.stored_path = None
    session.add(v)
    session.commit()


def delete(session: Session, vid: int) -> None:
    v = session.get(Video, vid)
    if v is None:
        return
    session.delete(v)  # publications cascade via FK ON DELETE CASCADE
    session.commit()


def count_for_group(session: Session, group_id: int) -> dict:
    """{published, pending, failed} across a group's videos' publications.
    'published' if any publication SUCCESS; 'failed' if any FAILURE and
    none still PENDING/QUEUED/INITIATED/SUCCESS; otherwise 'pending'."""
    import sqlalchemy as sa
    row = session.exec(sa.text("""
        SELECT
          COUNT(DISTINCT CASE WHEN p.result='SUCCESS' THEN v.id END) AS published,
          COUNT(DISTINCT CASE WHEN p.result IN ('PENDING','QUEUED','INITIATED') THEN v.id END) AS pending,
          COUNT(DISTINCT CASE WHEN p.result='FAILURE' THEN v.id END) AS failed
        FROM videos v
        LEFT JOIN publications p ON p.video_id = v.id
        WHERE v.group_id = :gid
    """), {"gid": group_id}).first()
    return {"published": row[0] or 0,
            "pending":   row[1] or 0,
            "failed":    row[2] or 0}
```

- [ ] **Step 6.2: Update touched tests in `tests/test_repositories.py`**

Delete or rewrite (move behavior to `tests/test_repositories_publications.py`):
- `test_video_pending_and_status` — DELETE (publications repo now)
- `test_set_account_assigns_and_clears` — DELETE (`set_account` removed)

Rewrite `test_videos_count_for_group`:

```python
def test_videos_count_for_group(session):
    from app.repositories import videos as vr
    from app.repositories import publications as pr
    from app.repositories import accounts as ar
    from app.repositories import groups as gr
    ar.create(session, {"profile_name": "C1", "geelark_phone_id": "phc1"})
    gid = gr.create(session, name="g1")
    vid_a = vr.create(session, "a.mp4", "/tmp/a.mp4", group_id=gid)
    vid_b = vr.create(session, "b.mp4", "/tmp/b.mp4", group_id=gid)
    vr.create(session, "c.mp4", "/tmp/c.mp4", group_id=gid)  # no publications
    pa = pr.create(session, video_id=vid_a, platform="tiktok")
    pb = pr.create(session, video_id=vid_b, platform="tiktok")
    pr.set_result(session, pa, "SUCCESS", "ok")
    pr.set_result(session, pb, "FAILURE", "err")
    c = vr.count_for_group(session, gid)
    assert c == {"published": 1, "pending": 0, "failed": 1}
```

- [ ] **Step 6.3: Run repo + db tests**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_repositories.py tests/test_platforms.py \
                 tests/test_repositories_publications.py -v
```

Expected: PASS.

- [ ] **Step 6.4: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories.py
git commit -m "refactor(videos): drop publishing-state fns; list_all embeds publications"
```

---

### Task 7: posting_service + assignment — publications-driven, platform-aware

The largest backend task. Replace `posting_service.py` and update
`assignment.py`. Rewrite touched tests in `tests/test_services.py` and
`tests/test_assignment.py`.

**Files:**
- Modify: `app/services/posting_service.py`
- Modify: `app/services/assignment.py`
- Modify: `tests/test_services.py`
- Modify: `tests/test_assignment.py`

- [ ] **Step 7.1: Replace `app/services/assignment.py`**

```python
"""Account pool building for posting. `platform` selects which
last_posted_at_* column and which <platform>_logged_in flag to use."""
import time
from app.services.platforms import LAST_POSTED_COL, LOGGED_IN_COL


def _eligible(account: dict, platform: str) -> bool:
    if not account.get("warmed_up"):
        return False
    if not account.get(LOGGED_IN_COL[platform]):
        return False
    return True


def build_pool(accounts: list[dict], platform: str = "tiktok") -> list[dict]:
    col = LAST_POSTED_COL[platform]
    pool = []
    for a in accounts:
        if not _eligible(a, platform):
            continue
        name = (a.get("profile_name") or "").strip()
        if not name:
            continue
        pool.append({
            "profile_name": name,
            "account_id": a.get("id"),
            "last_posted_at": a.get(col),
        })
    pool.sort(key=lambda p: p["last_posted_at"]
              if p["last_posted_at"] is not None else 0)
    return pool


def assign(videos: list, pool: list[dict],
           cooldown_hours: float = 2) -> list[dict]:
    cutoff = time.time() - cooldown_hours * 3600

    def _epoch(t):
        if t is None: return None
        if hasattr(t, "timestamp"): return t.timestamp()
        return t  # already epoch

    available = [p for p in pool
                 if _epoch(p["last_posted_at"]) is None
                 or _epoch(p["last_posted_at"]) < cutoff]
    assignments = []
    for video in videos:
        if not available:
            break
        chosen = available.pop(0)
        assignments.append({
            "video": video,
            "profile_name": chosen["profile_name"],
            "account_id": chosen["account_id"],
        })
    return assignments
```

- [ ] **Step 7.2: Replace `tests/test_assignment.py`**

```python
import time
from app.services.assignment import build_pool, assign


def _acc(name, warmed=True, account_id=1, **kw):
    base = {
        "id": account_id,
        "profile_name": name,
        "warmed_up": 1 if warmed else 0,
        "tiktok_logged_in": kw.pop("tiktok_logged_in", 1),
        "last_posted_at": kw.pop("last_posted_at", None),
        "youtube_logged_in": kw.pop("youtube_logged_in", 0),
        "last_posted_at_youtube": kw.pop("last_posted_at_youtube", None),
        "instagram_logged_in": kw.pop("instagram_logged_in", 0),
        "last_posted_at_instagram": kw.pop("last_posted_at_instagram", None),
        "facebook_logged_in": kw.pop("facebook_logged_in", 0),
        "last_posted_at_facebook": kw.pop("last_posted_at_facebook", None),
    }
    base.update(kw)
    return base


def test_pool_excludes_unwarmed_and_sorts_oldest_first():
    accounts = [
        _acc("new", account_id=1),
        _acc("old", account_id=2, last_posted_at=1000),
        _acc("recent", account_id=3, last_posted_at=9_000_000_000),
        _acc("cold", account_id=4, warmed=False),
    ]
    pool = build_pool(accounts)
    assert [p["profile_name"] for p in pool] == ["new", "old", "recent"]


def test_pool_requires_platform_logged_in():
    accounts = [
        _acc("tt-only", account_id=1),
        _acc("yt-yes", account_id=2, youtube_logged_in=1),
        _acc("ig-yes", account_id=3, instagram_logged_in=1),
        _acc("fb-yes", account_id=4, facebook_logged_in=1),
    ]
    assert [p["profile_name"] for p in build_pool(accounts, "tiktok")] == \
        ["tt-only", "yt-yes", "ig-yes", "fb-yes"]
    assert [p["profile_name"] for p in build_pool(accounts, "youtube")] == ["yt-yes"]
    assert [p["profile_name"] for p in build_pool(accounts, "instagram")] == ["ig-yes"]
    assert [p["profile_name"] for p in build_pool(accounts, "facebook")] == ["fb-yes"]


def test_assign_respects_cooldown_per_platform():
    now = time.time()
    accounts = [
        _acc("split", account_id=1, last_posted_at=now - 600,
             youtube_logged_in=1, last_posted_at_youtube=None),
        _acc("yt-hot", account_id=2, youtube_logged_in=1,
             last_posted_at_youtube=now - 600),
    ]
    yt_pool = build_pool(accounts, "youtube")
    out = assign(["v1"], yt_pool, cooldown_hours=2)
    assert out == [{"video": "v1", "profile_name": "split", "account_id": 1}]


def test_assign_respects_cooldown_and_one_per_run():
    now = time.time()
    accounts = [
        _acc("free", account_id=1),
        _acc("hot",  account_id=2, last_posted_at=now - 600),
    ]
    out = assign(["v1", "v2"], build_pool(accounts), cooldown_hours=2)
    assert out == [{"video": "v1", "profile_name": "free", "account_id": 1}]
```

- [ ] **Step 7.3: Replace `app/services/posting_service.py`**

```python
"""Publishing orchestration, publications-driven, platform-aware.

A publication row pairs one video with one target platform. This
service iterates PENDING publications for a chosen platform, picks an
eligible account, submits the platform-specific GeeLark task, and
records the resulting task ID. A separate poll (`check_initiated` or
the per-publication `check_publication_one`) resolves INITIATED
publications to SUCCESS/FAILURE later."""
import os
import time
from datetime import datetime

from sqlmodel import Session

from app.repositories import accounts as acc_repo
from app.repositories import settings as set_repo
from app.repositories import videos as vid_repo
from app.repositories import publications as pub_repo
from app.services.assignment import build_pool, assign
from app.services.platforms import (
    LAST_POSTED_COL, LOGGED_IN_COL, validate as validate_platform,
)


def _caption(session, local_path: str) -> str:
    from generate_caption import generate_caption_for_local_file
    model = (set_repo.get_all(session).get("gemini_model") or "").strip() or None
    return generate_caption_for_local_file(local_path, model=model)


def _epoch(ts):
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        return ts.timestamp()
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _submit(client, platform: str, env_id: str, video_url: str,
            caption: str) -> str:
    if platform == "tiktok":
        return client.create_video_post_task(env_id, video_url,
                                              video_desc=caption)
    if platform == "youtube":
        return client.create_youtube_post_task(env_id, video_url,
                                                title=caption)
    if platform == "instagram":
        return client.create_instagram_post_task(env_id, [video_url],
                                                  description=caption)
    if platform == "facebook":
        return client.create_facebook_post_task(env_id, video_url,
                                                 description=caption)
    raise ValueError(f"unknown platform: {platform!r}")


def _post_one(session, client, pub: dict, acc: dict):
    """Run one publication. `pub` should include stored_path /
    original_filename via JOIN in pub_repo.list_pending."""
    stored_path = pub.get("stored_path")
    original_filename = pub.get("original_filename")
    if not stored_path:
        v = vid_repo.get(session, pub["video_id"])
        stored_path = v.get("stored_path") if v else None
        original_filename = v.get("original_filename") if v else None
    if not stored_path:
        pub_repo.mark_failure(session, pub["id"], acc["id"],
                               "stored_path missing")
        yield "  FAILURE: stored file missing"
        return
    try:
        caption = _caption(session, stored_path)
    except Exception as e:
        pub_repo.mark_failure(session, pub["id"], acc["id"],
                               f"caption failed: {e}")
        yield f"  FAILURE caption: {e}"
        return
    try:
        ext = os.path.splitext(original_filename or "")[1].lstrip(".") or "mp4"
        res_url = client.upload_file(stored_path, ext)
        task_id = _submit(client, pub["platform"],
                          acc["geelark_phone_id"], res_url, caption)
    except Exception as e:
        pub_repo.mark_failure(session, pub["id"], acc["id"],
                               f"post failed: {e}")
        yield f"  FAILURE post: {e}"
        return
    pub_repo.mark_initiated(session, pub["id"], acc["id"], caption,
                             task_id, "submitted to GeeLark")
    acc_repo.touch_last_posted(session, acc["id"], pub["platform"])
    yield f"  INITIATED task {task_id}"


def publish_for_accounts(session, client, account_ids, *,
                          platform: str = "tiktok",
                          cooldown_hours: float = 2, force: bool = False):
    """Generator. Posts PENDING publications of `platform` to one of the
    given accounts, honoring per-platform eligibility and cooldown."""
    platform = validate_platform(platform)
    ids = set(account_ids)
    if not ids:
        yield "No videos to post."
        return
    accounts = [a for a in acc_repo.list_all(session) if a["id"] in ids]
    if not accounts:
        yield "No accounts found."
        return
    group_ids = {a.get("group_id") for a in accounts
                 if a.get("group_id") is not None}
    has_ungrouped = any(a.get("group_id") is None for a in accounts)

    pubs = pub_repo.list_pending(session, platform=platform)
    manual = [p for p in pubs if p.get("account_id") in ids]
    auto = [p for p in pubs if not p.get("account_id")
            and (p.get("group_id") in group_ids
                 or (p.get("group_id") is None and has_ungrouped))]
    if not manual and not auto:
        yield "No videos to post."
        return

    by_id = {a["id"]: a for a in accounts}
    cutoff = time.time() - cooldown_hours * 3600
    last_col = LAST_POSTED_COL[platform]
    logged_col = LOGGED_IN_COL[platform]

    for p in manual:
        acc = by_id.get(p["account_id"])
        if acc is None:
            continue
        label = (acc.get("profile_name") or "").strip()
        yield f"'{p['original_filename']}' -> {label} ({platform}/manual)"
        if not force:
            if not acc.get("warmed_up"):
                yield f"  SKIPPED: {label} not warmed up"
                continue
            if not acc.get(logged_col):
                yield f"  SKIPPED: {label} not logged in to {platform}"
                continue
            last = _epoch(acc.get(last_col))
            if last is not None and last >= cutoff:
                yield f"  SKIPPED: {label} in cooldown"
                continue
        yield from _post_one(session, client, p, acc)

    if auto:
        fresh = [a for a in acc_repo.list_all(session) if a["id"] in ids]
        # Normalize timestamps to epoch for build_pool / assign.
        normalized = []
        for a in fresh:
            a2 = {**a}
            a2[last_col] = _epoch(a.get(last_col))
            normalized.append(a2)
        pool = build_pool(normalized, platform=platform)
        plan = assign(auto, pool, cooldown_hours=cooldown_hours)
        if not plan and not manual:
            yield "No warmed-up accounts available."
        by_aid = {a["id"]: a for a in fresh}
        for item in plan:
            p = item["video"]
            acc = by_aid[item["account_id"]]
            yield (f"'{p['original_filename']}' -> {acc['profile_name']} "
                   f"({platform}/auto)")
            yield from _post_one(session, client, p, acc)
    yield "Done."


def publish(session, client, *, platform: str = "tiktok",
            cooldown_hours: float = 2, force: bool = False,
            group_id: int | None = None):
    """Synchronous publish entry point for the legacy /api/videos/publish
    endpoint. Resolves group_id -> account_ids -> delegates."""
    candidates = acc_repo.list_all(session)
    if group_id is not None:
        candidates = [a for a in candidates if a.get("group_id") == group_id]
    yield from publish_for_accounts(session, client,
                                     [a["id"] for a in candidates],
                                     platform=platform,
                                     cooldown_hours=cooldown_hours,
                                     force=force)


def check_initiated(session, client) -> str:
    """Synchronous poll across all platforms. Used by the legacy path.
    Cleans up the local stored_path file when every publication of a
    video is terminal."""
    rows = pub_repo.list_initiated(session)
    if not rows:
        return "0 pending"
    ok = fail = 0
    for p in rows:
        try:
            res = client.query_tasks([p["geelark_task_id"]])
        except Exception:
            continue
        if not res:
            pub_repo.set_result(session, p["id"], "FAILURE", "task not found")
            fail += 1
            continue
        status = res[0].get("status")
        if status == 3:
            pub_repo.set_result(session, p["id"], "SUCCESS", "completed")
            ok += 1
        elif status in (4, 7):
            pub_repo.set_result(session, p["id"], "FAILURE", "task failed")
            fail += 1
    for vid in pub_repo.video_ids_all_terminal(session):
        v = vid_repo.get(session, vid)
        if not v or not v.get("stored_path"):
            continue
        try:
            os.remove(v["stored_path"])
        except OSError:
            pass
        vid_repo.clear_stored_path(session, vid)
    return f"{ok} success, {fail} failure"


# ----- Per-publication path (Procrastinate task callers) ----- #

def plan_assignments(session, *, platform: str) -> list[int]:
    """Pick eligible (warmed-up + logged-in + outside cooldown) accounts
    for PENDING publications of `platform`. Flips each chosen
    publication to QUEUED + stamps account_id transactionally, returns
    the publication IDs the caller should defer."""
    platform = validate_platform(platform)
    pending = pub_repo.list_pending(session, platform=platform)
    if not pending:
        return []
    accounts = acc_repo.list_all(session)
    # Normalize timestamps for assignment
    last_col = LAST_POSTED_COL[platform]
    normalized = []
    for a in accounts:
        a2 = {**a}
        a2[last_col] = _epoch(a.get(last_col))
        normalized.append(a2)
    pool = build_pool(normalized, platform=platform)
    plan = assign(pending, pool, cooldown_hours=2)
    queued: list[int] = []
    for item in plan:
        p = item["video"]
        pub_repo.mark_queued(session, p["id"], item["account_id"])
        queued.append(p["id"])
    return queued


def submit_publication_to_geelark(session, client, *,
                                    publication_id: int) -> str:
    """Single-publication submit used by publish_publication_task.
    On caption/upload/submit failure, marks the publication FAILED and
    re-raises so the task framework can record the error."""
    pub = pub_repo.get(session, publication_id)
    if pub is None:
        raise RuntimeError(f"publication {publication_id} not found")
    if pub["account_id"] is None:
        raise RuntimeError(
            f"publication {publication_id} has no account_id; "
            f"plan_assignments should have stamped one")
    v = vid_repo.get(session, pub["video_id"])
    if v is None or not v.get("stored_path"):
        pub_repo.mark_failure(session, publication_id, pub["account_id"],
                               "stored_path missing")
        raise RuntimeError("stored_path missing")
    acc = acc_repo.get(session, pub["account_id"])
    if acc is None:
        pub_repo.mark_failure(session, publication_id, pub["account_id"],
                               "account vanished")
        raise RuntimeError("account vanished")
    try:
        caption = _caption(session, v["stored_path"])
        ext = os.path.splitext(v["original_filename"] or "")[1].lstrip(".") or "mp4"
        res_url = client.upload_file(v["stored_path"], ext)
        task_id = _submit(client, pub["platform"],
                          acc["geelark_phone_id"], res_url, caption)
    except Exception as e:
        pub_repo.mark_failure(session, publication_id, pub["account_id"],
                               f"submit failed: {e}")
        raise
    pub_repo.mark_initiated(session, publication_id, pub["account_id"],
                             caption, task_id, "submitted to GeeLark")
    acc_repo.touch_last_posted(session, pub["account_id"], pub["platform"])
    return task_id


def check_publication_one(session, client, publication_id: int) -> str:
    """Single-publication status poll used by check_publication_status_task.
    Returns 'running' | 'success' | 'failure' | 'unknown'."""
    pub = pub_repo.get(session, publication_id)
    if pub is None:
        return "unknown"
    if pub["result"] in ("SUCCESS", "FAILURE"):
        return "success" if pub["result"] == "SUCCESS" else "failure"
    if pub["result"] != "INITIATED" or not pub["geelark_task_id"]:
        return "unknown"
    try:
        res = client.query_tasks([pub["geelark_task_id"]])
    except Exception:
        return "running"  # transient — retry
    if not res:
        pub_repo.set_result(session, publication_id, "FAILURE",
                             "task not found")
        return "failure"
    status = res[0].get("status")
    if status == 3:
        pub_repo.set_result(session, publication_id, "SUCCESS", "completed")
        _maybe_cleanup_video(session, pub["video_id"])
        return "success"
    if status in (4, 7):
        pub_repo.set_result(session, publication_id, "FAILURE", "task failed")
        _maybe_cleanup_video(session, pub["video_id"])
        return "failure"
    return "running"


def _maybe_cleanup_video(session, video_id: int) -> None:
    """If every publication for `video_id` is terminal, remove the local
    file and clear stored_path."""
    terminal = pub_repo.video_ids_all_terminal(session)
    if video_id not in terminal:
        return
    v = vid_repo.get(session, video_id)
    if not v or not v.get("stored_path"):
        return
    try:
        os.remove(v["stored_path"])
    except OSError:
        pass
    vid_repo.clear_stored_path(session, video_id)
```

- [ ] **Step 7.4: Rewrite `tests/test_services.py` posting tests**

Open `tests/test_services.py`. Find every test currently using
`vid_repo.mark_initiated`, `Video.result`, `Video.account_id`, etc.,
and rewrite them around publications. The plan-equivalent test bodies
below — paste-replace each function by name. (Other tests in this file
that don't reference Video publishing state stay as-is.)

`test_posting_assigns_and_marks_initiated`:

```python
def test_posting_assigns_and_marks_initiated(session, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    acc_repo.create(session, {"profile_name": "R1",
                               "geelark_phone_id": "ph1",
                               "warmed_up": 1, "tiktok_logged_in": 1})
    f = tmp_path / "c.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "c.mp4", str(f))
    pub_repo.create(session, video_id=vid, platform="tiktok")
    class FakePost:
        def upload_file(self, p, t): return "https://res/c.mp4"
        def create_video_post_task(self, env_id, video_url,
                                    video_desc=None, cover_url=None):
            return "pt1"
    monkeypatch.setattr(posting_service, "_caption",
                         lambda s, p: "my caption")
    list(posting_service.publish(session, FakePost(), platform="tiktok"))
    pubs = pub_repo.list_for_video(session, vid)
    assert pubs[0]["result"] == "INITIATED"
    assert pubs[0]["geelark_task_id"] == "pt1"
    assert acc_repo.list_all(session)[0]["last_posted_at"] is not None
```

`test_check_initiated_success_clears_file`:

```python
def test_check_initiated_success_clears_file(session, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(session, {"profile_name": "R1",
                                     "geelark_phone_id": "ph1"})
    f = tmp_path / "s.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "s.mp4", str(f))
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, aid, "cap", "tk1", "submitted")
    class DoneClient:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]
    summary = posting_service.check_initiated(session, DoneClient())
    p = pub_repo.get(session, pid)
    assert p["result"] == "SUCCESS"
    v = vid_repo.get(session, vid)
    assert v["stored_path"] is None
    assert not f.exists()
    assert summary == "1 success, 0 failure"
```

`test_check_initiated_failure_and_not_found`:

```python
def test_check_initiated_failure_and_not_found(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(session, {"profile_name": "R2",
                                     "geelark_phone_id": "ph2"})
    v_fail = vid_repo.create(session, "f.mp4", "/x/f.mp4")
    p_fail = pub_repo.create(session, video_id=v_fail, platform="tiktok")
    pub_repo.mark_initiated(session, p_fail, aid, "c", "tkF", "s")
    v_missing = vid_repo.create(session, "m.mp4", "/x/m.mp4")
    p_missing = pub_repo.create(session, video_id=v_missing, platform="tiktok")
    pub_repo.mark_initiated(session, p_missing, aid, "c", "tkM", "s")
    class MixedClient:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 4}] if ids == ["tkF"] else []
    summary = posting_service.check_initiated(session, MixedClient())
    assert pub_repo.get(session, p_fail)["result"] == "FAILURE"
    assert pub_repo.get(session, p_missing)["result"] == "FAILURE"
    assert summary == "0 success, 2 failure"
```

Add four new tests at the bottom:

```python
def test_publish_dispatches_to_each_platform(session, monkeypatch, tmp_path):
    """For each of TT/YT/IG/FB, posting_service.publish calls the right
    client method, updates the right last_posted_at_* column, and the
    video shape (list vs string) matches the platform API."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    cases = [
        ("tiktok",    "phT", "tt-1", "https://res/a.mp4", "last_posted_at"),
        ("youtube",   "phY", "yt-1", "https://res/a.mp4", "last_posted_at_youtube"),
        ("instagram", "phI", "ig-1", ["https://res/a.mp4"], "last_posted_at_instagram"),
        ("facebook",  "phF", "fb-1", "https://res/a.mp4", "last_posted_at_facebook"),
    ]
    for platform, env_id, task_id, expected_video, ts_col in cases:
        aid = acc_repo.create(session, {
            "profile_name": f"A-{platform}",
            "geelark_phone_id": env_id,
            "warmed_up": 1,
            "tiktok_logged_in":    1 if platform == "tiktok"    else 0,
            "youtube_logged_in":   1 if platform == "youtube"   else 0,
            "instagram_logged_in": 1 if platform == "instagram" else 0,
            "facebook_logged_in":  1 if platform == "facebook"  else 0,
        })
        f = tmp_path / f"{platform}.mp4"; f.write_bytes(b"x")
        vid = vid_repo.create(session, f"{platform}.mp4", str(f))
        pid = pub_repo.create(session, video_id=vid, platform=platform)

        class FakeClient:
            def __init__(self): self.captured = {}
            def upload_file(self, p, t): return "https://res/a.mp4"
            def create_video_post_task(self, env, url, video_desc=None,
                                        cover_url=None):
                self.captured.update(env=env, video=url, text=video_desc)
                return task_id
            def create_youtube_post_task(self, env, url, title):
                self.captured.update(env=env, video=url, text=title)
                return task_id
            def create_instagram_post_task(self, env, urls, description):
                assert isinstance(urls, list)
                self.captured.update(env=env, video=urls, text=description)
                return task_id
            def create_facebook_post_task(self, env, url, description):
                self.captured.update(env=env, video=url, text=description)
                return task_id

        fake = FakeClient()
        monkeypatch.setattr(posting_service, "_caption",
                             lambda s, p: "shared caption")
        list(posting_service.publish(session, fake, platform=platform))
        row = pub_repo.get(session, pid)
        assert row["result"] == "INITIATED", f"{platform}: {row['result']}"
        assert row["geelark_task_id"] == task_id
        assert fake.captured["env"] == env_id
        assert fake.captured["video"] == expected_video
        a = acc_repo.get(session, aid)
        assert a[ts_col] is not None, f"{platform}: {ts_col} not stamped"


def test_publish_skips_accounts_not_logged_in_to_platform(session, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    for platform in ("youtube", "instagram", "facebook"):
        acc_repo.create(session, {"profile_name": f"NOT-{platform}",
                                   "geelark_phone_id": f"ph-NOT-{platform}",
                                   "warmed_up": 1})
        f = tmp_path / f"{platform}.mp4"; f.write_bytes(b"x")
        vid = vid_repo.create(session, f"{platform}.mp4", str(f))
        pid = pub_repo.create(session, video_id=vid, platform=platform)
        class FailFast:
            def upload_file(self, *a, **k):
                raise AssertionError("must not upload")
        monkeypatch.setattr(posting_service, "_caption", lambda s, p: "c")
        list(posting_service.publish(session, FailFast(), platform=platform))
        assert pub_repo.get(session, pid)["result"] == "PENDING"


def test_check_initiated_keeps_file_until_all_publications_terminal(session, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(session, {"profile_name": "MX",
                                     "geelark_phone_id": "phMX"})
    f = tmp_path / "mx.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "mx.mp4", str(f))
    pids = {}
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pid = pub_repo.create(session, video_id=vid, platform=plat)
        pub_repo.mark_initiated(session, pid, aid, "c", f"t-{plat}", "s")
        pids[plat] = pid

    class R1:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3 if ids[0] == "t-tiktok" else 2}]
    posting_service.check_initiated(session, R1())
    assert pub_repo.get(session, pids["tiktok"])["result"] == "SUCCESS"
    assert f.exists()
    assert vid_repo.get(session, vid)["stored_path"] is not None

    class R2:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]
    posting_service.check_initiated(session, R2())
    for plat in ("youtube", "instagram", "facebook"):
        assert pub_repo.get(session, pids[plat])["result"] == "SUCCESS"
    assert not f.exists()
    assert vid_repo.get(session, vid)["stored_path"] is None


def test_plan_assignments_marks_queued_and_returns_pids(session, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(session, {"profile_name": "PA",
                                     "geelark_phone_id": "phPA",
                                     "warmed_up": 1,
                                     "youtube_logged_in": 1})
    f = tmp_path / "p.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "p.mp4", str(f))
    pid = pub_repo.create(session, video_id=vid, platform="youtube")
    out = posting_service.plan_assignments(session, platform="youtube")
    assert out == [pid]
    row = pub_repo.get(session, pid)
    assert row["result"] == "QUEUED"
    assert row["account_id"] == aid


def test_submit_publication_to_geelark_happy_path(session, tmp_path, monkeypatch):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(session, {"profile_name": "SP",
                                     "geelark_phone_id": "phSP",
                                     "warmed_up": 1,
                                     "tiktok_logged_in": 1})
    f = tmp_path / "s.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "s.mp4", str(f))
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(session, pid, account_id=aid)

    class FakeClient:
        def upload_file(self, p, t): return "https://res/s.mp4"
        def create_video_post_task(self, env, url,
                                    video_desc=None, cover_url=None):
            return "tk-1"
    monkeypatch.setattr(posting_service, "_caption",
                         lambda s, p: "cap")
    task_id = posting_service.submit_publication_to_geelark(
        session, FakeClient(), publication_id=pid)
    assert task_id == "tk-1"
    row = pub_repo.get(session, pid)
    assert row["result"] == "INITIATED"


def test_check_publication_one_transitions(session, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(session, {"profile_name": "CK",
                                     "geelark_phone_id": "phCK"})
    f = tmp_path / "ck.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "ck.mp4", str(f))
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, aid, "c", "ck1", "s")

    class Running:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 2}]
    assert posting_service.check_publication_one(session, Running(), pid) \
        == "running"

    class Done:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]
    assert posting_service.check_publication_one(session, Done(), pid) \
        == "success"
    assert pub_repo.get(session, pid)["result"] == "SUCCESS"
```

Remove or rewrite any other test in `tests/test_services.py` that
still references `vid_repo.mark_initiated` / `Video.result` etc.
(e.g. the older `test_publish_handles_whitespace_profile_name`,
`test_publish_posts_manual_video_to_its_account`,
`test_publish_skips_manual_video_on_unwarmed_account`,
`test_publish_force_posts_manual_video_on_unwarmed_account`,
`test_publish_auto_still_assigns_unassigned_videos`,
`test_publish_with_group_id_only_posts_that_group`,
`test_publish_for_accounts_restricts_pool_to_given_ids`,
`test_job_auto_publish_skips_videos_without_group_id`,
`test_job_status_check_runs_without_operation_lock`) — replace each by
the publications-shaped equivalent, mirroring the patterns above.
**The grep** `grep -n "vid_repo.mark_initiated\|Video.result\|set_account\|mark_initiated_from_task" tests/test_services.py` should return zero hits after this step.

- [ ] **Step 7.5: Run posting + assignment tests**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_assignment.py tests/test_services.py -v
```

Expected: all PASS.

- [ ] **Step 7.6: Run full suite**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest -q
```

Expected: only `test_api.py` may still fail (upload doesn't write
publications yet — Task 9). Note count.

- [ ] **Step 7.7: Commit**

```bash
git add app/services/posting_service.py app/services/assignment.py \
        tests/test_services.py tests/test_assignment.py
git commit -m "feat(posting): publications-driven dispatch + per-publication path

posting_service iterates PENDING publications per platform, dispatches
to TT/YT/IG/FB via geelark_client. plan_assignments + mark_queued
provides the synchronous claim used by the auto_publish_scan task.
submit_publication_to_geelark + check_publication_one are the new
per-publication submit/poll path used by the Procrastinate worker."
```

---

### Task 8: Procrastinate tasks — publish_publication + check_publication_status + auto_publish_scan

**Files:**
- Modify: `app/tasks/__init__.py`
- Modify: `app/tasks/publish.py`
- Create: `app/tasks/auto_publish.py`
- Create: `tests/test_tasks_publish.py`

- [ ] **Step 8.1: Update `app/tasks/__init__.py`**

Add `"app.tasks.auto_publish"` to the `import_paths` list. The old
`"app.tasks.publish"` stays — we're replacing its body, not the path.

- [ ] **Step 8.2: Replace `app/tasks/publish.py`**

```python
"""Per-publication Procrastinate tasks. Replaces the per-video pair
from Phase 2 (publish_video_task / check_publish_status_task)."""
from app.tasks import procrastinate_app
from app.db import session_scope


@procrastinate_app.task(lock="phones", queue="phones",
                         name="publish_publication")
async def publish_publication_task(publication_id: int):
    from app.services import geelark_service, posting_service
    try:
        with session_scope() as session:
            task_id = posting_service.submit_publication_to_geelark(
                session,
                geelark_service.make_client(),
                publication_id=publication_id,
            )
    except Exception as e:
        return f"FAILURE: {e}"
    await check_publication_status_task.defer_async(
        publication_id=publication_id,
        attempts_remaining=40,
        schedule_in={"seconds": 30},
    )
    return f"INITIATED task {task_id}"


@procrastinate_app.task(queue="default",
                         name="check_publication_status")
async def check_publication_status_task(publication_id: int,
                                         attempts_remaining: int):
    from app.services import geelark_service, posting_service
    with session_scope() as session:
        outcome = posting_service.check_publication_one(
            session,
            geelark_service.make_client(),
            publication_id,
        )
    if outcome == "running" and attempts_remaining > 0:
        await check_publication_status_task.defer_async(
            publication_id=publication_id,
            attempts_remaining=attempts_remaining - 1,
            schedule_in={"seconds": 30},
        )
    return outcome
```

- [ ] **Step 8.3: Create `app/tasks/auto_publish.py`**

```python
"""Periodic scan that finds PENDING publications, claims them with
plan_assignments (PENDING -> QUEUED + account_id), and defers one
publish_publication_task per pairing. Cron: top of every hour."""
from app.tasks import procrastinate_app
from app.tasks.publish import publish_publication_task
from app.db import session_scope


@procrastinate_app.periodic(cron="0 * * * *")
@procrastinate_app.task(queue="default", name="auto_publish_scan",
                         pass_context=True)
async def auto_publish_scan_task(context, timestamp: int):
    from app.services.platforms import PLATFORMS
    from app.services import posting_service
    deferred = 0
    for platform in PLATFORMS:
        with session_scope() as session:
            pub_ids = posting_service.plan_assignments(
                session, platform=platform)
        for pid in pub_ids:
            await publish_publication_task.defer_async(publication_id=pid)
            deferred += 1
    return f"{deferred} publication(s) deferred"
```

- [ ] **Step 8.4: Write tests using `procrastinate_app` fixture**

Create `tests/test_tasks_publish.py`:

```python
"""End-to-end tests for the per-publication task chain and the
auto-publish scan. Uses the conftest `procrastinate_app` fixture
(InMemoryConnector) so no real PG queue is touched."""
import pytest


@pytest.fixture
def fake_client(monkeypatch):
    """A minimal GeeLark client that records submit + poll calls."""
    from app.services import geelark_service

    class FakeC:
        def upload_file(self, path, ext): return "https://res/x.mp4"
        def create_video_post_task(self, env, url, video_desc=None,
                                    cover_url=None):
            return "tk-fake"
        def create_youtube_post_task(self, env, url, title):
            return "yt-fake"
        def create_instagram_post_task(self, env, urls, description):
            return "ig-fake"
        def create_facebook_post_task(self, env, url, description):
            return "fb-fake"
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]

    monkeypatch.setattr(geelark_service, "make_client", lambda: FakeC())
    return FakeC


def test_publish_publication_task_calls_submit_and_defers_check(
        session, procrastinate_app, fake_client, tmp_path, monkeypatch):
    """publish_publication_task should submit to GeeLark, transition
    the publication to INITIATED, and defer one check_publication_status
    task with schedule_in 30s."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import posting_service

    aid = acc_repo.create(session, {"profile_name": "PT",
                                     "geelark_phone_id": "phPT",
                                     "warmed_up": 1,
                                     "tiktok_logged_in": 1})
    f = tmp_path / "p.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "p.mp4", str(f))
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(session, pid, account_id=aid)

    monkeypatch.setattr(posting_service, "_caption", lambda s, p: "c")

    # Run the task body directly (procrastinate's InMemoryConnector
    # makes defer_async safe to call without a real worker loop).
    import asyncio
    from app.tasks.publish import publish_publication_task
    result = asyncio.run(publish_publication_task(publication_id=pid))
    assert "INITIATED" in result
    assert pub_repo.get(session, pid)["result"] == "INITIATED"

    # check_publication_status was deferred. The InMemoryConnector's
    # job list should now have one entry. (Exact API depends on the
    # procrastinate version — see InMemoryConnector.jobs.)
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert any("check_publication_status" in n for n in names)


def test_check_publication_status_task_chains_when_running(
        session, procrastinate_app, monkeypatch, tmp_path):
    """When the GeeLark task is still running and attempts remain, the
    status check defers itself with schedule_in 30s."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import geelark_service

    aid = acc_repo.create(session, {"profile_name": "CP",
                                     "geelark_phone_id": "phCP"})
    f = tmp_path / "c.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "c.mp4", str(f))
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, aid, "c", "ck1", "s")

    class Running:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 2}]
    monkeypatch.setattr(geelark_service, "make_client", lambda: Running())

    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))
    assert outcome == "running"
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert any("check_publication_status" in n for n in names)


def test_auto_publish_scan_defers_one_task_per_eligible_publication(
        session, procrastinate_app, fake_client, tmp_path, monkeypatch):
    """The hourly scan should walk each platform, find eligible
    publications, mark them QUEUED, and defer one task each."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    for platform in ("tiktok", "youtube"):
        aid = acc_repo.create(session, {
            "profile_name": f"A-{platform}",
            "geelark_phone_id": f"ph-{platform}",
            "warmed_up": 1,
            f"{platform}_logged_in": 1,
        })
        f = tmp_path / f"{platform}.mp4"; f.write_bytes(b"x")
        vid = vid_repo.create(session, f"{platform}.mp4", str(f))
        pub_repo.create(session, video_id=vid, platform=platform)

    import asyncio
    from app.tasks.auto_publish import auto_publish_scan_task
    result = asyncio.run(auto_publish_scan_task(context=None, timestamp=0))
    assert "2 publication(s) deferred" in result
    # Both publications should now be QUEUED.
    rows = []
    for plat in ("tiktok", "youtube"):
        rows.extend([p for p in pub_repo.list_for_video(
            session, vid)] if False else [])  # noqa
    # Read all publications to verify.
    import sqlalchemy as sa
    statuses = [r[0] for r in session.exec(
        sa.text("SELECT result FROM publications")).all()]
    assert set(statuses) == {"QUEUED"}
```

> NOTE: the InMemoryConnector's exact job-list attribute name has
> shifted across procrastinate versions. If `procrastinate_app.connector.jobs`
> doesn't exist on the installed version, replace the access by
> reading whatever attribute the connector exposes (search the
> connector source under `.venv/lib/python*/site-packages/procrastinate/testing.py`).

- [ ] **Step 8.5: Run task tests**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_tasks_publish.py -v
```

Expected: all PASS.

- [ ] **Step 8.6: Commit**

```bash
git add app/tasks/__init__.py app/tasks/publish.py app/tasks/auto_publish.py \
        tests/test_tasks_publish.py
git commit -m "feat(tasks): per-publication chain + hourly auto_publish_scan

publish_publication_task submits one publication to GeeLark and
chains check_publication_status_task with schedule_in=30s (up to 40
attempts, mirrors phase-2 cadence). auto_publish_scan_task runs
hourly: for each platform, plan_assignments claims eligible PENDING
publications as QUEUED and defers one publish_publication_task each."
```

---

### Task 9: API — uploads create publications, publish requires platform

**Files:**
- Modify: `app/api/videos.py`
- Modify: `app/api/accounts.py`
- Modify: `app/services/drive_import.py` (one-line touch)
- Modify: `tests/test_api.py`

- [ ] **Step 9.1: Write failing API tests**

Append to `tests/test_api.py`:

```python
def test_upload_creates_default_tiktok_publication(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/upload",
                     files={"files": ("u1.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    v = client.get("/api/videos").json()[0]
    assert "publications" in v
    assert [p["platform"] for p in v["publications"]] == ["tiktok"]
    assert v["publications"][0]["result"] == "PENDING"


def test_upload_with_all_four_platforms(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.post(
        "/api/videos/upload",
        data={"platforms": "tiktok,youtube,instagram,facebook"},
        files={"files": ("u2.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    v = client.get("/api/videos").json()[0]
    platforms = sorted(p["platform"] for p in v["publications"])
    assert platforms == ["facebook", "instagram", "tiktok", "youtube"]


def test_upload_with_unknown_platform_returns_400(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.post(
        "/api/videos/upload",
        data={"platforms": "tiktok,linkedin"},
        files={"files": ("u3.mp4", b"x", "video/mp4")})
    assert r.status_code == 400


def test_publish_endpoint_requires_platform(client):
    client.post("/api/login", json={"password": "pw"})
    assert client.post("/api/videos/publish").status_code == 400
    assert client.post("/api/videos/publish?platform=linkedin").status_code == 400


def test_publish_endpoint_accepts_each_known_platform(client):
    client.post("/api/login", json={"password": "pw"})
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        r = client.post(f"/api/videos/publish?platform={plat}")
        assert r.status_code == 200, plat


def test_delete_video_cascades_publications(client):
    client.post("/api/login", json={"password": "pw"})
    client.post("/api/videos/upload",
                 data={"platforms": "tiktok,instagram,facebook"},
                 files={"files": ("u4.mp4", b"x", "video/mp4")})
    v = client.get("/api/videos").json()[0]
    assert len(v["publications"]) == 3
    assert client.delete(f"/api/videos/{v['id']}").status_code == 200
    from app.db import session_scope
    import sqlalchemy as sa
    with session_scope() as s:
        n = s.exec(sa.text(
            "SELECT COUNT(*) FROM publications WHERE video_id=:v"),
            {"v": v["id"]}).first()[0]
    assert n == 0


def test_accounts_endpoint_exposes_all_platform_columns(client):
    client.post("/api/login", json={"password": "pw"})
    payload = {"profile_name": "MULTI-X"}
    for plat in ("youtube", "instagram", "facebook"):
        payload[f"{plat}_logged_in"] = 1
        payload[f"{plat}_username"] = f"@{plat}"
    aid = client.post("/api/accounts", json=payload).json()["id"]
    row = next(a for a in client.get("/api/accounts").json()
                if a["id"] == aid)
    for plat in ("youtube", "instagram", "facebook"):
        assert row[f"{plat}_logged_in"] == 1
        assert row[f"{plat}_username"] == f"@{plat}"
        assert f"last_posted_at_{plat}" in row


def test_accounts_publish_endpoint_requires_platform(client):
    client.post("/api/login", json={"password": "pw"})
    gid = client.post("/api/groups", json={"name": "PG"}).json()["id"]
    aid = client.post("/api/accounts",
                       json={"profile_name": "PA",
                              "group_id": gid}).json()["id"]
    assert client.post(
        "/api/accounts/publish",
        json={"account_ids": [aid]}).status_code == 400
    assert client.post(
        "/api/accounts/publish",
        json={"account_ids": [aid], "platform": "linkedin"}).status_code == 400


def test_per_video_publish_returns_queued(client, monkeypatch):
    """POST /api/videos/{vid}/publish?platform=... defers and returns
    {status: QUEUED, job_id, publication_id}."""
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts",
                       json={"profile_name": "PVP",
                              "geelark_phone_id": "phPVP",
                              "warmed_up": 1,
                              "tiktok_logged_in": 1}).json()["id"]
    r = client.post("/api/videos/upload",
                     files={"files": ("pvp.mp4", b"x", "video/mp4")})
    vid = client.get("/api/videos").json()[0]["id"]

    # Stub the Procrastinate defer so we don't need a real connector
    # outside the InMemory fixture.
    deferred = []
    async def fake_defer_async(**kw):
        deferred.append(kw)
        return type("J", (), {"id": "fake-job-id"})()
    from app.tasks.publish import publish_publication_task
    monkeypatch.setattr(publish_publication_task, "defer_async",
                         fake_defer_async)

    r = client.post(f"/api/videos/{vid}/publish?platform=tiktok",
                     json={"account_id": aid})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "QUEUED"
    assert "job_id" in body and "publication_id" in body
    assert deferred and deferred[0]["publication_id"] == body["publication_id"]
```

Update existing tests that still reference the old Video.result shape
(e.g. `test_delete_account_with_videos_returns_200_and_nulls_video`,
`test_patch_video_sets_account`, `test_publish_stream_returns_event_stream`,
`test_publish_stream_accepts_force_param`) to use publications and
include `platform=tiktok` on publish endpoints. The pattern is exactly
the previous YouTube-only plan's; reuse those bodies.

- [ ] **Step 9.2: Run to verify failure**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_api.py -v -x
```

Expected: many failures.

- [ ] **Step 9.3: Update `app/api/videos.py`**

Replace it with the SQLModel-style equivalent of the previous
multi-platform plan. Key changes:
- `_parse_platforms(raw) -> list[str]` using `platforms.PLATFORMS`
- `_required_platform(p) -> str` using `platforms.validate`
- `POST /upload` accepts new `platforms` form field; creates one
  `Publication` per platform after the `Video` is created
- `PATCH /{vid}` body becomes `{platform, account_id}` and writes via
  `pub_repo`
- `POST /publish` and `POST /publish-stream` require `platform` query
  param
- `POST /{vid}/publish` requires `platform` query param;
  finds-or-creates the publication for (vid, platform), marks it
  QUEUED with the chosen account_id (from JSON body), defers
  `publish_publication_task`, returns
  `{status: "QUEUED", job_id, publication_id}`
- `GET /` returns embedded publications array (already via
  `vid_repo.list_all`)

(Full file: ~180 lines. Follow the previous plan's `app/api/videos.py`
template but with `session: Session = Depends(get_session)` injection
on every endpoint, no `_db()` helper.)

- [ ] **Step 9.4: Update `app/api/accounts.py::publish_accounts`**

Replace the function:

```python
@router.post("/publish")
def publish_accounts(body: dict,
                     session: Session = Depends(get_session)):
    from app.services import posting_service as ps
    from app.services import geelark_service
    from app.repositories import settings as srepo
    from app.services.platforms import validate as validate_platform
    from app.sse import sse_from_generator, locked_generator
    account_ids = body.get("account_ids") or []
    try:
        platform = validate_platform(body.get("platform"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
            detail="platform must be one of tiktok/youtube/instagram/facebook")
    _validate_account_ids(session, account_ids)
    cd = float(srepo.get_all(session).get("post_cooldown_hours", 2))
    return sse_from_generator(locked_generator(
        ps.publish_for_accounts(session, geelark_service.make_client(),
                                 account_ids, platform=platform,
                                 cooldown_hours=cd)))
```

- [ ] **Step 9.5: Touch `app/services/drive_import.py`**

Find every `vid_repo.create(...)` call inside drive_import. After each,
add:

```python
from app.repositories import publications as pub_repo
pub_repo.create(session, video_id=vid, platform="tiktok")
```

(`grep -n "vid_repo.create" app/services/drive_import.py` first; there
should be one or two sites.)

- [ ] **Step 9.6: Update `app/api/dashboard.py`**

Find the existing SUCCESS-counting query. Rewrite to read
publications:

```python
posted_today = session.exec(sa.text("""
    SELECT COUNT(*) FROM publications
    WHERE result = 'SUCCESS'
      AND posted_at >= CURRENT_DATE
      AND posted_at <  CURRENT_DATE + INTERVAL '1 day'
""")).first()[0]
```

And `pending_videos` becomes:

```python
pending_videos = session.exec(sa.text("""
    SELECT COUNT(DISTINCT v.id) FROM videos v
    WHERE EXISTS (
      SELECT 1 FROM publications p
      WHERE p.video_id = v.id AND p.result = 'PENDING'
    )
""")).first()[0]
```

- [ ] **Step 9.7: Run API tests**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest tests/test_api.py -v
```

Expected: all PASS.

- [ ] **Step 9.8: Run full suite**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest -q
```

Expected: green.

- [ ] **Step 9.9: Commit**

```bash
git add app/api/videos.py app/api/accounts.py app/api/dashboard.py \
        app/services/drive_import.py tests/test_api.py
git commit -m "feat(api): publications upload + per-platform publish endpoints"
```

---

### Task 10: Frontend api.ts

**Files:** `frontend/src/api.ts`.

Identical to the previous multi-platform plan — export `Platform`,
`PLATFORMS`, `PLATFORM_LABEL`; change `publishAccounts(ids, platform)`
and `setVideoAccount(vid, platform, accountId)`; add `publishVideo(vid,
platform, accountId)` that posts to `/api/videos/{vid}/publish`. Build
will fail until Task 11/12 land. Commit at the end of Task 12 to keep
the frontend changes together.

(Implementation steps: read the previous plan's Task 9 verbatim — only
difference is the new `publishVideo` helper.)

---

### Task 11: Frontend Videos.tsx

Same as the previous multi-platform plan Task 10 — platform
multi-select on upload, per-publication badges in the list, history
flatMap. Do NOT commit yet.

---

### Task 12: Frontend Accounts.tsx

Same as the previous multi-platform plan Task 11 — YT / IG / FB
columns in the list, per-platform checkbox + username inputs in the
edit form. Run `cd frontend && npm run build` and confirm green.

Commit the three frontend files together:

```bash
git add frontend/src/api.ts frontend/src/pages/Videos.tsx \
        frontend/src/pages/Accounts.tsx
git commit -m "feat(frontend): platform multi-select + per-publication badges + YT/IG/FB columns"
```

---

### Task 13: Deploy runbook + E2E verification + PR

**Files:**
- Modify: `docs/production-deploy.md`

- [ ] **Step 13.1: Runbook update**

Insert a section in `docs/production-deploy.md` (in the section that
covers Alembic upgrades). Suggested text:

````markdown
### Multi-platform-publish migration (2026-05-28)

This release adds a `publications` table and nine new `accounts`
columns; it DROPS six columns from `videos`. The Alembic migration
is destructive on `videos`. Back up the production DB first:

```bash
sudo systemctl stop mkt mkt-worker
pg_dump -U mkt -F c -d mkt > /var/backups/mkt.$(date +%Y%m%d-%H%M%S).before-multi-platform.dump
cd /opt/mkt
uv run alembic upgrade head
sudo systemctl start mkt mkt-worker
```

To roll back:

```bash
sudo systemctl stop mkt mkt-worker
cd /opt/mkt
uv run alembic downgrade -1
# downgrade preserves TikTok state; any YT/IG/FB publications written
# between upgrade and downgrade are lost.
sudo systemctl start mkt mkt-worker
```

For a full restore from the dump:

```bash
pg_restore -U mkt -d mkt --clean --if-exists \
  /var/backups/mkt.<timestamp>.before-multi-platform.dump
```
````

- [ ] **Step 13.2: Full backend sweep**

```bash
DATABASE_URL=postgresql+psycopg://"$USER"@127.0.0.1:5432/mkt_test \
  uv run pytest -q
```

Expected: 0 failures. New baseline (~330+ tests including the new
suites).

- [ ] **Step 13.3: Frontend build**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: success.

- [ ] **Step 13.4: Local smoke test**

```bash
# Terminal 1: web
./run.sh
# Terminal 2: worker
uv run procrastinate -a app.tasks:procrastinate_app worker --queues phones,default
# Browser:
# - login with DASHBOARD_PASSWORD
# - upload a clip with all four platforms checked, verify 4 publications
# - tick YT/IG/FB logged_in on a phone, save, reload, verify persistence
# - (Optional, costs GeeLark credits) trigger /api/videos/{vid}/publish?platform=tiktok
```

- [ ] **Step 13.5: Commit + push + PR**

```bash
git add docs/production-deploy.md
git commit -m "docs(deploy): runbook for multi-platform-publish migration"
git log --oneline main..HEAD
git push -u origin feat/multi-platform-publish
gh pr create --title "feat: multi-platform auto-publish (TT + YT + IG + FB)" \
  --body "$(cat <<'EOF'
## Summary
- Adds `publications(video_id, platform, ...)` SQLModel + Alembic migration so one video can publish to TikTok / YouTube / Instagram / Facebook from one upload.
- `accounts` gains 9 new columns (`<platform>_logged_in`, `<platform>_username`, `last_posted_at_<platform>` for YT/IG/FB); per-platform cooldown.
- `geelark_client` adds `create_youtube_post_task`, `create_instagram_post_task`, `create_facebook_post_task`.
- `posting_service` dispatches by platform; `plan_assignments` claims PENDING publications synchronously by flipping result to QUEUED.
- New `app/tasks/auto_publish.py` periodic Procrastinate task (cron `0 * * * *`) defers one `publish_publication_task` per QUEUED publication; `check_publication_status_task` chains itself every 30s up to 40 attempts.
- Frontend: platform multi-select on upload, per-publication badges, three new account columns.

Spec: `docs/superpowers/specs/2026-05-28-multi-platform-publish-design.md`

## Test plan
- [x] `uv run pytest -q` against PG `mkt_test` — all green
- [x] `cd frontend && npm run build` — clean
- [x] `alembic upgrade head; alembic downgrade -1; alembic upgrade head` round-trip
- [ ] Boot web + worker locally, upload with all four platforms, verify publications + queued task
- [ ] (Optional, real GeeLark) publish one test publication per new platform
EOF
)"
```

---

## Notes for the implementer

- **PG-first:** all DB work is via `Session` + `session.exec(select(...))`. No raw connections, no `sqlite3.*`, no `_migrate(conn)` style. Schema changes via Alembic only.
- **Tasks-first:** new "post a thing" flows MUST defer to Procrastinate. The web tier already keeps the legacy `OPERATION_LOCK` for the old synchronous publish endpoints; don't add new sync paths.
- **`platforms.py` is the only file that hard-codes the four-platform list.** Import from there everywhere.
- **TDD:** each task starts with a failing test. Don't skip the "run to verify failure" step.
- **Commits:** every task ends with a commit. Don't squash before merging.
- **Cost discipline:** GeeLark cloud phones cost per running minute. Procrastinate's `lock="phones"` discipline is preserved end-to-end in the worker; don't break it.
- **CLAUDE.md** is stale (mentions SQLite + APScheduler) but is owned by the user; flag in the PR description but don't edit it as part of this PR.
