# Self-Engagement for Owned TikTok Videos Design

## Overview

Build a self-engagement workflow for owned TikTok content. The first version lets the operator select logged-in accounts, paste one or more owned TikTok video links, and schedule AI comment runs through GeeLark. The same data model also stores TikTok share links from successful publications so later versions can pick targets directly from published videos.

This is not a generic public-video comment tool. It is an account-network activity feature: accounts in a group interact with videos published by accounts in the same group, while avoiding self-commenting and preserving an audit trail for every phone task.

## Goals

- Run real TikTok AI comment tasks against owned video links.
- Support manual target links immediately.
- Add `publications.share_link` so successful publishing can save GeeLark's returned share link.
- Track every engagement attempt separately from video publication state.
- Keep all real phone work in the Procrastinate worker `phones` queue.
- Prevent an account from running multiple phone tasks at the same time.
- Show enough status in the operational UI to know which accounts commented, which failed, and where they failed.

## Non-Goals

- No fully automatic daily engagement scheduler in the first version.
- No engagement marketplace or public video discovery.
- No automatic comment text generation by this app in the first version; GeeLark AI comment generation is used with `useAi=1`.
- No broad anti-detection strategy beyond conservative task serialization, group scoping, and self-comment exclusion.
- No cross-platform engagement in the first version; only TikTok AI comments.

## GeeLark API Findings

The tested endpoint is:

```text
POST https://openapi.geelark.cn/open/v1/rpa/task/tiktokRandomComment
```

The verified payload shape:

```json
{
  "name": "codex-real-ai-random-comment-test",
  "remark": "Real single-run API test",
  "scheduleAt": 1780550917,
  "id": "620176013586261591",
  "useAi": 1,
  "links": ["https://www.tiktok.com/@davesyd_/video/7542823446447688978"],
  "commentProbability": 100
}
```

It returned a task ID, completed with GeeLark task status `3`, and the task detail logs showed the comment flow entering the comment box and posting. The final screenshot showed the comment count increased. The same existing query/detail/stop APIs work:

- `POST /open/v1/task/query` with `{"ids":[taskId]}`
- `POST /open/v1/task/detail` with `{"ID": taskId}`
- `POST /open/v1/phone/stop` with `{"ids":[envId]}`

## User Flow

The first UI entry point is the Accounts page selection toolbar.

1. Operator opens a specific group.
2. Operator selects accounts that should comment.
3. Operator clicks `AI Comment`.
4. Modal asks for one or more TikTok video links and optional comment probability.
5. Backend validates that selected accounts are in one group, have `geelark_phone_id`, and are logged into TikTok.
6. Backend creates one engagement batch plus one run per selected account and target link.
7. Worker submits GeeLark RPA tasks one at a time through the `phones` queue.
8. Worker polls each GeeLark task until terminal status.
9. UI can refresh status and show success, failure reason, task ID, cost, and failure screenshot.

Later, the same modal can include a target picker populated from successful TikTok publications with `share_link`.

## Data Model

### `publications` Additions

Add nullable columns:

- `share_link`: returned TikTok URL for the published video.
- `share_link_captured_at`: timestamp when the share link was stored.

When `posting_service.check_publication_one()` sees a successful GeeLark publishing task, it should read `shareLink` from `/open/v1/task/query` or `/open/v1/task/detail` and save it if present.

### New `engagement_tasks` Table

One row per operator-created engagement batch.

Columns:

- `id`: primary key.
- `group_id`: nullable foreign key to `groups.id`; required for the Accounts page flow.
- `action`: string, first value `tiktok_ai_comment`.
- `source`: string, first values `manual_links` and `publication_share_links`.
- `target_links`: JSON array of target TikTok links. This preserves the submitted batch input even if individual runs are later normalized.
- `target_publication_ids`: JSON array of publication IDs when the batch targets stored share links.
- `comment_probability`: integer `0..100`, default `100`.
- `status`: `PENDING`, `QUEUED`, `RUNNING`, `SUCCESS`, `PARTIAL_FAILURE`, `FAILURE`.
- `created_by`: nullable string for future audit use.
- `created_at`, `updated_at`, `finished_at`: timestamps.
- `summary`: nullable text with a concise final count.

### New `engagement_runs` Table

One row per account and target link.

Columns:

- `id`: primary key.
- `engagement_task_id`: foreign key to `engagement_tasks.id`.
- `account_id`: foreign key to `accounts.id`.
- `target_publication_id`: nullable foreign key to `publications.id`.
- `target_account_id`: nullable foreign key to `accounts.id`; used when the target publication has an author account.
- `target_link`: TikTok URL used in the GeeLark task.
- `action`: string, first value `tiktok_ai_comment`.
- `result`: `PENDING`, `QUEUED`, `INITIATED`, `SUCCESS`, `FAILURE`, `SKIPPED`.
- `geelark_task_id`: nullable string.
- `schedule_at`: integer epoch sent to GeeLark.
- `notes`: nullable text.
- `fail_code`: nullable integer.
- `fail_screenshot`: nullable string.
- `cost`: nullable integer from GeeLark query/detail.
- `started_at`, `finished_at`, `last_checked_at`, `created_at`: timestamps.

The important invariant is that `engagement_runs` never mutates publication publishing status. Publication state remains about publishing; engagement state is separate.

## Assignment And Safety Rules

Eligibility for a commenting account:

- Account is in the selected group.
- `geelark_phone_id` is non-empty.
- `tiktok_logged_in == 1`.
- Account is not already running a publication task with result `QUEUED` or `INITIATED`.
- Account is not already running an engagement run with result `QUEUED` or `INITIATED`.
- If the target is a stored publication with an `account_id`, the commenting account must not be that same account.

For manual links where target author is unknown, the first version cannot reliably detect self-commenting. The UI should label manual links as operator-responsibility. Stored publication targets will enforce self-comment exclusion.

The worker should run engagement tasks with `lock="phones"` on queue `phones`, matching publication submission. This keeps phone activity serialized across publishing and engagement. Publication assignment should also check engagement in-flight rows before assigning a phone, so auto-publish does not race a queued comment run.

## Backend API

Add a new router at `/api/engagement`.

### `POST /api/engagement/comment`

Creates a batch and defers worker runs.

Request:

```json
{
  "group_id": 1,
  "account_ids": [8, 9, 10],
  "target_links": [
    "https://www.tiktok.com/@example/video/123"
  ],
  "target_publication_ids": [],
  "comment_probability": 100
}
```

Validation:

- `account_ids` must be non-empty integers.
- All accounts must exist and share `group_id`.
- At least one target link or target publication ID must be provided.
- Links must be `https://www.tiktok.com/.../video/...` or `https://vt.tiktok.com/...`.
- `comment_probability` must be integer `0..100`.
- Only TikTok logged-in accounts are eligible.

Response:

```json
{
  "task_id": 12,
  "created_runs": 3,
  "skipped_runs": 0
}
```

### `GET /api/engagement/tasks`

Lists recent engagement batches, filterable by group.

Query params:

- `group_id`: optional integer.
- `limit`: default `50`, max `200`.

### `GET /api/engagement/tasks/{task_id}`

Returns batch metadata and run rows.

### `GET /api/engagement/runs/{run_id}/screenshot`

Proxies or redirects the last GeeLark screenshot for a failed run, matching the existing publication screenshot pattern.

## GeeLark Client Changes

Add methods to `geelark_client.py`:

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
    ...
```

The method should call `/open/v1/rpa/task/tiktokRandomComment` with:

- `id`: GeeLark phone ID.
- `useAi`: `1`.
- `links`: target links.
- `commentProbability`: validated probability.
- `scheduleAt`: current epoch if not provided.

For terminal cleanup, the engagement service should call the existing `stop_phones()` method in a best-effort `try` block after a run reaches `SUCCESS`, `FAILURE`, or `SKIPPED`.

## Worker Flow

Add `app/tasks/engagement.py` to `procrastinate_app.import_paths`.

Tasks:

- `submit_engagement_run_task(run_id: int)`, queue `phones`, lock `phones`
- `check_engagement_run_task(run_id: int, attempts_remaining: int)`, queue `default`

Submit task:

1. Load run, account, and parent task.
2. Validate run is still `QUEUED` or `PENDING`.
3. Check account has no in-flight publication or engagement run other than this one.
4. Submit GeeLark AI comment task.
5. Mark run `INITIATED`, store `geelark_task_id`, `started_at`.
6. Defer status check after 30 seconds.

Check task:

1. Query GeeLark task.
2. If status is `1` or `2`, defer another check until attempts are exhausted.
3. If status is `3`, mark `SUCCESS`, save `cost` if present.
4. If status is `4` or `7`, fetch detail, save reason, fail code, screenshot, and mark `FAILURE`.
5. Recompute parent batch status.
6. Best-effort stop the run's phone when the run reaches terminal status.

Use the same retry window as publications unless testing shows random comment tasks regularly exceed it: 40 checks at 30 seconds each.

## Service And Repository Boundaries

Create:

- `app/repositories/engagement.py`: CRUD and query helpers for engagement tables.
- `app/services/engagement_service.py`: validation, run planning, GeeLark submit, status reconciliation.
- `app/tasks/engagement.py`: Procrastinate task wrappers only.
- `app/api/engagement.py`: FastAPI request validation and response shaping.

Keep `posting_service.py` focused on publishing. The only posting-service change should be storing `share_link` on successful publication tasks.

## Frontend

Add API client methods:

- `createEngagementComment(...)`
- `engagementTasks(...)`
- `engagementTask(id)`

Accounts page changes:

- Add `AI Comment` button to the selected-account toolbar in group mode.
- Modal fields:
  - target links textarea.
  - comment probability numeric input, default `100`.
  - summary of eligible selected accounts.
- Submit shows toast with created and skipped counts.

Status display:

- First version can show recent engagement batches in a compact section below the group counts or inside the group header area.
- Each batch row shows status, created time, target count, run counts, and a link to details.
- Run detail should show account name, result, notes, task ID, cost, and screenshot link when available.

Do not put this in Settings. Settings is for configuration, not operational activity.

## Error Handling

Creation-time validation errors return HTTP 400 with actionable messages:

- `account_ids must be non-empty`
- `all accounts must be in the selected group`
- `target link must be a TikTok video URL`
- `comment_probability must be between 0 and 100`
- `no eligible TikTok accounts selected`

Runtime failures are saved on `engagement_runs.notes` and do not fail the whole batch unless every run fails or is skipped.

Examples:

- GeeLark API error during submit: run `FAILURE`, notes `submit failed: ...`
- Task not found during polling: run `FAILURE`, notes `task not found`
- GeeLark status `4`: run `FAILURE`, notes from task detail `failDesc`
- GeeLark status `7`: run `FAILURE`, notes `task cancelled`

## Testing

Backend unit tests:

- GeeLark client builds the exact payload for AI comments.
- Engagement creation rejects invalid links and invalid probabilities.
- Engagement creation skips accounts not logged into TikTok.
- Engagement creation excludes target publication owner.
- In-flight publication blocks engagement submission for the same account.
- In-flight engagement blocks publication assignment for the same account if the publication code is updated to check both sources.
- Successful GeeLark status marks run `SUCCESS` and parent batch `SUCCESS`.
- Failed GeeLark status saves fail code, notes, and screenshot.
- Successful publication stores `share_link` when GeeLark returns one.

Frontend tests or focused manual QA:

- Accounts selection toolbar opens AI Comment modal in group mode.
- Modal submits selected account IDs, target links, and probability.
- Invalid links show backend error toast.
- Recent task status renders success and failure states.

End-to-end manual verification:

1. Pick a small group with two TikTok logged-in accounts.
2. Paste one owned TikTok video link.
3. Run one AI comment from one non-owner account.
4. Confirm GeeLark task reaches `SUCCESS`.
5. Confirm TikTok comment count increases.
6. Confirm M1 or the selected phone is stopped after the task.

## Rollout

1. Add schema and backend tests.
2. Add GeeLark client method and engagement service.
3. Add worker tasks and include them in Procrastinate import paths.
4. Add API router.
5. Add Accounts UI modal and status display.
6. Run full test suite and frontend build.
7. Deploy through `docs/production-deploy.md`, running Alembic migrations on production.

The feature is operationally sensitive because running phones cost money. The implementation must keep the worker-only execution model and stop phones on terminal paths.
