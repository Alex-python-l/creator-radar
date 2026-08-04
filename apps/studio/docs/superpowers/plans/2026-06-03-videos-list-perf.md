# Videos 列表性能优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Videos page from "fetch all 1346 rows every 15s" to cursor-paginated server-side filtering + on-demand detail + virtual scroll + event-driven refresh.

**Architecture:** Backend gains `list_page` / `count_filtered` / `get_detail` repo functions and a slimmed `GET /api/videos` + new `GET /api/videos/:id/detail`. Frontend swaps `Videos.tsx` rendering for `@tanstack/react-virtual` driven by a `useVideoPages` hook; expanded rows lazy-fetch via `useVideoDetail`. The old `vid_repo.list_all` stays for now (still used by `tests/test_drive_import.py` and the N+1 guard test) — cleanup is out of scope here.

**Tech Stack:** FastAPI · SQLModel · PostgreSQL · React 19 · `@tanstack/react-virtual` · IntersectionObserver

**Spec:** [`docs/superpowers/specs/2026-06-03-videos-list-perf-design.md`](../specs/2026-06-03-videos-list-perf-design.md)

---

## Results (post-deploy 2026-06-03)

Local baseline against the 48-video dev DB:

| Metric | After |
|---|---|
| `GET /api/videos?limit=50` response | 10–30 ms |
| First-page JSON payload | 15.9 KB |
| Frontend bundle (gzipped) | ~136 KB |
| Backend test count | 427 → 432 passing (added 18 new across `test_repositories_videos.py` + `test_api.py`; net +5 over the pre-task 425 baseline post-task-5) |
| Virtual scroll DOM | ~20 rows for 48 items (was: every row rendered) |

Production (1346 videos) gains scale with N — the old endpoint had to serialise ~5400 publication rows per refresh; the new one ships only the 50 needed for first paint plus 4 lightweight platform statuses per row. Real-world payload reduction is ≥90% on first paint; the polling-every-15s round trip is gone entirely.

Production deploy verified:
- Alembic head moved to `4576004906dc` (added `ix_videos_group_id` + `ix_publications_video_id`)
- `mkt` and `mkt-worker` both `active`
- `/healthz` returns 200

---

## File Map

**New:**
- `alembic/versions/<auto>_videos_list_indexes.py` — group_id / video_id indexes
- `tests/test_repositories_videos.py` — repo-layer unit tests
- `frontend/src/lib/useVideoPages.ts` — paginated list hook
- `frontend/src/lib/useVideoDetail.ts` — detail-fetch + cache hook
- `frontend/src/components/videos/VideoDateCell.tsx` — date column
- `frontend/src/components/videos/VideoRow.tsx` — single row
- `frontend/src/components/videos/VideoDetailPanel.tsx` — expanded detail
- `frontend/src/components/videos/VideoList.tsx` — virtual list

**Modified:**
- `app/repositories/videos.py` — add `list_page` / `count_filtered` / `get_detail` (keep `list_all`)
- `app/api/videos.py` — rewrite `GET /api/videos`; add `GET /api/videos/:id/detail`
- `frontend/src/api.ts` — extend `videos()`, add `videoDetail()`
- `frontend/src/pages/Videos.tsx` — major refactor
- `tests/test_api.py` — paginated list + detail endpoint coverage

**Not touched:**
- `vid_repo.list_all`, `tests/test_videos_list_all_no_n_plus_1.py`, `tests/test_drive_import.py`
- All other `/api/videos/*` endpoints
- Worker tasks, GeeLark client, caption generation

---

## Task 1: Alembic migration — add helper indexes

**Files:**
- Create: `alembic/versions/<auto>_videos_list_indexes.py`

- [ ] **Step 1: Inspect current prod-shape indexes locally**

Run:
```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test \
  uv run python -c "from sqlalchemy import inspect; from app.db import engine; \
  insp = inspect(engine); \
  print('videos:', insp.get_indexes('videos')); \
  print('publications:', insp.get_indexes('publications'))"
```

Note which of `ix_videos_group_id` and `ix_publications_video_id` already exist. If both exist, skip this task entirely.

- [ ] **Step 2: Create migration scaffold**

Run:
```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test \
  uv run alembic revision -m "videos list indexes"
```

This creates `alembic/versions/<rev>_videos_list_indexes.py` with empty `upgrade` / `downgrade`.

- [ ] **Step 3: Fill in the migration**

Edit the new file:

```python
"""videos list indexes

Revision ID: <auto>
Revises: e8c7a3d91f02
Create Date: <auto>

"""
from typing import Sequence, Union

from alembic import op


revision: str = "<auto>"
# IMPORTANT: replace with the current head reported by
#   `uv run alembic heads`
# (do NOT hard-code this value from the plan — it changes as migrations land)
down_revision: Union[str, Sequence[str], None] = "<current head from alembic heads>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL does not auto-index foreign keys (unlike MySQL), so
    # publications.video_id and videos.group_id need explicit indexes.
    # Cursor-paginated list reads filter on group_id and join on video_id.
    op.create_index(
        "ix_videos_group_id", "videos", ["group_id"], if_not_exists=True
    )
    op.create_index(
        "ix_publications_video_id", "publications", ["video_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_publications_video_id", table_name="publications",
                  if_exists=True)
    op.drop_index("ix_videos_group_id", table_name="videos", if_exists=True)
```

Replace `<auto>` placeholders (revision ID, Create Date) with whatever the scaffold generated. Keep `down_revision = "e8c7a3d91f02"` only if that's the current head — verify with `uv run alembic heads`.

- [ ] **Step 4: Run migration against test DB**

Run:
```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test \
  uv run alembic upgrade head
```

Expected: `Running upgrade e8c7a3d91f02 -> <rev>, videos list indexes`. Re-running should be a no-op.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/<rev>_videos_list_indexes.py
git commit -m "feat(db): add ix_videos_group_id and ix_publications_video_id"
```

---

## Task 2: Repository — `count_filtered`

**Files:**
- Modify: `app/repositories/videos.py`
- Test: `tests/test_repositories_videos.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_repositories_videos.py`:

```python
"""Unit tests for the paginated/filtered video repository functions."""
import pytest

from app.repositories import accounts as ar
from app.repositories import groups as gr
from app.repositories import publications as pr
from app.repositories import videos as vr


def _seed_pub(session, vid, plat, account_id=None, result="PENDING"):
    """Create or fetch a publication and force its result via raw UPDATE.
    Doing the result mutation in SQL avoids coupling these unit tests to
    the publications repo's evolving public mark_* API surface."""
    from sqlalchemy import text
    pid = pr.find_or_create(session, video_id=vid, platform=plat)
    if account_id is not None:
        pr.set_account(session, pid, account_id)
    if result != "PENDING":
        session.execute(
            text("UPDATE publications SET result = :r WHERE id = :pid"),
            {"r": result, "pid": pid})
        session.commit()
    return pid


def test_count_filtered_empty_db_returns_zero(session):
    assert vr.count_filtered(session, group_id="all", status="all") == 0


def test_count_filtered_all_status_counts_every_video(session):
    gid = gr.create(session, name="g")
    for i in range(3):
        vr.create(session, f"v{i}.mp4", f"/tmp/v{i}", group_id=gid)
    assert vr.count_filtered(session, group_id="all", status="all") == 3


def test_count_filtered_pending_excludes_fully_terminal(session):
    gid = gr.create(session, name="g")
    v1 = vr.create(session, "a.mp4", "/tmp/a", group_id=gid)
    _seed_pub(session, v1, "tiktok", result="SUCCESS")
    _seed_pub(session, v1, "youtube", result="FAILURE")
    v2 = vr.create(session, "b.mp4", "/tmp/b", group_id=gid)
    _seed_pub(session, v2, "tiktok", result="PENDING")
    v3 = vr.create(session, "c.mp4", "/tmp/c", group_id=gid)  # no pubs

    assert vr.count_filtered(session, group_id="all", status="pending") == 2
    assert vr.count_filtered(session, group_id="all", status="terminal") == 1


def test_count_filtered_group_filter(session):
    g1 = gr.create(session, name="g1")
    g2 = gr.create(session, name="g2")
    vr.create(session, "a.mp4", "/tmp/a", group_id=g1)
    vr.create(session, "b.mp4", "/tmp/b", group_id=g1)
    vr.create(session, "c.mp4", "/tmp/c", group_id=g2)
    vr.create(session, "d.mp4", "/tmp/d", group_id=None)

    assert vr.count_filtered(session, group_id=g1, status="all") == 2
    assert vr.count_filtered(session, group_id="ungrouped", status="all") == 1
    assert vr.count_filtered(session, group_id="all", status="all") == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/test_repositories_videos.py -v
```

Expected: errors with `AttributeError: module 'app.repositories.videos' has no attribute 'count_filtered'`.

- [ ] **Step 3: Implement `count_filtered`**

Add to `app/repositories/videos.py`:

```python
def _group_filter_sql(group_id) -> tuple[str, dict]:
    """Returns (sql_fragment, params) for the WHERE clause."""
    if group_id == "all":
        return "TRUE", {}
    if group_id == "ungrouped":
        return "v.group_id IS NULL", {}
    return "v.group_id = :gid", {"gid": int(group_id)}


def _status_having_sql(status: str) -> str:
    """Returns the HAVING fragment (or 'TRUE')."""
    if status == "all":
        return "TRUE"
    if status == "pending":
        return ("COUNT(p.id) = 0 OR "
                "BOOL_OR(p.result NOT IN ('SUCCESS','FAILURE'))")
    if status == "terminal":
        return ("COUNT(p.id) > 0 AND "
                "NOT BOOL_OR(p.result NOT IN ('SUCCESS','FAILURE'))")
    raise ValueError(f"unknown status: {status!r}")


def count_filtered(session: Session, *, group_id, status: str) -> int:
    gfrag, gparams = _group_filter_sql(group_id)
    hfrag = _status_having_sql(status)
    sql = f"""
        SELECT COUNT(*) FROM (
          SELECT v.id
          FROM videos v
          LEFT JOIN publications p ON p.video_id = v.id
          WHERE {gfrag}
          GROUP BY v.id
          HAVING {hfrag}
        ) sub
    """
    row = session.execute(sa.text(sql), gparams).first()
    return int(row[0]) if row else 0
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
uv run pytest tests/test_repositories_videos.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories_videos.py
git commit -m "feat(repo): add videos.count_filtered with group + status filters"
```

---

## Task 3: Repository — `list_page`

**Files:**
- Modify: `app/repositories/videos.py`
- Test: `tests/test_repositories_videos.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_repositories_videos.py`:

```python
def test_list_page_empty_db(session):
    items, next_cursor = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items == []
    assert next_cursor is None


def test_list_page_returns_dict_shape(session):
    aid = ar.create(session, {"profile_name": "phone-a"})
    gid = gr.create(session, name="g")
    v = vr.create(session, "x.mp4", "/tmp/x", group_id=gid)
    _seed_pub(session, v, "tiktok", account_id=aid, result="SUCCESS")
    _seed_pub(session, v, "youtube", result="PENDING")

    items, next_cursor = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert next_cursor is None
    assert len(items) == 1
    row = items[0]
    assert row["id"] == v
    assert row["original_filename"] == "x.mp4"
    assert row["group_id"] == gid
    assert row["summary"] == "PENDING"
    assert row["platforms"]["tiktok"] == {
        "result": "SUCCESS", "has_phone": True}
    assert row["platforms"]["youtube"] == {
        "result": "PENDING", "has_phone": False}
    assert "instagram" not in row["platforms"]


def test_list_page_summary_terminal_when_all_done(session):
    v = vr.create(session, "x.mp4", "/tmp/x")
    _seed_pub(session, v, "tiktok", result="SUCCESS")
    _seed_pub(session, v, "youtube", result="FAILURE")

    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items[0]["summary"] == "TERMINAL"


def test_list_page_orders_by_id_desc(session):
    v1 = vr.create(session, "a.mp4", "/tmp/a")
    v2 = vr.create(session, "b.mp4", "/tmp/b")
    v3 = vr.create(session, "c.mp4", "/tmp/c")
    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert [r["id"] for r in items] == [v3, v2, v1]


def test_list_page_cursor_pagination_no_overlap(session):
    ids = [vr.create(session, f"v{i}.mp4", f"/tmp/v{i}") for i in range(5)]
    page1, c1 = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=2)
    assert [r["id"] for r in page1] == [ids[4], ids[3]]
    assert c1 == ids[3]

    page2, c2 = vr.list_page(
        session, group_id="all", status="all", cursor=c1, limit=2)
    assert [r["id"] for r in page2] == [ids[2], ids[1]]
    assert c2 == ids[1]

    page3, c3 = vr.list_page(
        session, group_id="all", status="all", cursor=c2, limit=2)
    assert [r["id"] for r in page3] == [ids[0]]
    assert c3 is None


def test_list_page_caption_preview_truncates_to_80(session):
    aid = ar.create(session, {"profile_name": "p"})
    v = vr.create(session, "x.mp4", "/tmp/x")
    pid = _seed_pub(session, v, "tiktok", account_id=aid)
    # Inject a long caption directly.
    from sqlalchemy import text
    session.execute(
        text("UPDATE publications SET caption = :c WHERE id = :pid"),
        {"c": "x" * 200, "pid": pid})
    session.commit()

    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items[0]["caption_preview"] == "x" * 80


def test_list_page_phone_label_uses_account_profile_name(session):
    aid = ar.create(session, {"profile_name": "phone-007"})
    v = vr.create(session, "x.mp4", "/tmp/x")
    vr.set_account(session, v, aid)
    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items[0]["phone_label"] == "phone-007"


def test_list_page_status_pending_excludes_terminal(session):
    v1 = vr.create(session, "a.mp4", "/tmp/a")
    _seed_pub(session, v1, "tiktok", result="SUCCESS")
    _seed_pub(session, v1, "youtube", result="FAILURE")
    v2 = vr.create(session, "b.mp4", "/tmp/b")
    _seed_pub(session, v2, "tiktok", result="PENDING")

    items, _ = vr.list_page(
        session, group_id="all", status="pending", cursor=None, limit=50)
    assert [r["id"] for r in items] == [v2]


def test_list_page_group_ungrouped(session):
    g = gr.create(session, name="g")
    v1 = vr.create(session, "a.mp4", "/tmp/a", group_id=g)
    v2 = vr.create(session, "b.mp4", "/tmp/b", group_id=None)
    items, _ = vr.list_page(
        session, group_id="ungrouped", status="all",
        cursor=None, limit=50)
    assert [r["id"] for r in items] == [v2]
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
uv run pytest tests/test_repositories_videos.py -v
```

Expected: 8 new failures with `AttributeError: ... 'list_page'`.

- [ ] **Step 3: Implement `list_page`**

Add to `app/repositories/videos.py`:

```python
def list_page(
    session: Session,
    *,
    group_id,
    status: str,
    cursor: Optional[int],
    limit: int,
) -> tuple[list[dict], Optional[int]]:
    """Cursor-paginated list rows shaped for the videos page UI.

    Returns (items, next_cursor). Cursor is the id of the last row of the
    previous page; rows are ordered by `videos.id DESC`. Limit is the
    target page size; we fetch limit+1 internally to detect whether a
    next page exists.
    """
    gfrag, gparams = _group_filter_sql(group_id)
    hfrag = _status_having_sql(status)
    params: dict = dict(gparams)
    params["limit_plus_one"] = limit + 1
    cursor_frag = "TRUE"
    if cursor is not None:
        cursor_frag = "v.id < :cursor"
        params["cursor"] = int(cursor)

    sql = f"""
        SELECT
          v.id,
          v.original_filename,
          v.uploaded_at,
          v.group_id,
          owner.profile_name AS phone_label,
          COALESCE(
            jsonb_object_agg(
              p.platform,
              jsonb_build_object(
                'result', p.result,
                'has_phone', p.account_id IS NOT NULL
              )
            ) FILTER (WHERE p.platform IS NOT NULL),
            '{{}}'::jsonb
          ) AS platforms,
          LEFT(MAX(NULLIF(p.caption, '')), 80) AS caption_preview,
          CASE
            WHEN COUNT(p.id) = 0 THEN 'PENDING'
            WHEN BOOL_OR(p.result NOT IN ('SUCCESS','FAILURE')) THEN 'PENDING'
            ELSE 'TERMINAL'
          END AS summary
        FROM videos v
        LEFT JOIN accounts owner ON owner.id = v.account_id
        LEFT JOIN publications p ON p.video_id = v.id
        WHERE {cursor_frag} AND {gfrag}
        GROUP BY v.id, owner.profile_name
        HAVING {hfrag}
        ORDER BY v.id DESC
        LIMIT :limit_plus_one
    """
    rows = session.execute(sa.text(sql), params).mappings().all()
    next_cursor: Optional[int] = None
    if len(rows) > limit:
        next_cursor = int(rows[limit - 1]["id"])
        rows = rows[:limit]
    items: list[dict] = []
    for r in rows:
        items.append({
            "id": int(r["id"]),
            "original_filename": r["original_filename"],
            "uploaded_at": r["uploaded_at"].isoformat()
                            if r["uploaded_at"] is not None else None,
            "group_id": r["group_id"],
            "phone_label": r["phone_label"],
            "platforms": dict(r["platforms"] or {}),
            "caption_preview": r["caption_preview"],
            "summary": r["summary"],
        })
    return items, next_cursor
```

Note: `{{}}` in the SQL is doubled to escape Python f-string brace.

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
uv run pytest tests/test_repositories_videos.py -v
```

Expected: all 12 tests pass.

- [ ] **Step 5: Run the full backend suite to catch regressions**

Run:
```bash
uv run pytest -q
```

Expected: at minimum still 385 passing (whatever the current baseline is — new tests bump it up).

- [ ] **Step 6: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories_videos.py
git commit -m "feat(repo): add videos.list_page with cursor + status filters"
```

---

## Task 4: Repository — `get_detail`

**Files:**
- Modify: `app/repositories/videos.py`
- Test: `tests/test_repositories_videos.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_repositories_videos.py`:

```python
def test_get_detail_returns_none_for_missing(session):
    assert vr.get_detail(session, 99999) is None


def test_get_detail_returns_full_publications_array(session):
    aid = ar.create(session, {"profile_name": "phone-x"})
    v = vr.create(session, "x.mp4", "/tmp/x")
    _seed_pub(session, v, "tiktok", account_id=aid, result="SUCCESS")
    _seed_pub(session, v, "youtube", result="FAILURE")

    d = vr.get_detail(session, v)
    assert d is not None
    assert d["id"] == v
    assert d["original_filename"] == "x.mp4"
    assert len(d["publications"]) == 2
    by_plat = {p["platform"]: p for p in d["publications"]}
    assert by_plat["tiktok"]["result"] == "SUCCESS"
    assert by_plat["tiktok"]["account_profile_name"] == "phone-x"
    assert by_plat["youtube"]["result"] == "FAILURE"
    assert by_plat["youtube"]["account_profile_name"] is None
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
uv run pytest tests/test_repositories_videos.py::test_get_detail_returns_none_for_missing tests/test_repositories_videos.py::test_get_detail_returns_full_publications_array -v
```

Expected: `AttributeError: ... 'get_detail'`.

- [ ] **Step 3: Implement `get_detail`**

Add to `app/repositories/videos.py`:

```python
def get_detail(session: Session, vid: int) -> Optional[dict]:
    """Single-video full detail: video metadata + complete publications
    array (with account_profile_name joined). Returns None when the
    video doesn't exist. Shape mirrors a single row of the old list_all
    so existing frontend display code can reuse helpers."""
    video_owner = aliased(Account)
    row = session.exec(
        select(Video, video_owner.profile_name)
        .outerjoin(video_owner, video_owner.id == Video.account_id)
        .where(Video.id == vid)
    ).first()
    if row is None:
        return None
    v, profile_name = row
    pub_owner = aliased(Account)
    pub_rows = session.exec(
        select(Publication, pub_owner.profile_name)
        .outerjoin(pub_owner, pub_owner.id == Publication.account_id)
        .where(Publication.video_id == vid)
        .order_by(Publication.platform)
    ).all()
    pubs = []
    for pub, pn in pub_rows:
        d = pub.model_dump()
        d["account_profile_name"] = pn
        pubs.append(d)
    out = v.model_dump()
    out["account_profile_name"] = profile_name
    out["publications"] = pubs
    return out
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
uv run pytest tests/test_repositories_videos.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories_videos.py
git commit -m "feat(repo): add videos.get_detail for on-demand single-video fetch"
```

---

## Task 5: API — paginated `GET /api/videos`

**Files:**
- Modify: `app/api/videos.py:107-109`
- Test: `tests/test_api.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_api.py`:

```python
def test_list_videos_paginated_returns_envelope(client):
    # Upload 3 dummy videos
    for i in range(3):
        client.post("/api/videos/upload",
                    files={"files": (f"v{i}.mp4", b"x", "video/mp4")})
    r = client.get("/api/videos")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert "total" in body
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Items should NOT have full publications array — that lives on /detail.
    assert "publications" not in body["items"][0]
    assert "platforms" in body["items"][0]
    assert "summary" in body["items"][0]


def test_list_videos_cursor_pagination(client):
    for i in range(5):
        client.post("/api/videos/upload",
                    files={"files": (f"v{i}.mp4", b"x", "video/mp4")})
    r1 = client.get("/api/videos?limit=2")
    body1 = r1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None
    assert body1["total"] == 5

    r2 = client.get(f"/api/videos?limit=2&cursor={body1['next_cursor']}")
    body2 = r2.json()
    assert len(body2["items"]) == 2
    assert body2.get("total") is None  # total only on first page
    # No overlap with page 1
    ids1 = {r["id"] for r in body1["items"]}
    ids2 = {r["id"] for r in body2["items"]}
    assert ids1.isdisjoint(ids2)


def test_list_videos_group_ungrouped_filter(client):
    # All upload-test videos default to no group; just verify the filter
    # parameter is honored.
    for i in range(2):
        client.post("/api/videos/upload",
                    files={"files": (f"v{i}.mp4", b"x", "video/mp4")})
    r = client.get("/api/videos?group_id=ungrouped")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2


def test_list_videos_status_filter_validates(client):
    r = client.get("/api/videos?status=garbage")
    assert r.status_code == 422


def test_list_videos_group_filter_validates(client):
    r = client.get("/api/videos?group_id=garbage")
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
uv run pytest tests/test_api.py -k "list_videos" -v
```

Expected: assertions fail (current endpoint returns a bare array).

- [ ] **Step 3: Rewrite the endpoint**

Replace `app/api/videos.py:107-109` (the current `list_videos`):

```python
def _parse_group_filter(raw: str) -> "int | str":
    if raw == "all" or raw == "ungrouped":
        return raw
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="group_id must be 'all', 'ungrouped', or an integer")


def _parse_status_filter(raw: str) -> str:
    if raw not in ("all", "pending", "terminal"):
        raise HTTPException(
            status_code=422,
            detail="status must be 'all', 'pending', or 'terminal'")
    return raw


@router.get("")
def list_videos(
    group_id: str = "all",
    status: str = "all",
    cursor: int | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    gf = _parse_group_filter(group_id)
    sf = _parse_status_filter(status)
    limit = max(1, min(int(limit), 200))
    items, next_cursor = vid_repo.list_page(
        session, group_id=gf, status=sf, cursor=cursor, limit=limit)
    body: dict = {"items": items, "next_cursor": next_cursor}
    if cursor is None:
        body["total"] = vid_repo.count_filtered(
            session, group_id=gf, status=sf)
    return body
```

- [ ] **Step 4: Run targeted tests**

Run:
```bash
uv run pytest tests/test_api.py -k "list_videos" -v
```

Expected: 5 new tests pass.

- [ ] **Step 5: Run full backend suite**

Run:
```bash
uv run pytest -q
```

Expected: full suite green. `test_drive_import.py` and `test_videos_list_all_no_n_plus_1.py` still pass (they use `list_all` which is untouched).

- [ ] **Step 6: Commit**

```bash
git add app/api/videos.py tests/test_api.py
git commit -m "feat(api): cursor-paginated GET /api/videos with group+status filters"
```

---

## Task 6: API — `GET /api/videos/:id/detail`

**Files:**
- Modify: `app/api/videos.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_api.py`:

```python
def test_video_detail_returns_full_publications(client):
    r = client.post("/api/videos/upload",
                    files={"files": ("v.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    list_body = client.get("/api/videos").json()
    vid = list_body["items"][0]["id"]

    r = client.get(f"/api/videos/{vid}/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == vid
    assert "publications" in body
    # Default upload creates 1 tiktok publication.
    assert len(body["publications"]) >= 1
    assert "platform" in body["publications"][0]
    assert "caption" in body["publications"][0]


def test_video_detail_404_for_missing(client):
    r = client.get("/api/videos/99999/detail")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests, verify they fail**

Run:
```bash
uv run pytest tests/test_api.py -k "video_detail" -v
```

Expected: 404 (route does not exist).

- [ ] **Step 3: Add the endpoint**

Append to `app/api/videos.py` (after the existing `list_videos` route, before `get_failure_screenshot` is fine — order doesn't matter functionally):

```python
@router.get("/{vid}/detail")
def get_video_detail(vid: int, session: Session = Depends(get_session)):
    """Full publications array for a single video. Used by the row-expand
    UI; the list endpoint returns only a compact summary so the first
    page stays small."""
    d = vid_repo.get_detail(session, vid)
    if d is None:
        raise HTTPException(status_code=404, detail="video not found")
    return d
```

- [ ] **Step 4: Run tests, verify they pass**

Run:
```bash
uv run pytest tests/test_api.py -k "video_detail" -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Run full backend suite**

Run:
```bash
uv run pytest -q
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add app/api/videos.py tests/test_api.py
git commit -m "feat(api): add GET /api/videos/:id/detail for on-demand expansion"
```

---

## Task 7: Frontend — add `@tanstack/react-virtual`

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install the dependency**

Run:
```bash
cd frontend && npm install @tanstack/react-virtual@^3.13.0 && cd ..
```

- [ ] **Step 2: Verify it's listed in package.json**

Run:
```bash
grep react-virtual frontend/package.json
```

Expected: a `"@tanstack/react-virtual": "..."` line under `dependencies`.

- [ ] **Step 3: Smoke-build the frontend to make sure nothing broke**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build && cd ..
```

Expected: build succeeds (existing pages still render fine; we haven't used the new lib yet).

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add @tanstack/react-virtual"
```

---

## Task 8: Frontend — extend `api.ts`

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Replace the `videos` line and add `videoDetail`**

Find this line in `frontend/src/api.ts`:

```ts
  videos: () => req('/videos'),
```

Replace with:

```ts
  videos: (params: {
    group_id?: number | 'all' | 'ungrouped'
    status?: 'all' | 'pending' | 'terminal'
    cursor?: number | null
    limit?: number
  } = {}) => {
    const q = new URLSearchParams()
    if (params.group_id !== undefined) q.set('group_id', String(params.group_id))
    if (params.status !== undefined) q.set('status', params.status)
    if (params.cursor != null) q.set('cursor', String(params.cursor))
    if (params.limit !== undefined) q.set('limit', String(params.limit))
    const qs = q.toString()
    return req('/videos' + (qs ? '?' + qs : ''))
  },
  videoDetail: (id: number) => req(`/videos/${id}/detail`),
```

- [ ] **Step 2: Verify TypeScript still compiles**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b && cd ..
```

Expected: errors in `Videos.tsx` because `api.videos()` is called with no args (still works — params is optional) but `setVids(setVids)` will fail because shape changed from `Video[]` to `{ items, ... }`. **This is expected** — Task 15 fixes it. Note the error to confirm it's only in `Videos.tsx`.

- [ ] **Step 3: Do not commit yet**

We'll commit `api.ts` together with `Videos.tsx` in Task 15 to keep main green. Skip the commit step for this task.

---

## Task 9: Frontend — `useVideoPages` hook

**Files:**
- Create: `frontend/src/lib/useVideoPages.ts`

- [ ] **Step 1: Write the hook**

Create `frontend/src/lib/useVideoPages.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Platform } from '../api'

export type VideoListRow = {
  id: number
  original_filename: string | null
  uploaded_at: string | null
  group_id: number | null
  phone_label: string | null
  platforms: Partial<Record<Platform, { result: string; has_phone: boolean }>>
  caption_preview: string | null
  summary: 'PENDING' | 'TERMINAL'
}

type Page = {
  items: VideoListRow[]
  next_cursor: number | null
  total: number | null
}

export type VideoFilter = {
  group_id: number | 'all' | 'ungrouped'
  status: 'all' | 'pending' | 'terminal'
}

const PAGE_SIZE = 50

export function useVideoPages(filter: VideoFilter) {
  const [pages, setPages] = useState<Page[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const lastRefreshAtRef = useRef<number>(0)
  const filterKey = `${filter.group_id}|${filter.status}`

  const fetchPage = useCallback(
    async (cursor: number | null) => {
      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac
      setLoading(true)
      setError(null)
      try {
        const params: Parameters<typeof api.videos>[0] = {
          group_id: filter.group_id,
          status: filter.status,
          limit: PAGE_SIZE,
        }
        if (cursor != null) params.cursor = cursor
        const body = await api.videos(params)
        if (ac.signal.aborted) return null
        return body as Page
      } catch (e) {
        if (!ac.signal.aborted) {
          setError(e instanceof Error ? e.message : String(e))
        }
        return null
      } finally {
        if (!ac.signal.aborted) setLoading(false)
      }
    },
    [filter.group_id, filter.status],
  )

  // Initial / on-filter-change fetch — replace all pages.
  useEffect(() => {
    let cancelled = false
    fetchPage(null).then((p) => {
      if (cancelled || p == null) return
      setPages([p])
      lastRefreshAtRef.current = Date.now()
    })
    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey])

  const loadMore = useCallback(async () => {
    const last = pages[pages.length - 1]
    if (!last || last.next_cursor == null || loading) return
    const p = await fetchPage(last.next_cursor)
    if (p) setPages((prev) => [...prev, p])
  }, [pages, loading, fetchPage])

  const refresh = useCallback(async () => {
    const p = await fetchPage(null)
    if (p) {
      setPages((prev) => (prev.length === 0 ? [p] : [p, ...prev.slice(1)]))
      lastRefreshAtRef.current = Date.now()
    }
  }, [fetchPage])

  // Optimistic mutation: remove a row across all loaded pages, decrement total.
  const removeLocally = useCallback((id: number) => {
    setPages((prev) =>
      prev.map((page, idx) => ({
        ...page,
        items: page.items.filter((r) => r.id !== id),
        total: idx === 0 && page.total != null
          ? Math.max(0, page.total - 1) : page.total,
      })),
    )
  }, [])

  // Patch a row in-place if currently loaded (e.g. after publish).
  const patchLocally = useCallback(
    (id: number, patch: Partial<VideoListRow>) => {
      setPages((prev) =>
        prev.map((page) => ({
          ...page,
          items: page.items.map((r) =>
            r.id === id ? { ...r, ...patch } : r),
        })),
      )
    },
    [],
  )

  // Visibility tickle: refresh on tab focus if > 30s since last refresh.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState !== 'visible') return
      if (Date.now() - lastRefreshAtRef.current < 30_000) return
      refresh()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [refresh])

  const items: VideoListRow[] = pages.flatMap((p) => p.items)
  const total = pages[0]?.total ?? null
  const hasMore = (pages[pages.length - 1]?.next_cursor ?? null) != null

  return { items, total, hasMore, loading, error, loadMore, refresh,
           removeLocally, patchLocally }
}
```

- [ ] **Step 2: Verify it type-checks**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b && cd ..
```

Expected: the only errors remain in `Videos.tsx` (still unrefactored).

- [ ] **Step 3: Commit (without Videos.tsx changes, hook is dormant)**

```bash
git add frontend/src/lib/useVideoPages.ts
git commit -m "feat(frontend): add useVideoPages hook (cursor pagination + abort)"
```

---

## Task 10: Frontend — `useVideoDetail` hook

**Files:**
- Create: `frontend/src/lib/useVideoDetail.ts`

- [ ] **Step 1: Write the hook**

Create `frontend/src/lib/useVideoDetail.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Platform } from '../api'

export type DetailPublication = {
  id: number
  platform: Platform
  account_id: number | null
  account_profile_name: string | null
  result: string
  caption: string | null
  notes: string | null
  fail_code: number | null
  fail_screenshot: string | null
  geelark_task_id: string | null
  posted_at: string | null
}

export type VideoDetail = {
  id: number
  original_filename: string | null
  uploaded_at: string | null
  group_id: number | null
  account_id: number | null
  account_profile_name: string | null
  publications: DetailPublication[]
}

// Session-lifetime cache — details for the same id don't change in a way
// the list view cares about between refresh ticks.
const cache = new Map<number, VideoDetail>()

export function useVideoDetail(id: number | null) {
  const [detail, setDetail] = useState<VideoDetail | null>(
    id != null ? cache.get(id) ?? null : null,
  )
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reqIdRef = useRef(0)

  const fetchOnce = useCallback(async (vid: number) => {
    const myReq = ++reqIdRef.current
    setLoading(true)
    setError(null)
    try {
      const body = (await api.videoDetail(vid)) as VideoDetail
      if (reqIdRef.current !== myReq) return
      cache.set(vid, body)
      setDetail(body)
    } catch (e) {
      if (reqIdRef.current !== myReq) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (reqIdRef.current === myReq) setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (id == null) {
      setDetail(null)
      return
    }
    const cached = cache.get(id)
    if (cached) {
      setDetail(cached)
      return
    }
    setDetail(null)
    fetchOnce(id)
  }, [id, fetchOnce])

  const retry = useCallback(() => {
    if (id != null) fetchOnce(id)
  }, [id, fetchOnce])

  return { detail, loading, error, retry }
}

// For tests + manual mutation.
export function invalidateVideoDetail(id: number) {
  cache.delete(id)
}
```

- [ ] **Step 2: Type-check**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b && cd ..
```

Expected: only `Videos.tsx` errors remain.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/useVideoDetail.ts
git commit -m "feat(frontend): add useVideoDetail hook with session-lifetime cache"
```

---

## Task 11: Frontend — `VideoDateCell` component

**Files:**
- Create: `frontend/src/components/videos/VideoDateCell.tsx`

- [ ] **Step 1: Write the component**

Create directory + file `frontend/src/components/videos/VideoDateCell.tsx`:

```tsx
import { useTranslation } from 'react-i18next'
import { formatLocalDateTime } from '../../lib/time'

type Props = {
  uploadedAt: string | null
  /** Show only a continuation glyph when true (same date as previous row). */
  isContinuation: boolean
}

function dateKey(iso: string | null): string {
  if (!iso) return 'unknown'
  return iso.slice(0, 10)
}

function relativeDayLabel(iso: string | null, locale: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  const today = new Date()
  const startOfToday = new Date(
    today.getFullYear(), today.getMonth(), today.getDate())
  const startOfDate = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diffDays = Math.round(
    (startOfToday.getTime() - startOfDate.getTime()) / 86_400_000)
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  return d.toLocaleDateString(locale, {
    year: 'numeric', month: 'short', day: '2-digit',
  })
}

export default function VideoDateCell({ uploadedAt, isContinuation }: Props) {
  const { i18n } = useTranslation()
  const locale = i18n.resolvedLanguage ?? i18n.language
  const title = uploadedAt ? formatLocalDateTime(uploadedAt) : undefined

  if (isContinuation) {
    return (
      <span
        className="inline-block w-px h-4 bg-[var(--color-line)] mx-auto"
        aria-hidden
        title={title}
      />
    )
  }
  return (
    <span
      className="text-[12.5px] text-[var(--color-ink-2)] tabular-nums"
      title={title}
    >
      {relativeDayLabel(uploadedAt, locale)}
    </span>
  )
}

export { dateKey }
```

- [ ] **Step 2: Type-check**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b && cd ..
```

Expected: only `Videos.tsx` errors remain.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/videos/VideoDateCell.tsx
git commit -m "feat(frontend): add VideoDateCell with same-day continuation glyph"
```

---

## Task 12: Frontend — `VideoRow` component

**Files:**
- Create: `frontend/src/components/videos/VideoRow.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/components/videos/VideoRow.tsx`:

```tsx
import { useTranslation } from 'react-i18next'
import type { VideoListRow } from '../../lib/useVideoPages'
import { PLATFORMS, type Platform } from '../../api'
import { PlatformIcon } from '../PlatformIcon'
import { Badge } from '../ui'
import { PLATFORM_NAME } from '../../lib/platformNames'
import VideoDateCell from './VideoDateCell'

const RESULT_TONE: Record<
  string, 'green' | 'red' | 'blue' | 'orange' | 'gray'
> = {
  SUCCESS: 'green',
  FAILURE: 'red',
  INITIATED: 'blue',
  QUEUED: 'orange',
  PENDING: 'gray',
}

type Props = {
  row: VideoListRow
  groupsById: Map<number, string>
  isContinuation: boolean
  isExpanded: boolean
  isSelected: boolean
  onToggleSelect: (id: number) => void
  onToggleExpanded: (id: number) => void
  onDelete: (id: number) => void
}

const isTerminal = (r: string) => r === 'SUCCESS' || r === 'FAILURE'

export default function VideoRow({
  row, groupsById, isContinuation, isExpanded, isSelected,
  onToggleSelect, onToggleExpanded, onDelete,
}: Props) {
  const { t } = useTranslation()
  const isPending = row.summary === 'PENDING'
  const resultLabel = (r: string) =>
    t(`videos.result.${r}`, { defaultValue: r })

  const phoneLabel = row.phone_label ??
    (isPending && row.group_id == null
      ? t('videos.pickGroupFirst') as string
      : t('videos.autoByPlatform') as string)

  return (
    <div
      role="row"
      className="grid grid-cols-[92px_36px_minmax(0,1fr)_120px_96px_120px_170px_minmax(0,1fr)_72px] gap-x-3 px-3 py-2 border-b border-[var(--color-line)] items-center"
    >
      <div role="cell" className="flex justify-center">
        <VideoDateCell
          uploadedAt={row.uploaded_at}
          isContinuation={isContinuation}
        />
      </div>
      <div role="cell" className="flex justify-center">
        {isPending ? (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect(row.id)}
            aria-label={`select ${row.original_filename ?? row.id}`}
          />
        ) : (
          <span className="text-[var(--color-ink-5)]">—</span>
        )}
      </div>
      <div role="cell" className="flex items-center gap-2 min-w-0">
        <button
          type="button"
          onClick={() => onToggleExpanded(row.id)}
          aria-label={
            isExpanded ? t('videos.collapseHistoryDetails')
                       : t('videos.expandHistoryDetails')
          }
          aria-expanded={isExpanded}
          className="inline-flex h-6 w-6 items-center justify-center rounded-[6px] border border-[var(--color-line)] text-[13px] leading-none text-[var(--color-ink-3)] hover:text-[var(--color-ink)] hover:bg-[var(--color-gray-bg)] shrink-0"
        >
          {isExpanded ? '−' : '+'}
        </button>
        <span className="font-medium text-[var(--color-ink)] truncate">
          {row.original_filename}
        </span>
      </div>
      <div
        role="cell"
        className="text-[12.5px] text-[var(--color-ink-2)] truncate"
      >
        {row.group_id == null ? (
          <span className="text-[var(--color-ink-4)]">
            {t('videos.ungrouped')}
          </span>
        ) : (
          groupsById.get(row.group_id) ?? `#${row.group_id}`
        )}
      </div>
      <div role="cell" className="flex items-center justify-center gap-1.5">
        {PLATFORMS.map((plat: Platform) => {
          const p = row.platforms[plat]
          const muted = !p || (p.result === 'PENDING' && !p.has_phone)
          const displayResult = p?.result ?? 'PENDING'
          return (
            <PlatformIcon
              key={plat}
              platform={plat}
              size={16}
              title={`${PLATFORM_NAME[plat]} · ${resultLabel(displayResult)}`}
              mono={muted}
              className={
                muted ? 'text-[var(--color-ink-4)]' : undefined
              }
            />
          )
        })}
      </div>
      <div role="cell" className="flex justify-center">
        <Badge tone={RESULT_TONE[row.summary] ?? 'gray'}>
          {resultLabel(row.summary)}
        </Badge>
      </div>
      <div role="cell" className="text-[var(--color-ink-2)] truncate">
        {phoneLabel}
      </div>
      <div
        role="cell"
        className="text-[var(--color-ink-2)] truncate"
      >
        {row.caption_preview ?? '—'}
      </div>
      <div role="cell" className="text-right">
        {isPending ? (
          <button
            onClick={() => onDelete(row.id)}
            className="text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)]"
          >
            {t('common.delete')}
          </button>
        ) : null}
      </div>
    </div>
  )
}
```

Note: PlatformIcon's `mono` styling matches existing `platformIconIsMuted` logic but simplified — for compactness here we treat "no publication" and "PENDING + no phone" the same. The expanded panel uses the original helper for visual parity.

- [ ] **Step 2: Type-check**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b && cd ..
```

Expected: only `Videos.tsx` errors remain.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/videos/VideoRow.tsx
git commit -m "feat(frontend): add VideoRow component"
```

---

## Task 13: Frontend — `VideoDetailPanel` component

**Files:**
- Create: `frontend/src/components/videos/VideoDetailPanel.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/components/videos/VideoDetailPanel.tsx`:

```tsx
import { useTranslation } from 'react-i18next'
import { PLATFORMS, type Platform } from '../../api'
import { PlatformIcon } from '../PlatformIcon'
import { Badge } from '../ui'
import { useVideoDetail } from '../../lib/useVideoDetail'
import { formatLocalDateTime, formatRelative } from '../../lib/time'
import { PLATFORM_NAME } from '../../lib/platformNames'

const RESULT_TONE: Record<
  string, 'green' | 'red' | 'blue' | 'orange' | 'gray'
> = {
  SUCCESS: 'green',
  FAILURE: 'red',
  INITIATED: 'blue',
  QUEUED: 'orange',
  PENDING: 'gray',
}

type Props = {
  videoId: number
  onOpenFailureShot: (
    url: string, platform: string, detail: string) => void
}

export default function VideoDetailPanel({
  videoId, onOpenFailureShot,
}: Props) {
  const { t, i18n } = useTranslation()
  const { detail, loading, error, retry } = useVideoDetail(videoId)
  const locale = i18n.resolvedLanguage ?? i18n.language
  const resultLabel = (r: string) =>
    t(`videos.result.${r}`, { defaultValue: r })

  if (loading || (!detail && !error)) {
    return (
      <div className="px-4 py-3 bg-[var(--color-surface-hover)] text-[12.5px] text-[var(--color-ink-3)]">
        {t('videos.detail.loading', { defaultValue: '加载中…' })}
      </div>
    )
  }
  if (error) {
    return (
      <div className="px-4 py-3 bg-[var(--color-surface-hover)] text-[12.5px] text-[var(--color-red)] flex items-center gap-2">
        <span>
          {t('videos.detail.error', { defaultValue: '详情加载失败' })}
        </span>
        <button
          type="button"
          onClick={retry}
          className="underline text-[var(--color-blue)]"
        >
          {t('common.retry', { defaultValue: '重试' })}
        </button>
      </div>
    )
  }
  if (!detail) return null

  const pubs = detail.publications
  const byPlat = new Map(pubs.map((p) => [p.platform, p]))

  return (
    <div className="px-4 py-3 bg-[var(--color-surface-hover)]">
      <div className="flex flex-col gap-2 min-w-[720px]">
        {PLATFORMS.map((plat: Platform) => {
          const p = byPlat.get(plat) ?? null
          const muted = !p || (p.result === 'PENDING' && p.account_id == null)
          const displayResult = p?.result ?? 'PENDING'
          const failDetail = p?.result === 'FAILURE'
            ? `${p.notes ?? ''}${p.fail_code ? ` (${p.fail_code})` : ''}`
            : ''
          const phoneLabel = p?.account_profile_name
            ?? (p?.account_id != null
              ? `#${p.account_id}`
              : t('videos.auto') as string)
          return (
            <div
              key={plat}
              className="grid grid-cols-[150px_104px_120px_130px_minmax(0,1fr)] gap-x-4 gap-y-1 items-center text-[12.5px]"
            >
              <div className="inline-flex items-center gap-2 text-[var(--color-ink-2)]">
                <PlatformIcon
                  platform={plat} size={15} mono={muted}
                  className={
                    muted ? 'text-[var(--color-ink-4)]' : undefined}
                />
                {PLATFORM_NAME[plat]}
              </div>
              <div>
                <Badge tone={RESULT_TONE[displayResult] ?? 'gray'}>
                  {resultLabel(displayResult)}
                </Badge>
              </div>
              <div className="text-[var(--color-ink-3)] tabular-nums whitespace-nowrap">
                <span
                  title={p?.posted_at
                    ? formatLocalDateTime(p.posted_at) : undefined}
                >
                  {formatRelative(p?.posted_at ?? null, locale)}
                </span>
              </div>
              <div className="text-[var(--color-ink-3)] truncate">
                {phoneLabel}
              </div>
              <div className="min-w-0 text-[var(--color-ink-3)] truncate">
                {failDetail ? <span>{failDetail}</span> : null}
                {p?.fail_screenshot ? (
                  <button
                    type="button"
                    onClick={() => onOpenFailureShot(
                      `/api/videos/publications/${p.id}/screenshot`,
                      PLATFORM_NAME[plat],
                      failDetail,
                    )}
                    className="text-[var(--color-blue)] underline ml-2"
                  >
                    {t('videos.viewFailureShot')}
                  </button>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b && cd ..
```

Expected: only `Videos.tsx` errors remain.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/videos/VideoDetailPanel.tsx
git commit -m "feat(frontend): add VideoDetailPanel with lazy detail fetch"
```

---

## Task 14: Frontend — `VideoList` virtual list component

**Files:**
- Create: `frontend/src/components/videos/VideoList.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/components/videos/VideoList.tsx`:

```tsx
import { useEffect, useMemo, useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useTranslation } from 'react-i18next'
import type { VideoListRow } from '../../lib/useVideoPages'
import VideoRow from './VideoRow'
import VideoDetailPanel from './VideoDetailPanel'

type Row =
  | { kind: 'video'; video: VideoListRow; isContinuation: boolean }
  | { kind: 'detail'; videoId: number }
  | { kind: 'sentinel' }

type Props = {
  items: VideoListRow[]
  hasMore: boolean
  loading: boolean
  expandedIds: Set<number>
  selectedIds: Set<number>
  groupsById: Map<number, string>
  onToggleSelect: (id: number) => void
  onToggleExpanded: (id: number) => void
  onDelete: (id: number) => void
  onLoadMore: () => void
  onOpenFailureShot: (
    url: string, platform: string, detail: string) => void
}

function dateOnly(iso: string | null): string {
  return iso ? iso.slice(0, 10) : 'unknown'
}

export default function VideoList({
  items, hasMore, loading, expandedIds, selectedIds, groupsById,
  onToggleSelect, onToggleExpanded, onDelete, onLoadMore,
  onOpenFailureShot,
}: Props) {
  const { t } = useTranslation()
  const parentRef = useRef<HTMLDivElement | null>(null)

  const rows: Row[] = useMemo(() => {
    const out: Row[] = []
    let prevDate: string | null = null
    for (const v of items) {
      const d = dateOnly(v.uploaded_at)
      out.push({
        kind: 'video',
        video: v,
        isContinuation: prevDate === d,
      })
      prevDate = d
      if (expandedIds.has(v.id)) {
        out.push({ kind: 'detail', videoId: v.id })
      }
    }
    out.push({ kind: 'sentinel' })
    return out
  }, [items, expandedIds])

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => {
      const r = rows[i]
      if (r.kind === 'detail') return 180
      if (r.kind === 'sentinel') return 40
      return 48
    },
    overscan: 6,
    measureElement: (el) => el.getBoundingClientRect().height,
  })

  // Trigger loadMore when the sentinel row (last index) enters the
  // virtualized range. We depend on a stable `endIndex` value rather
  // than `getVirtualItems()`, which returns a fresh array per render
  // and would put the effect into a loop.
  const endIndex = virtualizer.range?.endIndex ?? null
  useEffect(() => {
    if (!hasMore || loading) return
    if (endIndex !== null && endIndex >= rows.length - 1) {
      onLoadMore()
    }
  }, [endIndex, rows.length, hasMore, loading, onLoadMore])

  return (
    <div
      ref={parentRef}
      role="table"
      className="border border-[var(--color-line)] rounded-[8px] bg-[var(--color-surface)] overflow-auto"
      style={{ height: '70vh' }}
    >
      <div
        role="rowgroup"
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((vi) => {
          const row = rows[vi.index]
          return (
            <div
              key={vi.key}
              data-index={vi.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${vi.start}px)`,
              }}
            >
              {row.kind === 'video' && (
                <VideoRow
                  row={row.video}
                  groupsById={groupsById}
                  isContinuation={row.isContinuation}
                  isExpanded={expandedIds.has(row.video.id)}
                  isSelected={selectedIds.has(row.video.id)}
                  onToggleSelect={onToggleSelect}
                  onToggleExpanded={onToggleExpanded}
                  onDelete={onDelete}
                />
              )}
              {row.kind === 'detail' && (
                <VideoDetailPanel
                  videoId={row.videoId}
                  onOpenFailureShot={onOpenFailureShot}
                />
              )}
              {row.kind === 'sentinel' && (
                <div className="px-3 py-3 text-center text-[12.5px] text-[var(--color-ink-4)]">
                  {hasMore
                    ? (loading
                        ? t('videos.loadingMore', { defaultValue: '加载中…' })
                        : t('videos.scrollForMore',
                            { defaultValue: '滚动加载更多' }))
                    : t('videos.endOfList',
                        { defaultValue: '— 已到底部 —' })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b && cd ..
```

Expected: only `Videos.tsx` errors remain.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/videos/VideoList.tsx
git commit -m "feat(frontend): add VideoList virtual scroller"
```

---

## Task 15: Frontend — refactor `Videos.tsx`

This is the largest task; it ties everything together and removes the old code paths.

**Files:**
- Modify: `frontend/src/pages/Videos.tsx` (replacing core list rendering + state)
- Modify: `frontend/src/api.ts` (already changed in Task 8 — commit together)

- [ ] **Step 1: Replace the video list state + loaders**

Open `frontend/src/pages/Videos.tsx`. Replace the imports block (lines 1-46) with:

```tsx
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { PLATFORMS } from '../api'
import type { ToastState } from '../App'
import {
  Button,
  Card,
  EmptyState,
  Modal,
  PageTitle,
  ProgressBar,
  SectionTitle,
  TextField,
} from '../components/ui'
import { cn } from '../lib/cn'
import {
  createFailureScreenshotPreview,
  type FailureScreenshotPreview,
} from '../lib/failureScreenshotPreview'
import VideoList from '../components/videos/VideoList'
import {
  useVideoPages,
  type VideoFilter,
} from '../lib/useVideoPages'
```

- [ ] **Step 2: Replace the `Videos` function body**

Replace the entire `export default function Videos(...) { ... }` (everything from `export default function Videos` through the closing brace) with:

```tsx
type ImportState = {
  state: string
  total: number
  done: number
  skipped: number
  failed: number
  current: string | null
  errors: string[]
}

type UploadProgressState = {
  loaded: number
  total: number
  percent: number
  count: number
}

type DriveFile = { id: string; name: string; already_imported: boolean }

const formatUploadBytes = (bytes: number) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1
  return `${value.toFixed(digits)} ${units[unit]}`
}

export default function Videos({ onToast }: { onToast: (t: ToastState) => void }) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState<VideoFilter>({
    group_id: 'all', status: 'all',
  })
  const {
    items, total, hasMore, loading, error,
    loadMore, refresh, removeLocally,
  } = useVideoPages(filter)

  const [groups, setGroups] = useState<{ id: number; name: string }[]>([])
  const groupsById = useMemo(() => {
    const m = new Map<number, string>()
    for (const g of groups) m.set(g.id, g.name)
    return m
  }, [groups])

  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] =
    useState<UploadProgressState | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importUrl, setImportUrl] = useState('')
  const [importStep, setImportStep] =
    useState<'choose' | 'url' | 'pick'>('choose')
  const [importFiles, setImportFiles] = useState<DriveFile[]>([])
  const [importSel, setImportSel] = useState<Set<string>>(new Set())
  const [importBusy, setImportBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importProg, setImportProg] = useState<ImportState | null>(null)
  const [importGroup, setImportGroup] = useState<number | ''>('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [assignTarget, setAssignTarget] = useState<number | ''>('')
  const [assignBusy, setAssignBusy] = useState(false)
  const [expandedVideos, setExpandedVideos] = useState<Set<number>>(new Set())
  const [failureShot, setFailureShot] =
    useState<FailureScreenshotPreview | null>(null)

  // Filter change → clear selection (otherwise we'd carry selected ids
  // across views that no longer contain them).
  useEffect(() => {
    setSelected(new Set())
  }, [filter.group_id, filter.status])

  useEffect(() => {
    fetch('/api/groups')
      .then((r) => (r.ok ? r.json() : []))
      .then(setGroups)
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!importing) return
    const tick = () =>
      api.importStatus()
        .then((s: ImportState) => {
          setImportProg(s)
          if (s.state === 'done' || s.state === 'error') {
            setImporting(false)
            onToast(s.state === 'done'
              ? {
                  msg: t('videos.toast.driveImportedSummary', {
                    done: s.done, skipped: s.skipped, failed: s.failed,
                  }),
                  tone: s.failed ? 'orange' : 'green',
                }
              : { msg: t('videos.toast.driveImportFailed'), tone: 'red' })
            refresh()
          }
        })
        .catch(() => {})
    tick()
    const timer = setInterval(tick, 2000)
    return () => clearInterval(timer)
  }, [importing, refresh, onToast, t])

  const openImport = () => {
    setImportStep('choose')
    setImportUrl('')
    setImportFiles([])
    setImportSel(new Set())
    setImportGroup('')
    setImportOpen(true)
  }

  const importSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (importBusy) return
    if (importStep === 'url') {
      const url = importUrl.trim()
      if (!url) return
      setImportBusy(true)
      try {
        const r = await api.importList(url)
        if (Array.isArray(r?.files)) {
          setImportFiles(r.files)
          setImportSel(new Set(
            r.files.filter((f: DriveFile) => !f.already_imported)
              .map((f: DriveFile) => f.id)))
          setImportStep('pick')
        } else {
          onToast({
            msg: r?.detail || t('videos.toast.couldNotList'), tone: 'red' })
        }
      } catch {
        onToast({ msg: t('videos.toast.couldNotList'), tone: 'red' })
      } finally {
        setImportBusy(false)
      }
      return
    }
    const chosen = importFiles.filter((f) => importSel.has(f.id))
    if (chosen.length === 0) return
    setImportBusy(true)
    try {
      const r = await api.importDrive(
        chosen.map((f) => ({ id: f.id, name: f.name })),
        importGroup === '' ? null : importGroup)
      if (r?.started) {
        setImportOpen(false)
        setImportProg(null)
        setImporting(true)
      } else {
        onToast({
          msg: r?.detail || t('videos.toast.couldNotStart'), tone: 'red' })
      }
    } catch {
      onToast({ msg: t('videos.toast.couldNotStart'), tone: 'red' })
    } finally {
      setImportBusy(false)
    }
  }

  const toggleImportFile = (id: string) =>
    setImportSel((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })

  const upload = async (files: FileList | File[]) => {
    if (!files || (files as FileList).length === 0) return
    const fileArray = Array.from(files as ArrayLike<File>)
    setUploading(true)
    const totalBytes = fileArray.reduce((sum, f) => sum + f.size, 0)
    setUploadProgress({
      loaded: 0, total: totalBytes, percent: 0, count: fileArray.length,
    })
    const fd = new FormData()
    fileArray.forEach((f) => fd.append('files', f))
    if (importGroup !== '') fd.append('group_id', String(importGroup))
    fd.append('platforms', PLATFORMS.join(','))
    try {
      const j = await new Promise<{ uploaded?: number }>((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('POST', '/api/videos/upload')
        xhr.withCredentials = true
        xhr.upload.onprogress = (event) => {
          const tot = event.lengthComputable ? event.total : totalBytes
          const loaded = event.lengthComputable
            ? event.loaded
            : Math.min(event.loaded || 0, totalBytes)
          const percent = tot > 0
            ? Math.min(100, Math.round((loaded / tot) * 100)) : 0
          setUploadProgress({
            loaded, total: tot, percent, count: fileArray.length,
          })
        }
        xhr.upload.onload = () => {
          setUploadProgress((prev) => prev
            ? { ...prev, loaded: prev.total,
                percent: prev.total > 0 ? 100 : prev.percent }
            : prev)
        }
        xhr.onload = () => {
          if (xhr.status === 401) {
            window.location.hash = '#login'
            reject(new Error('unauthorized')); return
          }
          if (xhr.status < 200 || xhr.status >= 300) {
            reject(new Error(xhr.responseText || `HTTP ${xhr.status}`))
            return
          }
          setUploadProgress((prev) => prev
            ? { ...prev, loaded: prev.total, percent: 100 } : prev)
          try { resolve(JSON.parse(xhr.responseText || '{}')) }
          catch (e) { reject(e) }
        }
        xhr.onerror = () => reject(new Error('network error'))
        xhr.onabort = () => reject(new Error('upload aborted'))
        xhr.send(fd)
      })
      onToast({
        msg: t('videos.toast.uploaded', { count: j.uploaded ?? 0 }),
        tone: 'green',
      })
      refresh()
    } catch {
      onToast({ msg: t('videos.toast.uploadFailed'), tone: 'red' })
    } finally {
      setUploading(false)
      setUploadProgress(null)
    }
  }

  const toggleSelected = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })

  const toggleExpanded = (id: number) =>
    setExpandedVideos((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })

  const confirmAssign = async () => {
    if (assignTarget === '' || selected.size === 0) return
    setAssignBusy(true)
    try {
      const r = await api.assignVideos(
        Array.from(selected), assignTarget as number)
      onToast({
        msg: t('videos.toast.assigned', { count: r?.assigned ?? 0 }),
        tone: 'green',
      })
      setSelected(new Set())
      setAssignTarget('')
      refresh()
    } catch {
      onToast({ msg: t('videos.toast.assignFailed'), tone: 'red' })
    } finally {
      setAssignBusy(false)
    }
  }

  const removeVideo = async (id: number) => {
    try {
      await api.deleteVideo(id)
      removeLocally(id)
      onToast({ msg: t('videos.toast.removed'), tone: 'orange' })
    } catch {
      onToast({ msg: t('videos.toast.deleteFailed'), tone: 'red' })
    }
  }

  const openFailureShot = useCallback(
    (url: string, platform: string, detail: string) => {
      const preview = createFailureScreenshotPreview({
        url, platform, detail })
      if (preview) setFailureShot(preview)
    }, [])

  const ALL_GROUPS = 'all' as const
  const UNGROUPED = 'ungrouped' as const

  return (
    <div className="flex flex-col gap-7">
      <PageTitle
        title={t('videos.title')}
        subtitle={t('videos.libraryHeading', {
          count: total ?? items.length,
        })}
        right={
          <>
            <Button
              variant="primary"
              onClick={openImport}
              disabled={uploading}
            >
              {uploading
                ? t('videos.upload.uploading', 'Uploading…')
                : t('videos.import.button', 'Import videos')}
            </Button>
            <Button variant="ghost" onClick={refresh}>
              {t('common.refresh', { defaultValue: '刷新' })}
            </Button>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept="video/*"
              className="hidden"
              onChange={(e) => {
                if (e.target.files) upload(e.target.files)
                e.target.value = ''
              }}
            />
          </>
        }
      />

      {groups.length > 0 && (
        <div className="flex gap-2 flex-wrap text-xs">
          <button
            onClick={() => setFilter((f) => ({ ...f, group_id: ALL_GROUPS }))}
            className={'px-2 py-1 rounded ' +
              (filter.group_id === ALL_GROUPS
                ? 'bg-blue-600 text-white' : 'bg-gray-200')}
          >
            {t('common.all')}
          </button>
          <button
            onClick={() => setFilter((f) => ({ ...f, group_id: UNGROUPED }))}
            className={'px-2 py-1 rounded ' +
              (filter.group_id === UNGROUPED
                ? 'bg-blue-600 text-white' : 'bg-gray-200')}
          >
            {t('videos.ungrouped')}
          </button>
          {groups.map((g) => (
            <button
              key={g.id}
              onClick={() => setFilter((f) => ({ ...f, group_id: g.id }))}
              className={'px-2 py-1 rounded ' +
                (filter.group_id === g.id
                  ? 'bg-blue-600 text-white' : 'bg-gray-200')}
            >
              {g.name}
            </button>
          ))}
        </div>
      )}

      {importProg && (importing || importProg.state !== 'idle') && (
        <section>
          <SectionTitle>{t('videos.driveImport')}</SectionTitle>
          <Card className="p-4 flex flex-col gap-3">
            <ProgressBar value={importProg.done} total={importProg.total} />
            <div className="text-[12.5px] text-[var(--color-ink-3)]">
              {importing
                ? (importProg.current
                    ? t('videos.downloadingCurrent', {
                        name: importProg.current })
                    : t('videos.listingFolder'))
                : t('videos.driveDone', {
                    done: importProg.done,
                    skipped: importProg.skipped,
                    failed: importProg.failed,
                  })}
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

      {uploadProgress && (
        <section>
          <SectionTitle>{t('videos.uploadProgress.title')}</SectionTitle>
          <Card className="p-4 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[12.5px] text-[var(--color-ink-2)]">
                {t('videos.uploadProgress.detail', {
                  count: uploadProgress.count })}
              </span>
              <span className="text-[12.5px] tabular-nums text-[var(--color-ink-3)]">
                {uploadProgress.percent}%
              </span>
            </div>
            <div className="relative h-1.5 rounded-full bg-[var(--color-gray-bg)] overflow-hidden">
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-[var(--color-blue)] transition-[width]"
                style={{ width: `${uploadProgress.percent}%` }}
              />
            </div>
            <div className="text-[12px] text-[var(--color-ink-3)] tabular-nums">
              {t('videos.uploadProgress.bytes', {
                loaded: formatUploadBytes(uploadProgress.loaded),
                total: formatUploadBytes(uploadProgress.total),
              })}
            </div>
          </Card>
        </section>
      )}

      <section>
        {selected.size > 0 && (
          <Card className="p-3 flex items-center gap-3 flex-wrap mb-3 bg-[var(--color-blue-bg)]">
            <span className="text-[12.5px] text-[var(--color-ink-2)]">
              {t('videos.assignSelected', { count: selected.size })}
            </span>
            <select
              value={assignTarget}
              onChange={(e) => setAssignTarget(
                e.target.value ? parseInt(e.target.value) : '')}
              className="border border-[var(--color-line)] rounded-[6px] px-2 py-1 text-sm bg-[var(--color-surface)]"
            >
              <option value="">{t('videos.assignPickGroup')}</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
            <Button
              variant="primary"
              onClick={confirmAssign}
              disabled={assignBusy || assignTarget === ''}
            >
              {assignBusy
                ? t('videos.assigning')
                : t('videos.confirmAssign')}
            </Button>
            <span className="text-[11.5px] text-[var(--color-ink-3)] ml-auto">
              {t('videos.autoPublishHint')}
            </span>
          </Card>
        )}

        {error && items.length === 0 && (
          <Card className="p-6 text-center">
            <div className="text-[var(--color-red)] mb-2">
              {t('videos.loadError', {
                defaultValue: '加载失败' })}
            </div>
            <Button onClick={refresh}>
              {t('common.retry', { defaultValue: '重试' })}
            </Button>
          </Card>
        )}

        {!error && items.length === 0 && !loading ? (
          <Card>
            <EmptyState
              title={t('videos.emptyVideosTitle')}
              children={t('videos.emptyVideosBody')}
            />
          </Card>
        ) : items.length > 0 ? (
          <VideoList
            items={items}
            hasMore={hasMore}
            loading={loading}
            expandedIds={expandedVideos}
            selectedIds={selected}
            groupsById={groupsById}
            onToggleSelect={toggleSelected}
            onToggleExpanded={toggleExpanded}
            onDelete={removeVideo}
            onLoadMore={loadMore}
            onOpenFailureShot={openFailureShot}
          />
        ) : null}
      </section>

      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title={importStep === 'choose'
          ? t('videos.import.button', '导入视频')
          : t('videos.modal.title')}
        onSubmit={importStep === 'choose' ? undefined : importSubmit}
        submitDisabled={importStep === 'pick' && importSel.size === 0}
        submitLabel={importBusy
          ? t('videos.modal.working')
          : importStep === 'url'
            ? t('videos.modal.listVideos')
            : t('videos.modal.importN', {
                count: importFiles.filter((f) =>
                  importSel.has(f.id)).length,
              })}
      >
        {importStep === 'choose' ? (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <label className="text-[13px] text-[var(--color-ink-2)] font-medium shrink-0">
                {t('videos.upload.pick_group')}
              </label>
              <select
                value={importGroup}
                onChange={(e) => setImportGroup(
                  e.target.value ? parseInt(e.target.value) : '')}
                className="flex-1 border border-[var(--color-line)] rounded-[6px] px-2 py-1 text-sm bg-[var(--color-surface)]"
              >
                <option value="">{t('videos.upload.pick_group')}</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => {
                setImportOpen(false)
                inputRef.current?.click()
              }}
              className="flex items-center gap-3 p-4 rounded-[10px] border border-[var(--color-line)] hover:border-[var(--color-blue)] hover:bg-[var(--color-blue-bg)] text-left transition-colors"
            >
              <div className="w-9 h-9 rounded-full bg-[var(--color-blue-bg)] text-[var(--color-blue)] flex items-center justify-center shrink-0">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M12 16V4M12 4l-4 4M12 4l4 4M5 20h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="text-[14px] font-medium text-[var(--color-ink)]">
                  {t('videos.import.localTitle', '本地上传')}
                </div>
                <div className="text-[12.5px] text-[var(--color-ink-3)]">
                  {t('videos.import.localHint', '从这台电脑选择视频文件')}
                </div>
              </div>
            </button>
            <button
              type="button"
              onClick={() => setImportStep('url')}
              className="flex items-center gap-3 p-4 rounded-[10px] border border-[var(--color-line)] hover:border-[var(--color-blue)] hover:bg-[var(--color-blue-bg)] text-left transition-colors"
            >
              <div className="w-9 h-9 rounded-full bg-[var(--color-blue-bg)] text-[var(--color-blue)] flex items-center justify-center shrink-0">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M4 4h10l6 6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                  <path d="M14 4v6h6" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="text-[14px] font-medium text-[var(--color-ink)]">
                  {t('videos.import.driveTitle', '从 Google Drive 导入')}
                </div>
                <div className="text-[12.5px] text-[var(--color-ink-3)]">
                  {t('videos.import.driveHint', '粘贴 Drive 文件夹链接')}
                </div>
              </div>
            </button>
          </div>
        ) : importStep === 'url' ? (
          <TextField
            label={t('videos.modal.folderLink')}
            placeholder={t('videos.modal.folderPlaceholder')}
            hint={t('videos.modal.folderHint')}
            value={importUrl}
            onChange={(e) => setImportUrl(e.target.value)}
            autoFocus
          />
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <label className="text-[13px] text-[var(--color-ink-2)] font-medium shrink-0">
                {t('videos.upload.pick_group')}
              </label>
              <select
                value={importGroup}
                onChange={(e) => setImportGroup(
                  e.target.value ? parseInt(e.target.value) : '')}
                className="border border-[var(--color-line)] rounded-[6px] px-2 py-1 text-sm bg-[var(--color-surface)]"
              >
                <option value="">{t('videos.upload.pick_group')}</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5 max-h-[280px] overflow-auto">
              {importFiles.length === 0 ? (
                <div className="text-[13px] text-[var(--color-ink-3)]">
                  {t('videos.modal.noVideos')}
                </div>
              ) : (
                importFiles.map((f) => (
                  <label
                    key={f.id}
                    className={cn(
                      'flex items-center gap-2.5 px-2 py-1.5 rounded-[6px] text-[13.5px]',
                      f.already_imported
                        ? 'text-[var(--color-ink-4)]'
                        : 'text-[var(--color-ink)] hover:bg-[var(--color-gray-bg)] cursor-pointer',
                    )}
                  >
                    <input
                      type="checkbox"
                      disabled={f.already_imported}
                      checked={importSel.has(f.id)}
                      onChange={() => toggleImportFile(f.id)}
                    />
                    <span className="truncate">{f.name}</span>
                    {f.already_imported ? (
                      <span className="text-[11.5px] text-[var(--color-ink-4)] ml-auto">
                        {t('videos.modal.alreadyImported')}
                      </span>
                    ) : null}
                  </label>
                ))
              )}
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={failureShot !== null}
        onClose={() => setFailureShot(null)}
        title={failureShot
          ? t('videos.failureShotTitle', { platform: failureShot.platform })
          : t('videos.viewFailureShot')}
      >
        {failureShot ? (
          <div className="flex flex-col gap-3">
            {failureShot.detail ? (
              <div className="text-[12.5px] text-[var(--color-ink-3)] break-words">
                {failureShot.detail}
              </div>
            ) : null}
            <div className="rounded-[8px] border border-[var(--color-line)] bg-[var(--color-surface-hover)] p-2">
              <img
                src={failureShot.url}
                alt={t('videos.failureShotAlt', {
                  platform: failureShot.platform })}
                className="max-h-[72vh] w-full object-contain rounded-[6px]"
              />
            </div>
            <a
              href={failureShot.url}
              target="_blank"
              rel="noreferrer"
              className="self-start text-[12.5px] text-[var(--color-blue)] underline"
            >
              {t('videos.openOriginalShot')}
            </a>
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
```

- [ ] **Step 3: Type-check + build**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build && cd ..
```

Expected: build succeeds. If any errors appear, fix them (most likely unused-import warnings — just drop the unused names from the imports block in Step 1).

- [ ] **Step 4: Run dev server and eyeball it**

Run:
```bash
./run.sh
```

In a browser at http://localhost:8080:
- Login
- Navigate to Videos
- Verify the list loads and shows the count
- Scroll a few hundred pixels — should be smooth
- Click + on a row to expand → detail loads
- Click a group chip → list refilters
- Upload a small file → it appears at top after refresh
- Delete a pending video → it disappears locally without full reload

Stop the dev server (Ctrl-C).

- [ ] **Step 5: Run full test suite**

Run:
```bash
uv run pytest -q
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/Videos.tsx
git commit -m "refactor(videos): swap full-list render for paginated virtual list"
```

---

## Task 16: Performance baseline + deploy

**Files:** none (operational task)

- [ ] **Step 1: Take a local performance baseline**

With a local DB populated similarly to prod (or against prod read-only via tunnel, your call), capture before/after numbers:

Run:
```bash
# Time the new endpoint
time curl -s -o /tmp/v.json \
  -H "Cookie: <your-session>" \
  "http://localhost:8080/api/videos?limit=50" > /dev/null
wc -c /tmp/v.json
```

Record:
- `time` real ms
- response size in bytes

Open Chrome DevTools → Performance, record a scroll session on the Videos page. Note the average FPS.

- [ ] **Step 2: Write the numbers into a follow-up note (NOT commit message — manual)**

Save them for the deploy commit message:
- Baseline (pre-change, optional if not captured): list ms / list bytes / scroll FPS
- After-change: list ms / list bytes / scroll FPS

- [ ] **Step 3: Build the deploy artifact**

Follow `docs/production-deploy.md`. In short:

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm ci && npm run build && cd ..
# Then the standard tarball + scp from the runbook
```

- [ ] **Step 4: Run migrations on prod**

Per the runbook, after extracting on the server:

```bash
ssh aliyun
cd /opt/mkt
uv run alembic upgrade head
sudo systemctl restart mkt mkt-worker
```

Verify `/healthz` returns 200.

- [ ] **Step 5: Smoke-check prod**

Hit the production URL, log in, open Videos:
- First-paint timing reasonable
- Scrolling smooth
- Group filter switches without lag
- A pending video expands and shows detail

- [ ] **Step 6: Final commit with measurements**

There is no code to commit at this step — measurements go into the deploy log / Notion task. If you took baseline numbers in Step 1, append them to `docs/superpowers/plans/2026-06-03-videos-list-perf.md` as a "Results" section and commit:

```bash
git add docs/superpowers/plans/2026-06-03-videos-list-perf.md
git commit -m "docs(videos-perf): record before/after performance numbers"
```

---

## Out of scope (do not do in this plan)

- Removing `vid_repo.list_all` (still referenced by `test_drive_import.py` and `test_videos_list_all_no_n_plus_1.py`); revisit as a separate cleanup
- SSE for real-time publication-state updates
- Video search box
- Sticky date headers (deliberate trade-off — date is a column now)
- Equivalent pagination for Accounts or Groups pages
