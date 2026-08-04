# Design: GeeLark Cost Audit

**Date**: 2026-06-09
**Status**: Approved (user delegated all detail decisions)

## Problem

Operators need to audit every GeeLark deduction: **which account spent it, and which task caused it**. The sidebar already shows the wallet balance, but there is no per-transaction view, no cross-time aggregation, no link from a charge back to the publish/warmup/engagement that triggered it.

## Out of scope

- Forecasting / budget alerts (D = audit, not B = predictive monitoring).
- Re-implementing GeeLark's billing portal (we mirror, we don't manage plans).
- Currency conversion — GeeLark `amount` is USD and we display USD.
- 1-to-1 mapping for transactions GeeLark itself can't tie to a task (the `idle_phone_sweeper` cleanup path). These are surfaced as `attribution_kind = "idle"`, on purpose — surfacing waste is an audit goal.

## Data source: `/open/v1/billing/transaction/detail`

Verified live against GeeLark on 2026-06-09. Returns `{total, list[]}` with each item:

```json
{
  "id": "623208480613918306",       // unique txn id (string, treat as PK)
  "envId": "620176013586261591",    // cloud phone id → accounts.geelark_phone_id
  "amount": -0.049,                  // USD, negative for charges
  "usedTime": 7,                     // minutes (already rounded up by GeeLark)
  "type": 2,                         // GeeLark internal code (1 or 2 observed)
  "chargeType": 2,                   // pay-per-use only on this account
  "createdTime": 1780990801          // unix epoch (seconds) — stop time
}
```

**Critical constraints** (verified 2026-06-09):

- Server schema is `EnvFlowSearchReq`. The **only honored field is `limit`** (max 1000; >1000 → `[40004] EnvFlowSearchReq.Limit max`).
- Every other request field we tried is silently dropped: `page`, `pageNo`, `pageNum`, `pageIndex`, `current`, `offset`, `Offset`, `Skip`, `Start`, `cursor`, `marker`, `before`, `after`, `lastId`, `pageToken`, `startTime`, `BeginTime`, `EndTime`, `envId`, `EnvId`. There is **no pagination, no time filter** on this endpoint.
- The response's `total` field shows the real backend count (~1767 at deploy time), but anything beyond the newest 1000 is **unreachable**.
- **Effective contract**: one call returns the newest up-to-1000 transactions. Re-call on a schedule. Anything older than the trailing 1000 charges at first-deploy time is permanently absent from the audit.

Account today has **1767 historical transactions** of which the **latest 1000** are reachable. The mirror grows from there forward.

## Attribution model

A charge is a phone start→stop billing event, no task id attached. In our codebase **each publish / warmup / engagement_run wraps its own `start_phones` + `stop_phones`** (with `idle_phone_sweeper` as a backstop) — so a charge is **1:1 with one business task** in 95%+ of cases. The exceptions become "idle" attributions, which is the desired signal.

For each transaction, at query time we join sequentially:

1. **`envId` → `accounts.geelark_phone_id`** — always present (L1, hard link).
2. Within the time window `[createdTime − usedTime·60s − 30s, createdTime + 30s]` on that account, look at:
   - `publications.posted_at` → kind `"publish"`, label `"Publish video #N → TikTok"`
   - `engagement_runs.finished_at` → kind `"engagement"`, label `"Comment on @user"`
   - Account-level warmup completion: `accounts.last_warm_at` falling in window → kind `"warmup"`, label `"Warmup #k/n"`
   - Otherwise → kind `"idle"`, label `"Unattributed (sweeper / dropped task)"`

Multiple candidates: pick the one whose timestamp is closest to `createdTime`. The full ranked list is *not* shown — first hit wins (audit, not forensics).

The 30s leeway compensates for slight clock drift between our DB writes and GeeLark's bill-out moment.

## Architecture

```
┌─────── mkt-worker (procrastinate) ───────┐
│ sync_geelark_transactions  cron */15      │
│   crawl GeeLark → upsert ON CONFLICT DO   │
│   NOTHING; stop when first existing id    │
│   seen on a page.                         │
└────────────────────┬──────────────────────┘
                     │ writes
              geelark_transactions
                     │
┌────────────────────┴──────────────────────┐
│ web tier                                  │
│   GET /api/costs/transactions  list+join  │
│   GET /api/costs/summary       agg by ... │
│   GET /api/costs/export.csv    streamed   │
└────────────────────┬──────────────────────┘
                     │
              frontend Costs page
              (sidebar top-level)
```

## Schema

New table `geelark_transactions`, new Alembic migration head:

| column | type | notes |
|---|---|---|
| `id` | `varchar(32)` PK | GeeLark txn id |
| `env_id` | `varchar(32)` not null, indexed | → `accounts.geelark_phone_id` (no FK — accounts can be deleted) |
| `amount` | `numeric(10,4)` not null | USD; negative |
| `used_time_min` | `int` not null | minutes |
| `type` | `int` not null | raw GeeLark `type` |
| `charge_type` | `int` not null | raw GeeLark `chargeType` |
| `geelark_created_at` | `timestamp` not null, indexed | `to_timestamp(createdTime)` UTC |
| `synced_at` | `timestamp` not null | row insert time |

Indexes:
- `(env_id, geelark_created_at desc)` — list + join hot path.
- `(geelark_created_at desc)` — global timeline page.

## Sync task

`app/tasks/sync_transactions.py`:

```python
@procrastinate_app.periodic(cron="*/15 * * * *")
@procrastinate_app.task(name="sync_geelark_transactions")
async def sync_geelark_transactions(timestamp: int):
    data = await asyncio.to_thread(client.list_billing_transactions, limit=1000)
    rows = data["list"]               # newest up-to-1000
    repo.upsert_many(session, rows)   # conflicts on prior rows are expected
```

Each tick does one HTTP call and one upsert. The trailing 1000-row window is large enough that any normal cadence of billing activity ends up fully captured between ticks; rows that already exist trip `ON CONFLICT (id) DO NOTHING`.

The `scripts/backfill_geelark_transactions.py` is just a one-shot wrapper around the same call; it does not paginate either.

## Repository / query

`app/repositories/transactions.py`:

- `upsert_many(session, rows) -> bool` — INSERT ... ON CONFLICT (id) DO NOTHING; returns True iff at least one row was a no-op (used by the sync loop).
- `list_with_attribution(session, *, page, page_size, account_id?, group_id?, kind?, date_from?, date_to?) -> (rows, total)`
  - Base query: `geelark_transactions LEFT JOIN accounts ON env_id = geelark_phone_id LEFT JOIN groups ON accounts.group_id = groups.id`.
  - Time-window join: a `LATERAL` subquery that for each txn picks the nearest publication/engagement_run/warmup-marker within the window. Implemented as `UNION ALL` of three windowed selects, ordered by `abs(diff)`, `LIMIT 1`.
  - Filters apply on outer where; `kind` filter requires the lateral result, hence pushdown into a CTE.
- `summary(session, *, group_by, date_from, date_to) -> rows` — straightforward `SUM(amount)` grouping by `date_trunc('day', ...)`, `account_id`, `group_id`, or attribution kind. The attribution-kind grouping reuses the same lateral join CTE.

## API

`app/api/costs.py`, mounted under `require_auth`:

| Endpoint | Returns |
|---|---|
| `GET /api/costs/transactions?page=&page_size=&account_id=&group_id=&kind=&from=&to=` | `{items: [...txn with attribution...], total}` |
| `GET /api/costs/summary?group_by=day\|account\|group\|kind&from=&to=` | `{groups: [{key, label, total, count}]}` |
| `GET /api/costs/export.csv?from=&to=&account_id=...` | streamed CSV, columns: time, account, group, kind, target, used_min, amount_usd |
| `GET /api/costs/kpis` | `{today_spent, mtd_spent, day_avg_30d, idle_30d_pct}` |

Each txn item shape:

```json
{
  "id": "623208480613918306",
  "time": "2026-06-09T12:34:01Z",
  "amount_usd": -0.049,
  "used_min": 7,
  "env_id": "620176013586261591",
  "account": {"id": 42, "profile_name": "alice01", "platform_flags": {...}},
  "group": {"id": 3, "name": "IELTS-A"},
  "attribution": {
    "kind": "publish" | "engagement" | "warmup" | "idle",
    "label": "Publish video #112 → TikTok",
    "ref_type": "publication",  // for click-through
    "ref_id": 112
  }
}
```

## UI

New top-level sidebar item **"Cost"** (icon: receipt/dollar). Page `frontend/src/pages/Costs.tsx`:

1. **KPI row** (4 cards): Today / This month / Avg/day (30d) / Idle %.
2. **Filter bar**: Account select (typeahead), Group select, Kind select (`all / publish / warmup / engagement / idle`), date range (default last 7 days), "Export CSV" button.
3. **Table**:
   - Time (relative + absolute on hover)
   - Account (profile_name + platform icons, click → accounts page)
   - Group
   - Kind (badge, "idle" red, others neutral)
   - Target (clickable — `publication` → /videos, `engagement` → /comments, `warmup` → /accounts focus, `idle` → no link)
   - Used min
   - Amount (USD, two decimals, right-aligned)
4. **Pagination**: 50 rows/page, server-side.

Server-driven loading; no client-side caching beyond React Query's default 60s stale.

## Migration / deploy steps

1. Alembic head (will be merged with existing head):
   - `2026_06_09_geelark_transactions.py` — create table + indexes.
2. Code rollout — web + worker restart.
3. Run backfill **after** the new code is live (so the table exists):
   ```
   uv run python -m scripts.backfill_geelark_transactions
   ```
   Expected ~18 pages, finishes in ~10s. Idempotent (ON CONFLICT).
4. Cron picks up from there.

No data is at risk of destruction; the new table is additive. No `pg_dump` precaution needed beyond the routine deploy backup.

## Test plan

- `tests/test_geelark_client.py`: assert `list_billing_transactions(page=, page_size=)` POSTs the right payload to `/open/v1/billing/transaction/detail` and parses `{total, list}`.
- `tests/test_repositories_transactions.py`:
  - `upsert_many` is idempotent on repeated calls (no duplicate rows; returns `True` on full-overlap).
  - `list_with_attribution` correctly attributes:
    - publication within window → kind=publish.
    - engagement_run within window → kind=engagement.
    - warmup (`accounts.last_warm_at` in window) → kind=warmup.
    - no match → kind=idle.
    - multiple candidates → nearest wins.
- `tests/test_tasks_sync_transactions.py`: stop after first existing id is seen; no infinite loop on empty page.
- `tests/test_api_costs.py`: list endpoint shape, filter combinations, csv export Content-Type, summary group_by branches.

Baseline must hold (≥ 385 passed).
