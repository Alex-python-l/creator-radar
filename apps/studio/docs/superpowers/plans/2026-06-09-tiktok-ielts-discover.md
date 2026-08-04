# TikTok IELTS Video Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover popular IELTS TikTok videos via Bright Data, store as `discovered_videos`, and surface them as a third "Discover" tab in CommentModal so the operator picks instead of pastes URLs.

**Architecture:** A daily Procrastinate task in `mkt-worker` triggers Bright Data's "Tiktok posts by keyword" dataset (`gd_lilwhto81z415d9mdl`), polls for completion, filters/upserts into PostgreSQL. The web tier serves an anti-join endpoint that hides videos already comment-attempted (non-failure). The frontend adds a third Segmented option to CommentModal that reuses the existing `target_links` submission path — engagement pipeline unchanged.

**Tech Stack:** FastAPI + SQLModel + Procrastinate + PostgreSQL 16 (backend); React 19 + Vite + react-i18next (frontend); `requests` for Bright Data HTTP; `pytest` + `responses` for tests.

**Spec:** [docs/superpowers/specs/2026-06-09-tiktok-ielts-discover-design.md](../specs/2026-06-09-tiktok-ielts-discover-design.md)

---

## Task 0: Pre-flight

**Files:** None modified — environment-check only.

- [ ] **Step 1: Confirm Alembic head count**

Run: `uv run alembic heads`
Expected: one revision id.
If more than one prints (today's state has 3 heads: `9762066ca2ef`, `bb8f5c2d4a91`, `d4b8c9e2f301`), run a merge first:

```bash
uv run alembic merge heads -m "merge before discovered_videos" -n <pick_a_short_slug>
uv run alembic upgrade head
```

Then re-run `uv run alembic heads` and proceed only when it prints exactly one id. Record that id; you will reference it in Task 2 as `down_revision`.

- [ ] **Step 2: Make sure `.env` has the BD token locally**

Add to `.env` (do not commit):

```
BRIGHTDATA_TOKEN=e15dd434-4e69-410c-9045-6aac4c8d7123
BRIGHTDATA_KEYWORD_DATASET_ID=gd_lilwhto81z415d9mdl
```

Verify:

```bash
grep -E "^BRIGHTDATA_" .env | awk -F= '{print $1}'
```

Expected output: both keys listed.

- [ ] **Step 3: Confirm test baseline**

Run: `uv run pytest -q`
Expected: 385 passing (per CLAUDE.md baseline). Record actual number; later tasks add ~20 and target must be ≥ baseline + new.

No commit for Task 0.

---

## Task 1: Settings defaults + .env.example

**Files:**
- Modify: `app/config.py` (SETTINGS_DEFAULTS dict)
- Modify: `.env.example`
- Test: `tests/test_config.py` (extend existing if present, else create)

- [ ] **Step 1: Find existing config test**

Run: `ls tests/test_config*.py 2>/dev/null || grep -l SETTINGS_DEFAULTS tests/`
If a config-defaults test file does not exist, create `tests/test_config_defaults.py` in Step 2 with the test below. If it exists, append the test instead.

- [ ] **Step 2: Write failing test**

Create or append `tests/test_config_defaults.py`:

```python
from app.config import SETTINGS_DEFAULTS


def test_tiktok_discover_defaults_present():
    assert SETTINGS_DEFAULTS["tiktok_discover_keywords"] == "ielts"
    assert SETTINGS_DEFAULTS["tiktok_discover_min_views"] == "10000"
    assert SETTINGS_DEFAULTS["tiktok_discover_window_days"] == "30"
    assert SETTINGS_DEFAULTS["tiktok_discover_retain_days"] == "60"
    assert SETTINGS_DEFAULTS["tiktok_discover_max_per_keyword"] == "500"
```

- [ ] **Step 3: Run test — verify failure**

Run: `uv run pytest tests/test_config_defaults.py::test_tiktok_discover_defaults_present -v`
Expected: FAIL with `KeyError: 'tiktok_discover_keywords'`.

- [ ] **Step 4: Add the defaults**

Edit `app/config.py` — add inside `SETTINGS_DEFAULTS = { ... }` (right before the closing brace):

```python
    "tiktok_discover_keywords": "ielts",
    "tiktok_discover_min_views": "10000",
    "tiktok_discover_window_days": "30",
    "tiktok_discover_retain_days": "60",
    "tiktok_discover_max_per_keyword": "500",
```

- [ ] **Step 5: Document env vars**

Edit `.env.example` — add at the end:

```
# Bright Data — TikTok keyword discovery (for CommentModal "Discover" tab).
# Token comes from Bright Data dashboard → Account settings → API tokens.
# Dataset ID rarely changes; override only if Bright Data renames it.
BRIGHTDATA_TOKEN=
BRIGHTDATA_KEYWORD_DATASET_ID=gd_lilwhto81z415d9mdl
```

- [ ] **Step 6: Run test — verify pass**

Run: `uv run pytest tests/test_config_defaults.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/config.py .env.example tests/test_config_defaults.py
git commit -m "feat(discover): add tiktok_discover_* settings defaults"
```

---

## Task 2: DiscoveredVideo model + Alembic migration

**Files:**
- Modify: `app/models.py` (add class)
- Create: `alembic/versions/<rev_id>_add_discovered_videos.py`
- Test: `tests/test_models_discovered_video.py`

- [ ] **Step 1: Write failing model test**

Create `tests/test_models_discovered_video.py`:

```python
from datetime import datetime

from app.models import DiscoveredVideo


def test_discovered_video_field_set():
    fields = set(DiscoveredVideo.model_fields.keys())
    required = {
        "id", "url", "video_id",
        "author_unique_id", "author_nickname", "author_followers",
        "title",
        "play_count", "like_count", "comment_count",
        "share_count", "collect_count",
        "thumbnail_url",
        "create_time",
        "search_keyword", "country",
        "discovered_at", "last_seen_at",
    }
    missing = required - fields
    assert not missing, f"missing: {missing}"


def test_discovered_video_defaults_for_timestamps():
    v = DiscoveredVideo(url="https://x", video_id="1", search_keyword="ielts")
    assert isinstance(v.discovered_at, datetime)
    assert isinstance(v.last_seen_at, datetime)
```

- [ ] **Step 2: Run test — verify failure**

Run: `uv run pytest tests/test_models_discovered_video.py -v`
Expected: FAIL — `ImportError: cannot import name 'DiscoveredVideo' from app.models`.

- [ ] **Step 3: Add the model**

Edit `app/models.py` — append at the bottom of the file (after the last class):

```python
class DiscoveredVideo(SQLModel, table=True):
    __tablename__ = "discovered_videos"
    __table_args__ = (
        Index("idx_discovered_videos_play_count", "play_count"),
        Index("idx_discovered_videos_create_time", "create_time"),
        Index("idx_discovered_videos_last_seen_at", "last_seen_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(nullable=False, sa_column_kwargs={"unique": True})
    video_id: str = Field(nullable=False)
    author_unique_id: Optional[str] = None
    author_nickname: Optional[str] = None
    author_followers: Optional[int] = None
    title: Optional[str] = Field(default=None, sa_column=Column(Text))
    play_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    collect_count: Optional[int] = None
    thumbnail_url: Optional[str] = Field(default=None, sa_column=Column(Text))
    create_time: Optional[datetime] = None
    search_keyword: str = Field(nullable=False)
    country: Optional[str] = None
    discovered_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    last_seen_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
```

(If the top of the file does not already import `Text` from sqlalchemy, add it. The other helpers — `Index`, `Column`, `_utcnow_col`, `Optional`, `datetime`, `Field`, `SQLModel` — are already imported in `app/models.py`.)

- [ ] **Step 4: Run model test — verify pass**

Run: `uv run pytest tests/test_models_discovered_video.py -v`
Expected: PASS for both tests.

- [ ] **Step 5: Scaffold migration**

Replace `<HEAD>` with the head revision from Task 0 Step 1.

```bash
uv run alembic revision -m "add discovered_videos table" --head <HEAD>
```

Identify the generated file path (something like `alembic/versions/<new_id>_add_discovered_videos.py`).

- [ ] **Step 6: Fill in migration bodies**

Open the generated file and replace the `upgrade()` and `downgrade()` bodies:

```python
def upgrade() -> None:
    op.create_table(
        "discovered_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("author_unique_id", sa.String(), nullable=True),
        sa.Column("author_nickname", sa.String(), nullable=True),
        sa.Column("author_followers", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("play_count", sa.Integer(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("share_count", sa.Integer(), nullable=True),
        sa.Column("collect_count", sa.Integer(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=True),
        sa.Column("search_keyword", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("url", name="uq_discovered_videos_url"),
    )
    op.create_index("idx_discovered_videos_play_count",
                    "discovered_videos", ["play_count"])
    op.create_index("idx_discovered_videos_create_time",
                    "discovered_videos", ["create_time"])
    op.create_index("idx_discovered_videos_last_seen_at",
                    "discovered_videos", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("idx_discovered_videos_last_seen_at",
                  table_name="discovered_videos")
    op.drop_index("idx_discovered_videos_create_time",
                  table_name="discovered_videos")
    op.drop_index("idx_discovered_videos_play_count",
                  table_name="discovered_videos")
    op.drop_table("discovered_videos")
```

- [ ] **Step 7: Apply migration locally**

```bash
uv run alembic upgrade head
```

Expected: no errors. Verify:

```bash
uv run python -c "from sqlmodel import Session, select; from app.db import engine; from app.models import DiscoveredVideo; s = Session(engine); print(s.exec(select(DiscoveredVideo)).all())"
```

Expected: `[]`.

- [ ] **Step 8: Commit**

```bash
git add app/models.py alembic/versions/*add_discovered_videos.py tests/test_models_discovered_video.py
git commit -m "feat(discover): add discovered_videos table + model"
```

---

## Task 3: BrightDataClient.trigger_keyword_search

**Files:**
- Create: `brightdata_client.py` (root-level, parallel to `geelark_client.py`)
- Test: `tests/test_brightdata_client.py`

`responses` library is the project's HTTP mocking helper. Verify it is in deps with `grep -E '"responses"' pyproject.toml`; add it via `uv add --dev responses` if missing.

- [ ] **Step 1: Write failing test**

Create `tests/test_brightdata_client.py`:

```python
import json

import pytest
import responses

from brightdata_client import BrightDataClient


@pytest.fixture
def client():
    return BrightDataClient(token="test-token", dataset_id="gd_test")


@responses.activate
def test_trigger_keyword_search_builds_correct_request(client):
    responses.add(
        responses.POST,
        "https://api.brightdata.com/datasets/v3/trigger",
        json={"snapshot_id": "sd_test123"},
        status=200,
    )
    snap = client.trigger_keyword_search("ielts")
    assert snap == "sd_test123"
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer test-token"
    assert call.request.headers["Content-Type"] == "application/json"
    assert call.request.url.startswith(
        "https://api.brightdata.com/datasets/v3/trigger?"
    )
    assert "dataset_id=gd_test" in call.request.url
    body = json.loads(call.request.body)
    assert body == [{
        "url": "https://www.tiktok.com/search?q=ielts",
        "search_keyword": "ielts",
        "country": "",
        "keyword_type": "",
    }]


@responses.activate
def test_trigger_keyword_search_raises_on_error(client):
    responses.add(
        responses.POST,
        "https://api.brightdata.com/datasets/v3/trigger",
        json={"error": "bad input"},
        status=400,
    )
    with pytest.raises(Exception) as exc:
        client.trigger_keyword_search("ielts")
    assert "bad input" in str(exc.value).lower() or "400" in str(exc.value)
```

- [ ] **Step 2: Run test — verify failure**

Run: `uv run pytest tests/test_brightdata_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brightdata_client'`.

- [ ] **Step 3: Implement**

Create `brightdata_client.py` at the repo root:

```python
"""Thin HTTP wrapper for Bright Data's datasets/v3 API.

Mirrors the shape of geelark_client.py. Used by the discover_tiktok
periodic task to surface candidate TikTok videos for the CommentModal
'Discover' tab. See docs/superpowers/specs/2026-06-09-tiktok-ielts-discover-design.md.
"""
from __future__ import annotations

import time

import requests


class BrightDataError(RuntimeError):
    pass


class BrightDataTimeout(BrightDataError):
    pass


class BrightDataClient:
    BASE = "https://api.brightdata.com/datasets/v3"

    def __init__(self, token: str, *, dataset_id: str, timeout: int = 30):
        self.token = token
        self.dataset_id = dataset_id
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def trigger_keyword_search(
        self, keyword: str, *, country: str = ""
    ) -> str:
        body = [{
            "url": f"https://www.tiktok.com/search?q={keyword}",
            "search_keyword": keyword,
            "country": country,
            "keyword_type": "",
        }]
        r = requests.post(
            f"{self.BASE}/trigger",
            params={"dataset_id": self.dataset_id},
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise BrightDataError(
                f"trigger failed {r.status_code}: {r.text[:200]}"
            )
        snap = r.json().get("snapshot_id")
        if not snap:
            raise BrightDataError(f"trigger missing snapshot_id: {r.text[:200]}")
        return snap
```

- [ ] **Step 4: Run test — verify pass**

Run: `uv run pytest tests/test_brightdata_client.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add brightdata_client.py tests/test_brightdata_client.py
git commit -m "feat(discover): BrightDataClient.trigger_keyword_search"
```

---

## Task 4: BrightDataClient — progress + fetch_snapshot

**Files:**
- Modify: `brightdata_client.py`
- Modify: `tests/test_brightdata_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_brightdata_client.py`:

```python
@responses.activate
def test_get_progress_returns_status(client):
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/progress/sd_x",
        json={"status": "running", "snapshot_id": "sd_x"},
        status=200,
    )
    assert client.get_progress("sd_x") == "running"


@responses.activate
def test_get_progress_raises_on_failed_lookup(client):
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/progress/sd_x",
        json={"error": "snapshot not found"},
        status=404,
    )
    with pytest.raises(Exception):
        client.get_progress("sd_x")


@responses.activate
def test_fetch_snapshot_returns_records(client):
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/snapshot/sd_x",
        json=[{"post_id": "1"}, {"post_id": "2"}],
        status=200,
    )
    rows = client.fetch_snapshot("sd_x")
    assert rows == [{"post_id": "1"}, {"post_id": "2"}]
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_brightdata_client.py -v -k "progress or snapshot"`
Expected: 3 FAIL — `AttributeError: 'BrightDataClient' object has no attribute 'get_progress'` (or similar).

- [ ] **Step 3: Implement both methods**

Add to `BrightDataClient` in `brightdata_client.py` (after `trigger_keyword_search`):

```python
    def get_progress(self, snapshot_id: str) -> str:
        r = requests.get(
            f"{self.BASE}/progress/{snapshot_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise BrightDataError(
                f"progress failed {r.status_code}: {r.text[:200]}"
            )
        status = r.json().get("status")
        if not status:
            raise BrightDataError(f"progress missing status: {r.text[:200]}")
        return status

    def fetch_snapshot(self, snapshot_id: str) -> list[dict]:
        r = requests.get(
            f"{self.BASE}/snapshot/{snapshot_id}",
            params={"format": "json"},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise BrightDataError(
                f"snapshot fetch failed {r.status_code}: {r.text[:200]}"
            )
        data = r.json()
        if not isinstance(data, list):
            raise BrightDataError(f"snapshot not list: {str(data)[:200]}")
        return data
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_brightdata_client.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add brightdata_client.py tests/test_brightdata_client.py
git commit -m "feat(discover): BrightDataClient.get_progress + fetch_snapshot"
```

---

## Task 5: BrightDataClient.poll_until_ready

**Files:**
- Modify: `brightdata_client.py`
- Modify: `tests/test_brightdata_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_brightdata_client.py`:

```python
def test_poll_until_ready_returns_records(client, monkeypatch):
    calls = []
    def fake_progress(snap):
        calls.append(snap)
        return "ready" if len(calls) >= 2 else "running"
    def fake_fetch(snap):
        return [{"post_id": "1"}]
    sleeps = []
    monkeypatch.setattr(client, "get_progress", fake_progress)
    monkeypatch.setattr(client, "fetch_snapshot", fake_fetch)
    monkeypatch.setattr("brightdata_client.time.sleep",
                       lambda s: sleeps.append(s))
    rows = client.poll_until_ready("sd_x", interval_seconds=20,
                                   max_wait_seconds=600)
    assert rows == [{"post_id": "1"}]
    assert sleeps == [20]  # slept once between running -> ready


def test_poll_until_ready_times_out(client, monkeypatch):
    monkeypatch.setattr(client, "get_progress", lambda s: "running")
    monkeypatch.setattr("brightdata_client.time.sleep", lambda s: None)
    monkeypatch.setattr("brightdata_client.time.monotonic",
                       _make_clock([0, 1, 200, 400, 700]))
    with pytest.raises(Exception) as exc:
        client.poll_until_ready("sd_x", interval_seconds=20,
                                max_wait_seconds=600)
    assert "timeout" in str(exc.value).lower()


def test_poll_until_ready_raises_on_failed_status(client, monkeypatch):
    monkeypatch.setattr(client, "get_progress", lambda s: "failed")
    monkeypatch.setattr("brightdata_client.time.sleep", lambda s: None)
    with pytest.raises(Exception) as exc:
        client.poll_until_ready("sd_x")
    assert "failed" in str(exc.value).lower()


def _make_clock(values):
    it = iter(values)
    return lambda: next(it)
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_brightdata_client.py -v -k "poll_until_ready"`
Expected: 3 FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Append to `BrightDataClient` in `brightdata_client.py`:

```python
    def poll_until_ready(
        self,
        snapshot_id: str,
        *,
        interval_seconds: int = 20,
        max_wait_seconds: int = 600,
    ) -> list[dict]:
        start = time.monotonic()
        while True:
            status = self.get_progress(snapshot_id)
            if status == "ready":
                return self.fetch_snapshot(snapshot_id)
            if status == "failed":
                raise BrightDataError(
                    f"snapshot {snapshot_id} reported failed status"
                )
            if time.monotonic() - start >= max_wait_seconds:
                raise BrightDataTimeout(
                    f"snapshot {snapshot_id} timeout after "
                    f"{max_wait_seconds}s (last status: {status})"
                )
            time.sleep(interval_seconds)
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_brightdata_client.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add brightdata_client.py tests/test_brightdata_client.py
git commit -m "feat(discover): BrightDataClient.poll_until_ready"
```

---

## Task 6: Test fixture — frozen BD sample

**Files:**
- Create: `tests/fixtures/brightdata_keyword_ielts_sample.json`

The fixture used by service-level tests. To stay small + privacy-friendly, ship 5 hand-picked records that exercise filter branches (one with `playCount=0`, one with `create_time` older than window, one missing `post_url`, two normal ones).

- [ ] **Step 1: Create fixtures dir if missing**

```bash
mkdir -p tests/fixtures
```

- [ ] **Step 2: Write the fixture**

Create `tests/fixtures/brightdata_keyword_ielts_sample.json`:

```json
[
  {
    "post_url": "https://www.tiktok.com/@a/video/7600000000000000001",
    "post_id": "7600000000000000001",
    "description": "IELTS speaking tips",
    "playCount": 2100000,
    "diggCount": 103800,
    "commentCount": 262,
    "shareCount": 17700,
    "collectCount": 102200,
    "create_time": "2026-04-18T13:37:28.000Z",
    "profile_nickname": "studying with gia",
    "profile_url": "https://www.tiktok.com/@gia.studs",
    "profile_followers": 2923,
    "thumbnail": "https://example.com/thumb1.jpg",
    "search_keyword": "ielts",
    "country": "DE"
  },
  {
    "post_url": "https://www.tiktok.com/@b/video/7600000000000000002",
    "post_id": "7600000000000000002",
    "description": "Another IELTS clip",
    "playCount": 136400,
    "diggCount": 9917,
    "commentCount": 141,
    "shareCount": 436,
    "collectCount": 478,
    "create_time": "2026-05-12T11:59:07.000Z",
    "profile_nickname": "jin xing",
    "profile_url": "https://www.tiktok.com/@jinxing2007",
    "profile_followers": 1013,
    "thumbnail": "https://example.com/thumb2.jpg",
    "search_keyword": "ielts",
    "country": "DE"
  },
  {
    "post_url": "https://www.tiktok.com/@c/video/7600000000000000003",
    "post_id": "7600000000000000003",
    "description": "Below threshold",
    "playCount": 500,
    "diggCount": 5,
    "commentCount": 0,
    "shareCount": 0,
    "collectCount": 0,
    "create_time": "2026-06-01T00:00:00.000Z",
    "profile_nickname": "tiny",
    "profile_url": "https://www.tiktok.com/@tiny",
    "profile_followers": 10,
    "thumbnail": null,
    "search_keyword": "ielts",
    "country": "DE"
  },
  {
    "post_url": "https://www.tiktok.com/@d/video/7600000000000000004",
    "post_id": "7600000000000000004",
    "description": "Too old",
    "playCount": 500000,
    "diggCount": 30000,
    "commentCount": 100,
    "shareCount": 1000,
    "collectCount": 2000,
    "create_time": "2025-09-01T00:00:00.000Z",
    "profile_nickname": "old.poster",
    "profile_url": "https://www.tiktok.com/@old.poster",
    "profile_followers": 5000,
    "thumbnail": "https://example.com/thumb4.jpg",
    "search_keyword": "ielts",
    "country": "DE"
  },
  {
    "post_url": null,
    "post_id": "7600000000000000005",
    "description": "Missing URL — should drop",
    "playCount": 999999,
    "diggCount": 12345,
    "commentCount": 0,
    "shareCount": 0,
    "collectCount": 0,
    "create_time": "2026-06-01T00:00:00.000Z",
    "profile_nickname": "no-url",
    "profile_url": null,
    "profile_followers": null,
    "thumbnail": null,
    "search_keyword": "ielts",
    "country": "DE"
  }
]
```

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/brightdata_keyword_ielts_sample.json
git commit -m "test(discover): BD keyword response fixture"
```

---

## Task 7: discovery_service._parse_settings

**Files:**
- Create: `app/services/discovery_service.py`
- Create: `tests/test_discovery_service.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_discovery_service.py`:

```python
from unittest.mock import MagicMock

from app.services import discovery_service


def _stub_session(setting_values):
    sess = MagicMock()
    from app.repositories import settings as srepo
    orig = srepo.get_all
    return sess, orig, setting_values


def test_parse_settings_uses_defaults(monkeypatch):
    sess = MagicMock()
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {},
    )
    cfg = discovery_service._parse_settings(sess)
    assert cfg.keywords == ["ielts"]
    assert cfg.min_views == 10000
    assert cfg.window_days == 30
    assert cfg.retain_days == 60
    assert cfg.max_per_keyword == 500


def test_parse_settings_splits_keyword_csv(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": "ielts, ieltsspeaking, "},
    )
    cfg = discovery_service._parse_settings(MagicMock())
    assert cfg.keywords == ["ielts", "ieltsspeaking"]


def test_parse_settings_overrides_numerics(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {
            "tiktok_discover_min_views": "50000",
            "tiktok_discover_window_days": "7",
            "tiktok_discover_retain_days": "30",
            "tiktok_discover_max_per_keyword": "200",
        },
    )
    cfg = discovery_service._parse_settings(MagicMock())
    assert (cfg.min_views, cfg.window_days, cfg.retain_days,
            cfg.max_per_keyword) == (50000, 7, 30, 200)
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_discovery_service.py -v -k parse_settings`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `app/services/discovery_service.py`:

```python
"""Orchestrate the daily TikTok-discovery cycle.

Reads settings → for each keyword: BD trigger → poll → filter → upsert.
Then prunes stale rows. Surfaces stats for JobRun logging.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from app.config import SETTINGS_DEFAULTS
from app.repositories import settings as srepo


@dataclass(frozen=True)
class DiscoverConfig:
    keywords: list[str]
    min_views: int
    window_days: int
    retain_days: int
    max_per_keyword: int


def _parse_settings(session: Session) -> DiscoverConfig:
    raw = srepo.get_all(session)
    def _get(key: str) -> str:
        return (raw.get(key) or SETTINGS_DEFAULTS[key]).strip()

    keywords_raw = _get("tiktok_discover_keywords")
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]

    return DiscoverConfig(
        keywords=keywords,
        min_views=int(_get("tiktok_discover_min_views")),
        window_days=int(_get("tiktok_discover_window_days")),
        retain_days=int(_get("tiktok_discover_retain_days")),
        max_per_keyword=int(_get("tiktok_discover_max_per_keyword")),
    )
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_discovery_service.py -v -k parse_settings`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/discovery_service.py tests/test_discovery_service.py
git commit -m "feat(discover): _parse_settings"
```

---

## Task 8: discovery_service._filter_and_normalize

**Files:**
- Modify: `app/services/discovery_service.py`
- Modify: `tests/test_discovery_service.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_discovery_service.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "brightdata_keyword_ielts_sample.json"


def _load_fixture():
    return json.loads(FIXTURE.read_text())


def test_filter_drops_missing_post_url():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=10000, window_days=365, max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    urls = {d.url for d in drafts}
    assert all(u for u in urls)
    # 5 records → drop missing-url (1) + below-threshold (1) → 3 left
    assert len(drafts) == 3


def test_filter_enforces_min_views():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=200000,  # only the 2.1M-view record passes
        window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    assert len(drafts) == 1
    assert drafts[0].play_count == 2100000


def test_filter_enforces_window_days():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1, window_days=60,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    # 5 records → drop missing-url (1) + 2025-09 old (1) → 3 remain
    assert len(drafts) == 3
    for d in drafts:
        delta = datetime(2026, 6, 9, tzinfo=timezone.utc) - d.create_time
        assert delta <= timedelta(days=60)


def test_filter_caps_at_max_per_keyword():
    records = _load_fixture() * 10  # 50 records, many duplicates by post_id
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1, window_days=365,
        max_per_keyword=2,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    assert len(drafts) == 2


def test_filter_dedup_by_post_id_within_batch():
    records = _load_fixture() + _load_fixture()  # double up
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1, window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    # Same dedup count as single-load
    single = discovery_service._filter_and_normalize(
        _load_fixture(), "ielts",
        min_views=1, window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    assert len(drafts) == len(single)


def test_filter_parses_full_record():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1000000, window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    d = drafts[0]
    assert d.url == "https://www.tiktok.com/@a/video/7600000000000000001"
    assert d.video_id == "7600000000000000001"
    assert d.title == "IELTS speaking tips"
    assert d.play_count == 2100000
    assert d.like_count == 103800
    assert d.comment_count == 262
    assert d.share_count == 17700
    assert d.collect_count == 102200
    assert d.author_unique_id == "gia.studs"
    assert d.author_nickname == "studying with gia"
    assert d.author_followers == 2923
    assert d.thumbnail_url == "https://example.com/thumb1.jpg"
    assert d.country == "DE"
    assert d.search_keyword == "ielts"
    assert d.create_time == datetime(2026, 4, 18, 13, 37, 28,
                                     tzinfo=timezone.utc)
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_discovery_service.py -v -k filter`
Expected: FAIL — `AttributeError: module 'discovery_service' has no '_filter_and_normalize'`.

- [ ] **Step 3: Implement**

Append to `app/services/discovery_service.py`:

```python
import re
from datetime import datetime, timedelta, timezone


@dataclass
class DiscoveredVideoDraft:
    url: str
    video_id: str
    author_unique_id: str | None
    author_nickname: str | None
    author_followers: int | None
    title: str | None
    play_count: int | None
    like_count: int | None
    comment_count: int | None
    share_count: int | None
    collect_count: int | None
    thumbnail_url: str | None
    create_time: datetime | None
    search_keyword: str
    country: str | None


_AUTHOR_RE = re.compile(r"tiktok\.com/@([^/?#]+)")


def _parse_author_unique_id(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    m = _AUTHOR_RE.search(profile_url)
    return m.group(1) if m else None


def _parse_create_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        # BD format: "2026-04-18T13:37:28.000Z"
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _filter_and_normalize(
    records: list[dict],
    keyword: str,
    *,
    min_views: int,
    window_days: int,
    max_per_keyword: int,
    now: datetime | None = None,
) -> list[DiscoveredVideoDraft]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    seen_ids: set[str] = set()
    out: list[DiscoveredVideoDraft] = []
    for r in records:
        url = r.get("post_url")
        if not url:
            continue
        post_id = str(r.get("post_id") or "")
        if not post_id or post_id in seen_ids:
            continue
        play_count = r.get("playCount") or 0
        if play_count < min_views:
            continue
        ct = _parse_create_time(r.get("create_time"))
        if ct is None or ct < cutoff:
            continue
        seen_ids.add(post_id)
        out.append(DiscoveredVideoDraft(
            url=url,
            video_id=post_id,
            author_unique_id=_parse_author_unique_id(r.get("profile_url")),
            author_nickname=r.get("profile_nickname"),
            author_followers=r.get("profile_followers"),
            title=r.get("description"),
            play_count=play_count,
            like_count=r.get("diggCount"),
            comment_count=r.get("commentCount"),
            share_count=r.get("shareCount"),
            collect_count=r.get("collectCount"),
            thumbnail_url=r.get("thumbnail"),
            create_time=ct,
            search_keyword=keyword,
            country=r.get("country"),
        ))
        if len(out) >= max_per_keyword:
            break
    return out
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_discovery_service.py -v -k filter`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/discovery_service.py tests/test_discovery_service.py
git commit -m "feat(discover): _filter_and_normalize"
```

---

## Task 9: discovery_service._upsert_videos

**Files:**
- Modify: `app/services/discovery_service.py`
- Modify: `tests/test_discovery_service.py`

The project tests run against PostgreSQL (check `conftest.py` for fixture name like `pg_session`). Use whatever real-DB session the existing repositories tests use — patterned after `tests/test_repositories_engagement.py`.

- [ ] **Step 1: Confirm DB session fixture name**

Run: `grep -nE "def .*session|@pytest.fixture" tests/test_repositories_engagement.py tests/conftest.py 2>/dev/null | head -10`
Note the fixture name (commonly `session` or `pg_session`). Use that name verbatim in the test below — replace `session` if needed.

- [ ] **Step 2: Write failing test**

Append to `tests/test_discovery_service.py`:

```python
from datetime import datetime, timezone

from app.models import DiscoveredVideo
from sqlmodel import select


def _draft(url, video_id="vid_1", play_count=100, like_count=10,
           title="t", search_keyword="ielts"):
    return discovery_service.DiscoveredVideoDraft(
        url=url, video_id=video_id,
        author_unique_id="a", author_nickname="A",
        author_followers=10, title=title,
        play_count=play_count, like_count=like_count,
        comment_count=1, share_count=1, collect_count=1,
        thumbnail_url="https://t/1.jpg",
        create_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        search_keyword=search_keyword, country="DE",
    )


def test_upsert_inserts_new_rows(session):
    drafts = [_draft("https://x/1", "vid_1"), _draft("https://x/2", "vid_2")]
    inserted, updated = discovery_service._upsert_videos(session, drafts)
    assert (inserted, updated) == (2, 0)
    rows = session.exec(select(DiscoveredVideo)).all()
    assert {r.url for r in rows} == {"https://x/1", "https://x/2"}


def test_upsert_updates_counts_but_preserves_search_keyword(session):
    first = _draft("https://x/1", "vid_1", play_count=100,
                   search_keyword="ielts")
    discovery_service._upsert_videos(session, [first])
    second = _draft("https://x/1", "vid_1", play_count=999,
                    search_keyword="ieltsspeaking")
    inserted, updated = discovery_service._upsert_videos(session, [second])
    assert (inserted, updated) == (0, 1)
    row = session.exec(select(DiscoveredVideo)).one()
    assert row.play_count == 999
    assert row.search_keyword == "ielts"   # preserved


def test_upsert_coalesces_nullable_fields(session):
    populated = _draft("https://x/1", "vid_1", title="real title")
    discovery_service._upsert_videos(session, [populated])
    nulled = discovery_service.DiscoveredVideoDraft(
        url="https://x/1", video_id="vid_1",
        author_unique_id=None, author_nickname=None, author_followers=None,
        title=None, play_count=200, like_count=20,
        comment_count=2, share_count=2, collect_count=2,
        thumbnail_url=None,
        create_time=datetime(2026, 6, 2, tzinfo=timezone.utc),
        search_keyword="ielts", country=None,
    )
    discovery_service._upsert_videos(session, [nulled])
    row = session.exec(select(DiscoveredVideo)).one()
    assert row.title == "real title"          # preserved by COALESCE
    assert row.play_count == 200              # overwritten
```

- [ ] **Step 3: Run — verify failure**

Run: `uv run pytest tests/test_discovery_service.py -v -k upsert`
Expected: FAIL — `_upsert_videos` not defined.

- [ ] **Step 4: Implement**

Append to `app/services/discovery_service.py`:

```python
from dataclasses import asdict

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import DiscoveredVideo


def _draft_row(d: DiscoveredVideoDraft) -> dict:
    row = asdict(d)
    # discovered_at / last_seen_at handled by DB defaults
    return row


def _upsert_videos(
    session: Session,
    drafts: list[DiscoveredVideoDraft],
) -> tuple[int, int]:
    if not drafts:
        return (0, 0)
    tbl = DiscoveredVideo.__table__
    rows = [_draft_row(d) for d in drafts]
    stmt = pg_insert(tbl).values(rows)
    excluded = stmt.excluded
    # Overwrite always
    overwrite_keys = [
        "play_count", "like_count", "comment_count",
        "share_count", "collect_count",
        "author_followers", "country", "video_id",
    ]
    # COALESCE: keep prior value if incoming is NULL
    coalesce_keys = ["title", "thumbnail_url", "author_nickname",
                     "author_unique_id"]
    set_ = {k: getattr(excluded, k) for k in overwrite_keys}
    for k in coalesce_keys:
        set_[k] = func.coalesce(getattr(excluded, k), tbl.c[k])
    set_["last_seen_at"] = func.now()
    # search_keyword and discovered_at NOT in set_ → preserved

    stmt = stmt.on_conflict_do_update(
        index_elements=["url"], set_=set_
    )
    # We need per-row inserted/updated counts. Easiest: pre-query existing.
    existing_urls = set(session.exec(
        select(DiscoveredVideo.url).where(
            DiscoveredVideo.url.in_([d.url for d in drafts])
        )
    ).all())
    session.execute(stmt)
    session.commit()
    inserted = sum(1 for d in drafts if d.url not in existing_urls)
    updated = len(drafts) - inserted
    return (inserted, updated)
```

Add `from sqlmodel import select` to the imports at the top of the file if not present.

- [ ] **Step 5: Run — verify pass**

Run: `uv run pytest tests/test_discovery_service.py -v -k upsert`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/discovery_service.py tests/test_discovery_service.py
git commit -m "feat(discover): _upsert_videos with COALESCE preservation"
```

---

## Task 10: discovery_service._prune_stale

**Files:**
- Modify: `app/services/discovery_service.py`
- Modify: `tests/test_discovery_service.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_discovery_service.py`:

```python
from datetime import datetime, timedelta, timezone

from app.models import (
    DiscoveredVideo, EngagementTask, EngagementRun,
)


def _make_engagement_run(session, target_link, result="SUCCESS"):
    task = EngagementTask(action="comment", source="manual", status="PENDING")
    session.add(task); session.commit(); session.refresh(task)
    run = EngagementRun(
        engagement_task_id=task.id,
        target_link=target_link,
        action="comment",
        result=result,
    )
    session.add(run); session.commit()


def test_prune_deletes_stale_unreferenced(session):
    old = DiscoveredVideo(
        url="https://x/old", video_id="1", search_keyword="ielts",
        last_seen_at=datetime.utcnow() - timedelta(days=90),
    )
    fresh = DiscoveredVideo(
        url="https://x/fresh", video_id="2", search_keyword="ielts",
    )
    session.add(old); session.add(fresh); session.commit()
    deleted = discovery_service._prune_stale(session, retain_days=60)
    assert deleted == 1
    remaining = {r.url for r in session.exec(select(DiscoveredVideo)).all()}
    assert remaining == {"https://x/fresh"}


def test_prune_preserves_stale_but_referenced(session):
    old_used = DiscoveredVideo(
        url="https://x/old_used", video_id="3", search_keyword="ielts",
        last_seen_at=datetime.utcnow() - timedelta(days=90),
    )
    session.add(old_used); session.commit()
    _make_engagement_run(session, "https://x/old_used", result="SUCCESS")
    deleted = discovery_service._prune_stale(session, retain_days=60)
    assert deleted == 0
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_discovery_service.py -v -k prune`
Expected: FAIL — `_prune_stale` not defined.

- [ ] **Step 3: Implement**

Append to `app/services/discovery_service.py`:

```python
from sqlalchemy import delete, exists, text

from app.models import EngagementRun


def _prune_stale(session: Session, retain_days: int) -> int:
    tbl = DiscoveredVideo.__table__
    cutoff_clause = text(f"now() - interval '{int(retain_days)} days'")
    stmt = delete(tbl).where(
        tbl.c.last_seen_at < cutoff_clause,
        ~exists().where(EngagementRun.target_link == tbl.c.url),
    )
    result = session.execute(stmt)
    session.commit()
    return result.rowcount or 0
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_discovery_service.py -v -k prune`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/discovery_service.py tests/test_discovery_service.py
git commit -m "feat(discover): _prune_stale with referenced-row protection"
```

---

## Task 11: discovery_service.run_discovery_cycle

**Files:**
- Modify: `app/services/discovery_service.py`
- Modify: `tests/test_discovery_service.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_discovery_service.py`:

```python
def test_run_discovery_cycle_aggregates_per_keyword(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": "ielts,ieltsspeaking",
                   "tiktok_discover_min_views": "1",
                   "tiktok_discover_window_days": "3650",
                   "tiktok_discover_retain_days": "60",
                   "tiktok_discover_max_per_keyword": "500"},
    )
    calls = []
    class FakeClient:
        def trigger_keyword_search(self, kw, *, country=""):
            calls.append(("trigger", kw))
            return f"snap_{kw}"
        def poll_until_ready(self, snap):
            calls.append(("poll", snap))
            return _load_fixture()
    stats = discovery_service.run_discovery_cycle(session, FakeClient())
    assert stats.keywords_processed == 2
    assert stats.keywords_failed == 0
    assert stats.inserted >= 1
    assert ("trigger", "ielts") in calls
    assert ("trigger", "ieltsspeaking") in calls


def test_run_discovery_cycle_isolates_keyword_failures(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": "good,bad",
                   "tiktok_discover_min_views": "1",
                   "tiktok_discover_window_days": "3650",
                   "tiktok_discover_retain_days": "60",
                   "tiktok_discover_max_per_keyword": "500"},
    )
    class FlakyClient:
        def trigger_keyword_search(self, kw, *, country=""):
            if kw == "bad":
                raise RuntimeError("boom")
            return f"snap_{kw}"
        def poll_until_ready(self, snap):
            return _load_fixture()
    stats = discovery_service.run_discovery_cycle(session, FlakyClient())
    assert stats.keywords_processed == 1
    assert stats.keywords_failed == 1
    assert "bad" in stats.failed_keywords
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_discovery_service.py -v -k run_discovery`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `app/services/discovery_service.py`:

```python
import logging

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryStats:
    keywords_processed: int = 0
    keywords_failed: int = 0
    failed_keywords: list[str] = None
    inserted: int = 0
    updated: int = 0
    pruned: int = 0

    def __post_init__(self):
        if self.failed_keywords is None:
            self.failed_keywords = []

    def __str__(self):
        return (
            f"kw_ok={self.keywords_processed} kw_fail={self.keywords_failed} "
            f"ins={self.inserted} upd={self.updated} pruned={self.pruned} "
            f"failed={self.failed_keywords}"
        )


def run_discovery_cycle(session: Session, client) -> DiscoveryStats:
    cfg = _parse_settings(session)
    stats = DiscoveryStats()
    for kw in cfg.keywords:
        try:
            snap = client.trigger_keyword_search(kw)
            records = client.poll_until_ready(snap)
            drafts = _filter_and_normalize(
                records, kw,
                min_views=cfg.min_views,
                window_days=cfg.window_days,
                max_per_keyword=cfg.max_per_keyword,
            )
            ins, upd = _upsert_videos(session, drafts)
            stats.inserted += ins
            stats.updated += upd
            stats.keywords_processed += 1
        except Exception as e:
            stats.keywords_failed += 1
            stats.failed_keywords.append(kw)
            logger.warning("discover keyword %s failed: %s", kw, e)
    stats.pruned = _prune_stale(session, cfg.retain_days)
    return stats
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_discovery_service.py -v`
Expected: all discovery_service tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/discovery_service.py tests/test_discovery_service.py
git commit -m "feat(discover): run_discovery_cycle orchestrator"
```

---

## Task 12: repositories.discovered_videos.list_unused_targets

**Files:**
- Create: `app/repositories/discovered_videos.py`
- Create: `tests/test_repositories_discovered_videos.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_repositories_discovered_videos.py`:

```python
from datetime import datetime, timezone

from sqlmodel import select

from app.models import DiscoveredVideo, EngagementTask, EngagementRun
from app.repositories import discovered_videos as repo


def _make_video(session, url, play_count=100):
    v = DiscoveredVideo(
        url=url, video_id=url.rsplit("/", 1)[-1],
        search_keyword="ielts", play_count=play_count,
        create_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    session.add(v); session.commit()
    return v


def _make_run(session, target_link, result):
    task = EngagementTask(action="comment", source="manual", status="PENDING")
    session.add(task); session.commit(); session.refresh(task)
    run = EngagementRun(
        engagement_task_id=task.id,
        target_link=target_link,
        action="comment",
        result=result,
    )
    session.add(run); session.commit()


def test_list_unused_returns_videos_with_no_engagement(session):
    _make_video(session, "https://x/1", play_count=100)
    _make_video(session, "https://x/2", play_count=200)
    items = repo.list_unused_targets(session)
    urls = [v.url for v in items]
    assert set(urls) == {"https://x/1", "https://x/2"}


def test_list_unused_hides_success_referenced(session):
    _make_video(session, "https://x/1")
    _make_run(session, "https://x/1", "SUCCESS")
    items = repo.list_unused_targets(session)
    assert items == []


def test_list_unused_hides_pending_queued_initiated(session):
    for url, status in [
        ("https://x/p", "PENDING"),
        ("https://x/q", "QUEUED"),
        ("https://x/i", "INITIATED"),
    ]:
        _make_video(session, url)
        _make_run(session, url, status)
    items = repo.list_unused_targets(session)
    assert items == []


def test_list_unused_shows_failure_referenced(session):
    _make_video(session, "https://x/f")
    _make_run(session, "https://x/f", "FAILURE")
    items = repo.list_unused_targets(session)
    assert len(items) == 1
    assert items[0].url == "https://x/f"


def test_list_unused_orders_by_play_count_desc(session):
    _make_video(session, "https://x/low", play_count=100)
    _make_video(session, "https://x/mid", play_count=500)
    _make_video(session, "https://x/high", play_count=999)
    items = repo.list_unused_targets(session)
    assert [v.url for v in items] == [
        "https://x/high", "https://x/mid", "https://x/low"
    ]


def test_list_unused_respects_limit(session):
    for i in range(5):
        _make_video(session, f"https://x/{i}", play_count=i * 10)
    items = repo.list_unused_targets(session, limit=2)
    assert len(items) == 2
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_repositories_discovered_videos.py -v`
Expected: FAIL — module import.

- [ ] **Step 3: Implement**

Create `app/repositories/discovered_videos.py`:

```python
"""Query layer over discovered_videos table. Used by the engagement
discover-targets API endpoint to surface candidate TikTok URLs that no
account has comment-attempted (other than failed attempts)."""
from __future__ import annotations

from sqlmodel import Session, select
from sqlalchemy import not_, exists

from app.models import DiscoveredVideo, EngagementRun


def list_unused_targets(
    session: Session, *, limit: int = 100
) -> list[DiscoveredVideo]:
    used_results = ("PENDING", "QUEUED", "INITIATED", "SUCCESS")
    stmt = (
        select(DiscoveredVideo)
        .where(
            not_(exists().where(
                (EngagementRun.target_link == DiscoveredVideo.url)
                & (EngagementRun.result.in_(used_results))
            ))
        )
        .order_by(DiscoveredVideo.play_count.desc().nullslast())
        .limit(limit)
    )
    return list(session.exec(stmt).all())
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_repositories_discovered_videos.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/discovered_videos.py tests/test_repositories_discovered_videos.py
git commit -m "feat(discover): list_unused_targets repo with anti-join"
```

---

## Task 13: API endpoint /api/engagement/discover-targets

**Files:**
- Modify: `app/api/engagement.py`
- Modify: `tests/test_api_engagement.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_engagement.py`. First find the existing TestClient fixture name with `grep -nE "TestClient|fixture" tests/test_api_engagement.py | head -5` and reuse it. The test below assumes a fixture named `client` and a `session` fixture (matching existing patterns):

```python
from datetime import datetime, timezone

from app.models import DiscoveredVideo


def test_discover_targets_returns_unused(client, session):
    session.add(DiscoveredVideo(
        url="https://www.tiktok.com/@a/video/1",
        video_id="1",
        search_keyword="ielts",
        title="hello",
        author_nickname="A",
        play_count=2_100_000,
        like_count=100_000,
        create_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
    ))
    session.commit()
    r = client.get("/api/engagement/discover-targets")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["url"] == "https://www.tiktok.com/@a/video/1"
    assert item["title"] == "hello"
    assert item["author_nickname"] == "A"
    assert item["play_count"] == 2_100_000
    assert item["like_count"] == 100_000
    assert item["create_time"].startswith("2026-06-01")
    assert item["search_keyword"] == "ielts"


def test_discover_targets_respects_limit_param(client, session):
    for i in range(5):
        session.add(DiscoveredVideo(
            url=f"https://x/{i}", video_id=str(i),
            search_keyword="ielts", play_count=i,
        ))
    session.commit()
    r = client.get("/api/engagement/discover-targets?limit=2")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2
```

- [ ] **Step 2: Run — verify failure**

Run: `uv run pytest tests/test_api_engagement.py -v -k discover_targets`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Implement the endpoint**

Edit `app/api/engagement.py` — add a new route. Place it near the other GET routes (after `list_eligible_phones` for visual grouping). Add the necessary import at the top:

```python
from app.repositories import discovered_videos as discovered_repo
```

Then add the route:

```python
@router.get("/discover-targets")
def list_discover_targets(
    limit: int = Query(100, le=200),
    session: Session = Depends(get_session),
) -> dict:
    items = discovered_repo.list_unused_targets(session, limit=limit)
    return {"items": [_serialize_discover(v) for v in items]}


def _serialize_discover(v) -> dict:
    return {
        "url": v.url,
        "title": v.title,
        "author_nickname": v.author_nickname or v.author_unique_id,
        "play_count": v.play_count,
        "like_count": v.like_count,
        "create_time": v.create_time.isoformat() if v.create_time else None,
        "search_keyword": v.search_keyword,
    }
```

- [ ] **Step 4: Run — verify pass**

Run: `uv run pytest tests/test_api_engagement.py -v -k discover_targets`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/engagement.py tests/test_api_engagement.py
git commit -m "feat(discover): GET /api/engagement/discover-targets"
```

---

## Task 14: Procrastinate task

**Files:**
- Create: `app/tasks/discover_tiktok.py`
- Modify: `app/tasks/__init__.py` (import side-effect if existing tasks are auto-loaded — check first)
- Create: `tests/test_tasks_discover_tiktok.py`

- [ ] **Step 1: Confirm task loading style**

Run: `cat app/tasks/__init__.py | head -40`
Identify how existing tasks (e.g. `auto_publish`, `pic_check`) get registered. If `__init__.py` imports each task module explicitly, you must add `from . import discover_tiktok` to it. If it auto-imports via `importlib`, no edit needed.

- [ ] **Step 2: Write failing test**

Create `tests/test_tasks_discover_tiktok.py`:

```python
import asyncio
from unittest.mock import MagicMock

from app.tasks import discover_tiktok as task_module


def test_task_registered_on_default_queue():
    task = task_module.discover_tiktok_task
    # procrastinate stores queue on the underlying task object
    assert getattr(task, "queue", None) == "default"


def test_task_writes_jobrun_and_calls_cycle(monkeypatch):
    seen_summary = {}
    stats = task_module.discovery_service.DiscoveryStats(
        keywords_processed=1, keywords_failed=0,
        failed_keywords=[], inserted=5, updated=2, pruned=0,
    )
    monkeypatch.setattr(
        "app.tasks.discover_tiktok.discovery_service.run_discovery_cycle",
        lambda session, client: stats,
    )
    monkeypatch.setattr(
        "app.tasks.discover_tiktok._make_client",
        lambda: MagicMock(),
    )
    started = []
    finished = []
    monkeypatch.setattr(
        "app.tasks.discover_tiktok.job_runs.start",
        lambda s, job_type: started.append(job_type) or 7,
    )
    monkeypatch.setattr(
        "app.tasks.discover_tiktok.job_runs.finish",
        lambda s, job_id, status, summary: (
            seen_summary.update({"job_id": job_id, "status": status,
                                 "summary": summary})
        ),
    )
    asyncio.run(task_module.discover_tiktok_task(timestamp=1234))
    assert started == ["discover_tiktok"]
    assert seen_summary["job_id"] == 7
    assert seen_summary["status"] == "SUCCESS"
    assert "ins=5" in seen_summary["summary"]
```

- [ ] **Step 3: Run — verify failure**

Run: `uv run pytest tests/test_tasks_discover_tiktok.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement**

Create `app/tasks/discover_tiktok.py`:

```python
"""Daily Procrastinate task that discovers IELTS TikTok videos via
Bright Data and upserts them into discovered_videos. Cron: 0 4 * * *.
See docs/superpowers/specs/2026-06-09-tiktok-ielts-discover-design.md.
"""
import asyncio
import os

from app.tasks import procrastinate_app
from app.db import session_scope
from app.services import discovery_service
from app.repositories import job_runs

from brightdata_client import BrightDataClient


def _make_client() -> BrightDataClient:
    return BrightDataClient(
        token=os.environ["BRIGHTDATA_TOKEN"],
        dataset_id=os.environ.get(
            "BRIGHTDATA_KEYWORD_DATASET_ID",
            "gd_lilwhto81z415d9mdl",
        ),
    )


@procrastinate_app.periodic(cron="0 4 * * *")
@procrastinate_app.task(
    queue="default",
    name="discover_tiktok",
    queueing_lock="discover_tiktok",
)
async def discover_tiktok_task(timestamp: int):
    def _work() -> str:
        with session_scope() as session:
            job_id = job_runs.start(session, "discover_tiktok")
        client = _make_client()
        try:
            with session_scope() as session:
                stats = discovery_service.run_discovery_cycle(session, client)
            status = "SUCCESS" if stats.keywords_failed == 0 else "PARTIAL"
            summary = str(stats)
        except Exception as e:
            status = "FAILURE"
            summary = f"discover cycle crashed: {e!r}"
        with session_scope() as session:
            job_runs.finish(session, job_id, status, summary)
        return summary
    return await asyncio.to_thread(_work)
```

If Step 1 indicated that `app/tasks/__init__.py` registers tasks explicitly, also add `from . import discover_tiktok  # noqa: F401` to it.

- [ ] **Step 5: Run — verify pass**

Run: `uv run pytest tests/test_tasks_discover_tiktok.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/tasks/discover_tiktok.py tests/test_tasks_discover_tiktok.py app/tasks/__init__.py
git commit -m "feat(discover): daily discover_tiktok procrastinate task"
```

---

## Task 15: Frontend — api.ts type + call

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Locate the api export**

Run: `grep -nE "engagementTargets|eligiblePhones" frontend/src/api.ts | head -5`
Note the surrounding shape — you will mimic it.

- [ ] **Step 2: Add type + endpoint**

Add to `frontend/src/api.ts` near other engagement types:

```ts
export type DiscoverTarget = {
  url: string
  title: string | null
  author_nickname: string | null
  play_count: number | null
  like_count: number | null
  create_time: string | null   // ISO
  search_keyword: string
}
```

Inside the `api` object literal, alongside `engagementTargets`:

```ts
  discoverTargets: () =>
    request<{ items: DiscoverTarget[] }>('/api/engagement/discover-targets'),
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run build`
Expected: clean build (no TS errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(discover): frontend api.discoverTargets"
```

---

## Task 16: i18n keys

**Files:**
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Add keys to en.json**

Inside the `"comments"` object in `frontend/src/i18n/locales/en.json`, add:

```json
    "discoverMode": "Discover",
    "discoverEmptyTitle": "No candidates yet",
    "discoverEmptyBody": "The daily discovery job (04:00 UTC) will populate this.",
    "viewsCount": "{{n}} views",
    "daysAgo": "{{n}} days ago",
    "loadFailed": "Failed to load candidates",
```

- [ ] **Step 2: Mirror in zh.json**

Inside the `"comments"` object in `frontend/src/i18n/locales/zh.json`, add:

```json
    "discoverMode": "发现",
    "discoverEmptyTitle": "暂无候选",
    "discoverEmptyBody": "每日发现任务（UTC 04:00）会填充这个列表。",
    "viewsCount": "{{n}} 播放",
    "daysAgo": "{{n}} 天前",
    "loadFailed": "候选加载失败",
```

- [ ] **Step 3: Verify JSON is valid**

Run: `python3 -m json.tool frontend/src/i18n/locales/en.json > /dev/null && python3 -m json.tool frontend/src/i18n/locales/zh.json > /dev/null && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "feat(discover): i18n keys for Discover tab"
```

---

## Task 17: CommentModal — add Discover mode + fetch

**Files:**
- Modify: `frontend/src/components/CommentModal.tsx`

This task introduces the third Segmented option and the state/fetch plumbing, but renders only a placeholder. Task 18 fills in the panel UI.

- [ ] **Step 1: Update the `Mode` type and add state**

In `frontend/src/components/CommentModal.tsx`:

1. Change the type definition:

```ts
type Mode = 'owned' | 'discover' | 'manual'
```

2. Add the new import next to the existing api type imports at the top:

```ts
import { api, type CommentTarget, type EligiblePhone, type DiscoverTarget } from '../api'
```

3. Inside the component, add state declarations next to `selectedTargets`:

```ts
const [discoverTargets, setDiscoverTargets] = useState<DiscoverTarget[]>([])
const [selectedDiscover, setSelectedDiscover] = useState<Set<string>>(new Set())
const [discoverLoadFailed, setDiscoverLoadFailed] = useState(false)
```

4. In the `useEffect(() => { ... }, [open])` block, extend the Promise.all and reset on close:

```ts
Promise.all([
  api.eligiblePhones(),
  api.engagementTargets(),
  api.discoverTargets().catch(() => null),
])
  .then(([p, t, d]) => {
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
  })
  .catch(() => {})
```

5. Update `targetCount` to add the discover branch:

```ts
const targetCount =
  mode === 'owned'    ? selectedTargets.size :
  mode === 'discover' ? selectedDiscover.size :
                        manualLinkList.length
```

6. Update `submit()` `target_links` and `target_publication_ids` lines:

```ts
target_links:
  mode === 'manual'   ? manualLinkList :
  mode === 'discover' ? [...selectedDiscover] :
                        [],
target_publication_ids:
  mode === 'owned' ? [...selectedTargets] : [],
```

7. Update the Segmented `options` inside `TargetsField`:

```ts
options={[
  { value: 'owned',    label: t('comments.ownedMode') },
  { value: 'discover', label: t('comments.discoverMode') },
  { value: 'manual',   label: t('comments.manualMode') },
]}
```

8. Inside `TargetsField`'s mode-switch block (where `manual` and `owned` are rendered), add a `discover` branch with a temporary stub (the real list comes in Task 18):

```tsx
) : mode === 'discover' ? (
  <DiscoverList
    items={discoverTargets}
    selected={selectedDiscover}
    onToggle={(url) => {
      setSelectedDiscover((prev) => {
        const next = new Set(prev)
        if (next.has(url)) next.delete(url); else next.add(url)
        return next
      })
    }}
    loadFailed={discoverLoadFailed}
  />
```

(Define a stub `DiscoverList` immediately below the modal component returning `null` for now — actual implementation in Task 18. This keeps the build green between tasks.)

```tsx
function DiscoverList(_props: {
  items: DiscoverTarget[]
  selected: Set<string>
  onToggle: (url: string) => void
  loadFailed: boolean
}) {
  return null
}
```

9. Pass `discoverTargets`, `selectedDiscover`, `setSelectedDiscover`, and `discoverLoadFailed` into `TargetsField` props. Add them to its function signature.

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/CommentModal.tsx
git commit -m "feat(discover): CommentModal three-way Mode + fetch wiring"
```

---

## Task 18: CommentModal — DiscoverList panel UI

**Files:**
- Modify: `frontend/src/components/CommentModal.tsx`

- [ ] **Step 1: Replace stub DiscoverList**

Replace the `DiscoverList` function in `frontend/src/components/CommentModal.tsx`:

```tsx
function DiscoverList({
  items, selected, onToggle, loadFailed,
}: {
  items: DiscoverTarget[]
  selected: Set<string>
  onToggle: (url: string) => void
  loadFailed: boolean
}) {
  const { t } = useTranslation()
  if (loadFailed) {
    return (
      <div className="text-center py-8 text-[13px] text-[var(--color-red)] border border-dashed border-[var(--color-line)] rounded-[10px]">
        {t('comments.loadFailed')}
      </div>
    )
  }
  if (items.length === 0) {
    return (
      <div className="text-center py-8 text-[13px] text-[var(--color-ink-3)] border border-dashed border-[var(--color-line)] rounded-[10px]">
        <div className="font-medium text-[var(--color-ink-2)] mb-1">
          {t('comments.discoverEmptyTitle')}
        </div>
        <div>{t('comments.discoverEmptyBody')}</div>
      </div>
    )
  }
  return (
    <div className="border border-[var(--color-line)] rounded-[10px] max-h-[260px] overflow-y-auto">
      {items.map((it) => (
        <DiscoverRow
          key={it.url}
          item={it}
          checked={selected.has(it.url)}
          onToggle={() => onToggle(it.url)}
        />
      ))}
    </div>
  )
}

function DiscoverRow({
  item, checked, onToggle,
}: {
  item: DiscoverTarget
  checked: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const plays = formatCount(item.play_count)
  const author = item.author_nickname ?? '—'
  const days = relativeDays(item.create_time)
  return (
    <label className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 px-3 py-2.5 border-b border-[var(--color-line)] last:border-b-0 text-[13px] cursor-pointer hover:bg-[var(--color-surface-hover)]">
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="accent-[var(--color-blue)] w-[14px] h-[14px]"
      />
      <div className="min-w-0">
        <div className="font-medium text-[var(--color-ink)] truncate">
          {item.title ?? item.url}
        </div>
        <div className="text-[11.5px] text-[var(--color-ink-4)] truncate">
          @{author} · {t('comments.viewsCount', { n: plays })} · {days ?? '—'}
        </div>
      </div>
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer"
        onClick={(e) => e.stopPropagation()}
        className="text-[var(--color-ink-4)] hover:text-[var(--color-blue)] text-[13px] px-1.5 py-0.5"
      >
        ↗
      </a>
    </label>
  )
}

function formatCount(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function relativeDays(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return null
  const diffMs = Date.now() - d.getTime()
  const days = Math.max(0, Math.round(diffMs / 86_400_000))
  if (days === 0) return 'today'
  return `${days}d ago`
}
```

(`relativeDays` returns English literals for "today" / "Nd ago" — quick and consistent with existing `relativeTime` in `Comments.tsx`. Internationalizing this is out of scope per spec §Frontend — the existing component does the same.)

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/CommentModal.tsx
git commit -m "feat(discover): DiscoverList + DiscoverRow with empty/fail states"
```

---

## Task 19: Final integration check

No code changes — manual acceptance per spec §Testing.

- [ ] **Step 1: Apply schema + restart processes locally**

```bash
uv run alembic upgrade head
# In one terminal:
./run.sh
# In another:
uv run python -m procrastinate -a app.tasks.procrastinate_app worker --queues phones,default
```

- [ ] **Step 2: Trigger task manually instead of waiting for 04:00 UTC**

In a Python shell:

```bash
uv run python -c "
import asyncio
from app.tasks.discover_tiktok import discover_tiktok_task
import time
asyncio.run(discover_tiktok_task.defer_async(timestamp=int(time.time())))
print('deferred')
"
```

Watch the worker logs. The cycle should take ~1–3 minutes (BD poll).

- [ ] **Step 3: Verify DB state**

```bash
psql "$DATABASE_URL" -c "SELECT count(*), max(play_count), min(create_time) FROM discovered_videos;"
```

Expected: ≥ 20 rows, max(play_count) in the millions, min(create_time) within last 30 days.

- [ ] **Step 4: Smoke test the UI**

1. Open `http://localhost:8080/`.
2. Go to Comments → "+ New comment".
3. Confirm three Segmented options: Owned / Discover / Manual.
4. Click Discover — verify a checklist appears with titles, view counts, and days-ago.
5. Select 2–3 entries + 1 phone.
6. Submit. Confirm a green toast.
7. Go back to Comments page — confirm 2–3 new EngagementRun records exist with the URLs you selected.
8. Reopen modal → Discover tab → confirm the URLs you just used are gone from the list.

- [ ] **Step 5: Confirm full test suite**

```bash
uv run pytest -q
```

Expected: ≥ baseline + new tests passing. If anything fails that you did not write, restore the prior commit and diagnose.

- [ ] **Step 6: Production deploy checklist (per CLAUDE.md "deploy after every change")**

1. SSH to production: `ssh -i ~/.ssh/svm_server root@139.196.52.92`.
2. Edit `/opt/mkt/.env` — append the two `BRIGHTDATA_*` lines (token is the one user provided; do not commit to repo).
3. Follow `docs/production-deploy.md` to tarball + scp + deploy.
4. After `alembic upgrade head` on the server, restart **both** services (memory: deploy-restart-worker):
   - `systemctl restart mkt`
   - `systemctl restart mkt-worker` (only when no warmup is in flight)
5. First cycle fires at next 04:00 UTC. To smoke-test now, defer one job via:
   `cd /opt/mkt && uv run python -c "import asyncio,time; from app.tasks.discover_tiktok import discover_tiktok_task; asyncio.run(discover_tiktok_task.defer_async(timestamp=int(time.time())))"`

No commit for Task 19.

---

## Self-review notes

Spec coverage check:
- §Architecture diagram — Tasks 13 (endpoint), 14 (task) ✓
- §Data model — Task 2 ✓
- §Settings — Task 1 ✓
- §brightdata_client — Tasks 3–5 ✓
- §discovery_service — Tasks 7–11 ✓
- §repositories.discovered_videos — Task 12 ✓
- §API endpoint — Task 13 ✓
- §Procrastinate task — Task 14 ✓
- §Frontend api.ts — Task 15 ✓
- §CommentModal mode switching — Task 17 ✓
- §DiscoverList component — Task 18 ✓
- §i18n — Task 16 ✓
- §Error handling — covered inline (BD client raises, service catches per-keyword, JobRun records failures)
- §Testing — fixture (Task 6) + per-component unit tests + Task 19 manual
- §Deployment — Task 19 Step 6 ✓
