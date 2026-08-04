"""Outbox dispatcher — periodic task that drains pending outbox rows
to their target Procrastinate task.

Producers (API handlers, periodic scanners) enqueue an outbox row
inside the same transaction as the business write. The dispatcher
runs every two minutes, picks up rows whose next_attempt_at is due,
and calls the matching defer_async. Success → mark_sent; failure →
record_failure with exponential backoff.

When a producer is on the happy path it ALSO tries to defer the same
task synchronously after commit (best-effort prompt dispatch). When
that prompt defer succeeds, the producer calls mark_sent immediately
so the dispatcher never picks the row up. The double-defer is safe:
the second defer_async creates a second job, and the worker tasks all
finalize/no-op on a publication/run/video that's already past their
state.
"""
import asyncio
import logging
from typing import Any

from app.db import session_scope
from app.repositories import outbox as outbox_repo
from app.tasks import procrastinate_app


logger = logging.getLogger(__name__)

_DISPATCH_BATCH = 100


async def _dispatch_one(row: dict[str, Any]) -> None:
    """Route an outbox row to its target task.

    Raises on any defer failure. Callers map exceptions to
    record_failure.
    """
    intent = row["intent"]
    payload = row["payload"] or {}

    if intent == "publish_publication":
        from app.tasks.publish import publish_publication_task
        await publish_publication_task.defer_async(
            publication_id=int(payload["publication_id"]),
        )
        return
    if intent == "submit_engagement_run":
        from app.tasks.engagement import submit_engagement_run_task
        # delay_seconds = per-phone submit stagger computed at enqueue
        # time; same-phone runs must not all start their PhoneBusy retry
        # window at t=0 (see api/engagement.py SUBMIT_SLOT_SECONDS).
        await submit_engagement_run_task.configure(
            schedule_in={"seconds": int(payload.get("delay_seconds") or 0)},
        ).defer_async(
            run_id=int(payload["run_id"]),
        )
        return
    if intent == "extract_understanding":
        from app.tasks.writer_understanding import (
            extract_understanding_task,
        )
        await extract_understanding_task.defer_async(
            video_id=int(payload["video_id"]),
        )
        return
    raise ValueError(f"unknown outbox intent: {intent!r}")


def _list_pending_sync() -> list[dict[str, Any]]:
    with session_scope() as session:
        return outbox_repo.list_pending(session, limit=_DISPATCH_BATCH)


def _mark_sent_sync(outbox_id: int) -> None:
    with session_scope() as session:
        outbox_repo.mark_sent(session, outbox_id)


def _record_failure_sync(outbox_id: int, error: str) -> None:
    with session_scope() as session:
        outbox_repo.record_failure(session, outbox_id, error)


@procrastinate_app.periodic(cron="*/2 * * * *")
@procrastinate_app.task(
    queue="default",
    name="dispatch_outbox",
    queueing_lock="dispatch_outbox",
)
async def dispatch_outbox_task(timestamp: int) -> str:
    """Drain pending outbox rows. Capped per tick by _DISPATCH_BATCH
    so a flood drains gracefully across ticks rather than starving the
    worker pool."""
    rows = await asyncio.to_thread(_list_pending_sync)
    sent = 0
    failed = 0
    for row in rows:
        outbox_id = row["id"]
        try:
            await _dispatch_one(row)
        except Exception as exc:
            failed += 1
            message = (str(exc).strip() or exc.__class__.__name__)[:1024]
            await asyncio.to_thread(
                _record_failure_sync, outbox_id, message)
            logger.exception(
                "outbox dispatch failed (intent=%s, business_ref=%s, "
                "attempts=%s)",
                row["intent"], row["business_ref"], row["attempts"],
            )
            continue
        await asyncio.to_thread(_mark_sent_sync, outbox_id)
        sent += 1
    return f"dispatch_outbox: sent={sent} failed={failed}"


# ---------------------------------------------------------------------------
# Prompt dispatch helper for API handlers
# ---------------------------------------------------------------------------
async def try_prompt_dispatch(outbox_id: int, intent: str,
                                payload: dict[str, Any]) -> bool:
    """Best-effort immediate defer for a freshly enqueued outbox row.

    Returns True when the defer succeeded and the row has been marked
    sent; False when defer failed and the dispatcher will pick it up.
    Never raises — the outbox row is the safety net.
    """
    row = {"id": outbox_id, "intent": intent, "payload": payload,
           "business_ref": "", "attempts": 0,
           "next_attempt_at": None, "last_error": None}
    try:
        await _dispatch_one(row)
    except Exception:
        logger.exception(
            "outbox prompt dispatch failed for id=%s intent=%s; "
            "dispatcher will retry",
            outbox_id, intent,
        )
        return False
    try:
        await asyncio.to_thread(_mark_sent_sync, outbox_id)
    except Exception:
        logger.exception(
            "outbox mark_sent failed for id=%s after prompt dispatch; "
            "dispatcher may double-defer (downstream tasks are idempotent)",
            outbox_id,
        )
        return False
    return True
