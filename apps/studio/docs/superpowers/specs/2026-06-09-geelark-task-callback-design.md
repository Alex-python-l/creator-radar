# Design: GeeLark Task-Done Callback (instant stop_phones)

**Date**: 2026-06-09
**Status**: Approved (mixed approach: callback main path + sparse polling fallback)

## Problem

A GeeLark cloud phone bills per minute, so every second between "task done" and `stop_phones` is wasted money. Current detection is poll-only:

- Warmup: first check at +160s, then every 10s up to 60 attempts.
- Publish / Engagement: every 30s up to 40 attempts.

Worst-case latency from `status=3` to `stop_phones` call: ~10s (warmup) or ~30s (publish/engagement). GeeLark itself does not stop a phone on task completion — `chargeMode: 0` pay-per-minute requires us to call `stop_phones` explicitly.

We want detection latency reduced to ~1s for the common case, without losing the polling safety net.

## Out of scope

- Phone-level idle auto-shutdown (GeeLark exposes no such API; the existing `idle_phone_sweeper` cron is the only backstop and stays as-is).
- Rewriting the warmup / publish / engagement state machines. We only change *when* `stop_phones` is invoked, not the surrounding bookkeeping.
- Webhook security beyond a shared-secret query token. GeeLark does not document an HMAC signature and `/open/v1/callback/set` accepts any URL — we cannot bind to a richer scheme until GeeLark publishes one.

## Discovery (verified 2026-06-09)

- `/open/v1/callback/get` → `{"url": ""}` when unset; returns the configured URL after set.
- `/open/v1/callback/set` accepts plain HTTP and any URL string (no validation). Returns `{}` on success.
- The docs list `/webhook/*` but those return 404; the live endpoints are `/callback/*`. The docs are wrong.
- Payload schema for the inbound POST is **not documented**. We will discover it in Phase 0 instead of guessing.

## Architecture

```
┌──────────────────── GeeLark cloud ─────────────────────────┐
│ task X done → POST <url>/api/geelark/callback?token=…       │
└─────────────────────────────┬───────────────────────────────┘
                              │ HTTP POST
                              ▼
┌──────────────────── web tier (mkt) ─────────────────────────┐
│  /api/geelark/callback   no-auth + shared-token check       │
│  1. log raw body         to geelark_callback_log table      │
│  2. parse {task_id,...}  using Phase-0-derived schema       │
│  3. look up task_id      across publications / accounts /   │
│                          engagement_runs                    │
│  4. defer "finalize"     to mkt-worker (queue=phones)       │
└──────────────────────┬──────────────────────────────────────┘
                       │ procrastinate defer
                       ▼
┌──────────────────── mkt-worker ─────────────────────────────┐
│  finalize_task_callback                                     │
│  - call stop_phones([phone_id])  (idempotent)               │
│  - write terminal state          (publication.result /      │
│                                   engagement_run.result /   │
│                                   warmup advance)           │
│  - release phone lease                                      │
└─────────────────────────────────────────────────────────────┘
```

Polling stays in place as a fallback (see "Polling fallback" below). Both paths converge on the same finalize logic; whoever gets there first wins, the loser observes "already terminal" and no-ops.

## Phasing

Implementation is staged so we never deploy a guessed payload schema:

### Phase 0 — Probe (ship first, observe, then build)

Goal: capture one real callback payload from GeeLark.

Components:
- `geelark_callback_log` table (id, received_at, source_ip, headers_json, body_text, parsed_task_id nullable, parsed_status nullable, handled bool).
- `POST /api/geelark/callback` endpoint that **only logs** — no parsing, no `stop_phones`, no lookup. Returns 200 always.
- `.env`: `GEELARK_CALLBACK_TOKEN` (random 32-char). Endpoint requires `?token=<value>` and 401s without it.
- One-shot script `scripts/register_geelark_callback.py` that posts to `/open/v1/callback/set` with the production URL + token.

Deploy steps for Phase 0:
1. Generate token, add to prod `.env`.
2. Deploy code (migration + endpoint + script).
3. Run the script to register the URL.
4. Trigger any warmup (or wait — daily_warmup_scan will fire one within 10 min).
5. Read `geelark_callback_log` rows to learn the payload shape.

Phase 0 is complete when at least one real callback row exists in the table.

### Phase 1 — Wire the callback to finalize

Goal: callback path actually stops phones.

Components:
- Parser for the payload, based on Phase 0 observation. Conservative: extract `task_id`, `status`. If schema varies by task type, branch on the type field.
- Task-id lookup helper `find_task_owner(task_id) -> {kind, account_id, phone_id, ...}` scanning across `publications`, `engagement_runs`, and `accounts.warmup_task_id`.
- Procrastinate task `finalize_task_callback(task_id, status)` on `queue=phones` that:
  - looks up the owner
  - calls `stop_phones`
  - calls the same finalize helpers already used by the polling chain (`advance_warmup_one`, `check_publication_one`, `check_engagement_one`), so behavior matches polling exactly
  - releases the lease
  - flips `geelark_callback_log.handled = true`
- Idempotency: each finalize helper already checks "is this already in terminal state, skip if so". Callback retries arriving after polling already finished are no-op.

### Phase 2 — Loosen polling to fallback density

Once Phase 1 is observed working for ~24h, retune the polling intervals:

| Task | Before | After (callback-era) |
|---|---|---|
| Warmup `tasks/warmup.py` | first +160s, then 10s × 60 | unchanged (already tight; the warmup case is the most cost-sensitive)|
| Publish `tasks/publish.py` | 30s × 40 (~20 min) | 15s × 40 (~10 min) |
| Engagement `tasks/engagement.py` | 30s × 40 (~20 min) | 15s × 40 (~10 min) |

Rationale: callback handles the happy path in ~1s. Polling is only the fallback for callback loss; tightening publish/engagement from 30s → 15s halves the worst-case fallback latency and keeps total budget at a reasonable 10 min before timeout escalation.

### Phase 3 — Monitoring

- A `*/30 * * * *` cron checks "did we receive any callback in the last 30 min?" and logs a warning to the `mkt-worker` journal (and pings healthchecks.io with `?fail=true` if integrated) when zero callbacks arrive despite outstanding tasks. This catches "GeeLark stopped delivering callbacks" silently.

## Schema

One new table:

```sql
CREATE TABLE geelark_callback_log (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    source_ip       VARCHAR(45),
    headers_json    JSONB NOT NULL,
    body_text       TEXT  NOT NULL,
    parsed_task_id  VARCHAR(32),
    parsed_status   INTEGER,
    handled         BOOLEAN NOT NULL DEFAULT FALSE,
    handled_at      TIMESTAMP,
    handle_outcome  TEXT
);
CREATE INDEX idx_gcl_task ON geelark_callback_log (parsed_task_id);
CREATE INDEX idx_gcl_received ON geelark_callback_log (received_at DESC);
```

Retention: keep rows for 7 days (a cron prunes older). Long enough to debug a problem, short enough to keep the table cheap.

No changes to `publications` / `engagement_runs` / `accounts` — they already store `geelark_task_id` and that is the join key.

## API

```
POST /api/geelark/callback?token=<GEELARK_CALLBACK_TOKEN>
  no auth header required (GeeLark cannot send our HMAC cookie)
  401 if token missing or wrong
  200 always on accept (so GeeLark does not retry on our parse errors)
```

The response body is `{}`; GeeLark only checks status code.

## Security

- Token in query string, random 32 chars, set per environment via `.env`. Token compromise = attacker can submit fake callbacks, but the worst they cause is a stray `stop_phones` on a known phone id (which they would also need to know). Acceptable for the threat model.
- HTTPS would be ideal but production currently serves HTTP. Adding TLS termination is a separate project; this design works over HTTP today.
- Endpoint is rate-limited by FastAPI's default behavior (no special limit added) — GeeLark callback volume is bounded by our own task throughput, so abuse from there is implausible.

## Failure handling

| Failure | Behavior |
|---|---|
| GeeLark never sends callback | Polling fallback fires within 15s (publish/engagement) or 10s (warmup). |
| Callback arrives but `task_id` unknown | Log to `handle_outcome="unknown_task"`, return 200. Most likely the task was cancelled and the polling chain already finalized. |
| Callback parsing throws | Log raw, return 200. Manual recovery via the polling chain. |
| `stop_phones` fails | Lease stays, `idle_phone_sweeper` (every 5 min) catches it. |
| Callback arrives twice | First completes finalize, second observes terminal state and is a no-op. `handled = true` is set on the first row; the second still gets logged so we can debug duplicates. |
| `callback/set` URL drifts | Phase 3 monitor detects "no callbacks received" and we re-run the register script. |

## Testing

- `tests/test_api_geelark_callback.py`:
  - missing token → 401
  - wrong token → 401
  - valid token + arbitrary body → 200 and a row in `geelark_callback_log`
  - valid token + known task_id → defers `finalize_task_callback` job
- `tests/test_tasks_finalize_callback.py`:
  - finalize on warmup task_id → calls stop_phones, advances warmup
  - finalize on publish task_id → calls stop_phones, marks publication
  - finalize on engagement task_id → calls stop_phones, marks run
  - finalize when already terminal → no-op, no extra stop_phones
- `tests/test_repositories_callback_log.py`: insert + prune + lookup.
- Baseline `pytest -q` must stay ≥ 669 (current) plus the new tests.

## Deployment

1. Phase 0: deploy code + table + register script. Generate `GEELARK_CALLBACK_TOKEN` and add to prod `.env` *before* the register script runs.
2. Watch `geelark_callback_log` until at least 3 callbacks arrive for 3 different task types (warmup, publish, engagement). Update the parser in Phase 1 to match.
3. Phase 1: deploy parser + finalize task. Do NOT loosen polling yet — keep belt-and-suspenders for the first 24h.
4. After 24h with callback path observed working: Phase 2 (loosen polling).
5. Phase 3: monitoring cron in a follow-up commit.

No destructive migrations; no pg_dump precaution beyond routine deploy.
