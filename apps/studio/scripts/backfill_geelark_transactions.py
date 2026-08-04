"""Pull the current GeeLark transactions snapshot into the mirror.

GeeLark's `/billing/transaction/detail` endpoint has a HARD limit
(verified 2026-06-09): `page` and `pageSize` parameters are ignored and
each call returns the newest ~100 transactions only. `total` reports the
real backend count (≫ 100) but those older rows are not reachable via
this API. Therefore this script — and the periodic `sync_transactions`
cron — can only mirror the trailing window of activity. From whenever
you first deploy this code forward, every new transaction will land in
the mirror; anything older than ~the last 100 charges at deploy time is
permanently lost to the audit.

Idempotent. Re-running just re-confirms the latest 100 (all conflict).

Usage:
    uv run python scripts/backfill_geelark_transactions.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.db import session_scope
from app.repositories import transactions as tx_repo
from app.services.geelark_service import make_client


def main() -> int:
    client = make_client()
    started = time.monotonic()
    data = client.list_billing_transactions(limit=1000)
    rows = data.get("list") or []
    total_backend = data.get("total")

    with session_scope() as session:
        conflicts = tx_repo.upsert_many(session, rows)
    new_in_page = len(rows) - conflicts

    elapsed = time.monotonic() - started
    print(
        f"sync done: GeeLark reports total={total_backend} rows; "
        f"API exposed {len(rows)} (the latest up to 1000, no pagination "
        f"available); inserted={new_in_page} conflicts={conflicts} "
        f"elapsed={elapsed:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
