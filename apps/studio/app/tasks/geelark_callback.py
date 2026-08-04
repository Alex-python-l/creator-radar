"""Worker side of the GeeLark callback inbox.

The endpoint at app/api/geelark.py records every accepted callback in
`callback_inbox` and defers exactly one `process_geelark_callback_task`
per newly-inserted row. This task walks the row to the right business
entity and wakes the matching check_*_status_task on a 0-second
schedule. Outcome tags land on the inbox row so an operator can audit
why a callback did or didn't drive convergence.

A periodic reaper (`reclaim_callback_inbox_task`) scans for rows whose
defer was lost — e.g. the endpoint crashed between INSERT and
defer_async, or the Procrastinate App was not yet open during a
deploy. It re-defers each one, capped to a small batch per tick so a
backlog can't flood the queue.

The polling chain remains armed in parallel: this task is a fast-path
that collapses the polling delay, not the only path. If a callback is
ever lost or arrives malformed, polling still converges.
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import text

from app.db import session_scope
from app.tasks import procrastinate_app


logger = logging.getLogger(__name__)


_PHONE_SESSION_BY_TASK_SQL = (
    "SELECT id, account_id, source, owner_type, owner_id, stopped_at "
    "FROM phone_sessions "
    "WHERE geelark_task_id = :tid "
    "ORDER BY id DESC LIMIT 1"
)


def _mark_outcome_sync(inbox_id: int, outcome: str) -> None:
    with session_scope() as session:
        session.execute(
            text(
                "UPDATE callback_inbox "
                "SET processed_at = :now, process_outcome = :out "
                "WHERE id = :id AND processed_at IS NULL"
            ),
            {
                "id": inbox_id,
                "out": outcome[:64],
                "now": datetime.utcnow(),
            },
        )
        session.commit()


def _resolve_dispatch_sync(inbox_id: int) -> dict | None:
    """Return a dict describing the wake to defer, or None when nothing
    actionable is found (in which case the outcome is already marked).

    Possible return shapes:
      {"kind": "publish",    "publication_id": int}
      {"kind": "engagement", "run_id": int}
      {"kind": "warmup",     "account_id": int, "task_id": str}
    """
    with session_scope() as session:
        row = session.execute(
            text(
                "SELECT task_id, status FROM callback_inbox "
                "WHERE id = :id AND processed_at IS NULL"
            ),
            {"id": inbox_id},
        ).first()
        if row is None:
            # Already processed by a duplicate defer (rare) or the row
            # vanished. Nothing more to do.
            return None
        task_id = row[0]
        sess = session.execute(
            text(_PHONE_SESSION_BY_TASK_SQL),
            {"tid": task_id},
        ).first()
    if sess is None:
        _mark_outcome_sync(inbox_id, "no_session")
        return None
    if sess.stopped_at is not None:
        # Session already finalized — the poll chain (or a prior wake)
        # has converged this flight; waking another 18-attempt check
        # chain would only burn GeeLark queries on a terminal row.
        _mark_outcome_sync(inbox_id, "session_closed")
        return None
    source = sess.source
    owner_type = sess.owner_type
    owner_id = sess.owner_id
    if source == "publish" and owner_type == "publication" and owner_id:
        return {"kind": "publish", "publication_id": int(owner_id)}
    if (
        source == "engagement"
        and owner_type == "engagement_run"
        and owner_id
    ):
        return {"kind": "engagement", "run_id": int(owner_id)}
    if source == "warmup" and owner_type == "account" and owner_id:
        return {
            "kind": "warmup",
            "account_id": int(owner_id),
            "task_id": str(task_id),
        }
    # Manual holds and external_detected sessions don't have a polling
    # chain to wake — the manual operator owns the session lifecycle.
    _mark_outcome_sync(
        inbox_id, f"unhandled:source={source},owner={owner_type}",
    )
    return None


@procrastinate_app.task(
    queue="default",
    name="process_geelark_callback",
)
async def process_geelark_callback_task(inbox_id: int) -> str:
    """Dispatch one inbox row to the correct check_*_status task and
    mark the row processed. Returns a short outcome string so the
    procrastinate console shows what happened."""
    dispatch = await asyncio.to_thread(_resolve_dispatch_sync, inbox_id)
    if dispatch is None:
        return "no_dispatch"
    kind = dispatch["kind"]
    if kind == "publish":
        from app.tasks.publish import (
            MAX_CHECK_RETRIES, check_publication_status_task,
        )
        await check_publication_status_task.configure(
            schedule_in={"seconds": 0},
        ).defer_async(
            publication_id=dispatch["publication_id"],
            attempts_remaining=MAX_CHECK_RETRIES,
        )
        outcome = "wake:publish"
    elif kind == "engagement":
        from app.tasks.engagement import (
            MAX_CHECK_RETRIES, check_engagement_run_task,
        )
        await check_engagement_run_task.configure(
            schedule_in={"seconds": 0},
        ).defer_async(
            run_id=dispatch["run_id"],
            attempts_remaining=MAX_CHECK_RETRIES,
        )
        outcome = "wake:engagement"
    elif kind == "warmup":
        from app.tasks.warmup import (
            MAX_CHECK_ATTEMPTS, check_warmup_status_task,
        )
        await check_warmup_status_task.configure(
            schedule_in={"seconds": 0},
        ).defer_async(
            account_id=dispatch["account_id"],
            task_id=dispatch["task_id"],
            attempts_remaining=MAX_CHECK_ATTEMPTS,
        )
        outcome = "wake:warmup"
    else:
        outcome = f"unknown_kind:{kind}"
    await asyncio.to_thread(_mark_outcome_sync, inbox_id, outcome)
    return outcome


# ---------------------------------------------------------------------------
# Reaper for inbox rows whose initial defer was lost
# ---------------------------------------------------------------------------
_INBOX_REAPER_BATCH = 50
_INBOX_REAPER_MIN_AGE_SECONDS = 120


def _list_unprocessed_inbox_sync() -> list[int]:
    """Inbox rows older than _INBOX_REAPER_MIN_AGE_SECONDS that have
    never been processed. The age cutoff avoids racing the endpoint's
    own defer call for fresh rows."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(
        seconds=_INBOX_REAPER_MIN_AGE_SECONDS)
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT id FROM callback_inbox "
                "WHERE processed_at IS NULL AND received_at < :cutoff "
                "ORDER BY id "
                "LIMIT :limit"
            ),
            {"cutoff": cutoff, "limit": _INBOX_REAPER_BATCH},
        ).fetchall()
    return [r[0] for r in rows]


@procrastinate_app.periodic(cron="*/2 * * * *")
@procrastinate_app.task(
    queue="default",
    name="reclaim_callback_inbox",
    queueing_lock="reclaim_callback_inbox",
)
async def reclaim_callback_inbox_task(timestamp: int) -> str:
    """Re-defer process_geelark_callback_task for any inbox row whose
    original defer was lost (endpoint crashed mid-flight, Procrastinate
    App not yet open during a deploy, etc.). Capped per tick so a
    backlog can drain without flooding the worker."""
    candidates = await asyncio.to_thread(_list_unprocessed_inbox_sync)
    deferred = 0
    for inbox_id in candidates:
        try:
            await process_geelark_callback_task.configure(
                schedule_in={"seconds": 0},
            ).defer_async(inbox_id=inbox_id)
            deferred += 1
        except Exception:
            logger.exception(
                "reclaim_callback_inbox: defer failed for inbox %d",
                inbox_id,
            )
    return f"reclaim_callback_inbox: deferred={deferred}"
