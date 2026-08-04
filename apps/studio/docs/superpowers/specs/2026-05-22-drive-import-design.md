# Design: Import videos from Google Drive

- **Date:** 2026-05-22
- **Status:** Approved (design); implementation plan pending
- **Scope:** extends the video ingestion path of the mkt app

## 1. Problem & context

The mkt app currently ingests videos only through manual file upload on the
dashboard (`POST /api/videos/upload`). Operators keep their source videos in
Google Drive and want to import them directly.

The mkt app is deployed on a Tencent Cloud VPS in China (`1.15.155.204`,
"server A"), which cannot reach Google services. A second, overseas server
(`107.174.1.82`, "server B", Debian 13) reaches Google Drive normally. This
design uses server B as a network relay so the China-hosted app can fetch
Drive content.

## 2. Goals / non-goals

**Goals**
- Operator pastes a public ("anyone with the link") Google Drive *folder* link
  into the dashboard and imports all videos in it.
- Imported videos become normal `PENDING` rows in the `videos` table —
  identical to manually uploaded videos, so the existing publish flow is
  unchanged.
- Re-importing a folder skips videos already imported.
- No Google account credentials of any kind.
- No `googleapiclient` / `gspread` dependency added to mkt (honors the
  convention in CLAUDE.md). `gdown` (a small standalone library) is acceptable.

**Non-goals**
- Fixed/configured folder, or scheduled/automatic sync (chosen trigger is
  manual, paste-link, one-shot).
- Caption generation for imported videos.
- Importing Drive content that is not publicly link-shared (private folders,
  OAuth, service accounts).
- Storing or caching videos on server B.

## 3. Architecture

**Server B acts as a plain forward proxy.** mkt itself fetches Drive content
with `gdown`, routing that traffic through B's proxy. Video bytes flow
Drive → B (proxy) → server A and are written straight to A's disk; **no video
file is ever stored on server B** (relevant — B has only ~3.3 GB free disk).

```
Operator pastes a Drive folder link on the Videos page
      |
      v
 mkt (server A, 1.15.155.204)
 gdown lists + downloads ──HTTPS via proxy──▶ proxy on server B ──▶ Google Drive
      |                                       (107.174.1.82)          |
      ◀──────────────── video byte stream ───────────────────────────┘
      v
 written to data/uploads/ + a videos row (PENDING) is created
```

- **Server A — `1.15.155.204`** — runs the mkt app (`/opt/mkt`, systemd
  service `mkt`, port 8080). Gains the import feature.
- **Server B — `107.174.1.82`** — overseas, reaches Google. Gains exactly one
  new thing: a dedicated forward proxy. Its existing services (xray, s-ui,
  GithubRepoMonitor, postgres, …) are untouched.

## 4. Component: import proxy on server B

- A lightweight HTTP forward proxy — `tinyproxy` (in the Debian repo, minimal
  memory footprint; suits B's 1 GB RAM and crowded service set).
- Runs as a systemd service, listening on `0.0.0.0:8118` (port `8118` is
  currently free on B).
- Locked down two ways:
  1. **Source-IP allowlist** — only `1.15.155.204` (server A) may connect
     (`Allow` directive).
  2. **Basic auth** — username + generated password (`BasicAuth` directive).
- An HTTP proxy handles HTTPS via `CONNECT`, which is all gdown needs — no
  SOCKS, so mkt needs no `pysocks` dependency.
- The panel-managed xray/s-ui config is **not** touched; hand edits there
  would be overwritten by the panels.
- Implementation must verify port `8118` is reachable from server A. B appears
  to have no restrictive host firewall; if a provider firewall exists, `8118`
  must be allowed.

## 5. Component: mkt backend

### 5.1 Dependency & config
- Add `gdown` to `requirements.txt`. It pulls small pure-Python deps
  (`requests`, `beautifulsoup4`, `filelock`, `tqdm`); no `googleapiclient`.
- New env var `DRIVE_IMPORT_PROXY`, read in `app/config.py`, e.g.
  `http://mkt:<password>@107.174.1.82:8118`. Stored in `/opt/mkt/.env`, never
  committed. If unset, the import endpoint returns a clear "not configured"
  error.

### 5.2 New service module — `app/services/drive_import.py`
- Parse a Google Drive folder URL → folder ID; reject non-folder / non-Drive
  URLs.
- **List** the folder's files via gdown's folder listing (no download),
  routed through the proxy.
- **Filter**: keep only video files (extensions `.mp4 .mov .m4v .webm .mkv
  .avi`); drop files whose Drive file ID already exists in
  `videos.drive_file_id`.
- **Download** each remaining file through the proxy into `config.UPLOAD_DIR`,
  with a collision-safe filename.
- For each completed file, insert a `videos` row (`result='PENDING'`,
  `original_filename`, `stored_path`, `drive_file_id`).
- Maintain progress state and write an audit `job_runs` row.

### 5.3 Background job model
- The import runs in a background daemon thread; the request returns
  immediately. An import can take 10+ minutes, so this survives the operator
  closing the browser tab.
- Only one import at a time: a second `POST /api/videos/import` while one is
  running returns `409`.
- The import does **not** take `OPERATION_LOCK`. That lock serializes GeeLark
  phone operations (publish / warmup / set-pics), which cost money. Importing
  only downloads files and inserts DB rows; it is safe to run concurrently
  (SQLite WAL). A dedicated lightweight guard (a single in-process import lock
  / running-state flag) prevents overlapping imports.
- Progress is held in an in-process state object — valid because the app is
  single-process (`--workers 1`) — and mirrored to a `job_runs` row
  (`job_type='drive_import'`) for the audit log.

### 5.4 API (added to `app/api/videos.py`, behind the existing `require_auth`)
- `POST /api/videos/import` — body `{"folder_url": "<drive folder link>"}`.
  Validates the URL and proxy config, starts the background import, returns
  `{"started": true}`. Returns `409` if an import is already running, `400`
  on a bad URL, `503` if `DRIVE_IMPORT_PROXY` is unset.
- `GET /api/videos/import/status` — returns current/last import progress:
  `{state, total, done, skipped, failed, current_filename, errors[],
  started_at, finished_at}`. The frontend polls this.

## 6. Component: mkt frontend

- On the Videos page (`frontend/src/pages/Videos.tsx`): an **"Import from
  Drive"** button opens a small form with one text input (the folder link)
  and a submit button.
- On submit → `POST /api/videos/import`, then poll `GET
  /api/videos/import/status` every ~2 s.
- Show progress: `done / total`, current filename, and a final summary
  (imported / skipped / failed with reasons). Refresh the video list when the
  import finishes.
- All user-facing strings in English (repo convention).

## 7. Data model change

`videos` gains one nullable column:

```sql
ALTER TABLE videos ADD COLUMN drive_file_id TEXT;
```

- Holds the Google Drive file ID for imported videos; `NULL` for manually
  uploaded videos.
- Used to skip already-imported files on re-import.
- The app has no migration framework. `app/db.py::init_schema` runs this
  `ALTER TABLE` idempotently — guarded by a `PRAGMA table_info(videos)` check
  (add the column only if absent) — so it is safe on every startup.

## 8. Data flow

1. Operator pastes a Drive folder link on the Videos page and clicks Import.
2. `POST /api/videos/import` validates the URL and that `DRIVE_IMPORT_PROXY`
   is set, starts the background thread, returns immediately.
3. The import thread lists the folder via gdown, through B's proxy.
4. It filters: video extensions only; drops file IDs already in
   `videos.drive_file_id`.
5. For each remaining file: gdown downloads it through the proxy straight into
   `data/uploads/<unique-name>`; on success a `videos` row is inserted
   (`PENDING`, with `drive_file_id`); progress state updates.
6. A file that fails to download is recorded in `errors[]` and skipped; the
   import continues.
7. On completion the `job_runs` row and progress state are finalized.
8. The frontend, polling `import/status`, shows progress and a final summary;
   imported videos appear in the list as `PENDING`, ready for the normal
   publish flow.

## 9. Error handling

| Situation | Behavior |
|---|---|
| `DRIVE_IMPORT_PROXY` unset | `POST /import` → 503, clear message |
| URL is not a Drive folder URL | `POST /import` → 400 |
| Proxy unreachable / B down | Import fails fast; `state=error` with the reason |
| Folder listing fails (private folder, bad ID) | Import ends with a clear error; nothing inserted |
| A single file fails to download | Recorded in `errors[]`, that file skipped, import continues |
| Partial/interrupted file | Partial file deleted; no `videos` row created |
| Non-video file in the folder | Skipped (counted in `skipped`) |
| Filename collision in `data/uploads/` | Stored under a unique name (`<stem>_<drive_file_id><ext>`); `original_filename` keeps the real name |
| Import already running | Second `POST /import` → 409 |

## 10. Security

- The proxy on B is IP-locked to server A and basic-auth protected. Both the
  proxy password and `DRIVE_IMPORT_PROXY` are secrets kept in `.env`, never
  committed.
- `POST /api/videos/import` is behind the dashboard's existing `require_auth`.
- The user-supplied URL is only handed to gdown, which talks solely to Google
  Drive domains; the import validates it is a Drive folder URL first.

## 11. Known limitations & risks

- **gdown folder size.** gdown's public-folder listing is reliable up to
  roughly ~50 files per folder (a Google Drive folder-page limitation). For
  larger folders the import surfaces an explicit warning/error rather than
  silently importing a subset. Mitigation if hit: split the Drive folder, or
  replace just the *listing* step with the Drive API (API key) — a change
  isolated to `drive_import.py`.
- **gdown scraping fragility.** gdown scrapes Drive pages; a future Google
  change could require a gdown version bump. Isolated to one dependency and
  one module.
- **China↔US transfer speed.** Large folders may take many minutes; this is
  why the import is a background job with progress reporting.

## 12. Testing

- Unit tests (mock gdown / network):
  - Drive folder URL parsing — valid forms; rejects non-folder / non-Drive URLs.
  - Video-extension filtering.
  - Dedup against existing `drive_file_id`s.
  - Collision-safe filename generation.
  - `init_schema` idempotent `drive_file_id` migration (column added once,
    safe on repeated startup).
- Keep the existing test suite green (currently 38 tests) and add the above.

## 13. Out of scope

- Caption generation for imported videos.
- Fixed-folder or scheduled/automatic sync.
- Private Drive content (OAuth / service account).
- Single-file Drive links (folders only); trivial to add later if needed.

## 14. Implementation addendum — GFW and the encrypted tunnel

Discovered during deployment: a plain HTTP proxy does not work for this use
case. The proxy `CONNECT drive.google.com:443` request travels from server A
(China) to server B in cleartext; the GFW inspects China↔overseas traffic,
sees the Google hostname, and resets the connection. Non-blocked
destinations work through the proxy; Google destinations do not.

Fix (implemented): the A↔B hop is encrypted with an SSH tunnel.

- Server A runs a systemd service `mkt-drive-tunnel` —
  `ssh -N -L 127.0.0.1:8118:127.0.0.1:8118 root@107.174.1.82` (key-based,
  `Restart=always`) — forwarding A's `localhost:8118` to B's tinyproxy over
  the encrypted SSH connection.
- `DRIVE_IMPORT_PROXY` points at `http://mkt:<pw>@127.0.0.1:8118` — server
  A's local end of the tunnel, not server B directly.
- tinyproxy on server B is unchanged; tunnelled requests reach it from B's
  localhost (covered by tinyproxy's default `Allow 127.0.0.1`).
- The dedicated SSH key on A is restricted on B's `authorized_keys` with
  `restrict,port-forwarding,permitopen="127.0.0.1:8118"` — it can only
  forward to the proxy, nothing else.
- The GFW sees only an ordinary SSH connection; the proxied hostnames stay
  inside the encrypted channel.

This changes only the A↔B transport — no mkt application code is affected;
`drive_import.py` still uses `gdown` with the `DRIVE_IMPORT_PROXY` URL.
