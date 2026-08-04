"""Repository for the v1 cloud-phone-runtime ledger.

A `phone_sessions` row represents one billable cloud-phone runtime,
from start to stop. Phase 1 Step 1 only ships these CRUD primitives —
the PhoneRuntimeManager wires them together; nothing else in
production reads or writes here yet.

Conventions:
- Row dicts are returned from helpers (not SQLModel instances) to
  match the rest of the repo layer; rebinding-to-session pitfalls
  inside callers are then a non-issue.
- All datetime values are naive UTC (datetime.utcnow()), matching
  every other timestamp column in this schema.
- Status / final_status string vocabularies are defined in
  app/services/phone_runtime.py — repo layer is value-agnostic.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import Session


_COLUMNS = {
    "account_id", "env_id", "source", "owner_type", "owner_id",
    "geelark_task_id", "started_at", "budget_deadline_at",
    "stop_requested_at", "stopped_at", "status", "final_status",
    "stop_reason", "last_observed_status", "cost_transaction_id",
    "created_by",
}


def _row(r) -> dict[str, Any]:
    return {
        "id": r.id,
        "account_id": r.account_id,
        "env_id": r.env_id,
        "source": r.source,
        "owner_type": r.owner_type,
        "owner_id": r.owner_id,
        "geelark_task_id": r.geelark_task_id,
        "started_at": r.started_at,
        "budget_deadline_at": r.budget_deadline_at,
        "stop_requested_at": r.stop_requested_at,
        "stopped_at": r.stopped_at,
        "status": r.status,
        "final_status": r.final_status,
        "stop_reason": r.stop_reason,
        "last_observed_status": r.last_observed_status,
        "cost_transaction_id": r.cost_transaction_id,
        "created_by": r.created_by,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def create(session: Session, **fields) -> int:
    """Insert a new phone_session row, returning its id.

    The partial unique index `idx_phone_sessions_one_active` enforces
    "one in-flight session per account_id". A concurrent caller that
    races will get an IntegrityError — PhoneRuntimeManager catches
    that and translates it to PhoneBusy.
    """
    payload = {k: v for k, v in fields.items() if k in _COLUMNS}
    required = {
        "account_id", "env_id", "source", "started_at",
        "budget_deadline_at", "status", "created_by",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(
            f"phone_sessions.create missing required: {sorted(missing)}"
        )
    columns = ", ".join(payload.keys())
    placeholders = ", ".join(f":{k}" for k in payload)
    row = session.execute(
        text(
            f"INSERT INTO phone_sessions ({columns}) "
            f"VALUES ({placeholders}) RETURNING id"
        ),
        payload,
    ).first()
    return int(row.id)


def get(session: Session, session_id: int) -> Optional[dict[str, Any]]:
    r = session.execute(
        text("SELECT * FROM phone_sessions WHERE id = :id"),
        {"id": session_id},
    ).first()
    return _row(r) if r else None


def get_active_for_account(
        session: Session, account_id: int) -> Optional[dict[str, Any]]:
    """Find the in-flight session for an account (stopped_at IS NULL).

    Bound by the partial unique index, so this returns at most one row.
    Returns None if the account currently has no active session.
    """
    r = session.execute(
        text(
            "SELECT * FROM phone_sessions "
            "WHERE account_id = :aid AND stopped_at IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"aid": account_id},
    ).first()
    return _row(r) if r else None


def get_by_geelark_task(
        session: Session, geelark_task_id: str) -> Optional[dict[str, Any]]:
    r = session.execute(
        text(
            "SELECT * FROM phone_sessions "
            "WHERE geelark_task_id = :tid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"tid": geelark_task_id},
    ).first()
    return _row(r) if r else None


def update(session: Session, session_id: int, **fields) -> None:
    """Patch arbitrary writeable columns on a phone_session.

    Always bumps updated_at. Caller is responsible for committing.
    """
    payload = {k: v for k, v in fields.items() if k in _COLUMNS}
    if not payload:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in payload)
    payload["id"] = session_id
    session.execute(
        text(
            f"UPDATE phone_sessions "
            f"SET {assignments}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id = :id"
        ),
        payload,
    )


def update_if_active(session: Session, session_id: int, **fields) -> bool:
    """Compare-and-swap variant of `update`: only touches the row while
    it is still in flight (stopped_at IS NULL). Returns True when this
    call won the write, False when the session was already finalized by
    a concurrent path (poll chain vs watchdog vs callback fast-path all
    race to close the same session).

    Caller is responsible for committing.
    """
    payload = {k: v for k, v in fields.items() if k in _COLUMNS}
    if not payload:
        return False
    assignments = ", ".join(f"{k} = :{k}" for k in payload)
    payload["id"] = session_id
    result = session.execute(
        text(
            f"UPDATE phone_sessions "
            f"SET {assignments}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id = :id AND stopped_at IS NULL"
        ),
        payload,
    )
    return result.rowcount > 0


def list_overdue(
        session: Session, *, now: Optional[datetime] = None,
        limit: int = 100) -> list[dict[str, Any]]:
    """In-flight sessions past their budget deadline.

    Used by the future overdue-session watchdog to trigger the timeout
    flow. Phase 1 Step 1 doesn't run the watchdog yet — included here so
    the watchdog ships as a single self-contained PR in a later step.
    """
    now = now or datetime.utcnow()
    rows = session.execute(
        text(
            "SELECT * FROM phone_sessions "
            "WHERE stopped_at IS NULL "
            "  AND budget_deadline_at < :now "
            "ORDER BY budget_deadline_at ASC "
            "LIMIT :lim"
        ),
        {"now": now, "lim": limit},
    ).all()
    return [_row(r) for r in rows]


def attach_cost_transaction(session: Session, session_id: int,
                            transaction_id: str) -> None:
    """Link a phone_session to the GeeLark billing row it paid for.

    Called by Phase 3 cost_attributions reconciliation; included here
    because it's a single-column write that doesn't deserve its own
    repo module.
    """
    session.execute(
        text(
            "UPDATE phone_sessions "
            "SET cost_transaction_id = :tid, "
            "    updated_at = CURRENT_TIMESTAMP "
            "WHERE id = :id"
        ),
        {"tid": transaction_id, "id": session_id},
    )


def default_budget_deadline(now: datetime, seconds: int) -> datetime:
    """Compute budget_deadline_at = now + seconds.

    Centralised so callers don't accidentally drift across modules
    (e.g. one using naive utcnow and another using tz-aware now)."""
    return now + timedelta(seconds=int(seconds))
