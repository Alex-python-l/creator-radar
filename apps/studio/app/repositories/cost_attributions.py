"""Repository for the persisted GeeLark-transaction → task attribution.

Phase 3 of the 2026-06-13 phone-runtime-architecture spec. Each row
fixes the answer to "which task/session paid for this transaction" so
the Costs page can render without re-running the attribution CTE on
every page-load and so the audit trail survives schema drift in the
underlying business tables (publications, engagement_runs, accounts).

Usage:
  - upsert(session, transaction_id, **fields) — write/replace.
  - get(session, transaction_id) — fetch by id, or None.
  - summary_by_kind(session, date_from, date_to) — group/sum the
    persisted rows for the Costs overview pie chart.

Phase 3 sync writes rows for every NEW transaction the
sync_transactions task pulls. Lazy upsert handles backfill for the
trailing window of historical transactions on first read.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import Session


_COLUMNS = {
    "phone_session_id", "task_run_id", "kind", "confidence", "reason",
}


def _row(r) -> dict[str, Any]:
    return {
        "transaction_id": r.transaction_id,
        "phone_session_id": r.phone_session_id,
        "task_run_id": r.task_run_id,
        "kind": r.kind,
        "confidence": r.confidence,
        "reason": r.reason,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def upsert(session: Session, transaction_id: str, **fields) -> None:
    """Write or update the attribution row for one transaction.

    Required fields: kind, confidence, reason. Optional: phone_session_id,
    task_run_id. Caller owns the commit.
    """
    payload = {k: v for k, v in fields.items() if k in _COLUMNS}
    required = {"kind", "confidence", "reason"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(
            f"cost_attributions.upsert missing required: {sorted(missing)}"
        )
    payload["transaction_id"] = transaction_id
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(f":{k}" for k in payload)
    updates = ", ".join(
        f"{k} = EXCLUDED.{k}" for k in payload if k != "transaction_id"
    )
    session.execute(
        text(
            f"INSERT INTO cost_attributions ({columns}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (workspace_id, transaction_id) DO UPDATE "
            f"SET {updates}, updated_at = CURRENT_TIMESTAMP"
        ),
        payload,
    )


def get(session: Session, transaction_id: str) -> Optional[dict[str, Any]]:
    r = session.execute(
        text(
            "SELECT * FROM cost_attributions "
            "WHERE transaction_id = :tid"
        ),
        {"tid": transaction_id},
    ).first()
    return _row(r) if r else None


def summary_by_kind(
    session: Session, *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Sum amount by kind from the persisted attributions.

    JOINs cost_attributions to geelark_transactions for the amount /
    time range filter. Used by the Costs overview pie chart and the
    /api/costs/summary?group_by=kind variant.
    """
    rows = session.execute(
        text(
            "SELECT ca.kind AS k, "
            "       count(*) AS n, "
            "       sum(t.amount) AS total "
            "FROM cost_attributions ca "
            "JOIN geelark_transactions t ON t.id = ca.transaction_id "
            "WHERE (CAST(:date_from AS timestamp) IS NULL "
            "       OR t.geelark_created_at >= CAST(:date_from AS timestamp)) "
            "  AND (CAST(:date_to AS timestamp) IS NULL "
            "       OR t.geelark_created_at <  CAST(:date_to AS timestamp)) "
            "GROUP BY ca.kind ORDER BY total ASC"
        ),
        {"date_from": date_from, "date_to": date_to},
    ).all()
    return [
        {"key": r.k, "label": r.k,
         "count": int(r.n), "total": float(r.total or 0)}
        for r in rows
    ]
