"""Phone Runtime Manager — cloud-phone-runtime orchestrator.

The single allowed entry point for starting / stopping / finalizing a
cloud-phone session. Wraps lease bookkeeping + phone_sessions ledger
into one transactional API.

Invariants:
- phone_sessions is the single source of truth for "is this account's
  phone busy". The legacy accounts.phone_busy_until column has been
  retired.
- One in-flight phone_session per account, backstopped by the partial
  unique index idx_phone_sessions_one_active.
- Renewing, finalizing, and timing out all happen in one DB
  transaction so observers never see an intermediate state.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.repositories import phone_sessions as ps_repo
from app.repositories import task_runs as tr_repo


# Status vocabulary — kept here as the single source of truth so the
# repo layer stays value-agnostic.
STATUS_ACTIVE = "ACTIVE"
STATUS_STOPPING = "STOPPING"
STATUS_STOPPED = "STOPPED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_STOP_VERIFY_FAILED = "STOP_VERIFY_FAILED"

FINAL_STATUS_STOPPED = "STOPPED"
FINAL_STATUS_TIMEOUT = "TIMEOUT"
FINAL_STATUS_FAILED = "FAILED"

# task_runs state vocabulary. PLANNED is reserved for future
# deferral-without-phone-acquire; today we jump straight to PHONE_STARTING
# because start_session is what kicks the flow off.
TASK_STATE_PLANNED = "PLANNED"
TASK_STATE_PHONE_STARTING = "PHONE_STARTING"
# Publish-only step between PHONE_STARTING and SUBMITTED_TO_GEELARK: the
# resource upload to GeeLark is the slowest single operation and worth
# surfacing on the ops dashboard separately.
TASK_STATE_UPLOADING = "UPLOADING_RESOURCE"
# Engagement-only step between PHONE_STARTING and SUBMITTED_TO_GEELARK:
# the comment text (studio_ai) or the Facebook candidates (studio_ai or
# provided) get prepared right before the GeeLark task is created. For
# studio_ai this fires Gemini, which can be slow or hang.
TASK_STATE_PREPARING_COMMENT = "PREPARING_COMMENT"
TASK_STATE_SUBMITTED = "SUBMITTED_TO_GEELARK"
TASK_STATE_RUNNING = "RUNNING"
# Between RUNNING and SUCCESS/FAILURE the adapter does platform-specific
# verification (YouTube audit screenshot, future TikTok shareLink
# confirmation). Surface it so a stuck VERIFYING saga is distinguishable
# from a stuck RUNNING one.
TASK_STATE_VERIFYING = "VERIFYING"
TASK_STATE_SUCCESS = "SUCCESS"
TASK_STATE_FAILURE = "FAILURE"
TASK_STATE_TIMEOUT = "TIMEOUT"
TASK_STATE_CANCELLED = "CANCELLED"

# Default budget (in seconds) per source. Override per-call when a
# service has a tighter / looser ceiling. Defaults match the "建议默认
# 预算" table in the 2026-06-13 spec.
DEFAULT_BUDGET_SECONDS = {
    "warmup":             10 * 60,   # 8-12 min envelope
    "publish":            15 * 60,
    "engagement":         12 * 60,   # 10-15 min envelope
    "manual":             30 * 60,
    "external_detected":   5 * 60,
}


class PhoneBusy(Exception):
    """Raised when start_session can't grab the account's lease."""

    def __init__(self, account_id: int):
        super().__init__(f"phone busy for account {account_id}")
        self.account_id = account_id


def start_session(
    db: Session, *,
    account_id: int,
    env_id: str,
    source: str,
    owner_type: Optional[str] = None,
    owner_id: Optional[int] = None,
    budget_seconds: Optional[int] = None,
    created_by: str = "system",
    # Phase 2: optional task_run wiring. When kind is supplied a paired
    # task_run is created with state=PHONE_STARTING. Defaults from
    # source/owner if omitted so the common warmup/publish/engagement
    # case can pass just `kind=`.
    kind: Optional[str] = None,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
) -> int:
    """Atomically acquire the account's phone lease AND create the
    matching phone_session row (Phase 1) plus an optional task_run
    pairing row (Phase 2).

    Raises PhoneBusy if either the account-level lease CAS or the
    partial-unique-index INSERT fails — the two checks are redundant
    on purpose so a stale lease doesn't slip past while the session
    table is the source of truth, and vice versa.

    Returns the new phone_session id (task_run id is retrievable via
    tr_repo.get_by_phone_session).
    """
    if budget_seconds is None:
        budget_seconds = DEFAULT_BUDGET_SECONDS.get(source)
        if budget_seconds is None:
            raise ValueError(
                f"phone_runtime.start_session: source={source!r} has no "
                f"default budget — pass budget_seconds explicitly"
            )

    now = datetime.utcnow()
    deadline = now + timedelta(seconds=int(budget_seconds))

    # Single source of truth: the partial unique index
    # `idx_phone_sessions_one_active` on (account_id) WHERE
    # stopped_at IS NULL — concurrent callers race on the INSERT and
    # the loser sees an IntegrityError.
    try:
        sid = ps_repo.create(
            db,
            account_id=account_id,
            env_id=env_id,
            source=source,
            owner_type=owner_type,
            owner_id=owner_id,
            started_at=now,
            budget_deadline_at=deadline,
            status=STATUS_ACTIVE,
            created_by=created_by,
        )
    except IntegrityError:
        db.rollback()
        raise PhoneBusy(account_id)

    # Step 3 (Phase 2): if the caller named the business kind, stamp a
    # task_run alongside. Same transaction so the two rows commit or
    # roll back together.
    if kind is not None:
        effective_ref_type = ref_type or owner_type
        effective_ref_id = ref_id if ref_id is not None else owner_id
        if effective_ref_type is None or effective_ref_id is None:
            db.rollback()
            raise ValueError(
                "phone_runtime.start_session: kind requires "
                "ref_type/ref_id (or owner_type/owner_id as fallback)"
            )
        tr_repo.create(
            db,
            kind=kind,
            ref_type=effective_ref_type,
            ref_id=effective_ref_id,
            account_id=account_id,
            phone_session_id=sid,
            state=TASK_STATE_PHONE_STARTING,
            attempt=1,
            planned_at=now,
            started_at=now,
            deadline_at=deadline,
        )

    db.commit()
    return sid


def attach_geelark_task(
        db: Session, session_id: int, geelark_task_id: str) -> None:
    """Record the GeeLark RPA task id created for this session and
    transition the paired task_run to SUBMITTED_TO_GEELARK."""
    ps_repo.update(db, session_id, geelark_task_id=geelark_task_id)
    tr = tr_repo.get_by_phone_session(db, session_id)
    if tr is not None:
        tr_repo.update(
            db, tr["id"],
            geelark_task_id=geelark_task_id,
            state=TASK_STATE_SUBMITTED,
        )
    db.commit()


def advance_task_run_state(
        db: Session, session_id: int, new_state: str) -> None:
    """Mid-flight saga step transition: update the task_run paired with
    `session_id` to `new_state` without touching phone_session.

    Called between start_session (PHONE_STARTING) and the terminal
    finalize_* path so saga observability sees UPLOADING_RESOURCE
    and VERIFYING separately from RUNNING. No-op when there is no
    task_run row (a session created with `kind=None` skips task_run
    creation by design — manual holds and external_detected fall
    in this bucket)."""
    tr = tr_repo.get_by_phone_session(db, session_id)
    if tr is None:
        return
    tr_repo.update(db, tr["id"], state=new_state)
    db.commit()


def renew_budget(
        db: Session, session_id: int, *, budget_seconds: int) -> None:
    """Extend an in-flight session's budget deadline."""
    row = ps_repo.get(db, session_id)
    if row is None:
        raise ValueError(f"phone_session {session_id} not found")
    if row["stopped_at"] is not None:
        raise ValueError(
            f"phone_session {session_id} already stopped — cannot renew"
        )
    now = datetime.utcnow()
    new_deadline = now + timedelta(seconds=int(budget_seconds))
    # CAS: the pre-read above is advisory only — a finalizer may commit
    # between it and this write, and a renewed deadline on a closed
    # session would resurrect it in overdue scans.
    if not ps_repo.update_if_active(
            db, session_id, budget_deadline_at=new_deadline):
        db.rollback()
        raise ValueError(
            f"phone_session {session_id} already stopped — cannot renew"
        )
    db.commit()


def finalize_success(
    db: Session, session_id: int, *,
    observed_status: Optional[str] = None,
) -> None:
    """GeeLark reported terminal SUCCESS (status=3). Close out the
    session as STOPPED + release the account lease, in one
    transaction."""
    _finalize(
        db, session_id,
        status=STATUS_STOPPED,
        final_status=FINAL_STATUS_STOPPED,
        stop_reason=None,
        observed_status=observed_status,
    )


def finalize_failure(
    db: Session, session_id: int, *,
    reason: str,
    observed_status: Optional[str] = None,
) -> None:
    """GeeLark reported terminal FAILURE (status=4 or 7) or our code
    decided the task failed (e.g. task purged). Close out the session
    as STOPPED with final_status=FAILED."""
    _finalize(
        db, session_id,
        status=STATUS_STOPPED,
        final_status=FINAL_STATUS_FAILED,
        stop_reason=reason,
        observed_status=observed_status,
    )


def mark_timeout(
    db: Session, session_id: int, *,
    reason: str,
    observed_status: Optional[str] = None,
) -> None:
    """Budget hit before GeeLark went terminal. Mark the session
    TIMEOUT + release the lease. Caller is responsible for the actual
    stop_phones call (and Phase 2: cancel_task) afterwards."""
    _finalize(
        db, session_id,
        status=STATUS_TIMEOUT,
        final_status=FINAL_STATUS_TIMEOUT,
        stop_reason=reason,
        observed_status=observed_status,
    )


_TASK_STATE_FOR_FINAL_STATUS = {
    FINAL_STATUS_STOPPED: TASK_STATE_SUCCESS,
    FINAL_STATUS_TIMEOUT: TASK_STATE_TIMEOUT,
    FINAL_STATUS_FAILED: TASK_STATE_FAILURE,
}


def _finalize(
    db: Session, session_id: int, *,
    status: str,
    final_status: str,
    stop_reason: Optional[str],
    observed_status: Optional[str],
) -> None:
    """Common close-out: update the session row + paired task_run
    (Phase 2) + release the account-level lease, in one transaction.

    Concurrent finalizers race here — the poll chain, the callback
    fast-path, and the overdue watchdog can all decide to close the
    same session. The CAS on stopped_at picks exactly one winner; the
    losers return without touching the row, so a SUCCESS recorded by
    the poll chain can't be overwritten into TIMEOUT by a slower
    watchdog (or vice versa)."""
    row = ps_repo.get(db, session_id)
    if row is None:
        return
    now = datetime.utcnow()
    won = ps_repo.update_if_active(
        db, session_id,
        status=status,
        final_status=final_status,
        stopped_at=now,
        stop_requested_at=row["stop_requested_at"] or now,
        stop_reason=stop_reason,
        last_observed_status=observed_status,
    )
    if not won:
        db.rollback()
        return
    tr = tr_repo.get_by_phone_session(db, session_id)
    if tr is not None and tr["finished_at"] is None:
        tr_repo.update(
            db, tr["id"],
            state=_TASK_STATE_FOR_FINAL_STATUS[final_status],
            finished_at=now,
            failure_reason=(
                stop_reason
                if final_status != FINAL_STATUS_STOPPED else None
            ),
        )
    db.commit()


def get_active_for_account(
    db: Session, account_id: int,
) -> Optional[dict[str, Any]]:
    """Return the in-flight session for this account, or None."""
    return ps_repo.get_active_for_account(db, account_id)


def overdue_sessions(
    db: Session, *,
    now: Optional[datetime] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List in-flight sessions past their budget deadline. The
    watchdog (Phase 1 Step 4) uses this to trigger mark_timeout."""
    return ps_repo.list_overdue(db, now=now, limit=limit)
