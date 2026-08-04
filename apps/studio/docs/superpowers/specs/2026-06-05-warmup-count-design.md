# Warmup Count Completion Design

## Goal

Change account warmup completion from elapsed warmup time to successful warmup
run count. An account becomes publish-eligible only after a configured number
of successful warmup task completions, with an explicit manual completion
escape hatch.

## Decisions

- Add `accounts.warmup_count` with default `0`.
- Add `accounts.target_warmup_count` with default `10`.
- Drop `accounts.time_spent_warming`.
- Drop `accounts.max_warm_time`.
- Replace `settings.default_max_warm_time` with
  `settings.default_target_warmup_count`, defaulting to `"10"`.
- Existing completed accounts migrate to `warmup_count=10`,
  `target_warmup_count=10`.
- Existing incomplete accounts migrate to `warmup_count=0`,
  `target_warmup_count=10`.

## Behavior

Each GeeLark warmup task still receives a short per-run duration in minutes.
That duration remains an operational parameter for GeeLark only; it no longer
defines account completion.

On a GeeLark warmup task success (`status == 3`), the service increments
`warmup_count` by one and writes `last_warm_result="Success"`. When the new
count is greater than or equal to `target_warmup_count`, it sets
`warmed_up=1`.

Failures, timeouts, and exceptions do not increment `warmup_count`. They write
`last_warm_result="Failed"` plus the existing diagnostic notes.

The existing `warmup_account` worker chaining remains in place. After a
successful run, if the account is still logged in, has a GeeLark phone, and is
not yet `warmed_up`, the task requeues itself. This repeats until the target
count is reached or a failure stops the chain.

Manual completion sets `warmed_up=1` and raises `warmup_count` to at least
`target_warmup_count`.

## UI

The accounts table progress bar reads `warmup_count / target_warmup_count`.
The Settings page labels the default as target warmup runs/counts, not seconds.
Manual completion is exposed as a clear account action for selected accounts.

## Testing

- Repository/model tests cover the new account columns and old column removal.
- Migration tests cover column replacement and basic existing-row migration.
- Warmup service tests cover success increment, completion at target, and no
  increment on failed GeeLark status.
- Worker tests cover requeue below target using `warmup_count`.
- Settings default/API tests cover `default_target_warmup_count`.
- API tests cover manual warmup completion.
- Frontend build verifies updated TypeScript field names.

## Deployment Note

This migration drops columns. Production deploy must take a PostgreSQL backup
with the documented `docker exec svm-postgres pg_dump` flow before running
`alembic upgrade head`.
