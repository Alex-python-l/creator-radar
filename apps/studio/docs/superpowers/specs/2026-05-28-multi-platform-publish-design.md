# Multi-Platform Auto-Publish (YouTube + Instagram + Facebook)

**Date:** 2026-05-28 (rewritten 2026-05-29 to match Phase 1+2 stack)
**Status:** Design approved, plan ready
**Branch:** `feat/multi-platform-publish` (Task 1 already committed: `3d74a8e`)

## Goal

Extend the studio so the same set of GeeLark cloud phones can publish to
YouTube Shorts, Instagram Reels and Facebook Reels in addition to
TikTok, driven by the same upload → assign → auto-publish flow. The
data model becomes platform-agnostic from day one so adding a fifth
platform later is a single-file change.

## Stack we're building on

This spec lands on top of Phase 1 (PG migration) and Phase 2
(Procrastinate worker). The implementation MUST follow the patterns
already established:

- **Persistence:** PostgreSQL via `app/db.py::engine` + `Session`;
  all schema changes via Alembic revisions in `alembic/versions/`.
  `app/models.py` holds SQLModel `table=True` classes — Alembic's
  `target_metadata = SQLModel.metadata` (via `alembic/env.py`).
- **Repositories** (`app/repositories/*.py`): every fn takes
  `session: Session` first, uses `session.exec(select(...))`, returns
  `.model_dump()` dicts.
- **Services** (`app/services/*.py`): take `session, client, ...`,
  delegate to repos, return generators of log lines where applicable.
  `OPERATION_LOCK` (`app/locks.py`) still serializes the web-tier
  synchronous publish/start-phones paths.
- **Tasks** (`app/tasks/*.py`): every long-running job runs in the
  Procrastinate worker process. Periodic jobs use
  `@procrastinate_app.periodic(cron="...")` (e.g. `pic_check.py`).
  Per-item jobs (one publication = one task) use
  `@procrastinate_app.task(lock="phones", queue="phones")` and are
  `defer_async`'d from the web tier or another task. The web tier
  must NOT do cloud-phone work synchronously for new flows.
- **Testing:** PG test database `mkt_test`. `tests/conftest.py`
  provides `session` / `client` / `procrastinate_app` (InMemory
  connector) fixtures. `_truncate_tables` runs before every test.
  Baseline on `feat/multi-platform-publish`: 292 passed, 20 skipped.

Anything that contradicts the above is wrong and should not be merged.

## Why now

Each cloud phone hosts multiple social apps (TT, YT, IG, FB).
Publishing only to TikTok wastes the per-minute cost of those phones.
GeeLark now exposes RPA endpoints for each app:

- `/open/v1/rpa/task/youtubePubShort` — YouTube Shorts
- `/open/v1/rpa/task/instagramPubReels` — Instagram Reels
- `/open/v1/rpa/task/faceBookPubReels` — Facebook Reels

All four platforms (TikTok via the legacy `/open/v1/task/add`, the
other three via `/rpa/task/*`) share the same scheduling primitive:
submit a task with `scheduleAt = now`, poll status via
`/open/v1/task/query`, status code 3 = done, 4|7 = failed.

## API contracts (GeeLark)

### TikTok (already implemented)

`POST /open/v1/task/add` with `{ taskType: 1, list: [{scheduleAt, envId, video, videoDesc}] }`.
Returns `data.taskIds[0]`.

### YouTube Shorts

`POST /open/v1/rpa/task/youtubePubShort`. Required: `scheduleAt`, `id`,
`title` (≤100), `video`. Hidden defaults: `sameStyleVoice=0`,
`originalVoice=100`, `isDisclosureMandatory=false`. Returns
`data.taskId` (single string).

### Instagram Reels

`POST /open/v1/rpa/task/instagramPubReels`. Required: `scheduleAt`,
`id`, `description` (≤2200), `video` (**array**, up to 10). Hidden
defaults: `aiTag=false`, `needShareLink=false`. Returns `data.taskId`.

### Facebook Reels

`POST /open/v1/rpa/task/faceBookPubReels`. Required: `scheduleAt`,
`id`, `description` (≤500), `video` (single string). Hidden defaults:
`needShareLink=false`, `page` omitted. Returns `data.taskId`.

## Decisions (preserved from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Account model | One phone hosts multiple apps; one row in `accounts` carries `<platform>_logged_in` + `<platform>_username` + `last_posted_at_<platform>` per platform | Matches reality of GeeLark cloud phones |
| Video ↔ platform | One video may publish to multiple platforms | Operator may want TT + YT + IG + FB on the same clip |
| Caption source | Single Gemini caption, truncated per platform's limit at the `geelark_client` layer | YAGNI — one prompt to maintain, platform-aware truncation lives where the API limit is known |
| Platform-specific params | Hard-coded sensible defaults, hidden from UI | Minimal surface area; flip in code if ops needs override |
| Eligibility | Manual `<platform>_logged_in=1` flag per platform | Explicit operator control beats silent RPA failures |
| Cooldown | Per-platform, 2h each | Same phone can post TT now, YT later, IG later, FB later |
| Scheduling | One Procrastinate periodic task `auto_publish_scan` (cron hourly) defers one `publish_publication_task` per eligible publication | Mirrors phase-2 per-video task pattern; each defer is one logical unit of phone work |
| Data shape | New `publications` table, full Alembic migration of existing TikTok rows | Future-proof; adding a platform is a string + a few columns |

## Platform set — single source of truth

Already committed in Task 1 (commit `3d74a8e`,
`app/services/platforms.py`). Exports:

```python
PLATFORMS         # tuple: ("tiktok", "youtube", "instagram", "facebook")
TEXT_LIMIT        # dict: per-platform character limit (TT/IG 2200, YT 100, FB 500)
LAST_POSTED_COL   # dict: platform -> accounts column name
LOGGED_IN_COL     # dict: platform -> accounts column name
USERNAME_COL      # dict: platform -> accounts column name
validate(value)   # raises ValueError on unknown / non-string / empty
```

API validation, service dispatch, repository updates, the periodic
scan task, and per-publication Procrastinate tasks all import from
here. No platform literal is hard-coded outside this module (except
where SQL needs a constant default for the migration).

## Architecture

### Data model (SQLModel + Alembic)

`app/models.py` changes:

**`Account`** — add nine columns (three per new platform; TikTok is
already represented by `tiktok_logged_in`, `tiktok_username`,
`last_posted_at`):

```python
youtube_logged_in: int = Field(default=0, nullable=False)
youtube_username: Optional[str] = None
last_posted_at_youtube: Optional[datetime] = None

instagram_logged_in: int = Field(default=0, nullable=False)
instagram_username: Optional[str] = None
last_posted_at_instagram: Optional[datetime] = None

facebook_logged_in: int = Field(default=0, nullable=False)
facebook_username: Optional[str] = None
last_posted_at_facebook: Optional[datetime] = None
```

`accounts.last_posted_at` keeps its on-disk name but is semantically
"last TikTok post" (matches the existing `tiktok_logged_in` /
`tiktok_username` asymmetry). Not renamed — would cascade through too
much code with no behavior change.

**`Video`** — REMOVE these columns (they move to `publications`):

```
account_id, result, geelark_task_id, notes, posted_at, last_checked_at
```

`Video` becomes pure material metadata: `id`, `original_filename`,
`stored_path`, `caption`, `drive_file_id`, `uploaded_at`, `group_id`.

**`Publication`** — new SQLModel:

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
        sa_column=Column(Integer, ForeignKey("videos.id", ondelete="CASCADE"),
                         nullable=False))
    platform: str = Field(nullable=False)  # one of PLATFORMS
    account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"),
                         nullable=True))
    result: str = Field(default="PENDING", nullable=False)
    # PENDING -> QUEUED -> INITIATED -> SUCCESS|FAILURE
    caption: Optional[str] = None
    geelark_task_id: Optional[str] = None
    notes: Optional[str] = None
    posted_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP"),
                         nullable=False))
```

`result` reuses the existing state machine (Video already had QUEUED
in Phase 2). The transition is PENDING (waiting on a scan to claim it) →
QUEUED (Procrastinate job deferred) → INITIATED (GeeLark task
submitted, polling) → SUCCESS / FAILURE (terminal).

### Alembic migration

One new revision, generated with
`uv run alembic revision -m "publications table + platform columns"`,
with manual `upgrade()` / `downgrade()` body (NOT autogenerate — the
data-copy step needs careful ordering):

```python
def upgrade():
    # 1) Nine new account columns.
    for col in ("youtube_logged_in", "instagram_logged_in", "facebook_logged_in"):
        op.add_column("accounts",
            sa.Column(col, sa.Integer(), nullable=False, server_default="0"))
    for col in ("youtube_username", "instagram_username", "facebook_username"):
        op.add_column("accounts",
            sa.Column(col, AutoString(), nullable=True))
    for col in ("last_posted_at_youtube",
                "last_posted_at_instagram",
                "last_posted_at_facebook"):
        op.add_column("accounts", sa.Column(col, sa.DateTime(), nullable=True))

    # 2) publications table + UNIQUE + INDEX.
    op.create_table("publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(),
                  sa.ForeignKey("videos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("platform", AutoString(), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("accounts.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("result", AutoString(), nullable=False,
                  server_default="PENDING"),
        sa.Column("caption", AutoString(), nullable=True),
        sa.Column("geelark_task_id", AutoString(), nullable=True),
        sa.Column("notes", AutoString(), nullable=True),
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

    # 4) Drop now-redundant videos columns (reverse order to avoid FK issues).
    for col in ("last_checked_at", "posted_at", "notes",
                "geelark_task_id", "result", "account_id"):
        op.drop_column("videos", col)


def downgrade():
    op.add_column("videos", sa.Column("account_id", sa.Integer(),
                                       nullable=True))
    op.add_column("videos", sa.Column("result", AutoString(), nullable=True,
                                       server_default="PENDING"))
    op.add_column("videos", sa.Column("geelark_task_id", AutoString(),
                                       nullable=True))
    op.add_column("videos", sa.Column("notes", AutoString(), nullable=True))
    op.add_column("videos", sa.Column("posted_at", sa.DateTime(),
                                       nullable=True))
    op.add_column("videos", sa.Column("last_checked_at", sa.DateTime(),
                                       nullable=True))
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

`alembic downgrade -1; alembic upgrade head` round-trips cleanly
except for any non-TikTok publications written between the two — those
are silently dropped by `downgrade()` (operator must `pg_dump` first
if real data exists; covered in the production runbook).

### Service layer

**`geelark_client.py`** gets three new methods. Each accepts the
operator-provided caption and truncates to its platform's API limit
so callers don't need to know.

```python
def create_youtube_post_task(self, env_id, video_url, title) -> str: ...

def create_instagram_post_task(self, env_id, video_urls: list[str],
                                description) -> str: ...

def create_facebook_post_task(self, env_id, video_url,
                               description) -> str: ...
```

Each method sets `scheduleAt = int(time.time())`, truncates input to
its platform's `TEXT_LIMIT`, hard-codes the platform-specific defaults
listed under "API contracts" above, POSTs, reads `data.taskId`, and
raises `RuntimeError` if missing.

**`app/services/posting_service.py`** restructures around publications.
Old TikTok-only paths are REPLACED:

- `_caption(session, local_path) -> str` — unchanged.
- `_submit(client, platform, env_id, video_url, caption) -> str` —
  dispatches by platform string to one of the four client methods.
  For Instagram, wraps the URL in `[video_url]`.
- `_post_one(session, client, pub, video, acc)` — generator. Calls
  `_caption`, `client.upload_file`, `_submit`. On success writes
  `publications.result='INITIATED'` + `geelark_task_id` + `caption` +
  `account_id` + `posted_at` via `pub_repo.mark_initiated`; stamps
  `accounts.last_posted_at_<platform>` via
  `acc_repo.touch_last_posted`. On failure writes
  `publications.result='FAILURE'` + `notes` via
  `pub_repo.mark_failure`.
- `publish_for_accounts(session, client, account_ids, *, platform, cooldown_hours, force)`
  — generator. Reads PENDING publications for the requested platform
  via `pub_repo.list_pending(session, platform=platform)`, filters by
  per-platform eligibility (`accounts.<platform>_logged_in=1`) and
  cooldown (`accounts.last_posted_at_<platform>`), calls `_post_one`.
- `publish(session, client, *, platform, cooldown_hours, force, group_id)`
  — synchronous entry point used by the legacy `/api/videos/publish`
  endpoint. Resolves group_id → account_ids → delegates.
- `check_initiated(session, client) -> str` — synchronous polling.
  Reads INITIATED publications across all platforms, calls
  `client.query_tasks`, transitions to SUCCESS / FAILURE. When all
  publications of a video are terminal, deletes the local stored file
  and clears `videos.stored_path`. Kept for parity with the existing
  `/api/videos/publish` flow.
- `plan_assignments(session, *, platform: str) -> list[int]` — NEW
  helper used by the periodic scan task. Returns publication IDs to
  defer; also flips `publications.result` to `QUEUED` and stamps
  `account_id` synchronously inside the same DB transaction to claim
  the work and prevent double-defer on the next scan.
- `submit_publication_to_geelark(session, client, *, publication_id) -> str`
  — single-publication submit used by the Procrastinate per-publication
  task. Mirrors phase-2's `submit_video_to_geelark` shape. Caller
  (the task) handles retries.
- `check_publication_one(session, client, publication_id) -> str` —
  single-publication status poll, mirrors phase-2's `check_one`.
  Returns `"running" | "success" | "failure" | "unknown"`.

**`app/services/assignment.py`**:

- `build_pool(accounts, platform="tiktok")` filters by `warmed_up=1`
  AND `LOGGED_IN_COL[platform]=1`. Returns pool sorted by each
  platform's own `LAST_POSTED_COL[platform]`.
- `assign(videos, pool, cooldown_hours)` unchanged in shape.

### Repositories

**New `app/repositories/publications.py`** — same shape as
`videos.py`, all SQLModel/Session style:

```python
def create(session, *, video_id, platform) -> int
def get(session, pub_id) -> dict | None
def list_for_video(session, video_id) -> list[dict]
def list_pending(session, platform: str) -> list[dict]
    # JOIN videos to include stored_path / original_filename / group_id
def list_initiated(session) -> list[dict]
    # JOIN videos to include stored_path
def mark_queued(session, pub_id, account_id) -> None
def mark_initiated(session, pub_id, account_id, caption, task_id, notes) -> None
def mark_failure(session, pub_id, account_id, notes) -> None
def set_result(session, pub_id, result, notes) -> None
def video_ids_all_terminal(session) -> set[int]
    # videos where every publication is SUCCESS or FAILURE; used for
    # local file cleanup
```

**`app/repositories/videos.py`** shrinks — remove `set_account`,
`mark_queued`, `mark_initiated`, `mark_initiated_from_task`,
`mark_failure`, `set_result`, `list_pending`, `list_initiated`. Keep
`create`, `get`, `list_all` (embed publications array via JOIN),
`bulk_set_group`, `existing_drive_file_ids`, `clear_stored_path`,
`delete`, `count_for_group` (rewrite over publications).

**`app/repositories/accounts.py`**:

- Add `touch_last_posted(session, account_id, platform) -> None`
  using `platforms.LAST_POSTED_COL[validate(platform)]`. Stamps the
  per-platform column with `datetime.utcnow()` and bumps
  `updated_at`. Uses `getattr(account, col)` style — no raw SQL.
- The existing `update()` already uses field-name filtering via
  SQLModel column names, so the nine new fields work automatically
  once the migration runs — no `_COLUMNS` set to extend.

### Tasks (Procrastinate)

Two new files / changes in `app/tasks/`:

**`app/tasks/publish.py`** — replace existing `publish_video_task` and
`check_publish_status_task` with publication-aware versions:

```python
@procrastinate_app.task(lock="phones", queue="phones",
                         name="publish_publication")
async def publish_publication_task(publication_id: int):
    with session_scope() as session:
        try:
            task_id = posting_service.submit_publication_to_geelark(
                session,
                geelark_service.make_client(),
                publication_id=publication_id,
            )
        except Exception as e:
            # publication.result already set to FAILURE inside submit_…
            return f"FAILURE: {e}"
    # Chain status-check task with 30s delay (mirrors phase-2 pattern):
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

**New `app/tasks/auto_publish.py`** — periodic scan + defer:

```python
@procrastinate_app.periodic(cron="0 * * * *")  # top of every hour
@procrastinate_app.task(queue="default", name="auto_publish_scan",
                         pass_context=True)
async def auto_publish_scan_task(context, timestamp: int):
    """Every hour, scan PENDING publications across all platforms,
    pick eligible (warmed-up + platform-logged-in + outside cooldown)
    accounts, mark each chosen publication QUEUED + write account_id
    transactionally, and defer one publish_publication_task per pair."""
    from app.services.platforms import PLATFORMS
    from app.db import session_scope
    from app.services import posting_service

    deferred = 0
    for platform in PLATFORMS:
        with session_scope() as session:
            publication_ids = posting_service.plan_assignments(
                session, platform=platform)
        for pid in publication_ids:
            await publish_publication_task.defer_async(publication_id=pid)
            deferred += 1
    return f"{deferred} publication(s) deferred"
```

**`app/tasks/__init__.py`** — add `"app.tasks.auto_publish"` to the
`import_paths=[...]` list. Existing tasks (warmup / pic_check /
proxy_check / heartbeat) are untouched.

**Backward compatibility:** the existing `submit_video_to_geelark` /
`check_one` / `publish_video_task` / `check_publish_status_task` are
REPLACED, not kept alongside. Their callers (the
`POST /api/videos/{vid}/publish` endpoint) move to defer the new
publication-shaped task with an explicit `platform` query parameter.

### API

**`app/api/videos.py`** changes:

| Method | Path | Change |
|---|---|---|
| POST | `/api/videos/upload` | New form field `platforms` (default `"tiktok"`, comma-separated). Backend creates the `Video` row plus one `Publication` per chosen platform. |
| PATCH | `/api/videos/{vid}` | Body `{platform, account_id}` — set account on a specific platform's publication; creates the publication if missing. |
| POST | `/api/videos/publish` | New query param `platform` (required). 400 on missing/unknown. Synchronous legacy path; kept for compatibility. |
| POST | `/api/videos/publish-stream` | Same. |
| POST | `/api/videos/{vid}/publish` | New query param `platform` (required). Defers `publish_publication_task` for the (video, platform) publication; returns `{status: "QUEUED", job_id, publication_id}`. |
| GET | `/api/videos` | Each video now returns `publications: [{platform, result, account_id, geelark_task_id, posted_at, ...}]`. |
| DELETE | `/api/videos/{vid}` | No change — `ON DELETE CASCADE` cleans publications. |

**`app/api/accounts.py`** changes:

| Method | Path | Change |
|---|---|---|
| GET | `/api/accounts` | Each row now includes the nine new platform fields. |
| PATCH | `/api/accounts/{id}` | Allows writing the nine new fields. |
| POST | `/api/accounts/publish` | Body now requires `platform`. 400 otherwise. |

`app/api/dashboard.py` is unchanged (its current SUCCESS-counting
already reads `Video.result`, which moves to publications — the
counting query is rewritten to `SELECT COUNT(*) FROM publications
WHERE result='SUCCESS' ...` joined to `videos.posted_at`-equivalent).

### Frontend

Identical to previous design — frontend is unaffected by the PG/
Procrastinate stack.

- **`api.ts`**: export `Platform = 'tiktok' | 'youtube' | 'instagram' | 'facebook'`,
  `PLATFORMS`, `PLATFORM_LABEL`. `publishAccounts(ids, platform)` and
  `setVideoAccount(vid, platform, accountId)`.
- **`Videos.tsx`**: upload dialog gains a four-way platform
  multi-select (default `[tiktok]`). The list renders per-publication
  badges; history table flattens one row per `(video, publication)`.
- **`Accounts.tsx`**: three new columns (YT / IG / FB), each a
  checkbox bound to `<platform>_logged_in` and an editable
  `<platform>_username` input.
- No new tab/page — everything inline.

### Untouched

- `warmup_service`, `generate_caption`, `geelark_service.set_pics`,
  `proxy_check_service`, `drive_import` (adds one line: create a
  default `'tiktok'` publication when it inserts a Video row, same
  default as `POST /api/videos/upload`).
- `app/tasks/warmup.py`, `app/tasks/pic_check.py`,
  `app/tasks/proxy_check.py`, `app/tasks/heartbeat.py`.
- Auth, settings page, dashboard SSE feed, `app/sse.py`,
  `app/locks.py::OPERATION_LOCK`.

## Testing

Keep current 292 passed / 20 skipped green and add coverage. Every new
test uses the existing `session` / `client` / `procrastinate_app`
fixtures from `tests/conftest.py`. PG-backed test DB is `mkt_test`
(set by conftest).

| File | Cases |
|---|---|
| New `tests/test_repositories_publications.py` | CRUD on publications; `UNIQUE(video_id, platform)` enforcement (PG raises `IntegrityError`); `ON DELETE CASCADE` from videos; `ON DELETE SET NULL` from accounts; `video_ids_all_terminal` helper logic. |
| `tests/test_repositories.py` | `touch_last_posted` for each of the four platforms; rejects unknown platform; new `<platform>_logged_in` / `<platform>_username` columns roundtrip. |
| `tests/test_assignment.py` | `build_pool(platform=p)` excludes accounts where `<p>_logged_in=0`, for each platform; cooldown uses each platform's own `last_posted_at_<p>` column. |
| `tests/test_services.py` | Existing TikTok cases switched to read/write publications; one new `_post_one`-flavor test per added platform (mock the right client method); mixed-platform `check_initiated`; file cleanup only when every publication terminal; `submit_publication_to_geelark` and `check_publication_one` for the per-publication task path; `plan_assignments` returns PIDs + marks QUEUED. |
| `tests/test_api.py` | Upload with all four platforms → four publications; publish without `platform` → 400; publish with unknown platform → 400; GET response shape includes publications array; accounts endpoint exposes the nine new columns; `POST /api/videos/{vid}/publish?platform=...` returns `{status: "QUEUED", job_id, publication_id}`. |
| `tests/test_geelark_client.py` | One block per added platform: mock `_post`, assert endpoint, payload shape (array vs string `video`, hidden defaults), text-limit truncation, `RuntimeError` when `data.taskId` missing. |
| New `tests/test_tasks_publish.py` | Using `procrastinate_app` fixture's `InMemoryConnector`, exercise `publish_publication_task` end-to-end against a mocked GeeLark client; verify chained `check_publication_status_task` defer with `schedule_in`; verify periodic `auto_publish_scan_task` defers one task per eligible publication and marks them QUEUED. |
| `tests/test_platforms.py` | Already committed in Task 1 — no change. |

Migration test: `tests/test_migrations.py` (new) — runs
`alembic upgrade head` against `mkt_test`, inserts a legacy-shape
fixture via raw SQL on a fresh schema **before** the new revision is
applied, then upgrades and verifies the TikTok publication backfill +
old column drops. Plan Task 2 spells out the exact mechanics
(alembic.command.upgrade with `script_directory` pinned to a fixture
dir, or using `op.execute(...)` to inject legacy rows in a test-only
revision). If too brittle, the plan falls back to asserting upgrade
idempotency only (re-running `upgrade head` is a no-op).

## Risks and rollback

1. **In-flight INITIATED rows at migration time.** Mitigation: stop
   both the `mkt` and `mkt-worker` systemd units before
   `uv run alembic upgrade head`. The new code paths refuse to start
   (import errors on the missing columns) if migration wasn't applied,
   so the failure mode is loud.

2. **Stored video files linger.** Multi-platform publication waits for
   every platform to terminalize before deleting the local file. Worst
   case one platform is stuck and the file persists; operator can
   manually `UPDATE publications SET result='FAILURE'` to unblock.

3. **Rollback.** `alembic downgrade -1` reverses cleanly for the
   TikTok-only subset; any YT/IG/FB publications written in the
   meantime are dropped. Production runbook must require a `pg_dump`
   before the upgrade.

4. **Procrastinate scan duplication.** The hourly
   `auto_publish_scan_task` may double-defer if its DB transaction
   doesn't claim work atomically. `plan_assignments` therefore flips
   `publications.result` from `PENDING` to `QUEUED` in the same
   `session.commit()` as it selects, and the periodic filter is
   `WHERE result='PENDING'` only. The per-publication task transitions
   QUEUED → INITIATED on success.

5. **GeeLark RPA failure modes.** YT/IG/FB RPA tasks may silently
   succeed-then-fail inside the cloud phone. `publications.notes`
   carries the GeeLark error string; operators must monitor it.

## Out of scope

- Per-publication overrides for platform-specific params
  (`sameStyleUrl`, FB `page`, IG `aiTag`, etc.).
- A dedicated per-platform analytics view.
- Bulk re-publish UI (delete + re-upload is the workaround).
- Auto-detection of which apps are installed on a phone (operator must
  set `<platform>_logged_in` manually).
- Migrating the remaining web-tier synchronous publish paths
  (`POST /api/videos/publish`, `POST /api/accounts/publish`) to
  Procrastinate; they keep their existing OPERATION_LOCK behavior.
- Updating `CLAUDE.md` — the project root memory file is stale (still
  says SQLite + APScheduler) but is owned by the user; this PR will
  flag it but won't edit it.
