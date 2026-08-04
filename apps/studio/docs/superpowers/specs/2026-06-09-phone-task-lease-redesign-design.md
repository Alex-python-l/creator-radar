# Phone-Task Lease Redesign — Replace Procrastinate Locks with DB Leases

## Overview

Production warmup pipeline currently uses Procrastinate per-account `lock=phone:acc{id}` to serialize phone activity. When a worker process dies in any way that leaves `procrastinate_jobs.status='doing'` rows behind without a matching dead-worker signal (SIGKILL during a long blocking call, OOM, container restart, network partition mid-task), the lock is never released and `daily_warmup_scan` keeps deferring fresh todo jobs for that account — but those todo jobs never start because the dead doing job still holds the lock. The 2026-06-08 incident saw 12 accounts stuck for 16+ hours this way, with `warmup_count` frozen at 11 or 12 while peers progressed to 17.

Root cause: **Procrastinate's `lock=` field is in-band data on the jobs table; mutual exclusion depends on the running-worker fleet correctly transitioning doing rows to succeeded/failed.** Any failure mode that strands a `doing` row also strands the lock. `reset_stale_doing_jobs` (`app/tasks/__init__.py:44`) is a partial fix — it only catches doing rows whose `worker_id` is no longer in `procrastinate_workers`, missing the case where the worker_id is reused or its heartbeat row outlives the actual job execution.

This redesign moves phone-level mutual exclusion off Procrastinate and onto a per-account DB lease, splits warmup into a fire-and-forget submit-then-poll pair (the same pattern publish already uses), and removes the in-band `lock=` parameter from all `phones`-queue tasks. Worker death — by any cause — becomes self-healing: the lease expires, `daily_warmup_scan` picks the account up again, no manual SQL required.

## Goals

- **No combination of worker crash / GeeLark API stall / network partition / PG primary failover can leave an account permanently blocked.** Recovery time is bounded by the lease TTL (~20 minutes) plus the cron scan interval (5 minutes).
- Procrastinate's only job becomes scheduling and retry. It no longer carries mutual-exclusion responsibility.
- All long-running blocking GeeLark calls are removed from inside a single Procrastinate task. Each task body runs for seconds, not minutes — minimizing the crash window.
- `publish` adopts the same lease pattern so the two pipelines share one failure model and one recovery mechanism.
- No new `geelark_client.py` API surface required. The design tolerates GeeLark having no "list active tasks on a phone" endpoint.
- The redesign is observable: a single `SELECT account_id, phone_busy_until, warmup_task_id FROM accounts WHERE phone_busy_until IS NOT NULL` answers "who is currently holding a lease and what for".

## Non-Goals

- No replacement of Procrastinate as the queue runtime. Its scheduling, retry, and concurrency limits remain in use.
- No state-machine `enum` column on accounts. `phone_busy_until` (timestamp) + `warmup_task_id` (string) + existing `last_warm_result` already express every state we need. Adding an enum is redundant.
- No `phone_lease_token` UUID or `phone_lease_purpose` enum (rejected as YAGNI during brainstorming — single-task seconds-level execution makes lease-token race protection unnecessary; observability needs are served by `procrastinate_jobs.task_name`).
- No PostgreSQL advisory-lock approach (rejected: depends on TCP connection health, lost on PG failover, harder to observe).
- No retry of GeeLark tasks the worker submitted but failed to record (the T2→T3 crash window — see below). Accepted as a vanishingly rare cost, measured in single-account-warmup units.
- No changes to `pic_check`, `proxy_check`, `heartbeat`, `engagement` tasks. They don't share phones with warmup/publish in a way the current locks protect against.
- No frontend changes. The redesign is invisible to the dashboard.

## Data Model

### `accounts` — two new columns

```sql
phone_busy_until    TIMESTAMP WITHOUT TIME ZONE NULL  -- lease expiration; NULL = idle
warmup_task_id      VARCHAR(64)                  NULL  -- current in-flight GeeLark task id;
                                                       -- 'pending' sentinel between lease grab and GeeLark submit
```

`phone_busy_until` follows the existing convention in `accounts.last_warm_at` of naive UTC (`datetime.utcnow()`), so all comparisons stay naive-vs-naive — no Postgres session-timezone surprises.

### `publications` — no new columns

`publications.geelark_task_id` already exists; that is the publish equivalent of `accounts.warmup_task_id`. The redesign reuses it directly. Publish does **not** get a per-publication lease — `accounts.phone_busy_until` is sufficient because a phone is a single physical resource (only one start/stop/submit sequence can be in flight on it at a time, regardless of whether it's a warmup or a publish or which publication).

### Index

```sql
CREATE INDEX idx_accounts_warmup_inflight
  ON accounts (phone_busy_until)
  WHERE warmup_task_id IS NOT NULL;
```

Supports the `reclaim_in_flight_warmup` cron scan. Partial index — only the small in-flight subset.

## Task Topology

### Warmup (new)

```
start_warmup_task        queue=phones,  no lock=     Seconds. Acquire lease → start phone → submit
                                                     GeeLark task → write task_id → chain check.
                                                     Failure at any step: release lease, stop phone,
                                                     raise. Procrastinate retries per its own policy.

check_warmup_status_task queue=default, no lock=     Seconds. Poll one GeeLark task_id. Non-terminal
                                                     → self-defer 30s up to 40 retries (20-min window).
                                                     Terminal → update warmup_count, release lease,
                                                     stop phone.

reclaim_in_flight_warmup queue=default, cron */5 *   Seconds per scan. SELECT accounts WHERE
                                                     warmup_task_id IS NOT NULL AND
                                                     phone_busy_until < now(). For each: if task_id
                                                     == 'pending' → release lease + clear task_id +
                                                     log warning (T1↔T3 crash, acceptable); else →
                                                     defer one check_warmup_status_task to resume
                                                     polling.
```

### Warmup scheduler — `daily_warmup_scan` modification

```sql
SELECT id FROM accounts
WHERE warmup_mode = 'daily'
  AND tiktok_logged_in = 1
  AND geelark_phone_id IS NOT NULL
  AND (last_warm_at IS NULL OR last_warm_at < :cutoff)
  AND (phone_busy_until IS NULL OR phone_busy_until < now())   -- NEW
```

Defer call drops the `lock=_phone_lock(aid)` and `queueing_lock=...` parameters. Both responsibilities now live on the lease.

### Warmup — old code removed

- `warmup_account_task` (`app/tasks/warmup.py:59`)
- `warmup_task` manual-scan entry (`app/tasks/warmup.py:38`)
- `_phone_lock` helper (`app/tasks/warmup.py:26`)
- `enqueue_warmup_for_accounts` / `queue_warmup_for_eligible_accounts_sync` (`app/tasks/warmup.py:100`, `:114`) — callers in `app/services/` to be redirected to `start_warmup_task.defer_async`
- `_should_requeue_after_success` self-requeue logic — replaced by the lease + scan loop
- `reset_stale_doing_jobs` (`app/tasks/__init__.py:44`) — no longer needed
- `mkt-worker.service` `ExecStartPre` line that calls it — removed from systemd unit

### Publish (modified in place)

`publish_publication_task` (`app/tasks/publish.py:73`):
- Remove `lock=_phone_lock(account_id)` from the deferred-from-PhoneBusy retry path (line 92) — replaced by lease acquire
- Add `try_acquire_phone_lease` at task entry. Acquire failure raises `PhoneBusy`, which the existing retry path already handles
- Release lease on submit failure or on terminal outcome (success / scheduled retry / final failure)

`check_publication_status_task` (`app/tasks/publish.py:131`):
- On terminal status (success or failure), release the account-level phone lease

`reclaim_in_flight_publish` (new cron `*/5 * * * *`):
- SELECT publications WHERE result = 'INITIATED' AND geelark_task_id IS NOT NULL AND geelark_task_id != 'pending' AND EXISTS account with expired lease for this account_id
- For each → defer check_publication_status_task once

### Service layer

`app/repositories/accounts.py` gains:

```python
def try_acquire_phone_lease(session, account_id: int, ttl_seconds: int) -> bool:
    """Atomic CAS via single UPDATE returning rowcount. Returns True iff acquired."""

def release_phone_lease(session, account_id: int) -> None:
    """Unconditional: SET phone_busy_until = NULL, warmup_task_id = NULL."""

def mark_warmup_task_id(session, account_id: int, task_id: str) -> None:
    """SET warmup_task_id = :task_id."""
```

No new repository functions on `publications.py` — publish uses the same `accounts.py` lease helpers.

`app/services/warmup_service.py`:
- New `start_warmup_one(session, client, account_id)` — the T1→T4 sequence below
- New `advance_warmup_one(session, client, account_id, task_id)` — single GeeLark poll → DB transition
- Old `run_warmup_for_accounts` / `run_warmup` deleted

## Atomicity & Idempotency

The start sequence is split into four atomic steps. Crashes between any pair are recovered by the lease + reclaim cron without manual intervention.

```
T1  (same DB tx)   Acquire lease via CAS UPDATE                 (ttl 20 min)
                   Write warmup_task_id = 'pending'             (sentinel)
                   COMMIT

T2  (GeeLark side) client.start_phones([phone_id])
                   gtid = client.create_warmup_task(phone_id, dur)

T3  (DB)           UPDATE accounts SET warmup_task_id = :gtid

T4  (Procrastinate) check_warmup_status_task.defer_async(account_id, gtid)
```

| Crash point | DB state | GeeLark state | Recovery |
|---|---|---|---|
| Before T1 | unchanged | unchanged | nothing to recover |
| T1↔T2 | lease held, task_id='pending' | none | reclaim_cron sees task_id='pending' + lease expired → release lease + clear task_id. Next `daily_warmup_scan` re-defers. **No GeeLark side effect.** |
| T2↔T3 (single-digit ms window — `create_warmup_task` returns, then the next statement is the DB UPDATE) | lease held, task_id='pending' | task running | reclaim_cron releases lease + clears task_id. The orphan GeeLark task finishes on its own; we no longer track its id, so its outcome is silently dropped. **Accepted cost: one wasted warmup per crash in this window. The window is single-digit ms, the warmup is 2-5 minutes; expected loss is <1 warmup/year across the fleet.** |
| T3↔T4 | lease held, task_id=real | task running | reclaim_cron sees real task_id + lease expired → defer one check. Polling resumes from where it left off. |
| Inside check chain | lease held, task_id=real | task running or terminal | same as T3↔T4. |
| After terminal | lease released, task_id cleared, warmup_count updated | terminal | no recovery needed. |

Key property: **outside the single-digit-ms T2↔T3 window, no crash can produce a phantom GeeLark task that costs the user money or drifts DB state.** The T2↔T3 window is conservatively bounded by network RTT + Python statement execution time.

## Lease TTL Selection

- `_warm_one` historical max duration: `dur * 60 + 300` seconds = `5*60 + 300` = 600s = 10 minutes
- check chain: 40 polls × 30s = 20 minutes max from submit to terminal
- Lease TTL: **20 minutes** — comfortably above the polling window so a healthy task never has its lease expire mid-flight, but short enough that a crashed task is recovered within one reasonable workshop break.

Reclaim cron runs `*/5 * * * *` so worst-case recovery latency = lease TTL + cron interval = ~25 minutes from crash to retry.

## Removing `reset_stale_doing_jobs`

Once `lock=` is gone from all `phones`-queue tasks, a stranded `doing` row no longer blocks any other work — it just becomes a row that never transitions. Procrastinate's own `procrastinate_prune_stalled_workers_v1()` + the foreign-key `ON DELETE SET NULL` chain still eventually marks them `failed` via Procrastinate's worker-loss detection at the heartbeat-30s mark. We don't need our own cleanup pass.

The `mkt-worker.service` `ExecStartPre` line that calls `reset_stale_doing_jobs` is removed in the same deploy. The function itself is deleted.

## Migration / Deployment Sequence

1. **Alembic migration** adds 2 nullable columns to `accounts` + 1 partial index. Non-blocking; no table rewrite. (`alembic/versions/`).
2. **Code deploy** (tarball + restart). New tasks coexist with old `warmup_account_task` registration but `daily_warmup_scan` now defers `start_warmup_task`.
3. **Manual cleanup of 12 stranded doing jobs** (one-shot SQL):
   ```sql
   UPDATE procrastinate_jobs
   SET status = 'cancelled'::procrastinate_job_status
   WHERE status = 'doing' AND task_name = 'warmup_account'
     AND scheduled_at < now() - interval '1 hour';
   ```
4. **Observe** for 1-2 hours: `phone_busy_until` populates and drains; `warmup_count` advances on previously-stuck accounts.
5. **Cleanup commit**: delete `warmup_account_task`, `_phone_lock`, `reset_stale_doing_jobs`, the `ExecStartPre` line; deploy again.

PG backup taken before step 1 per the project's destructive-migration convention.

## Testing

- `tests/test_warmup_service.py`: cover the four crash windows (T1↔T2, T2↔T3, T3↔T4, in-check) by injecting failures into a fake GeeLark client + asserting lease + task_id state after the failure.
- `tests/test_daily_warmup_scan.py`: cover the new `phone_busy_until` filter — held lease excludes the account, expired lease includes it.
- `tests/test_reclaim_in_flight_warmup.py`: cover the two reclaim branches (pending sentinel → release; real task_id → defer check).
- `tests/test_publish_lease.py`: parallel coverage for the publish pipeline.
- Pre-existing test baseline (385+ passed) must stay green.

## Risks

- **Lease TTL too tight.** If a real warmup occasionally exceeds 20 minutes (e.g., GeeLark's polling endpoint is slow that day), the lease expires mid-task and `reclaim_cron` defers a redundant check. The redundant check is idempotent (it just reads GeeLark state) so the only cost is a wasted poll cycle. Mitigation: monitor lease-expiry-mid-flight frequency; bump TTL to 30 minutes if it ever fires.
- **Reclaim cron storm after long outage.** If the worker is down for hours, reclaim_cron's first scan after recovery could enqueue hundreds of check tasks at once. The `default` queue's concurrency limit absorbs this; checks are sub-second each.
- **PG primary failover during T1's COMMIT.** The standard write-acknowledgement window. If a quorum loss eats the commit, the lease wasn't acquired — the task fails outright, Procrastinate retries, no harm.
- **Old running warmup_account jobs at deploy time.** Step 3 of the migration cancels them explicitly; the lease columns default to NULL so accounts are immediately eligible for `start_warmup_task` on the next scan tick.

## Open Questions

None. All design points resolved during brainstorming. The lone "depends on GeeLark API" question (`query_phone_active_tasks` for reverse lookup of orphaned tasks) was eliminated by accepting the single-digit-ms T2↔T3 orphan cost.
