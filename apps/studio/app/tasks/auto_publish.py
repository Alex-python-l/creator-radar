"""Periodic scan that finds PENDING publications, claims them with
plan_assignments (PENDING -> QUEUED + account_id), and defers one
publish_publication_task per pairing. Cron: top of every hour.

The sync main body runs in a worker thread via asyncio.to_thread so a
slow GeeLark API call inside check_initiated/plan_assignments cannot
block the procrastinate event loop. Modeled on
app/tasks/idle_phone_sweeper.py. Before the refactor (2026-06-11), this
task was an `async def` that called the sync services directly,
freezing the loop for ~90s/run and starving every other periodic task
    (reclaim_* and idle_phone_sweeper) on the same tick."""
import asyncio
import logging
import time
import threading
from datetime import datetime

from procrastinate.exceptions import AlreadyEnqueued, AppNotOpen

from app.tasks import procrastinate_app
from app.tasks.publish import (
    _defer_auto_publish_scan_at,
    publish_publication_task,
)
from app.db import session_scope

logger = logging.getLogger(__name__)

AUTO_PUBLISH_SCAN_LIMIT_PER_PLATFORM = 50
_SCAN_LOCK = threading.Lock()


def _workspace_session(workspace_id: int):
    try:
        return session_scope(workspace_id)
    except TypeError:
        return session_scope()


def _auto_publish_scan_one_sync(workspace_id: int) -> tuple[list[int], datetime | None]:
    """Synchronous main body.

    Returns:
        (pub_ids, next_ready)
        - pub_ids: publication ids to defer to publish_publication_task.
        - next_ready: if no pub_ids found, the soonest time a cooled-down
          account would be eligible; else None.
    """
    from app.services.platforms import PLATFORMS
    from app.services import geelark_service, posting_service
    from app.repositories import settings as srepo

    with _workspace_session(workspace_id) as session:
        posting_service.check_initiated(
            session, geelark_service.workspace_client(session, workspace_id),
        )

    pub_ids: list[int] = []
    cooldown_hours = 2.0
    # post_cooldown_hours is a single global setting; reading it inside
    # the loop keeps each iteration in sync with the DB, and the value
    # surviving the loop is reused for next_publish_scan_ready_at below.
    for platform in PLATFORMS:
        with _workspace_session(workspace_id) as session:
            cooldown_hours = float(
                srepo.get_all(session).get("post_cooldown_hours") or 2
            )
            pub_ids.extend(posting_service.plan_assignments(
                session, platform=platform, cooldown_hours=cooldown_hours,
                limit=AUTO_PUBLISH_SCAN_LIMIT_PER_PLATFORM,
            ))

    next_ready: datetime | None = None
    if not pub_ids:
        with _workspace_session(workspace_id) as session:
            next_ready = posting_service.next_publish_scan_ready_at(
                session, cooldown_hours=cooldown_hours,
                limit_per_platform=AUTO_PUBLISH_SCAN_LIMIT_PER_PLATFORM,
            )
    return pub_ids, next_ready


def _auto_publish_scan_sync() -> tuple[list[int], datetime | None]:
    from app.workspace_secrets import list_configured_workspaces

    with session_scope() as session:
        workspace_ids = list_configured_workspaces(session, "GEELARK_TOKEN")
    all_ids: list[int] = []
    next_times: list[datetime] = []
    for workspace_id in workspace_ids:
        ids, next_ready = _auto_publish_scan_one_sync(workspace_id)
        all_ids.extend(ids)
        if next_ready is not None:
            next_times.append(next_ready)
    return all_ids, min(next_times) if next_times else None


def _unclaim_publication_sync(publication_id: int) -> None:
    from app.repositories import publications as pub_repo

    with session_scope() as session:
        pub_repo.unclaim(session, publication_id)


@procrastinate_app.periodic(cron="0 * * * *")
@procrastinate_app.task(queue="default", name="auto_publish_scan",
                         queueing_lock="auto_publish_scan")
async def auto_publish_scan_task(timestamp: int):
    if not _SCAN_LOCK.acquire(blocking=False):
        # This tick may be a precisely-timed retry wake-up
        # (_defer_auto_publish_scan_at) — silently dropping it stalls the
        # woken publication until the next hourly cron. Re-defer ~90s out
        # instead; by then the running scan has almost always finished.
        # The retry_{epoch} queueing_lock dedupes same-second bursts.
        from datetime import timedelta
        try:
            await _defer_auto_publish_scan_at(
                datetime.utcnow() + timedelta(seconds=90))
        except Exception:
            pass  # worst case we fall back to the hourly cron
        return "auto_publish_scan: already running, re-deferred +90s"
    try:
        pub_ids, next_ready = await asyncio.to_thread(_auto_publish_scan_sync)
        for i, pid in enumerate(pub_ids):
            try:
                await publish_publication_task.defer_async(publication_id=pid)
            except Exception:
                # Every publication from pid onward is QUEUED with no job
                # to carry it — unclaim them ALL, not just the current
                # one. A QUEUED row without a job is invisible to every
                # sweep (scan reads PENDING, reclaim reads INITIATED)
                # and its account_has_inflight hit blocks the account's
                # publishing AND engagement indefinitely.
                for orphan in pub_ids[i:]:
                    try:
                        await asyncio.to_thread(
                            _unclaim_publication_sync, orphan)
                    except Exception:
                        logger.exception(
                            "auto_publish_scan: unclaim failed for "
                            "publication %s", orphan)
                raise
        if not pub_ids and next_ready is not None:
            await _defer_auto_publish_scan_at(next_ready)
        return f"{len(pub_ids)} publication(s) deferred"
    finally:
        _SCAN_LOCK.release()


async def wake_auto_publish_scan() -> bool:
    """Queue one immediate scan, deduped by auto_publish_scan's queueing lock."""
    async def defer_once() -> bool:
        try:
            await auto_publish_scan_task.defer_async(timestamp=int(time.time()))
        except AlreadyEnqueued:
            return True
        return True

    try:
        return await defer_once()
    except AppNotOpen:
        try:
            await procrastinate_app.connector.open_async()
            return await defer_once()
        except Exception:
            return False
    except Exception:
        return False
