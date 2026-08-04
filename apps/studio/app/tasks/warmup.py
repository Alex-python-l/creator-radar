"""Warmup procrastinate tasks — lease-based.

Three tasks make up the pipeline:
  start_warmup_task         queue=phones,  no lock=    — T1→T3 + chain check
  check_warmup_status_task  queue=default, no lock=    — one poll → maybe self-defer
  reclaim_in_flight_warmup_task  cron */5  — sweep stale lease + dispatch

Per-task notes live in app/services/warmup_service.py.
"""
import asyncio
from datetime import datetime

from sqlalchemy import text

from app.db import session_scope
from app.services import warmup_service
from app.services.geelark_service import workspace_client, workspace_for_row
import procrastinate

from app.tasks import procrastinate_app

WARMUP_DURATION_MINUTES = 3
# Don't poll before the warmup is almost certainly done — every poll
# in the early phase is wasted because GeeLark always reports
# `status=2` (running) until the RPA finishes. Schedule the first
# status check ~20s before the expected end so we catch a fast finish
# without paying for early polls.
FIRST_CHECK_DELAY_SECONDS = WARMUP_DURATION_MINUTES * 60 - 20  # 160s
# Once we start polling, keep the cadence tight so the moment GeeLark
# flips status=3 we stop the phone — every extra 10s of polling lag is
# billed.
CHECK_INTERVAL_SECONDS = 10
# Budget for stuck/timed-out tasks: 60 polls × 10s ≈ 10 minutes after
# warmup should have finished, then mark timed_out.
MAX_CHECK_ATTEMPTS = 60


def _start_warmup_sync(account_id: int) -> str:
    with session_scope() as session:
        workspace_id = workspace_for_row(session, "accounts", account_id)
        return warmup_service.start_warmup_one(
            session, workspace_client(session, workspace_id), account_id,
            duration_minutes=WARMUP_DURATION_MINUTES)


@procrastinate_app.task(queue="phones", name="start_warmup")
async def start_warmup_task(account_id: int) -> str:
    try:
        gtid = await asyncio.to_thread(_start_warmup_sync, account_id)
    except warmup_service.PhoneBusy as exc:
        return f"skipped: {exc}"
    if gtid.startswith("skipped"):
        return gtid
    await check_warmup_status_task.configure(
        schedule_in={"seconds": FIRST_CHECK_DELAY_SECONDS},
    ).defer_async(account_id=account_id, task_id=gtid,
                  attempts_remaining=MAX_CHECK_ATTEMPTS)
    return f"INITIATED task {gtid}"


def _advance_warmup_sync(account_id: int, task_id: str) -> str:
    with session_scope() as session:
        workspace_id = workspace_for_row(session, "accounts", account_id)
        return warmup_service.advance_warmup_one(
            session, workspace_client(session, workspace_id), account_id, task_id)


def _mark_warmup_timed_out_sync(account_id: int, task_id: str) -> str:
    with session_scope() as session:
        workspace_id = workspace_for_row(session, "accounts", account_id)
        return warmup_service.mark_warmup_timed_out(
            session, workspace_client(session, workspace_id), account_id, task_id)


# retry: transient failures must not kill the poll chain (idempotent
# poll guarded by _chain_owns_active_session).
@procrastinate_app.task(queue="default", name="check_warmup_status",
                        retry=procrastinate.RetryStrategy(
                            max_attempts=3, wait=30))
async def check_warmup_status_task(account_id: int, task_id: str,
                                    attempts_remaining: int) -> str:
    outcome = await asyncio.to_thread(_advance_warmup_sync, account_id, task_id)
    if outcome == "running" and attempts_remaining > 0:
        await check_warmup_status_task.configure(
            schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
        ).defer_async(account_id=account_id, task_id=task_id,
                      attempts_remaining=attempts_remaining - 1)
        return outcome
    if outcome == "running":
        # Poll chain exhausted but GeeLark task never reached a terminal
        # status. Without this branch the phone keeps billing and the
        # reclaim cron just resets the 20-min window forever.
        outcome = await asyncio.to_thread(
            _mark_warmup_timed_out_sync, account_id, task_id)
    return outcome


def _list_stranded_warmups_sync() -> list[int]:
    """Stranded warmups: in-flight warmup phone_sessions whose budget
    has passed, OR whose GeeLark task was never attached (T1↔T3 crash
    window). overdue_session_watchdog also monitors expired sessions;
    this scan additionally surfaces the crash-window case so
    reclaim_in_flight_one can finalize them."""
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT ps.account_id "
                "FROM phone_sessions ps "
                "WHERE ps.source = 'warmup' "
                "  AND ps.stopped_at IS NULL "
                "  AND ("
                "    ps.budget_deadline_at < :now "
                "    OR ps.geelark_task_id IS NULL"
                "  )"
            ),
            {"now": datetime.utcnow()},
        ).fetchall()
    return [r[0] for r in rows]


def _reclaim_one_sync(account_id: int) -> str:
    with session_scope() as session:
        workspace_id = workspace_for_row(session, "accounts", account_id)
        return warmup_service.reclaim_in_flight_one(
            session, workspace_client(session, workspace_id), account_id)


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(queue="default", name="reclaim_in_flight_warmup",
                        queueing_lock="reclaim_in_flight_warmup")
async def reclaim_in_flight_warmup_task(timestamp: int) -> str:
    candidates = await asyncio.to_thread(_list_stranded_warmups_sync)
    cleared, resumed = 0, 0
    for aid in candidates:
        outcome = await asyncio.to_thread(_reclaim_one_sync, aid)
        if outcome == "pending_cleared":
            cleared += 1
        elif outcome.startswith("resume:"):
            task_id = outcome.split(":", 1)[1]
            await check_warmup_status_task.configure(
                schedule_in={"seconds": 0},
            ).defer_async(account_id=aid, task_id=task_id,
                          attempts_remaining=MAX_CHECK_ATTEMPTS)
            resumed += 1
        # "busy:*" returns (from Task 4 follow-up) — neither cleared nor resumed
    return f"reclaim_in_flight_warmup: cleared={cleared}, resumed={resumed}"
