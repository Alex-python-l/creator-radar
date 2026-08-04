"""Per-publication Procrastinate tasks. Replaces the per-video pair
from Phase 2 (publish_video_task / check_publish_status_task).

Locking model
-------------
publish_publication_task relies on per-account phone_sessions ledger
(via PhoneRuntimeManager.start_session) for serialization. The
Procrastinate `lock=` parameter is not used here — the partial unique
index on phone_sessions is the single source of truth for phone
occupancy across warmup / publish / engagement.

Concurrency note
----------------
Procrastinate's worker runs all jobs on a single event loop. The
GeeLark-touching service calls inside these tasks are blocking sync
(httpx + time.sleep), so the task bodies wrap them with
asyncio.to_thread to release the loop. Without that, --concurrency=N
has no effect — one blocking sync call freezes every other job.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import procrastinate
from procrastinate.exceptions import AlreadyEnqueued

from app.tasks import procrastinate_app
from app.db import session_scope
from app.services import geelark_service, posting_service

# Status-check chain: 18 polls × 30s = 9-minute window after submit.
# Was 10 (5 min, commit 238deeb); 5 min wrongly assumed yt publish completes
# in 4-7 min, but production data shows yt uploads regularly take 8-9 min,
# so the 5-min window cancel_tasks()ed every yt mid-upload (8/8 fb/yt
# stall on 2026-06-16 07:01). 9 min covers the yt happy path with a slim
# margin while still cutting per-stuck-task phone burn 2.2× vs the
# pre-238deeb 20-min window. tt/ig/fb finish well under 4 min so the
# extra ceiling never fires for them.
MAX_CHECK_RETRIES = 18
CHECK_INTERVAL_SECONDS = 30
MAX_SUBMIT_RETRIES = 40
SUBMIT_RETRY_INTERVAL_SECONDS = 30


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retry_at_epoch_seconds(retry_at: datetime) -> int:
    return int(_as_utc(retry_at).timestamp())


def _retry_delay_seconds(retry_at: datetime, *,
                         now: datetime | None = None) -> int:
    now_utc = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    return max(0, int((_as_utc(retry_at) - now_utc).total_seconds()))


async def _defer_auto_publish_scan_at(retry_at) -> None:
    from app.tasks.auto_publish import auto_publish_scan_task

    retry_epoch = _retry_at_epoch_seconds(retry_at)
    delay_seconds = _retry_delay_seconds(retry_at)
    try:
        await auto_publish_scan_task.configure(
            schedule_in={"seconds": delay_seconds},
            queueing_lock=f"auto_publish_scan_retry_{retry_epoch}",
        ).defer_async(timestamp=retry_epoch)
    except AlreadyEnqueued:
        pass


def _submit_publication_sync(publication_id: int) -> str:
    with session_scope() as session:
        workspace_id = geelark_service.workspace_for_row(
            session, "publications", publication_id,
        )
        return posting_service.submit_publication_to_geelark(
            session,
            geelark_service.workspace_client(session, workspace_id),
            publication_id=publication_id,
        )


def _schedule_publication_retry_sync(publication_id: int):
    with session_scope() as session:
        return posting_service.schedule_retry_if_allowed(
            session, publication_id,
        )


@procrastinate_app.task(queue="phones", name="publish_publication")
async def publish_publication_task(
    publication_id: int,
    submit_attempts_remaining: int = MAX_SUBMIT_RETRIES,
):
    try:
        task_id = await asyncio.to_thread(
            _submit_publication_sync, publication_id,
        )
    except posting_service.PhoneBusy as e:
        if submit_attempts_remaining > 0:
            await publish_publication_task.configure(
                schedule_in={"seconds": SUBMIT_RETRY_INTERVAL_SECONDS},
            ).defer_async(
                publication_id=publication_id,
                submit_attempts_remaining=submit_attempts_remaining - 1,
            )
            return f"QUEUED: {e}"
        return f"SKIPPED: {e}"
    except Exception as e:
        retry_at = await asyncio.to_thread(
            _schedule_publication_retry_sync, publication_id,
        )
        if retry_at is not None:
            await _defer_auto_publish_scan_at(retry_at)
            return f"RETRY scheduled at {retry_at.isoformat()}: {e}"
        return f"FAILURE: {e}"
    # If we got here, the GeeLark submit succeeded. Chain the poll.
    # `schedule_in` is a Procrastinate deferral option, not a task kwarg:
    # it must go through .configure(), otherwise it is passed to the task
    # function and raises TypeError at execution time.
    await check_publication_status_task.configure(
        schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
    ).defer_async(
        publication_id=publication_id,
        attempts_remaining=MAX_CHECK_RETRIES,
    )
    return f"INITIATED task {task_id}"


def _check_publication_one_sync(publication_id: int) -> str:
    with session_scope() as session:
        workspace_id = geelark_service.workspace_for_row(
            session, "publications", publication_id,
        )
        return posting_service.check_publication_one(
            session,
            geelark_service.workspace_client(session, workspace_id),
            publication_id,
        )


def _mark_publication_timed_out_sync(publication_id: int) -> str:
    with session_scope() as session:
        workspace_id = geelark_service.workspace_for_row(
            session, "publications", publication_id,
        )
        return posting_service.mark_publication_timed_out(
            session,
            geelark_service.workspace_client(session, workspace_id),
            publication_id,
        )


# retry: a single transient failure (DB blip, GeeLark 5xx bubbling out
# of the sync body) must not kill the whole poll chain — a dead chain
# leaves the publication to the 20-min lease expiry + reclaim, and the
# expired-lease path is where wrong verdicts historically came from.
# The check itself is an idempotent poll, safe to re-run.
@procrastinate_app.task(queue="default",
                         name="check_publication_status",
                         retry=procrastinate.RetryStrategy(
                             max_attempts=3, wait=30))
async def check_publication_status_task(publication_id: int,
                                         attempts_remaining: int):
    outcome = await asyncio.to_thread(
        _check_publication_one_sync, publication_id,
    )
    if outcome == "running" and attempts_remaining > 0:
        await check_publication_status_task.configure(
            schedule_in={"seconds": CHECK_INTERVAL_SECONDS},
        ).defer_async(
            publication_id=publication_id,
            attempts_remaining=attempts_remaining - 1,
        )
        return outcome
    if outcome == "running":
        # Poll chain exhausted. Drop into the timeout handler so the
        # phone gets stopped and the lease released — otherwise the
        # reclaim cron just restarts the cycle and the phone keeps
        # billing.
        outcome = await asyncio.to_thread(
            _mark_publication_timed_out_sync, publication_id,
        )
    if outcome in ("success", "failure", "timeout"):
        if outcome in ("failure", "timeout"):
            retry_at = await asyncio.to_thread(
                _schedule_publication_retry_sync, publication_id,
            )
            if retry_at is not None:
                await _defer_auto_publish_scan_at(retry_at)
        # Account just freed up. Wake auto_publish_scan now instead of
        # waiting up to an hour for the next cron tick. queueing_lock on
        # the scan task dedupes burst defers when several checks land
        # in the same window — AlreadyEnqueued just means another wake
        # is already in flight, which is exactly what we want.
        from app.tasks.auto_publish import wake_auto_publish_scan
        await wake_auto_publish_scan()
    return outcome


def _list_stranded_publish_sync() -> list[int]:
    """Stranded publishes via the legacy lease column. Phase 1 Step 5
    keeps this signature for backward compat with existing tests."""
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT p.id "
                "FROM publications p "
                "JOIN accounts a ON a.id = p.account_id "
                "LEFT JOIN phone_sessions ps "
                "  ON ps.account_id = a.id "
                "  AND ps.stopped_at IS NULL "
                "WHERE p.result = 'INITIATED' "
                "  AND p.geelark_task_id IS NOT NULL "
                "  AND p.geelark_task_id <> 'pending' "
                "  AND ("
                "    ps.budget_deadline_at < :now "
                "    OR ps.id IS NULL"
                "  )"
            ),
            {"now": datetime.utcnow()},
        ).fetchall()
    return [r[0] for r in rows]


# A publication should spend seconds-to-minutes in QUEUED (defer →
# worker picks up → submit flips it to INITIATED or PhoneBusy unclaims
# it). Anything QUEUED this long with no in-flight session has lost its
# job — defer crashed mid-scan, or the stalled sweeper failed out a
# non-idempotent submit job. Stuck QUEUED rows are worse than stuck
# INITIATED ones: account_has_inflight counts them, freezing the
# account's publishing and engagement until someone resets the row.
QUEUED_ORPHAN_MINUTES = 30


def _unclaim_orphaned_queued_sync() -> int:
    from app.repositories import publications as pub_repo
    from app.services.posting_service import TASK_ID_PLACEHOLDER
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT p.id, p.geelark_task_id "
                "FROM publications p "
                "LEFT JOIN phone_sessions ps "
                "  ON ps.account_id = p.account_id "
                "  AND ps.stopped_at IS NULL "
                "WHERE p.result = 'QUEUED' "
                "  AND ps.id IS NULL "
                "  AND (p.last_checked_at IS NULL "
                "       OR p.last_checked_at < :cutoff)"
            ),
            {"cutoff": datetime.utcnow()
                - timedelta(minutes=QUEUED_ORPHAN_MINUTES)},
        ).fetchall()
        for r in rows:
            if r[1] == TASK_ID_PLACEHOLDER:
                # A submit crashed after persisting intent but before
                # mark_initiated — a GeeLark task may exist and may have
                # posted. Re-planning would risk a duplicate post, so
                # park the row as FAILURE for manual verification. The
                # attempt source is deliberately NOT in
                # RETRIABLE_FAILURE_SOURCES: the retry pipeline must
                # never resubmit an unknown-outcome row on its own.
                pub_repo.record_attempt(
                    session, r[0],
                    stage="submit",
                    source="manual_review",
                    message="submit interrupted mid-flight; verify on "
                            "GeeLark before retrying",
                )
                pub_repo.set_result(
                    session, r[0], "FAILURE",
                    "submit interrupted mid-flight; verify on GeeLark "
                    "before retrying")
            else:
                pub_repo.unclaim(session, r[0])
        return len(rows)


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(queue="default", name="reclaim_in_flight_publish",
                        queueing_lock="reclaim_in_flight_publish")
async def reclaim_in_flight_publish_task(timestamp: int) -> str:
    stranded = await asyncio.to_thread(_list_stranded_publish_sync)
    for pub_id in stranded:
        await check_publication_status_task.configure(
            schedule_in={"seconds": 0},
        ).defer_async(publication_id=pub_id, attempts_remaining=MAX_CHECK_RETRIES)
    orphans = await asyncio.to_thread(_unclaim_orphaned_queued_sync)
    return (f"reclaim_in_flight_publish: resumed {len(stranded)}, "
            f"unclaimed {orphans} orphaned QUEUED")
