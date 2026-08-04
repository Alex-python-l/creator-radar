# Comments Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/comments` page — comment text as content, custom centered modal for submit, phone multi-select replaces group dropdown, drop comment-mode/probability from UI, add `comments_per_video`.

**Architecture:** Frontend rewrites `Comments.tsx` (≤350 lines) + extracts `CommentModal.tsx`; backend keeps the existing Procrastinate `phones` queue + `submit_engagement_run_task` plumbing untouched, but the dispatch step (`engagement_service.create_comment_batch`) is rewritten to take an explicit `account_ids` list and a `comments_per_video` integer with phone reuse allowed.

**Tech Stack:** FastAPI + SQLModel + Alembic + Procrastinate worker (backend), React 19 + Vite + Tailwind v4 + react-i18next (frontend), PostgreSQL 16.

**Spec:** [docs/superpowers/specs/2026-06-05-comments-page-redesign-design.md](docs/superpowers/specs/2026-06-05-comments-page-redesign-design.md)

---

## File Structure

### Backend — create / modify

- **Create** `alembic/versions/<new_revision>_comments_per_video.py` — adds the `engagement_tasks.comments_per_video` column.
- **Modify** `app/models.py:184-220` — add `comments_per_video` field to `EngagementTask`.
- **Modify** `app/services/engagement_service.py:121-216` — rewrite `create_comment_batch` dispatch (accept `account_ids` cross-group, allow phone reuse, multiply by `comments_per_video`).
- **Modify** `app/repositories/engagement.py` — extend `create_task` signature to persist `comments_per_video`.
- **Modify** `app/api/engagement.py:29-66` — update `POST /comment` to accept new payload and add `GET /eligible-phones`.
- **Create** `app/repositories/eligible_phones.py` — new repository helper that returns commenter-eligible accounts cross-group with a `busy` flag.
- **Modify** `tests/test_engagement_service.py` — add 3 new TDD cases for the new dispatch path.
- **Create** `tests/test_eligible_phones_endpoint.py` — TDD for new endpoint.

### Frontend — create / modify

- **Modify** `frontend/src/index.css:3-49` — add `--font-serif` to the `@theme` block.
- **Modify** `frontend/src/api.ts:37-104` — update `EngagementRun` / payload types, add `eligiblePhones()` and update `createEngagementComment()`.
- **Create** `frontend/src/components/CommentModal.tsx` — custom centered modal (~280 lines) for the submit form. Not a Drawer, not a native `<dialog>`.
- **Modify** `frontend/src/pages/Comments.tsx` — full rewrite (≤350 lines): hero-comment cards, status/source filter row, integrate `CommentModal`.
- **Modify** `frontend/src/i18n/locales/en.json` and `zh.json` — drop 17 old keys, add 10 new keys.

### Deploy

- **Modify** nothing in `docs/production-deploy.md` for now — the deploy runbook is generic; the new migration runs via the existing `alembic upgrade head` step.

---

## Task 1: Backend — add `comments_per_video` column

**Files:**
- Create: `alembic/versions/a1b2c3d4e5f6_comments_per_video.py`
- Modify: `app/models.py:184-220`

- [ ] **Step 1: Create the Alembic migration**

```python
"""add comments_per_video to engagement_tasks

Revision ID: a1b2c3d4e5f6
Revises: e2f4a6b8c901
Create Date: 2026-06-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e2f4a6b8c901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "comments_per_video",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("engagement_tasks", "comments_per_video")
```

- [ ] **Step 2: Apply the migration against `mkt_test`**

Run: `DATABASE_URL="postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test" uv run alembic upgrade head`
Expected: prints `Running upgrade e2f4a6b8c901 -> a1b2c3d4e5f6, add comments_per_video to engagement_tasks` and exits 0.

- [ ] **Step 3: Add the field to the model**

In `app/models.py`, locate the `EngagementTask` class at line 184. After the `comment_mode` field (line 206), add:

```python
    comments_per_video: int = Field(default=1, nullable=False, sa_column_kwargs={"server_default": "1"})
```

- [ ] **Step 4: Smoke test — model loads + column reads**

Add at the end of `tests/test_engagement_service.py`:

```python
def test_engagement_task_has_comments_per_video_column(session):
    from sqlalchemy import inspect
    from app.db import engine
    cols = {c["name"] for c in inspect(engine).get_columns("engagement_tasks")}
    assert "comments_per_video" in cols
```

Run: `DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test uv run pytest tests/test_engagement_service.py::test_engagement_task_has_comments_per_video_column -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/a1b2c3d4e5f6_comments_per_video.py app/models.py tests/test_engagement_service.py
git commit -m "feat(engagement): add comments_per_video column"
```

---

## Task 2: Backend — extend `eng_repo.create_task` to persist `comments_per_video`

**Files:**
- Modify: `app/repositories/engagement.py` (`create_task` function — find with `grep -n "def create_task" app/repositories/engagement.py`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engagement_service.py`:

```python
def test_eng_repo_create_task_persists_comments_per_video(session):
    from app.repositories import engagement as eng_repo
    task_id = eng_repo.create_task(
        session,
        group_id=None,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        product_info=None,
        comment_brief=None,
        comments_per_video=5,
    )
    task = eng_repo.get_task(session, task_id)
    assert task["comments_per_video"] == 5
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_engagement_service.py::test_eng_repo_create_task_persists_comments_per_video -v`
Expected: FAIL with `TypeError: create_task() got an unexpected keyword argument 'comments_per_video'`.

- [ ] **Step 3: Implement — add `comments_per_video` to the create_task signature**

In `app/repositories/engagement.py`, update `create_task` to accept and persist `comments_per_video: int = 1`. The function builds an `EngagementTask` instance; add `comments_per_video=comments_per_video` to that constructor. Also make sure `get_task` returns it (most repo functions use `model_dump()` which will include it automatically once the column exists).

- [ ] **Step 4: Run test, verify it passes**

Run: `uv run pytest tests/test_engagement_service.py::test_eng_repo_create_task_persists_comments_per_video -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/engagement.py tests/test_engagement_service.py
git commit -m "feat(engagement): persist comments_per_video on task creation"
```

---

## Task 3: Backend — rewrite `create_comment_batch` dispatch

**Files:**
- Modify: `app/services/engagement_service.py:121-216`
- Modify: `tests/test_engagement_service.py`

**Context:** Today the service takes `group_id` (required), filters eligible accounts inside the group, and assigns 1 account per target via round-robin. The new behavior takes a flat `account_ids` list (may span groups), takes a `comments_per_video` integer, and creates `comments_per_video * len(targets)` runs by picking accounts with `random.choice` (repeats allowed). `group_id` becomes optional: if the caller does not pass it, the task's `group_id` is derived from the first selected account, OR left `None` if accounts span groups. `comment_probability` and `comment_mode` are kept as accepted-but-defaulted parameters (`100`, `"studio_ai"`) so the new UI omits them.

- [ ] **Step 1: Write failing test — phone reuse when comments_per_video > phones**

Append to `tests/test_engagement_service.py`:

```python
def test_create_comment_batch_reuses_phones_when_per_video_exceeds_phone_count(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, _owner, commenter, _logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comments_per_video=3,
    )

    all_runs = []
    for tid in result["task_ids"]:
        all_runs.extend(eng_repo.list_runs_for_task(session, tid))
    assert len(all_runs) == 3
    assert all(r["account_id"] == commenter for r in all_runs)
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_engagement_service.py::test_create_comment_batch_reuses_phones_when_per_video_exceeds_phone_count -v`
Expected: FAIL — either `group_id is required` error from current code, or `create_comment_batch()` rejects unknown `comments_per_video` kwarg.

- [ ] **Step 3: Write second failing test — multi-target × per-video produces M×K runs**

Append:

```python
def test_create_comment_batch_creates_videos_times_per_video_runs(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, _owner, commenter, _logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        account_ids=[commenter],
        target_links=[
            "https://www.tiktok.com/@x/video/1",
            "https://www.tiktok.com/@x/video/2",
        ],
        target_publication_ids=[],
        comments_per_video=2,
    )

    assert result["created_runs"] == 4
    all_runs = []
    for tid in result["task_ids"]:
        all_runs.extend(eng_repo.list_runs_for_task(session, tid))
    assert len(all_runs) == 4
```

- [ ] **Step 4: Write third failing test — invalid account_id rejected**

Append:

```python
def test_create_comment_batch_rejects_unknown_account(session):
    from app.services import engagement_service

    with pytest.raises(ValueError, match="does not exist"):
        engagement_service.create_comment_batch(
            session,
            account_ids=[99999],
            target_links=["https://www.tiktok.com/@x/video/1"],
            target_publication_ids=[],
            comments_per_video=1,
        )
```

- [ ] **Step 5: Run all three tests, verify failure**

Run: `uv run pytest tests/test_engagement_service.py::test_create_comment_batch_reuses_phones_when_per_video_exceeds_phone_count tests/test_engagement_service.py::test_create_comment_batch_creates_videos_times_per_video_runs tests/test_engagement_service.py::test_create_comment_batch_rejects_unknown_account -v`
Expected: 3 FAILs.

- [ ] **Step 6: Rewrite `create_comment_batch` to the new signature and dispatch logic**

Replace the function body in `app/services/engagement_service.py` (lines 121-216 today) with:

```python
import itertools


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
    product_info: str | None = None,
    comment_brief: str | None = None,
) -> dict:
    _validate_probability(comment_probability)
    comment_mode = _validate_comment_config(comment_mode, product_info)
    if not isinstance(account_ids, list) or not account_ids:
        raise ValueError("at least one phone must be selected")
    if not isinstance(comments_per_video, int) or comments_per_video < 1 or comments_per_video > 20:
        raise ValueError("comments_per_video must be between 1 and 20")
    target_links = target_links or []
    target_publication_ids = target_publication_ids or []

    accounts = []
    for account_id in account_ids:
        account = acc_repo.get(session, account_id)
        if account is None:
            raise ValueError(f"account {account_id} does not exist")
        if not _eligible_account(account):
            raise ValueError(
                f"account {account_id} is not an eligible TikTok commenter"
            )
        accounts.append(account)

    if group_id is None:
        group_ids = {a.get("group_id") for a in accounts}
        group_ids.discard(None)
        group_id = next(iter(group_ids)) if len(group_ids) == 1 else None

    manual_links = [normalize_target_link(link) for link in target_links]
    publication_targets = _publication_targets(session, target_publication_ids)
    targets = _dedupe_targets(_expand_targets(manual_links, publication_targets))
    if not targets:
        raise ValueError("at least one target link is required")

    planned: list[tuple[dict, dict]] = []
    for target in targets:
        eligible_for_target = [
            a for a in accounts if target.get("target_account_id") != a["id"]
        ]
        if not eligible_for_target:
            continue
        cyc = itertools.cycle(eligible_for_target)
        for _ in range(comments_per_video):
            planned.append((next(cyc), target))

    if not planned:
        raise ValueError("no eligible TikTok comment phones found")

    task_ids: list[int] = []
    run_ids: list[int] = []
    for account, target in planned:
        source = (
            "publication_share_links"
            if target.get("target_publication_id") is not None
            else "manual_links"
        )
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
            product_info=(product_info or "").strip() or None,
            comment_brief=(comment_brief or "").strip() or None,
            comments_per_video=comments_per_video,
        )
        run_id = eng_repo.create_run(
            session,
            engagement_task_id=task_id,
            account_id=account["id"],
            target_link=target["target_link"],
            action=ACTION_TIKTOK_AI_COMMENT,
            target_publication_id=target.get("target_publication_id"),
            target_account_id=target.get("target_account_id"),
        )
        task_ids.append(task_id)
        run_ids.append(run_id)

    return {
        "task_id": task_ids[0],
        "task_ids": task_ids,
        "run_ids": run_ids,
        "created_runs": len(planned),
        "skipped_runs": 0,
    }
```

Also make sure the module-level imports include `import itertools` if not already there (search the top of the file).

The constant `COMMENT_MODE_STUDIO_AI = "studio_ai"` is already defined near the top of the file (it is referenced by the existing `_validate_comment_config`). If `grep -n COMMENT_MODE_STUDIO_AI app/services/engagement_service.py` returns nothing, add it next to `COMMENT_MODE_GEELARK_AI`.

- [ ] **Step 7: Run the 3 new tests, verify they pass**

Run: `uv run pytest tests/test_engagement_service.py::test_create_comment_batch_reuses_phones_when_per_video_exceeds_phone_count tests/test_engagement_service.py::test_create_comment_batch_creates_videos_times_per_video_runs tests/test_engagement_service.py::test_create_comment_batch_rejects_unknown_account -v`
Expected: 3 PASS.

- [ ] **Step 8: Run the full engagement test file and fix existing tests**

Run: `uv run pytest tests/test_engagement_service.py -v`

Expected failures and how to fix each:

| Failure mode | Fix |
|---|---|
| `ValueError: account N is not an eligible TikTok commenter` (any test that passes `logged_out` in `account_ids`) | Remove `logged_out` from the `account_ids` list — pass only `[owner, commenter]` (or `[commenter]` when owner is the target's account). |
| `ValueError: at least one phone must be selected` | Add `account_ids=[commenter]` (or whichever fixture-returned account variable the test uses) to the call. |
| Assertion failure on run ordering / cursor-based round-robin (e.g. `assert [r["account_id"] for r in runs] == [owner]` no longer holds) | Update assertion to use `set(...) == {owner}` or `len(runs) == 1`. The new dispatcher uses `itertools.cycle(eligible_for_target)` per target, so each target's runs start from `eligible_for_target[0]`. For single-phone test cases the assertion stays valid. For multi-phone cases adapt to the cycle order. |
| Test passes `comment_mode="geelark_ai"` and asserts that branch | Keep the explicit kwarg — the service still accepts both modes. |

Repeat `uv run pytest tests/test_engagement_service.py -v` until all green.

- [ ] **Step 9: Commit**

```bash
git add app/services/engagement_service.py tests/test_engagement_service.py
git commit -m "feat(engagement): rewrite dispatch — account_ids list, comments_per_video, phone reuse"
```

---

## Task 4: Backend — new `GET /api/engagement/eligible-phones` endpoint

**Files:**
- Create: `app/repositories/eligible_phones.py`
- Modify: `app/api/engagement.py:1-99`
- Create: `tests/test_eligible_phones_endpoint.py`

- [ ] **Step 1: Write failing test for the endpoint**

Create `tests/test_eligible_phones_endpoint.py`:

```python
from fastapi.testclient import TestClient


def _bypass_auth(monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "require_admin", lambda: None)


def test_eligible_phones_returns_commenter_eligible_accounts(session, monkeypatch):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.main import create_app

    _bypass_auth(monkeypatch)
    gid = group_repo.create(session, name="G1")
    eligible = acc_repo.create(session, {
        "profile_name": "good_phone",
        "geelark_phone_id": "ph-good",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    acc_repo.create(session, {
        "profile_name": "warmed_but_logged_out",
        "geelark_phone_id": "ph-out",
        "group_id": gid,
        "tiktok_logged_in": 0,
        "warmed_up": 1,
    })
    acc_repo.create(session, {
        "profile_name": "logged_in_not_warmed",
        "geelark_phone_id": "ph-cold",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 0,
    })

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/engagement/eligible-phones")
    assert r.status_code == 200
    items = r.json()["items"]
    profile_names = {it["profile_name"] for it in items}
    assert "good_phone" in profile_names
    assert "warmed_but_logged_out" not in profile_names
    assert "logged_in_not_warmed" not in profile_names

    good = next(it for it in items if it["profile_name"] == "good_phone")
    assert good["id"] == eligible
    assert good["group_name"] == "G1"
    assert good["busy"] is False
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_eligible_phones_endpoint.py::test_eligible_phones_returns_commenter_eligible_accounts -v`
Expected: FAIL with `404 Not Found`.

- [ ] **Step 3: Implement the repository helper**

Create `app/repositories/eligible_phones.py`:

```python
from sqlmodel import Session, select

from app.models import Account, Group, EngagementRun


_INFLIGHT_RESULTS = ("PENDING", "QUEUED", "INITIATED")


def list_eligible_commenters(session: Session) -> list[dict]:
    rows = session.exec(
        select(Account, Group.name)
        .join(Group, Group.id == Account.group_id, isouter=True)
        .where(
            Account.tiktok_logged_in == 1,
            Account.warmed_up == 1,
            Account.geelark_phone_id.is_not(None),
            Account.geelark_phone_id != "",
        )
        .order_by(Group.name.nullslast(), Account.profile_name)
    ).all()

    busy_ids = {
        row[0]
        for row in session.exec(
            select(EngagementRun.account_id).where(
                EngagementRun.result.in_(_INFLIGHT_RESULTS),
                EngagementRun.account_id.is_not(None),
            )
        ).all()
    }

    items = []
    for account, group_name in rows:
        items.append({
            "id": account.id,
            "profile_name": account.profile_name,
            "group_id": account.group_id,
            "group_name": group_name,
            "busy": account.id in busy_ids,
        })
    return items
```

- [ ] **Step 4: Add the endpoint**

In `app/api/engagement.py`, after the existing `list_engagement_targets` route, add:

```python
@router.get("/eligible-phones")
def list_eligible_phones(session: Session = Depends(get_session)):
    from app.repositories import eligible_phones as eph_repo
    return {"items": eph_repo.list_eligible_commenters(session)}
```

- [ ] **Step 5: Run test, verify it passes**

Run: `uv run pytest tests/test_eligible_phones_endpoint.py::test_eligible_phones_returns_commenter_eligible_accounts -v`
Expected: PASS.

- [ ] **Step 6: Add busy-flag test**

Append to `tests/test_eligible_phones_endpoint.py`:

```python
def test_eligible_phones_marks_busy_when_inflight_run_exists(session, monkeypatch):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo
    from app.main import create_app

    _bypass_auth(monkeypatch)
    gid = group_repo.create(session, name="G1")
    acc_id = acc_repo.create(session, {
        "profile_name": "busy_phone",
        "geelark_phone_id": "ph-busy",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })

    tid = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        product_info=None,
        comment_brief=None,
        comments_per_video=1,
    )
    eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=acc_id,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/engagement/eligible-phones")
    busy = next(it for it in r.json()["items"] if it["profile_name"] == "busy_phone")
    assert busy["busy"] is True
```

Run: `uv run pytest tests/test_eligible_phones_endpoint.py::test_eligible_phones_marks_busy_when_inflight_run_exists -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/repositories/eligible_phones.py app/api/engagement.py tests/test_eligible_phones_endpoint.py
git commit -m "feat(engagement): GET /api/engagement/eligible-phones"
```

---

## Task 5: Backend — update `POST /api/engagement/comment` to new payload

**Files:**
- Modify: `app/api/engagement.py:29-66`

**Context:** The endpoint body-builder is permissive (`body: dict`). The only changes are: read `account_ids` (already supported), `comments_per_video` (new), and tolerate missing `group_id` / `comment_probability` / `comment_mode`.

- [ ] **Step 1: Modify the endpoint**

Replace lines 29-66 of `app/api/engagement.py` with:

```python
@router.post("/comment")
async def create_comment_engagement(
    body: dict,
    session: Session = Depends(get_session),
):
    try:
        result = engagement_service.create_comment_batch(
            session,
            account_ids=body.get("account_ids") or [],
            target_links=body.get("target_links") or [],
            target_publication_ids=body.get("target_publication_ids") or [],
            comments_per_video=int(body.get("comments_per_video", 1)),
            group_id=body.get("group_id"),
            comment_probability=body.get("comment_probability", 100),
            comment_mode=body.get("comment_mode", "studio_ai"),
            product_info=body.get("product_info"),
            comment_brief=body.get("comment_brief"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    deferred = 0
    run_ids = result.get("run_ids")
    if run_ids is None:
        run_ids = [
            run["id"]
            for run in eng_repo.list_runs_for_task(session, result["task_id"])
        ]
    for run_id in run_ids:
        try:
            await _defer_submit_engagement_run(run_id)
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="failed to enqueue engagement run",
            )
        eng_repo.mark_queued(session, run_id)
        deferred += 1
    return {**result, "deferred_runs": deferred}
```

- [ ] **Step 2: Run full engagement test suite, verify no regressions**

Run: `uv run pytest tests/test_engagement_service.py tests/test_eligible_phones_endpoint.py -v`
Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add app/api/engagement.py
git commit -m "feat(engagement): POST /comment accepts comments_per_video, account_ids primary"
```

---

## Task 6: Frontend — add `--font-serif` token

**Files:**
- Modify: `frontend/src/index.css:1-49`

- [ ] **Step 1: Add the serif font stack to the theme**

In `frontend/src/index.css`, inside the `@theme {}` block (line 3), after `--font-mono: …;` (line 48), add:

```css
  --font-serif: "Iowan Old Style", "Charter", "Source Serif Pro", "Hoefler Text", Georgia, serif;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(ui): add --font-serif token"
```

---

## Task 7: Frontend — update `api.ts`

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add type for eligible phone**

In `frontend/src/api.ts`, after the `CommentTarget` type (around line 93-103), add:

```typescript
export type EligiblePhone = {
  id: number
  profile_name: string | null
  group_id: number | null
  group_name: string | null
  busy: boolean
}
```

- [ ] **Step 2: Update the `createEngagementComment` signature inside `api`**

Find the existing `createEngagementComment` function inside the `api = { … }` object (`grep -n createEngagementComment frontend/src/api.ts`) and replace its body with:

```typescript
  createEngagementComment: (body: {
    account_ids: number[]
    target_links?: string[]
    target_publication_ids?: number[]
    comments_per_video: number
    product_info?: string | null
    comment_brief?: string | null
  }): Promise<{ task_id: number; created_runs: number; deferred_runs: number }> =>
    req('/engagement/comment', { method: 'POST', body: JSON.stringify(body) }),
```

- [ ] **Step 3: Add `eligiblePhones` function inside `api`**

In the same `api` object, near the other engagement functions, add:

```typescript
  eligiblePhones: (): Promise<{ items: EligiblePhone[] }> =>
    req('/engagement/eligible-phones'),
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(ui-api): eligiblePhones + updated createEngagementComment shape"
```

---

## Task 8: Frontend — update i18n locale files

**Files:**
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Update `en.json` `comments` block**

Replace the entire `comments: { … }` block in `frontend/src/i18n/locales/en.json` with:

```json
  "comments": {
    "title": "TikTok Comments",
    "newComment": "New comment",
    "filterStatus": "Status",
    "filterSource": "Source",
    "filterAll": "All",
    "filterSuccess": "Posted",
    "filterRunning": "Running",
    "filterQueued": "Queued",
    "filterFailed": "Failed",
    "filterOwned": "Owned videos",
    "filterManual": "Manual links",
    "autoRefresh": "Refreshes every 5s",
    "recordsEmpty": "No comments yet — click \"New comment\" to start.",
    "modalTitle": "New comment",
    "availablePhones": "Available phones",
    "selectAll": "Select all",
    "clearSelection": "Clear",
    "selectedCount": "{{selected}} / {{total}} selected",
    "ownedMode": "Owned published",
    "manualMode": "Manual links",
    "linksLabel": "TikTok video links",
    "linksPlaceholder": "Paste one TikTok video link per line",
    "ownedEmptyTitle": "No captured TikTok links yet",
    "ownedEmptyBody": "Published TikTok videos appear here after GeeLark returns a share link.",
    "commentsPerVideo": "Comments per video",
    "commentsPerVideoHelp": "Each selected video receives this many comments from random phones in the pool.",
    "phoneReuseHint": "Per-video count exceeds selected phones — some phones will comment more than once.",
    "productInfo": "Product override",
    "productInfoOptional": "(optional)",
    "productInfoPlaceholder": "Leave empty to use the built-in Cathoven IELTS pitch. Override for a special angle this batch (e.g. push the Rewriter, not the CEFR highlighter).",
    "commentBrief": "Comment brief",
    "commentBriefPlaceholder": "Optional tone, angle, or video type hint (e.g. \"big creator viral video — use Strategy A\" or \"help-seeking, name-drop the brand\").",
    "estimate": "About {{count}} comments queued ({{videos}} videos × {{perVideo}})",
    "cancel": "Cancel",
    "start": "Start commenting",
    "starting": "Starting…",
    "created": "Queued {{count}} comment(s)",
    "failed": "Could not start comments",
    "selectPhonesRequired": "Select at least one phone.",
    "targetsRequired": "Select at least one target.",
    "statusSuccess": "Posted",
    "statusRunning": "GeeLark running",
    "statusQueued": "Queued",
    "statusPending": "Pending",
    "statusFailed": "Failed",
    "statusSkipped": "Skipped",
    "free": "Idle",
    "busy": "Busy"
  },
```

- [ ] **Step 2: Update `zh.json` `comments` block**

Replace with:

```json
  "comments": {
    "title": "TikTok 评论",
    "newComment": "新建评论",
    "filterStatus": "状态",
    "filterSource": "来源",
    "filterAll": "全部",
    "filterSuccess": "已发布",
    "filterRunning": "运行中",
    "filterQueued": "排队中",
    "filterFailed": "失败",
    "filterOwned": "自有视频",
    "filterManual": "手动链接",
    "autoRefresh": "5 秒自动刷新",
    "recordsEmpty": "还没有评论 — 点「新建评论」开始派单。",
    "modalTitle": "新建评论",
    "availablePhones": "可用手机",
    "selectAll": "全选",
    "clearSelection": "清空",
    "selectedCount": "已选 {{selected}} / {{total}} 台",
    "ownedMode": "自有已发布",
    "manualMode": "手动链接",
    "linksLabel": "TikTok 视频链接",
    "linksPlaceholder": "每行粘贴一个 TikTok 视频链接",
    "ownedEmptyTitle": "暂无已捕获 TikTok 链接",
    "ownedEmptyBody": "GeeLark 返回分享链接后，已发布的 TikTok 视频会显示在这里。",
    "commentsPerVideo": "每个视频评论数",
    "commentsPerVideoHelp": "每个被选中的视频会被随机派出这么多台手机分别评论。",
    "phoneReuseHint": "手机数小于本次评论数 — 同一台手机会被多次派单。",
    "productInfo": "产品覆盖",
    "productInfoOptional": "(可选)",
    "productInfoPlaceholder": "留空即用内置 Cathoven IELTS 卖点。本批次想突出特定角度时再覆盖（例如：本批主推 Rewriter，不推 CEFR 高亮）。",
    "commentBrief": "评论要求",
    "commentBriefPlaceholder": "语气 / 角度 / 视频类型提示（例如「大V爆款 — 走策略A」「求助帖 — 直接带品牌名」）。",
    "estimate": "预计派发 {{count}} 条评论（{{videos}} 视频 × {{perVideo}} 条）",
    "cancel": "取消",
    "start": "开始评论",
    "starting": "开始中…",
    "created": "已排队 {{count}} 条评论",
    "failed": "评论启动失败",
    "selectPhonesRequired": "请至少选择一台手机。",
    "targetsRequired": "请至少选择一个评论目标。",
    "statusSuccess": "已发布",
    "statusRunning": "GeeLark 运行中",
    "statusQueued": "排队中",
    "statusPending": "等待派发",
    "statusFailed": "失败",
    "statusSkipped": "已跳过",
    "free": "空闲",
    "busy": "忙碌"
  },
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(i18n): comments page key set for the redesign"
```

---

## Task 9: Frontend — build `CommentModal` component

**Files:**
- Create: `frontend/src/components/CommentModal.tsx`

**Context:** This is the centered modal. Not a Drawer, not a native `<dialog>`. It is portaled into `<body>`, has its own scrim with click-to-close, ESC handler, and focus restoration. It manages all its own internal state (phone selection, target mode, etc.) and exposes a single `onSubmit({ payload })` callback.

- [ ] **Step 1: Create the component skeleton**

Create `frontend/src/components/CommentModal.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { api, type CommentTarget, type EligiblePhone } from '../api'
import { Button } from './ui'
import { cn } from '../lib/cn'

type Mode = 'owned' | 'manual'

export type CommentSubmitPayload = {
  account_ids: number[]
  target_links: string[]
  target_publication_ids: number[]
  comments_per_video: number
  product_info: string | null
  comment_brief: string | null
}

export function CommentModal({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (payload: CommentSubmitPayload) => Promise<void>
}) {
  const { t } = useTranslation()
  const cardRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLElement | null>(null)

  const [phones, setPhones] = useState<EligiblePhone[]>([])
  const [targets, setTargets] = useState<CommentTarget[]>([])
  const [selectedPhones, setSelectedPhones] = useState<Set<number>>(new Set())
  const [selectedTargets, setSelectedTargets] = useState<Set<number>>(new Set())
  const [mode, setMode] = useState<Mode>('owned')
  const [manualLinks, setManualLinks] = useState('')
  const [perVideo, setPerVideo] = useState(3)
  const [productInfo, setProductInfo] = useState('')
  const [commentBrief, setCommentBrief] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) return
    triggerRef.current = (document.activeElement as HTMLElement) ?? null
    Promise.all([api.eligiblePhones(), api.engagementTargets()])
      .then(([p, t]) => {
        setPhones(p.items)
        setTargets(t.items)
        setSelectedPhones(new Set(p.items.filter((x) => !x.busy).map((x) => x.id)))
        setSelectedTargets(new Set())
      })
      .catch(() => {})
    return () => {
      const trigger = triggerRef.current
      if (trigger && typeof trigger.focus === 'function') trigger.focus()
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const original = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = original
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const manualLinkList = useMemo(
    () =>
      manualLinks
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    [manualLinks],
  )

  const targetCount = mode === 'owned' ? selectedTargets.size : manualLinkList.length
  const phoneCount = selectedPhones.size
  const totalRuns = targetCount * perVideo
  const reuseHint = perVideo > phoneCount && phoneCount > 0

  const canSubmit =
    !submitting && phoneCount > 0 && targetCount > 0 && perVideo >= 1 && perVideo <= 20

  const togglePhone = (id: number) =>
    setSelectedPhones((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const toggleTarget = (id: number) =>
    setSelectedTargets((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      await onSubmit({
        account_ids: [...selectedPhones],
        target_links: mode === 'manual' ? manualLinkList : [],
        target_publication_ids:
          mode === 'owned' ? [...selectedTargets] : [],
        comments_per_video: perVideo,
        product_info: productInfo.trim() || null,
        comment_brief: commentBrief.trim() || null,
      })
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-[rgba(0,0,0,0.34)] backdrop-blur-[4px] modal-scrim-in"
      onClick={onClose}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-label={t('comments.modalTitle')}
        className="w-[560px] max-w-[92vw] max-h-[86vh] flex flex-col bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[var(--radius-lg)] shadow-[0_24px_48px_rgba(0,0,0,0.18)] overflow-hidden modal-card-in"
        onClick={(e) => e.stopPropagation()}
      >
        <Header onClose={onClose} title={t('comments.modalTitle')} />
        <div className="flex-1 overflow-y-auto px-[22px] py-[18px]">
          <PhonesField
            phones={phones}
            selected={selectedPhones}
            onToggle={togglePhone}
            onSelectAll={() =>
              setSelectedPhones(new Set(phones.map((p) => p.id)))
            }
            onClear={() => setSelectedPhones(new Set())}
          />
          <TargetsField
            mode={mode}
            onMode={setMode}
            targets={targets}
            selected={selectedTargets}
            onToggle={toggleTarget}
            manualLinks={manualLinks}
            onManualLinks={setManualLinks}
          />
          <PerVideoField
            value={perVideo}
            onValue={setPerVideo}
            reuseHint={reuseHint}
          />
          <TextareaField
            label={t('comments.productInfo')}
            optional
            value={productInfo}
            onValue={setProductInfo}
            placeholder={t('comments.productInfoPlaceholder')}
          />
          <TextareaField
            label={t('comments.commentBrief')}
            optional
            value={commentBrief}
            onValue={setCommentBrief}
            placeholder={t('comments.commentBriefPlaceholder')}
            rows={3}
          />
        </div>
        <Footer
          estimate={t('comments.estimate', {
            count: totalRuns,
            videos: targetCount,
            perVideo,
          })}
          onCancel={onClose}
          onSubmit={submit}
          submitting={submitting}
          canSubmit={canSubmit}
        />
      </div>
    </div>,
    document.body,
  )
}

function Header({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <div className="px-[22px] py-[18px] border-b border-[var(--color-line)] flex items-start justify-between gap-3">
      <div className="text-[15.5px] font-semibold text-[var(--color-ink)]">{title}</div>
      <button
        type="button"
        onClick={onClose}
        className="w-7 h-7 rounded-full text-[var(--color-ink-3)] hover:bg-[var(--color-gray-bg)] flex items-center justify-center"
        aria-label="close"
      >
        ✕
      </button>
    </div>
  )
}

function PhonesField({
  phones,
  selected,
  onToggle,
  onSelectAll,
  onClear,
}: {
  phones: EligiblePhone[]
  selected: Set<number>
  onToggle: (id: number) => void
  onSelectAll: () => void
  onClear: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="mb-[18px]">
      <div className="flex items-baseline justify-between mb-1.5">
        <label className="text-[12px] font-medium text-[var(--color-ink-2)]">
          {t('comments.availablePhones')}
        </label>
        <div className="flex items-center gap-1.5 text-[11.5px]">
          <button type="button" className="text-[var(--color-blue)] hover:underline" onClick={onSelectAll}>
            {t('comments.selectAll')}
          </button>
          <span className="text-[var(--color-ink-5)]">·</span>
          <button type="button" className="text-[var(--color-blue)] hover:underline" onClick={onClear}>
            {t('comments.clearSelection')}
          </button>
        </div>
      </div>
      <div className="border border-[var(--color-line)] rounded-[10px] max-h-[200px] overflow-y-auto">
        {phones.map((p) => (
          <PhoneRow key={p.id} phone={p} checked={selected.has(p.id)} onToggle={() => onToggle(p.id)} />
        ))}
      </div>
      <div className="mt-1.5 text-[11.5px] text-[var(--color-ink-4)]">
        {t('comments.selectedCount', { selected: selected.size, total: phones.length })}
      </div>
    </div>
  )
}

function PhoneRow({
  phone,
  checked,
  onToggle,
}: {
  phone: EligiblePhone
  checked: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  return (
    <label className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 px-3 py-2.5 border-b border-[var(--color-line)] last:border-b-0 text-[13px] cursor-pointer hover:bg-[var(--color-surface-hover)]">
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="accent-[var(--color-blue)] w-[14px] h-[14px]"
      />
      <div className="min-w-0">
        <div className="font-medium text-[var(--color-ink)] truncate">{phone.profile_name ?? `#${phone.id}`}</div>
        <div className="text-[11.5px] text-[var(--color-ink-4)] truncate">{phone.group_name ?? '—'}</div>
      </div>
      <div className={cn('text-[12px]', phone.busy ? 'text-[var(--color-orange)]' : 'text-[var(--color-ink-4)]')}>
        {phone.busy ? t('comments.busy') : t('comments.free')}
      </div>
    </label>
  )
}

function TargetsField({
  mode,
  onMode,
  targets,
  selected,
  onToggle,
  manualLinks,
  onManualLinks,
}: {
  mode: Mode
  onMode: (m: Mode) => void
  targets: CommentTarget[]
  selected: Set<number>
  onToggle: (id: number) => void
  manualLinks: string
  onManualLinks: (v: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="mb-[18px]">
      <label className="block text-[12px] font-medium text-[var(--color-ink-2)] mb-1.5">
        {t('comments.filterSource')}
      </label>
      <Segmented
        value={mode}
        options={[
          { value: 'owned', label: t('comments.ownedMode') },
          { value: 'manual', label: t('comments.manualMode') },
        ]}
        onChange={(v) => onMode(v as Mode)}
      />
      <div className="mt-2.5">
        {mode === 'manual' ? (
          <textarea
            rows={6}
            value={manualLinks}
            onChange={(e) => onManualLinks(e.target.value)}
            placeholder={t('comments.linksPlaceholder')}
            className="w-full min-h-[120px] resize-y bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-5)] focus:border-[var(--color-blue)] focus:ring-2 focus:ring-[var(--color-blue-bg)] focus:outline-none"
          />
        ) : targets.length === 0 ? (
          <div className="text-center py-8 text-[13px] text-[var(--color-ink-3)] border border-dashed border-[var(--color-line)] rounded-[10px]">
            <div className="font-medium text-[var(--color-ink-2)] mb-1">{t('comments.ownedEmptyTitle')}</div>
            <div>{t('comments.ownedEmptyBody')}</div>
          </div>
        ) : (
          <div className="border border-[var(--color-line)] rounded-[10px] max-h-[200px] overflow-y-auto">
            {targets.map((tg) => (
              <label
                key={tg.publication_id}
                className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 px-3 py-2.5 border-b border-[var(--color-line)] last:border-b-0 text-[13px] cursor-pointer hover:bg-[var(--color-surface-hover)]"
              >
                <input
                  type="checkbox"
                  checked={selected.has(tg.publication_id)}
                  onChange={() => onToggle(tg.publication_id)}
                  className="accent-[var(--color-blue)] w-[14px] h-[14px]"
                />
                <div className="min-w-0">
                  <div className="font-medium text-[var(--color-ink)] truncate">{tg.video_filename ?? `#${tg.video_id}`}</div>
                  <div className="text-[11.5px] text-[var(--color-ink-4)] truncate">{tg.account_profile_name ?? '—'}</div>
                </div>
              </label>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function PerVideoField({
  value,
  onValue,
  reuseHint,
}: {
  value: number
  onValue: (v: number) => void
  reuseHint: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="mb-[18px]">
      <label className="block text-[12px] font-medium text-[var(--color-ink-2)] mb-1.5">
        {t('comments.commentsPerVideo')}
      </label>
      <input
        type="number"
        min={1}
        max={20}
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value)
          onValue(Number.isFinite(n) ? Math.max(1, Math.min(20, n)) : 1)
        }}
        className="w-[120px] bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[10px] px-3 py-2 text-[14px] text-[var(--color-ink)] focus:border-[var(--color-blue)] focus:ring-2 focus:ring-[var(--color-blue-bg)] focus:outline-none"
      />
      <div className="mt-1.5 text-[11.5px] text-[var(--color-ink-4)]">{t('comments.commentsPerVideoHelp')}</div>
      {reuseHint ? (
        <div className="mt-1 text-[11.5px] text-[var(--color-orange)]">{t('comments.phoneReuseHint')}</div>
      ) : null}
    </div>
  )
}

function TextareaField({
  label,
  optional,
  value,
  onValue,
  placeholder,
  rows = 4,
}: {
  label: string
  optional?: boolean
  value: string
  onValue: (v: string) => void
  placeholder: string
  rows?: number
}) {
  const { t } = useTranslation()
  return (
    <div className="mb-[18px]">
      <label className="block text-[12px] font-medium text-[var(--color-ink-2)] mb-1.5">
        {label}
        {optional ? (
          <span className="text-[var(--color-ink-4)] font-normal ml-1">{t('comments.productInfoOptional')}</span>
        ) : null}
      </label>
      <textarea
        rows={rows}
        value={value}
        onChange={(e) => onValue(e.target.value)}
        placeholder={placeholder}
        className="w-full resize-y bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-5)] focus:border-[var(--color-blue)] focus:ring-2 focus:ring-[var(--color-blue-bg)] focus:outline-none"
      />
    </div>
  )
}

function Segmented({
  value,
  options,
  onChange,
}: {
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (v: string) => void
}) {
  return (
    <div className="inline-flex bg-[var(--color-gray-bg)] p-0.5 rounded-[8px] gap-0">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            'px-3 py-1 text-[12.5px] font-medium rounded-[6px]',
            value === opt.value
              ? 'bg-[var(--color-surface)] text-[var(--color-ink)] shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
              : 'text-[var(--color-ink-3)]',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

function Footer({
  estimate,
  onCancel,
  onSubmit,
  submitting,
  canSubmit,
}: {
  estimate: ReactNode
  onCancel: () => void
  onSubmit: () => void
  submitting: boolean
  canSubmit: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="px-[22px] py-3.5 border-t border-[var(--color-line)] bg-[var(--color-surface-hover)] flex justify-between items-center gap-2.5">
      <div className="text-[12px] text-[var(--color-ink-3)]">{estimate}</div>
      <div className="flex gap-2">
        <Button type="button" variant="secondary" onClick={onCancel}>
          {t('comments.cancel')}
        </Button>
        <Button type="button" variant="primary" onClick={onSubmit} disabled={!canSubmit}>
          {submitting ? t('comments.starting') : t('comments.start')}
        </Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add modal animations to `index.css`**

In `frontend/src/index.css`, after the `@theme {}` block, add:

```css
@keyframes modal-scrim-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}
@keyframes modal-card-in {
  from { opacity: 0; transform: translateY(8px) scale(0.985); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
.modal-scrim-in { animation: modal-scrim-in 150ms ease-out; }
.modal-card-in  { animation: modal-card-in 180ms cubic-bezier(0.18, 0.74, 0.46, 1); }
```

- [ ] **Step 3: Build the frontend to surface type errors early**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build`
Expected: builds successfully with no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/CommentModal.tsx frontend/src/index.css
git commit -m "feat(ui): CommentModal — phone multi-select, custom centered modal"
```

---

## Task 10: Frontend — rewrite `Comments.tsx`

**Files:**
- Modify: `frontend/src/pages/Comments.tsx`

- [ ] **Step 1: Replace the file with the new implementation**

Replace the entire contents of `frontend/src/pages/Comments.tsx` with:

```tsx
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  api,
  type EngagementRun,
  type EngagementRunResult,
  type EngagementTaskStatus,
} from '../api'
import type { ToastState } from '../App'
import { Button, Card, EmptyState, PageTitle } from '../components/ui'
import { CommentModal, type CommentSubmitPayload } from '../components/CommentModal'
import { cn } from '../lib/cn'

type StatusKey = 'all' | 'success' | 'running' | 'queued' | 'failed'
type SourceKey = 'all' | 'owned' | 'manual'

function parseBackendIso(value: string | null | undefined) {
  if (!value) return null
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(value)
  const d = new Date(hasTz ? value : `${value}Z`)
  return Number.isFinite(d.getTime()) ? d : null
}

function relativeTime(value: string | null | undefined) {
  const d = parseBackendIso(value)
  if (!d) return '—'
  const diffMs = Date.now() - d.getTime()
  const m = Math.round(diffMs / 60_000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} 小时前`
  const days = Math.round(h / 24)
  return `${days} 天前`
}

function isInflight(record: EngagementRun) {
  const runInflight: EngagementRunResult[] = ['PENDING', 'QUEUED', 'INITIATED']
  const taskInflight: EngagementTaskStatus[] = ['PENDING', 'QUEUED', 'RUNNING']
  return (
    runInflight.includes(record.result) ||
    (record.task_status != null && taskInflight.includes(record.task_status))
  )
}

function classifyStatus(record: EngagementRun): {
  key: StatusKey
  accent: 'green' | 'blue' | 'orange' | 'red' | 'gray'
  labelKey: string
  spin: boolean
} {
  if (record.result === 'SUCCESS') return { key: 'success', accent: 'green', labelKey: 'comments.statusSuccess', spin: false }
  if (record.result === 'FAILURE') return { key: 'failed', accent: 'red', labelKey: 'comments.statusFailed', spin: false }
  if (record.result === 'INITIATED' || record.task_status === 'RUNNING')
    return { key: 'running', accent: 'blue', labelKey: 'comments.statusRunning', spin: true }
  if (record.result === 'QUEUED') return { key: 'queued', accent: 'orange', labelKey: 'comments.statusQueued', spin: false }
  if (record.result === 'PENDING') return { key: 'queued', accent: 'orange', labelKey: 'comments.statusPending', spin: false }
  return { key: 'queued', accent: 'gray', labelKey: 'comments.statusSkipped', spin: false }
}

function sourceOf(record: EngagementRun): SourceKey {
  return record.task_source === 'publication_share_links' ? 'owned' : 'manual'
}

const ACCENT_BAR: Record<string, string> = {
  green: 'before:bg-[var(--color-green)]',
  blue: 'before:bg-[var(--color-blue)]',
  orange: 'before:bg-[var(--color-orange)]',
  red: 'before:bg-[var(--color-red)]',
  gray: 'before:bg-[var(--color-ink-5)]',
}
const STATUS_TEXT: Record<string, string> = {
  green: 'text-[var(--color-green)]',
  blue: 'text-[var(--color-blue)]',
  orange: 'text-[var(--color-orange)]',
  red: 'text-[var(--color-red)]',
  gray: 'text-[var(--color-ink-4)]',
}
const DOT_BG: Record<string, string> = {
  green: 'bg-[var(--color-green)]',
  blue: 'bg-[var(--color-blue)]',
  orange: 'bg-[var(--color-orange)]',
  red: 'bg-[var(--color-red)]',
  gray: 'bg-[var(--color-ink-5)]',
}

export default function Comments({ onToast }: { onToast: (t: ToastState) => void }) {
  const { t } = useTranslation()
  const [records, setRecords] = useState<EngagementRun[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<StatusKey>('all')
  const [sourceFilter, setSourceFilter] = useState<SourceKey>('all')
  const [modalOpen, setModalOpen] = useState(false)

  const reload = async () => {
    try {
      const r = await api.engagementRuns()
      setRecords(r.items)
    } catch {
      // surfaced via toast on next user action
    }
  }

  useEffect(() => {
    setLoading(true)
    reload().finally(() => setLoading(false))
  }, [])

  const hasInflight = useMemo(() => records.some(isInflight), [records])
  useEffect(() => {
    if (!hasInflight) return undefined
    const timer = window.setInterval(() => reload(), 5000)
    return () => window.clearInterval(timer)
  }, [hasInflight])

  const filtered = useMemo(
    () =>
      records.filter((r) => {
        if (statusFilter !== 'all' && classifyStatus(r).key !== statusFilter) return false
        if (sourceFilter !== 'all' && sourceOf(r) !== sourceFilter) return false
        return true
      }),
    [records, statusFilter, sourceFilter],
  )

  const onSubmit = async (payload: CommentSubmitPayload) => {
    try {
      const result = await api.createEngagementComment(payload)
      onToast({
        msg: t('comments.created', { count: result.created_runs ?? 0 }),
        tone: 'green',
      })
      setModalOpen(false)
      await reload()
    } catch (e) {
      const msg = e instanceof Error && e.message ? e.message : t('comments.failed')
      onToast({ msg, tone: 'red' })
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageTitle
        title={t('comments.title')}
        right={
          <Button variant="primary" onClick={() => setModalOpen(true)}>
            + {t('comments.newComment')}
          </Button>
        }
      />

      <div className="flex items-center gap-2.5">
        <FilterMenu
          label={t('comments.filterStatus')}
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as StatusKey)}
          options={[
            { value: 'all', label: t('comments.filterAll') },
            { value: 'success', label: t('comments.filterSuccess') },
            { value: 'running', label: t('comments.filterRunning') },
            { value: 'queued', label: t('comments.filterQueued') },
            { value: 'failed', label: t('comments.filterFailed') },
          ]}
        />
        <FilterMenu
          label={t('comments.filterSource')}
          value={sourceFilter}
          onChange={(v) => setSourceFilter(v as SourceKey)}
          options={[
            { value: 'all', label: t('comments.filterAll') },
            { value: 'owned', label: t('comments.filterOwned') },
            { value: 'manual', label: t('comments.filterManual') },
          ]}
        />
        <div className="flex-1" />
        <span className="text-[12px] text-[var(--color-ink-3)] inline-flex items-center gap-1.5">
          <span
            className={cn(
              'w-[7px] h-[7px] rounded-full',
              hasInflight ? 'bg-[var(--color-green)] animate-pulse' : 'bg-[var(--color-ink-5)]',
            )}
          />
          {t('comments.autoRefresh')}
        </span>
      </div>

      {loading ? (
        <Card><EmptyState title={t('common.loading')} /></Card>
      ) : filtered.length === 0 ? (
        <Card><EmptyState title={t('comments.recordsEmpty')} /></Card>
      ) : (
        <div className="flex flex-col gap-2.5">
          {filtered.map((r) => (
            <RecordCard key={r.id} record={r} />
          ))}
        </div>
      )}

      <CommentModal open={modalOpen} onClose={() => setModalOpen(false)} onSubmit={onSubmit} />
    </div>
  )
}

function FilterMenu({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (v: string) => void
}) {
  return (
    <label className="inline-flex items-center gap-1.5 h-8 px-3 border border-[var(--color-line)] rounded-[8px] bg-[var(--color-surface)] text-[13px] text-[var(--color-ink-2)]">
      <span className="text-[var(--color-ink-4)]">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-[var(--color-ink)] font-medium focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  )
}

function RecordCard({ record }: { record: EngagementRun }) {
  const { t } = useTranslation()
  const stat = classifyStatus(record)
  const isFail = stat.key === 'failed'
  const isManual = sourceOf(record) === 'manual'
  return (
    <article
      className={cn(
        'relative overflow-hidden bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[var(--radius-lg)]',
        'shadow-[0_1px_2px_rgba(0,0,0,0.03)] px-[26px] pt-[22px] pb-[18px]',
        'before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[3px]',
        ACCENT_BAR[stat.accent],
      )}
    >
      {!isFail && record.comment_text ? (
        <div className="font-[family-name:var(--font-serif)] text-[18px] leading-[1.45] tracking-[-0.005em] text-[var(--color-ink)] max-w-[65ch]">
          <span className="text-[var(--color-ink-5)]">“</span>
          {record.comment_text}
          <span className="text-[var(--color-ink-5)]">”</span>
        </div>
      ) : isFail ? (
        <div className="italic text-[14px] text-[var(--color-ink-4)]">
          {record.notes ?? t('comments.statusFailed')}
        </div>
      ) : (
        <div className="italic text-[14px] text-[var(--color-ink-4)]">
          {t('comments.statusPending')}…
        </div>
      )}

      <div className="h-px bg-[var(--color-line)] mt-4 mb-3" />

      <div className="flex items-center gap-2 text-[12.5px] text-[var(--color-ink-3)]">
        <span className={cn('inline-flex items-center gap-1.5 font-medium', STATUS_TEXT[stat.accent])}>
          <span className={cn('w-[7px] h-[7px] rounded-full', DOT_BG[stat.accent], stat.spin && 'animate-pulse')} />
          {t(stat.labelKey)}
        </span>
        <span className="text-[var(--color-ink-5)]">·</span>
        <span className="font-medium text-[var(--color-ink-2)]">
          {record.account_profile_name ?? `#${record.account_id ?? '—'}`}
        </span>
        <span className="text-[var(--color-ink-5)]">·</span>
        <span className="text-[var(--color-ink-4)]">{relativeTime(record.created_at ?? record.task_created_at)}</span>
      </div>

      <div className="mt-2.5 flex items-baseline gap-2.5 text-[13px] min-w-0">
        {!isManual && record.target_title ? (
          <span className="text-[var(--color-ink-2)] font-medium truncate max-w-[55%]">{record.target_title}</span>
        ) : null}
        <a
          href={record.target_link}
          target="_blank"
          rel="noreferrer"
          className="flex-1 min-w-0 text-[12.5px] text-[var(--color-blue)] truncate hover:underline"
        >
          {record.target_link}
        </a>
        <a
          href={record.target_link}
          target="_blank"
          rel="noreferrer"
          className="flex-shrink-0 text-[var(--color-ink-4)] hover:text-[var(--color-blue)] hover:bg-[var(--color-blue-bg)] rounded-[6px] px-1.5 py-0.5 text-[13px]"
          aria-label="open"
        >
          ↗
        </a>
      </div>
    </article>
  )
}
```

- [ ] **Step 2: Build to verify**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Comments.tsx
git commit -m "feat(ui): rewrite /comments page — hero-quote cards, custom modal entry"
```

---

## Task 11: Verify the new page in preview

**Files:** none — preview-only

- [ ] **Step 1: Start the existing dev server (uvicorn) via the preview tool**

Use the `geelark-studio` launch config (`.claude/launch.json` already has it). The web tier serves both API and the built frontend.

- [ ] **Step 2: Rebuild the frontend so served bundle reflects current source**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build`
Expected: builds.

- [ ] **Step 3: Open /comments in the preview and screenshot**

Use the Claude Preview tools to navigate to `/comments` and take a screenshot. Visual checklist:
- Page header shows "TikTok 评论" + `+ 新建评论` button on the right.
- No subtitle anywhere.
- Filter row has only 状态 / 来源 (no 分组).
- Existing engagement run records render as hero-quote cards (serif font, status accent strip).
- Click `+ 新建评论` opens the centered modal (scrim + card animation).
- Modal shows: 可用手机 list, 目标视频 segmented + list, 每个视频评论数 (default 3), 产品覆盖, 评论要求.
- Footer estimate updates as you toggle phones/targets and change perVideo.

- [ ] **Step 4: Smoke-submit a minimal real batch (only on local dev DB, not prod)**

Pick 1 phone, 1 target, perVideo=1. Click 开始评论. Expect: toast `已排队 1 条评论`, modal closes, list shows the new row with status `等待派发` or `排队中`.

- [ ] **Step 5: Commit any small fixes you find**

If you discover layout / wording issues during preview, fix them and commit:

```bash
git add <files>
git commit -m "fix(ui): <what you fixed in preview>"
```

---

## Task 12: Run the full test suite

**Files:** none

- [ ] **Step 1: Run pytest**

Run: `uv run pytest -q`
Expected: total passed count ≥ 388 (385 baseline + new tests added by this plan), 0 failed.

If anything fails that was previously passing, investigate and fix. Most likely cause if there are regressions: an existing `create_comment_batch` test still passes `group_id=X` without `account_ids` — update those tests to pass `account_ids` explicitly.

---

## Task 13: Ship to production

**Files:** none

- [ ] **Step 1: Confirm the tree is clean**

Run: `git status -s`
Expected: empty output.

- [ ] **Step 2: Follow the deploy runbook**

Open `docs/production-deploy.md` and follow the tarball + restart steps. The migration step (`alembic upgrade head`) runs the new `comments_per_video` migration.

After the systemd restart, restart `mkt-worker` too (per the `deploy-restart-worker` memory).

- [ ] **Step 3: Smoke test on production**

Open `http://139.196.52.92:8080/comments`. Verify:
- Page renders the new layout.
- `+ 新建评论` opens the centered modal.
- 可用手机 list shows real phones.
- Existing engagement runs render correctly.

If anything broken: roll back per runbook (revert the new commits, redeploy old tarball — schema column is additive, safe to keep).

---

## Self-Review

**Spec coverage:**
- Page structure (header / filter / cards) → Tasks 10.
- Record card 6 fields + states → Task 10.
- Custom Modal (not Drawer, not <dialog>) → Task 9.
- Phone multi-select cross-group with busy flag → Tasks 4 + 9.
- `comments_per_video` payload + schema → Tasks 1 + 2 + 5.
- Phone reuse semantics → Task 3 (with explicit test).
- `comment_mode` / `comment_probability` defaulted, not in UI → Task 3 (service signature) + Task 5 (API).
- Eligible-phones endpoint → Task 4.
- `--font-serif` token → Task 6.
- i18n key surgery → Task 8.
- Drop "engagement task" UI concept → Task 10 (no UI reads `engagement_task_id`).
- Pytest tests for new dispatch path → Task 3 (3 cases) + Task 4 (2 cases).
- Deploy via tarball + alembic upgrade head → Task 13.

**Placeholder scan:** No TBD / TODO / "appropriate" / "similar to". All code blocks are complete.

**Type consistency:** `CommentSubmitPayload` defined in Task 9 and consumed in Task 10. `EligiblePhone` defined in Task 7 and consumed in Tasks 4 (backend shape matches) + 9. `comments_per_video` is consistent (`int`, 1-20) across Tasks 1, 2, 3, 5, 7, 9, 10.
