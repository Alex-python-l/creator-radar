"""Cost audit endpoints.

Serves the local mirror of GeeLark's billing transactions plus
attribution to the business task that triggered each charge. Used by the
`/costs` page in the dashboard.

GeeLark currency is USD; values are returned as floats with up to four
decimal places (most charges are 3 cents or so).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.db import session_scope
from app.repositories import transactions as tx_repo

router = APIRouter(prefix="/api/costs")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    # Accept either "YYYY-MM-DD" or full ISO; treat date-only as UTC midnight.
    if len(value) == 10:
        return datetime.fromisoformat(value)
    # strip trailing Z for ISO inputs
    return datetime.fromisoformat(value.rstrip("Z"))


@router.get("/transactions")
def list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    account_id: Optional[int] = None,
    group_id: Optional[int] = None,
    kind: Optional[str] = Query(None, pattern="^(publish|warmup|engagement|idle)$"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    with session_scope() as s:
        items, total = tx_repo.list_with_attribution(
            s,
            page=page,
            page_size=page_size,
            account_id=account_id,
            group_id=group_id,
            kind=kind,
            date_from=_parse_dt(date_from),
            date_to=_parse_dt(date_to),
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/summary")
def summary(
    group_by: str = Query("day", pattern="^(day|account|group|kind)$"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    with session_scope() as s:
        rows = tx_repo.summary(
            s,
            group_by=group_by,
            date_from=_parse_dt(date_from),
            date_to=_parse_dt(date_to),
        )
    return {"groups": rows}


@router.get("/kpis")
def kpis():
    with session_scope() as s:
        return tx_repo.kpis(s, now=datetime.utcnow())


@router.get("/export.csv")
def export_csv(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[int] = None,
):
    df = _parse_dt(date_from)
    dt = _parse_dt(date_to)

    def _stream():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([
            "time_utc", "env_id", "profile_name", "group",
            "attribution_kind", "attribution_label",
            "used_min", "amount_usd",
        ])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        with session_scope() as s:
            for row in tx_repo.iter_for_export(
                s, date_from=df, date_to=dt, account_id=account_id,
            ):
                w.writerow([
                    row["time"],
                    row["env_id"],
                    (row["account"] or {}).get("profile_name") or "",
                    (row["group"] or {}).get("name") or "",
                    row["attribution"]["kind"],
                    row["attribution"]["label"],
                    row["used_min"],
                    f"{row['amount_usd']:.4f}",
                ])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

    headers = {
        "Content-Disposition": 'attachment; filename="geelark-costs.csv"',
    }
    return StreamingResponse(_stream(), media_type="text/csv", headers=headers)
