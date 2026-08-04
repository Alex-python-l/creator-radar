import asyncio
from datetime import datetime, timedelta

from sqlalchemy import text

import procrastinate

from app.tasks import procrastinate_app
from app.db import session_scope
from app.services import engagement_service, geelark_service

# 18 × 30s = 9-min window (matches publish). The old 10 × 30s = 5-min
# budget sat inside the normal completion band — RPA tasks routinely
# finish in 3-6 min, so 5.5-6-min finishers were cancelled with the
# comment already live on the platform: recorded FAILURE, yet posted,
# and invisible to the daily quota (which only counts terminal
# successes). The timeout handler's last-look narrows the damage, but
# the budget itself must clear the p99, not straddle the median.
MAX_CHECK_RETRIES = 18
CHECK_INTERVAL_SECONDS = 30
MAX_SUBMIT_RETRIES = 40
SUBMIT_RETRY_INTERVAL_SECONDS = 30
QUEUE_WAIT_SECONDS = 600
PROXY_RECOVERY_WAIT_SECONDS = 3600

# Pad past midnight to be safe against clock skew between worker and DB.
DAILY_LIMIT_PAD_SECONDS = 60


def _seconds_until_next_utc_midnight() -> int:
    now = datetime.utcnow()
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return max(1, int((tomorrow - now).total_seconds()) + DAILY_LIMIT_PAD_SECONDS)


def _submit_run_sync(run_id: int) -> str:
    with session_scope() as session:
        workspace_id = geelark_service.workspace_for_row(
            session, "engagement_runs", run_id,
        )
        return engagement_service.submit_run_to_geelark(
            session,
            geelark_service.workspace_client(session, workspace_id),
            run_id=run_id,
        )


def _mark_run_submit_exhausted_sync(run_id: int) -> str:
    with session_scope() as session:
        return engagement_service.mark_submit_retries_exhausted(session, run_id)


def _check_run_sync(run_id: int) -> str:
    with session_scope() as session:
        workspace_id = geelark_service.workspace_for_row(
            session, "engagement_runs", run_id,
        )
        return engagement_service.check_engagement_run(
            session,
            geelark_service.workspace_client(session, workspace_id),
            run_id,
        )


def _mark_run_timed_out_sync(run_id: int) -> str:
    with session_scope() as session:
        workspace_id = geelark_service.workspace_for_row(
            session, "engagement_runs", run_id,
        )
        return engagement_service.mark_run_timed_out(
            session,
            geelark_service.workspace_client(session, workspace_id),
            run_id,
        )


@procrastinate_app.task(queue="phones", name="submit_engagement_run")
async def submit_engagement_run_task(
    run_id: int,
    attempts_remaining: int = MAX_SUBMIT_RETRIES,
):
    try:
        task_id = await asyncio.to_thread(_submit_run_sync, run_id)
    except engagement_service.DailyLimitReached as e:
        # Daily limits are an expected, planned throttle — not a transient
        # error — so we do NOT consume attempts_remaining. Sleep past the
        # next UTC midnight, then try again with a fresh quota.
        await submit_engagement_run_task.configure(
            schedule_in={"seconds": _seconds_until_next_utc_midnight()},
        ).defer_async(
            run_id=run_id,
            attempts_remaining=attempts_remaining,
        )
        return f"DAILY_LIMIT: {e}"
    except engagement_service.ProxyUnavailable as e:
        # Known-bad proxies are an operator/re-probe condition. Keep the run
        # queued and check hourly instead of paying GeeLark for guaranteed
        # 29996 failures.
        await submit_engagement_run_task.configure(
            schedule_in={"seconds": PROXY_RECOVERY_WAIT_SECONDS},
        ).defer_async(
            run_id=run_id,
            attempts_remaining=attempts_remaining,
        )
        return f"PROXY_UNAVAILABLE: {e}"
    except engagement_service.QueueBlocked as e:
        # Waiting behind another run on the same phone is normal queue
        # backpressure, not a failing submit attempt. A bounded retry budget
        # turns a healthy serialized queue into mass FAILURE under backlog.
        await submit_engagement_run_task.configure(
            schedule_in={"seconds": QUEUE_WAIT_SECONDS},
        ).defer_async(
            run_id=run_id,
            attempts_remaining=attempts_remaining,
        )
        return f"QUEUED: {e}"
    except engagement_service.PhoneBusy as e:
        if attempts_remaining > 0:
            await submit_engagement_run_task.configure(
                schedule_in={"seconds": SUBMIT_RETRY_INTERVAL_SECONDS},
            ).defer_async(
                run_id=run_id,
                attempts_remaining=attempts_remaining - 1,
            )
            return f"QUEUED: {e}"
        outcome = await asyncio.to_thread(_mark_run_submit_exhausted_sync, run_id)
        return f"{outcome.upper()}: {e}"
    if not task_id:
        return "SKIPPED: engagement run is not queueable"
    await check_engagement_run_task.configure(
        schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
    ).defer_async(
        run_id=run_id,
        attempts_remaining=MAX_CHECK_RETRIES,
    )
    return f"INITIATED task {task_id}"


# retry: transient failures must not kill the poll chain (idempotent
# poll; a dead chain historically meant a wrong FAILURE verdict at the
# 20-min lease expiry with the comment already live).
@procrastinate_app.task(queue="default", name="check_engagement_run",
                        retry=procrastinate.RetryStrategy(
                            max_attempts=3, wait=30))
async def check_engagement_run_task(run_id: int, attempts_remaining: int):
    outcome = await asyncio.to_thread(_check_run_sync, run_id)
    if outcome == "running" and attempts_remaining > 0:
        await check_engagement_run_task.configure(
            schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
        ).defer_async(
            run_id=run_id,
            attempts_remaining=attempts_remaining - 1,
        )
    elif outcome == "running":
        outcome = await asyncio.to_thread(_mark_run_timed_out_sync, run_id)
    return outcome


def _list_stranded_engagements_sync() -> list[int]:
    """Stranded engagements via the legacy lease column. Phase 1 Step 5
    keeps this signature for backward compat with existing tests."""
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT r.id "
                "FROM engagement_runs r "
                "JOIN accounts a ON a.id = r.account_id "
                "LEFT JOIN phone_sessions ps "
                "  ON ps.account_id = a.id "
                "  AND ps.stopped_at IS NULL "
                "WHERE r.result = 'INITIATED' "
                "  AND r.geelark_task_id IS NOT NULL "
                "  AND ("
                "    ps.budget_deadline_at < :now "
                "    OR ps.id IS NULL"
                "  )"
            ),
            {"now": datetime.utcnow()},
        ).fetchall()
    return [r[0] for r in rows]


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(queue="default", name="reclaim_in_flight_engagement",
                        queueing_lock="reclaim_in_flight_engagement")
async def reclaim_in_flight_engagement_task(timestamp: int) -> str:
    stranded = await asyncio.to_thread(_list_stranded_engagements_sync)
    for run_id in stranded:
        await check_engagement_run_task.configure(
            schedule_in={"seconds": 0},
        ).defer_async(run_id=run_id, attempts_remaining=MAX_CHECK_RETRIES)
    return f"reclaim_in_flight_engagement: resumed {len(stranded)}"
