"""Periodic logger for step-level stuck sagas.

`overdue_session_watchdog` (every 5 min) finalizes phone_sessions whose
lease deadline has passed. It catches "the whole lease is wedged" but
not "one step took way too long" — and that second class is the
silent-stall pattern: lease still has time on the clock, but the saga
hasn't moved off PHONE_STARTING in 5 minutes, or UPLOADING_RESOURCE
hasn't finished in 10 minutes.

This task scans task_runs every 5 minutes and emits one warning per
stuck saga. It does NOT mark TIMEOUT — that's overdue_session_watchdog's
job. The log line is enough to surface the issue in ops; auto-killing
a step-stuck saga risks racing the very-near-future completion that
just got slow.

If the same saga is step-stuck on subsequent ticks the log fires again
(at-most-once-per-tick semantics, not at-most-once-per-saga). Operators
can correlate by saga_id.
"""
import asyncio
import logging
from typing import Any

from app.db import session_scope
from app.services import sagas as saga_views
from app.tasks import procrastinate_app


logger = logging.getLogger(__name__)


def _list_step_stuck_sync() -> list[dict[str, Any]]:
    with session_scope() as session:
        views = saga_views.list_step_stuck_sagas(session)
        # Materialise the dataclasses to plain dicts inside the
        # session_scope window. Caller is sync-friendly.
        return [v.to_jsonable() for v in views]


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(
    queue="default",
    name="saga_stuck_logger",
    queueing_lock="saga_stuck_logger",
)
async def saga_stuck_logger_task(timestamp: int) -> str:
    """One log warning per step-stuck saga. Returns a one-line summary
    so the procrastinate dashboard shows the count at a glance."""
    rows = await asyncio.to_thread(_list_step_stuck_sync)
    for view in rows:
        logger.warning(
            "saga_step_stuck saga_id=%s kind=%s business_ref=%s "
            "step=%s seconds_at_step=%s step_budget=%s "
            "phone_session_id=%s geelark_task_id=%s",
            view["saga_id"], view["kind"], view["business_ref"],
            view["current_step"], view["seconds_at_current_step"],
            view["step_budget_seconds"], view["phone_session_id"],
            view["geelark_task_id"],
        )
    return f"saga_stuck_logger: warned={len(rows)}"
