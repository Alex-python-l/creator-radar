# Design: TikTok IELTS Video Discovery for Comment Targets

**Date**: 2026-06-09
**Status**: Approved for plan-writing

## Problem

The CommentModal in the dashboard currently has two ways to pick comment
targets: **Owned** (videos this stack has already published) and **Manual**
(user pastes TikTok URLs into a textarea). The Manual flow is what gets used
for outbound engagement on IELTS-related content, and it requires the
operator to leave the dashboard, browse TikTok, copy URLs, and paste them
back in one by one.

We want the dashboard to discover popular, recent IELTS-related TikTok
videos itself, store the candidates, and offer them as a checkbox list in
the same modal — so the user picks instead of pastes.

## Out of scope

- Downloading or re-posting the discovered videos (not a content-reuse
  feature — these are comment targets only).
- A standalone Discovery page or CSV export — discovery lives entirely
  inside CommentModal.
- A live "refresh now" button in the modal — the modal only reads the
  database; the scheduled background job is the only writer.
- Multi-platform discovery (YouTube, Instagram, Facebook). TikTok only.
- Per-account hiding logic — once *any* account has comment-attempted a
  video (other than failed attempts), it disappears from the Discover list
  for *everyone*.

## Data source decision

**Bright Data "Tiktok posts by keyword" dataset** (`gd_lilwhto81z415d9mdl`)
via the synchronous-trigger / async-poll API.

### Why not yt-dlp

We tried yt-dlp 2026.03.17 both locally (direct connect) and on production
(through `GEMINI_PROXY`):

- Hashtag pages: `No working app info is available` (yt-dlp's bundled TikTok
  mobile-app credentials have been invalidated by TikTok's anti-bot
  upgrades; status is unpredictable).
- Single video pages: `Your IP address is blocked from accessing this post`
  — even from the residential proxy.

These are independent failures: fixing yt-dlp's `app_info` would still leave
the IP-block issue, which residential proxies do not currently bypass.

### Why Bright Data

Verified on 2026-06-09 with `search_keyword=ielts`:

- Returned **346 records** for one trigger, **201 unique `post_url`s** after
  dedup.
- **100%** completeness on `post_url`, `post_id`, `playCount`, `diggCount`,
  `commentCount`, `create_time`, `profile_nickname`, `profile_url`,
  `profile_followers`.
- **97%** on `description`.
- Trigger → ready: ~60–180 seconds.
- Cost: $1.5 / 1k records. With one keyword × 1 trigger/day, monthly cost is
  ~$13.5.

## Architecture

```
┌────────────────────── mkt-worker (procrastinate) ──────────────────────┐
│                                                                        │
│   discover_tiktok_task        cron: 0 4 * * *  (hardcoded, daily 04 UTC)│
│   ──────────────────────                                               │
│   for keyword in settings.tiktok_discover_keywords:                    │
│       snapshot_id = brightdata_client.trigger_keyword_search(keyword)  │
│       records    = brightdata_client.poll_until_ready(snapshot_id)     │
│       drafts     = filter_and_normalize(records, keyword, settings)    │
│       upsert(drafts)                                                   │
│   prune_stale(retain_days)                                             │
│   job_runs.log(stats)                                                  │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ writes
                  ┌──────────────▼──────────────┐
                  │  PostgreSQL                 │
                  │  discovered_videos          │
                  │  engagement_runs (existing) │
                  └──────────────▲──────────────┘
                                 │ reads (anti-join: hide used)
┌────────────────────────────────┴──────────────────────────────────────┐
│ web tier (FastAPI)                                                    │
│   GET /api/engagement/discover-targets  →  candidates, sorted desc    │
│                                                                       │
│ Frontend (CommentModal.tsx)                                           │
│   Segmented: Owned | Discover (NEW) | Manual                          │
│   Discover panel: checklist; selected URLs reused as target_links     │
└───────────────────────────────────────────────────────────────────────┘
```

### Key decisions

- All Bright Data interaction happens in **mkt-worker**, never in the web
  tier — the poll loop runs for minutes, and we already have the
  Procrastinate event loop guidance (see CLAUDE.md memory:
  procrastinate-asyncio-trap) for blocking HTTP.
- The endpoint lives in `app/api/engagement.py` (not a new module) because
  it is the discovery feature's only consumer.
- On submit, the modal sends selected Discover URLs as `target_links` —
  **the same field manual mode uses** — so the existing engagement
  pipeline needs zero changes.

## Data model

### New table — `discovered_videos`

Added to `app/models.py`:

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
    video_id: str = Field(nullable=False)               # from post_id
    author_unique_id: Optional[str] = None              # parsed from profile_url
    author_nickname: Optional[str] = None
    author_followers: Optional[int] = None              # profile_followers
    title: Optional[str] = Field(default=None, sa_column=Column(Text))  # description
    play_count: Optional[int] = None
    like_count: Optional[int] = None                    # diggCount
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    collect_count: Optional[int] = None
    thumbnail_url: Optional[str] = Field(default=None, sa_column=Column(Text))
    create_time: Optional[datetime] = None              # video posted at
    search_keyword: str = Field(nullable=False)         # which keyword surfaced it first
    country: Optional[str] = None                       # BD's reported country
    discovered_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    last_seen_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
```

### Upsert rule

PostgreSQL `INSERT ... ON CONFLICT (url) DO UPDATE` on `url`:

- **Overwrite** every cycle: `play_count`, `like_count`, `comment_count`,
  `share_count`, `collect_count`, `author_followers`, `last_seen_at`.
- **Preserve** first-seen values: `search_keyword`, `discovered_at`.
- `title`, `thumbnail_url`, `author_nickname` are overwritten **only if**
  the incoming value is non-null (avoid clobbering a previously-populated
  field if BD returns null this cycle).

### Anti-join for the modal endpoint

```sql
SELECT dv.*
FROM discovered_videos dv
WHERE NOT EXISTS (
  SELECT 1 FROM engagement_runs er
  WHERE er.target_link = dv.url
    AND er.result IN ('PENDING', 'QUEUED', 'INITIATED', 'SUCCESS')
)
ORDER BY dv.play_count DESC NULLS LAST
LIMIT 100;
```

Failed runs do NOT hide the video — the user can retry.

### Settings (added to `app/config.py:SETTINGS_DEFAULTS`)

| Key | Default | Notes |
|---|---|---|
| `tiktok_discover_keywords` | `ielts` | Comma-separated. Default is one keyword — one keyword surfaces hundreds of unique videos per day and keeps cost ~$13/mo. |
| `tiktok_discover_min_views` | `10000` | `playCount` below this is dropped. |
| `tiktok_discover_window_days` | `30` | `create_time` older than this is dropped. |
| `tiktok_discover_retain_days` | `60` | `last_seen_at` older than this is pruned (but only if not referenced by an `engagement_run`). |
| `tiktok_discover_max_per_keyword` | `500` | Hard cap on records consumed per keyword per cycle (defense against runaway BD billing). |
| `brightdata_token` | empty | Bearer token. Required; cycle fails fast if unset. |
| `brightdata_keyword_dataset_id` | `gd_lilwhto81z415d9mdl` | Override only if BD changes the dataset id. |

### Alembic migration

New file `alembic/versions/<id>_discovered_videos.py`:

- `op.create_table("discovered_videos", ...)`
- The three indexes from `__table_args__`.
- No downgrade-data-destruction concerns: table starts empty.

## Backend

### `brightdata_client.py` (new root-level module)

Parallel to the existing `geelark_client.py` style. ~80 lines.

```python
class BrightDataClient:
    BASE = "https://api.brightdata.com/datasets/v3"

    def __init__(self, token: str, *, dataset_id: str, timeout: int = 30):
        self.token = token
        self.dataset_id = dataset_id
        self.timeout = timeout

    def trigger_keyword_search(self, keyword: str, *, country: str = "") -> str:
        """POST /trigger; returns snapshot_id. Raises on 4xx/5xx."""

    def get_progress(self, snapshot_id: str) -> str:
        """GET /progress/<id>; returns status ('running' | 'ready' | 'failed')."""

    def fetch_snapshot(self, snapshot_id: str) -> list[dict]:
        """GET /snapshot/<id>?format=json; returns parsed JSON array."""

    def poll_until_ready(
        self, snapshot_id: str, *,
        interval_seconds: int = 20,
        max_wait_seconds: int = 600,
    ) -> list[dict]:
        """Polls get_progress every `interval_seconds`; when ready, returns
        fetch_snapshot. Raises BrightDataTimeout after max_wait."""
```

### `app/services/discovery_service.py` (new, ~140 lines)

```python
def _parse_settings(session: Session) -> DiscoverConfig:
    """Read all tiktok_discover_* settings; split keywords by comma+strip."""

def _filter_and_normalize(
    records: list[dict],
    keyword: str,
    *,
    min_views: int,
    window_days: int,
    max_per_keyword: int,
) -> list[DiscoveredVideoDraft]:
    """Drop empty post_urls, dedup on post_id within batch,
       require play_count >= min_views,
       require create_time within window_days,
       truncate to max_per_keyword."""

def _upsert_videos(session: Session, drafts: list[DiscoveredVideoDraft]) -> tuple[int, int]:
    """Returns (inserted, updated). Uses PG dialect insert(...).on_conflict_do_update."""

def _prune_stale(session: Session, retain_days: int) -> int:
    """DELETE FROM discovered_videos WHERE last_seen_at < now() - retain_days
       AND NOT EXISTS (SELECT 1 FROM engagement_runs WHERE target_link = url);
       returns rows deleted."""

def run_discovery_cycle(session: Session, client: BrightDataClient) -> DiscoveryStats:
    """Orchestrate: parse settings → for each keyword: trigger → poll → filter
       → upsert. Per-keyword failures are caught & logged, not fatal.
       Then prune_stale. Returns aggregate stats."""
```

### `app/repositories/discovered_videos.py` (new, ~35 lines)

```python
def list_unused_targets(session: Session, *, limit: int = 100) -> list[DiscoveredVideo]:
    """The anti-join SELECT from §Data model, parameterized by limit."""
```

### `app/tasks/discover_tiktok.py` (new, ~30 lines)

```python
@procrastinate_app.periodic(cron="0 4 * * *")  # daily 04:00 UTC; hardcoded like auto_publish_scan
@procrastinate_app.task(name="discover_tiktok", queue="default")
async def discover_tiktok_task(timestamp: int):
    def _work():
        with Session(engine) as session:
            client = BrightDataClient(
                token=os.environ["BRIGHTDATA_TOKEN"],
                dataset_id=os.environ.get(
                    "BRIGHTDATA_KEYWORD_DATASET_ID",
                    "gd_lilwhto81z415d9mdl",
                ),
            )
            stats = discovery_service.run_discovery_cycle(session, client)
            job_runs_repo.log(session, name="discover_tiktok", summary=str(stats))
            session.commit()
    await asyncio.to_thread(_work)
```

The task is not in the `phones` queue — discovery never touches GeeLark.

### API endpoint — extend `app/api/engagement.py`

```python
@router.get("/discover-targets")
def list_discover_targets(
    limit: int = Query(100, le=200),
    session: Session = Depends(get_session),
) -> dict:
    items = discovered_videos_repo.list_unused_targets(session, limit=limit)
    return {"items": [_serialize_discover(v) for v in items]}

def _serialize_discover(v: DiscoveredVideo) -> dict:
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

Auth follows the rest of `engagement.py` — no new auth surface.

## Frontend

### `frontend/src/api.ts`

Add type + call:

```ts
export type DiscoverTarget = {
  url: string
  title: string | null
  author_nickname: string | null
  play_count: number | null
  like_count: number | null
  create_time: string | null      // ISO
  search_keyword: string
}

export const api = {
  // ...existing
  discoverTargets: () =>
    request<{ items: DiscoverTarget[] }>('/api/engagement/discover-targets'),
}
```

### `frontend/src/components/CommentModal.tsx` — diff

1. `Mode` type adds `'discover'`. Segmented gets 3 options instead of 2.
2. New state: `discoverTargets: DiscoverTarget[]`, `selectedDiscover: Set<string>` (keyed by `url`).
3. `useEffect(open)` parallel-fetches all three sources, including `api.discoverTargets()`.
4. `targetCount` branches for `discover`: `selectedDiscover.size`.
5. `submit()` builds `target_links` from `[...selectedDiscover]` when in discover mode; `target_publication_ids` stays `[]`.
6. `TargetsField` renders a new `<DiscoverList>` panel for discover mode.
7. Empty state when `discoverTargets.length === 0` says "No candidates yet — the daily discovery job (04:00 UTC) will populate this", no refresh button.

### New `DiscoverList` component — inline in CommentModal.tsx

Visual style **identical** to `PhonesField` rows: `grid-cols-[24px_minmax(0,1fr)_auto]`, 24px checkbox, 13px text, hover row color, dividers. No new design tokens.

Each row shows:
- Title (or `(no description)`)
- Sub-line: `@author · 1.2M views · 3 days ago · #ielts`

Footer shows `Showing N · M selected`.

### i18n

Add to `frontend/src/i18n.ts` (both `en` and `zh` namespaces):
- `comments.discoverMode` → "Discover" / "发现"
- `comments.discoverEmptyTitle`
- `comments.discoverEmptyBody`
- `comments.viewsCount` (e.g. `{{n}} views`)
- `comments.daysAgo`

## Error handling

| Failure | Handling | User effect |
|---|---|---|
| `BRIGHTDATA_TOKEN` unset | Service raises `RuntimeError` on cycle start | JobRun row marked failure; modal endpoint still works (reads existing data) |
| BD `/trigger` 4xx | `BrightDataClient` raises `BrightDataError`; outer per-keyword try/except catches | Other keywords still run; JobRun summary lists failed keyword |
| BD `/progress` returns `failed` | Treated same as polling error | Same |
| `/progress` still `running` after 10 min | `BrightDataTimeout`; same handling | Same |
| BD returns empty array | Logged as warning; cycle continues | No new rows; existing `last_seen_at` not touched |
| Record missing `post_url` | Skipped (filter step) | Doesn't enter DB |
| Record's `playCount` < min_views | Skipped | Doesn't enter DB |
| Record's `create_time` outside window | Skipped | Doesn't enter DB |
| Same `post_url` returned in 2 keywords in same cycle | Second upsert just updates last_seen_at; `search_keyword` keeps first one | One DB row |
| Endpoint DB error | FastAPI 500; frontend Toast | Discover tab shows load-failed empty state |
| `_prune_stale` tries to delete a referenced video | SQL `NOT EXISTS` clause protects | Referenced videos never disappear |
| Race: two operators select the same URL simultaneously | Existing engagement pipeline accepts both; GeeLark posts both as separate comments | Acceptable (rare) |

### Not handled (YAGNI)

- BD `gd_lilwhto81z415d9mdl` dataset id changes → override via setting `brightdata_keyword_dataset_id`, no auto-discovery of new id.
- BD raises pricing → fixed by changing `tiktok_discover_max_per_keyword` cap.
- Multi-language IELTS content (Arabic / Chinese / etc) → BD already returns these by `search_keyword=ielts`; we keep them.

## Testing

### Unit tests (pytest, new files)

| File | Coverage |
|---|---|
| `tests/test_brightdata_client.py` | `trigger_keyword_search` builds correct URL + body; `get_progress` parses status; `poll_until_ready` returns on ready, raises on timeout (use `time.monotonic` patching, not sleep). All HTTP mocked. |
| `tests/test_discovery_service.py` | `_filter_and_normalize`: drops below-min-views, outside-window, missing post_url, dedups within batch, respects max_per_keyword cap. `_parse_settings`: comma-split keywords with whitespace trim, default fallback. `_upsert_videos`: insert on first call, update counts on second call, preserves `search_keyword` + `discovered_at`. `_prune_stale`: deletes stale only when not referenced. `run_discovery_cycle`: per-keyword failure isolation. |
| `tests/test_repositories_discovered_videos.py` | `list_unused_targets`: hides SUCCESS-referenced rows, hides INITIATED/QUEUED/PENDING, **shows** FAILURE-referenced rows, orders by play_count desc. |
| `tests/test_api_engagement.py` (extend) | `GET /api/engagement/discover-targets` returns serialized items with the field set in §Backend. |
| `tests/test_tasks_discover_tiktok.py` | Monkeypatched `discovery_service.run_discovery_cycle` is called; JobRun row written; task is registered on `default` queue not `phones`. |

### Test fixture

`tests/fixtures/brightdata_keyword_ielts_sample.json` — committed copy of
the 346-record snapshot pulled on 2026-06-09. The keyword service and
client tests use this rather than mocking field-by-field.

### Manual acceptance

1. Set `BRIGHTDATA_TOKEN` in local `.env`.
2. Run `uv run python -c "from app.services import discovery_service as d; from brightdata_client import BrightDataClient; from app.db import engine; from sqlmodel import Session; import os; c = BrightDataClient(os.environ['BRIGHTDATA_TOKEN'], dataset_id='gd_lilwhto81z415d9mdl'); print(d.run_discovery_cycle(Session(engine), c))"`.
3. Check `SELECT count(*), max(play_count) FROM discovered_videos;` ≥ 50 rows.
4. Open dashboard → Comments → New comment → Discover tab → list populated.
5. Select 3 rows + 1 phone → submit → 3 EngagementRuns created.
6. Reopen modal → those 3 rows gone from Discover.

### Test baseline

Existing 385 tests pass; ~15 new tests added; target ≥ 400 passing.

## Deployment

Standard `docs/production-deploy.md` flow plus:

1. Set `BRIGHTDATA_TOKEN` in `/opt/mkt/.env` on the server before tarball
   restart (key value provided by user — do not commit it).
2. `alembic upgrade head` to create `discovered_videos`.
3. Restart `mkt-worker.service` so the new periodic task registers (the
   runbook restarts `mkt`; do NOT skip the worker restart — see CLAUDE.md
   memory: deploy-restart-worker).
4. First cycle fires at next 04:00 UTC. To smoke-test immediately, defer
   one job from the worker shell.

## Open questions

None. All scoping decisions are recorded above.

## References

- `app/api/engagement.py` — endpoint extension point.
- `app/services/engagement_service.py` — pipeline consuming `target_links` (unchanged).
- `frontend/src/components/CommentModal.tsx` — UI extension point.
- `app/tasks/auto_publish.py` — reference for a periodic procrastinate task in `default` queue.
- `geelark_client.py` — reference for a thin HTTP client module shape (`brightdata_client.py` mirrors it).
- Bright Data dataset: `gd_lilwhto81z415d9mdl` ("Tiktok posts by keyword").
- Verification snapshot from 2026-06-09: 346 records, 201 unique, full field coverage.
