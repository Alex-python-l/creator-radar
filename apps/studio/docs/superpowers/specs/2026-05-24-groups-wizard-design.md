# Groups Wizard — Design

Date: 2026-05-24
Status: Approved (brainstorming complete; awaiting implementation plan)

## Purpose

Turn "starting a new TikTok account batch" into a single guided flow:

1. User buys proxies → pastes them in
2. User says N phones → we create N GeeLark profiles, each bound to one proxy, all under one named group
3. User pastes X messy account credential blobs → we parse and assign to the new phones
4. User manually logs in on each phone (still happens in the GeeLark desktop app)
5. User multi-selects and marks them logged-in in our UI
6. User clicks "start warmup" for the group
7. Later: import N videos from a Drive folder → pick group → publish round-robin to that group's warmed accounts

Today this is N separate page operations with no concept of a "batch". We add a first-class **Group** entity that wraps the whole lifecycle.

## Out of scope

- Automating the actual TikTok login (still manual in GeeLark)
- Auto-triggering warmup on login (explicit button only, to avoid burning paid phone minutes on a misclick)
- Re-doing the cron warmup schedule (existing `warmup_times` cron untouched)
- Auto-detecting which proxy belongs to which account (1:1 sequential binding is enough)

## Data model

### New table

```sql
CREATE TABLE groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,            -- also passed to GeeLark as groupName
  target_phone_count INTEGER,           -- what the user typed in wizard step 2
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  archived_at TEXT                       -- soft delete
);
```

### accounts — new columns (via `_migrate`)

| Column | Type | Purpose |
|---|---|---|
| `group_id` | INTEGER FK → groups.id ON DELETE SET NULL | Replaces lookup by `group_name` string |
| `password` | TEXT | Email/account password from credential paste |
| `tiktok_password` | TEXT | TikTok password if separate |
| `recovery_email` | TEXT | 2FA / recovery mailbox |
| `mail_inbox_url` | TEXT | Code-receiving service URL |
| `raw_credentials` | TEXT | Fallback: original pasted line so nothing is lost on parse failure |

Existing `group_name` column stays for one release: migration walks all distinct `group_name` values, creates a `groups` row per unique non-empty name, sets `accounts.group_id`. After this lands and you've confirmed the UI works, a follow-up can drop `group_name`.

### videos — new column

| Column | Type | Purpose |
|---|---|---|
| `group_id` | INTEGER FK → groups.id ON DELETE SET NULL | Constrains assignment pool to one group |

### Progress is NOT stored

No `groups.status` column. Per-group counts are computed by aggregating `accounts` and `videos` rows on every read:

```
phones        = COUNT(accounts WHERE group_id=?)
assigned      = COUNT(accounts WHERE group_id=? AND email IS NOT NULL)
logged_in     = COUNT(accounts WHERE group_id=? AND tiktok_logged_in=1)
warmed        = COUNT(accounts WHERE group_id=? AND warmed_up=1)
published     = COUNT(videos   WHERE group_id=? AND result='SUCCESS')
pending_video = COUNT(videos   WHERE group_id=? AND result='PENDING')
```

Single source of truth → no drift between a stored state machine and actual row state.

## Backend

### New service modules

```
app/services/groups_service.py
  create(name, target_phone_count, notes) -> int
  list_all() -> [{id, name, counts, created_at, ...}]
  get(group_id) -> {..., counts}
  archive(group_id)
  counts_for(group_id) -> dict     # the aggregation above

app/services/proxy_intake.py
  parse_lines(text) -> [{line_no, proxy, error?}]
  # Splits on newlines, validates each line matches scheme://[user:pass@]host:port,
  # returns malformed lines with an error string instead of raising.

  check_batch(proxies) -> [{proxy, ok, exit_ip, latency_ms, error?}]
  # Fan out to proxy_check_service.check_one via ThreadPoolExecutor;
  # never raises (each probe result is a dict).

app/services/credentials_parser.py
  ParsedAccount = TypedDict with email, password, tiktok_username,
    tiktok_password, recovery_email, mail_inbox_url, raw, warnings: list[str]

  parse_lines(text) -> [ParsedAccount]
  # Per-line scanning, field-by-shape recognition (see rules below).
```

### Modified services

```
app/services/geelark_service.py
  + create_profiles_for_group(conn, client, group_id, proxies: list[str]) -> generator
      # For each proxy: create one accounts row (group_id set, proxy set),
      # then create GeeLark phone (serialName auto-generated as "<group>-NN",
      # proxy and groupName both passed), write back geelark_phone_id,
      # install TikTok. Yields log lines.

  # Existing create_profiles(conn, client, settings) stays for backward compat.

app/services/warmup_service.py
  - run_warmup(conn, client, duration_minutes=None, wait=True)
  + run_warmup(conn, client, *, group_id: int | None = None,
               duration_minutes=None, wait=True)
  # group_id=None preserves today's behavior (warm every logged-in,
  # not-yet-warmed account). With group_id, the same query gets
  # AND group_id=? appended.

app/services/posting_service.py
  - publish(conn, client, cooldown_hours=2, force=False)
  + publish(conn, client, *, group_id: int | None = None,
            cooldown_hours=2, force=False)
  # Without group_id: today's behavior. With group_id, both the manual
  # and auto branches filter videos and the assignment pool to that group.
```

### New API endpoints (all return JSON or SSE; all writes go through `OPERATION_LOCK`)

```
POST   /api/groups                          {name, target_phone_count, notes}   -> {id}
GET    /api/groups                          -> [{id, name, counts, ...}]
GET    /api/groups/{id}                     -> {..., counts, accounts, videos}
POST   /api/groups/{id}/archive             -> {ok: true}

POST   /api/groups/{id}/proxies/check       {proxies: ["url",...]}              -> [{proxy, ok, exit_ip, ...}]
POST   /api/groups/{id}/phones/create       {proxies: ["url",...]}              -> text/event-stream

POST   /api/groups/{id}/accounts/parse      {text: "..."}                       -> [ParsedAccount]
POST   /api/groups/{id}/accounts/assign     {accounts: [ParsedAccount,...]}     -> {assigned: N, skipped: M}

POST   /api/accounts/bulk-mark-logged-in    {ids: [int,...]}                    -> {updated: N}

POST   /api/groups/{id}/warmup/start        (no body)                           -> text/event-stream
POST   /api/groups/{id}/publish             (no body)                           -> text/event-stream
```

`proxies/check` and `phones/create` are split because the user needs to see probe results before deciding what to create. Same for `accounts/parse` and `accounts/assign` — the parsed preview is editable in the UI before commit.

`bulk-mark-logged-in` lives under `/api/accounts/` (not `/api/groups/`) because the capability itself is unrelated to grouping — the Accounts page should expose it too.

## Frontend

### Tabs

`Dashboard | Groups (new) | Accounts | Videos | Settings`

`Accounts` and `Videos` pages get a group-filter chip strip at the top (same pattern already used elsewhere in the app).

### Groups list page

A simple table:

```
Name           Created       Phones    Logged-in   Warmed     Published    Actions
project-jp     2026-05-24    12 / 15   8 / 12      5 / 8      23           [open] [archive]
```

Top-right: `[+ New group]` opens the wizard as a full-screen modal.

### Wizard — 4 steps

**Step 1 — Name + Proxies**
- Inputs: group name (required, uniqueness validated server-side), notes (optional), proxies textarea (one URL per line, placeholder shows `socks5://user:pass@host:port`)
- Button: `Validate proxies` → calls `proxies/check`. Each row gets 🟢 (with exit_ip + latency) or 🔴 (with error text)
- Button: `Next` → only allowed once at least 1 row exists; user can uncheck failing rows or choose "include all"

**Step 2 — Create phones**
- Header: "Creating 12 phones for `project-jp`, each bound to one proxy"
- Button: `Start` → SSE stream live-rendered (reuse the streaming pattern from current `create-profiles-stream`)
- After done: table of `profile_name | phone_id | proxy | TikTok installed?`
- Failed rows get a per-row `Retry` button

**Step 3 — Parse accounts + assign**
- Textarea: paste the messy credential blob
- Button: `Parse` → calls `accounts/parse` → renders an **editable preview table**:
  ```
  Row | email | password | tk_username | tk_password | recovery | inbox | warnings
  ```
  - Column headers are drag-reorderable (in case the parser guessed wrong on which field is which)
  - Cells are inline-editable
  - Rows can be deleted
  - Warnings render as red chips so the user sees what didn't parse
- Footer: "Will assign `min(parsed_rows, group_phones_without_credentials)` accounts, sorted by `accounts.id` ascending"
- Button: `Confirm assignment` → calls `accounts/assign`

**Step 4 — Wait for login + start warmup**
- Table of all accounts in the group with columns: `profile_name | phone_id | email | ☐ logged-in`
- The user goes to GeeLark desktop, opens each phone, logs in, then comes back and checks the boxes here
- `Select all` / `Invert` toggles
- Button: `Confirm logged-in status` → `bulk-mark-logged-in`
- Button (newly enabled): `▶ Start warmup now` → `groups/{id}/warmup/start` (SSE)
- `Finish` closes wizard → land on Group detail page

### Group detail page

```
[name] [created] [count chips]                                 [archive]

Progress
  Phones        12 / 15  ████████████░░░
  Accounts assigned   11 / 12
  Logged-in           8 / 11
  Warmed up           5 / 8
  Published videos    23   (4 pending)

Actions
  [▶ Warmup this group]   [▶ Publish this group]   [+ Add more phones (wizard)]

Accounts (filtered to this group)    ← reuse Accounts page table component
Videos   (filtered to this group)    ← reuse Videos page table component
```

### Videos page

- Add group filter chip strip
- Upload form: require a "target group" select before allowing upload/import
- Drive import wizard: same — require target group selection

## Credentials parser rules

Per line, split on any of: whitespace, comma, semicolon, pipe, tab. Classify each non-empty token:

| Match | Goes to |
|---|---|
| `[\w.+-]+@[\w.-]+\.[a-z]{2,}` | First email → `email`, second → `recovery_email`, more → warning |
| Starts with `@` and ≥3 alphanumeric/underscore | `tiktok_username` |
| Starts with `http://` or `https://` | `mail_inbox_url` |
| 6–8 digits only | Drop (likely a 2FA code, not a secret) |
| Anything else | First → `password`, second → `tiktok_password`, third+ → warning |

The parser does NOT guess `tiktok_username` from leftover tokens. If no token started with `@`, `tiktok_username` is left empty and the user fills it in the preview table. (Guessing here is too error-prone — passwords with no `@` look identical to handles.)

Always set `raw` to the original line. Always populate `warnings` with what couldn't be classified. UI surfaces warnings in red.

## Locking and concurrency

- All write endpoints acquire `OPERATION_LOCK` (existing global), same as current code
- `proxies/check` and `accounts/parse` are read-only and don't acquire the lock
- SSE streaming endpoints follow the existing `sse_from_generator(locked_generator(...))` pattern
- Scheduler jobs (`warmup`, `status_check`, `pic_check`) keep their current locking — the new group-scoped variants share the same `run_warmup` body so they automatically inherit it

## Testing

New test files:

```
tests/test_credentials_parser.py
  - happy path: email, password, tk_username, tk_password, recovery, inbox in random order
  - missing fields (only email + password)
  - extra unclassified tokens → warnings
  - TOTP digits ignored
  - two emails → second is recovery_email
  - raw always preserved

tests/test_proxy_intake.py
  - parse_lines: valid URLs, malformed URLs returned with errors, empty lines skipped
  - check_batch: fan-out with fake http_post, mixed ok/fail results

tests/test_groups_service.py
  - create + uniqueness
  - counts_for aggregation (set up rows in various states)
  - archive sets archived_at, list_all excludes archived

tests/test_geelark_service_create_for_group.py
  - create_profiles_for_group with fake client: verifies proxy + groupName passed,
    accounts row inserted with group_id, geelark_phone_id written back,
    TikTok install attempted
```

Extend existing tests:

```
tests/test_warmup_service.py
  + group_id filter scopes target set

tests/test_posting_service.py
  + group_id filter scopes both manual and auto branches
  + cooldown still respected when group_id is set
```

Existing 38 passed must stay 38+ passed.

## Migration

`app/db._migrate` additions (idempotent column adds + one-time data backfill):

1. `CREATE TABLE IF NOT EXISTS groups ...`
2. `ALTER TABLE accounts ADD COLUMN group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL` (if missing)
3. Same for `password`, `tiktok_password`, `recovery_email`, `mail_inbox_url`, `raw_credentials`
4. `ALTER TABLE videos ADD COLUMN group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL` (if missing)
5. Backfill: for each distinct non-empty `accounts.group_name`, `INSERT OR IGNORE INTO groups(name) VALUES (?)`, then `UPDATE accounts SET group_id=(SELECT id FROM groups WHERE name=accounts.group_name)`

`group_name` column is retained for one release for safety; it's no longer read by app code after this lands.

## Implementation order

1. Migration (groups table + new columns + backfill) — keeps existing reads working
2. `credentials_parser.py` + tests
3. `proxy_intake.py` + tests
4. `groups_service.py` + groups CRUD API + tests
5. `geelark_service.create_profiles_for_group` + tests
6. `warmup_service` / `posting_service` group_id filter + extended tests
7. SSE endpoints for `phones/create`, `warmup/start`, `publish`
8. `bulk-mark-logged-in` endpoint + test
9. Frontend: Groups list page + Group detail page (no wizard yet — manually test endpoints)
10. Frontend: Wizard step 1 → 2 → 3 → 4
11. Videos page: group filter + upload-requires-group
12. Accounts page: group filter

Each step ships green-tested before the next.
