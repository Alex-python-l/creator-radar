"""Procrastinate queue self-maintenance.

Two periodic tasks keep procrastinate_jobs lean and self-healing:

  queue_cleanup         cron 30 4 * * *   — delete_old_jobs: succeeded/
                        cancelled after 7 days, failed/aborted after 30.
                        Without it the table grew ~590k rows / 380 MB in
                        five weeks of production.
  stalled_jobs_sweeper  cron */30 * * * * — heartbeat-based stalled-job
                        retry. A worker that dies (or freezes, as in the
                        2026-06-10 pool-exhaustion incident) leaves its
                        jobs in `doing` forever; any queueing_lock they
                        hold blocks that task from ever enqueuing again
                        (start_warmup_97 sat like that for three weeks).

The 300s heartbeat threshold is far above the worker's ~10s heartbeat
interval, so a deploy restart (seconds of downtime) never trips it, while
a genuinely dead worker is reclaimed within ~35 minutes.

Retry is NOT safe for every task. check_*/scan/reclaim tasks are
idempotent polls, and start_warmup re-checks its lease + chain
ownership. But the two submit tasks create GeeLark RPA tasks with no
idempotency key: a worker killed between the GeeLark create call and
the mark_initiated commit leaves the row in QUEUED, so a blind re-run
sails through the state gate and posts the same video/comment twice.
Those jobs are failed out instead — their rows are re-planned by the
QUEUED reaper + auto_publish_scan / outbox, which route through the
normal dedup gates.
"""
import logging

from procrastinate import jobs as procrastinate_jobs

from app.tasks import procrastinate_app

logger = logging.getLogger(__name__)

KEEP_SUCCEEDED_HOURS = 7 * 24
KEEP_FAILED_HOURS = 30 * 24
STALLED_HEARTBEAT_SECONDS = 300

# Tasks whose re-run repeats a PAID external side effect the dead worker
# already performed:
#   publish_publication / submit_engagement_run — create GeeLark RPA
#     tasks with no idempotency key; re-running past a crash between
#     task-create and mark_initiated posts the same video/comment twice.
#   discover_tiktok — BrightData charges per /trigger (~$35-40 for the
#     full keyword set); the trigger happens at the top of a 1-2h
#     poll+ingest job, so a blind re-run re-buys every snapshot.
#   extract_understanding — re-pays the Gemini upload+generation; cheap
#     but pointless, and the outbox reaper re-plans it anyway.
NON_IDEMPOTENT_TASKS = frozenset({
    "publish_publication",
    "submit_engagement_run",
    "discover_tiktok",
    "extract_understanding",
})


@procrastinate_app.periodic(cron="30 4 * * *")
@procrastinate_app.task(queue="default", name="queue_cleanup",
                        queueing_lock="queue_cleanup")
async def queue_cleanup_task(timestamp: int) -> str:
    manager = procrastinate_app.job_manager
    await manager.delete_old_jobs(
        nb_hours=KEEP_SUCCEEDED_HOURS, include_cancelled=True)
    await manager.delete_old_jobs(
        nb_hours=KEEP_FAILED_HOURS, include_failed=True,
        include_cancelled=True, include_aborted=True)
    return (f"queue_cleanup: dropped succeeded/cancelled >{KEEP_SUCCEEDED_HOURS}h "
            f"and failed/aborted >{KEEP_FAILED_HOURS}h")


@procrastinate_app.periodic(cron="*/30 * * * *")
@procrastinate_app.task(queue="default", name="stalled_jobs_sweeper",
                        queueing_lock="stalled_jobs_sweeper")
async def stalled_jobs_sweeper_task(timestamp: int) -> str:
    manager = procrastinate_app.job_manager
    stalled = list(await manager.get_stalled_jobs(
        seconds_since_heartbeat=STALLED_HEARTBEAT_SECONDS))
    retried, failed_out = [], []
    for job in stalled:
        if job.task_name in NON_IDEMPOTENT_TASKS:
            await manager.finish_job(
                job, status=procrastinate_jobs.Status.FAILED,
                delete_job=False)
            failed_out.append((job.id, job.task_name))
            continue
        try:
            await manager.retry_job(job)
            retried.append((job.id, job.task_name))
        except Exception:
            # Typically a fresh periodic instance already queued 'todo'
            # under the same queueing_lock, so retrying would violate the
            # unique index. The successor owns the work — fail the zombie.
            await manager.finish_job(
                job, status=procrastinate_jobs.Status.FAILED,
                delete_job=False)
            failed_out.append((job.id, job.task_name))
    if stalled:
        logger.warning(
            "stalled_jobs_sweeper: retried %s, failed out %s",
            retried, failed_out)
    return (f"stalled_jobs_sweeper: retried {len(retried)}, "
            f"failed out {len(failed_out)}")
