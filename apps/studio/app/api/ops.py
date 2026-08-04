"""Operations API — surface in-flight phone_sessions and sagas for
hands-on triage.

Lets an operator answer "what's running right now and why" without
dropping into psql, and gives them a button to close out an
external_detected or wedged session manually.

Endpoints:
  GET  /api/ops/sessions               list in-flight phone_sessions
  POST /api/ops/sessions/{id}/release  finalize a session (operator override)
  GET  /api/ops/sagas/in-flight        list in-flight sagas (task_runs)
                                        with deadline + stuck detection
  GET  /api/ops/sagas/summary          per-kind in-flight count + stuck count
  GET  /api/ops/sagas/{kind}/{ref}     describe one saga by business_ref
  GET  /api/ops/rpa-watchdog/*         inspect the standalone RPA watchdog
  POST /api/ops/rpa-watchdog/start     create a guarded GeeLark RPA
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import Session

from app.db import session_scope
from app.services import phone_runtime
from app.services import rpa_watchdog_client
from app.services import sagas as saga_views


router = APIRouter()


class StartWatchdogRpaIn(BaseModel):
    deviceId: str = Field(min_length=1, max_length=128)
    flowId: str = Field(min_length=1, max_length=128)
    scheduleAt: int | None = None
    paramMap: dict[str, Any] = Field(default_factory=dict)


def _watchdog_geelark_token(request: Request) -> str:
    if not rpa_watchdog_client.configured():
        raise HTTPException(status_code=503, detail="RPA watchdog is not configured")

    from app.workspace_secrets import get_secret

    workspace_id = int(request.state.workspace["id"])
    with session_scope(workspace_id) as session:
        token = get_secret(session, workspace_id, "GEELARK_TOKEN")
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Current workspace has no GeeLark token",
        )
    return token


def _watchdog_call(fn, *args):
    try:
        return fn(*args)
    except rpa_watchdog_client.WatchdogClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/ops/rpa-watchdog/health")
def rpa_watchdog_health(request: Request):
    token = _watchdog_geelark_token(request)
    return _watchdog_call(rpa_watchdog_client.health, token)


@router.get("/api/ops/rpa-watchdog/tasks")
def rpa_watchdog_tasks(request: Request):
    token = _watchdog_geelark_token(request)
    return _watchdog_call(rpa_watchdog_client.list_tasks, token)


@router.post("/api/ops/rpa-watchdog/start")
def start_watchdog_rpa(body: StartWatchdogRpaIn, request: Request):
    token = _watchdog_geelark_token(request)
    return _watchdog_call(
        rpa_watchdog_client.start_task,
        token,
        body.model_dump(),
    )


@router.post("/api/ops/rpa-watchdog/tasks/{task_id}/stop")
def stop_watchdog_rpa(task_id: str, request: Request):
    token = _watchdog_geelark_token(request)
    return _watchdog_call(rpa_watchdog_client.stop_task, token, task_id)


@router.delete("/api/ops/rpa-watchdog/tasks/{task_id}")
def delete_watchdog_rpa(task_id: str, request: Request):
    token = _watchdog_geelark_token(request)
    return _watchdog_call(rpa_watchdog_client.delete_task, token, task_id)


def _list_sessions(session: Session, *, only_overdue: bool) -> list[dict]:
    """Return in-flight sessions with the account profile + computed
    overdue flag.

    Ordered active-first by overdue status, then oldest first so the
    longest-running sessions float to the top of the triage view.
    """
    where = "stopped_at IS NULL"
    if only_overdue:
        where += " AND budget_deadline_at < :now"
    rows = session.execute(
        text(
            f"SELECT ps.id, ps.account_id, a.profile_name, ps.env_id, "
            f"       ps.source, ps.owner_type, ps.owner_id, "
            f"       ps.geelark_task_id, ps.status, ps.started_at, "
            f"       ps.budget_deadline_at, ps.created_by, "
            f"       (ps.budget_deadline_at < :now) AS overdue "
            f"FROM phone_sessions ps "
            f"LEFT JOIN accounts a ON a.id = ps.account_id "
            f"WHERE {where} "
            f"ORDER BY (ps.budget_deadline_at < :now) DESC, "
            f"         ps.started_at ASC"
        ),
        {"now": datetime.utcnow()},
    ).all()
    return [
        {
            "id": r.id,
            "account_id": r.account_id,
            "profile_name": r.profile_name,
            "env_id": r.env_id,
            "source": r.source,
            "owner_type": r.owner_type,
            "owner_id": r.owner_id,
            "geelark_task_id": r.geelark_task_id,
            "status": r.status,
            "started_at": r.started_at.isoformat() + "Z",
            "budget_deadline_at": r.budget_deadline_at.isoformat() + "Z",
            "created_by": r.created_by,
            "overdue": bool(r.overdue),
        }
        for r in rows
    ]


@router.get("/api/ops/sessions")
def list_sessions(status: str = "active"):
    """List in-flight phone_sessions.

    Query param `status`:
      - 'active' (default): every session with stopped_at IS NULL
      - 'overdue': only sessions whose budget_deadline_at has passed
    """
    if status not in ("active", "overdue"):
        raise HTTPException(
            status_code=400,
            detail="status must be 'active' or 'overdue'",
        )
    with session_scope() as session:
        return {"items": _list_sessions(
            session, only_overdue=(status == "overdue"))}


@router.post("/api/ops/sessions/{session_id}/release")
def release_session(session_id: int):
    """Operator override: finalize an in-flight session as STOPPED.

    Use for external_detected sessions (after manually stopping the
    phone in GeeLark) or for wedged sessions the per-service finalize
    paths missed. Does NOT stop the cloud phone — pair with the
    /api/phones/stop-all admin endpoint if needed.
    """
    with session_scope() as session:
        row = session.execute(
            text(
                "SELECT id, stopped_at FROM phone_sessions WHERE id = :id"
            ),
            {"id": session_id},
        ).first()
        if row is None:
            raise HTTPException(
                status_code=404, detail="session not found")
        if row.stopped_at is not None:
            return {"ok": True, "already_stopped": True}
        phone_runtime.finalize_success(
            session, session_id, observed_status="operator-release",
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Saga read-side ops
# ---------------------------------------------------------------------------
@router.get("/api/ops/sagas/in-flight")
def list_in_flight_sagas(
        kind: Optional[str] = Query(
            default=None,
            description="Filter to one saga kind: warmup | publish | engagement",
        ),
        limit: int = Query(default=500, ge=1, le=2000),
):
    """All sagas (task_runs) that have not finished yet. Sorted by
    deadline ascending so the closest-to-stuck appears first.

    Each row carries elapsed_seconds + budget_remaining_seconds +
    is_stuck so the dashboard can colour-code without doing its own
    time math.
    """
    with session_scope() as session:
        views = saga_views.list_in_flight_sagas(
            session, kind=kind, limit=limit,
        )
    return {"items": [v.to_jsonable() for v in views]}


@router.get("/api/ops/sagas/summary")
def saga_summary():
    """Per-kind in-flight count + the stuck counts. Two stuck signals:
      - stuck:      total lease budget passed (PhoneLease level)
      - step_stuck: one step over STEP_BUDGETS_SECONDS (saga level)
    The dashboard polls this once per refresh to render header badges."""
    with session_scope() as session:
        return {
            "in_flight_by_kind": saga_views.count_in_flight_by_kind(session),
            "stuck": saga_views.count_stuck(session),
            "step_stuck": saga_views.count_step_stuck(session),
        }


@router.get("/api/ops/sagas/{kind}/{business_ref}")
def describe_saga_endpoint(kind: str, business_ref: str):
    """Lookup the most recent saga attempt for (kind, business_ref).
    business_ref is the same format the outbox + inbox use, e.g.
    'publication:123' or 'engagement_run:42'."""
    with session_scope() as session:
        view = saga_views.describe_saga(
            session, kind=kind, business_ref=business_ref,
        )
    if view is None:
        raise HTTPException(status_code=404, detail="saga not found")
    return view.to_jsonable()


# ---------------------------------------------------------------------------
# YouTube false-success manual review queue
# ---------------------------------------------------------------------------
@router.get("/api/ops/youtube-audit")
def list_youtube_audit(limit: int = Query(default=100, ge=1, le=500)):
    """YouTube publications that landed as SUCCESS with an audit
    screenshot recorded. Phase 7b's PlatformAdapter.verify_real
    attaches the final RPA screenshot on every YouTube status=3 — this
    endpoint surfaces those rows so an operator can manually
    classify "real success" vs "GeeLark said success but YouTube
    rejected the title" (the 2026-06-18 incident).

    Returns the most recent rows with fail_screenshot set (the audit
    layer reuses that column to carry the screenshot URL — it's NULL
    on rows that never went through the YouTube audit path).
    """
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT p.id, p.video_id, p.account_id, p.platform, "
                "       p.result, p.posted_at, p.last_checked_at, "
                "       p.share_link, p.fail_screenshot, "
                "       a.profile_name "
                "FROM publications p "
                "LEFT JOIN accounts a ON a.id = p.account_id "
                "WHERE p.platform = 'youtube' "
                "  AND p.result = 'SUCCESS' "
                "  AND p.fail_screenshot IS NOT NULL "
                "ORDER BY COALESCE(p.last_checked_at, p.posted_at, "
                "                  p.created_at) DESC, p.id DESC "
                "LIMIT :limit"
            ),
            {"limit": limit},
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Inbox + Outbox health
# ---------------------------------------------------------------------------
@router.get("/api/ops/inbox/health")
def inbox_health():
    """callback_inbox health: how the GeeLark webhook firehose is
    landing. The numbers an operator wants:

      total_24h          how many callbacks GeeLark sent in last 24h
      unprocessed        rows whose process_geelark_callback never ran
                          (defer lost AND reaper hasn't caught it yet)
      oldest_unprocessed how long the worst lag is
      outcomes_24h       histogram of process_outcome (wake:*, no_session, ...)
                          across the last 24h

    `now` is passed in as a bind parameter (Python utcnow) rather than
    calling PG `now()` because prod PG runs in Asia/Shanghai while every
    timestamp we write is UTC — mixing the two shifts every duration
    by 8 hours.
    """
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    with session_scope() as session:
        total_24h = session.execute(text(
            "SELECT count(*) FROM callback_inbox "
            "WHERE received_at > :cutoff"
        ), {"cutoff": cutoff_24h}).scalar() or 0
        unprocessed = session.execute(text(
            "SELECT count(*) FROM callback_inbox WHERE processed_at IS NULL"
        )).scalar() or 0
        oldest = session.execute(text(
            "SELECT EXTRACT(EPOCH FROM (:now - min(received_at)))::int "
            "FROM callback_inbox WHERE processed_at IS NULL"
        ), {"now": now}).scalar()
        outcomes = session.execute(text(
            "SELECT COALESCE(process_outcome, 'pending') AS outcome, "
            "       count(*) AS n "
            "FROM callback_inbox "
            "WHERE received_at > :cutoff "
            "GROUP BY 1 ORDER BY n DESC"
        ), {"cutoff": cutoff_24h}).mappings().all()
    return {
        "total_24h": int(total_24h),
        "unprocessed": int(unprocessed),
        "oldest_unprocessed_seconds": (
            int(oldest) if oldest is not None else None
        ),
        "outcomes_24h": {r["outcome"]: int(r["n"]) for r in outcomes},
    }


@router.get("/api/ops/outbox/health")
def outbox_health():
    """outbox health: how reliable defer dispatch is. A non-zero
    pending_overdue means the dispatcher isn't keeping up; high
    average attempts means a particular intent is broken upstream.
    """
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    with session_scope() as session:
        total = session.execute(text(
            "SELECT count(*) FROM outbox WHERE sent_at IS NULL"
        )).scalar() or 0
        overdue = session.execute(text(
            "SELECT count(*) FROM outbox "
            "WHERE sent_at IS NULL AND next_attempt_at < :now"
        ), {"now": now}).scalar() or 0
        per_intent = session.execute(text(
            "SELECT intent, count(*) AS n, "
            "       avg(attempts)::float AS avg_attempts, "
            "       max(attempts) AS max_attempts "
            "FROM outbox WHERE sent_at IS NULL "
            "GROUP BY intent ORDER BY n DESC"
        )).mappings().all()
        sent_24h = session.execute(text(
            "SELECT count(*) FROM outbox WHERE sent_at > :cutoff"
        ), {"cutoff": cutoff_24h}).scalar() or 0
        oldest = session.execute(text(
            "SELECT EXTRACT(EPOCH FROM (:now - min(created_at)))::int "
            "FROM outbox WHERE sent_at IS NULL"
        ), {"now": now}).scalar()
    return {
        "pending": int(total),
        "pending_overdue": int(overdue),
        "sent_24h": int(sent_24h),
        "oldest_pending_seconds": (
            int(oldest) if oldest is not None else None
        ),
        "per_intent": [
            {
                "intent": r["intent"],
                "pending": int(r["n"]),
                "avg_attempts": (
                    round(float(r["avg_attempts"]), 2)
                    if r["avg_attempts"] is not None else 0.0
                ),
                "max_attempts": int(r["max_attempts"] or 0),
            }
            for r in per_intent
        ],
    }


@router.post("/api/ops/publications/{publication_id}/mark-failed")
def operator_mark_publication_failed(publication_id: int, body: dict):
    """Operator override: a publication GeeLark reported successful
    actually failed (most common case: YouTube rejected the title
    and the audit screenshot shows a disabled Upload button). Flip
    the row to FAILURE so it surfaces in the dashboard as needing
    rework and so any downstream cost attribution still ties to the
    original task.

    Body: {"reason": "operator: youtube title rejected", "retry": true}
      reason: free text, goes on publication.notes
      retry:  optional; when true (default) leave geelark_task_id
              cleared so the next auto_publish_scan can re-claim the
              publication. When false, the row stays FAILURE forever.
    """
    from app.repositories import publications as pub_repo
    reason = (body.get("reason") or "operator: manual mark-failed").strip()
    retry = body.get("retry", True)
    with session_scope() as session:
        pub = pub_repo.get(session, publication_id)
        if pub is None:
            raise HTTPException(status_code=404, detail="publication not found")
        if retry:
            # Wipe the GeeLark task id and result-side timestamps so
            # auto_publish_scan re-queues the row. Mirror the shape of
            # pub_repo.schedule_retry but without retry_count math (the
            # operator override should not consume a retry budget).
            session.execute(
                text(
                    "UPDATE publications "
                    "SET result = 'PENDING', "
                    "    notes = :notes, "
                    "    geelark_task_id = NULL, "
                    "    posted_at = NULL, "
                    "    last_checked_at = :now, "
                    "    next_retry_at = NULL, "
                    "    fail_code = NULL, "
                    "    fail_screenshot = NULL "
                    "WHERE id = :id"
                ),
                {
                    "id": publication_id,
                    "notes": reason,
                    "now": datetime.utcnow(),
                },
            )
        else:
            pub_repo.set_result(
                session, publication_id, "FAILURE", reason,
            )
        session.commit()
    return {"ok": True, "publication_id": publication_id, "retry": retry}
