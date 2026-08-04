# Facebook 自动评论 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 engagement 流水线上扩展 Facebook 自动评论，端到端覆盖 GeeLark client / service / API / 前端 / 测试 / 部署。

**Architecture:** 复用 `engagement_tasks` / `engagement_runs` 表，加 `platform` / `keywords` / `comment_candidates` 三列；service 按 `task.action` 分派 GeeLark 调用；独立 `/api/engagement/comment/facebook` 端点 + 独立 `FbComments` UI 页；keyword 黑盒透传。

**Tech Stack:** FastAPI · SQLModel · Alembic · Procrastinate · PostgreSQL 16 · React 19 · Vite · TypeScript · react-i18next · pytest · GeeLark Open API

**Spec:** [docs/superpowers/specs/2026-06-16-facebook-comment-design.md](../specs/2026-06-16-facebook-comment-design.md)

---

## Task 1: Alembic migration + 模型更新

**Files:**
- Create: `alembic/versions/a1b2c3d4e5f6_engagement_platform_keywords.py`
- Modify: `app/models.py:249-340` (EngagementTask + EngagementRun)
- Test: `tests/test_engagement_models_columns.py` (new)

- [ ] **Step 1: 写失败测试 — 验证新列存在 & 默认行为**

```python
# tests/test_engagement_models_columns.py
from sqlalchemy import text
from app.db import engine


def test_engagement_tasks_has_platform_and_keywords():
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='engagement_tasks'"
            )).all()
        }
    assert "platform" in cols
    assert "keywords" in cols


def test_engagement_runs_has_comment_candidates():
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='engagement_runs'"
            )).all()
        }
    assert "comment_candidates" in cols


def test_engagement_tasks_platform_default_is_tiktok():
    from app.repositories import engagement as eng_repo
    from sqlmodel import Session
    with Session(engine) as s:
        tid = eng_repo.create_task(
            s, group_id=None, action="tiktok_ai_comment",
            source="manual_links", target_links=["x"],
            target_publication_ids=[], comment_probability=100,
            comments_per_video=1,
        )
        task = eng_repo.get_task(s, tid)
    assert task["platform"] == "tiktok"
    assert task["keywords"] == []
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_engagement_models_columns.py -v`
Expected: FAIL — `column engagement_tasks.platform does not exist` 或 KeyError on `task["platform"]`

- [ ] **Step 3: 写 Alembic migration**

```python
# alembic/versions/a1b2c3d4e5f6_engagement_platform_keywords.py
"""engagement platform / keywords / comment_candidates

Revision ID: a1b2c3d4e5f6
Revises: e2f3c4d5b9a8
Create Date: 2026-06-16 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e2f3c4d5b9a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "platform",
            sa.String(),
            nullable=False,
            server_default="tiktok",
        ),
    )
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "keywords",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.create_index(
        "idx_engagement_tasks_platform_status",
        "engagement_tasks",
        ["platform", "status"],
    )
    op.add_column(
        "engagement_runs",
        sa.Column(
            "comment_candidates",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_engagement_tasks_platform_status",
        table_name="engagement_tasks",
    )
    op.drop_column("engagement_runs", "comment_candidates")
    op.drop_column("engagement_tasks", "keywords")
    op.drop_column("engagement_tasks", "platform")
```

- [ ] **Step 4: 更新 SQLModel 模型**

在 `app/models.py` 的 `EngagementTask` 类（行 249 附近）插入：

```python
    platform: str = Field(
        default="tiktok",
        nullable=False,
        sa_column_kwargs={"server_default": "tiktok"},
    )
    keywords: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            JSON, nullable=False, server_default=text("'[]'::json"),
        ),
    )
```

在 `EngagementRun` 类（行 296 附近）插入：

```python
    comment_candidates: Optional[list[str]] = Field(
        default=None, sa_column=Column(JSON, nullable=True),
    )
```

注意：保持现有 `Field(...)` 风格 + import 已有的 `Column`, `JSON`, `text`。

- [ ] **Step 5: 升级测试 DB 并跑测试**

```bash
DATABASE_URL="postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test" uv run alembic upgrade head
uv run pytest tests/test_engagement_models_columns.py -v
```

Expected: 3 PASS

- [ ] **Step 6: 跑全量 engagement 旧测试，确认不回归**

Run: `uv run pytest tests/test_engagement_service.py tests/test_engagement_lease.py tests/test_api_engagement.py -q`
Expected: 全 PASS（新列对旧代码透明）

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/a1b2c3d4e5f6_engagement_platform_keywords.py \
        app/models.py tests/test_engagement_models_columns.py
git commit -m "feat(engagement): add platform/keywords/comment_candidates columns"
```

---

## Task 2: engagement_repo 接受 platform / keywords / comment_candidates

**Files:**
- Modify: `app/repositories/engagement.py:32-55` (`create_task`)
- Modify: `app/repositories/engagement.py:117-155` (`list_runs`, `list_tasks`)
- Add: `set_comment_candidates` 函数
- Test: `tests/test_engagement_repo.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engagement_repo.py
import pytest
from sqlmodel import Session
from app.db import engine
from app.repositories import engagement as eng_repo


def test_create_task_writes_platform_and_keywords():
    with Session(engine) as s:
        tid = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["x"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=3, platform="facebook",
            keywords=["k1", "k2"],
        )
        task = eng_repo.get_task(s, tid)
    assert task["platform"] == "facebook"
    assert task["keywords"] == ["k1", "k2"]


def test_list_tasks_filters_by_platform():
    with Session(engine) as s:
        tk_id = eng_repo.create_task(
            s, group_id=None, action="tiktok_ai_comment",
            source="manual_links", target_links=["a"],
            target_publication_ids=[], comment_probability=100,
            comments_per_video=1,
        )
        fb_id = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["b"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=2, platform="facebook",
            keywords=["x"],
        )
        fb_tasks = eng_repo.list_tasks(s, platform="facebook")
        tk_tasks = eng_repo.list_tasks(s, platform="tiktok")
    assert {t["id"] for t in fb_tasks} == {fb_id}
    assert {t["id"] for t in tk_tasks} == {tk_id}


def test_set_comment_candidates_writes_pool():
    with Session(engine) as s:
        tid = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["x"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=2, platform="facebook",
            keywords=["k1"],
        )
        rid = eng_repo.create_run(
            s, engagement_task_id=tid, account_id=None,
            target_link="x", action="facebook_auto_comment",
        )
        eng_repo.set_comment_candidates(
            s, rid, candidates=["c1", "c2"], source="studio_ai",
        )
        run = eng_repo.get_run(s, rid)
    assert run["comment_candidates"] == ["c1", "c2"]
    assert run["comment_text"] == "c1"  # 第一条作展示用
    assert run["comment_source"] == "studio_ai"


def test_list_runs_filters_by_platform():
    with Session(engine) as s:
        fb_task = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["fb-target"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=1, platform="facebook", keywords=["k"],
        )
        tk_task = eng_repo.create_task(
            s, group_id=None, action="tiktok_ai_comment",
            source="manual_links", target_links=["tk-target"],
            target_publication_ids=[], comment_probability=100,
            comments_per_video=1,
        )
        fb_run = eng_repo.create_run(
            s, engagement_task_id=fb_task, account_id=None,
            target_link="fb-target", action="facebook_auto_comment",
        )
        tk_run = eng_repo.create_run(
            s, engagement_task_id=tk_task, account_id=None,
            target_link="tk-target", action="tiktok_ai_comment",
        )
        fb_runs = eng_repo.list_runs(s, platform="facebook")
        tk_runs = eng_repo.list_runs(s, platform="tiktok")
    assert {r["id"] for r in fb_runs} == {fb_run}
    assert {r["id"] for r in tk_runs} == {tk_run}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_engagement_repo.py -v`
Expected: FAIL (`create_task() got an unexpected keyword argument 'platform'` 等)

- [ ] **Step 3: 改 engagement_repo.py**

修改 `create_task` 签名 + 实现：

```python
def create_task(session: Session, *, group_id: Optional[int], action: str,
                source: str, target_links: list[str],
                target_publication_ids: list[int],
                comment_probability: int,
                comment_mode: str = "geelark_ai",
                comment_brief: Optional[str] = None,
                comment_prompt_id: Optional[int] = None,
                comments_per_video: int = 1,
                platform: str = "tiktok",
                keywords: Optional[list[str]] = None) -> int:
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
        platform=platform,
        keywords=keywords or [],
    )
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id
```

修改 `list_tasks` 签名加 `platform`：

```python
def list_tasks(session: Session, *, group_id: Optional[int] = None,
               platform: Optional[str] = None,
               limit: int = 50) -> list[dict]:
    stmt = select(EngagementTask).order_by(EngagementTask.id.desc())
    if group_id is not None:
        stmt = stmt.where(EngagementTask.group_id == group_id)
    if platform is not None:
        stmt = stmt.where(EngagementTask.platform == platform)
    stmt = stmt.limit(max(1, min(limit, 200)))
    return [_dump_task(row) for row in session.exec(stmt).all()]
```

修改 `list_runs` 签名加 `platform`，在 join 后加 `.where(EngagementTask.platform == platform)`：

```python
def list_runs(session: Session, *, group_id: Optional[int] = None,
              limit: int = 100,
              before_id: Optional[int] = None,
              status: Optional[str] = None,
              source: Optional[str] = None,
              platform: Optional[str] = None) -> list[dict]:
    stmt = (
        select(EngagementRun, EngagementTask, Account.profile_name)
        .join(EngagementTask, EngagementTask.id == EngagementRun.engagement_task_id)
        .outerjoin(Account, Account.id == EngagementRun.account_id)
        .order_by(EngagementRun.id.desc())
    )
    if group_id is not None:
        stmt = stmt.where(EngagementTask.group_id == group_id)
    if platform is not None:
        stmt = stmt.where(EngagementTask.platform == platform)
    # ... 余下原代码不动
```

新增 `set_comment_candidates` 函数（紧跟现有 `set_generated_comment`）：

```python
def set_comment_candidates(session: Session, run_id: int, *,
                           candidates: list[str],
                           source: str) -> None:
    """Write FB candidate pool; comment_text shows the first one."""
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.comment_candidates = list(candidates)
    run.comment_text = candidates[0] if candidates else None
    run.comment_source = source
    run.comment_generated_at = datetime.utcnow()
    session.add(run)
    session.commit()
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_engagement_repo.py -v`
Expected: 4 PASS

- [ ] **Step 5: 跑既有 repo / service / API 测试不回归**

Run: `uv run pytest tests/test_engagement_service.py tests/test_api_engagement.py -q`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add app/repositories/engagement.py tests/test_engagement_repo.py
git commit -m "feat(engagement-repo): accept platform/keywords; add set_comment_candidates"
```

---

## Task 3: eligible_phones repo 按平台参数化

**Files:**
- Modify: `app/repositories/eligible_phones.py`
- Test: `tests/test_eligible_phones_endpoint.py` (扩) 或新增 `tests/test_eligible_phones_facebook.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_eligible_phones_facebook.py
from sqlmodel import Session
from app.db import engine
from app.repositories import accounts as acc_repo
from app.repositories import eligible_phones as eph_repo


def test_eligible_commenters_filters_by_facebook_logged_in(default_group_id):
    with Session(engine) as s:
        a_fb_ok = acc_repo.create(s, {
            "profile_name": "fb-ok",
            "facebook_logged_in": 1,
            "tiktok_logged_in": 0,
            "warmed_up": 1,
            "geelark_phone_id": "ph-fb-1",
            "group_id": default_group_id,
        })
        a_tk_only = acc_repo.create(s, {
            "profile_name": "tk-only",
            "facebook_logged_in": 0,
            "tiktok_logged_in": 1,
            "warmed_up": 1,
            "geelark_phone_id": "ph-tk-1",
            "group_id": default_group_id,
        })
    with Session(engine) as s:
        fb_items = eph_repo.list_eligible_commenters(s, platform="facebook")
        tk_items = eph_repo.list_eligible_commenters(s, platform="tiktok")
    fb_ids = {i["id"] for i in fb_items}
    tk_ids = {i["id"] for i in tk_items}
    assert a_fb_ok in fb_ids and a_tk_only not in fb_ids
    assert a_tk_only in tk_ids and a_fb_ok not in tk_ids


def test_eligible_commenters_default_platform_is_tiktok(default_group_id):
    """向后兼容：不传 platform 时仍按 tiktok 行为。"""
    from sqlmodel import Session
    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "default-tk",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-d-1",
            "group_id": default_group_id,
        })
    with Session(engine) as s:
        items = eph_repo.list_eligible_commenters(s)
    assert a in {i["id"] for i in items}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_eligible_phones_facebook.py -v`
Expected: FAIL (`unexpected keyword argument 'platform'`)

- [ ] **Step 3: 改 eligible_phones.py**

```python
# app/repositories/eligible_phones.py
from sqlmodel import Session, select

from app.models import Account, Group, EngagementRun
from app.repositories.engagement import BUSY_RESULTS
from app.services.platforms import LOGGED_IN_COL, validate as validate_platform


def list_eligible_commenters(session: Session, *,
                             platform: str = "tiktok") -> list[dict]:
    platform = validate_platform(platform)
    logged_in_col = getattr(Account, LOGGED_IN_COL[platform])
    rows = session.exec(
        select(Account, Group.name)
        .join(Group, Group.id == Account.group_id, isouter=True)
        .where(
            logged_in_col == 1,
            Account.warmed_up == 1,
            Account.geelark_phone_id.is_not(None),
            Account.geelark_phone_id != "",
        )
        .order_by(Group.name.nullslast(), Account.profile_name)
    ).all()

    busy_ids = set(
        session.exec(
            select(EngagementRun.account_id).where(
                EngagementRun.result.in_(BUSY_RESULTS),
                EngagementRun.account_id.is_not(None),
            )
        ).all()
    )

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

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_eligible_phones_facebook.py tests/test_eligible_phones_endpoint.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add app/repositories/eligible_phones.py tests/test_eligible_phones_facebook.py
git commit -m "feat(eligible-phones): parameterize by platform"
```

---

## Task 4: publications.list_comment_targets 按平台参数化

**Files:**
- Modify: `app/repositories/publications.py:234-277`
- Test: `tests/test_publications_comment_targets.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_publications_comment_targets.py
from sqlmodel import Session
from app.db import engine
from app.models import Account, Publication, Video
from app.repositories import publications as pub_repo


def _seed_publication(session, *, platform: str, share_link: str):
    v = Video(stored_path="/tmp/x.mp4", original_filename="x.mp4")
    session.add(v); session.commit(); session.refresh(v)
    a = Account(profile_name=f"acc-{platform}",
                geelark_phone_id=f"ph-{platform}")
    session.add(a); session.commit(); session.refresh(a)
    p = Publication(
        video_id=v.id, account_id=a.id,
        platform=platform, result="SUCCESS", share_link=share_link,
    )
    session.add(p); session.commit(); session.refresh(p)
    return p.id


def test_list_comment_targets_returns_only_facebook_when_requested():
    with Session(engine) as s:
        fb_pub = _seed_publication(s, platform="facebook",
                                    share_link="https://www.facebook.com/x/posts/1")
        _seed_publication(s, platform="tiktok",
                          share_link="https://www.tiktok.com/@x/video/1")
    with Session(engine) as s:
        items = pub_repo.list_comment_targets(s, platform="facebook")
    assert [i["publication_id"] for i in items] == [fb_pub]


def test_list_comment_targets_default_platform_is_tiktok():
    with Session(engine) as s:
        _seed_publication(s, platform="facebook",
                          share_link="https://www.facebook.com/x/posts/1")
        tk_pub = _seed_publication(s, platform="tiktok",
                                    share_link="https://www.tiktok.com/@x/video/1")
    with Session(engine) as s:
        items = pub_repo.list_comment_targets(s)
    assert [i["publication_id"] for i in items] == [tk_pub]
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_publications_comment_targets.py -v`
Expected: FAIL — `unexpected keyword argument 'platform'`

- [ ] **Step 3: 改 publications.py**

修改 `list_comment_targets` 签名 + where 子句：

```python
def list_comment_targets(session: Session, *, group_id: int | None = None,
                         platform: str = "tiktok",
                         limit: int = 100) -> list[dict]:
    from app.services.platforms import validate as validate_platform
    platform = validate_platform(platform)
    stmt = (
        select(Publication, Video.original_filename, Video.group_id,
               Account.profile_name)
        .join(Video, Video.id == Publication.video_id)
        .outerjoin(Account, Account.id == Publication.account_id)
        .where(Publication.platform == platform)
        .where(Publication.result == "SUCCESS")
        .where(Publication.share_link.is_not(None))
        .where(sa.func.btrim(Publication.share_link) != "")
        .order_by(
            sa.func.coalesce(
                Publication.posted_at,
                Publication.last_checked_at,
                Publication.created_at,
            ).desc(),
            Publication.id.desc(),
        )
        .limit(max(1, min(int(limit), 200)))
    )
    if group_id is not None:
        stmt = stmt.where(Video.group_id == group_id)

    out = []
    for pub, filename, video_group_id, profile_name in session.exec(stmt).all():
        out.append({
            "publication_id": pub.id,
            "video_id": pub.video_id,
            "video_filename": filename,
            "group_id": video_group_id,
            "account_id": pub.account_id,
            "account_profile_name": profile_name,
            "share_link": pub.share_link,
            "posted_at": pub.posted_at,
            "share_link_captured_at": pub.share_link_captured_at,
        })
    return out
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_publications_comment_targets.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/repositories/publications.py tests/test_publications_comment_targets.py
git commit -m "feat(publications): parameterize list_comment_targets by platform"
```

---

## Task 5: GeeLark client — `create_facebook_auto_comment_task`

**Files:**
- Modify: `geelark_client.py:326` (插在 `create_tiktok_ai_comment_task` 后)
- Test: `tests/test_geelark_client.py` (扩)

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_geelark_client.py 末尾


def test_create_facebook_auto_comment_task_payload_and_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskId": "fb-comment-1"}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")

    tid = c.create_facebook_auto_comment_task(
        "env-1",
        post_address="https://www.facebook.com/zuck/posts/abc",
        comments=["Nice!", "Great!"],
        keywords=["english", "tip"],
        schedule_at=999,
        name="FB Comment Test",
        remark="engagement_run_id=42",
    )

    assert tid == "fb-comment-1"
    assert captured["path"] == "/open/v1/rpa/task/faceBookAutoComment"
    assert captured["payload"] == {
        "scheduleAt": 999,
        "id": "env-1",
        "postAddress": "https://www.facebook.com/zuck/posts/abc",
        "comment": ["Nice!", "Great!"],
        "keyword": ["english", "tip"],
        "name": "FB Comment Test",
        "remark": "engagement_run_id=42",
    }


def test_create_facebook_auto_comment_task_defaults_schedule(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient,
        "_post",
        lambda self, path, payload: captured.update(payload=payload) or {"taskId": "fb-x"},
    )
    c = geelark_client.GeeLarkClient("token")
    c.create_facebook_auto_comment_task(
        "env-2",
        post_address="https://www.facebook.com/a/posts/b",
        comments=["c1"],
        keywords=["k1"],
    )
    assert isinstance(captured["payload"]["scheduleAt"], int)
    # name / remark omitted → not in payload
    assert "name" not in captured["payload"]
    assert "remark" not in captured["payload"]


def test_create_facebook_auto_comment_task_truncates_long_strings(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient, "_post",
        lambda self, path, payload: captured.update(payload=payload) or {"taskId": "fb-trunc"},
    )
    c = geelark_client.GeeLarkClient("token")
    long_comment = "x" * 9000
    long_keyword = "k" * 150
    c.create_facebook_auto_comment_task(
        "env-3",
        post_address="https://www.facebook.com/a/posts/b",
        comments=[long_comment],
        keywords=[long_keyword],
        name="n" * 200,
        remark="r" * 300,
    )
    p = captured["payload"]
    assert len(p["comment"][0]) == 8000
    assert len(p["keyword"][0]) == 100
    assert len(p["name"]) == 128
    assert len(p["remark"]) == 200


def test_create_facebook_auto_comment_task_raises_when_taskid_missing(monkeypatch):
    monkeypatch.setattr(
        geelark_client.GeeLarkClient, "_post",
        lambda self, path, payload: {},
    )
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="facebook comment task"):
        c.create_facebook_auto_comment_task(
            "env-x",
            post_address="https://www.facebook.com/a/posts/b",
            comments=["c"], keywords=["k"],
        )
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_geelark_client.py::test_create_facebook_auto_comment_task_payload_and_endpoint -v`
Expected: FAIL — `AttributeError: 'GeeLarkClient' object has no attribute 'create_facebook_auto_comment_task'`

- [ ] **Step 3: 实现客户端方法**

在 `geelark_client.py` 中 `create_tiktok_ai_comment_task` 之后插入：

```python
    def create_facebook_auto_comment_task(
        self,
        env_id: str,
        post_address: str,
        comments: list[str],
        keywords: list[str],
        *,
        schedule_at: int | None = None,
        name: str | None = None,
        remark: str | None = None,
    ) -> str:
        """Schedule a Facebook auto-comment task. Returns the task ID.

        `post_address` is NOT truncated here — service layer guarantees
        the URL is acceptable. `comments` / `keywords` are truncated per
        the GeeLark API limits as a defense-in-depth bound.
        """
        import time as _time
        payload = {
            "scheduleAt": int(schedule_at if schedule_at is not None else _time.time()),
            "id": env_id,
            "postAddress": post_address,
            "comment": [c[:8000] for c in comments],
            "keyword": [k[:100] for k in keywords],
        }
        if name:
            payload["name"] = name[:128]
        if remark:
            payload["remark"] = remark[:200]
        data = self._post("/open/v1/rpa/task/faceBookAutoComment", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"facebook comment task created but no taskId: {data}"
            )
        return task_id
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_geelark_client.py -v -k facebook_auto_comment`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add geelark_client.py tests/test_geelark_client.py
git commit -m "feat(geelark-client): add create_facebook_auto_comment_task"
```

---

## Task 6: Service 层 helpers（URL / 关键词 / 候选池 / 资格判断）

**Files:**
- Modify: `app/services/engagement_service.py`
- Test: `tests/test_engagement_service_facebook.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engagement_service_facebook.py
import pytest

from app.services import engagement_service as svc


def test_normalize_target_link_accepts_facebook_post_url():
    assert svc.normalize_target_link(
        "https://www.facebook.com/zuck/posts/pfbid0KdQ", platform="facebook",
    ).startswith("https://www.facebook.com/zuck/posts/")


def test_normalize_target_link_accepts_fb_watch_short():
    assert svc.normalize_target_link(
        "https://fb.watch/abc123/", platform="facebook",
    ).startswith("https://fb.watch/")


def test_normalize_target_link_rejects_non_facebook_url_in_facebook_mode():
    with pytest.raises(ValueError, match="Facebook"):
        svc.normalize_target_link(
            "https://www.tiktok.com/@a/video/1", platform="facebook",
        )


def test_normalize_target_link_rejects_overlong_facebook_url():
    long_url = (
        "https://www.facebook.com/x/posts/" + "a" * 200
    )
    with pytest.raises(ValueError, match="length"):
        svc.normalize_target_link(long_url, platform="facebook")


def test_sanitize_keywords_strips_and_dedupes():
    out = svc._sanitize_keywords([" a ", "b", "a", "  ", "c"])
    assert out == ["a", "b", "c"]


def test_sanitize_keywords_rejects_empty_input():
    with pytest.raises(ValueError, match="keywords"):
        svc._sanitize_keywords([])
    with pytest.raises(ValueError, match="keywords"):
        svc._sanitize_keywords(None)
    with pytest.raises(ValueError, match="keywords"):
        svc._sanitize_keywords(["   ", "\t"])


def test_sanitize_keywords_rejects_too_long_keyword():
    with pytest.raises(ValueError, match="100"):
        svc._sanitize_keywords(["a" * 101])


def test_sanitize_keywords_rejects_more_than_ten():
    with pytest.raises(ValueError, match="10"):
        svc._sanitize_keywords([str(i) for i in range(11)])


def test_sanitize_fb_candidates_strips_and_bounds():
    out = svc._sanitize_fb_candidates([" c1 ", "c2", "   "])
    assert out == ["c1", "c2"]


def test_sanitize_fb_candidates_rejects_too_long_comment():
    with pytest.raises(ValueError, match="8000"):
        svc._sanitize_fb_candidates(["x" * 8001])


def test_sanitize_fb_candidates_rejects_more_than_ten():
    with pytest.raises(ValueError, match="10"):
        svc._sanitize_fb_candidates([f"c{i}" for i in range(11)])


def test_eligible_account_checks_facebook_logged_in():
    acc_fb = {"geelark_phone_id": "p1", "warmed_up": 1,
              "facebook_logged_in": 1, "tiktok_logged_in": 0}
    acc_tk_only = {"geelark_phone_id": "p2", "warmed_up": 1,
                   "facebook_logged_in": 0, "tiktok_logged_in": 1}
    assert svc._eligible_account(acc_fb, platform="facebook") is True
    assert svc._eligible_account(acc_tk_only, platform="facebook") is False
    assert svc._eligible_account(acc_fb, platform="tiktok") is False
    assert svc._eligible_account(acc_tk_only, platform="tiktok") is True
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_engagement_service_facebook.py -v`
Expected: 全 FAIL（功能未实现 / 关键字参数 unsupported）

- [ ] **Step 3: 实现 service 改动**

在 `app/services/engagement_service.py` 顶部 import 旁加：

```python
from app.services.platforms import LOGGED_IN_COL, validate as validate_platform
```

新增常量与正则（在 `_TIKTOK_VIDEO_RE` 附近）：

```python
ACTION_FACEBOOK_AUTO_COMMENT = "facebook_auto_comment"

PLATFORM_TO_ACTION = {
    "tiktok":   ACTION_TIKTOK_AI_COMMENT,
    "facebook": ACTION_FACEBOOK_AUTO_COMMENT,
}

FB_KEYWORD_MAX_COUNT = 10
FB_KEYWORD_MAX_LEN   = 100
FB_COMMENT_MAX_COUNT = 10
FB_COMMENT_MAX_LEN   = 8000
FB_POST_ADDRESS_MAX_LEN = 128  # 文档限制；存疑，见 spec §11.1

_FB_POST_RE = re.compile(
    r"^https://(?:www\.|web\.|m\.|mbasic\.)?facebook\.com/"
    r"(?:[\w.\-]+/posts/|permalink\.php\?|share/[pv]/|reel/|video\.php\?|story\.php\?|"
    r"groups/[\w.\-]+/posts/|[\w.\-]+/videos/)"
    r"[\w./?=&%\-]*$"
)
_FB_SHORT_RE = re.compile(r"^https://fb\.(?:watch|com)/[A-Za-z0-9_\-]+/?(?:[?#].*)?$")
```

替换 `normalize_target_link`：

```python
def normalize_target_link(raw, *, platform: str = "tiktok"):
    link = (raw or "").strip()
    if platform == "tiktok":
        if _TIKTOK_VIDEO_RE.match(link) or _TIKTOK_SHORT_RE.match(link):
            return link
        raise ValueError("target link must be a TikTok video URL")
    if platform == "facebook":
        if not (_FB_POST_RE.match(link) or _FB_SHORT_RE.match(link)):
            raise ValueError("target link must be a Facebook post/reel URL")
        if len(link) > FB_POST_ADDRESS_MAX_LEN:
            raise ValueError(
                f"facebook post URL length {len(link)} exceeds GeeLark API limit "
                f"({FB_POST_ADDRESS_MAX_LEN})"
            )
        return link
    raise ValueError(f"unsupported platform: {platform!r}")
```

替换 `_eligible_account`：

```python
def _eligible_account(account: dict | None, *, platform: str = "tiktok") -> bool:
    if not (account
            and (account.get("geelark_phone_id") or "").strip()
            and account.get("warmed_up") == 1):
        return False
    col = LOGGED_IN_COL[platform]
    return account.get(col) == 1
```

> 注意 `_eligible_account` 现有调用点（同文件内 `create_comment_batch` 与 `submit_run_to_geelark`）需要传 `platform=` 关键字。Task 7 / Task 8 会做这件事。

新增两个 helpers（紧跟 `_allocate_provided_pool`）：

```python
def _sanitize_keywords(raw: list[str] | None) -> list[str]:
    """1 ≤ N ≤ 10，每个 strip 后 ≤ 100 字，保序去重。"""
    if not raw:
        raise ValueError("keywords must contain at least one entry")
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("keywords must be strings")
        text = item.strip()
        if not text:
            continue
        if len(text) > FB_KEYWORD_MAX_LEN:
            raise ValueError(
                f"keyword exceeds {FB_KEYWORD_MAX_LEN} characters"
            )
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    if not out:
        raise ValueError("keywords must contain at least one non-empty entry")
    if len(out) > FB_KEYWORD_MAX_COUNT:
        raise ValueError(
            f"keywords must contain at most {FB_KEYWORD_MAX_COUNT} entries"
        )
    return out


def _sanitize_fb_candidates(raw: list[str] | None) -> list[str]:
    """1 ≤ N ≤ 10，每条 strip 后 ≤ 8000 字。"""
    if not raw:
        raise ValueError("fb candidates must contain at least one entry")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("fb candidates must be strings")
        text = item.strip()
        if not text:
            continue
        if len(text) > FB_COMMENT_MAX_LEN:
            raise ValueError(
                f"fb candidate exceeds {FB_COMMENT_MAX_LEN} characters"
            )
        out.append(text)
    if not out:
        raise ValueError("fb candidates must contain at least one non-empty entry")
    if len(out) > FB_COMMENT_MAX_COUNT:
        raise ValueError(
            f"fb candidates must contain at most {FB_COMMENT_MAX_COUNT} entries"
        )
    return out
```

- [ ] **Step 4: 修旧 callsite — `_eligible_account` 需传 platform**

`engagement_service.py` 中：

第 145 行附近 `def _eligible_account(account: dict | None) -> bool:` 已被替换。
第 242 行附近 `if not _eligible_account(account):` → `if not _eligible_account(account, platform="tiktok"):`
第 406 行附近同理。

> Task 7 / Task 8 会把这两处再改为 `platform=task["platform"]`。本步保持向后兼容（TikTok 路径行为不变）。

- [ ] **Step 5: 跑测试**

```bash
uv run pytest tests/test_engagement_service_facebook.py -v
uv run pytest tests/test_engagement_service.py -q   # 现有 TikTok 路径不回归
```

Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/engagement_service.py tests/test_engagement_service_facebook.py
git commit -m "feat(engagement-service): FB URL/keyword/candidate helpers + platform-aware eligibility"
```

---

## Task 7: Service — `create_comment_batch` 支持 FB 分支

**Files:**
- Modify: `app/services/engagement_service.py`（`create_comment_batch` 函数）
- Test: 扩 `tests/test_engagement_service_facebook.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_engagement_service_facebook.py`：

```python
from sqlmodel import Session
from app.db import engine
from app.repositories import accounts as acc_repo
from app.repositories import engagement as eng_repo
from app.services import engagement_service as svc


def _make_fb_eligible_account(session, *, profile, phone_id, group_id):
    return acc_repo.create(session, {
        "profile_name": profile,
        "facebook_logged_in": 1, "tiktok_logged_in": 0,
        "warmed_up": 1, "geelark_phone_id": phone_id,
        "group_id": group_id,
    })


def test_create_comment_batch_facebook_writes_platform_and_keywords(
    default_group_id,
):
    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-a", phone_id="ph-fb-a", group_id=default_group_id,
        )
        result = svc.create_comment_batch(
            s,
            platform="facebook",
            account_ids=[a],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=2,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!", "Great!"],
            keywords=["english", "tip"],
        )
        task = eng_repo.get_task(s, result["task_id"])
    assert task["platform"] == "facebook"
    assert task["action"] == svc.ACTION_FACEBOOK_AUTO_COMMENT
    assert task["keywords"] == ["english", "tip"]
    assert task["comments_per_video"] == 2


def test_create_comment_batch_facebook_rejects_geelark_ai_mode(
    default_group_id,
):
    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-b", phone_id="ph-fb-b", group_id=default_group_id,
        )
        with pytest.raises(ValueError, match="comment_mode"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://www.facebook.com/zuck/posts/abc"],
                keywords=["k1"],
                comment_mode=svc.COMMENT_MODE_GEELARK_AI,
            )


def test_create_comment_batch_facebook_requires_keywords(default_group_id):
    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-c", phone_id="ph-fb-c", group_id=default_group_id,
        )
        with pytest.raises(ValueError, match="keywords"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://www.facebook.com/zuck/posts/abc"],
                comment_mode=svc.COMMENT_MODE_PROVIDED,
                provided_comments=["Nice"],
                keywords=None,
            )


def test_create_comment_batch_tiktok_rejects_nonempty_keywords(
    default_group_id,
):
    """防 UI 误传：TikTok 路径若收到非空 keywords 抛 ValueError。"""
    from app.repositories import accounts as acc_repo
    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "tk-z",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-z",
            "group_id": default_group_id,
        })
        with pytest.raises(ValueError, match="keywords"):
            svc.create_comment_batch(
                s,
                platform="tiktok",
                account_ids=[a],
                target_links=["https://www.tiktok.com/@x/video/1234567890"],
                keywords=["bad"],
            )


def test_create_comment_batch_facebook_rejects_invalid_url(
    default_group_id,
):
    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-d", phone_id="ph-fb-d", group_id=default_group_id,
        )
        with pytest.raises(ValueError, match="Facebook"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://example.com/not-fb"],
                comment_mode=svc.COMMENT_MODE_PROVIDED,
                provided_comments=["Nice"],
                keywords=["k1"],
            )


def test_create_comment_batch_facebook_rejects_non_eligible_account(
    default_group_id,
):
    """账号 tiktok_logged_in=1 但 facebook_logged_in=0，FB 路径应拒。"""
    from app.repositories import accounts as acc_repo
    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "tk-only",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-x",
            "group_id": default_group_id,
        })
        with pytest.raises(ValueError, match="Facebook"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://www.facebook.com/zuck/posts/abc"],
                keywords=["k"],
                comment_mode=svc.COMMENT_MODE_PROVIDED,
                provided_comments=["Nice"],
            )
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_engagement_service_facebook.py -v -k create_comment_batch`
Expected: FAIL — 多数缺 `platform` / `keywords` 关键字支持

- [ ] **Step 3: 改 `create_comment_batch`**

在签名加形参，并按平台分派：

```python
def create_comment_batch(
    session,
    *,
    platform: str = "tiktok",
    account_ids: list[int],
    target_links: list[str] | None = None,
    target_publication_ids: list[int] | None = None,
    comments_per_video: int = 1,
    group_id: int | None = None,
    comment_probability: int = 100,
    comment_mode: str = COMMENT_MODE_GEELARK_AI,
    comment_prompt_id: int | None = None,
    provided_comments: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict:
    platform = validate_platform(platform)

    # ---- platform-specific validation ----
    if platform == "facebook":
        if comment_mode == COMMENT_MODE_GEELARK_AI:
            raise ValueError(
                "comment_mode 'geelark_ai' is not supported for Facebook "
                "comments; use 'studio_ai' or 'provided'."
            )
        keywords_clean = _sanitize_keywords(keywords)
        if comment_probability != 100:
            comment_probability = 100  # ignored on FB path
    else:
        if keywords:
            raise ValueError(
                "keywords are only accepted when platform='facebook'"
            )
        keywords_clean = []

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
    if not isinstance(comments_per_video, int) or comments_per_video < 1 \
            or comments_per_video > 20:
        raise ValueError("comments_per_video must be between 1 and 20")

    pool: list[str] = []
    if comment_mode == COMMENT_MODE_PROVIDED:
        if platform == "facebook":
            pool = _sanitize_fb_candidates(provided_comments)
        else:
            pool = _sanitize_provided_pool(provided_comments)
        if not pool:
            raise ValueError(
                "provided_comments must contain at least one non-empty entry"
            )

    target_links = target_links or []
    target_publication_ids = target_publication_ids or []

    accounts = []
    for account_id in account_ids:
        account = acc_repo.get(session, account_id)
        if account is None:
            raise ValueError(f"account {account_id} does not exist")
        if not _eligible_account(account, platform=platform):
            label = "Facebook commenter" if platform == "facebook" else "TikTok commenter"
            raise ValueError(
                f"account {account_id} is not an eligible {label}"
            )
        accounts.append(account)

    if group_id is None:
        group_ids = {a.get("group_id") for a in accounts}
        group_ids.discard(None)
        group_id = next(iter(group_ids)) if len(group_ids) == 1 else None

    manual_links = [normalize_target_link(link, platform=platform)
                    for link in target_links]
    publication_targets = _publication_targets(
        session, target_publication_ids, platform=platform,
    )
    targets = _dedupe_targets(_expand_targets(manual_links, publication_targets))
    if not targets:
        raise ValueError("at least one target link is required")

    planned: list[tuple[dict, dict]] = []
    cyc = itertools.cycle(accounts)
    for target in targets:
        owner_id = target.get("target_account_id")
        eligible_for_target = [a for a in accounts if owner_id != a["id"]]
        if not eligible_for_target:
            continue
        for _ in range(comments_per_video):
            for _attempt in range(len(accounts) + 1):
                candidate = next(cyc)
                if candidate["id"] != owner_id:
                    break
            planned.append((candidate, target))

    if not planned:
        label = "Facebook" if platform == "facebook" else "TikTok"
        raise ValueError(f"no eligible {label} comment phones found")

    allocated_comments: list[str] = (
        _allocate_provided_pool(pool, len(planned))
        if comment_mode == COMMENT_MODE_PROVIDED
        else []
    )

    action = PLATFORM_TO_ACTION[platform]

    task_ids: list[int] = []
    run_ids: list[int] = []
    for idx, (account, target) in enumerate(planned):
        source = (
            "publication_share_links"
            if target.get("target_publication_id") is not None
            else "manual_links"
        )
        task_id = eng_repo.create_task(
            session,
            group_id=group_id,
            action=action,
            source=source,
            target_links=[target["target_link"]],
            target_publication_ids=[target["target_publication_id"]]
            if target.get("target_publication_id") is not None
            else [],
            comment_probability=comment_probability,
            comment_mode=comment_mode,
            comment_prompt_id=comment_prompt_id,
            comments_per_video=comments_per_video,
            platform=platform,
            keywords=keywords_clean,
        )
        run_id = eng_repo.create_run(
            session,
            engagement_task_id=task_id,
            account_id=account["id"],
            target_link=target["target_link"],
            action=action,
            target_publication_id=target.get("target_publication_id"),
            target_account_id=target.get("target_account_id"),
        )
        if comment_mode == COMMENT_MODE_PROVIDED and platform == "tiktok":
            eng_repo.set_generated_comment(
                session, run_id,
                comment_text=allocated_comments[idx],
                source=COMMENT_MODE_PROVIDED,
            )
        # FB 路径下，候选池在 submit 阶段写入（见 Task 8），保持与 studio_ai 一致
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

同步修改 `_publication_targets` 签名加 `platform`：

```python
def _publication_targets(session, publication_ids,
                         *, platform: str = "tiktok") -> list[dict]:
    targets = []
    for pub_id in publication_ids:
        publication = pub_repo.get(session, pub_id)
        if publication is None:
            raise ValueError(f"publication {pub_id} does not exist")
        if publication.get("platform") != platform:
            label = "Facebook" if platform == "facebook" else "TikTok"
            raise ValueError(f"target publication must be a {label} publication")
        share_link = normalize_target_link(
            publication.get("share_link"), platform=platform,
        )
        targets.append({
            "target_publication_id": publication["id"],
            "target_account_id": publication.get("account_id"),
            "target_link": share_link,
        })
    return targets
```

- [ ] **Step 4: 跑测试**

```bash
uv run pytest tests/test_engagement_service_facebook.py -v
uv run pytest tests/test_engagement_service.py -q
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/engagement_service.py tests/test_engagement_service_facebook.py
git commit -m "feat(engagement-service): create_comment_batch FB branch"
```

---

## Task 8: Service — `submit_run_to_geelark` 按 action 分派 + `_fb_run_candidates`

**Files:**
- Modify: `app/services/engagement_service.py`（`submit_run_to_geelark` + 新增 `_fb_run_candidates`）
- Test: 扩 `tests/test_engagement_service_facebook.py`

- [ ] **Step 1: 写失败测试 — submit 路径**

追加测试：

```python
def test_submit_run_facebook_calls_geelark_with_candidates_and_keywords(
    default_group_id, monkeypatch,
):
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "fb-submit",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-submit",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s,
            platform="facebook",
            account_ids=[a],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=3,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!", "Great!", "Cool!"],
            keywords=["english"],
        )
        run_id = result["run_ids"][0]

    captured = {}

    class FakeClient:
        def create_facebook_auto_comment_task(
            self, env_id, *, post_address, comments, keywords,
            schedule_at=None, name=None, remark=None,
        ):
            captured.update(
                env_id=env_id, post_address=post_address,
                comments=comments, keywords=keywords,
                schedule_at=schedule_at, name=name, remark=remark,
            )
            return "fb-task-1"

    with Session(engine) as s:
        task_id = svc.submit_run_to_geelark(s, FakeClient(), run_id=run_id)
        run = eng_repo.get_run(s, run_id)

    assert task_id == "fb-task-1"
    assert captured["post_address"] == "https://www.facebook.com/zuck/posts/abc"
    assert captured["comments"] == ["Nice!", "Great!", "Cool!"]
    assert captured["keywords"] == ["english"]
    assert run["result"] == "INITIATED"
    assert run["geelark_task_id"] == "fb-task-1"
    assert run["comment_candidates"] == ["Nice!", "Great!", "Cool!"]
    assert run["comment_text"] == "Nice!"
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_engagement_service_facebook.py::test_submit_run_facebook_calls_geelark_with_candidates_and_keywords -v`
Expected: FAIL — submit 仍调 TikTok 客户端方法 (AttributeError on FakeClient)

- [ ] **Step 3: 实现 `_fb_run_candidates` + dispatch**

新增辅助函数（紧跟 `_prepare_studio_comment`）：

```python
def _fb_run_candidates(session, run: dict, parent: dict) -> list[str]:
    """Build the candidate pool for an FB engagement run and persist it.

    Returns the candidate list (1..10). Always writes
    EngagementRun.comment_candidates + comment_text(=first one) +
    comment_source.
    """
    mode = parent.get("comment_mode") or COMMENT_MODE_PROVIDED
    k = max(1, int(parent.get("comments_per_video") or 1))

    if mode == COMMENT_MODE_STUDIO_AI:
        meta = tiktok_metadata.fetch_tiktok_metadata(run["target_link"])  # OK to reuse: returns empty dict on FB URL
        title = meta.get("title")
        description = meta.get("description")
        if title or description:
            eng_repo.set_target_context(
                session, run["id"], title=title, description=description,
            )
        prompt_id = parent.get("comment_prompt_id")
        if prompt_id is not None:
            prompt = cp_repo.get(session, prompt_id)
            if prompt is None:
                raise RuntimeError(
                    f"comment_prompts row {prompt_id} vanished before submit"
                )
            prompt_body = prompt["body"]
        else:
            prompt_body = _combined_brief(parent) or ""

        from app import config
        from app.services.posting_service import _thinking_budget
        settings_map = {**config.SETTINGS_DEFAULTS, **set_repo.get_all(session)}
        budget = _thinking_budget(settings_map)

        candidates: list[str] = []
        for _ in range(k):
            c = comment_generation.generate_comment_for_target(
                prompt_body=prompt_body,
                target_title=title,
                thinking_budget=budget,
            )
            candidates.append(c)
        candidates = _sanitize_fb_candidates(candidates)
        eng_repo.set_comment_candidates(
            session, run["id"], candidates=candidates, source=COMMENT_MODE_STUDIO_AI,
        )
        return candidates

    # COMMENT_MODE_PROVIDED
    # Build candidates from the parent's provided pool by re-running
    # _allocate_provided_pool — the original allocator already returns a
    # stable rotation; for FB we just need K items.
    raw_pool = [run.get("comment_text")] if run.get("comment_text") else []
    # Provided pool isn't stored on the task row in TikTok-mode; for FB we
    # store provided_comments on the task via target_links? — No: keep
    # provided pool in EngagementRun.comment_text written by Task 7's
    # create_comment_batch? — We did NOT write it for FB in Task 7.
    # Therefore, for FB provided mode, the candidates were allocated in
    # Task 7 and stored as the *first* item; here we re-fetch from the
    # original create_comment_batch allocation. Simplest approach: store
    # candidates eagerly at create_comment_batch time. See Task 7 step 3
    # comment — adjust as follows in Step 4 below.
    candidates = run.get("comment_candidates") or []
    if not candidates:
        raise RuntimeError(
            f"engagement run {run['id']} has no comment_candidates for FB provided mode"
        )
    return candidates
```

> 注：实现 `_fb_run_candidates` 揭示了 Task 7 的一个 gap：FB provided 模式下 candidates 必须在 create 阶段就持久化，不能等到 submit。Step 4 修复。

- [ ] **Step 4: 改 `create_comment_batch` 让 FB provided 模式立即写候选池**

把 Task 7 中的：

```python
if comment_mode == COMMENT_MODE_PROVIDED and platform == "tiktok":
    eng_repo.set_generated_comment(
        session, run_id,
        comment_text=allocated_comments[idx],
        source=COMMENT_MODE_PROVIDED,
    )
```

改为：

```python
if comment_mode == COMMENT_MODE_PROVIDED:
    if platform == "facebook":
        # FB 需要候选池：从池里取 K 条
        k = comments_per_video
        # idx 落在 planned 里；从 pool 里以 (idx*k) 开头连续取 K 条；
        # _allocate_provided_pool 已铺平为 len(planned) 个独立 slot，
        # 所以我们改为一次分配 len(planned)*k 条
        pass  # 见下方重构
    else:
        eng_repo.set_generated_comment(
            session, run_id,
            comment_text=allocated_comments[idx],
            source=COMMENT_MODE_PROVIDED,
        )
```

更干净的重构：在 FB provided 模式下提前为每个 run 分配 K 条候选（一次分配 `len(planned) * comments_per_video` 个 slot）：

```python
allocated_comments: list[str] = []
allocated_fb_pools: list[list[str]] = []
if comment_mode == COMMENT_MODE_PROVIDED:
    if platform == "facebook":
        flat = _allocate_provided_pool(pool, len(planned) * comments_per_video)
        # 切成 len(planned) 段，每段 comments_per_video 条
        allocated_fb_pools = [
            flat[i * comments_per_video:(i + 1) * comments_per_video]
            for i in range(len(planned))
        ]
    else:
        allocated_comments = _allocate_provided_pool(pool, len(planned))
```

然后在 run 创建循环里：

```python
if comment_mode == COMMENT_MODE_PROVIDED:
    if platform == "facebook":
        eng_repo.set_comment_candidates(
            session, run_id,
            candidates=allocated_fb_pools[idx],
            source=COMMENT_MODE_PROVIDED,
        )
    else:
        eng_repo.set_generated_comment(
            session, run_id,
            comment_text=allocated_comments[idx],
            source=COMMENT_MODE_PROVIDED,
        )
```

- [ ] **Step 5: 改 `submit_run_to_geelark` 按 action 分派**

在 `submit_run_to_geelark` 中：

```python
parent = eng_repo.get_task(session, run["engagement_task_id"])
parent_action = parent.get("action") or ACTION_TIKTOK_AI_COMMENT
platform = parent.get("platform") or "tiktok"

# eligibility 现按平台
if not _eligible_account(account, platform=platform):
    eng_repo.set_result(session, run_id, "SKIPPED", "account is not eligible")
    eng_repo.recompute_task_status(session, run["engagement_task_id"])
    raise RuntimeError("account is not eligible")
```

调度分支：

```python
schedule_at = int(time.time())

if parent_action == ACTION_TIKTOK_AI_COMMENT:
    # 现有 TikTok 路径不动
    comment_text = None
    parent_mode = parent.get("comment_mode") or COMMENT_MODE_GEELARK_AI
    if parent_mode == COMMENT_MODE_STUDIO_AI:
        # ... 现有逻辑 ...
        try:
            comment_text = _prepare_studio_comment(session, run, parent)
        except Exception as exc:
            ...
    elif parent_mode == COMMENT_MODE_PROVIDED:
        comment_text = (run.get("comment_text") or "").strip() or None
        ...
    try:
        task_id = client.create_tiktok_ai_comment_task(
            account["geelark_phone_id"],
            [run["target_link"]],
            schedule_at=schedule_at,
            comment_probability=int(parent.get("comment_probability") or 100),
            comment=comment_text,
            name="GeeLark Studio AI Comment",
            remark=f"engagement_run_id={run_id}",
        )
    except Exception as exc:
        ...

elif parent_action == ACTION_FACEBOOK_AUTO_COMMENT:
    try:
        candidates = _fb_run_candidates(session, run, parent)
    except Exception as exc:
        eng_repo.set_result(
            session, run_id, "FAILURE",
            f"candidate preparation failed: {exc}",
        )
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _finalize_session(
            session, account["id"], kind="failure",
            reason=f"candidate preparation failed: {exc}",
        )
        raise
    try:
        task_id = client.create_facebook_auto_comment_task(
            account["geelark_phone_id"],
            post_address=run["target_link"],
            comments=candidates,
            keywords=parent.get("keywords") or [],
            schedule_at=schedule_at,
            name="GeeLark Studio FB Comment",
            remark=f"engagement_run_id={run_id}",
        )
    except Exception as exc:
        eng_repo.set_result(session, run_id, "FAILURE",
                            f"submit failed: {exc}")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, account["geelark_phone_id"])
        _finalize_session(
            session, account["id"], kind="failure",
            reason=f"submit failed: {exc}",
        )
        raise
else:
    raise RuntimeError(f"unknown engagement action: {parent_action!r}")
```

注意：现有 `submit_run_to_geelark` 中的 PhoneBusy / phone_runtime / mark_initiated / attach_geelark_task 等代码**保留不动**，只把"准备评论 + 调用 GeeLark"那一段按 action 包装。

- [ ] **Step 6: 跑测试**

```bash
uv run pytest tests/test_engagement_service_facebook.py -v
uv run pytest tests/test_engagement_service.py -q
uv run pytest tests/test_engagement_lease.py -q
```

Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/engagement_service.py tests/test_engagement_service_facebook.py
git commit -m "feat(engagement-service): submit dispatch for FB + _fb_run_candidates"
```

---

## Task 9: API — `POST /api/engagement/comment/facebook`

**Files:**
- Modify: `app/api/engagement.py`
- Test: `tests/test_api_engagement_facebook.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_api_engagement_facebook.py
from sqlmodel import Session
from app.db import engine
from app.repositories import accounts as acc_repo


def _seed_fb_phone(session, *, group_id, profile="fb-acc"):
    return acc_repo.create(session, {
        "profile_name": profile,
        "facebook_logged_in": 1, "warmed_up": 1,
        "geelark_phone_id": f"ph-{profile}",
        "group_id": group_id,
    })


def test_post_comment_facebook_creates_runs(client, default_group_id):
    with Session(engine) as s:
        aid = _seed_fb_phone(s, group_id=default_group_id)
    body = {
        "account_ids": [aid],
        "target_links": ["https://www.facebook.com/zuck/posts/abc"],
        "candidates_per_post": 2,
        "keywords": ["english", "tip"],
        "comment_mode": "provided",
        "provided_comments": ["Nice!", "Great!"],
    }
    r = client.post("/api/engagement/comment/facebook", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created_runs"] == 1
    assert isinstance(data["task_id"], int)


def test_post_comment_facebook_rejects_invalid_keywords(client, default_group_id):
    with Session(engine) as s:
        aid = _seed_fb_phone(s, group_id=default_group_id)
    body = {
        "account_ids": [aid],
        "target_links": ["https://www.facebook.com/zuck/posts/abc"],
        "candidates_per_post": 1,
        "keywords": [],
        "comment_mode": "provided",
        "provided_comments": ["c1"],
    }
    r = client.post("/api/engagement/comment/facebook", json=body)
    assert r.status_code == 400
    assert "keywords" in r.json()["detail"].lower()


def test_post_comment_facebook_rejects_geelark_ai_mode(client, default_group_id):
    with Session(engine) as s:
        aid = _seed_fb_phone(s, group_id=default_group_id)
    body = {
        "account_ids": [aid],
        "target_links": ["https://www.facebook.com/zuck/posts/abc"],
        "candidates_per_post": 1,
        "keywords": ["k1"],
        "comment_mode": "geelark_ai",
    }
    r = client.post("/api/engagement/comment/facebook", json=body)
    assert r.status_code == 400
    assert "comment_mode" in r.json()["detail"].lower()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_api_engagement_facebook.py -v`
Expected: FAIL — 404 not found

- [ ] **Step 3: 加路由**

在 `app/api/engagement.py` 现有 `create_comment_engagement` 之后插入：

```python
@router.post("/comment/facebook")
async def create_facebook_comment_engagement(
    body: dict,
    session: Session = Depends(get_session),
):
    try:
        candidates_per_post = int(body.get("candidates_per_post", 1))
        result = engagement_service.create_comment_batch(
            session,
            platform="facebook",
            account_ids=body.get("account_ids") or [],
            target_links=body.get("target_links") or [],
            target_publication_ids=body.get("target_publication_ids") or [],
            comments_per_video=candidates_per_post,
            group_id=body.get("group_id"),
            comment_mode=body.get("comment_mode", "provided"),
            comment_prompt_id=body.get("comment_prompt_id"),
            provided_comments=body.get("provided_comments"),
            keywords=body.get("keywords") or [],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    deferred = 0
    run_ids = result.get("run_ids") or [
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

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_api_engagement_facebook.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/engagement.py tests/test_api_engagement_facebook.py
git commit -m "feat(api): POST /api/engagement/comment/facebook"
```

---

## Task 10: API — list 端点支持 `?platform=`

**Files:**
- Modify: `app/api/engagement.py`（4 个 GET 端点）
- Test: 扩 `tests/test_api_engagement_facebook.py`

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_get_engagement_runs_filters_by_platform(client, default_group_id):
    with Session(engine) as s:
        fb_aid = _seed_fb_phone(s, group_id=default_group_id, profile="fb-a")
        tk_aid = acc_repo.create(s, {
            "profile_name": "tk-a",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-tk-a",
            "group_id": default_group_id,
        })
    # seed 1 FB run + 1 TikTok run
    client.post("/api/engagement/comment/facebook", json={
        "account_ids": [fb_aid],
        "target_links": ["https://www.facebook.com/x/posts/1"],
        "candidates_per_post": 1, "keywords": ["k"],
        "comment_mode": "provided", "provided_comments": ["c1"],
    })
    client.post("/api/engagement/comment", json={
        "account_ids": [tk_aid],
        "target_links": ["https://www.tiktok.com/@x/video/1234567890"],
        "comments_per_video": 1, "comment_probability": 100,
        "comment_mode": "provided", "provided_comments": ["c1"],
    })
    fb = client.get("/api/engagement/runs?platform=facebook").json()
    tk = client.get("/api/engagement/runs?platform=tiktok").json()
    assert all(
        "facebook.com" in r["target_link"] for r in fb["items"]
    )
    assert all(
        "tiktok.com" in r["target_link"] for r in tk["items"]
    )


def test_get_eligible_phones_filters_by_platform(client, default_group_id):
    with Session(engine) as s:
        fb_aid = _seed_fb_phone(s, group_id=default_group_id, profile="fb-only")
        tk_aid = acc_repo.create(s, {
            "profile_name": "tk-only",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-tk-only",
            "group_id": default_group_id,
        })
    fb = client.get("/api/engagement/eligible-phones?platform=facebook").json()
    ids = {x["id"] for x in fb["items"]}
    assert fb_aid in ids and tk_aid not in ids


def test_get_engagement_targets_filters_by_platform(client, default_group_id):
    from app.models import Account, Publication, Video
    with Session(engine) as s:
        a = Account(profile_name="x", geelark_phone_id="phx",
                    group_id=default_group_id)
        s.add(a); s.commit(); s.refresh(a)
        v = Video(stored_path="/tmp/x.mp4", original_filename="x.mp4",
                  group_id=default_group_id)
        s.add(v); s.commit(); s.refresh(v)
        p = Publication(
            video_id=v.id, account_id=a.id, platform="facebook",
            result="SUCCESS",
            share_link="https://www.facebook.com/x/posts/123",
        )
        s.add(p); s.commit()
    fb = client.get("/api/engagement/targets?platform=facebook").json()
    assert any(
        "facebook.com" in i["share_link"] for i in fb["items"]
    )
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `uv run pytest tests/test_api_engagement_facebook.py -v -k filters_by_platform`
Expected: FAIL（runs/eligible 不识别 ?platform=）

- [ ] **Step 3: 改 list 端点**

在 `app/api/engagement.py`：

```python
@router.get("/tasks")
def list_engagement_tasks(
    group_id: int | None = None,
    platform: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    return eng_repo.list_tasks(
        session, group_id=group_id, platform=platform, limit=limit,
    )


@router.get("/runs")
def list_engagement_runs(
    group_id: int | None = None,
    platform: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    before_id: int | None = Query(None, ge=1),
    status: str | None = None,
    source: str | None = None,
    session: Session = Depends(get_session),
):
    return {
        "items": eng_repo.list_runs(
            session,
            group_id=group_id,
            platform=platform,
            limit=limit,
            before_id=before_id,
            status=status,
            source=source,
        )
    }


@router.get("/eligible-phones")
def list_eligible_phones(
    platform: str = "tiktok",
    session: Session = Depends(get_session),
):
    return {"items": eph_repo.list_eligible_commenters(session, platform=platform)}


@router.get("/targets")
def list_engagement_targets(
    group_id: int | None = None,
    platform: str = "tiktok",
    limit: int = Query(100, ge=1, le=200),
    session: Session = Depends(get_session),
):
    return {
        "items": pub_repo.list_comment_targets(
            session, group_id=group_id, platform=platform, limit=limit,
        )
    }
```

- [ ] **Step 4: 跑测试**

```bash
uv run pytest tests/test_api_engagement_facebook.py -v
uv run pytest tests/test_api_engagement.py -q
uv run pytest tests/test_eligible_phones_endpoint.py -q
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/engagement.py tests/test_api_engagement_facebook.py
git commit -m "feat(api): ?platform= filter on engagement list endpoints"
```

---

## Task 11: FB 端到端集成测试

**Files:**
- Test: `tests/test_engagement_facebook_flow.py` (new)

- [ ] **Step 1: 写集成测试**

```python
# tests/test_engagement_facebook_flow.py
"""End-to-end FB engagement flow: API → submit → GeeLark → check."""
from sqlmodel import Session

from app.db import engine
from app.repositories import accounts as acc_repo
from app.repositories import engagement as eng_repo
from app.services import engagement_service as svc


class _FakeClient:
    """Records calls; configurable status returns for query_tasks."""

    def __init__(self, status_seq=(3,)):
        self.created: list[dict] = []
        self.status_seq = list(status_seq)
        self.queried: list[str] = []
        self.stopped: list[str] = []

    def create_facebook_auto_comment_task(
        self, env_id, *, post_address, comments, keywords,
        schedule_at=None, name=None, remark=None,
    ):
        self.created.append(dict(
            env_id=env_id, post_address=post_address,
            comments=comments, keywords=keywords,
        ))
        return "fake-fb-task-1"

    def query_tasks(self, ids):
        self.queried.extend(ids)
        status = self.status_seq.pop(0) if self.status_seq else 3
        return [{"id": ids[0], "status": status, "cost": 100}]

    def get_task_detail(self, tid):
        return {"resultImages": ["https://shot/result.jpg"]}

    def stop_phones(self, ids):
        self.stopped.extend(ids)


def test_full_flow_success(default_group_id):
    with Session(engine) as s:
        aid = acc_repo.create(s, {
            "profile_name": "fb-flow",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-flow",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s, platform="facebook", account_ids=[aid],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=2,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!", "Great!"],
            keywords=["english"],
        )
        run_id = result["run_ids"][0]

    client = _FakeClient(status_seq=[3])

    with Session(engine) as s:
        task_id = svc.submit_run_to_geelark(s, client, run_id=run_id)
        assert task_id == "fake-fb-task-1"
        outcome = svc.check_engagement_run(s, client, run_id)
        run = eng_repo.get_run(s, run_id)

    assert outcome == "success"
    assert run["result"] == "SUCCESS"
    assert run["result_screenshot"] == "https://shot/result.jpg"
    assert client.stopped == ["ph-flow"]


def test_full_flow_failure_status_4(default_group_id):
    with Session(engine) as s:
        aid = acc_repo.create(s, {
            "profile_name": "fb-fail",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-fail",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s, platform="facebook", account_ids=[aid],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=1,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!"],
            keywords=["k"],
        )
        run_id = result["run_ids"][0]

    class _FailClient(_FakeClient):
        def get_task_detail(self, tid):
            return {"failDesc": "kw not found", "failCode": 1001,
                    "resultImages": ["https://shot/fail.jpg"]}

    client = _FailClient(status_seq=[4])

    with Session(engine) as s:
        svc.submit_run_to_geelark(s, client, run_id=run_id)
        outcome = svc.check_engagement_run(s, client, run_id)
        run = eng_repo.get_run(s, run_id)

    assert outcome == "failure"
    assert run["result"] == "FAILURE"
    assert run["fail_code"] == 1001
    assert "kw not found" in run["notes"]
```

备注：API 层字段名 `candidates_per_post` 对应 service 层 `comments_per_video`（见 Task 9 step 3）。

- [ ] **Step 2: 跑测试**

Run: `uv run pytest tests/test_engagement_facebook_flow.py -v`
Expected: 2 PASS

- [ ] **Step 3: 跑全套确认无回归**

Run: `uv run pytest -q`
Expected: 全 PASS（基线 385+）

- [ ] **Step 4: Commit**

```bash
git add tests/test_engagement_facebook_flow.py
git commit -m "test(engagement): FB end-to-end flow (success + status-4 failure)"
```

---

## Task 12: 前端 — `api.ts` 加 FB 类型与方法

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: 新增类型 & 方法**

在 `frontend/src/api.ts` 末尾或 `engagement*` 块附近加：

```ts
export type EngagementPlatform = 'tiktok' | 'facebook'

export type FbCommentSubmitPayload = {
  account_ids: number[]
  target_links: string[]
  target_publication_ids: number[]
  candidates_per_post: number
  keywords: string[]
  comment_mode: 'studio_ai' | 'provided'
  comment_prompt_id?: number | null
  provided_comments?: string[] | null
  group_id?: number | null
}
```

把现有 `engagementRuns / engagementTargets / eligiblePhones` 函数签名加可选 `platform`，并把参数拼到 query string：

```ts
engagementRuns: (params: {
  groupId?: number
  limit?: number
  beforeId?: number
  status?: string
  source?: string
  platform?: EngagementPlatform
}): Promise<{ items: EngagementRun[] }> => {
  const qs = new URLSearchParams()
  if (params.groupId != null) qs.set('group_id', String(params.groupId))
  if (params.limit != null) qs.set('limit', String(params.limit))
  if (params.beforeId != null) qs.set('before_id', String(params.beforeId))
  if (params.status) qs.set('status', params.status)
  if (params.source) qs.set('source', params.source)
  if (params.platform) qs.set('platform', params.platform)
  return req('/engagement/runs' + (qs.toString() ? '?' + qs.toString() : ''))
},

engagementTargets: (
  opts: { groupId?: number; platform?: EngagementPlatform } = {},
): Promise<{ items: CommentTarget[] }> => {
  const qs = new URLSearchParams()
  if (opts.groupId != null) qs.set('group_id', String(opts.groupId))
  if (opts.platform) qs.set('platform', opts.platform)
  return req('/engagement/targets' + (qs.toString() ? '?' + qs.toString() : ''))
},

eligiblePhones: (
  opts: { platform?: EngagementPlatform } = {},
): Promise<{ items: EligiblePhone[] }> => {
  const qs = new URLSearchParams()
  if (opts.platform) qs.set('platform', opts.platform)
  return req('/engagement/eligible-phones' + (qs.toString() ? '?' + qs.toString() : ''))
},
```

> **重要**：现有 callsites 必须对应升级 — `Comments.tsx` 调用 `api.eligiblePhones()` / `api.engagementTargets(...)` —— 旧签名兼容（不传 platform 时默认 TikTok 行为）。把旧 callsite 升级为传 `platform: 'tiktok'` 显式更安全；本任务**保留旧签名兼容**，让 Task 14 的 FbComments 页传 `platform: 'facebook'`。

新增 FB 提交方法：

```ts
createFbEngagementComment: (
  body: FbCommentSubmitPayload,
): Promise<{ task_id: number; created_runs: number; deferred_runs: number }> =>
  req('/engagement/comment/facebook', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
```

- [ ] **Step 2: 编译通过**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(frontend-api): FB engagement types + createFbEngagementComment"
```

---

## Task 13: 前端 — `FbCommentModal.tsx`（三步向导）

**Files:**
- Create: `frontend/src/components/FbCommentModal.tsx`

- [ ] **Step 1: 拷贝 + 改造**

由于 `CommentModal.tsx` 已 1115 行，复制后**最小化改动**：

1. 改 import 类型为 FB
2. 删 Discover Tab
3. Step 1 验证 URL 改 FB 正则（轻量提示，权威校验留后端）
4. Step 2 加 keywords textarea
5. Step 3 candidates_per_post 标签改为 "每帖候选评论数"
6. Submit payload 改为 `FbCommentSubmitPayload`

具体片段（节选；完整文件由 agent 实现时基于 `CommentModal.tsx` 派生）：

```tsx
// frontend/src/components/FbCommentModal.tsx
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import {
  api,
  type CommentTarget,
  type EligiblePhone,
  type CommentPrompt,
  type FbCommentSubmitPayload,
} from '../api'
import { Button } from './ui'
import { cn } from '../lib/cn'

type Mode = 'owned' | 'manual'
type Step = 1 | 2 | 3
type CommentMode = 'generate' | 'provided'

// FB 帖子 URL 轻量校验
const FB_URL_RE = /^https:\/\/(?:(?:www|m|web|mbasic)\.)?facebook\.com\/.+/i
const FB_SHORT_RE = /^https:\/\/fb\.(?:watch|com)\/[A-Za-z0-9_\-]+/i

function isLikelyFbUrl(u: string) {
  return FB_URL_RE.test(u.trim()) || FB_SHORT_RE.test(u.trim())
}

export function FbCommentModal({
  open, onClose, onSubmit,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (payload: FbCommentSubmitPayload) => Promise<void>
}) {
  const { t } = useTranslation()
  const [step, setStep] = useState<Step>(1)
  const [phones, setPhones] = useState<EligiblePhone[]>([])
  const [targets, setTargets] = useState<CommentTarget[]>([])
  const [selectedPhones, setSelectedPhones] = useState<Set<number>>(new Set())
  const [selectedTargets, setSelectedTargets] = useState<Set<number>>(new Set())
  const [mode, setMode] = useState<Mode>('owned')
  const [manualLinks, setManualLinks] = useState('')
  const [keywordsText, setKeywordsText] = useState('')
  const [candidatesPerPost, setCandidatesPerPost] = useState(3)
  const [prompts, setPrompts] = useState<CommentPrompt[]>([])
  const [commentPromptId, setCommentPromptId] = useState<number | null>(null)
  const [commentMode, setCommentMode] = useState<CommentMode>('generate')
  const [providedComments, setProvidedComments] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) return
    setStep(1)
    Promise.all([
      api.eligiblePhones({ platform: 'facebook' }),
      api.engagementTargets({ platform: 'facebook' }),
      api.commentPrompts().catch(() => [] as CommentPrompt[]),
    ]).then(([p, ts, pr]) => {
      setPhones(p.items)
      setTargets(ts.items)
      setSelectedPhones(new Set(p.items.filter((x) => !x.busy).map((x) => x.id)))
      setPrompts(pr)
      setCommentPromptId(pr.length === 1 ? pr[0].id : null)
    }).catch(() => {})
  }, [open])

  const manualLinkList = useMemo(
    () => manualLinks.split('\n').map((s) => s.trim()).filter(Boolean),
    [manualLinks],
  )
  const keywordList = useMemo(
    () => keywordsText.split('\n').map((s) => s.trim()).filter(Boolean),
    [keywordsText],
  )
  const providedList = useMemo(
    () => providedComments.split('\n').map((s) => s.trim()).filter(Boolean),
    [providedComments],
  )

  const targetCount =
    mode === 'owned' ? selectedTargets.size : manualLinkList.length
  const allLinksLookOK = mode !== 'manual' || manualLinkList.every(isLikelyFbUrl)
  const keywordsOk =
    keywordList.length >= 1 &&
    keywordList.length <= 10 &&
    keywordList.every((k) => k.length <= 100)
  const candidateCountOk =
    candidatesPerPost >= 1 && candidatesPerPost <= 10
  const providedOk =
    commentMode !== 'provided' ||
    (providedList.length > 0 &&
      providedList.length <= 10 &&
      providedList.every((c) => c.length <= 8000))
  const promptOk = commentMode === 'generate' ? commentPromptId != null : true

  const canSubmit =
    !submitting &&
    selectedPhones.size > 0 &&
    targetCount > 0 &&
    allLinksLookOK &&
    keywordsOk &&
    candidateCountOk &&
    providedOk &&
    promptOk

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      const payload: FbCommentSubmitPayload = {
        account_ids: [...selectedPhones],
        target_links: mode === 'manual' ? manualLinkList : [],
        target_publication_ids: mode === 'owned' ? [...selectedTargets] : [],
        candidates_per_post: candidatesPerPost,
        keywords: keywordList,
        comment_mode: commentMode === 'generate' ? 'studio_ai' : 'provided',
        comment_prompt_id: commentMode === 'generate' ? commentPromptId : null,
        provided_comments: commentMode === 'provided' ? providedList : null,
      }
      await onSubmit(payload)
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[var(--radius-lg)] w-[640px] max-h-[88vh] overflow-y-auto p-6">
        {/* Step indicator */}
        <div className="text-[13px] text-[var(--color-ink-3)] mb-3">
          {t('fbComments.stepLabel', { current: step, total: 3 })}
        </div>

        {step === 1 && (
          <Step1Targets
            mode={mode} setMode={setMode}
            targets={targets}
            selectedTargets={selectedTargets}
            setSelectedTargets={setSelectedTargets}
            manualLinks={manualLinks}
            setManualLinks={setManualLinks}
            allLinksLookOK={allLinksLookOK}
            t={t}
          />
        )}

        {step === 2 && (
          <Step2PhonesAndKeywords
            phones={phones}
            selectedPhones={selectedPhones}
            setSelectedPhones={setSelectedPhones}
            keywordsText={keywordsText}
            setKeywordsText={setKeywordsText}
            keywordList={keywordList}
            keywordsOk={keywordsOk}
            t={t}
          />
        )}

        {step === 3 && (
          <Step3Comments
            candidatesPerPost={candidatesPerPost}
            setCandidatesPerPost={setCandidatesPerPost}
            commentMode={commentMode}
            setCommentMode={setCommentMode}
            prompts={prompts}
            commentPromptId={commentPromptId}
            setCommentPromptId={setCommentPromptId}
            providedComments={providedComments}
            setProvidedComments={setProvidedComments}
            providedList={providedList}
            providedOk={providedOk}
            t={t}
          />
        )}

        {/* Footer */}
        <div className="mt-5 flex items-center justify-between">
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <div className="flex gap-2">
            {step > 1 && (
              <Button variant="ghost" onClick={() => setStep((s) => (s - 1) as Step)}>
                {t('common.back')}
              </Button>
            )}
            {step < 3 && (
              <Button
                variant="primary"
                onClick={() => setStep((s) => (s + 1) as Step)}
                disabled={
                  (step === 1 && (targetCount === 0 || !allLinksLookOK)) ||
                  (step === 2 && (selectedPhones.size === 0 || !keywordsOk))
                }
              >
                {t('common.next')}
              </Button>
            )}
            {step === 3 && (
              <Button variant="primary" disabled={!canSubmit} onClick={submit}>
                {t('fbComments.submit')}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}

/* Step1Targets / Step2PhonesAndKeywords / Step3Comments — 简单子组件，
   完整代码由 agent 实现时类比 CommentModal.tsx 的 Step1Owned / Step2Phones /
   Step3Comments 子组件铺开。Step2 的 keywords 区域用 textarea：

   <textarea
     value={keywordsText}
     onChange={(e) => setKeywordsText(e.target.value)}
     placeholder={t('fbComments.keywordsPlaceholder')}
     rows={4}
     className="w-full border border-[var(--color-line)] rounded-[8px] p-2"
   />
   <div className="text-[11.5px] text-[var(--color-ink-4)] mt-1">
     {t('fbComments.keywordsHelp')}
     · {keywordList.length}/10
   </div>

   Step3 候选条数 selector：

   <label className="flex items-center gap-2">
     {t('fbComments.candidatesPerPost')}
     <input
       type="number" min={1} max={10}
       value={candidatesPerPost}
       onChange={(e) => setCandidatesPerPost(Math.max(1, Math.min(10, +e.target.value)))}
     />
   </label>
   <div className="text-[11.5px] text-[var(--color-ink-4)]">
     {t('fbComments.candidatesHelp')}
   </div>
*/
```


- [ ] **Step 2: 编译通过**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/FbCommentModal.tsx
git commit -m "feat(frontend): FbCommentModal 3-step wizard"
```

---

## Task 14: 前端 — `FbComments.tsx` 页面 + 路由 + 导航 + i18n

**Files:**
- Create: `frontend/src/pages/FbComments.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Shell.tsx`
- Modify: `frontend/src/i18n/locales/zh.json`、`en.json`

- [ ] **Step 1: 写页面（基于 `Comments.tsx` 派生）**

```tsx
// frontend/src/pages/FbComments.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  api,
  type EngagementRun,
  type EngagementRunResult,
  type EngagementTaskStatus,
} from '../api'
import type { ToastState } from '../App'
import { Button, Card, EmptyState, PageTitle } from '../components/ui'
import { FbCommentModal } from '../components/FbCommentModal'
import { cn } from '../lib/cn'

// 复用 Comments.tsx 的 parseBackendIso / relativeTime / isInflight /
// classifyStatus / dedupeAndSortByIdDesc / ACCENT_BAR / STATUS_TEXT /
// DOT_BG —— 由 agent 实现时拷贝（不抽公共模块）。

type StatusKey = 'all' | 'success' | 'running' | 'queued' | 'failed'
const PAGE_SIZE = 50

export default function FbComments({ onToast }: { onToast: (t: ToastState) => void }) {
  const { t } = useTranslation()
  const [records, setRecords] = useState<EngagementRun[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusKey>('all')
  const [modalOpen, setModalOpen] = useState(false)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  const loadFirstPage = useCallback(async () => {
    const r = await api.engagementRuns({
      limit: PAGE_SIZE,
      status: statusFilter === 'all' ? undefined : statusFilter,
      platform: 'facebook',
    })
    setRecords(dedupeAndSortByIdDesc(r.items))
    setExhausted(r.items.length < PAGE_SIZE)
  }, [statusFilter])

  const loadMore = useCallback(async () => {
    if (loadingMore || exhausted || records.length === 0) return
    setLoadingMore(true)
    try {
      const r = await api.engagementRuns({
        limit: PAGE_SIZE,
        beforeId: records[records.length - 1].id,
        status: statusFilter === 'all' ? undefined : statusFilter,
        platform: 'facebook',
      })
      setRecords((prev) => dedupeAndSortByIdDesc([...prev, ...r.items]))
      if (r.items.length < PAGE_SIZE) setExhausted(true)
    } finally {
      setLoadingMore(false)
    }
  }, [loadingMore, exhausted, records, statusFilter])

  useEffect(() => {
    setLoading(true); setExhausted(false)
    loadFirstPage().finally(() => setLoading(false))
  }, [loadFirstPage])

  const hasInflight = useMemo(() => records.some(isInflight), [records])
  useEffect(() => {
    if (!hasInflight) return
    const timer = window.setInterval(() => {
      api.engagementRuns({
        limit: PAGE_SIZE,
        status: statusFilter === 'all' ? undefined : statusFilter,
        platform: 'facebook',
      }).then((r) =>
        setRecords((prev) => dedupeAndSortByIdDesc([...r.items, ...prev])),
      ).catch(() => {})
    }, 5000)
    return () => window.clearInterval(timer)
  }, [hasInflight, statusFilter])

  useEffect(() => {
    const node = sentinelRef.current
    if (!node) return
    const obs = new IntersectionObserver(
      (es) => { if (es[0]?.isIntersecting) loadMore() },
      { rootMargin: '400px' },
    )
    obs.observe(node)
    return () => obs.disconnect()
  }, [loadMore])

  const onSubmit = async (payload: import('../api').FbCommentSubmitPayload) => {
    try {
      const r = await api.createFbEngagementComment(payload)
      onToast({
        msg: t('fbComments.created', { count: r.created_runs ?? 0 }),
        tone: 'green',
      })
      setModalOpen(false)
      setLoading(true); setExhausted(false)
      await loadFirstPage()
      setLoading(false)
    } catch (e) {
      const msg = e instanceof Error && e.message ? e.message : t('fbComments.failed')
      onToast({ msg, tone: 'red' })
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageTitle
        title={t('fbComments.title')}
        right={
          <Button variant="primary" onClick={() => setModalOpen(true)}>
            + {t('fbComments.newComment')}
          </Button>
        }
      />
      {/* 过滤器（仅状态，无 source — FB 端的 source 大多是 manual） */}
      {/* …复用 Comments.tsx 的 FilterMenu 视觉块… */}
      {loading
        ? <Card><EmptyState title={t('common.loading')} /></Card>
        : records.length === 0
          ? <Card><EmptyState title={t('fbComments.recordsEmpty')} /></Card>
          : <div className="flex flex-col gap-2.5">
              {records.map((r) => <FbRecordCard key={r.id} record={r} />)}
              <div ref={sentinelRef} className="h-px" />
            </div>}
      <FbCommentModal open={modalOpen} onClose={() => setModalOpen(false)} onSubmit={onSubmit} />
    </div>
  )
}

function FbRecordCard({ record }: { record: EngagementRun }) {
  const { t } = useTranslation()
  const stat = classifyStatus(record)
  const candidates = (record as any).comment_candidates as string[] | null
  const extras = candidates && candidates.length >= 2
    ? candidates.length - 1
    : 0
  // 复用 Comments.tsx 的 RecordCard 视觉，仅在 record.comment_text 后追加：
  // {extras > 0 && (
  //   <span className="ml-2 text-[var(--color-ink-5)]">+{extras} {t('fbComments.candidatesExtra')}</span>
  // )}
  return (/* …同 Comments.tsx RecordCard… */ null as any)
}
```

> Agent 实现时应**完整拷贝** `Comments.tsx` 的 `RecordCard` 子组件，加 `+N 候选` 标识，并把"视频" → "帖子"文案换掉。

- [ ] **Step 2: 加路由 `App.tsx`**

在 `<Route path="/comments" ... />` 行下面加：

```tsx
import FbComments from './pages/FbComments'

// ...

<Route
  path="/comments/facebook"
  element={<FbComments onToast={setToast} />}
/>
```

- [ ] **Step 3: 加导航 `Shell.tsx`**

`Tab` 类型加 `'fbComments'`；`tabs` 数组在 `comments` 后插入：

```tsx
{ id: 'fbComments', path: '/comments/facebook', icon: <FbCommentsIcon /> },
```

新增 `FbCommentsIcon`（与 `CommentsIcon` 风格一致，可同图标加 fb 角标，或直接复用 CommentsIcon）。

`shell.fbComments` i18n 条目加到 zh / en：

```jsonc
// zh.json
"shell": {
  ...
  "comments": "TikTok 评论",
  "fbComments": "Facebook 评论",
  ...
}
// en.json
"shell": {
  ...
  "comments": "TikTok comments",
  "fbComments": "Facebook comments",
  ...
}
```

- [ ] **Step 4: 加 i18n keys**

在 `frontend/src/i18n/locales/zh.json` 顶层加 `fbComments` 命名空间：

```jsonc
"fbComments": {
  "title": "Facebook 评论",
  "newComment": "新建评论任务",
  "stepLabel": "第 {{current}} 步 / 共 {{total}} 步",
  "targetIsPost": "帖子",
  "pickFbPost": "选择 FB 帖子",
  "manualUrl": "手动输入 FB 帖子链接",
  "keywords": "关键词",
  "keywordsPlaceholder": "一行一个，最多 10 个，每个 ≤ 100 字",
  "keywordsHelp": "GeeLark 在帖子内匹配这些关键词后再发布评论。语义以 GeeLark 行为为准。",
  "candidatesPerPost": "每帖候选评论数",
  "candidatesHelp": "GeeLark 会从候选池里随机挑一条发布。",
  "candidatesExtra": "条候选",
  "submit": "提交",
  "created": "已创建 {{count}} 个评论任务",
  "failed": "创建失败",
  "recordsEmpty": "暂无 Facebook 评论记录",
  "statusSuccess": "成功",
  "statusFailed": "失败",
  "statusRunning": "进行中",
  "statusQueued": "排队中",
  "statusPending": "等待中"
}
```

英文同步对照（labels：`Facebook Comments / New Comment Task / Step {{current}} of {{total}} / Post / Pick FB post / Manual FB post URL / Keywords / one per line, max 10, ≤100 chars / GeeLark uses these in-post to decide whether/when to comment. Behaviour is GeeLark-defined. / Candidates per post / GeeLark picks one from the pool. / candidates / Submit / Created {{count}} comment tasks / Failed to create / No Facebook comments yet`）。

- [ ] **Step 5: 编译 + 视觉冒烟**

```bash
cd frontend && npx tsc --noEmit && npm run build
```

Expected: build 成功

```bash
cd .. && ./run.sh &
sleep 6
curl -s http://localhost:8080/comments/facebook -o /dev/null -w "%{http_code}\n"
# 200 = SPA HTML 加载 OK
kill %1
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/FbComments.tsx \
        frontend/src/App.tsx frontend/src/components/Shell.tsx \
        frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
git commit -m "feat(frontend): FB Comments page + routing + nav + i18n"
```

---

## Task 15: 跑全套测试 + 前端构建 + 视觉 smoke

**Files:** none — verification step

- [ ] **Step 1: 后端全套测试**

```bash
uv run pytest -q
```

Expected: 全 PASS（基线 385+，新增至少 ~25 条）

- [ ] **Step 2: 前端构建**

```bash
cd frontend && npm run build
```

Expected: build 成功，0 type error

- [ ] **Step 3: 启动 web + worker，手动点一下 UI**

终端 1：

```bash
./run.sh
```

终端 2：

```bash
uv run python -m procrastinate -a app.tasks.procrastinate_app worker --queues phones,default
```

浏览器：
- 打开 `http://localhost:8080`
- 左栏点 "Facebook 评论" → 进入空列表页
- 点 "新建评论任务"
- 三步：Manual URL → 填 FB 帖子链接（用一个真实的 FB 帖子 URL）；选一台已 FB 登录的测试机；填 1 条 keyword；填 1 条 provided 评论
- 提交 → toast 显示 "已创建 1 个评论任务"
- 列表出现一条 PENDING / QUEUED 卡片

> 注：如果没有真实 FB 登录的测试机，可手动改 DB：
> `UPDATE accounts SET facebook_logged_in=1, warmed_up=1 WHERE id=<test-acc>;`

- [ ] **Step 4: 若有失败/回归，回到对应 Task 修复**

- [ ] **Step 5: 无需 commit（仅验证）**

---

## Task 16: 部署到 aliyun + 生产烟雾测试

**Files:** none — deployment step

**前置**：本 PR 所有 commit 已 push 到 main（如需）或合并就绪。

- [ ] **Step 1: 打 tar**

按 `docs/production-deploy.md`：

```bash
# 在项目根
cd frontend && npm run build && cd ..
tar --exclude='./.git' --exclude='./data' --exclude='./node_modules' \
    --exclude='./frontend/node_modules' --exclude='./.venv' \
    -czf /tmp/mkt-deploy.tar.gz .
```

- [ ] **Step 2: SCP + 备份**

```bash
scp /tmp/mkt-deploy.tar.gz aliyun:/tmp/
ssh aliyun "docker exec svm-postgres pg_dump -U postgres mkt > /root/backup_pre_fb_comment_$(date +%Y%m%d).sql"
```

- [ ] **Step 3: 解包 + 升级 schema**

```bash
ssh aliyun '
  set -e
  cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  uv sync
  uv run alembic upgrade head
'
```

- [ ] **Step 4: 重启服务**

```bash
ssh aliyun '
  sudo systemctl restart mkt
  sudo systemctl restart mkt-worker
  sudo systemctl status mkt mkt-worker --no-pager | head -20
'
```

预期：两服务 `active (running)`

- [ ] **Step 5: 生产烟雾测试**

在浏览器登录 prod dashboard：
- 进 `/comments/facebook`
- 用 1 台已 FB 登录的真实账号 + 1 条真实 FB 帖子 URL（**注意 URL 长度 ≤128 字符；超长按 spec §11.1 处理**）
- 1 条 keyword + 1 条 provided 评论
- 提交 → 等 ~1 分钟 → 卡片转 INITIATED → 等 ~2-5 分钟 → SUCCESS or FAILURE
- 如果 FAILURE，看截图与 notes，对照 spec §11 已知风险定位

- [ ] **Step 6: 若失败，回滚预案见 spec §9.2**

代码回滚：
```bash
ssh aliyun '
  cd /opt/mkt
  git checkout <prev-commit>  # 注意 /opt/mkt 不是 git repo — 用上一版 tar
  sudo systemctl restart mkt mkt-worker
'
```

DB 不回滚（新列对旧代码透明）。

- [ ] **Step 7: 收尾**

若烟雾测试通过，记录：
- 一条真实 FB 评论 SUCCESS 的截图（spec §9.1 step 7）
- 真实 FB URL 长度 ↔ 是否触发 GeeLark 拒绝（决定后续是否放宽 spec §11.1 的 128 字校验）

---

## 全局检查清单

完成所有任务后：

- [ ] **新列对 prod 历史数据无影响**：抽查 `engagement_tasks` 几条历史行 `platform='tiktok' AND keywords='[]'::json`
- [ ] **TikTok Comments 页行为零回归**：dashboard `/comments` 仍工作，老评论卡片视觉正常
- [ ] **`uv run pytest -q` 全绿**
- [ ] **`cd frontend && npm run build` 全绿**
- [ ] **真实 FB 评论烟雾通过**
- [ ] **PR 描述链接 spec + plan + 一句话总结 risk §11.1 真实结果**
