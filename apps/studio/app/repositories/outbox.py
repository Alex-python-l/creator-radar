"""Outbox repository — transactional event dispatch primitives.

The outbox table holds one row per (business entity, intent) that
needs a worker task deferred. Producers (API handlers, periodic
scanners) call `enqueue` inside their business transaction; the
dispatcher (app/tasks/outbox_dispatcher.py) drains pending rows by
calling defer_async on the matching Procrastinate task.

Idempotency: the partial unique index
`uq_outbox_inflight_business_ref` rejects a second enqueue for the
same (intent, business_ref) while the previous row is still pending.
Repeated handler invocations safely no-op.

Backoff: each failed dispatch increments `attempts` and pushes
`next_attempt_at` further out. The schedule is conservative — a
permanently broken intent stops re-trying within an hour, freeing
worker slots for healthy work.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import Session


# Backoff steps in seconds, indexed by attempt count. attempts=0 (the
# enqueue moment) is already at zero delay; the i-th failure picks
# steps[min(i, len-1)] so we cap at the last value forever.
_BACKOFF_STEPS_SECONDS = (
    30,        # 1st failure → retry in 30s
    2 * 60,    # 2nd          → 2 min
    10 * 60,   # 3rd          → 10 min
    60 * 60,   # 4th+         → 1 hour
)


def _next_attempt_at(attempts_after: int) -> datetime:
    """attempts_after is the new value of `attempts` after the failure.
    Index into the backoff table accordingly."""
    idx = min(attempts_after - 1, len(_BACKOFF_STEPS_SECONDS) - 1)
    idx = max(idx, 0)
    return datetime.utcnow() + timedelta(
        seconds=_BACKOFF_STEPS_SECONDS[idx]
    )


def enqueue(
        session: Session, *,
        intent: str,
        business_ref: str,
        payload: dict[str, Any],
) -> Optional[int]:
    """Insert one outbox row for (intent, business_ref). Returns the
    new id, or None when a pending row for the same key already
    exists (the inflight UNIQUE rejected the insert).

    Must be called inside the producer's business transaction so the
    outbox write commits or rolls back together with the business
    write. The caller is responsible for the commit.

    `created_at` and `next_attempt_at` are set with Python's
    `datetime.utcnow()` rather than PG's `now()` because the prod
    server runs in Asia/Shanghai while the dispatcher compares against
    `utcnow()`; mixing the two would shift `next_attempt_at` 8 hours
    into the future and the dispatcher would never pick fresh rows up.
    """
    now = datetime.utcnow()
    row = session.execute(
        text(
            "INSERT INTO outbox "
            "(intent, business_ref, payload, "
            " created_at, next_attempt_at) "
            "VALUES (:intent, :ref, CAST(:payload AS jsonb), "
            "        :now, :now) "
            "ON CONFLICT (workspace_id, intent, business_ref) "
            "  WHERE sent_at IS NULL "
            "  DO NOTHING "
            "RETURNING id"
        ),
        {
            "intent": intent,
            "ref": business_ref,
            "payload": json.dumps(payload),
            "now": now,
        },
    ).first()
    return row[0] if row is not None else None


def mark_sent(session: Session, outbox_id: int) -> None:
    """Mark a row as successfully dispatched. Idempotent: a row whose
    sent_at is already set just stays put."""
    session.execute(
        text(
            "UPDATE outbox SET sent_at = :now "
            "WHERE id = :id AND sent_at IS NULL"
        ),
        {"id": outbox_id, "now": datetime.utcnow()},
    )
    session.commit()


def record_failure(
        session: Session, outbox_id: int, error: str) -> None:
    """Increment attempts, push next_attempt_at out by the next backoff
    step, and store the failure message for ops triage."""
    session.execute(
        text(
            "UPDATE outbox "
            "SET attempts = attempts + 1, "
            "    next_attempt_at = :next_at, "
            "    last_error = :err "
            "WHERE id = :id AND sent_at IS NULL"
        ),
        {
            "id": outbox_id,
            "err": (error or "")[:1024],
            # We don't know the new attempts count yet, so look it up.
            # Two-step is fine: row contention is negligible here.
            "next_at": _next_attempt_at_after_failure(
                session, outbox_id),
        },
    )
    session.commit()


def _next_attempt_at_after_failure(
        session: Session, outbox_id: int) -> datetime:
    row = session.execute(
        text("SELECT attempts FROM outbox WHERE id = :id"),
        {"id": outbox_id},
    ).first()
    next_attempts = (row[0] if row is not None else 0) + 1
    return _next_attempt_at(next_attempts)


def list_pending(
        session: Session, *,
        limit: int = 100,
        now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Outbox rows ready for dispatch: sent_at NULL AND
    next_attempt_at <= now. Oldest first to give old rows a fair shot
    at being drained."""
    eff_now = now or datetime.utcnow()
    rows = session.execute(
        text(
            "SELECT id, intent, business_ref, payload, "
            "       attempts, next_attempt_at, last_error "
            "FROM outbox "
            "WHERE sent_at IS NULL AND next_attempt_at <= :now "
            "ORDER BY next_attempt_at, id "
            "LIMIT :limit"
        ),
        {"now": eff_now, "limit": limit},
    ).fetchall()
    return [
        {
            "id": r.id,
            "intent": r.intent,
            "business_ref": r.business_ref,
            "payload": (
                json.loads(r.payload) if isinstance(r.payload, str)
                else r.payload
            ),
            "attempts": r.attempts,
            "next_attempt_at": r.next_attempt_at,
            "last_error": r.last_error,
        }
        for r in rows
    ]
