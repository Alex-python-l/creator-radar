"""Liveness/readiness endpoint for production monitoring.

GET /healthz returns 200 with a JSON body summarizing DB + worker
state. Returns 503 if the DB itself is unreachable.

Unauthenticated by design — must be hittable by external monitors.
"""

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlmodel import Session

from app.db import get_system_session

router = APIRouter()


@router.get("/healthz")
def healthz(
    response: Response,
    session: Session = Depends(get_system_session),
) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True}

    # DB ping
    try:
        session.exec(text("SELECT 1")).first()
        payload["db"] = "ok"
    except Exception as e:
        payload["ok"] = False
        payload["db"] = f"fail: {type(e).__name__}"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return payload

    # Most recent procrastinate worker heartbeat
    try:
        row = session.exec(text(
            "SELECT EXTRACT(EPOCH FROM MAX(last_heartbeat)) "
            "FROM procrastinate_workers"
        )).first()
        ts = row[0] if row else None
        payload["worker_last_seen"] = float(ts) if ts is not None else None
    except Exception:
        # If procrastinate tables aren't there yet (e.g. mkt_test fresh), skip
        payload["worker_last_seen"] = None

    return payload
