# TikTok Photo Post (Carousel) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TikTok photo carousel posts (1–10 images) alongside the existing video pipeline. Spec at `docs/superpowers/specs/2026-06-11-tiktok-photo-post-design.md`.

**Architecture:** Extend `videos` with `media_kind` + new `video_assets` sibling table; reuse `publications`, scheduler, retry, and status reconciliation; add one `taskType=3` method to the GeeLark client, one multimodal caption entry point, and one new API/modal in the frontend.

**Tech Stack:** Python 3.11, FastAPI, SQLModel + Alembic, Procrastinate, React 19 + Vite, Google Gemini File API, GeeLark Open API.

---

## File map

**Create:**
- `alembic/versions/f0a1b2c3d4e5_videos_media_kind_and_video_assets.py`
- `frontend/src/components/PhotoUploadModal.tsx`
- `frontend/src/components/videos/PhotoStackIcon.tsx`

**Modify (server):**
- `app/models.py`
- `geelark_client.py`
- `generate_caption.py`
- `app/services/thumbnail_service.py`
- `app/repositories/videos.py`
- `app/services/posting_service.py`
- `app/api/videos.py`

**Modify (frontend):**
- `frontend/src/api.ts`
- `frontend/src/pages/Videos.tsx`
- `frontend/src/components/videos/VideoListView.tsx`
- `frontend/src/components/videos/VideoGridView.tsx`
- `frontend/src/components/videos/VideoDetailPanel.tsx`
- `frontend/src/i18n/locales/en.json`

**Tests added:**
- `tests/test_geelark_client.py`
- `tests/test_generate_caption.py`
- `tests/test_thumbnail_service.py`
- `tests/test_repositories_videos.py`
- `tests/test_posting_service.py`
- `tests/test_api.py` (or `test_videos_api.py` if exists; this codebase uses `test_api.py` and `test_publish_endpoint.py`)

---

## Task 1: Schema — Alembic migration + SQLModel changes

**Files:**
- Create: `alembic/versions/f0a1b2c3d4e5_videos_media_kind_and_video_assets.py`
- Modify: `app/models.py`

- [ ] **Step 1.1: Write the migration**

```python
# alembic/versions/f0a1b2c3d4e5_videos_media_kind_and_video_assets.py
"""videos.media_kind and video_assets

Revision ID: f0a1b2c3d4e5
Revises: d9f2c3a4b5e6
Create Date: 2026-06-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "d9f2c3a4b5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column(
            "media_kind",
            sa.Text(),
            nullable=False,
            server_default="video",
        ),
    )
    op.create_index(
        "idx_videos_media_kind", "videos", ["media_kind"],
    )
    op.create_table(
        "video_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "video_id", "idx", name="uq_video_assets_video_idx",
        ),
    )


def downgrade() -> None:
    op.drop_table("video_assets")
    op.drop_index("idx_videos_media_kind", table_name="videos")
    op.drop_column("videos", "media_kind")
```

- [ ] **Step 1.2: Add `media_kind` field to `Video` model and `VideoAsset` model**

Add to `app/models.py` after the existing `Video` class:

```python
# In Video class, after duration_seconds:
    media_kind: str = Field(default="video", nullable=False)
```

Then append at the end of the file (after the last existing model, before any trailing helpers):

```python
class VideoAsset(SQLModel, table=True):
    __tablename__ = "video_assets"
    __table_args__ = (
        UniqueConstraint(
            "video_id", "idx", name="uq_video_assets_video_idx",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    idx: int = Field(nullable=False)
    stored_path: str = Field(sa_column=Column(Text, nullable=False))
    original_filename: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
```

- [ ] **Step 1.3: Apply migration to mkt_test and run existing tests**

Run:

```bash
uv run alembic upgrade head
uv run pytest tests/test_repositories_videos.py -q
```

Expected: head is now `f0a1b2c3d4e5`, all existing repo tests still pass.

- [ ] **Step 1.4: Commit**

```bash
git add alembic/versions/f0a1b2c3d4e5_videos_media_kind_and_video_assets.py app/models.py
git commit -m "feat(photo-post): schema for media_kind and video_assets"
```

---

## Task 2: GeeLark client — `create_tiktok_photo_post_task`

**Files:**
- Modify: `geelark_client.py`
- Modify: `tests/test_geelark_client.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_geelark_client.py`:

```python
def test_create_tiktok_photo_post_task_payload_and_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskIds": ["tt-photo-1"]}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_tiktok_photo_post_task(
        "env-1",
        ["https://res/a.jpg", "https://res/b.jpg"],
        video_desc="caption text",
    )
    assert tid == "tt-photo-1"
    assert captured["path"] == "/open/v1/task/add"
    p = captured["payload"]
    assert p["taskType"] == 3
    assert len(p["list"]) == 1
    item = p["list"][0]
    assert item["envId"] == "env-1"
    assert item["images"] == ["https://res/a.jpg", "https://res/b.jpg"]
    assert item["videoDesc"] == "caption text"
    assert item["needShareLink"] is True
    assert isinstance(item["scheduleAt"], int)


def test_create_tiktok_photo_post_task_without_caption(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["payload"] = payload
        return {"taskIds": ["tt-photo-2"]}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")
    c.create_tiktok_photo_post_task("env-1", ["https://res/a.jpg"])
    item = captured["payload"]["list"][0]
    assert "videoDesc" not in item
    assert item["images"] == ["https://res/a.jpg"]
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
uv run pytest tests/test_geelark_client.py::test_create_tiktok_photo_post_task_payload_and_endpoint -v
```

Expected: FAIL with `AttributeError: 'GeeLarkClient' object has no attribute 'create_tiktok_photo_post_task'`.

- [ ] **Step 2.3: Implement the method**

Add to `geelark_client.py` immediately after `create_video_post_task` (around line 194):

```python
    def create_tiktok_photo_post_task(
        self,
        env_id: str,
        image_urls: list[str],
        video_desc: str | None = None,
    ) -> str:
        """Schedule a TikTok photo carousel post (taskType 3). Returns the
        task ID. `image_urls` must be pre-uploaded resourceUrls from
        upload_file(). TikTok caps at 35 slides; we cap at 10 in the API
        layer (see /api/videos/upload-photos)."""
        import time as _time
        item = {
            "scheduleAt": int(_time.time()),
            "envId": env_id,
            "images": list(image_urls),
            "needShareLink": True,
        }
        if video_desc:
            item["videoDesc"] = video_desc
        payload = {"taskType": 3, "list": [item]}
        data = self._post("/open/v1/task/add", payload)
        return data["taskIds"][0]
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_geelark_client.py -v
```

Expected: all pass, including the two new tests.

- [ ] **Step 2.5: Commit**

```bash
git add geelark_client.py tests/test_geelark_client.py
git commit -m "feat(photo-post): GeeLark client carousel task method"
```

---

## Task 3: Caption — `generate_caption_for_local_images`

**Files:**
- Modify: `generate_caption.py`
- Modify: `tests/test_generate_caption.py`

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_generate_caption.py` (read the top of that file to copy the import pattern for monkey-patching `_get_client`; the file already monkeypatches Gemini):

```python
def test_generate_caption_for_local_images_passes_each_file_in_order(
    monkeypatch, tmp_path,
):
    """The carousel caption call sends every image as a file_data part,
    in input order, then the prompt text last. Mirrors the video path."""
    import generate_caption

    img_a = tmp_path / "a.jpg"
    img_a.write_bytes(b"\xff\xd8\xff\xe0fakejpg-a")
    img_b = tmp_path / "b.png"
    img_b.write_bytes(b"\x89PNGfakeb")

    uploads: list[dict] = []
    deletions: list[str] = []

    class _FakeFile:
        def __init__(self, name, mime):
            self.name = name
            self.uri = f"gs://fake/{name}"
            self.state = type("S", (), {"value": "ACTIVE"})()
            self.mime = mime

    class _FakeFiles:
        def upload(self, *, file, config):
            uploads.append({"mime_type": config["mime_type"]})
            return _FakeFile(f"file-{len(uploads)}",
                             config["mime_type"])
        def get(self, *, name, config):
            f = _FakeFile(name, "ignored")
            f.state = type("S", (), {"value": "ACTIVE"})()
            return f
        def delete(self, *, name):
            deletions.append(name)

    captured_contents = {}

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured_contents["contents"] = contents
            return type("R", (), {"text": "hello world #tag"})()

    class _FakeClient:
        def __init__(self):
            self.files = _FakeFiles()
            self.models = _FakeModels()

    monkeypatch.setattr(generate_caption, "_get_client",
                        lambda: _FakeClient())

    out = generate_caption.generate_caption_for_local_images(
        [str(img_a), str(img_b)],
        brief="test brief",
        max_len=120,
    )

    assert out.strip() == "hello world #tag"
    # 2 image uploads happened in order
    assert [u["mime_type"] for u in uploads] == [
        "image/jpeg", "image/png",
    ]
    # contents.parts has: 2 file_data parts + 1 text part, in that order
    parts = captured_contents["contents"][0]["parts"]
    assert len(parts) == 3
    assert "file_data" in parts[0] and "file_data" in parts[1]
    assert "text" in parts[2]
    # Cleanup ran for every uploaded file
    assert len(deletions) == 2


def test_generate_caption_for_local_images_raises_on_empty_list(
    monkeypatch,
):
    import generate_caption

    monkeypatch.setattr(generate_caption, "_get_client",
                        lambda: object())
    import pytest as _pytest
    with _pytest.raises(ValueError):
        generate_caption.generate_caption_for_local_images([])
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
uv run pytest tests/test_generate_caption.py::test_generate_caption_for_local_images_passes_each_file_in_order -v
```

Expected: FAIL with `AttributeError`.

- [ ] **Step 3.3: Implement the function**

Add to `generate_caption.py` right after `generate_caption_for_local_file` (around line 415; preserve the surrounding helper imports):

```python
_IMAGE_EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


def _infer_image_mime(path: str) -> str:
    import os as _os
    ext = _os.path.splitext(path)[1].lstrip(".").lower()
    mime = _IMAGE_EXT_TO_MIME.get(ext)
    if not mime:
        raise ValueError(
            f"unsupported image extension: {ext!r} (expected jpg/png)")
    return mime


def generate_caption_for_local_images(
    local_paths: list[str],
    *,
    brief: str | None = None,
    max_len: int | None = None,
    thinking_budget: int = 0,
    model: str | None = None,
    banned_terms: list[str] | None = None,
) -> str:
    """Upload N images to Gemini File API in order, generate one carousel
    caption that sees all of them, clean up. Same sanitize rules as the
    video path."""
    if not local_paths:
        raise ValueError("local_paths must contain at least one image")

    client = _get_client()
    uploaded: list[tuple[object, str]] = []  # (gemini_file, mime)
    try:
        for path in local_paths:
            mime = _infer_image_mime(path)
            log.info(
                "  Uploading image to Gemini File API: %s",
                os.path.basename(path),
            )
            with open(path, "rb") as f:
                gemini_file = client.files.upload(
                    file=f, config={"mime_type": mime},
                )
            # Poll to ACTIVE — images are usually instant but we keep the
            # same robustness as the video path.
            deadline = time.time() + FILE_PROCESSING_TIMEOUT
            poll_config = {
                "http_options": {
                    "timeout": GEMINI_FILE_STATUS_TIMEOUT_MS,
                },
            }
            while time.time() < deadline:
                try:
                    gemini_file = client.files.get(
                        name=gemini_file.name, config=poll_config,
                    )
                except httpx.TransportError:
                    time.sleep(FILE_PROCESSING_POLL)
                    continue
                state = gemini_file.state
                state_val = (
                    state.value if hasattr(state, "value") else str(state)
                )
                if str(state_val) in ("2", "ACTIVE"):
                    break
                if str(state_val) in ("3", "FAILED"):
                    raise RuntimeError(
                        f"Gemini image processing failed "
                        f"(state={state_val})"
                    )
                time.sleep(FILE_PROCESSING_POLL)
            else:
                raise RuntimeError(
                    f"Timed out waiting for Gemini to process image after "
                    f"{FILE_PROCESSING_TIMEOUT}s"
                )
            uploaded.append((gemini_file, mime))

        chosen_model = (model or "").strip() or GEMINI_MODEL
        effective_max_len = max_len or CAPTION_MAX_LEN_DEFAULT
        prompt = build_prompt(brief, effective_max_len)
        parts = [
            {"file_data": {
                "file_uri": gf.uri, "mime_type": mime,
            }}
            for gf, mime in uploaded
        ]
        parts.append({"text": prompt})

        log.info(
            "  Generating carousel caption (model=%s, images=%d)...",
            chosen_model, len(uploaded),
        )
        response = _generate_content_with_retry(
            client,
            model=chosen_model,
            contents=[{"role": "user", "parts": parts}],
            config={"thinking_config": {
                "thinking_budget": thinking_budget,
            }},
        )
        raw = (response.text or "").strip()
    finally:
        for gf, _ in uploaded:
            try:
                client.files.delete(name=gf.name)
            except Exception:
                pass

    if not raw:
        raise RuntimeError("Gemini returned an empty caption")
    return sanitize_caption(
        raw,
        max_len=max_len or CAPTION_MAX_LEN_DEFAULT,
        banned_terms=banned_terms,
    )
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_generate_caption.py -v
```

Expected: all pass, including the two new tests.

- [ ] **Step 3.5: Commit**

```bash
git add generate_caption.py tests/test_generate_caption.py
git commit -m "feat(photo-post): multimodal carousel caption generator"
```

---

## Task 4: Thumbnail service — image source branch

**Files:**
- Modify: `app/services/thumbnail_service.py`
- Modify: `tests/test_thumbnail_service.py`

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_thumbnail_service.py`:

```python
def test_generate_image_thumbnail_creates_resized_jpeg(tmp_path,
                                                       monkeypatch):
    """For a photo post the 'source' is an image, not a video.
    generate_image_thumbnail writes a small JPEG copy and returns
    ('thumbnails/<id>.jpg', None)."""
    from PIL import Image
    from app.services import thumbnail_service

    monkeypatch.setattr(thumbnail_service, "THUMBNAIL_DIR", tmp_path)

    src = tmp_path / "src.jpg"
    Image.new("RGB", (1000, 800), color=(200, 50, 50)).save(src, "JPEG")

    rel, duration = thumbnail_service.generate_image_thumbnail(
        src, video_id=42,
    )
    assert rel == "thumbnails/42.jpg"
    assert duration is None
    out = tmp_path / "42.jpg"
    assert out.exists() and out.stat().st_size > 0
    with Image.open(out) as img:
        assert img.width <= thumbnail_service.THUMB_WIDTH


def test_generate_image_thumbnail_returns_none_on_unreadable(tmp_path,
                                                              monkeypatch):
    from app.services import thumbnail_service
    monkeypatch.setattr(thumbnail_service, "THUMBNAIL_DIR", tmp_path)

    src = tmp_path / "broken.jpg"
    src.write_bytes(b"not actually a jpeg")
    rel, duration = thumbnail_service.generate_image_thumbnail(
        src, video_id=7,
    )
    assert rel is None
    assert duration is None
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
uv run pytest tests/test_thumbnail_service.py::test_generate_image_thumbnail_creates_resized_jpeg -v
```

Expected: FAIL with `AttributeError: module 'app.services.thumbnail_service' has no attribute 'generate_image_thumbnail'`.

- [ ] **Step 4.3: Implement the function**

Append to `app/services/thumbnail_service.py`:

```python
def generate_image_thumbnail(
    image_path: Path | str, *, video_id: int,
) -> tuple[Optional[str], Optional[int]]:
    """Resize an uploaded image (JPG/PNG) into a 320px JPEG thumbnail.
    Same return contract as generate_thumbnail; duration is always None
    for photo posts. Never raises."""
    from PIL import Image, UnidentifiedImageError  # local import: avoid
    # pulling Pillow at module load for the video-only path
    src = Path(image_path)
    out = THUMBNAIL_DIR / f"{video_id}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as img:
            img = img.convert("RGB")
            ratio = THUMB_WIDTH / max(img.width, 1)
            new_size = (
                THUMB_WIDTH,
                max(1, int(round(img.height * ratio))),
            )
            img.thumbnail(new_size, Image.Resampling.LANCZOS)
            img.save(out, "JPEG", quality=85)
    except (UnidentifiedImageError, OSError) as e:
        logger.info("image thumbnail failed src=%s err=%s", src, e)
        return None, None
    return f"thumbnails/{video_id}.jpg", None
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_thumbnail_service.py -v
```

Expected: all pass.

- [ ] **Step 4.5: Confirm Pillow is in deps; add it if missing**

Run:

```bash
uv run python -c "from PIL import Image; print(Image.__version__)"
```

If this errors, add Pillow to `pyproject.toml` (`dependencies = [..., "pillow>=10",]`) and run `uv sync`, then commit `pyproject.toml` + `uv.lock`.

- [ ] **Step 4.6: Commit**

```bash
git add app/services/thumbnail_service.py tests/test_thumbnail_service.py
# include pyproject.toml + uv.lock if Pillow was newly added
git commit -m "feat(photo-post): image-source thumbnail generator"
```

---

## Task 5: Videos repository — `create_photo_post` + `get_detail` returns assets

**Files:**
- Modify: `app/repositories/videos.py`
- Modify: `tests/test_repositories_videos.py`

- [ ] **Step 5.1: Write the failing test**

Append to `tests/test_repositories_videos.py`:

```python
def test_create_photo_post_persists_video_and_assets(session):
    from app.repositories import groups as gr
    gid = gr.create(session, name="g-photo")
    vid = vr.create_photo_post(
        session,
        group_id=gid,
        image_paths=[
            ("/tmp/a.jpg", "a.jpg"),
            ("/tmp/b.png", "b.png"),
            ("/tmp/c.jpg", "c.jpg"),
        ],
    )
    assert isinstance(vid, int) and vid > 0
    v = vr.get(session, vid)
    assert v["media_kind"] == "photo"
    assert v["stored_path"] is None
    assert v["group_id"] == gid


def test_get_detail_returns_assets_in_order(session):
    from app.repositories import groups as gr
    gid = gr.create(session, name="g-photo2")
    vid = vr.create_photo_post(
        session,
        group_id=gid,
        image_paths=[
            ("/tmp/x.jpg", "x.jpg"),
            ("/tmp/y.jpg", "y.jpg"),
        ],
    )
    detail = vr.get_detail(session, vid)
    assert detail is not None
    assert detail["media_kind"] == "photo"
    assets = detail["assets"]
    assert [a["idx"] for a in assets] == [0, 1]
    assert [a["original_filename"] for a in assets] == ["x.jpg", "y.jpg"]


def test_get_detail_returns_empty_assets_for_video(session, sample_video):
    detail = vr.get_detail(session, sample_video.id)
    assert detail["media_kind"] == "video"
    assert detail["assets"] == []


def test_delete_returns_asset_paths_for_cleanup(session, tmp_path):
    from app.repositories import groups as gr
    gid = gr.create(session, name="g-photo-del")
    img_a = tmp_path / "a.jpg"
    img_b = tmp_path / "b.jpg"
    img_a.write_bytes(b"a"); img_b.write_bytes(b"b")
    vid = vr.create_photo_post(
        session,
        group_id=gid,
        image_paths=[(str(img_a), "a.jpg"), (str(img_b), "b.jpg")],
    )
    paths = vr.collect_asset_paths(session, vid)
    assert set(paths) == {str(img_a), str(img_b)}
    vr.delete(session, vid)
    assert vr.get(session, vid) is None
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_repositories_videos.py::test_create_photo_post_persists_video_and_assets -v
```

Expected: FAIL with `AttributeError: module 'app.repositories.videos' has no attribute 'create_photo_post'`.

- [ ] **Step 5.3: Implement repository changes**

Add to `app/repositories/videos.py`:

```python
# At the top with the other imports:
from app.models import Account, Publication, Video, VideoAsset


def create_photo_post(
    session: Session,
    *,
    group_id: Optional[int],
    image_paths: list[tuple[str, str]],
) -> int:
    """Atomic create of one videos(media_kind='photo') row plus N
    video_assets rows ordered by input position. `image_paths` is a list
    of (stored_path, original_filename) tuples."""
    if not image_paths:
        raise ValueError("create_photo_post requires at least one image")
    v = Video(
        original_filename=image_paths[0][1],
        stored_path=None,
        group_id=group_id,
        media_kind="photo",
    )
    session.add(v)
    session.flush()
    for idx, (path, name) in enumerate(image_paths):
        session.add(VideoAsset(
            video_id=v.id, idx=idx,
            stored_path=path, original_filename=name,
        ))
    session.commit()
    session.refresh(v)
    return v.id


def collect_asset_paths(session: Session, vid: int) -> list[str]:
    """All on-disk image paths for a photo post, in idx order.
    Returns [] for video posts. Caller is responsible for unlinking."""
    rows = session.exec(
        select(VideoAsset.stored_path)
        .where(VideoAsset.video_id == vid)
        .order_by(VideoAsset.idx)
    ).all()
    return [r if isinstance(r, str) else r[0] for r in rows]
```

Modify `get_detail` so the returned dict includes `assets` (always a list, empty for video):

```python
# In get_detail, after pubs are built and before returning out:
    asset_rows = session.exec(
        select(VideoAsset)
        .where(VideoAsset.video_id == vid)
        .order_by(VideoAsset.idx)
    ).all()
    out["assets"] = [
        {"idx": a.idx,
         "stored_path": a.stored_path,
         "original_filename": a.original_filename}
        for a in asset_rows
    ]
    return out
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_repositories_videos.py -v
```

Expected: all new tests pass; existing tests still pass.

- [ ] **Step 5.5: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories_videos.py
git commit -m "feat(photo-post): videos repo create_photo_post + assets in get_detail"
```

---

## Task 6: Videos repository — `list_page` exposes `media_kind` + `asset_count`

**Files:**
- Modify: `app/repositories/videos.py`
- Modify: `tests/test_repositories_videos.py`

- [ ] **Step 6.1: Write the failing test**

Append to `tests/test_repositories_videos.py`:

```python
def test_list_page_returns_media_kind_and_asset_count(session):
    from app.repositories import groups as gr
    gid = gr.create(session, name="g-page")
    vr.create(session, "v.mp4", "/tmp/v.mp4", group_id=gid)
    vr.create_photo_post(
        session,
        group_id=gid,
        image_paths=[
            ("/tmp/a.jpg", "a.jpg"),
            ("/tmp/b.jpg", "b.jpg"),
            ("/tmp/c.jpg", "c.jpg"),
        ],
    )
    items, _ = vr.list_page(
        session, group_id=gid, status="all", cursor=None, limit=50,
    )
    by_kind = {i["media_kind"]: i for i in items}
    assert set(by_kind) == {"video", "photo"}
    assert by_kind["video"]["asset_count"] is None
    assert by_kind["photo"]["asset_count"] == 3
```

- [ ] **Step 6.2: Run test to verify it fails**

```bash
uv run pytest tests/test_repositories_videos.py::test_list_page_returns_media_kind_and_asset_count -v
```

Expected: FAIL with `KeyError: 'media_kind'`.

- [ ] **Step 6.3: Extend `list_page` SQL and output dict**

In `app/repositories/videos.py::list_page`, modify the SELECT to add the two columns. Update the SELECT list and the items dict.

In the SQL string, add to the SELECT list (after `v.duration_seconds,`):

```sql
          v.media_kind,
          CASE
            WHEN v.media_kind = 'photo' THEN COUNT(DISTINCT va.id)
            ELSE NULL
          END AS asset_count,
```

Add to the FROM:

```sql
        LEFT JOIN video_assets va ON va.video_id = v.id
```

Place the join before the existing `LEFT JOIN publications p ON p.video_id = v.id`.

Update the GROUP BY to include `v.media_kind`:

```sql
        GROUP BY v.id, owner.profile_name, v.media_kind
```

In the items dict builder at the bottom of `list_page`, add the two fields:

```python
        items.append({
            ...,  # existing fields
            "media_kind": r["media_kind"],
            "asset_count": (
                int(r["asset_count"])
                if r["asset_count"] is not None else None
            ),
        })
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_repositories_videos.py tests/test_videos_list_all_no_n_plus_1.py -v
```

Expected: all pass.

- [ ] **Step 6.5: Commit**

```bash
git add app/repositories/videos.py tests/test_repositories_videos.py
git commit -m "feat(photo-post): list_page exposes media_kind + asset_count"
```

---

## Task 7: Posting service — `_submit` carousel dispatch + asset upload helper

**Files:**
- Modify: `app/services/posting_service.py`
- Modify: `tests/test_posting_service.py`

- [ ] **Step 7.1: Write the failing test**

Append to `tests/test_posting_service.py`:

```python
def test_submit_routes_single_asset_to_video_post():
    from app.services import posting_service as ps
    captured = {}

    class _StubClient:
        def create_video_post_task(self, env_id, video, *, video_desc=None):
            captured["called"] = "video"
            captured["video"] = video
            captured["desc"] = video_desc
            return "vid-task-1"
        def create_tiktok_photo_post_task(self, *a, **kw):
            captured["called"] = "photo"
            return "should-not-happen"

    tid = ps._submit(_StubClient(), "tiktok", "env-1",
                     ["https://res/v.mp4"], "caption")
    assert tid == "vid-task-1"
    assert captured["called"] == "video"
    assert captured["video"] == "https://res/v.mp4"
    assert captured["desc"] == "caption"


def test_submit_routes_multi_asset_to_carousel():
    from app.services import posting_service as ps
    captured = {}

    class _StubClient:
        def create_video_post_task(self, *a, **kw):
            captured["called"] = "video"
            return "should-not-happen"
        def create_tiktok_photo_post_task(
            self, env_id, image_urls, *, video_desc=None,
        ):
            captured["called"] = "photo"
            captured["urls"] = image_urls
            captured["desc"] = video_desc
            return "photo-task-1"

    tid = ps._submit(_StubClient(), "tiktok", "env-1",
                     ["https://res/a.jpg", "https://res/b.jpg"],
                     "carousel caption")
    assert tid == "photo-task-1"
    assert captured["called"] == "photo"
    assert captured["urls"] == ["https://res/a.jpg", "https://res/b.jpg"]
    assert captured["desc"] == "carousel caption"


def test_submit_rejects_non_tiktok_carousel():
    from app.services import posting_service as ps
    import pytest as _pytest

    class _StubClient:
        pass

    with _pytest.raises(ValueError, match="photo posts.*tiktok"):
        ps._submit(_StubClient(), "instagram", "env-1",
                   ["https://res/a.jpg", "https://res/b.jpg"], "x")
```

- [ ] **Step 7.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_posting_service.py::test_submit_routes_multi_asset_to_carousel -v
```

Expected: FAIL — current `_submit` takes a single `video_url` string, not a list.

- [ ] **Step 7.3: Update `_submit` signature and dispatch**

Replace the existing `_submit` in `app/services/posting_service.py`:

```python
def _submit(client, platform: str, env_id: str,
            asset_urls: list[str], caption: str) -> str:
    """Dispatch a publication submit by (platform, asset_count).
    asset_urls is always a list — len 1 for video, 1–10 for carousel."""
    if platform == "tiktok":
        if len(asset_urls) > 1:
            return client.create_tiktok_photo_post_task(
                env_id, asset_urls, video_desc=caption,
            )
        return client.create_video_post_task(
            env_id, asset_urls[0], video_desc=caption,
        )
    # Non-TikTok platforms don't support photo carousel today.
    if len(asset_urls) > 1:
        raise ValueError(
            f"photo posts are tiktok only; got platform={platform!r}"
        )
    if platform == "youtube":
        return client.create_youtube_post_task(
            env_id, asset_urls[0], title=caption)
    if platform == "instagram":
        return client.create_instagram_post_task(
            env_id, [asset_urls[0]], description=caption)
    if platform == "facebook":
        return client.create_facebook_post_task(
            env_id, asset_urls[0], description=caption)
    raise ValueError(f"unknown platform: {platform!r}")
```

- [ ] **Step 7.4: Update both call sites to pass a list**

In `_post_one` (around line 386), replace:

```python
        res_url = client.upload_file(stored_path, ext)
        task_id = _submit(client, pub["platform"],
                          acc["geelark_phone_id"], res_url, caption)
```

with:

```python
        asset_urls = _upload_assets_for_publication(session, client, pub)
        task_id = _submit(client, pub["platform"],
                          acc["geelark_phone_id"], asset_urls, caption)
```

In `submit_publication_to_geelark` (the upload-and-submit block around line 765), replace:

```python
    ext = os.path.splitext(v["original_filename"] or "")[1].lstrip(".") or "mp4"
    try:
        res_url = client.upload_file(v["stored_path"], ext)
    except Exception as e:
        ...
    try:
        task_id = _submit(client, pub["platform"],
                          acc["geelark_phone_id"], res_url, caption)
```

with:

```python
    try:
        asset_urls = _upload_assets_for_publication(session, client, pub)
    except Exception as e:
        message = _exception_message(e)
        _record_attempt_and_fail(
            session, publication_id,
            stage="upload", source="geelark",
            message=message,
            notes=f"upload failed: {message}",
        )
        acc_repo.release_phone_lease(session, account_id)
        raise
    try:
        task_id = _submit(client, pub["platform"],
                          acc["geelark_phone_id"], asset_urls, caption)
```

Add the helper anywhere above `_submit`:

```python
def _upload_assets_for_publication(session, client, pub: dict) -> list[str]:
    """Upload the publication's source media (1 video file OR N images
    in carousel order) and return the GeeLark resource URLs."""
    from app.repositories import videos as _vid_repo
    v = _vid_repo.get(session, pub["video_id"])
    if v is None:
        raise RuntimeError(f"video {pub['video_id']} missing")
    if v.get("media_kind") == "photo":
        assets = _vid_repo.collect_asset_paths(session, pub["video_id"])
        if not assets:
            raise RuntimeError("photo post has no images")
        urls: list[str] = []
        for path in assets:
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"stored asset missing: {path}")
            ext = os.path.splitext(path)[1].lstrip(".") or "jpg"
            urls.append(client.upload_file(path, ext))
        return urls
    stored_path = v.get("stored_path")
    if not stored_path or not os.path.exists(stored_path):
        raise FileNotFoundError(_stored_file_missing_message(stored_path))
    ext = (
        os.path.splitext(v.get("original_filename") or "")[1]
        .lstrip(".") or "mp4"
    )
    return [client.upload_file(stored_path, ext)]
```

- [ ] **Step 7.5: Update existing posting tests that pass a string**

Grep for `_submit(` in `tests/`. Wherever a test calls `_submit(client, plat, env, "https://res/...", caption)`, change the URL string to a single-element list `["https://res/..."]`. Run the full posting-service suite:

```bash
uv run pytest tests/test_posting_service.py tests/test_publish_endpoint.py tests/test_publish_lease.py tests/test_publish_prompt_missing.py -v
```

Expected: all pass.

- [ ] **Step 7.6: Commit**

```bash
git add app/services/posting_service.py tests/test_posting_service.py
git commit -m "feat(photo-post): _submit dispatch + asset upload helper"
```

---

## Task 8: Posting service — caption switching + cleanup

**Files:**
- Modify: `app/services/posting_service.py`
- Modify: `tests/test_posting_service.py`

- [ ] **Step 8.1: Write the failing test**

Append to `tests/test_posting_service.py`:

```python
def test_caption_for_photo_calls_images_function(monkeypatch, session,
                                                  tmp_path):
    """For a photo publication, the caption path must go through
    generate_caption_for_local_images with all asset paths."""
    from app.repositories import groups as gr
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo
    from app.services import posting_service as ps

    gid = gr.create(session, name="g-cap")
    img = tmp_path / "a.jpg"
    img.write_bytes(b"fake-jpg")
    vid = vid_repo.create_photo_post(
        session, group_id=gid,
        image_paths=[(str(img), "a.jpg")],
    )
    pid = pub_repo.find_or_create(
        session, video_id=vid, platform="tiktok")
    pub = pub_repo.get(session, pid)
    acc = {"group_id": gid}

    captured = {}

    def fake_images(paths, **kw):
        captured["paths"] = list(paths)
        captured["kw"] = kw
        return "img caption #x"

    def fake_video(*a, **kw):
        captured["video_called"] = True
        return "should not run"

    monkeypatch.setattr(
        "generate_caption.generate_caption_for_local_images", fake_images)
    monkeypatch.setattr(
        "generate_caption.generate_caption_for_local_file", fake_video)

    out = ps._caption_for_publication(session, pub, str(img), acc)
    assert out == "img caption #x"
    assert captured["paths"] == [str(img)]
    assert "video_called" not in captured
```

- [ ] **Step 8.2: Run test to verify it fails**

```bash
uv run pytest tests/test_posting_service.py::test_caption_for_photo_calls_images_function -v
```

Expected: FAIL — `_caption` always calls `generate_caption_for_local_file`.

- [ ] **Step 8.3: Update `_caption` and `_caption_for_publication`**

Replace `_caption` (around line 106) in `app/services/posting_service.py`:

```python
def _caption(session, local_path: str, acc: dict,
             *, media_kind: str = "video",
             asset_paths: list[str] | None = None) -> str:
    from generate_caption import (
        generate_caption_for_local_file,
        generate_caption_for_local_images,
    )
    cfg = _resolve_caption_config(session, acc.get("group_id"))
    kwargs = {
        "brief":           cfg["brief"],
        "max_len":         cfg["max_len"],
        "thinking_budget": cfg["thinking_budget"],
        "model":           cfg["model"],
        "banned_terms":    cfg["banned_terms"],
    }
    if media_kind == "photo":
        if not asset_paths:
            raise RuntimeError(
                "photo caption requires non-empty asset_paths")
        return generate_caption_for_local_images(asset_paths, **kwargs)
    return generate_caption_for_local_file(local_path, **kwargs)
```

Replace `_caption_for_publication` (around line 119):

```python
def _caption_for_publication(session, pub: dict, local_path: str,
                             acc: dict) -> str:
    """Video-level caption cache backed by sibling publications.
    For photo posts the cache is effectively single-publication
    (TikTok only), so generation always runs."""
    for sibling in pub_repo.list_for_video(session, pub["video_id"]):
        caption = (sibling.get("caption") or "").strip()
        if caption:
            return sibling["caption"]
    v = vid_repo.get(session, pub["video_id"])
    if v and v.get("media_kind") == "photo":
        asset_paths = vid_repo.collect_asset_paths(
            session, pub["video_id"])
        caption = _caption(
            session, local_path, acc,
            media_kind="photo", asset_paths=asset_paths,
        )
    else:
        caption = _caption(session, local_path, acc)
    pub_repo.set_caption(session, pub["id"], caption)
    return caption
```

(Note: `local_path` is now optional for the photo branch but kept for the video branch's interface — callers always have it.)

- [ ] **Step 8.4: Update `_maybe_cleanup_video` and the legacy `check_initiated` cleanup**

`_maybe_cleanup_video` (around line 908) — change to delete asset files too:

```python
def _maybe_cleanup_video(session, video_id: int) -> None:
    """If every publication for `video_id` is SUCCESS, remove local files
    and clear stored_path. Handles both video (single file) and photo
    (N image files via video_assets)."""
    success = pub_repo.video_ids_all_success(session)
    if video_id not in success:
        return
    v = vid_repo.get(session, video_id)
    if not v:
        return
    paths_to_remove: list[str] = []
    if v.get("media_kind") == "photo":
        paths_to_remove.extend(
            vid_repo.collect_asset_paths(session, video_id))
    elif v.get("stored_path"):
        paths_to_remove.append(v["stored_path"])
    for p in paths_to_remove:
        try:
            os.remove(p)
        except OSError:
            pass
    if v.get("stored_path"):
        vid_repo.clear_stored_path(session, video_id)
```

Same idea for the `check_initiated` cleanup loop (around line 559). Replace the post-loop cleanup body with a call to `_maybe_cleanup_video(session, vid)` (which already returns silently when not all-success):

```python
    for vid in pub_repo.video_ids_all_success(session):
        _maybe_cleanup_video(session, vid)
    return f"{ok} success, {fail} failure"
```

- [ ] **Step 8.5: Run tests to verify they pass**

```bash
uv run pytest tests/test_posting_service.py tests/test_tasks_publish.py -v
```

Expected: all pass.

- [ ] **Step 8.6: Commit**

```bash
git add app/services/posting_service.py tests/test_posting_service.py
git commit -m "feat(photo-post): photo caption + asset cleanup in posting service"
```

---

## Task 9: API — `POST /api/videos/upload-photos`

**Files:**
- Modify: `app/api/videos.py`
- Modify: `tests/test_api.py`

- [ ] **Step 9.1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def _png_bytes() -> bytes:
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _jpg_bytes() -> bytes:
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(50, 50, 50)).save(buf, "JPEG")
    return buf.getvalue()


def test_upload_photos_creates_video_assets_and_tiktok_publication(
    client, db,
):
    from app.repositories import groups as gr
    from app.repositories import videos as vr
    from app.repositories import publications as pr

    gid = gr.create(db, name="g-upload-photo")
    files = [
        ("files", ("a.jpg", _jpg_bytes(), "image/jpeg")),
        ("files", ("b.png", _png_bytes(), "image/png")),
        ("files", ("c.jpg", _jpg_bytes(), "image/jpeg")),
    ]
    resp = client.post(
        "/api/videos/upload-photos",
        files=files,
        data={"group_id": str(gid)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["uploaded"] == 1

    rows = vr.list_all(db)
    # newest first; pick the one with media_kind=photo
    photo = next(r for r in rows if r["media_kind"] == "photo")
    detail = vr.get_detail(db, photo["id"])
    assert len(detail["assets"]) == 3
    assert detail["thumbnail_path"] is not None
    pubs = pr.list_for_video(db, photo["id"])
    assert {p["platform"] for p in pubs} == {"tiktok"}


def test_upload_photos_rejects_empty(client):
    resp = client.post("/api/videos/upload-photos", files=[], data={})
    # FastAPI's File(...) returns 422 when files is missing entirely
    assert resp.status_code in (400, 422)


def test_upload_photos_rejects_more_than_10(client, db):
    from app.repositories import groups as gr
    gid = gr.create(db, name="g-too-many")
    files = [
        ("files", (f"{i}.jpg", _jpg_bytes(), "image/jpeg"))
        for i in range(11)
    ]
    resp = client.post(
        "/api/videos/upload-photos",
        files=files,
        data={"group_id": str(gid)},
    )
    assert resp.status_code == 400
    assert "at most 10" in resp.json()["detail"].lower()


def test_upload_photos_rejects_non_image_mime(client, db):
    from app.repositories import groups as gr
    gid = gr.create(db, name="g-bad-mime")
    files = [("files", ("evil.mp4", b"\x00", "video/mp4"))]
    resp = client.post(
        "/api/videos/upload-photos",
        files=files,
        data={"group_id": str(gid)},
    )
    assert resp.status_code == 400


def test_publish_one_rejects_photo_to_youtube(client, db, tmp_path):
    from app.repositories import accounts as ar
    from app.repositories import groups as gr
    from app.repositories import videos as vr

    gid = gr.create(db, name="g-pub-block")
    aid = ar.create(db, {
        "profile_name": "yt-account",
        "tiktok_logged_in": 1,
        "youtube_logged_in": 1,
        "geelark_phone_id": "phone-yt",
        "group_id": gid,
        "warmed_up": 1,
    })
    img = tmp_path / "x.jpg"
    img.write_bytes(_jpg_bytes())
    vid = vr.create_photo_post(
        db, group_id=gid,
        image_paths=[(str(img), "x.jpg")],
    )

    resp = client.post(
        f"/api/videos/{vid}/publish",
        params={"platform": "youtube"},
        json={"account_id": aid},
    )
    assert resp.status_code == 400
    assert "tiktok" in resp.json()["detail"].lower()
```

- [ ] **Step 9.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_api.py::test_upload_photos_creates_video_assets_and_tiktok_publication -v
```

Expected: FAIL with 404 (no `upload-photos` route).

- [ ] **Step 9.3: Implement the endpoint and the platform guard**

Add to `app/api/videos.py`:

```python
_ALLOWED_PHOTO_MIME = {"image/jpeg", "image/jpg", "image/png"}
_PHOTO_MAX_COUNT = 10


def _ext_for_image_mime(mime: str) -> str:
    if mime in ("image/jpeg", "image/jpg"):
        return "jpg"
    if mime == "image/png":
        return "png"
    raise ValueError(f"unsupported image mime: {mime!r}")


@router.post("/upload-photos")
async def upload_photos(
    files: list[UploadFile] = File(...),
    group_id: int | None = Form(None),
    session: Session = Depends(get_session),
):
    """Create one photo carousel post (1–10 images, TikTok only)."""
    if not files:
        raise HTTPException(400, "at least 1 image required")
    if len(files) > _PHOTO_MAX_COUNT:
        raise HTTPException(
            400, f"at most {_PHOTO_MAX_COUNT} images per photo post")
    for f in files:
        ctype = (f.content_type or "").lower()
        if ctype not in _ALLOWED_PHOTO_MIME:
            raise HTTPException(
                400, f"unsupported image type: {ctype!r}; "
                     "expected image/jpeg or image/png")

    saved: list[Path] = []
    vid: int | None = None
    try:
        image_paths: list[tuple[str, str]] = []
        for f in files:
            safe_name = Path(f.filename or "image").name or "image"
            dest = _save_upload_file(f, safe_name)
            saved.append(dest)
            image_paths.append((str(dest), safe_name))
        vid = vid_repo.create_photo_post(
            session, group_id=group_id, image_paths=image_paths)
        pub_repo.find_or_create(
            session, video_id=vid, platform="tiktok")
        rel, _ = generate_image_thumbnail(saved[0], video_id=vid)
        if rel is not None:
            vid_repo.update_media_meta(
                session, vid, thumbnail_path=rel, duration_seconds=None)
    except Exception:
        if vid is not None:
            vid_repo.delete(session, vid)
        for path in saved:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    scan_queued = False
    if group_id is not None:
        scan_queued = await _wake_auto_publish_scan()
    return {"uploaded": 1, "publish_scan_queued": scan_queued}
```

Add the import at the top of `app/api/videos.py`:

```python
from app.services.thumbnail_service import (
    generate_thumbnail, generate_image_thumbnail,
)
```

(Replace the existing `from app.services.thumbnail_service import generate_thumbnail` line.)

In `publish_one` (around line 433), insert the photo-platform guard right after the `vid_repo.get` check:

```python
    if vid_repo.get(session, vid) is None:
        raise HTTPException(status_code=404, detail="video not found")
    v = vid_repo.get(session, vid)
    if v.get("media_kind") == "photo" and platform != "tiktok":
        raise HTTPException(
            status_code=400,
            detail="photo posts can only be published to tiktok",
        )
```

- [ ] **Step 9.4: Update `delete_video` to clean up photo assets**

Replace the existing `delete_video` endpoint:

```python
@router.delete("/{vid}")
def delete_video(vid: int, session: Session = Depends(get_session)):
    row = vid_repo.get(session, vid)
    asset_paths = (
        vid_repo.collect_asset_paths(session, vid)
        if row and row.get("media_kind") == "photo"
        else []
    )
    vid_repo.delete(session, vid)
    if row and row.get("stored_path"):
        try:
            os.remove(row["stored_path"])
        except OSError:
            pass
    for path in asset_paths:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"ok": True}
```

Add a test in `tests/test_api.py`:

```python
def test_delete_photo_post_removes_image_files(client, db, tmp_path):
    from app.repositories import groups as gr
    from app.repositories import videos as vr

    gid = gr.create(db, name="g-del-photo")
    img_a = tmp_path / "a.jpg"
    img_b = tmp_path / "b.jpg"
    img_a.write_bytes(_jpg_bytes())
    img_b.write_bytes(_jpg_bytes())
    vid = vr.create_photo_post(
        db, group_id=gid,
        image_paths=[(str(img_a), "a.jpg"), (str(img_b), "b.jpg")],
    )
    resp = client.delete(f"/api/videos/{vid}")
    assert resp.status_code == 200
    assert not img_a.exists()
    assert not img_b.exists()
    assert vr.get(db, vid) is None
```

Run:

```bash
uv run pytest tests/test_api.py -k "photo or upload_photos" -v
uv run pytest tests/test_api.py -v
```

Expected: new tests pass; pre-existing API tests still pass.

- [ ] **Step 9.6: Commit**

```bash
git add app/api/videos.py tests/test_api.py
git commit -m "feat(photo-post): POST /api/videos/upload-photos + photo platform guard + delete cleanup"
```

---

## Task 10: Frontend — types and i18n

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/i18n/locales/en.json`

- [ ] **Step 10.1: Extend the type definitions in `frontend/src/api.ts`**

Find the existing `VideoSummary` and `VideoDetail` interface declarations (or whatever the file actually calls them — read the file first; if the project uses an aggregated `Video` type, follow that name). Add:

```ts
export type MediaKind = 'video' | 'photo'

export interface VideoAsset {
  idx: number
  stored_path: string
  original_filename: string | null
}

// Inside VideoSummary (or whatever the list item interface is):
//   media_kind: MediaKind
//   asset_count: number | null

// Inside VideoDetail:
//   assets: VideoAsset[]
```

Also add the upload helper:

```ts
export async function uploadPhotoPost(
  files: File[],
  groupId: number | null,
  onProgress?: (loaded: number, total: number) => void,
): Promise<{ uploaded: number; publish_scan_queued: boolean }> {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  if (groupId !== null) fd.append('group_id', String(groupId))
  // Use the same XHR helper pattern as the existing uploadVideos.
  // If the existing helper exposes `uploadWithProgress`, reuse it.
  return uploadWithProgress(
    '/api/videos/upload-photos', fd, onProgress,
  )
}
```

If `uploadWithProgress` doesn't exist with that exact name, mirror whatever the existing `uploadVideos` helper does — they should be identical except for endpoint.

- [ ] **Step 10.2: Add i18n keys**

In `frontend/src/i18n/locales/en.json`, under the `videos` namespace, add:

```json
{
  "videos": {
    "uploadPhotos": "Upload photo post",
    "uploadPhotosHint": "1–10 images, TikTok only",
    "uploadPhotosTooMany": "Photo posts support at most 10 images",
    "uploadPhotosEmpty": "Pick at least one image",
    "uploadPhotosBadMime": "Only JPG and PNG images are supported",
    "uploadPhotosCta": "Upload",
    "detail": {
      "photoCount": "{{n}} images"
    }
  }
}
```

(Merge into the existing `videos` object; do not duplicate top-level keys.)

- [ ] **Step 10.3: Run typecheck**

```bash
cd frontend && npm run build
```

Expected: build succeeds (no type errors anywhere even before the modal exists, since these are additive types).

- [ ] **Step 10.4: Commit**

```bash
git add frontend/src/api.ts frontend/src/i18n/locales/en.json
git commit -m "feat(photo-post): frontend types + i18n keys"
```

---

## Task 11: Frontend — `PhotoUploadModal`

**Files:**
- Create: `frontend/src/components/PhotoUploadModal.tsx`
- Create: `frontend/src/components/videos/PhotoStackIcon.tsx`

- [ ] **Step 11.1: Build the photo-stack icon**

Create `frontend/src/components/videos/PhotoStackIcon.tsx`:

```tsx
export function PhotoStackIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden
    >
      <rect x="3" y="1.5" width="10" height="10" rx="1.5"
            stroke="currentColor" strokeWidth="1.2" fill="white" />
      <rect x="1.5" y="3" width="10" height="10" rx="1.5"
            stroke="currentColor" strokeWidth="1.2" fill="white" />
    </svg>
  )
}
```

- [ ] **Step 11.2: Build the modal**

Create `frontend/src/components/PhotoUploadModal.tsx`:

```tsx
import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Modal, TextField } from './ui'
import { uploadPhotoPost } from '../api'
import type { ToastState } from '../App'

const ALLOWED_MIME = new Set(['image/jpeg', 'image/png'])
const MAX_IMAGES = 10

interface Props {
  open: boolean
  onClose: () => void
  groups: { id: number; name: string }[]
  onUploaded: () => void
  onToast: (t: ToastState) => void
}

export function PhotoUploadModal({
  open, onClose, groups, onUploaded, onToast,
}: Props) {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [groupId, setGroupId] = useState<number | ''>('')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    if (!open) {
      setFiles([])
      setGroupId('')
      setUploading(false)
      setProgress(0)
    }
  }, [open])

  const previews = files.map((f) => URL.createObjectURL(f))
  useEffect(() => {
    return () => previews.forEach(URL.revokeObjectURL)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files])

  function handlePick(list: FileList | null) {
    if (!list) return
    const incoming = Array.from(list)
    const valid = incoming.filter((f) => ALLOWED_MIME.has(f.type))
    if (valid.length !== incoming.length) {
      onToast({ kind: 'error', text: t('videos.uploadPhotosBadMime') })
    }
    const next = [...files, ...valid].slice(0, MAX_IMAGES)
    if (next.length === MAX_IMAGES && files.length + valid.length > MAX_IMAGES) {
      onToast({ kind: 'error', text: t('videos.uploadPhotosTooMany') })
    }
    setFiles(next)
  }

  function removeAt(i: number) {
    setFiles((prev) => prev.filter((_, idx) => idx !== i))
  }

  function moveItem(from: number, to: number) {
    if (from === to) return
    setFiles((prev) => {
      const copy = [...prev]
      const [item] = copy.splice(from, 1)
      copy.splice(to, 0, item)
      return copy
    })
  }

  async function submit() {
    if (files.length === 0) {
      onToast({ kind: 'error', text: t('videos.uploadPhotosEmpty') })
      return
    }
    setUploading(true)
    setProgress(0)
    try {
      await uploadPhotoPost(
        files,
        groupId === '' ? null : Number(groupId),
        (loaded, total) =>
          setProgress(total ? Math.round((loaded / total) * 100) : 0),
      )
      onToast({ kind: 'success', text: 'Uploaded' })
      onUploaded()
      onClose()
    } catch (e: any) {
      onToast({
        kind: 'error',
        text: e?.message || 'Upload failed',
      })
    } finally {
      setUploading(false)
    }
  }

  if (!open) return null

  return (
    <Modal onClose={onClose} title={t('videos.uploadPhotos')}>
      <div className="space-y-3">
        <p className="text-xs text-gray-500">
          {t('videos.uploadPhotosHint')}
        </p>
        <div className="grid grid-cols-5 gap-2">
          {files.map((f, i) => (
            <div
              key={`${f.name}-${i}`}
              className="relative aspect-square border rounded overflow-hidden bg-gray-50"
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData('text/plain', String(i))
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                const from = Number(
                  e.dataTransfer.getData('text/plain'))
                if (!Number.isNaN(from)) moveItem(from, i)
              }}
            >
              <img
                src={previews[i]}
                alt={f.name}
                className="w-full h-full object-cover"
              />
              <span className="absolute top-1 left-1 px-1 text-xs bg-black/60 text-white rounded">
                {i + 1}
              </span>
              <button
                type="button"
                className="absolute top-1 right-1 px-1 text-xs bg-black/60 text-white rounded"
                onClick={() => removeAt(i)}
                aria-label="Remove"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png"
          multiple
          hidden
          onChange={(e) => handlePick(e.target.files)}
        />
        <Button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading || files.length >= MAX_IMAGES}
        >
          {files.length === 0
            ? t('videos.uploadPhotosCta')
            : `+ Add (${files.length}/${MAX_IMAGES})`}
        </Button>
        <div>
          <label className="text-xs block mb-1">Group</label>
          <select
            value={groupId}
            onChange={(e) =>
              setGroupId(e.target.value === '' ? '' : Number(e.target.value))
            }
            disabled={uploading}
            className="w-full border rounded p-1"
          >
            <option value="">— Unassigned —</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        </div>
        {uploading && (
          <div className="text-xs text-gray-500">
            Uploading… {progress}%
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose} disabled={uploading}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={submit}
            disabled={uploading || files.length === 0}
          >
            {t('videos.uploadPhotosCta')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
```

If `Modal` / `Button` props in this codebase differ (e.g. `Modal` expects `isOpen` not just `open`), adapt to match `frontend/src/components/ui.tsx`. Read that file first.

- [ ] **Step 11.3: Run typecheck**

```bash
cd frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 11.4: Commit**

```bash
git add frontend/src/components/PhotoUploadModal.tsx frontend/src/components/videos/PhotoStackIcon.tsx
git commit -m "feat(photo-post): PhotoUploadModal component"
```

---

## Task 12: Frontend — wire button into `Videos.tsx` and update list views

**Files:**
- Modify: `frontend/src/pages/Videos.tsx`
- Modify: `frontend/src/components/videos/VideoListView.tsx`
- Modify: `frontend/src/components/videos/VideoGridView.tsx`

- [ ] **Step 12.1: Add the trigger in `Videos.tsx`**

Add to the imports:

```tsx
import { PhotoUploadModal } from '../components/PhotoUploadModal'
```

Add state next to `uploading`:

```tsx
const [photoModalOpen, setPhotoModalOpen] = useState(false)
```

In the toolbar JSX (where the existing Upload button lives), add a sibling button:

```tsx
<Button
  type="button"
  onClick={() => setPhotoModalOpen(true)}
  disabled={uploading}
>
  {t('videos.uploadPhotos')}
</Button>
```

Render the modal at the bottom of the component's return:

```tsx
<PhotoUploadModal
  open={photoModalOpen}
  onClose={() => setPhotoModalOpen(false)}
  groups={groups}
  onUploaded={() => refresh()}
  onToast={onToast}
/>
```

- [ ] **Step 12.2: Add `media_kind` / `asset_count` rendering in list view**

In `frontend/src/components/videos/VideoListView.tsx` (read it first to find where `duration_seconds` is displayed). Wherever the duration chip is rendered, branch:

```tsx
{item.media_kind === 'photo' ? (
  <span className="text-xs text-gray-500">
    {t('videos.detail.photoCount', { n: item.asset_count ?? 0 })}
  </span>
) : (
  // existing duration chip
  ...
)}
```

Where the thumbnail is rendered, overlay the icon:

```tsx
<div className="relative">
  {/* existing <img /> */}
  {item.media_kind === 'photo' && (
    <PhotoStackIcon className="absolute top-1 right-1 w-4 h-4 text-white drop-shadow" />
  )}
</div>
```

Import it at the top:

```tsx
import { PhotoStackIcon } from './PhotoStackIcon'
```

Repeat the same two changes in `frontend/src/components/videos/VideoGridView.tsx`.

- [ ] **Step 12.3: Run the dev server, upload one photo post, confirm**

```bash
./run.sh
```

(In a second terminal) start the worker:

```bash
uv run python -m procrastinate -a app.tasks.procrastinate_app worker --queues phones,default
```

Open the dashboard, click "Upload photo post", pick 3 JPEGs (use any local images), pick a group, submit. Confirm:

- Toast shows "Uploaded".
- The new row appears at the top with the photo-stack icon and "3 images" text.

If the photo upload succeeds but stalls at QUEUED → INITIATED, this is expected without a real GeeLark phone connected. The end-to-end remote test happens in production smoke (Task 14).

- [ ] **Step 12.4: Commit**

```bash
git add frontend/src/pages/Videos.tsx frontend/src/components/videos/VideoListView.tsx frontend/src/components/videos/VideoGridView.tsx
git commit -m "feat(photo-post): Videos page upload trigger + list view media_kind"
```

---

## Task 13: Frontend — `VideoDetailPanel` photo branch

**Files:**
- Modify: `frontend/src/components/videos/VideoDetailPanel.tsx`

- [ ] **Step 13.1: Render `assets` grid for photo posts**

Read the existing detail panel and find where the video preview is rendered (probably an `<img>` or `<video>` based on `detail.thumbnail_path`). Add a photo branch:

```tsx
{detail.media_kind === 'photo' ? (
  <div className="grid grid-cols-3 gap-2">
    {detail.assets.map((a) => (
      <div
        key={a.idx}
        className="aspect-square overflow-hidden border rounded bg-gray-50"
      >
        <img
          src={`/static/${a.stored_path.replace(/^.*\/data\//, '')}`}
          alt={a.original_filename ?? `image ${a.idx + 1}`}
          className="w-full h-full object-cover"
        />
      </div>
    ))}
  </div>
) : (
  // existing video preview rendering
  ...
)}
```

Note: the existing video page serves uploads via `/static/` because `app/main.py` mounts `data/` as static. Double-check the URL mapping in `app/main.py` before committing — if `data/` is NOT served as static, the existing video thumbnails wouldn't work either, so they must be — confirm the prefix matches.

If the existing detail panel resolves the upload URL via a helper (e.g. `staticUrl(path)`), use that helper instead of inlining the path manipulation above.

- [ ] **Step 13.2: Run typecheck and smoke**

```bash
cd frontend && npm run build
```

Open a photo post's detail in the dashboard → the 3 images render in a grid.

- [ ] **Step 13.3: Commit**

```bash
git add frontend/src/components/videos/VideoDetailPanel.tsx
git commit -m "feat(photo-post): VideoDetailPanel renders photo assets"
```

---

## Task 14: Manual smoke + production deploy

**Files:** none (operational task)

- [ ] **Step 14.1: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass, count is ≥ pre-existing baseline + new tests (we added ~12 server-side tests across geelark client, generate_caption, thumbnail, repo, posting service, API).

- [ ] **Step 14.2: Build the frontend**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build && cd ..
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 14.3: Local end-to-end smoke**

Run web tier (`./run.sh`) + worker (in a second terminal). Through the UI:

1. Upload one photo post (3 images) to a group with a caption prompt.
2. Watch the worker's logs for the `auto_publish_scan_task` picking it up.
3. Confirm the publication progresses PENDING → QUEUED → INITIATED with a GeeLark task ID logged.

If a test phone is configured, watch through to SUCCESS and observe the share link appearing.

- [ ] **Step 14.4: Back up production PG before migration**

(Per the destructive-migration memory, even additive changes should have a backup.)

```bash
ssh deploy@139.196.52.92 \
  "docker exec svm-postgres pg_dump -U <user> mkt > /tmp/mkt-pre-photo-post-$(date +%Y%m%d).sql"
```

(Adjust DB name / user per `docs/production-deploy.md`.)

- [ ] **Step 14.5: Deploy per the runbook**

Follow `docs/production-deploy.md`:

1. Tarball the working tree (excluding `data/`, `frontend/node_modules/`, `.venv/`).
2. `scp` to `/opt/mkt`.
3. `uv sync`, `uv run alembic upgrade head`.
4. `systemctl restart mkt` AND `systemctl restart mkt-worker` (memory: tarball deploy overwrites app/ shared code, restart worker too, but not while warmup is running).

- [ ] **Step 14.6: Post-deploy smoke**

Upload one photo post on prod (single image is fine to minimize risk), watch it run, confirm a successful publication and share link. Roll back via `pg_restore` if anything looks broken (lower-priority — the migration is additive).

---

## Self-Review Notes

This plan implements every section of `docs/superpowers/specs/2026-06-11-tiktok-photo-post-design.md`:

- §Data model → Task 1 (migration + SQLModel), Task 5 (repo create_photo_post, get_detail with assets), Task 6 (list_page exposes media_kind + asset_count)
- §GeeLark client → Task 2
- §Posting service → Task 7 (_submit + _upload_assets_for_publication), Task 8 (caption switching + cleanup)
- §Caption generation → Task 3
- §API surface → Task 9 (upload-photos + platform guard)
- §Frontend → Tasks 10, 11, 12, 13
- §Testing → tests are written inline TDD-style in each task
- §Rollout → Task 14
- §Failure modes — partial upload rollback (Task 9), preflight asset existence (Task 7's `_upload_assets_for_publication`), platform constraint enforcement at three layers (Task 7 `_submit`, Task 9 API guard, Task 9 upload always tiktok-only)
