# GeeLark Cost Audit — Implementation Plan

**Spec:** [docs/superpowers/specs/2026-06-09-geelark-cost-audit-design.md](../specs/2026-06-09-geelark-cost-audit-design.md)

**Goal:** Mirror GeeLark's `/billing/transaction/detail` into PostgreSQL, attribute each transaction to a business task (publish / warmup / engagement / idle), expose list / summary / CSV-export endpoints, and ship a `/costs` page.

## Task 1: Alembic migration `geelark_transactions`

- Confirm single head: `uv run alembic heads`. If multiple, merge first.
- Add `alembic/versions/<rev>_geelark_transactions.py`:
  - Table columns per spec.
  - Indexes: `(env_id, geelark_created_at desc)`, `(geelark_created_at desc)`.
- `uv run alembic upgrade head` against local + `mkt_test`.

## Task 2: SQLModel + GeeLarkClient method

- `app/models.py`: `class GeelarkTransaction(SQLModel, table=True)`.
- `geelark_client.py`: `list_billing_transactions(page=1, page_size=100)` → returns `{"total": int, "list": [...]}` raw.

## Task 3: Repository

- `app/repositories/transactions.py`:
  - `upsert_many(session, rows) -> int` (returns the number of conflicting rows; sync loop stops when `>0`).
  - `list_with_attribution(...)` with the lateral / CTE join described in spec.
  - `summary(group_by, from, to)` for KPIs and aggregations.
  - `kpis()` convenience wrapper.

## Task 4: Sync task + backfill script

- `app/tasks/sync_transactions.py` — `@procrastinate_app.periodic(cron="*/15 * * * *")` + task body.
- Register in `app/tasks/__init__.py` (or wherever others are).
- `scripts/backfill_geelark_transactions.py` — same logic but no stop-on-existing (paginate until empty); reusable.

## Task 5: API

- `app/api/costs.py` — four endpoints per spec.
- Wire into `app/main.py` with `require_auth`.

## Task 6: Frontend

- `frontend/src/pages/Costs.tsx`.
- Sidebar entry in `frontend/src/components/` (find existing sidebar component).
- Reuse existing `ui.tsx` primitives + `PlatformIcon`.
- Table + filters + KPI cards + CSV export button.

## Task 7: Tests

- `tests/test_geelark_client.py` extension.
- `tests/test_repositories_transactions.py`.
- `tests/test_tasks_sync_transactions.py`.
- `tests/test_api_costs.py`.
- Confirm `uv run pytest -q` ≥ baseline passes.

## Task 8: Verify in browser

- Run worker + web tier, open `/costs`, run backfill, confirm rows render, kinds attribute correctly on real data, CSV downloads.

## Task 9: Commit + deploy

- Stage explicit paths, one commit per logical unit, never `-A`.
- Follow `docs/production-deploy.md`. Restart both `mkt` and `mkt-worker`.
- SSH + run backfill script once on prod after deploy.
