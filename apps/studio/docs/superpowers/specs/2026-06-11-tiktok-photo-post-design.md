# TikTok Photo Post (Carousel) — Design

Date: 2026-06-11
Status: Draft

## Goal

Add support for publishing TikTok photo carousels (a.k.a. TikTok Photos /
图集) alongside the existing video pipeline. Marketers should be able to:

1. Upload 1–10 images as a single photo post from the Videos page.
2. Have Gemini generate a caption for the carousel as a whole (multimodal,
   all images in order).
3. Push the resulting publication into the same auto_publish scheduler
   that already drives video posts — same cooldown, same retry, same
   status reconciliation, same dashboard.

## Non-goals

- Instagram, Facebook, or YouTube carousel posts. GeeLark's photo
  endpoint we're wiring up is TikTok-only. Adding IG carousel later
  would be an additional RPA endpoint + a `LOGGED_IN`/`LAST_POSTED`
  column reuse, but is out of scope here.
- More than 10 images per post. TikTok permits 35 and GeeLark passes
  through, but we cap at 10 in both the upload UI and the API to keep
  the surface conservative.
- Manual "Publish photo post" button. The user has explicitly removed
  manual warmup/publish actions; this feature is automation-only via
  `auto_publish_scan`.
- A new prompt template for image-vs-video. The current `PROMPT_TEMPLATE`
  in `generate_caption.py` is media-agnostic and used as-is.

## GeeLark API reference

TikTok photo posts share the same endpoint as TikTok video posts:

- POST `/open/v1/task/add`
- `taskType: 3` (vs `1` for video)
- Per-task body: `{ envId, scheduleAt, images: [resourceUrl, ...],
  videoDesc?, needShareLink? }`
- No `cover` field — first image is auto-cover.
- Status / `shareLink` / `failCode` / `resultImages` follow the same
  schema as video posts; `/open/v1/task/query` and
  `/open/v1/task/detail` work unchanged.
- Image upload uses the existing `/open/v1/upload/getUrl` flow with
  `fileType: jpg | png`; `geelark_client._FILETYPE_ALIASES` already
  handles `jpeg → jpg`.

Source: GeeLark OpenAPI spec mirror
`docs/zh_cn/云手机API/自动化/TikTok/添加视频_图集_养号任务.md`.

## Data model

### `videos` — add column

```sql
ALTER TABLE videos
  ADD COLUMN media_kind TEXT NOT NULL DEFAULT 'video';
CREATE INDEX idx_videos_media_kind ON videos (media_kind);
```

Values: `'video' | 'photo'`. Existing rows default to `'video'`.

### New table — `video_assets`

```sql
CREATE TABLE video_assets (
    id                SERIAL PRIMARY KEY,
    video_id          INT  NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    idx               INT  NOT NULL,
    stored_path       TEXT NOT NULL,
    original_filename TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (video_id, idx)
);
```

Conventions:

- `media_kind='video'` (legacy): `videos.stored_path` /
  `thumbnail_path` / `duration_seconds` are used as today;
  `video_assets` has zero rows for the video.
- `media_kind='photo'`: `videos.stored_path` is NULL,
  `videos.duration_seconds` is NULL, `videos.thumbnail_path` points
  at a generated 320px JPEG copy of `idx=0`, and `video_assets`
  contains 1–10 rows ordered by `idx`.
- `publications` is unchanged. A photo post is exactly one row in
  `publications` (video_id, platform='tiktok'). The publication state
  machine (PENDING → QUEUED → INITIATED → SUCCESS/FAILURE) is reused.
- Asset deletion: `ON DELETE CASCADE` removes `video_assets` rows when
  the parent video is deleted; the repository layer must also remove
  the on-disk image files at the same path it currently removes
  `stored_path` for videos.

### Alembic migration

`alembic/versions/<hash>_videos_media_kind_and_video_assets.py` — one
revision, two operations: `add_column('videos', 'media_kind', ...,
server_default='video', nullable=False)` and `create_table(
'video_assets', ...)` with the index + FK + unique constraint. No
backfill needed (default does the work).

## GeeLark client

`geelark_client.py` — new method, sitting next to
`create_video_post_task`:

```python
def create_tiktok_photo_post_task(
    self,
    env_id: str,
    image_urls: list[str],
    video_desc: str | None = None,
) -> str:
    """Schedule a TikTok carousel post (taskType 3). Returns the task ID.
    image_urls must be pre-uploaded resourceUrls from upload_file()."""
```

Builds `{taskType: 3, list: [{scheduleAt, envId, images, videoDesc?,
needShareLink: True}]}`, POSTs to `/open/v1/task/add`, returns
`data["taskIds"][0]`. `needShareLink: True` matches the video path so
`shareLink` comes back on success.

`_FILETYPE_ALIASES` is unchanged — already maps `jpeg → jpg`.
`upload_file`, `query_tasks`, `get_task_detail` are reused.

## Posting service

`app/services/posting_service.py`:

### `_submit`

Branch on the number of asset URLs (the caller decides the shape; the
dispatcher stays simple):

```python
def _submit(client, platform, env_id, asset_urls: list[str], caption):
    if platform == "tiktok":
        if len(asset_urls) == 1:
            return client.create_video_post_task(
                env_id, asset_urls[0], video_desc=caption)
        return client.create_tiktok_photo_post_task(
            env_id, asset_urls, video_desc=caption)
    # youtube / instagram / facebook unchanged: use asset_urls[0]
```

### Asset upload helper

```python
def _upload_assets_for_publication(client, v: dict, assets: list[dict]) -> list[str]:
    if v.get("media_kind") == "photo":
        return [
            client.upload_file(a["stored_path"], _ext(a["original_filename"]))
            for a in assets
        ]
    return [client.upload_file(v["stored_path"], _ext(v["original_filename"]))]
```

Used by both `_post_one` (legacy synchronous path) and
`submit_publication_to_geelark` (per-publication Procrastinate path).

### Preflight

For `media_kind='photo'`, preflight checks that every `video_assets`
row's `stored_path` exists on disk. Missing one = FAILURE with
`stored asset missing: <path>` recorded in `publication_attempts`;
GeeLark is never called.

### Caption resolution

`_caption_for_publication` keeps its sibling-publication cache lookup
(captions are per video, not per platform). For a photo post there's
only one publication so the cache effectively does nothing — the
function still works because it falls through to generation on
miss. On a fresh generation:

- `media_kind='video'`: `generate_caption_for_local_file(v.stored_path, ...)`.
- `media_kind='photo'`: `generate_caption_for_local_images([a.stored_path for a in assets], ...)`.

Group → caption prompt resolution is unchanged
(`_resolve_caption_config`), so `CaptionPromptMissing` still blocks
photo publish exactly like video publish.

### Status, retry, cleanup, share_link

Unchanged. `check_publication_one`, `mark_publication_timed_out`,
`schedule_retry_if_allowed`, `_geelark_failure_detail`,
`_maybe_cleanup_video` all work for photo publications without a
branch. Cleanup must also iterate `video_assets` and remove each
file from disk before clearing `stored_path` (which is NULL for
photo, so the existing `clear_stored_path` is still safe to call).

### Platform constraint

Photo publications are only allowed on TikTok. Enforcement points:

1. `POST /api/videos/upload-photos` only creates `(video, tiktok)`
   publications, ignoring any `platforms` form input.
2. `POST /api/videos/{vid}/publish` returns 400 if
   `media_kind='photo' AND platform != 'tiktok'`.
3. `posting_service._submit` is the last line of defense: if it
   receives a photo post for a non-tiktok platform, raise
   `ValueError`. Should be unreachable via the API but cheap to add.

## Caption generation

`generate_caption.py` — new function:

```python
def generate_caption_for_local_images(
    local_paths: list[str],
    *,
    brief: str | None = None,
    max_len: int | None = None,
    thinking_budget: int = 0,
    model: str | None = None,
    banned_terms: list[str] | None = None,
) -> str:
```

Implementation:

- Upload each image to Gemini File API in input order; MIME inferred
  by extension (`jpg`/`jpeg` → `image/jpeg`, `png` → `image/png`).
- Poll each upload to ACTIVE (image processing is near-instant in
  practice but reuse the same `FILE_PROCESSING_POLL` / `_TIMEOUT`
  loop for robustness). Any FAILED / timeout → cleanup uploaded
  files so far + raise RuntimeError.
- Single `_generate_content_with_retry` call whose `contents.parts`
  is `[file_data(image_1), file_data(image_2), ..., text(prompt)]`.
  Order matters — preserves carousel sequence for the model.
- Finally clause deletes all uploaded Gemini files (even on
  exception), matching the video path's hygiene.
- Reuses `build_prompt`, `sanitize_caption`, `banned_terms`
  enforcement, `CAPTION_MAX_LEN_DEFAULT`, and the structural
  "Comment INFO/LINK/BET" check.

`PROMPT_TEMPLATE` is **not** modified. If we discover the model needs
carousel-specific guidance later, that's a follow-up.

## API surface

`app/api/videos.py`:

### New endpoint — `POST /api/videos/upload-photos`

```python
@router.post("/upload-photos")
async def upload_photos(
    files: list[UploadFile] = File(...),
    group_id: int | None = Form(None),
    session: Session = Depends(get_session),
): ...
```

Behavior:

- 400 if `len(files) == 0` or `len(files) > 10`.
- 400 if any file's content type is not in
  `{image/jpeg, image/png}`.
- Save each file via `_save_upload_file` (same dest pattern, same
  `.part → rename` semantics, same `data/` directory).
- Create one `videos(media_kind='photo')` row, then N
  `video_assets(video_id, idx, stored_path, original_filename)`
  rows in input order (single DB transaction via
  `vid_repo.create_photo_post`).
- Create one publication: `pub_repo.find_or_create(video_id=vid,
  platform='tiktok')`. The form's `platforms` field is ignored.
- Generate thumbnail from `idx=0` via a new image branch in
  `thumbnail_service.generate_thumbnail` (PIL resize to 320px JPEG,
  no ffmpeg).
- On any partial failure, roll back: delete `video` row (cascade
  drops asset rows), unlink any files written. Mirrors the existing
  `/upload` error path.
- Wake `auto_publish_scan` if `group_id` is set, matching `/upload`.
- Returns `{"uploaded": 1, "publish_scan_queued": <bool>}`.

### `POST /api/videos/{vid}/publish` — platform guard

```python
v = vid_repo.get(session, vid)
if v["media_kind"] == "photo" and platform != "tiktok":
    raise HTTPException(400, "photo posts can only be published to tiktok")
```

Inserted before the existing eligibility checks. Defensive — the UI
shouldn't surface non-TikTok options for photo posts.

### `GET /api/videos` list response

Adds two fields per item:

- `media_kind: 'video' | 'photo'` (always present)
- `asset_count: int | null` — `NULL` for video rows, integer for
  photo (computed via `LEFT JOIN video_assets … GROUP BY v.id`).

`duration_seconds` stays for video rows; photo rows return NULL there
and the frontend renders `asset_count` instead.

### `GET /api/videos/{vid}/detail`

Adds `assets: [{idx, stored_path, original_filename}, ...]`
ordered by `idx`. Always present; empty list for `media_kind='video'`
so the frontend can use one shape.

## Repositories

`app/repositories/videos.py`:

- `create_photo_post(session, *, group_id, image_paths: list[tuple[str, str]]) -> int`
  — atomic create of one `videos` row + N `video_assets` rows. The
  tuple is `(stored_path, original_filename)`.
- `get_detail(session, vid)` — joins `video_assets` and includes
  `assets` in the output when `media_kind='photo'`.
- `list_page` — SELECT adds `v.media_kind` and `asset_count` (via
  the new join). The status-bucket / cursor / sort logic is
  unchanged; image posts go through the same publication-state
  filter.
- `delete(session, vid)` — pre-collects `video_assets.stored_path`
  rows, deletes the video (cascade clears the asset rows), then the
  caller (`videos.py::delete_video`) unlinks each image plus the
  legacy `videos.stored_path` (which is NULL for photo, safe).
- `update_media_meta` — unchanged; still writes
  `thumbnail_path` and `duration_seconds` (the latter is just
  skipped for photo since we never compute it).

`app/repositories/publications.py` is unchanged. The platform string
column ('tiktok') is enforced at the API layer.

## Scheduler / Procrastinate tasks

- `auto_publish_scan` — no change. It iterates every platform's
  PENDING publications and calls `plan_assignments(platform=...)`;
  photo posts ride in as ordinary `(video, tiktok)` publications and
  are handled in the tiktok pass.
- `publish_publication_task` — no change. It calls
  `submit_publication_to_geelark`, which calls `_submit`, which
  branches on `len(asset_urls)`.
- `check_publication_task` — no change.
- `pic_check`, `proxy_check`, `warmup_account`, `heartbeat` — no
  change.

## Frontend

`frontend/src/pages/Videos.tsx` + new components:

### Upload entry

In the toolbar where "Upload" lives today, add a sibling "Upload
photo post" button. Two distinct entries; no auto-detect via MIME
type at the file picker level (too error-prone).

### `PhotoUploadModal` (new — `frontend/src/components/PhotoUploadModal.tsx`)

- Multi-select `<input type="file" accept="image/jpeg,image/png"
  multiple>`; on change, append to local `files` state.
- Render selected files as a grid of thumbnails (object URLs).
  Each thumbnail has an `×` delete button (top-right) and an
  `idx` badge (top-left).
- Drag-and-drop reordering via native HTML5 DnD events
  (`onDragStart`, `onDragOver`, `onDrop`) — no extra dependency.
  Reorder mutates `files` array.
- Client-side validation: `files.length` between 1 and 10; all
  MIME types are `image/jpeg` or `image/png`. Block submit if
  not satisfied; surface message via `onToast`.
- Group dropdown (single-select), reuses existing `groups` state
  fetched by Videos page.
- Submit: `FormData` with `files` in current order + `group_id` →
  `POST /api/videos/upload-photos` via the existing XHR helper
  (`UploadProgressState` displayed).
- A small hint line: "Photo posts publish to TikTok only."

### List view

`VideoListView` and `VideoGridView` look at `item.media_kind`:

- If `'photo'`: render a small stacked-cards overlay icon on the
  thumbnail (pure SVG, lives in the same dir as `PlatformIcon`).
- Replace the `duration_seconds` chip with `${asset_count} images`.
- `platforms` chips render as today; for photo rows there's only
  the TikTok chip, so this is automatic.

### Detail panel (`VideoDetailPanel`)

When `media_kind='photo'`, replace the single video preview area
with a grid of `assets` (each image opens in a lightbox / modal on
click). All other sections (publications, attempts, retry buttons)
are unchanged.

### Types

`frontend/src/api.ts` (or wherever `VideoSummary` / `VideoDetail`
live):

```ts
type MediaKind = 'video' | 'photo'

interface VideoSummary {
  // existing fields...
  media_kind: MediaKind
  asset_count: number | null  // null for video rows
}

interface VideoAsset {
  idx: number
  stored_path: string
  original_filename: string | null
}

interface VideoDetail extends VideoSummary {
  // existing fields...
  assets: VideoAsset[]  // [] for video rows
}
```

### i18n

`frontend/src/i18n/locales/en.json` additions:

- `videos.uploadPhotos`: "Upload photo post"
- `videos.uploadPhotos.hint`: "1–10 images, TikTok only"
- `videos.uploadPhotos.tooMany`: "Photo posts support at most 10 images"
- `videos.uploadPhotos.empty`: "Pick at least one image"
- `videos.uploadPhotos.badMime`: "Only JPG and PNG images are supported"
- `videos.uploadPhotos.cta`: "Upload"
- `videos.detail.photoCount`: "{{n}} images"

## Failure modes / edge cases

- **Partial upload on the photo modal** — if one of N images is the
  wrong MIME, reject the whole submit client-side rather than the
  server saving N–1 + erroring. The server still validates as a
  safety net.
- **Auto-cleanup on a photo publication's terminal SUCCESS** —
  `_maybe_cleanup_video` should iterate `video_assets` for the
  photo and `os.remove` each file before the existing
  `vid_repo.clear_stored_path` call (which for photo is a no-op
  but harmless).
- **A photo post that somehow ends up with non-tiktok publication
  rows** — defensive `ValueError` in `_submit` keeps GeeLark
  protected. Inserting one manually via SQL is the only way to
  reach this.
- **Deletion mid-publish** — the existing CASCADE + lease/lock
  setup already covers this; no special handling for photo.
- **Image too large** — GeeLark caps each carousel slide at 5MB.
  We don't pre-check (it would require reading file size on the
  server and adds little value); GeeLark will return a fail code
  and the publication goes FAILURE with the screenshot, like any
  other rejection.

## Testing

New tests:

- `tests/test_geelark_client.py::test_create_tiktok_photo_post_task`
  — POST body shape, taskType=3, images list passes through,
  `needShareLink=True`, optional `videoDesc`.
- `tests/test_posting_service.py::test_submit_routes_photo_to_carousel_api`
  — `_submit` with `len(asset_urls) > 1` and `platform='tiktok'`
  calls `create_tiktok_photo_post_task`.
- `tests/test_posting_service.py::test_preflight_missing_image_marks_failure`
  — photo publication with a deleted asset path goes FAILURE
  without touching GeeLark.
- `tests/test_videos_api.py::test_upload_photos_creates_video_assets_and_pub`
  — `POST /api/videos/upload-photos` with 3 images creates 1 video
  row (media_kind=photo) + 3 asset rows + 1 publication
  (platform=tiktok).
- `tests/test_videos_api.py::test_upload_photos_rejects_over_10`,
  `..._rejects_bad_mime`, `..._rejects_empty`.
- `tests/test_videos_api.py::test_publish_one_rejects_photo_to_youtube`.
- `tests/test_videos_repo.py::test_create_photo_post_and_get_detail_returns_assets`.
- `tests/test_generate_caption.py::test_generate_caption_for_local_images_passes_each_file`
  (mock Gemini client, assert each file_data part is included in
  order, single text prompt last).

Total baseline today is `≥ 385 passed` per CLAUDE.md; the new tests
should land that count at ~395.

## Rollout

- Local: write migration, apply on `mkt_test`, write code, run
  full pytest (≥ 395), `npm run build`, manual smoke via
  `./run.sh` + worker — upload 3 images, watch the publication
  go through QUEUED → INITIATED → SUCCESS on a test phone.
- Production: `pg_dump` (per the "deploy after every change" /
  "destructive migration" memo — though this migration is
  additive, the user wants a backup before any schema change),
  tarball deploy via `docs/production-deploy.md`,
  `alembic upgrade head`, restart `mkt` + `mkt-worker`.

## Open follow-ups (explicitly out of scope)

- Instagram carousel posts (separate RPA endpoint).
- Image reordering UX with drag handles on touch devices.
- Per-image alt text / caption (TikTok doesn't expose it).
- Carousel-specific caption prompt template.
- "Upload multiple photo posts at once" — would require either
  predictable per-post groupings or a different UI metaphor.
