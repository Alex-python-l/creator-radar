"""Repository for the business-side task_runs state machine.

Phase 2 of the 2026-06-13 phone-runtime-architecture spec. Each row
represents one business attempt (publish a particular publication,
warm an account, run an engagement) and tracks where in the pipeline
it currently sits. Paired 1:1 with a phone_sessions row in the common
path; separate so the two lifecycles can diverge under failure.

Phase 2 writes but doesn't yet read in production code paths — the
table is observed first so the state-machine values can be sanity-
checked before downstream code depends on them.

State vocabulary (declared in app/services/phone_runtime.py):
    PLANNED → PHONE_STARTING → SUBMITTED_TO_GEELARK → RUNNING
                                                    ↘ SUCCESS / FAILURE
                                                      TIMEOUT / CANCELLED
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import Session


_COLUMNS = {
    "kind", "ref_type", "ref_id", "account_id", "phone_session_id",
    "geelark_task_id", "state", "attempt", "planned_at", "started_at",
    "finished_at", "deadline_at", "failure_reason",
}


def _row(r) -> dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind,
        "ref_type": r.ref_type,
        "ref_id": r.ref_id,
        "account_id": r.account_id,
        "phone_session_id": r.phone_session_id,
        "geelark_task_id": r.geelark_task_id,
        "state": r.state,
        "attempt": r.attempt,
        "planned_at": r.planned_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "deadline_at": r.deadline_at,
        "failure_reason": r.failure_reason,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def create(session: Session, **fields) -> int:
    payload = {k: v for k, v in fields.items() if k in _COLUMNS}
    required = {
        "kind", "ref_type", "ref_id", "account_id", "state", "planned_at",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(
            f"task_runs.create missing required: {sorted(missing)}"
        )
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(f":{k}" for k in payload)
    row = session.execute(
        text(
            f"INSERT INTO task_runs ({columns}) "
            f"VALUES ({placeholders}) RETURNING id"
        ),
        payload,
    ).first()
    return int(row.id)


def get(session: Session, run_id: int) -> Optional[dict[str, Any]]:
    r = session.execute(
        text("SELECT * FROM task_runs WHERE id = :id"),
        {"id": run_id},
    ).first()
    return _row(r) if r else None


def get_by_phone_session(
        session: Session, phone_session_id: int) -> Optional[dict[str, Any]]:
    r = session.execute(
        text(
            "SELECT * FROM task_runs "
            "WHERE phone_session_id = :psid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"psid": phone_session_id},
    ).first()
    return _row(r) if r else None


def get_by_geelark_task(
        session: Session, geelark_task_id: str) -> Optional[dict[str, Any]]:
    r = session.execute(
        text(
            "SELECT * FROM task_runs WHERE geelark_task_id = :tid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"tid": geelark_task_id},
    ).first()
    return _row(r) if r else None


def update(session: Session, run_id: int, **fields) -> None:
    """Patch arbitrary writeable columns on a task_run.

    Always bumps updated_at. Caller owns the commit.
    """
    payload = {k: v for k, v in fields.items() if k in _COLUMNS}
    if not payload:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in payload)
    payload["id"] = run_id
    session.execute(
        text(
            f"UPDATE task_runs SET {assignments}, "
            f"updated_at = CURRENT_TIMESTAMP WHERE id = :id"
        ),
        payload,
    )


def list_for_ref(
        session: Session, ref_type: str, ref_id: int,
        *, limit: int = 20) -> list[dict[str, Any]]:
    """Audit lookup: all task_runs (across attempts) for one business
    referent, newest first."""
    rows = session.execute(
        text(
            "SELECT * FROM task_runs "
            "WHERE ref_type = :rt AND ref_id = :ri "
            "ORDER BY id DESC LIMIT :lim"
        ),
        {"rt": ref_type, "ri": ref_id, "lim": limit},
    ).all()
    return [_row(r) for r in rows]
