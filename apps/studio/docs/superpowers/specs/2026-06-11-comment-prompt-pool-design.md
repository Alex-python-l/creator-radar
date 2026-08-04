# Comment Prompt Pool — Design

Date: 2026-06-11
Status: Draft

## Goal

Replace the single freeform "AI comment brief" textarea in the comment-batch
modal with a reusable prompt pool. Marketers should be able to:

1. Save commonly used briefs (e.g. "lead with the Rewriter, not CEFR
   highlighter — viral video voice") once.
2. Pick the prompt to use when starting a comment batch — per batch, not
   per group.
3. Add / edit / delete prompts from a central place in Settings.

This mirrors the caption prompt pool shipped in 9bb78cb, with one key
difference: comment prompts are selected at batch time (CommentModal),
not bound to a group, because comment runs are operator-initiated and a
single batch may target accounts from multiple groups.

## Non-goals

- Per-group default prompts. The selection is per batch.
- Migrating the existing `engagement_tasks.comment_brief` column out of
  the schema. It stays put as a fallback path for rows persisted before
  this change.
- Versioning or change history for prompts. Edits overwrite in place.
- Inline "create new prompt" from inside the comment modal. Management
  lives in Settings.

## Data model

### New table — `comment_prompts`

| Column      | Type      | Constraint                           |
|-------------|-----------|--------------------------------------|
| id          | int       | PK                                   |
| name        | str(120)  | UNIQUE, INDEX, NOT NULL              |
| body        | TEXT      | NOT NULL                             |
| created_at  | timestamp | server_default CURRENT_TIMESTAMP     |
| updated_at  | timestamp | server_default CURRENT_TIMESTAMP     |

Shape and constraints copied verbatim from `caption_prompts`.

### `engagement_tasks` — add `comment_prompt_id`

- `comment_prompt_id: Optional[int]` — FK to `comment_prompts.id`,
  `ON DELETE SET NULL`. Deletion is gated by the service layer (it
  refuses while any inflight engagement_runs reference the prompt);
  rows in TERMINAL states should not block GC of an unused prompt,
  so the DB-level FK clears the back-reference instead of blocking.
- `comment_brief: Optional[str]` stays — read-only fallback for any row
  written before this change. New rows write `comment_prompt_id` and
  leave `comment_brief = NULL`.

### Alembic migration

- Create `comment_prompts`.
- Add `engagement_tasks.comment_prompt_id` FK column (nullable).
- No data backfill. Existing rows keep `comment_brief` populated and
  continue to work through the existing `_combined_brief()` path.

## Backend

### Repository — `app/repositories/comment_prompts.py`

Same surface as `caption_prompts`: `list_all`, `get`, `get_by_name`,
`create`, `update`, `delete`.

### Service — `app/services/comment_prompts_service.py`

- `list_all`, `get`, `create`, `update` — delegate to repo with
  `name` ≤ 120, both `name` and `body` non-empty checks. Duplicate name →
  409 (via `PromptNameConflict`).
- `delete(session, prompt_id)` — refuses if any
  `engagement_tasks` row referencing the prompt has `status` in
  `{PENDING, QUEUED, INITIATED}`. Raises `PromptInUse(prompt_id, run_ids)`.
  Completed runs do not block deletion.

### Engagement service changes

- `create_comment_batch(...)` parameter swap:
  - **remove** `comment_brief: str | None`
  - **add** `comment_prompt_id: int | None`
- When `comment_mode == "studio_ai"`, `comment_prompt_id` is required —
  raises `ValueError("comment_prompt_id is required when comment_mode is
  studio_ai")` if absent. Service also validates that the prompt exists
  (404 → translated to 400 at the API layer).
- The repository call `eng_repo.create_task(...)` now writes
  `comment_prompt_id` instead of `comment_brief`.

### `_prepare_studio_comment` — prompt resolution

Replace the current `_combined_brief(parent)` call site with:

```
prompt_id = parent.get("comment_prompt_id")
if prompt_id is not None:
    prompt = cp_repo.get(session, prompt_id)
    if prompt is None:
        raise RuntimeError(f"comment_prompts row {prompt_id} vanished")
    brief = prompt["body"]
else:
    # Legacy row written before the pool — fall back to the merged
    # product_info + comment_brief path.
    brief = _combined_brief(parent)
```

This preserves backward compatibility for queued/inflight tasks that
predate the pool.

### REST API — `app/api/comment_prompts.py`

| Verb   | Path                          | Body                          | Response |
|--------|-------------------------------|-------------------------------|----------|
| GET    | `/api/comment-prompts`        | —                             | `[{id, name, body, created_at, updated_at}]` sorted by name |
| POST   | `/api/comment-prompts`        | `{name, body}`                | `{id}` or 400 / 409 |
| PATCH  | `/api/comment-prompts/{id}`   | `{name?, body?}`              | 200 or 400 / 404 / 409 |
| DELETE | `/api/comment-prompts/{id}`   | —                             | 200 or 404 / 409 `{error, runs: [run_id, ...]}` |

### API — engagement.py change

`POST /api/engagement/comment` body schema:

- **remove** `comment_brief`
- **add** `comment_prompt_id: int | null`

Existing fields (`account_ids`, `target_links`, `comment_mode`, etc.)
unchanged.

## Frontend

### Settings page — new "Comment prompts" section

Mirror the existing `CaptionPromptsSection`:

- List of prompts with name + body preview (200 chars, monospace).
- Per-row Edit / Delete buttons.
- "New comment prompt" button → modal with name + body textarea.
- Modal Save validates non-empty name/body. On 409 toast the bound
  `runs` array if delete was rejected.

Renders below the Caption prompts section, same visual treatment.

### CommentModal — Configure step

Replace the existing single textarea with:

- `<select>` listing all `comment_prompts` rows (sorted by name).
- Empty state: when the list is empty the select is disabled and shows
  `No prompts yet — create one in Settings`. Submit is blocked.
- Right of the select, a "Manage prompts" link that navigates to the
  Settings page (`/settings#comment-prompts`).
- Below the select, a 6-line read-only preview of the selected prompt's
  body so the operator can see what they're about to use.

`canSubmit` adds `comment_prompt_id != null` when
`commentMode === 'generate'`.

### API client / types

- New `CommentPrompt` type, shape identical to `CaptionPrompt`.
- New methods: `commentPrompts()`, `createCommentPrompt()`,
  `updateCommentPrompt()`, `deleteCommentPrompt()`.
- `createEngagementComment` body type:
  - **remove** `comment_brief?`
  - **add** `comment_prompt_id?: number | null`

### EngagementTask type

Add `comment_prompt_id: number | null`. Keep `comment_brief: string | null`
for the legacy fallback path (rendered nowhere new — kept for backward
compatibility on the wire only).

### i18n

Follow the existing caption-prompt approach — strings hard-coded in
English and Chinese inside the components. No new i18n keys.

## Behaviour around empty pool

- `CommentModal` cannot start a studio_ai batch when the pool is empty.
  The select is disabled, an inline hint points to Settings, and the
  primary button is greyed out.
- `provided` mode (paste-your-own comments) is unaffected — no prompt
  needed there.

## Deletion semantics

- `comment_prompts_service.delete(...)` queries
  `SELECT id FROM engagement_runs r JOIN engagement_tasks t ON r.engagement_task_id = t.id WHERE t.comment_prompt_id = :pid AND r.result IN ('PENDING','QUEUED','INITIATED')`.
- If the query returns any row, raise `PromptInUse(pid, run_ids)`.
  API returns 409 `{error: "prompt in use", runs: [...]}`.
- Frontend surfaces this as a toast: `Can't delete: still used by N
  inflight comment runs.`

## Tests

- `tests/test_comment_prompts_service.py` — create / update / delete
  happy + name conflict + prompt-in-use rejection.
- `tests/test_comment_prompts_api.py` — endpoint surface.
- `tests/test_engagement_service.py`:
  - `create_comment_batch` requires `comment_prompt_id` for studio_ai.
  - `_prepare_studio_comment` resolves prompt → brief from
    `comment_prompts` table.
  - Legacy task with `comment_brief` set and `comment_prompt_id` NULL
    still uses `_combined_brief()`.

## Migration & rollout

- Alembic: one new revision, additive only. Safe to deploy with services
  up; the FK column defaults to NULL, no data backfill needed.
- Operator workflow on first deploy:
  1. Apply migration.
  2. Go to Settings → Comment prompts → New, seed at least one prompt.
  3. After that, CommentModal can start studio_ai batches again.
- No code path silently invents a prompt. An operator must explicitly
  seed the pool.

## Open questions / risks

- If a prompt is edited while a task is queued, the new body takes
  effect on next worker pickup. Treated as intentional — same behaviour
  as caption_prompts.
- The DB FK uses `ON DELETE SET NULL`; the service-level check is the
  primary gate. Terminal-only references do not block deletion — the
  audit trail lives in engagement_runs.comment_text + comment_source,
  not in the FK back to comment_prompts.
