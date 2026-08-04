# Design: Video selection control

- **Date:** 2026-05-22
- **Status:** Approved (design); implementation plan pending
- **Scope:** how operators choose which videos to import and which account each video posts to

## 1. Problem & context

Two gaps in the video workflow, both about operator control:

1. **Drive import is all-or-nothing.** `POST /api/videos/import` imports every
   video in a Drive folder. Operators want to pick specific videos (e.g. 2 of 30).
2. **Account assignment is fully automatic.** Videos enter as `PENDING` with
   `account_id` NULL; `publish` auto-pairs videos to accounts via `assignment.py`
   (warmed accounts, oldest-posted first, one per account per run, 2h cooldown).
   Operators cannot control which video goes to which account.

This design adds operator control to both.

## 2. Goals / non-goals

**Goals**
- Drive import: list a folder, let the operator tick which videos to import,
  import only those.
- Each `PENDING` video can be manually assigned to a chosen account.
- Publish posts each manually-assigned video to its account; videos left
  unassigned still auto-assign (existing behavior) as a fallback.
- The warmup + 2h-cooldown guards still apply by default; a "force publish"
  toggle bypasses them for manually-assigned videos.

**Non-goals**
- Changing the auto-assignment algorithm itself (`assignment.py` stays).
- One video → many accounts (broadcast) — not requested.
- Caption handling — unchanged.

## 3. Part A — Drive import: select which videos

### Flow
1. Operator pastes a Drive folder link, clicks "List".
2. `POST /api/videos/import/list` (synchronous) lists the folder's videos.
3. UI shows the list with a checkbox per video; already-imported ones disabled.
4. Operator ticks the wanted videos, clicks "Import selected".
5. `POST /api/videos/import` — body is now the selected `files` list — starts
   the background download of just those.

### Backend
- `drive_import.list_folder(folder_url) -> list[dict]` — synchronous; uses
  `gdown.download_folder(skip_download=True, proxy=...)`; returns
  `[{id, name, already_imported}]` (video extensions only; `already_imported`
  from `existing_drive_file_ids`).
- `POST /api/videos/import/list` — body `{folder_url}`; returns `{files: [...]}`.
  `503` if `DRIVE_IMPORT_PROXY` unset, `400` on a bad URL.
- `POST /api/videos/import` — body changes from `{folder_url}` to
  `{files: [{id, name}, ...]}`. Validates the list is non-empty (`400` if not).
- `drive_import.run_import(files)` — takes the explicit selected files;
  downloads each (still dedups against `drive_file_id`); no longer lists or
  downloads the whole folder.
- `start_import(files)` — adjusted signature.

### Frontend
- The "Import from Drive" modal becomes two-step: a URL field + "List" button →
  then a scrollable checkbox list of videos (already-imported rows disabled with
  a note) + "Import selected".
- The existing progress panel is unchanged.

## 4. Part B — Per-video account assignment + publish

### Assigning
- `PATCH /api/videos/{id}` — body `{account_id: int | null}`. Sets
  `videos.account_id`. New `vid_repo.set_account(conn, vid, account_id)`.
- The pending-queue table gains an **Account** column with a `<select>` per row:
  an "(auto)" option (= null) plus every account (label = `profile_name`, with a
  marker for not-yet-warmed accounts). Changing it PATCHes immediately.
- `videos.account_id` already exists (FK `accounts(id) ON DELETE SET NULL`) — no
  schema change; it is now also set pre-publish, manually.

### Publishing
`posting_service.publish` is restructured:
- Partition `PENDING` videos into **manual** (`account_id` set) and **auto**
  (`account_id` null).
- **Manual** videos: for each, load its account. Guard — the account must be
  `warmed_up` and must not have posted within `cooldown_hours` — unless `force`
  is set. Blocked → skip the video (stays `PENDING`), emit a log line. Allowed →
  post via the existing GeeLark upload + task flow, then `mark_initiated`.
- **Auto** videos: the existing `assignment.build_pool` / `assign` flow, but the
  pool excludes any account already posted to by a manual video this run.
- New `force: bool` parameter (default false): bypasses the warmed/cooldown
  guard for **manual** videos only (auto videos inherently use only eligible
  accounts).
- `POST /api/videos/publish` and `/publish-stream` accept `force`.

### Frontend
- A "Force publish" toggle next to the Publish button; its state is passed to
  the publish call.
- The publish live-log already shows per-video lines; skipped manual videos
  appear there with the reason.

## 5. Data flow

- **Import:** paste link → `import/list` (sync) → checkboxes → `import`
  (selected) → background download → `PENDING` videos (unassigned).
- **Assign:** pending queue → pick an account per row → `PATCH /api/videos/{id}`
  → `account_id` stored.
- **Publish:** "Publish" (± force) → manual videos posted to their accounts
  (guarded unless force), then auto-fallback for the rest → `INITIATED` → the
  existing `status_check` job reconciles outcomes.

## 6. Error handling

| Situation | Behavior |
|---|---|
| `import/list` — proxy unset / bad URL | `503` / `400` |
| `import` with an empty `files` list | `400` |
| A selected video already imported | skipped by the existing `drive_file_id` dedup |
| `PATCH /api/videos/{id}` — unknown video | `404` |
| `PATCH` — unknown `account_id` | `400` |
| Publish — manual video's account not warmed / in cooldown, no force | video skipped, stays `PENDING`, logged |
| Publish — manual video's account was deleted (FK nulls `account_id`) | falls back to auto-assignment |
| Force publish | warmed/cooldown guard skipped for manual videos only |

## 7. Testing

- `drive_import.list_folder` — mock gdown; returns videos + already-imported flags.
- `run_import(files)` with an explicit selection — downloads only those.
- `import/list` and the new `import` body — endpoint tests.
- `PATCH /api/videos/{id}` — sets and clears `account_id`; `404` / `400` cases.
- `posting_service.publish` — manual videos posted to their accounts; the guard
  skips un-warmed / in-cooldown manual videos; `force` bypasses it; unassigned
  videos still auto-assign; manual-used accounts excluded from the auto pool.
- Keep the existing test suite green.

## 8. Out of scope

- The auto-assignment algorithm itself.
- One video → many accounts.
- Caption generation.
