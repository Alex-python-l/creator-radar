# Comment Prompt Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single free-text comment brief in the CommentModal with a reusable prompt pool managed in Settings, selected per batch.

**Architecture:** Add a new `comment_prompts` table modelled on `caption_prompts`. CommentModal swaps its textarea for a per-batch `<select>`. EngagementTask gains a `comment_prompt_id` FK; the existing `comment_brief` column stays as a read-only fallback for rows persisted before this change. Settings page mirrors the existing CaptionPromptsSection.

**Tech Stack:** FastAPI + SQLModel + Alembic + PostgreSQL on the backend; React 19 + Vite + i18next on the frontend. Tests run against `mkt_test` via pytest with truncated tables between runs.

**Spec:** `docs/superpowers/specs/2026-06-11-comment-prompt-pool-design.md`

---

## File Structure

**New files:**
- `alembic/versions/c8e1a2b3d4f5_comment_prompts_pool.py` — schema migration
- `app/repositories/comment_prompts.py` — CRUD over `comment_prompts`
- `app/services/comment_prompts_service.py` — validation + `PromptInUse`
- `app/api/comment_prompts.py` — REST endpoints
- `tests/test_repositories_comment_prompts.py`
- `tests/test_comment_prompts_service.py`
- `tests/test_comment_prompts_api.py`

**Modified files:**
- `app/models.py` — add `CommentPrompt`, add `EngagementTask.comment_prompt_id`
- `app/repositories/engagement.py` — `create_task` signature swap
- `app/services/engagement_service.py` — `create_comment_batch` swap, `_prepare_studio_comment` resolves prompt
- `app/api/engagement.py` — body schema swap
- `app/main.py` — mount the new router
- `tests/conftest.py` — add `comment_prompts` to the truncate list
- `tests/test_engagement_service.py`, `tests/test_eligible_phones_endpoint.py`, `tests/test_repositories_engagement.py` — update fixtures
- `frontend/src/api.ts` — types + client methods
- `frontend/src/components/CommentModal.tsx` — textarea → select
- `frontend/src/pages/Settings.tsx` — new `CommentPromptsSection`

---

## Task 1: Alembic migration — create `comment_prompts` + `engagement_tasks.comment_prompt_id`

**Files:**
- Create: `alembic/versions/c8e1a2b3d4f5_comment_prompts_pool.py`

The migration is additive: it does not move data, does not drop `comment_brief`. The current alembic head is `b5c4e8f3a712` (caption_prompts pool).

- [ ] **Step 1: Write the migration file**

```python
"""comment_prompts pool + engagement_tasks.comment_prompt_id

Adds a reusable prompt pool for AI-generated TikTok comments, mirroring
the caption_prompts pool added in b5c4e8f3a712. The existing
engagement_tasks.comment_brief column stays in place as a read-only
fallback for rows persisted before this change.

Revision ID: c8e1a2b3d4f5
Revises: b5c4e8f3a712
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e1a2b3d4f5"
down_revision: Union[str, Sequence[str], None] = "b5c4e8f3a712"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "comment_prompts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "comment_prompt_id",
            sa.Integer,
            sa.ForeignKey("comment_prompts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("engagement_tasks") as batch:
        batch.drop_column("comment_prompt_id")
    op.drop_table("comment_prompts")
```

- [ ] **Step 2: Apply against the test DB**

Run: `uv run alembic upgrade head`
Expected: `Running upgrade b5c4e8f3a712 -> c8e1a2b3d4f5, comment_prompts pool + engagement_tasks.comment_prompt_id`

- [ ] **Step 3: Add `comment_prompts` to the conftest truncate list**

Edit `tests/conftest.py` around line 52. The current value is:

```python
_TABLES = (
    "publication_attempts, engagement_runs, engagement_tasks, videos, "
    "accounts, groups, caption_prompts, settings, job_runs, "
    "discovered_videos, geelark_transactions"
)
```

Replace with:

```python
_TABLES = (
    "publication_attempts, engagement_runs, engagement_tasks, videos, "
    "accounts, groups, caption_prompts, comment_prompts, settings, "
    "job_runs, discovered_videos, geelark_transactions"
)
```

- [ ] **Step 4: Verify the existing suite still passes against the new schema**

Run: `uv run pytest -q`
Expected: 713 passed (no regressions; nothing reads the new column yet).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/c8e1a2b3d4f5_comment_prompts_pool.py tests/conftest.py
git commit -m "feat(comments): migration for the comment prompt pool"
```

---

## Task 2: SQLModel — `CommentPrompt` table + `EngagementTask.comment_prompt_id`

**Files:**
- Modify: `app/models.py` (add class after `CaptionPrompt`; add field to `EngagementTask`)

- [ ] **Step 1: Add the `CommentPrompt` class**

Insert after the existing `CaptionPrompt` class (around line 53):

```python
class CommentPrompt(SQLModel, table=True):
    __tablename__ = "comment_prompts"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, nullable=False)
    body: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
```

- [ ] **Step 2: Add `comment_prompt_id` to `EngagementTask`**

In `app/models.py`, find the `EngagementTask` class. Locate the line:

```python
    comment_mode: str = Field(default="geelark_ai", nullable=False)
```

Immediately after `comments_per_video` (currently around line 234), add:

```python
    comment_prompt_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("comment_prompts.id", ondelete="RESTRICT"),
        ),
    )
```

(Keep `product_info` and `comment_brief` columns where they are — they stay for backward compatibility.)

- [ ] **Step 3: Verify imports / smoke run**

Run: `uv run python -c "from app.models import CommentPrompt, EngagementTask; print(CommentPrompt.__tablename__, EngagementTask.__fields__['comment_prompt_id'])"`
Expected: prints `comment_prompts ...` without import error.

- [ ] **Step 4: Verify the existing suite still passes**

Run: `uv run pytest -q`
Expected: 713 passed.

- [ ] **Step 5: Commit**

```bash
git add app/models.py
git commit -m "feat(comments): SQLModel for CommentPrompt + engagement_tasks FK"
```

---

## Task 3: Repository — `app/repositories/comment_prompts.py` (TDD)

**Files:**
- Create: `tests/test_repositories_comment_prompts.py`
- Create: `app/repositories/comment_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_repositories_comment_prompts.py`:

```python
"""Repository-level CRUD over comment_prompts."""
from app.repositories import comment_prompts as repo


def test_create_then_get(session):
    pid = repo.create(session, name="snarky", body="be snarky")
    row = repo.get(session, pid)
    assert row["id"] == pid
    assert row["name"] == "snarky"
    assert row["body"] == "be snarky"


def test_list_all_sorted_by_name(session):
    repo.create(session, name="beta", body="b")
    repo.create(session, name="alpha", body="a")
    repo.create(session, name="gamma", body="g")
    names = [p["name"] for p in repo.list_all(session)]
    assert names == ["alpha", "beta", "gamma"]


def test_get_by_name(session):
    repo.create(session, name="x", body="b")
    assert repo.get_by_name(session, "x")["name"] == "x"
    assert repo.get_by_name(session, "missing") is None


def test_update_partial(session):
    pid = repo.create(session, name="p", body="old")
    assert repo.update(session, pid, body="new") is True
    assert repo.get(session, pid)["body"] == "new"
    assert repo.get(session, pid)["name"] == "p"


def test_update_returns_false_for_unknown(session):
    assert repo.update(session, 99999, body="x") is False


def test_delete(session):
    pid = repo.create(session, name="p", body="b")
    assert repo.delete(session, pid) is True
    assert repo.get(session, pid) is None
    assert repo.delete(session, pid) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repositories_comment_prompts.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'app.repositories.comment_prompts'`.

- [ ] **Step 3: Implement the repository**

Create `app/repositories/comment_prompts.py` (verbatim port of `caption_prompts.py`):

```python
from datetime import datetime

from sqlmodel import Session, select

from app.models import CommentPrompt


def list_all(session: Session) -> list[dict]:
    rows = session.exec(select(CommentPrompt).order_by(CommentPrompt.name)).all()
    return [r.model_dump() for r in rows]


def get(session: Session, prompt_id: int) -> dict | None:
    obj = session.get(CommentPrompt, prompt_id)
    return obj.model_dump() if obj else None


def get_by_name(session: Session, name: str) -> dict | None:
    obj = session.exec(
        select(CommentPrompt).where(CommentPrompt.name == name)
    ).first()
    return obj.model_dump() if obj else None


def create(session: Session, *, name: str, body: str) -> int:
    obj = CommentPrompt(name=name, body=body)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def update(session: Session, prompt_id: int, *,
           name: str | None = None, body: str | None = None) -> bool:
    obj = session.get(CommentPrompt, prompt_id)
    if obj is None:
        return False
    if name is not None:
        obj.name = name
    if body is not None:
        obj.body = body
    obj.updated_at = datetime.utcnow()
    session.add(obj)
    session.commit()
    return True


def delete(session: Session, prompt_id: int) -> bool:
    obj = session.get(CommentPrompt, prompt_id)
    if obj is None:
        return False
    session.delete(obj)
    session.commit()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_repositories_comment_prompts.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_repositories_comment_prompts.py app/repositories/comment_prompts.py
git commit -m "feat(comments): repository CRUD for comment_prompts"
```

---

## Task 4: Service — `app/services/comment_prompts_service.py` (TDD)

**Files:**
- Create: `tests/test_comment_prompts_service.py`
- Create: `app/services/comment_prompts_service.py`

The service enforces non-empty `name` (≤ 120 chars), non-empty `body`, name uniqueness, and refuses to delete a prompt while any `engagement_runs` row tied to it is still in `PENDING`, `QUEUED`, or `INITIATED`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comment_prompts_service.py`:

```python
"""Validation + delete-in-use behaviour for comment_prompts_service."""
import pytest

from app.repositories import accounts as acc_repo
from app.repositories import comment_prompts as cp_repo
from app.repositories import engagement as eng_repo
from app.services import comment_prompts_service as svc


def test_create_strips_and_persists(session):
    pid = svc.create(session, name="  IELTS lane ", body=" tone: snarky ")
    row = cp_repo.get(session, pid)
    assert row["name"] == "IELTS lane"
    assert row["body"] == "tone: snarky"


def test_create_rejects_empty_name(session):
    with pytest.raises(ValueError, match="name required"):
        svc.create(session, name="   ", body="b")


def test_create_rejects_empty_body(session):
    with pytest.raises(ValueError, match="body required"):
        svc.create(session, name="n", body="   ")


def test_create_rejects_overlong_name(session):
    with pytest.raises(ValueError, match="name too long"):
        svc.create(session, name="x" * 121, body="b")


def test_create_rejects_duplicate_name(session):
    svc.create(session, name="dup", body="a")
    with pytest.raises(ValueError, match="name already used"):
        svc.create(session, name="dup", body="b")


def test_update_can_rename_to_unused_name(session):
    pid = svc.create(session, name="old", body="b")
    assert svc.update(session, pid, name="new") is True
    assert cp_repo.get(session, pid)["name"] == "new"


def test_update_rejects_rename_to_used_name(session):
    a = svc.create(session, name="a", body="x")
    svc.create(session, name="b", body="y")
    with pytest.raises(ValueError, match="name already used"):
        svc.update(session, a, name="b")


def test_delete_unused(session):
    pid = svc.create(session, name="p", body="b")
    assert svc.delete(session, pid) is True
    assert cp_repo.get(session, pid) is None


def test_delete_blocked_when_referenced_by_inflight_run(session):
    pid = svc.create(session, name="p", body="b")
    acc = acc_repo.create(session, {
        "profile_name": "ph",
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    tid = eng_repo.create_task(
        session,
        group_id=None,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_prompt_id=pid,
        comments_per_video=1,
    )
    eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=acc,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )

    with pytest.raises(svc.PromptInUse) as exc:
        svc.delete(session, pid)
    assert exc.value.prompt_id == pid
    assert len(exc.value.run_ids) == 1
    assert cp_repo.get(session, pid) is not None


def test_delete_allowed_when_referenced_run_is_terminal(session):
    pid = svc.create(session, name="p", body="b")
    acc = acc_repo.create(session, {
        "profile_name": "ph",
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    tid = eng_repo.create_task(
        session,
        group_id=None,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_prompt_id=pid,
        comments_per_video=1,
    )
    rid = eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=acc,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )
    eng_repo.set_result(session, rid, "SUCCESS", "ok")

    assert svc.delete(session, pid) is True
    assert cp_repo.get(session, pid) is None
```

This test calls `eng_repo.create_task` with `comment_prompt_id=pid` — the repository signature is widened to accept this in Task 6. Run the tests now and they will fail; that's expected — they unblock once Task 6 lands.

- [ ] **Step 2: Implement the service**

Create `app/services/comment_prompts_service.py`:

```python
"""Validation + in-use guard for the comment_prompts pool.

CommentModal picks a prompt when starting a studio_ai batch. This service
owns the prompt rows themselves; the FK on engagement_tasks does the
linking. Deletion is refused while any engagement_runs row tied to the
prompt is still inflight (PENDING / QUEUED / INITIATED).
"""
from sqlalchemy import text
from sqlmodel import Session

from app.repositories import comment_prompts as repo


class PromptInUse(RuntimeError):
    """Raised when a caller tries to delete a prompt that is still referenced
    by an inflight engagement run. The dashboard surfaces the run_ids."""

    def __init__(self, prompt_id: int, run_ids: list[int]):
        self.prompt_id = prompt_id
        self.run_ids = run_ids
        super().__init__(
            f"prompt {prompt_id} still in use by run ids: {run_ids}"
        )


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    if len(name) > 120:
        raise ValueError("name too long (max 120 chars)")
    return name


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValueError("body required")
    return body


def list_all(session: Session) -> list[dict]:
    return repo.list_all(session)


def get(session: Session, prompt_id: int) -> dict | None:
    return repo.get(session, prompt_id)


def create(session: Session, *, name: str, body: str) -> int:
    name = _clean_name(name)
    body = _clean_body(body)
    if repo.get_by_name(session, name) is not None:
        raise ValueError("name already used")
    return repo.create(session, name=name, body=body)


def update(session: Session, prompt_id: int, *,
           name: str | None = None, body: str | None = None) -> bool:
    if name is not None:
        name = _clean_name(name)
        other = repo.get_by_name(session, name)
        if other is not None and other["id"] != prompt_id:
            raise ValueError("name already used")
    if body is not None:
        body = _clean_body(body)
    return repo.update(session, prompt_id, name=name, body=body)


def delete(session: Session, prompt_id: int) -> bool:
    rows = session.execute(
        text(
            "SELECT r.id FROM engagement_runs r "
            "JOIN engagement_tasks t ON r.engagement_task_id = t.id "
            "WHERE t.comment_prompt_id = :pid "
            "  AND r.result IN ('PENDING', 'QUEUED', 'INITIATED') "
            "ORDER BY r.id"
        ),
        {"pid": prompt_id},
    ).fetchall()
    if rows:
        raise PromptInUse(prompt_id, [r[0] for r in rows])
    return repo.delete(session, prompt_id)
```

- [ ] **Step 3: Verify tests still fail with the expected error**

Run: `uv run pytest tests/test_comment_prompts_service.py -v`
Expected: validation tests now PASS; the two `_referenced_*` tests still FAIL because `eng_repo.create_task` does not yet accept `comment_prompt_id`. Leave them red — Task 6 fixes them.

- [ ] **Step 4: Commit**

```bash
git add tests/test_comment_prompts_service.py app/services/comment_prompts_service.py
git commit -m "feat(comments): service layer for comment_prompts pool"
```

---

## Task 5: REST API — `app/api/comment_prompts.py` (TDD)

**Files:**
- Create: `tests/test_comment_prompts_api.py`
- Create: `app/api/comment_prompts.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_comment_prompts_api.py`:

```python
"""CRUD over /api/comment-prompts + delete-in-use 409."""
import pytest

from app import config as cfg
from app.repositories import accounts as acc_repo
from app.repositories import comment_prompts as cp_repo
from app.repositories import engagement as eng_repo


@pytest.fixture
def authed_client(client, monkeypatch):
    monkeypatch.setattr(cfg, "DASHBOARD_PASSWORD", "pw")
    r = client.post("/api/login", json={"password": "pw"})
    assert r.status_code == 200
    return client


def test_create_then_list(authed_client, session):
    r = authed_client.post(
        "/api/comment-prompts",
        json={"name": "viral", "body": "Big creator viral video"},
    )
    assert r.status_code == 200
    pid = r.json()["id"]

    rows = authed_client.get("/api/comment-prompts").json()
    assert any(p["id"] == pid and p["name"] == "viral" for p in rows)


def test_create_rejects_empty_name(authed_client):
    r = authed_client.post(
        "/api/comment-prompts", json={"name": " ", "body": "x"}
    )
    assert r.status_code == 400


def test_create_rejects_empty_body(authed_client):
    r = authed_client.post(
        "/api/comment-prompts", json={"name": "x", "body": ""}
    )
    assert r.status_code == 400


def test_create_rejects_duplicate_name(authed_client):
    authed_client.post(
        "/api/comment-prompts", json={"name": "dup", "body": "a"}
    )
    r = authed_client.post(
        "/api/comment-prompts", json={"name": "dup", "body": "b"}
    )
    assert r.status_code == 400


def test_patch_updates_body(authed_client, session):
    pid = cp_repo.create(session, name="p", body="old")
    r = authed_client.patch(
        f"/api/comment-prompts/{pid}", json={"body": "new"}
    )
    assert r.status_code == 200
    assert cp_repo.get(session, pid)["body"] == "new"


def test_patch_404_for_unknown(authed_client):
    r = authed_client.patch(
        "/api/comment-prompts/99999", json={"body": "x"}
    )
    assert r.status_code == 404


def test_delete_unused(authed_client, session):
    pid = cp_repo.create(session, name="p", body="b")
    r = authed_client.delete(f"/api/comment-prompts/{pid}")
    assert r.status_code == 200
    assert cp_repo.get(session, pid) is None


def test_delete_blocked_by_inflight_run(authed_client, session):
    pid = cp_repo.create(session, name="p", body="b")
    acc = acc_repo.create(session, {
        "profile_name": "ph",
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    tid = eng_repo.create_task(
        session,
        group_id=None,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_prompt_id=pid,
        comments_per_video=1,
    )
    eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=acc,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )
    r = authed_client.delete(f"/api/comment-prompts/{pid}")
    assert r.status_code == 409
    body = r.json()
    assert "runs" in body["detail"]
    assert cp_repo.get(session, pid) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_comment_prompts_api.py -v`
Expected: all FAIL with 404 or `ModuleNotFoundError`.

- [ ] **Step 3: Implement the API module**

Create `app/api/comment_prompts.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.services import comment_prompts_service as svc

router = APIRouter(prefix="/api/comment-prompts")


@router.get("")
def list_prompts(session: Session = Depends(get_session)):
    return svc.list_all(session)


@router.post("")
def create_prompt(body: dict, session: Session = Depends(get_session)):
    try:
        pid = svc.create(
            session,
            name=body.get("name") or "",
            body=body.get("body") or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": pid}


@router.patch("/{prompt_id}")
def patch_prompt(prompt_id: int, body: dict,
                 session: Session = Depends(get_session)):
    if svc.get(session, prompt_id) is None:
        raise HTTPException(status_code=404, detail="prompt not found")
    try:
        svc.update(
            session, prompt_id,
            name=body.get("name") if "name" in body else None,
            body=body.get("body") if "body" in body else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int, session: Session = Depends(get_session)):
    if svc.get(session, prompt_id) is None:
        raise HTTPException(status_code=404, detail="prompt not found")
    try:
        svc.delete(session, prompt_id)
    except svc.PromptInUse as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "prompt in use", "runs": e.run_ids},
        )
    return {"ok": True}
```

- [ ] **Step 4: Mount the router in `app/main.py`**

Find the import block around line 26:

```python
                          engagement, wallet, costs, caption_prompts)
```

Replace with:

```python
                          engagement, wallet, costs, caption_prompts,
                          comment_prompts)
```

Then find the `caption_prompts.router` include (around line 32) and add a sibling include below it:

```python
    app.include_router(caption_prompts.router,
                       dependencies=[Depends(require_auth)])
    app.include_router(comment_prompts.router,
                       dependencies=[Depends(require_auth)])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_comment_prompts_api.py -v`
Expected: 7 pass, `test_delete_blocked_by_inflight_run` still FAILS (it needs `eng_repo.create_task` to accept `comment_prompt_id` — fixed in Task 6).

- [ ] **Step 6: Commit**

```bash
git add tests/test_comment_prompts_api.py app/api/comment_prompts.py app/main.py
git commit -m "feat(comments): REST endpoints for comment_prompts"
```

---

## Task 6: Engagement repository — `create_task` accepts `comment_prompt_id`

**Files:**
- Modify: `app/repositories/engagement.py`
- Modify: `tests/test_repositories_engagement.py`
- Modify: `tests/test_eligible_phones_endpoint.py`

- [ ] **Step 1: Widen the repository signature**

In `app/repositories/engagement.py`, locate `create_task` (around line 32):

```python
def create_task(session: Session, *, group_id: Optional[int], action: str,
                source: str, target_links: list[str],
                target_publication_ids: list[int],
                comment_probability: int,
                comment_mode: str = "geelark_ai",
                comment_brief: Optional[str] = None,
                comments_per_video: int = 1) -> int:
    obj = EngagementTask(
        group_id=group_id,
        action=action,
        source=source,
        target_links=target_links,
        target_publication_ids=target_publication_ids,
        comment_probability=comment_probability,
        comment_mode=comment_mode,
        comment_brief=comment_brief,
        comments_per_video=comments_per_video,
    )
```

Replace with:

```python
def create_task(session: Session, *, group_id: Optional[int], action: str,
                source: str, target_links: list[str],
                target_publication_ids: list[int],
                comment_probability: int,
                comment_mode: str = "geelark_ai",
                comment_brief: Optional[str] = None,
                comment_prompt_id: Optional[int] = None,
                comments_per_video: int = 1) -> int:
    obj = EngagementTask(
        group_id=group_id,
        action=action,
        source=source,
        target_links=target_links,
        target_publication_ids=target_publication_ids,
        comment_probability=comment_probability,
        comment_mode=comment_mode,
        comment_brief=comment_brief,
        comment_prompt_id=comment_prompt_id,
        comments_per_video=comments_per_video,
    )
```

(Old callers that pass only `comment_brief` keep working — the new keyword defaults to `None`.)

- [ ] **Step 2: Run the existing engagement and prompts suites**

Run: `uv run pytest tests/test_repositories_engagement.py tests/test_comment_prompts_service.py tests/test_comment_prompts_api.py -v`
Expected: all PASS. The previously red prompt tests (`test_delete_blocked_when_referenced_by_inflight_run`, `test_delete_allowed_when_referenced_run_is_terminal`, `test_delete_blocked_by_inflight_run`) now turn green.

- [ ] **Step 3: Commit**

```bash
git add app/repositories/engagement.py
git commit -m "feat(comments): engagement repo accepts comment_prompt_id"
```

---

## Task 7: Engagement service — wire prompt selection into batch creation

**Files:**
- Modify: `app/services/engagement_service.py`
- Modify: `tests/test_engagement_service.py`

The service must (a) accept `comment_prompt_id` instead of `comment_brief`, (b) require it when `comment_mode == "studio_ai"`, (c) validate the prompt exists, and (d) resolve the brief from the prompt body in `_prepare_studio_comment`.

- [ ] **Step 1: Update existing tests to the new signature**

In `tests/test_engagement_service.py`:

1. Find `test_submit_engagement_run_generates_studio_ai_comment` (around line 296). The current call uses `comment_brief="..."`. Replace the `comment_brief=` kwarg with `comment_prompt_id=pid` after first creating the prompt:

```python
def test_submit_engagement_run_generates_studio_ai_comment(session, monkeypatch):
    from app.repositories import comment_prompts as cp_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    pid = cp_repo.create(
        session,
        name="ielts-mock",
        body=(
            "Push the daily AI mock interview angle. "
            "Mention practice naturally without sounding like an ad."
        ),
    )
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_prompt_id=pid,
    )
    run = eng_repo.list_runs_for_task(session, result["task_id"])[0]

    monkeypatch.setattr(
        engagement_service.tiktok_metadata,
        "fetch_tiktok_metadata",
        lambda link: {
            "source_url": link,
            "title": "How to improve IELTS speaking part 2",
            "description": "A creator explains a daily speaking practice loop.",
        },
    )

    def fake_generate_comment_for_target(**kwargs):
        assert kwargs["target_title"] == "How to improve IELTS speaking part 2"
        assert "daily AI mock interview angle" in kwargs["brief"]
        return "This kind of daily speaking loop is exactly what helps it stick."

    monkeypatch.setattr(
        engagement_service.comment_generation,
        "generate_comment_for_target",
        fake_generate_comment_for_target,
    )
    # ... rest of the test (FakeClient submit) unchanged
```

2. Find `test_create_comment_batch_allows_blank_brief_for_studio_ai` (around line 468) and rewrite to assert the new required-prompt-id behaviour:

```python
def test_create_comment_batch_requires_prompt_id_for_studio_ai(session):
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    with pytest.raises(ValueError, match="comment_prompt_id is required"):
        engagement_service.create_comment_batch(
            session,
            group_id=gid,
            account_ids=[commenter],
            target_links=["https://www.tiktok.com/@owner/video/1234567890"],
            target_publication_ids=[],
            comment_probability=100,
            comment_mode="studio_ai",
        )
```

3. Find `test_eng_repo_create_task_persists_comments_per_video` (around line 681) and leave it alone — it does not exercise the new field.

4. Add a new test verifying the legacy fallback path still works (rows with `comment_brief` set, `comment_prompt_id` NULL):

```python
def test_prepare_studio_comment_falls_back_to_legacy_brief(session, monkeypatch):
    """Rows persisted before the prompt pool keep working: when
    comment_prompt_id is NULL, _combined_brief() supplies the brief."""
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    # Insert directly via the repo without a prompt_id, mimicking pre-pool data.
    tid = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_brief="legacy free-text brief about CEFR highlighter",
        comments_per_video=1,
    )
    rid = eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=commenter,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )

    monkeypatch.setattr(
        engagement_service.tiktok_metadata,
        "fetch_tiktok_metadata",
        lambda link: {"source_url": link, "title": "t", "description": "d"},
    )

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(
        engagement_service.comment_generation,
        "generate_comment_for_target",
        fake_generate,
    )

    parent = eng_repo.get_task(session, tid)
    run = eng_repo.get_run(session, rid)
    engagement_service._prepare_studio_comment(session, run, parent)

    assert "legacy free-text brief about CEFR highlighter" in captured["brief"]
```

- [ ] **Step 2: Run tests to verify they fail in the expected spots**

Run: `uv run pytest tests/test_engagement_service.py -v -k "studio_ai or legacy"`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'comment_prompt_id'` (the service signature has not been updated yet).

- [ ] **Step 3: Update `create_comment_batch` signature + validation**

In `app/services/engagement_service.py`, locate `create_comment_batch` (around line 156). Replace the parameter list and the validation/persistence block:

```python
def create_comment_batch(
    session,
    *,
    account_ids: list[int],
    target_links: list[str] | None = None,
    target_publication_ids: list[int] | None = None,
    comments_per_video: int = 1,
    group_id: int | None = None,
    comment_probability: int = 100,
    comment_mode: str = COMMENT_MODE_STUDIO_AI,
    comment_prompt_id: int | None = None,
    provided_comments: list[str] | None = None,
) -> dict:
    if not isinstance(account_ids, list) or not account_ids:
        raise ValueError("at least one phone must be selected")
    _validate_probability(comment_probability)
    comment_mode = _validate_comment_config(comment_mode)
    if comment_mode == COMMENT_MODE_STUDIO_AI:
        if comment_prompt_id is None:
            raise ValueError(
                "comment_prompt_id is required when comment_mode is studio_ai"
            )
        if cp_repo.get(session, comment_prompt_id) is None:
            raise ValueError(
                f"comment_prompt_id {comment_prompt_id} does not exist"
            )
```

(Replaces the previous `comment_brief: str | None = None,` parameter and the corresponding `_validate_comment_config(comment_mode)` call.)

Add the new import at the top of the file (alongside the other `app.repositories` imports):

```python
from app.repositories import comment_prompts as cp_repo
```

Find the `eng_repo.create_task(...)` call (around line 243) and replace `comment_brief=(comment_brief or "").strip() or None,` with `comment_prompt_id=comment_prompt_id,` (drop the `comment_brief` line entirely — new rows do not write it):

```python
        task_id = eng_repo.create_task(
            session,
            group_id=group_id,
            action=ACTION_TIKTOK_AI_COMMENT,
            source=source,
            target_links=[target["target_link"]],
            target_publication_ids=[target["target_publication_id"]]
            if target.get("target_publication_id") is not None
            else [],
            comment_probability=comment_probability,
            comment_mode=comment_mode,
            comment_prompt_id=comment_prompt_id,
            comments_per_video=comments_per_video,
        )
```

- [ ] **Step 4: Update `_prepare_studio_comment` to resolve the prompt**

Replace the existing body (around line 295):

```python
def _prepare_studio_comment(session, run: dict, parent: dict) -> str:
    meta = tiktok_metadata.fetch_tiktok_metadata(run["target_link"])
    title = meta.get("title")
    description = meta.get("description")
    if title or description:
        eng_repo.set_target_context(
            session,
            run["id"],
            title=title,
            description=description,
        )
    prompt_id = parent.get("comment_prompt_id")
    if prompt_id is not None:
        prompt = cp_repo.get(session, prompt_id)
        if prompt is None:
            raise RuntimeError(
                f"comment_prompts row {prompt_id} vanished before submit"
            )
        brief = prompt["body"]
    else:
        brief = _combined_brief(parent)
    comment = comment_generation.generate_comment_for_target(
        target_title=title,
        target_description=description,
        target_link=run["target_link"],
        brief=brief,
    )
    eng_repo.set_generated_comment(
        session,
        run["id"],
        comment_text=comment,
        source=COMMENT_MODE_STUDIO_AI,
    )
    return comment
```

- [ ] **Step 5: Run the engagement tests**

Run: `uv run pytest tests/test_engagement_service.py -v`
Expected: all PASS (including the new `test_prepare_studio_comment_falls_back_to_legacy_brief`).

- [ ] **Step 6: Run the full suite for regressions**

Run: `uv run pytest -q`
Expected: 720+ passed (we added ~7 new tests; nothing else should regress).

- [ ] **Step 7: Commit**

```bash
git add app/services/engagement_service.py tests/test_engagement_service.py
git commit -m "feat(comments): create_comment_batch + worker use comment_prompt_id"
```

---

## Task 8: Engagement API — body schema swap

**Files:**
- Modify: `app/api/engagement.py`

- [ ] **Step 1: Update the POST body parsing**

In `app/api/engagement.py`, locate the `create_comment_engagement` handler (around line 32). Replace the `engagement_service.create_comment_batch(...)` call so it forwards `comment_prompt_id` instead of `comment_brief`:

```python
        result = engagement_service.create_comment_batch(
            session,
            account_ids=body.get("account_ids") or [],
            target_links=body.get("target_links") or [],
            target_publication_ids=body.get("target_publication_ids") or [],
            comments_per_video=comments_per_video,
            group_id=body.get("group_id"),
            comment_probability=body.get("comment_probability", 100),
            comment_mode=body.get("comment_mode", COMMENT_MODE_STUDIO_AI),
            comment_prompt_id=body.get("comment_prompt_id"),
            provided_comments=body.get("provided_comments"),
        )
```

- [ ] **Step 2: Run the engagement endpoint tests**

Run: `uv run pytest tests/test_eligible_phones_endpoint.py tests/test_comment_prompts_api.py tests/test_engagement_service.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add app/api/engagement.py
git commit -m "feat(comments): POST /api/engagement/comment takes comment_prompt_id"
```

---

## Task 9: Frontend API client + types

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add the `CommentPrompt` type**

Find the existing `CaptionPrompt` type (around line 172) and add immediately after it:

```typescript
export type CommentPrompt = {
  id: number
  name: string
  body: string
  created_at: string | null
  updated_at: string | null
}
```

- [ ] **Step 2: Swap the `EngagementTask` type field**

Find `EngagementTask` (around line 43). Add `comment_prompt_id` and keep `comment_brief` (read-only fallback):

```typescript
export type EngagementTask = {
  id: number
  group_id: number | null
  action: string
  source: string
  target_links: string[]
  target_publication_ids: number[]
  comment_probability: number
  comment_mode: 'geelark_ai' | 'studio_ai'
  comment_prompt_id: number | null
  comment_brief: string | null
  status: EngagementTaskStatus
  summary: string | null
  created_at: string | null
  updated_at: string | null
  finished_at: string | null
}
```

- [ ] **Step 3: Swap `createEngagementComment` body**

Find `createEngagementComment` (around line 327) and replace the body type:

```typescript
  createEngagementComment: (body: {
    account_ids: number[]
    target_links?: string[]
    target_publication_ids?: number[]
    comments_per_video: number
    comment_prompt_id?: number | null
  }): Promise<{ task_id: number; created_runs: number; deferred_runs: number }> =>
    req('/engagement/comment', { method: 'POST', body: JSON.stringify(body) }),
```

- [ ] **Step 4: Add the four CRUD methods next to the caption prompt ones**

Find the `deleteCaptionPrompt` line (around line 325) and add immediately after it:

```typescript
  commentPrompts: (): Promise<CommentPrompt[]> =>
    req('/comment-prompts'),
  createCommentPrompt: (body: { name: string; body: string }) =>
    req('/comment-prompts', { method: 'POST', body: JSON.stringify(body) }),
  updateCommentPrompt: (id: number,
                        body: { name?: string; body?: string }) =>
    req(`/comment-prompts/${id}`,
        { method: 'PATCH', body: JSON.stringify(body) }),
  deleteCommentPrompt: (id: number) =>
    req(`/comment-prompts/${id}`, { method: 'DELETE' }),
```

- [ ] **Step 5: Verify the frontend type-checks**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b --noEmit`
Expected: no errors (CommentModal will be updated next task — for now it still references `commentBrief`; if tsc complains, that's expected and will be fixed in Task 10).

If tsc complains only about CommentModal, that's OK — proceed.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(comments): frontend API client for comment_prompts"
```

---

## Task 10: CommentModal — replace textarea with prompt select

**Files:**
- Modify: `frontend/src/components/CommentModal.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Replace state, payload, and ConfigureStep props**

Open `frontend/src/components/CommentModal.tsx`.

(a) Update the import at the top of the file (around line 4) to include `CommentPrompt`:

```typescript
import { api, type CommentTarget, type EligiblePhone, type DiscoverTarget, type CommentPrompt } from '../api'
```

(b) Replace `CommentSubmitPayload` (around line 12) — swap `comment_brief` for `comment_prompt_id`:

```typescript
export type CommentSubmitPayload = {
  account_ids: number[]
  target_links: string[]
  target_publication_ids: number[]
  comments_per_video: number
  comment_prompt_id: number | null
  comment_mode?: 'studio_ai' | 'provided'
  provided_comments?: string[] | null
}
```

(c) Replace the `commentBrief` state (around line 50) with prompt state:

```typescript
  const [perVideo, setPerVideo] = useState(3)
  const [prompts, setPrompts] = useState<CommentPrompt[]>([])
  const [commentPromptId, setCommentPromptId] = useState<number | null>(null)
  const [commentMode, setCommentMode] = useState<CommentMode>('generate')
```

(d) Extend the existing modal-open effect (around line 56) to also load prompts:

Find the `Promise.all([...])` that fetches phones/targets/discover. Add `api.commentPrompts().catch(() => [])` as a fourth entry and destructure the new value:

```typescript
    Promise.all([
      api.eligiblePhones(),
      api.engagementTargets(),
      api.discoverTargets().catch(() => null),
      api.commentPrompts().catch(() => [] as CommentPrompt[]),
    ])
      .then(([p, t, d, pr]) => {
        setPhones(p.items)
        setTargets(t.items)
        if (d) {
          setDiscoverTargets(d.items)
          setDiscoverLoadFailed(false)
        } else {
          setDiscoverTargets([])
          setDiscoverLoadFailed(true)
        }
        setSelectedPhones(new Set(p.items.filter((x) => !x.busy).map((x) => x.id)))
        setSelectedTargets(new Set())
        setSelectedDiscover(new Set())
        setPrompts(pr)
        setCommentPromptId(pr.length === 1 ? pr[0].id : null)
      })
      .catch(() => {})
```

(e) Replace the `canSubmit` block (around line 137) to require a prompt selection in `generate` mode:

```typescript
  const canSubmit =
    !submitting &&
    phoneCount > 0 &&
    targetCount > 0 &&
    perVideo >= 1 &&
    perVideo <= 20 &&
    (
      (commentMode === 'generate' && commentPromptId != null) ||
      (commentMode === 'provided' && providedCommentList.length > 0)
    )
```

(f) In `submit` (around line 160), replace the `product_info` / `comment_brief` lines with `comment_prompt_id`:

```typescript
        comments_per_video: perVideo,
        comment_prompt_id: commentMode === 'generate' ? commentPromptId : null,
        comment_mode: commentMode === 'provided' ? 'provided' : 'studio_ai',
        provided_comments: commentMode === 'provided' ? providedCommentList : null,
```

(g) Update the `ConfigureStep` invocation (around line 236) — swap brief props for prompt props:

```typescript
          {step === 3 && (
            <ConfigureStep
              perVideo={perVideo}
              onPerVideo={setPerVideo}
              reuseHint={reuseHint}
              prompts={prompts}
              commentPromptId={commentPromptId}
              onCommentPromptId={setCommentPromptId}
              totalRuns={totalRuns}
              targetCount={targetCount}
              commentMode={commentMode}
              onCommentMode={setCommentMode}
              providedComments={providedComments}
              onProvidedComments={setProvidedComments}
              providedCount={providedCommentList.length}
              providedShortHint={providedShortHint}
            />
          )}
```

- [ ] **Step 2: Replace the `ConfigureStep` signature + textarea**

Find `function ConfigureStep({` (around line 645). Replace the props block:

```typescript
function ConfigureStep({
  perVideo,
  onPerVideo,
  reuseHint,
  prompts,
  commentPromptId,
  onCommentPromptId,
  totalRuns,
  targetCount,
  commentMode,
  onCommentMode,
  providedComments,
  onProvidedComments,
  providedCount,
  providedShortHint,
}: {
  perVideo: number
  onPerVideo: (v: number) => void
  reuseHint: boolean
  prompts: CommentPrompt[]
  commentPromptId: number | null
  onCommentPromptId: (v: number | null) => void
  totalRuns: number
  targetCount: number
  commentMode: CommentMode
  onCommentMode: (m: CommentMode) => void
  providedComments: string
  onProvidedComments: (v: string) => void
  providedCount: number
  providedShortHint: boolean
}) {
```

Find the `generate` branch (currently `{commentMode === 'generate' ? (<div> ...textarea... </div>) : (...) }` around line 744) and replace the whole `generate` block:

```typescript
      {commentMode === 'generate' ? (
        <div>
          <div className="flex items-end justify-between mb-1.5 gap-3">
            <label className="block text-[12px] font-medium text-[var(--color-ink-2)]">
              {t('comments.commentPromptLabel')}
            </label>
            <a
              href="/settings#comment-prompts"
              target="_blank"
              rel="noreferrer"
              className="text-[11.5px] text-[var(--color-blue)] hover:underline"
            >
              {t('comments.managePrompts')}
            </a>
          </div>
          {prompts.length === 0 ? (
            <div className="text-[12.5px] text-[var(--color-ink-4)] bg-[var(--color-surface-hover)] rounded-[10px] px-3 py-3 border border-dashed border-[var(--color-line)]">
              {t('comments.noPromptsHint')}
            </div>
          ) : (
            <>
              <select
                value={commentPromptId ?? ''}
                onChange={(e) => {
                  const v = e.target.value
                  onCommentPromptId(v === '' ? null : Number(v))
                }}
                className="w-full bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-ink)] focus:border-[var(--color-blue)] focus:ring-2 focus:ring-[var(--color-blue-bg)] focus:outline-none"
              >
                <option value="">{t('comments.commentPromptPlaceholder')}</option>
                {prompts.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              {commentPromptId != null && (
                <pre className="mt-2 text-[11.5px] text-[var(--color-ink-3)] bg-[var(--color-surface-hover)] rounded-[10px] px-3 py-2 max-h-[120px] overflow-y-auto whitespace-pre-wrap font-mono leading-relaxed">
                  {prompts.find((p) => p.id === commentPromptId)?.body ?? ''}
                </pre>
              )}
            </>
          )}
        </div>
      ) : (
```

(Leave the existing `provided` branch unchanged.)

- [ ] **Step 3: Update i18n strings**

In `frontend/src/i18n/locales/en.json` under the `comments` block, replace the three commentBrief keys with the new prompt keys:

```json
    "commentPromptLabel": "AI comment prompt",
    "commentPromptPlaceholder": "— pick a prompt —",
    "noPromptsHint": "No comment prompts yet — create one in Settings before starting a studio AI batch.",
    "managePrompts": "Manage prompts",
```

(Remove `commentBrief`, `commentBriefOptional`, `commentBriefPlaceholder`, `commentBriefHelp` — they are no longer referenced.)

Also update `step3Sub`:

```json
    "step3Sub": "Per-video count · AI comment prompt",
```

Mirror the same keys in `frontend/src/i18n/locales/zh.json`:

```json
    "commentPromptLabel": "AI 评论提示词",
    "commentPromptPlaceholder": "— 选一个提示词 —",
    "noPromptsHint": "还没有评论提示词，先去 Settings 创建一个才能用 studio AI 模式。",
    "managePrompts": "管理提示词",
    "step3Sub": "每视频条数 · AI 评论提示词",
```

(Remove the matching commentBrief* keys.)

- [ ] **Step 4: Verify the frontend builds**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build`
Expected: build completes with no TS errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CommentModal.tsx frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(comments): CommentModal picks from the prompt pool"
```

---

## Task 11: Settings page — `CommentPromptsSection`

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: Add the import**

In `frontend/src/pages/Settings.tsx` line 3, extend the type import:

```typescript
import { api, type CaptionPrompt, type CommentPrompt } from '../api'
```

- [ ] **Step 2: Mount the section below `CaptionPromptsSection`**

Find the `<CaptionPromptsSection onToast={onToast} />` line (around line 173) and add immediately after it:

```tsx
      <CommentPromptsSection onToast={onToast} />
```

- [ ] **Step 3: Define `CommentPromptsSection`**

Add this function at the bottom of the file, after `CaptionPromptsSection` (around line 369). It's a structural copy of CaptionPromptsSection with caption→comment renamed; we duplicate rather than abstract because the two pools may diverge later (different help text, different validation hooks):

```tsx
function CommentPromptsSection({ onToast }: { onToast: (t: ToastState) => void }) {
  const [prompts, setPrompts] = useState<CommentPrompt[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<
    { id: number | null; name: string; body: string } | null
  >(null)
  const [saving, setSaving] = useState(false)

  const load = () => {
    setLoading(true)
    api.commentPrompts()
      .then(setPrompts)
      .catch(() => onToast({ msg: 'Failed to load comment prompts', tone: 'red' }))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    if (!editing) return
    const name = editing.name.trim()
    const body = editing.body.trim()
    if (!name || !body) {
      onToast({ msg: 'Name and body are required', tone: 'red' })
      return
    }
    setSaving(true)
    try {
      if (editing.id == null) {
        await api.createCommentPrompt({ name, body })
      } else {
        await api.updateCommentPrompt(editing.id, { name, body })
      }
      setEditing(null)
      load()
      onToast({ msg: 'Comment prompt saved', tone: 'green' })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      onToast({ msg, tone: 'red' })
    } finally {
      setSaving(false)
    }
  }

  const remove = async (p: CommentPrompt) => {
    if (!confirm(`Delete comment prompt "${p.name}"? Inflight comment runs using it must finish first.`)) return
    try {
      await api.deleteCommentPrompt(p.id)
      load()
      onToast({ msg: 'Comment prompt deleted', tone: 'green' })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Delete failed'
      onToast({ msg, tone: 'red' })
    }
  }

  return (
    <section id="comment-prompts">
      <SectionTitle>Comment prompts</SectionTitle>
      <Card className="p-6 flex flex-col gap-4">
        <p className="text-[12.5px] text-[var(--color-ink-3)] leading-relaxed">
          The CommentModal picks one of these per batch when starting a
          studio AI comment run. Edits take effect on the next worker
          pickup for queued runs that already reference the prompt.
        </p>
        {loading ? (
          <p className="text-[13px] text-[var(--color-ink-3)]">Loading…</p>
        ) : prompts.length === 0 ? (
          <p className="text-[13px] text-[var(--color-ink-3)]">No comment prompts yet.</p>
        ) : (
          <div className="flex flex-col divide-y divide-[var(--color-line)]">
            {prompts.map((p) => (
              <div key={p.id} className="py-3 flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-medium text-[var(--color-ink)]">{p.name}</div>
                  <div className="text-[12px] text-[var(--color-ink-3)] mt-0.5 line-clamp-2 font-mono leading-relaxed">
                    {p.body.slice(0, 200)}{p.body.length > 200 ? '…' : ''}
                  </div>
                </div>
                <Button variant="ghost" size="sm"
                        onClick={() => setEditing({ id: p.id, name: p.name, body: p.body })}>
                  Edit
                </Button>
                <Button variant="ghost" size="sm" onClick={() => remove(p)}>
                  Delete
                </Button>
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-end">
          <Button variant="primary"
                  onClick={() => setEditing({ id: null, name: '', body: '' })}>
            + New prompt
          </Button>
        </div>
      </Card>

      {editing && (
        <Modal open onClose={() => setEditing(null)}
               title={editing.id == null ? 'New comment prompt' : 'Edit comment prompt'}>
          <div className="flex flex-col gap-5 p-1">
            <TextField label="Name"
                       value={editing.name}
                       onChange={(e) =>
                         setEditing((prev) => prev ? { ...prev, name: e.target.value } : prev)
                       } />
            <label className="block">
              <span className="block text-[12.5px] font-medium text-[var(--color-ink-2)] mb-1.5">
                Body
              </span>
              <textarea rows={14}
                        value={editing.body}
                        onChange={(e) =>
                          setEditing((prev) => prev ? { ...prev, body: e.target.value } : prev)
                        }
                        className="w-full bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[8px] px-3 py-2 text-[13px] text-[var(--color-ink)] focus:border-[var(--color-blue)] focus:ring-2 focus:ring-[var(--color-blue-bg)] transition-shadow font-mono leading-relaxed" />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setEditing(null)} disabled={saving}>
                Cancel
              </Button>
              <Button variant="primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  )
}
```

- [ ] **Step 4: Verify the frontend builds**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Settings.tsx
git commit -m "feat(comments): Settings page surfaces the comment prompt pool"
```

---

## Task 12: Full verification + deploy

- [ ] **Step 1: Run the entire pytest suite**

Run: `uv run pytest -q`
Expected: 720+ passed (existing 713 baseline + ~7 new tests, nothing regressed).

- [ ] **Step 2: Frontend build smoke check**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Package the deploy tarball**

```bash
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-deploy.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini geelark_client.py generate_caption.py generate_comment.py \
  pyproject.toml uv.lock frontend/dist
ls -lh /tmp/mkt-deploy.tar.gz
```

- [ ] **Step 4: Upload to aliyun**

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-deploy.tar.gz root@139.196.52.92:/tmp/mkt-deploy.tar.gz
```

- [ ] **Step 5: Extract + alembic upgrade + restart**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/root/.local/bin:$PATH"
  cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini frontend/dist geelark_client.py generate_caption.py generate_comment.py pyproject.toml uv.lock
  uv sync --no-dev
  uv run alembic upgrade head
  systemctl restart mkt mkt-worker
  sleep 5
  systemctl is-active mkt mkt-worker
  rm -f /tmp/mkt-deploy.tar.gz
'
```

Expected: `Running upgrade b5c4e8f3a712 -> c8e1a2b3d4f5, comment_prompts pool + engagement_tasks.comment_prompt_id` and `active` `active`.

- [ ] **Step 6: Smoke /healthz**

```bash
curl -fsS http://139.196.52.92:8080/healthz
```

Expected: `{"ok":true,"db":"ok",...}`.

- [ ] **Step 7: Operator follow-up**

Tell the user that they need to log in to production, open Settings → Comment prompts → New, and seed at least one prompt before the next studio_ai comment batch can be started.
