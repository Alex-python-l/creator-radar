# Self-Engagement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add worker-backed TikTok AI comment engagement for owned videos, with manual links now and stored publication share links for later target picking.

**Architecture:** Publishing remains in `publications`; engagement gets separate `engagement_tasks` and `engagement_runs` tables. The API creates batches and defers Procrastinate jobs; GeeLark phone work runs only in the worker `phones` queue with `lock="phones"`. Accounts can only run one active phone task across publishing and engagement.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy/Alembic, PostgreSQL, Procrastinate, React 19, Vite, pytest, existing GeeLark OpenAPI client.

---

## Preflight Notes

- Before implementation, run `git status --short`. At plan creation time, `app/repositories/accounts.py` and `tests/test_repositories.py` had pre-existing local modifications. Do not overwrite them. If you must edit either file, inspect the current diff first and preserve the user's changes.
- The first implementation target is manual TikTok links from the Accounts page. Publication `share_link` storage is included because it is a small schema and reconciliation change that enables the next UI iteration.
- Do not run real GeeLark tasks in automated tests. Use fakes and monkeypatches.

## File Map

Create:

- `app/repositories/engagement.py` - persistence helpers for engagement batches and runs.
- `app/services/engagement_service.py` - validation, planning, submission, and reconciliation.
- `app/tasks/engagement.py` - Procrastinate task wrappers.
- `app/api/engagement.py` - FastAPI endpoints.
- `tests/test_repositories_engagement.py` - repository tests.
- `tests/test_engagement_service.py` - service tests.
- `tests/test_tasks_engagement.py` - worker task tests.
- `tests/test_api_engagement.py` - API tests.
- `alembic/versions/b7c9d2e4f601_engagement_tasks.py` - schema migration.

Modify:

- `app/models.py` - add `EngagementTask`, `EngagementRun`, and publication share-link columns.
- `app/tasks/__init__.py` - import `app.tasks.engagement`.
- `app/main.py` - include engagement router.
- `geelark_client.py` - add TikTok AI comment client method.
- `tests/test_geelark_client.py` - add payload tests.
- `tests/conftest.py` - truncate new engagement tables between tests.
- `app/repositories/publications.py` - share-link setter and model dump exposure.
- `app/services/posting_service.py` - store `shareLink`; treat engagement in-flight as phone busy.
- `frontend/src/api.ts` - engagement API methods and types.
- `frontend/src/pages/Accounts.tsx` - `AI Comment` toolbar action, modal, recent status.
- `frontend/src/i18n/locales/en.json` and `frontend/src/i18n/locales/zh.json` - user-facing strings.

---

### Task 1: Schema, Models, And Test Cleanup

**Files:**
- Modify: `app/models.py`
- Create: `alembic/versions/b7c9d2e4f601_engagement_tasks.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_repositories_engagement.py`

- [ ] **Step 1: Write the failing model smoke test**

Create `tests/test_repositories_engagement.py` with this initial test. It should fail before the engagement models and tables exist.

```python
def test_engagement_models_persist_task_and_run(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.models import EngagementRun, EngagementTask

    gid = group_repo.create(session, name="Self Engage")
    aid = acc_repo.create(session, {
        "profile_name": "M1",
        "geelark_phone_id": "ph-m1",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })

    task = EngagementTask(
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    run = EngagementRun(
        engagement_task_id=task.id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    assert task.status == "PENDING"
    assert task.target_links == [
        "https://www.tiktok.com/@owner/video/1234567890"
    ]
    assert run.result == "PENDING"
    assert run.account_id == aid
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
uv run pytest tests/test_repositories_engagement.py::test_engagement_models_persist_task_and_run -q
```

Expected: fail with `ImportError` for `EngagementRun` / `EngagementTask` or missing engagement tables.

- [ ] **Step 3: Add SQLModel classes**

In `app/models.py`, import `JSON` from SQLAlchemy and add publication columns plus the two new table models. Use plain strings for statuses to match `Publication`.

```python
from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer, JSON,
                        UniqueConstraint, text)
```

Add these fields to `Publication` near `shareLink`-related failure detail fields:

```python
    share_link: Optional[str] = None
    share_link_captured_at: Optional[datetime] = None
```

Add these classes after `Publication`:

```python
class EngagementTask(SQLModel, table=True):
    __tablename__ = "engagement_tasks"
    __table_args__ = (
        Index("idx_engagement_tasks_group_status", "group_id", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("groups.id", ondelete="SET NULL")),
    )
    action: str = Field(nullable=False)
    source: str = Field(nullable=False)
    target_links: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default=text("'[]'::json")),
    )
    target_publication_ids: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default=text("'[]'::json")),
    )
    comment_probability: int = Field(default=100, nullable=False)
    status: str = Field(default="PENDING", nullable=False)
    created_by: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    finished_at: Optional[datetime] = None


class EngagementRun(SQLModel, table=True):
    __tablename__ = "engagement_runs"
    __table_args__ = (
        Index("idx_engagement_runs_task", "engagement_task_id"),
        Index("idx_engagement_runs_account_result", "account_id", "result"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_task_id: int = Field(
        sa_column=Column(Integer, ForeignKey("engagement_tasks.id", ondelete="CASCADE"), nullable=False)
    )
    account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL")),
    )
    target_publication_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("publications.id", ondelete="SET NULL")),
    )
    target_account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL")),
    )
    target_link: str = Field(nullable=False)
    action: str = Field(nullable=False)
    result: str = Field(default="PENDING", nullable=False)
    geelark_task_id: Optional[str] = None
    schedule_at: Optional[int] = None
    notes: Optional[str] = None
    fail_code: Optional[int] = None
    fail_screenshot: Optional[str] = None
    cost: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
```

- [ ] **Step 4: Add Alembic migration**

Create `alembic/versions/b7c9d2e4f601_engagement_tasks.py` after the current head `4576004906dc`.

Migration body:

```python
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

revision: str = "b7c9d2e4f601"
down_revision: Union[str, Sequence[str], None] = "4576004906dc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("publications", sa.Column("share_link", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column("publications", sa.Column("share_link_captured_at", sa.DateTime(), nullable=True))
    op.create_table(
        "engagement_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_links", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("target_publication_ids", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("comment_probability", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_engagement_tasks_group_status", "engagement_tasks", ["group_id", "status"], unique=False)
    op.create_table(
        "engagement_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("engagement_task_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("target_publication_id", sa.Integer(), nullable=True),
        sa.Column("target_account_id", sa.Integer(), nullable=True),
        sa.Column("target_link", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("result", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("geelark_task_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("schedule_at", sa.Integer(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("fail_code", sa.Integer(), nullable=True),
        sa.Column("fail_screenshot", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("cost", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["engagement_task_id"], ["engagement_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_publication_id"], ["publications.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_engagement_runs_task", "engagement_runs", ["engagement_task_id"], unique=False)
    op.create_index("idx_engagement_runs_account_result", "engagement_runs", ["account_id", "result"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_engagement_runs_account_result", table_name="engagement_runs")
    op.drop_index("idx_engagement_runs_task", table_name="engagement_runs")
    op.drop_table("engagement_runs")
    op.drop_index("idx_engagement_tasks_group_status", table_name="engagement_tasks")
    op.drop_table("engagement_tasks")
    op.drop_column("publications", "share_link_captured_at")
    op.drop_column("publications", "share_link")
```

- [ ] **Step 5: Update test truncation**

In `tests/conftest.py`, update `_TABLES`:

```python
_TABLES = "engagement_runs, engagement_tasks, videos, accounts, groups, settings, job_runs"
```

- [ ] **Step 6: Run migration and focused test**

Run:

```bash
uv run alembic upgrade head
uv run pytest tests/test_repositories_engagement.py::test_engagement_models_persist_task_and_run -q
```

Expected: migration succeeds and the model smoke test passes.

- [ ] **Step 7: Commit schema**

```bash
git add app/models.py alembic/versions/*_engagement_tasks.py tests/conftest.py tests/test_repositories_engagement.py
git commit -m "feat: add engagement task schema"
```

---

### Task 2: Engagement Repository

**Files:**
- Create: `app/repositories/engagement.py`
- Modify: `tests/test_repositories_engagement.py`

- [ ] **Step 1: Add repository behavior tests**

Append tests for repository creation, lifecycle, in-flight detection, parent status recompute, and listing.

```python
def test_create_engagement_task_and_run(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="Self Engage Repo")
    aid = acc_repo.create(session, {
        "profile_name": "M1",
        "geelark_phone_id": "ph-m1",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })

    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.create_run(
        session,
        engagement_task_id=task_id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )

    task = eng_repo.get_task(session, task_id)
    run = eng_repo.get_run(session, run_id)

    assert task["status"] == "PENDING"
    assert task["target_links"] == [
        "https://www.tiktok.com/@owner/video/1234567890"
    ]
    assert run["result"] == "PENDING"
    assert run["account_id"] == aid


def test_engagement_run_lifecycle_and_parent_status(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="Lifecycle")
    aid = acc_repo.create(session, {
        "profile_name": "M2",
        "geelark_phone_id": "ph-m2",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })
    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.create_run(
        session,
        engagement_task_id=task_id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )

    eng_repo.mark_queued(session, run_id)
    assert eng_repo.account_has_inflight(session, aid) is True

    eng_repo.mark_initiated(
        session,
        run_id,
        task_id="gee-1",
        schedule_at=123,
        notes="submitted to GeeLark",
    )
    row = eng_repo.get_run(session, run_id)
    assert row["result"] == "INITIATED"
    assert row["geelark_task_id"] == "gee-1"

    eng_repo.set_result(session, run_id, "SUCCESS", "completed", cost=135)
    eng_repo.recompute_task_status(session, task_id)

    assert eng_repo.account_has_inflight(session, aid) is False
    assert eng_repo.get_run(session, run_id)["cost"] == 135
    assert eng_repo.get_task(session, task_id)["status"] == "SUCCESS"


def test_parent_status_partial_failure(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="Partial")
    a1 = acc_repo.create(session, {"profile_name": "A1", "group_id": gid})
    a2 = acc_repo.create(session, {"profile_name": "A2", "group_id": gid})
    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    r1 = eng_repo.create_run(session, engagement_task_id=task_id, account_id=a1,
                             target_link="https://www.tiktok.com/@owner/video/1234567890",
                             action="tiktok_ai_comment")
    r2 = eng_repo.create_run(session, engagement_task_id=task_id, account_id=a2,
                             target_link="https://www.tiktok.com/@owner/video/1234567890",
                             action="tiktok_ai_comment")
    eng_repo.set_result(session, r1, "SUCCESS", "completed")
    eng_repo.set_result(session, r2, "FAILURE", "blocked",
                        fail_code=88, fail_screenshot="https://img/fail.jpg")
    eng_repo.recompute_task_status(session, task_id)

    assert eng_repo.get_task(session, task_id)["status"] == "PARTIAL_FAILURE"
    assert eng_repo.get_run(session, r2)["fail_screenshot"] == "https://img/fail.jpg"
```

- [ ] **Step 2: Run repository tests and confirm failures**

Run:

```bash
uv run pytest tests/test_repositories_engagement.py -q
```

Expected: fail because `app/repositories/engagement.py` is missing.

- [ ] **Step 3: Implement repository helpers**

Create `app/repositories/engagement.py` with these functions and exact status semantics.

```python
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Session, select

from app.models import Account, EngagementRun, EngagementTask

TERMINAL_RESULTS = ("SUCCESS", "FAILURE", "SKIPPED")
INFLIGHT_RESULTS = ("QUEUED", "INITIATED")


def _dump_task(obj: EngagementTask | None) -> Optional[dict]:
    return obj.model_dump() if obj else None


def _dump_run(obj: EngagementRun | None) -> Optional[dict]:
    return obj.model_dump() if obj else None


def create_task(session: Session, *, group_id: int | None, action: str,
                source: str, target_links: list[str],
                target_publication_ids: list[int],
                comment_probability: int) -> int:
    obj = EngagementTask(
        group_id=group_id,
        action=action,
        source=source,
        target_links=list(target_links),
        target_publication_ids=list(target_publication_ids),
        comment_probability=comment_probability,
        status="PENDING",
    )
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def get_task(session: Session, task_id: int) -> Optional[dict]:
    return _dump_task(session.get(EngagementTask, task_id))


def list_tasks(session: Session, *, group_id: int | None = None,
               limit: int = 50) -> list[dict]:
    stmt = select(EngagementTask).order_by(EngagementTask.id.desc()).limit(max(1, min(limit, 200)))
    if group_id is not None:
        stmt = stmt.where(EngagementTask.group_id == group_id)
    return [r.model_dump() for r in session.exec(stmt).all()]


def create_run(session: Session, *, engagement_task_id: int,
               account_id: int | None, target_link: str, action: str,
               target_publication_id: int | None = None,
               target_account_id: int | None = None) -> int:
    obj = EngagementRun(
        engagement_task_id=engagement_task_id,
        account_id=account_id,
        target_publication_id=target_publication_id,
        target_account_id=target_account_id,
        target_link=target_link,
        action=action,
        result="PENDING",
    )
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id
```

Also implement `get_run`, `list_runs_for_task`, `mark_queued`, `mark_initiated`, `set_result`, `mark_skipped`, `account_has_inflight`, and `recompute_task_status` with this behavior:

```python
def get_run(session: Session, run_id: int) -> Optional[dict]:
    return _dump_run(session.get(EngagementRun, run_id))


def list_runs_for_task(session: Session, task_id: int) -> list[dict]:
    stmt = (
        select(EngagementRun, Account.profile_name)
        .outerjoin(Account, Account.id == EngagementRun.account_id)
        .where(EngagementRun.engagement_task_id == task_id)
        .order_by(EngagementRun.id)
    )
    out = []
    for run, profile_name in session.exec(stmt).all():
        row = run.model_dump()
        row["account_profile_name"] = profile_name
        out.append(row)
    return out


def mark_queued(session: Session, run_id: int) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.result = "QUEUED"
    session.add(run)
    _touch_parent(session, run.engagement_task_id, status="QUEUED")
    session.commit()


def mark_initiated(session: Session, run_id: int, *, task_id: str,
                   schedule_at: int, notes: str) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.result = "INITIATED"
    run.geelark_task_id = task_id
    run.schedule_at = schedule_at
    run.notes = notes
    run.started_at = datetime.utcnow()
    session.add(run)
    _touch_parent(session, run.engagement_task_id, status="RUNNING")
    session.commit()


def set_result(session: Session, run_id: int, result: str, notes: str,
               *, fail_code: int | None = None,
               fail_screenshot: str | None = None,
               cost: int | None = None) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.result = result
    run.notes = notes
    if fail_code is not None:
        run.fail_code = fail_code
    if fail_screenshot is not None:
        run.fail_screenshot = fail_screenshot
    if cost is not None:
        run.cost = cost
    run.finished_at = datetime.utcnow()
    run.last_checked_at = datetime.utcnow()
    session.add(run)
    session.commit()


def account_has_inflight(session: Session, account_id: int,
                         *, exclude_run_id: int | None = None) -> bool:
    stmt = (
        select(EngagementRun.id)
        .where(EngagementRun.account_id == account_id)
        .where(EngagementRun.result.in_(INFLIGHT_RESULTS))
    )
    if exclude_run_id is not None:
        stmt = stmt.where(EngagementRun.id != exclude_run_id)
    return session.exec(stmt).first() is not None
```

For `recompute_task_status`, count all child results:

```python
def recompute_task_status(session: Session, task_id: int) -> None:
    task = session.get(EngagementTask, task_id)
    if task is None:
        return
    runs = session.exec(
        select(EngagementRun.result).where(EngagementRun.engagement_task_id == task_id)
    ).all()
    values = [r[0] if isinstance(r, tuple) else r for r in runs]
    if not values:
        task.status = "FAILURE"
        task.summary = "0 runs"
        task.finished_at = datetime.utcnow()
    elif any(v in INFLIGHT_RESULTS for v in values):
        task.status = "RUNNING"
    elif all(v == "SUCCESS" for v in values):
        task.status = "SUCCESS"
        task.finished_at = datetime.utcnow()
    elif any(v == "SUCCESS" for v in values):
        task.status = "PARTIAL_FAILURE"
        task.finished_at = datetime.utcnow()
    elif all(v == "SKIPPED" for v in values):
        task.status = "FAILURE"
        task.finished_at = datetime.utcnow()
    else:
        task.status = "FAILURE"
        task.finished_at = datetime.utcnow()
    task.updated_at = datetime.utcnow()
    counts = {v: values.count(v) for v in sorted(set(values))}
    task.summary = ", ".join(f"{k.lower()}={v}" for k, v in counts.items())
    session.add(task)
    session.commit()
```

Add a private `_touch_parent()` used by queued and initiated transitions.

- [ ] **Step 4: Run repository tests**

Run:

```bash
uv run pytest tests/test_repositories_engagement.py -q
```

Expected: pass.

- [ ] **Step 5: Commit repository**

```bash
git add app/repositories/engagement.py tests/test_repositories_engagement.py
git commit -m "feat: add engagement repository"
```

---

### Task 3: GeeLark Client Method

**Files:**
- Modify: `geelark_client.py`
- Modify: `tests/test_geelark_client.py`

- [ ] **Step 1: Add failing client tests**

Append to `tests/test_geelark_client.py`:

```python
def test_create_tiktok_ai_comment_task_payload_and_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskId": "comment-1"}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")

    tid = c.create_tiktok_ai_comment_task(
        "env-1",
        ["https://www.tiktok.com/@owner/video/1234567890"],
        schedule_at=123,
        comment_probability=100,
        name="AI comment",
        remark="owned video engagement",
    )

    assert tid == "comment-1"
    assert captured["path"] == "/open/v1/rpa/task/tiktokRandomComment"
    assert captured["payload"] == {
        "scheduleAt": 123,
        "id": "env-1",
        "useAi": 1,
        "links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "commentProbability": 100,
        "name": "AI comment",
        "remark": "owned video engagement",
    }


def test_create_tiktok_ai_comment_task_defaults_schedule(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient,
        "_post",
        lambda self, path, payload: captured.update(payload=payload) or {"taskId": "comment-2"},
    )
    c = geelark_client.GeeLarkClient("token")
    c.create_tiktok_ai_comment_task(
        "env-2",
        ["https://www.tiktok.com/@owner/video/1234567890"],
    )
    assert isinstance(captured["payload"]["scheduleAt"], int)
    assert captured["payload"]["commentProbability"] == 100


def test_create_tiktok_ai_comment_task_raises_when_taskid_missing(monkeypatch):
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                        lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="tiktok AI comment task"):
        c.create_tiktok_ai_comment_task(
            "env-3",
            ["https://www.tiktok.com/@owner/video/1234567890"],
        )
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_geelark_client.py::test_create_tiktok_ai_comment_task_payload_and_endpoint -q
```

Expected: fail because the method does not exist.

- [ ] **Step 3: Implement client method**

Add to `GeeLarkClient` near the other RPA task methods:

```python
    def create_tiktok_ai_comment_task(
        self,
        env_id: str,
        links: list[str],
        *,
        schedule_at: int | None = None,
        comment_probability: int = 100,
        name: str | None = None,
        remark: str | None = None,
    ) -> str:
        """Schedule a TikTok AI random-comment task. Returns the task ID."""
        import time as _time
        payload = {
            "scheduleAt": int(schedule_at if schedule_at is not None else _time.time()),
            "id": env_id,
            "useAi": 1,
            "links": list(links),
            "commentProbability": int(comment_probability),
        }
        if name:
            payload["name"] = name[:128]
        if remark:
            payload["remark"] = remark[:200]
        data = self._post("/open/v1/rpa/task/tiktokRandomComment", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"tiktok AI comment task created but no taskId: {data}")
        return task_id
```

- [ ] **Step 4: Run client tests**

Run:

```bash
uv run pytest tests/test_geelark_client.py -q
```

Expected: pass.

- [ ] **Step 5: Commit client method**

```bash
git add geelark_client.py tests/test_geelark_client.py
git commit -m "feat: add tiktok ai comment client"
```

---

### Task 4: Engagement Service Planning And Validation

**Files:**
- Create: `app/services/engagement_service.py`
- Modify: `tests/test_engagement_service.py`

- [ ] **Step 1: Write validation and planning tests**

Create `tests/test_engagement_service.py`:

```python
import pytest


def _group_with_accounts(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Engage Group")
    owner = acc_repo.create(session, {
        "profile_name": "Owner",
        "geelark_phone_id": "ph-owner",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })
    commenter = acc_repo.create(session, {
        "profile_name": "Commenter",
        "geelark_phone_id": "ph-commenter",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })
    logged_out = acc_repo.create(session, {
        "profile_name": "Logged Out",
        "geelark_phone_id": "ph-out",
        "group_id": gid,
        "tiktok_logged_in": 0,
    })
    return gid, owner, commenter, logged_out


def test_create_comment_batch_for_manual_links(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner, commenter, logged_out],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )

    assert result["created_runs"] == 2
    assert result["skipped_runs"] == 1
    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert [r["account_id"] for r in runs] == [owner, commenter]


def test_create_comment_batch_rejects_invalid_link(session):
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    with pytest.raises(ValueError, match="TikTok video URL"):
        engagement_service.create_comment_batch(
            session,
            group_id=gid,
            account_ids=[owner],
            target_links=["https://example.com/not-tiktok"],
            target_publication_ids=[],
            comment_probability=100,
        )


def test_create_comment_batch_excludes_publication_owner(session, tmp_path):
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    video_path = tmp_path / "owned.mp4"
    video_path.write_bytes(b"x")
    vid = vid_repo.create(session, "owned.mp4", str(video_path), group_id=gid)
    pub_id = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.set_account(session, pub_id, owner)
    pub_repo.set_share_link(session, pub_id, "https://www.tiktok.com/@owner/video/1234567890")

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner, commenter],
        target_links=[],
        target_publication_ids=[pub_id],
        comment_probability=100,
    )

    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert [r["account_id"] for r in runs] == [commenter]
    assert runs[0]["target_publication_id"] == pub_id
    assert runs[0]["target_account_id"] == owner
```

- [ ] **Step 2: Add publication share-link repository test**

Append to `tests/test_repositories_publications.py`:

```python
def test_set_share_link(session):
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    vid = vid_repo.create(session, "s.mp4", "/tmp/s.mp4")
    pub_id = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.set_share_link(session, pub_id, "https://www.tiktok.com/@owner/video/1234567890")

    row = pub_repo.get(session, pub_id)
    assert row["share_link"] == "https://www.tiktok.com/@owner/video/1234567890"
    assert row["share_link_captured_at"] is not None
```

- [ ] **Step 3: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_engagement_service.py tests/test_repositories_publications.py::test_set_share_link -q
```

Expected: fail because the service and `set_share_link()` do not exist.

- [ ] **Step 4: Implement `set_share_link()`**

In `app/repositories/publications.py`, add:

```python
def set_share_link(session: Session, pub_id: int, share_link: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.share_link = share_link
    pub.share_link_captured_at = datetime.utcnow()
    session.add(pub)
    session.commit()
```

- [ ] **Step 5: Implement engagement service validation and planning**

Create `app/services/engagement_service.py`:

```python
import re
import time
from typing import Iterable

from app.repositories import accounts as acc_repo
from app.repositories import engagement as eng_repo
from app.repositories import publications as pub_repo

ACTION_TIKTOK_AI_COMMENT = "tiktok_ai_comment"
_TIKTOK_VIDEO_RE = re.compile(
    r"^https://(www\.)?tiktok\.com/@[^/]+/video/\d+([/?#].*)?$"
)
_TIKTOK_SHORT_RE = re.compile(r"^https://vt\.tiktok\.com/[A-Za-z0-9]+/?([?#].*)?$")


class PhoneBusy(RuntimeError):
    pass


def normalize_target_link(raw: str) -> str:
    link = (raw or "").strip()
    if _TIKTOK_VIDEO_RE.match(link) or _TIKTOK_SHORT_RE.match(link):
        return link
    raise ValueError("target link must be a TikTok video URL")


def _validate_probability(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("comment_probability must be between 0 and 100")
    if value < 0 or value > 100:
        raise ValueError("comment_probability must be between 0 and 100")
    return value
```

Add the batch creation function:

```python
def create_comment_batch(session, *, group_id: int, account_ids: list[int],
                         target_links: list[str],
                         target_publication_ids: list[int],
                         comment_probability: int = 100) -> dict:
    if not isinstance(account_ids, list) or not account_ids:
        raise ValueError("account_ids must be non-empty")
    probability = _validate_probability(comment_probability)

    accounts = [acc_repo.get(session, aid) for aid in account_ids]
    if any(a is None for a in accounts):
        raise ValueError("account not found")
    if any(a.get("group_id") != group_id for a in accounts):
        raise ValueError("all accounts must be in the selected group")

    manual_links = [normalize_target_link(link) for link in (target_links or [])]
    publication_targets = _publication_targets(session, target_publication_ids or [])
    all_target_links = manual_links + [t["target_link"] for t in publication_targets]
    if not all_target_links:
        raise ValueError("at least one target link is required")

    task_id = eng_repo.create_task(
        session,
        group_id=group_id,
        action=ACTION_TIKTOK_AI_COMMENT,
        source="publication_share_links" if publication_targets and not manual_links else "manual_links",
        target_links=all_target_links,
        target_publication_ids=[t["target_publication_id"] for t in publication_targets],
        comment_probability=probability,
    )

    created = 0
    skipped = 0
    for target in _expand_targets(manual_links, publication_targets):
        for account in accounts:
            if not _eligible_account(account):
                skipped += 1
                continue
            if target.get("target_account_id") is not None and account["id"] == target["target_account_id"]:
                skipped += 1
                continue
            eng_repo.create_run(
                session,
                engagement_task_id=task_id,
                account_id=account["id"],
                target_link=target["target_link"],
                target_publication_id=target.get("target_publication_id"),
                target_account_id=target.get("target_account_id"),
                action=ACTION_TIKTOK_AI_COMMENT,
            )
            created += 1
    if created == 0:
        raise ValueError("no eligible TikTok accounts selected")
    return {"task_id": task_id, "created_runs": created, "skipped_runs": skipped}
```

Implement `_publication_targets()`, `_expand_targets()`, and `_eligible_account()`:

```python
def _eligible_account(account: dict | None) -> bool:
    return bool(
        account
        and (account.get("geelark_phone_id") or "").strip()
        and account.get("tiktok_logged_in") == 1
    )


def _publication_targets(session, publication_ids: Iterable[int]) -> list[dict]:
    out = []
    for pub_id in publication_ids:
        pub = pub_repo.get(session, int(pub_id))
        if pub is None:
            raise ValueError("target publication not found")
        if pub.get("platform") != "tiktok":
            raise ValueError("target publication must be TikTok")
        link = normalize_target_link(pub.get("share_link") or "")
        out.append({
            "target_publication_id": pub["id"],
            "target_account_id": pub.get("account_id"),
            "target_link": link,
        })
    return out


def _expand_targets(manual_links: list[str], publication_targets: list[dict]) -> list[dict]:
    return [{"target_link": link} for link in manual_links] + publication_targets
```

- [ ] **Step 6: Run service tests**

Run:

```bash
uv run pytest tests/test_engagement_service.py tests/test_repositories_publications.py::test_set_share_link -q
```

Expected: pass.

- [ ] **Step 7: Commit service planning**

```bash
git add app/services/engagement_service.py app/repositories/publications.py tests/test_engagement_service.py tests/test_repositories_publications.py
git commit -m "feat: plan engagement comment batches"
```

---

### Task 5: Engagement Submit And Status Reconciliation

**Files:**
- Modify: `app/services/engagement_service.py`
- Modify: `tests/test_engagement_service.py`

- [ ] **Step 1: Add submit and reconciliation tests**

Append to `tests/test_engagement_service.py`:

```python
def test_submit_engagement_run_to_geelark_marks_initiated(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run = eng_repo.list_runs_for_task(session, result["task_id"])[0]

    class FakeClient:
        def create_tiktok_ai_comment_task(self, env_id, links, **kwargs):
            assert env_id == "ph-commenter"
            assert links == ["https://www.tiktok.com/@owner/video/1234567890"]
            assert kwargs["comment_probability"] == 100
            return "gee-comment-1"

    task_id = engagement_service.submit_run_to_geelark(
        session, FakeClient(), run_id=run["id"])

    assert task_id == "gee-comment-1"
    row = eng_repo.get_run(session, run["id"])
    assert row["result"] == "INITIATED"
    assert row["geelark_task_id"] == "gee-comment-1"


def test_check_engagement_run_success_stops_phone(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]
    eng_repo.mark_initiated(session, run_id, task_id="gee-ok", schedule_at=123,
                            notes="submitted")

    class DoneClient:
        stopped = []
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3, "cost": 135}]
        def stop_phones(self, ids):
            self.stopped.extend(ids)
            return {}

    client = DoneClient()
    outcome = engagement_service.check_engagement_run(session, client, run_id)

    assert outcome == "success"
    assert eng_repo.get_run(session, run_id)["result"] == "SUCCESS"
    assert eng_repo.get_run(session, run_id)["cost"] == 135
    assert client.stopped == ["ph-commenter"]


def test_check_engagement_run_failure_saves_detail(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]
    eng_repo.mark_initiated(session, run_id, task_id="gee-fail", schedule_at=123,
                            notes="submitted")

    class FailedClient:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 4}]
        def get_task_detail(self, task_id):
            return {"failCode": 20267, "failDesc": "Comment box missing",
                    "resultImages": ["https://img/first.jpg", "https://img/last.jpg"]}
        def stop_phones(self, ids):
            return {}

    outcome = engagement_service.check_engagement_run(session, FailedClient(), run_id)
    row = eng_repo.get_run(session, run_id)

    assert outcome == "failure"
    assert row["result"] == "FAILURE"
    assert row["fail_code"] == 20267
    assert row["fail_screenshot"] == "https://img/last.jpg"
    assert row["notes"] == "Comment box missing"
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_engagement_service.py -q
```

Expected: new tests fail because submit and check functions do not exist.

- [ ] **Step 3: Implement submit function**

In `app/services/engagement_service.py`, add:

```python
def submit_run_to_geelark(session, client, *, run_id: int) -> str:
    run = eng_repo.get_run(session, run_id)
    if run is None:
        raise RuntimeError(f"engagement run {run_id} not found")
    account = acc_repo.get(session, run["account_id"])
    if account is None:
        eng_repo.set_result(session, run_id, "FAILURE", "account vanished")
        raise RuntimeError("account vanished")
    if pub_repo.account_has_inflight(session, account["id"]):
        eng_repo.mark_queued(session, run_id)
        raise PhoneBusy(f"account {account['id']} already has an active publication")
    if eng_repo.account_has_inflight(session, account["id"], exclude_run_id=run_id):
        eng_repo.mark_queued(session, run_id)
        raise PhoneBusy(f"account {account['id']} already has an active engagement")
    if not _eligible_account(account):
        eng_repo.set_result(session, run_id, "SKIPPED", "account is not eligible")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        raise RuntimeError("account is not eligible")
    schedule_at = int(time.time())
    parent = eng_repo.get_task(session, run["engagement_task_id"])
    task_id = client.create_tiktok_ai_comment_task(
        account["geelark_phone_id"],
        [run["target_link"]],
        schedule_at=schedule_at,
        comment_probability=int(parent.get("comment_probability") or 100),
        name="GeeLark Studio AI Comment",
        remark=f"engagement_run_id={run_id}",
    )
    eng_repo.mark_initiated(
        session,
        run_id,
        task_id=task_id,
        schedule_at=schedule_at,
        notes="submitted to GeeLark",
    )
    return task_id
```

- [ ] **Step 4: Implement status reconciliation**

Add helper and checker:

```python
def _geelark_failure_detail(client, task_id: str) -> tuple[int | None, str, str | None]:
    try:
        detail = client.get_task_detail(task_id)
    except Exception:
        return None, "task failed", None
    code = detail.get("failCode")
    reason = (detail.get("failDesc") or "").strip() or "task failed"
    images = detail.get("resultImages") or []
    screenshot = images[-1] if images else None
    return code, reason, screenshot


def _stop_phone_best_effort(client, phone_id: str | None) -> None:
    if not phone_id:
        return
    try:
        client.stop_phones([phone_id])
    except Exception:
        return


def check_engagement_run(session, client, run_id: int) -> str:
    run = eng_repo.get_run(session, run_id)
    if run is None:
        return "unknown"
    if run["result"] in ("SUCCESS", "FAILURE", "SKIPPED"):
        return "success" if run["result"] == "SUCCESS" else "failure"
    if run["result"] != "INITIATED" or not run["geelark_task_id"]:
        return "unknown"
    account = acc_repo.get(session, run["account_id"])
    try:
        rows = client.query_tasks([run["geelark_task_id"]])
    except Exception:
        return "running"
    if not rows:
        eng_repo.set_result(session, run_id, "FAILURE", "task not found")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, account.get("geelark_phone_id") if account else None)
        return "failure"
    status = rows[0].get("status")
    cost = rows[0].get("cost")
    if status == 3:
        eng_repo.set_result(session, run_id, "SUCCESS", "completed", cost=cost)
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, account.get("geelark_phone_id") if account else None)
        return "success"
    if status in (4, 7):
        code, reason, screenshot = _geelark_failure_detail(client, run["geelark_task_id"])
        if status == 7 and reason == "task failed":
            reason = "task cancelled"
        eng_repo.set_result(session, run_id, "FAILURE", reason,
                            fail_code=code, fail_screenshot=screenshot,
                            cost=cost)
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, account.get("geelark_phone_id") if account else None)
        return "failure"
    return "running"
```

- [ ] **Step 5: Run service tests**

Run:

```bash
uv run pytest tests/test_engagement_service.py -q
```

Expected: pass.

- [ ] **Step 6: Commit service execution**

```bash
git add app/services/engagement_service.py tests/test_engagement_service.py
git commit -m "feat: submit and check engagement runs"
```

---

### Task 6: Worker Tasks

**Files:**
- Create: `app/tasks/engagement.py`
- Modify: `app/tasks/__init__.py`
- Create: `tests/test_tasks_engagement.py`

- [ ] **Step 1: Write worker task tests**

Create `tests/test_tasks_engagement.py`:

```python
import pytest


@pytest.fixture
def fake_client(monkeypatch):
    from app.services import geelark_service

    class FakeC:
        def create_tiktok_ai_comment_task(self, env, links, **kwargs):
            return "gee-eng-1"
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3, "cost": 12}]
        def stop_phones(self, ids):
            return {}

    monkeypatch.setattr(geelark_service, "make_client", lambda: FakeC())
    return FakeC


def _make_run(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid = group_repo.create(session, name="Worker Engage")
    aid = acc_repo.create(session, {
        "profile_name": "M1",
        "geelark_phone_id": "ph-m1",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[aid],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    return result["task_id"], eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]


def test_submit_engagement_run_task_defers_check(
        session, procrastinate_app, fake_client):
    import asyncio
    from app.repositories import engagement as eng_repo
    from app.tasks.engagement import submit_engagement_run_task

    task_id, run_id = _make_run(session)

    result = asyncio.run(submit_engagement_run_task(run_id=run_id))

    assert "INITIATED" in result
    assert eng_repo.get_run(session, run_id)["result"] == "INITIATED"
    pending = list(procrastinate_app.connector.jobs.values())
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert any("check_engagement_run" in n for n in names)


def test_check_engagement_run_task_marks_success(
        session, procrastinate_app, fake_client):
    import asyncio
    from app.repositories import engagement as eng_repo
    from app.tasks.engagement import check_engagement_run_task

    task_id, run_id = _make_run(session)
    eng_repo.mark_initiated(session, run_id, task_id="gee-eng-1",
                            schedule_at=123, notes="submitted")

    outcome = asyncio.run(check_engagement_run_task(
        run_id=run_id, attempts_remaining=5))

    assert outcome == "success"
    assert eng_repo.get_run(session, run_id)["result"] == "SUCCESS"
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_tasks_engagement.py -q
```

Expected: fail because `app.tasks.engagement` does not exist.

- [ ] **Step 3: Implement worker tasks**

Create `app/tasks/engagement.py`:

```python
from app.tasks import procrastinate_app
from app.db import session_scope
from app.services import engagement_service, geelark_service

MAX_CHECK_RETRIES = 40
CHECK_INTERVAL_SECONDS = 30


@procrastinate_app.task(lock="phones", queue="phones",
                         name="submit_engagement_run")
async def submit_engagement_run_task(run_id: int):
    try:
        with session_scope() as session:
            task_id = engagement_service.submit_run_to_geelark(
                session,
                geelark_service.make_client(),
                run_id=run_id,
            )
    except engagement_service.PhoneBusy as e:
        return f"SKIPPED: {e}"
    await check_engagement_run_task.configure(
        schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
    ).defer_async(
        run_id=run_id,
        attempts_remaining=MAX_CHECK_RETRIES,
    )
    return f"INITIATED task {task_id}"


@procrastinate_app.task(queue="default", name="check_engagement_run")
async def check_engagement_run_task(run_id: int, attempts_remaining: int):
    with session_scope() as session:
        outcome = engagement_service.check_engagement_run(
            session,
            geelark_service.make_client(),
            run_id,
        )
    if outcome == "running" and attempts_remaining > 0:
        await check_engagement_run_task.configure(
            schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
        ).defer_async(
            run_id=run_id,
            attempts_remaining=attempts_remaining - 1,
        )
    return outcome
```

- [ ] **Step 4: Register task import path**

In `app/tasks/__init__.py`, add `"app.tasks.engagement"` to `import_paths`.

```python
        "app.tasks.engagement",
```

- [ ] **Step 5: Run worker tests**

Run:

```bash
uv run pytest tests/test_tasks_engagement.py -q
```

Expected: pass.

- [ ] **Step 6: Commit worker tasks**

```bash
git add app/tasks/engagement.py app/tasks/__init__.py tests/test_tasks_engagement.py
git commit -m "feat: add engagement worker tasks"
```

---

### Task 7: API Router

**Files:**
- Create: `app/api/engagement.py`
- Modify: `app/main.py`
- Create: `tests/test_api_engagement.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_api_engagement.py`:

```python
import pytest


@pytest.fixture
def authed_client(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DASHBOARD_PASSWORD", "pw")
    client.post("/api/login", json={"password": "pw"})
    return client


def _seed_account(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="API Engage")
    aid = acc_repo.create(session, {
        "profile_name": "M1",
        "geelark_phone_id": "ph-m1",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })
    return gid, aid


def test_create_engagement_comment_defers_runs(
        authed_client, session, monkeypatch):
    gid, aid = _seed_account(session)
    deferred = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("Job", (), {"id": "job-1"})()

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        fake_defer_async)

    r = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "target_publication_ids": [],
        "comment_probability": 100,
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_runs"] == 1
    assert body["skipped_runs"] == 0
    assert deferred and "run_id" in deferred[0]


def test_create_engagement_comment_rejects_bad_link(authed_client, session):
    gid, aid = _seed_account(session)
    r = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://example.com/nope"],
        "target_publication_ids": [],
        "comment_probability": 100,
    })
    assert r.status_code == 400
    assert "TikTok video URL" in r.json()["detail"]


def test_engagement_task_detail_returns_runs(
        authed_client, session, monkeypatch):
    gid, aid = _seed_account(session)

    async def fake_defer_async(**kwargs):
        return type("Job", (), {"id": "job-1"})()

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        fake_defer_async)

    created = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "target_publication_ids": [],
        "comment_probability": 100,
    }).json()

    r = authed_client.get(f"/api/engagement/tasks/{created['task_id']}")

    assert r.status_code == 200
    assert r.json()["task"]["id"] == created["task_id"]
    assert len(r.json()["runs"]) == 1
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_api_engagement.py -q
```

Expected: fail because `/api/engagement` routes do not exist.

- [ ] **Step 3: Implement API router**

Create `app/api/engagement.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.repositories import engagement as eng_repo
from app.services import engagement_service

router = APIRouter(prefix="/api/engagement")


@router.post("/comment")
async def create_comment(body: dict, session: Session = Depends(get_session)):
    try:
        result = engagement_service.create_comment_batch(
            session,
            group_id=body.get("group_id"),
            account_ids=body.get("account_ids") or [],
            target_links=body.get("target_links") or [],
            target_publication_ids=body.get("target_publication_ids") or [],
            comment_probability=body.get("comment_probability", 100),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from app.tasks.engagement import submit_engagement_run_task
    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    deferred = 0
    try:
        for run in runs:
            eng_repo.mark_queued(session, run["id"])
            await submit_engagement_run_task.defer_async(run_id=run["id"])
            deferred += 1
    except Exception:
        raise

    return {**result, "deferred_runs": deferred}
```

Add listing and detail:

```python
@router.get("/tasks")
def list_tasks(group_id: int | None = None,
               limit: int = Query(50, ge=1, le=200),
               session: Session = Depends(get_session)):
    return eng_repo.list_tasks(session, group_id=group_id, limit=limit)


@router.get("/tasks/{task_id}")
def get_task(task_id: int, session: Session = Depends(get_session)):
    task = eng_repo.get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="engagement task not found")
    return {"task": task, "runs": eng_repo.list_runs_for_task(session, task_id)}
```

For screenshot endpoint, reuse the screenshot helpers from `app.api.videos`:

```python
@router.get("/runs/{run_id}/screenshot")
def get_run_screenshot(run_id: int, session: Session = Depends(get_session)):
    from app.api.videos import _fetch_screenshot_bytes, _screenshot_cache_path

    run = eng_repo.get_run(session, run_id)
    url = (run.get("fail_screenshot") or "").strip() if run else ""
    if not url:
        raise HTTPException(status_code=404, detail="no screenshot")
    cache = _screenshot_cache_path(url)
    if not cache.exists():
        try:
            data = _fetch_screenshot_bytes(url)
        except Exception:
            return RedirectResponse(url, status_code=302)
        tmp = cache.with_name(f"{cache.name}.part")
        try:
            tmp.write_bytes(data)
            tmp.replace(cache)
        except OSError:
            tmp.unlink(missing_ok=True)
            return RedirectResponse(url, status_code=302)
    return FileResponse(
        path=str(cache),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )
```

- [ ] **Step 4: Include router**

In `app/main.py`:

```python
    from app.api import accounts, videos, dashboard, settings, phones, groups, engagement
```

Then:

```python
    app.include_router(engagement.router, dependencies=[Depends(require_auth)])
```

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest tests/test_api_engagement.py -q
```

Expected: pass.

- [ ] **Step 6: Commit API**

```bash
git add app/api/engagement.py app/main.py tests/test_api_engagement.py
git commit -m "feat: add engagement api"
```

---

### Task 8: Publication Share Link Capture And Cross-Task Busy Checks

**Files:**
- Modify: `app/services/posting_service.py`
- Modify: `tests/test_services.py`
- Modify: `tests/test_tasks_publish.py`

- [ ] **Step 1: Write share-link capture test**

Append to `tests/test_services.py`:

```python
def test_check_publication_one_stores_share_link(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import posting_service

    aid = acc_repo.create(db, {"profile_name": "SL", "geelark_phone_id": "ph-sl"})
    f = tmp_path / "sl.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "sl.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(db, pid, "caption", "task-sl", "submitted")

    class DoneWithLink:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3,
                     "shareLink": "https://www.tiktok.com/@owner/video/1234567890"}]

    outcome = posting_service.check_publication_one(db, DoneWithLink(), pid)

    assert outcome == "success"
    row = pub_repo.get(db, pid)
    assert row["share_link"] == "https://www.tiktok.com/@owner/video/1234567890"
    assert row["share_link_captured_at"] is not None
```

- [ ] **Step 2: Write engagement busy blocks auto-publish test**

Append to `tests/test_tasks_publish.py`:

```python
def test_auto_publish_scan_skips_account_with_inflight_engagement(
        session, procrastinate_app, fake_client, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr
    from app.repositories import engagement as eng_repo

    gid = gr.create(session, name="busy-engagement")
    aid = acc_repo.create(session, {
        "profile_name": "busy-eng",
        "geelark_phone_id": "ph-busy-eng",
        "group_id": gid,
        "warmed_up": 1,
        "tiktok_logged_in": 1,
    })
    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.create_run(
        session,
        engagement_task_id=task_id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )
    eng_repo.mark_initiated(session, run_id, task_id="eng-active",
                            schedule_at=123, notes="running")

    f = tmp_path / "new.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(session, "new.mp4", str(f), group_id=gid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")

    import asyncio
    from app.tasks.auto_publish import auto_publish_scan_task
    result = asyncio.run(auto_publish_scan_task(timestamp=0))

    assert "0 publication(s) deferred" in result
    assert pub_repo.get(session, pid)["result"] == "PENDING"
```

- [ ] **Step 3: Run focused failing tests**

Run:

```bash
uv run pytest tests/test_services.py::test_check_publication_one_stores_share_link tests/test_tasks_publish.py::test_auto_publish_scan_skips_account_with_inflight_engagement -q
```

Expected: fail because `check_publication_one()` does not save share links and `plan_assignments()` does not check engagement in-flight rows.

- [ ] **Step 4: Store share link on publication success**

In `app/services/posting_service.py`, update `check_publication_one()` success branch:

```python
    if status == 3:
        share_link = (res[0].get("shareLink") or "").strip()
        if share_link:
            pub_repo.set_share_link(session, publication_id, share_link)
        pub_repo.set_result(session, publication_id, "SUCCESS", "completed")
        _maybe_cleanup_video(session, pub["video_id"])
        return "success"
```

Make the same change in `check_initiated()` if it handles successful stale rows:

```python
        if status == 3:
            share_link = (res[0].get("shareLink") or "").strip()
            if share_link:
                pub_repo.set_share_link(session, p["id"], share_link)
            pub_repo.set_result(session, p["id"], "SUCCESS", "completed")
            ok += 1
```

- [ ] **Step 5: Add engagement in-flight checks to publication planning and submit**

In `posting_service.plan_assignments()`, import engagement repository inside the function to avoid module cycles:

```python
    from app.repositories import engagement as eng_repo
```

Add `and not eng_repo.account_has_inflight(session, account["id"])` to the already-assigned path and candidate filtering.

In `submit_publication_to_geelark()`, before upload/caption submission:

```python
    from app.repositories import engagement as eng_repo
    if eng_repo.account_has_inflight(session, account_id):
        pub_repo.unclaim(session, publication_id)
        raise PhoneBusy(f"account {account_id} already has an active engagement task")
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/test_services.py::test_check_publication_one_stores_share_link tests/test_tasks_publish.py::test_auto_publish_scan_skips_account_with_inflight_engagement -q
```

Expected: pass.

- [ ] **Step 7: Commit publishing integration**

```bash
git add app/services/posting_service.py tests/test_services.py tests/test_tasks_publish.py
git commit -m "feat: link publishing and engagement state"
```

---

### Task 9: Frontend API And Accounts Modal

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/Accounts.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Add API types and methods**

In `frontend/src/api.ts`, add:

```ts
export type EngagementTaskStatus =
  | 'PENDING' | 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'PARTIAL_FAILURE' | 'FAILURE'

export type EngagementRunResult =
  | 'PENDING' | 'QUEUED' | 'INITIATED' | 'SUCCESS' | 'FAILURE' | 'SKIPPED'

export type EngagementTask = {
  id: number
  group_id: number | null
  action: string
  source: string
  target_links: string[]
  target_publication_ids: number[]
  comment_probability: number
  status: EngagementTaskStatus
  summary: string | null
  created_at: string | null
  updated_at: string | null
  finished_at: string | null
}

export type EngagementRun = {
  id: number
  engagement_task_id: number
  account_id: number | null
  account_profile_name?: string | null
  target_link: string
  result: EngagementRunResult
  notes: string | null
  geelark_task_id: string | null
  fail_screenshot: string | null
  cost: number | null
}
```

Add API methods:

```ts
  createEngagementComment: (body: {
    group_id: number
    account_ids: number[]
    target_links: string[]
    target_publication_ids?: number[]
    comment_probability: number
  }) => req('/engagement/comment', {
    method: 'POST',
    body: JSON.stringify({
      target_publication_ids: [],
      ...body,
    }),
  }),
  engagementTasks: (groupId?: number): Promise<EngagementTask[]> => {
    const q = new URLSearchParams()
    if (groupId != null) q.set('group_id', String(groupId))
    const qs = q.toString()
    return req('/engagement/tasks' + (qs ? '?' + qs : ''))
  },
  engagementTask: (id: number): Promise<{ task: EngagementTask; runs: EngagementRun[] }> =>
    req(`/engagement/tasks/${id}`),
```

- [ ] **Step 2: Update translation strings**

In both locale files under `accounts`, add an `engagement` section. English:

```json
"engagement": {
  "aiComment": "AI Comment",
  "modalTitle": "AI comment on owned videos",
  "linksLabel": "TikTok video links",
  "linksPlaceholder": "Paste one TikTok video link per line",
  "probability": "Comment probability",
  "eligible": "{{count}} selected account(s)",
  "manualResponsibility": "Manual links cannot detect the owner account. Use owned videos and exclude the owner account from selection.",
  "submit": "Start comments",
  "submitting": "Starting…",
  "recent": "Recent AI comments",
  "empty": "No engagement tasks yet",
  "created": "Queued {{count}} comment run(s)",
  "failed": "Could not start AI comments"
}
```

Chinese:

```json
"engagement": {
  "aiComment": "AI 评论",
  "modalTitle": "给自有视频做 AI 评论",
  "linksLabel": "TikTok 视频链接",
  "linksPlaceholder": "每行粘贴一个 TikTok 视频链接",
  "probability": "评论概率",
  "eligible": "已选 {{count}} 个账号",
  "manualResponsibility": "手动链接无法自动识别视频作者账号，请只填写自有视频，并不要勾选视频作者账号。",
  "submit": "开始评论",
  "submitting": "开始中…",
  "recent": "最近 AI 评论",
  "empty": "暂无互动任务",
  "created": "已排队 {{count}} 个评论任务",
  "failed": "AI 评论启动失败"
}
```

- [ ] **Step 3: Add modal state and handlers in Accounts page**

In `frontend/src/pages/Accounts.tsx`, import engagement types if needed:

```ts
import { api, type AccountPublication, type EngagementTask } from '../api'
```

Add state near the selected state:

```tsx
  const [engagementOpen, setEngagementOpen] = useState(false)
  const [engagementLinks, setEngagementLinks] = useState('')
  const [engagementProbability, setEngagementProbability] = useState(100)
  const [engagementSubmitting, setEngagementSubmitting] = useState(false)
  const [engagementTasks, setEngagementTasks] = useState<EngagementTask[]>([])
```

Add loader:

```tsx
  const loadEngagementTasks = async () => {
    if (selectedGroup === ALL_ACCOUNTS) return
    try {
      setEngagementTasks(await api.engagementTasks(selectedGroup))
    } catch {
      setEngagementTasks([])
    }
  }
```

Call it in an effect keyed by `selectedGroup`:

```tsx
  useEffect(() => {
    void loadEngagementTasks()
  }, [selectedGroup])
```

Add submit handler:

```tsx
  const submitEngagement = async (e: React.FormEvent) => {
    e.preventDefault()
    if (selectedGroup === ALL_ACCOUNTS) return
    const links = engagementLinks
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean)
    setEngagementSubmitting(true)
    try {
      const result = await api.createEngagementComment({
        group_id: selectedGroup,
        account_ids: [...selected],
        target_links: links,
        target_publication_ids: [],
        comment_probability: engagementProbability,
      })
      onToast({
        msg: t('accounts.engagement.created', { count: result.created_runs ?? 0 }),
        tone: 'green',
      })
      setEngagementOpen(false)
      setEngagementLinks('')
      await loadEngagementTasks()
    } catch (err) {
      onToast({
        msg: errorMessage(err, t('accounts.engagement.failed')),
        tone: 'red',
      })
    } finally {
      setEngagementSubmitting(false)
    }
  }
```

- [ ] **Step 4: Add toolbar button**

Extend `SelectionToolbar` props:

```tsx
function SelectionToolbar({ count, mode, onWarmup, onPublish,
                            onComment, onMove, onDelete, onCancel }: {
  count: number; mode: 'all' | 'group'
  onWarmup: () => void; onPublish: () => void; onComment: () => void
  onMove: () => void; onDelete: () => void; onCancel: () => void
}) {
```

Add button in the group-mode button cluster:

```tsx
            <button onClick={onComment} style={primaryBtn('#8b5cf6')}>
              {t('accounts.engagement.aiComment')}
            </button>
```

If `SelectionToolbar` is outside the component and cannot access `t`, pass a `commentLabel` prop instead:

```tsx
commentLabel={t('accounts.engagement.aiComment')}
```

Then at usage:

```tsx
          onComment={() => setEngagementOpen(true)}
```

- [ ] **Step 5: Add modal**

Near other modals in `Accounts.tsx`, add:

```tsx
      <Modal
        open={engagementOpen}
        onClose={() => {
          if (!engagementSubmitting) setEngagementOpen(false)
        }}
        title={t('accounts.engagement.modalTitle')}
        submitLabel={engagementSubmitting
          ? t('accounts.engagement.submitting')
          : t('accounts.engagement.submit')}
        onSubmit={submitEngagement}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: '#4b5563' }}>
            {t('accounts.engagement.eligible', { count: selected.size })}
          </div>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>
              {t('accounts.engagement.linksLabel')}
            </span>
            <textarea
              value={engagementLinks}
              onChange={(e) => setEngagementLinks(e.target.value)}
              placeholder={t('accounts.engagement.linksPlaceholder')}
              rows={5}
              style={{ width: '100%', resize: 'vertical' }}
            />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>
              {t('accounts.engagement.probability')}
            </span>
            <input
              type="number"
              min={0}
              max={100}
              value={engagementProbability}
              onChange={(e) => setEngagementProbability(Number(e.target.value))}
            />
          </label>
          <div style={{ fontSize: 12, color: '#6b7280' }}>
            {t('accounts.engagement.manualResponsibility')}
          </div>
        </div>
      </Modal>
```

- [ ] **Step 6: Add compact recent status**

Below `CountsRow` for group view, render recent engagement batches:

```tsx
      {selectedGroup !== ALL_ACCOUNTS && (
        <div style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
            {t('accounts.engagement.recent')}
          </div>
          {engagementTasks.length === 0 ? (
            <div style={{ fontSize: 12, color: '#6b7280' }}>
              {t('accounts.engagement.empty')}
            </div>
          ) : (
            <div style={{ display: 'grid', gap: 6 }}>
              {engagementTasks.slice(0, 5).map((task) => (
                <div key={task.id}
                     style={{ display: 'flex', justifyContent: 'space-between',
                              border: '1px solid #e5e7eb', borderRadius: 6,
                              padding: '8px 10px', fontSize: 12 }}>
                  <span>#{task.id} · {task.status}</span>
                  <span style={{ color: '#6b7280' }}>{task.summary || ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
```

- [ ] **Step 7: Build frontend**

Run:

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: Vite build passes.

- [ ] **Step 8: Commit frontend**

```bash
git add frontend/src/api.ts frontend/src/pages/Accounts.tsx frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat: add ai comment account workflow"
```

---

### Task 10: Full Verification And Local Preview

**Files:**
- No planned source edits unless verification reveals a bug.

- [ ] **Step 1: Run backend tests**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass. The project baseline in AGENTS says at least 385 tests should pass; the final count should be higher after this feature.

- [ ] **Step 2: Run frontend build**

Run:

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: build passes.

- [ ] **Step 3: Start local web tier**

Run:

```bash
./run.sh
```

Expected: FastAPI serves on `http://localhost:8080` with one uvicorn worker. Keep it running for browser verification.

- [ ] **Step 4: Verify UI with Browser plugin**

Open:

```text
http://localhost:8080/accounts
```

Checks:

- Login works.
- Accounts page loads.
- In a concrete group, selecting accounts shows `AI Comment`.
- Modal opens without overlapping text.
- Invalid link produces an error toast.
- Valid link submits and shows queued count when the API is monkeypatched or connected to a safe test account.
- Recent engagement list renders without layout shift.

- [ ] **Step 5: Stop local server**

Stop the `./run.sh` process with Ctrl-C. Do not leave a server session running before the final response.

- [ ] **Step 6: Commit verification fixes**

If verification required fixes, run `git status --short`, stage only the exact source files changed by those fixes, and commit them. Do not use `git add .` because `app/repositories/accounts.py` and `tests/test_repositories.py` may contain unrelated user work in this workspace.

```bash
git status --short
git commit -m "fix: stabilize engagement workflow"
```

If no fixes were required, skip this commit.

---

### Task 11: Production Deploy

**Files:**
- No source edits expected.
- Follow: `docs/production-deploy.md`

- [ ] **Step 1: Re-read deploy runbook**

Run:

```bash
sed -n '1,220p' docs/production-deploy.md
```

Expected: confirm packaging, rsync, systemd, and Alembic steps.

- [ ] **Step 2: Deploy because AGENTS requires shipping after verified work**

Follow `docs/production-deploy.md`. This feature includes a non-destructive migration with `add_column` and `create_table`, so no destructive migration backup is required by the AGENTS rule. Still follow the runbook's normal safety steps.

Required production steps include:

```bash
uv run alembic upgrade head
sudo systemctl restart mkt mkt-worker
sudo systemctl status mkt --no-pager
sudo systemctl status mkt-worker --no-pager
```

- [ ] **Step 3: Production smoke test**

Checks:

- `GET /healthz` returns healthy.
- Dashboard loads.
- Accounts page loads.
- `AI Comment` modal opens.
- Recent engagement endpoint returns authenticated JSON.

- [ ] **Step 4: Final commit or deployment note**

If deployment required runbook edits or small source fixes, commit them. Otherwise report deployed commit hash and migration revision in the final response.

---

## Self-Review Checklist

- Spec coverage: manual links, `share_link`, separate engagement state, worker-only phone execution, in-flight checks, screenshots, UI entry point, and deploy are all covered.
- No implementation step requires real GeeLark network calls during automated tests.
- Cross-task busy checks are included in both engagement submit and publication planning.
- Publication share link storage is included but target picker UI is deferred, matching the approved MVP.
- Frontend puts the workflow in Accounts, not Settings.
