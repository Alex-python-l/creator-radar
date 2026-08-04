"""Defense-in-depth cron: stop any GeeLark phone running with no active
business reason.

The per-code-path `stop_phones` calls in warmup_service / posting_service
/ engagement_service are the primary "task done → phone off" mechanism.
This sweeper is the belt-and-suspenders safety net: if any code path
ever forgets to stop a phone (regression, new task type, race), the
phone gets caught within ~5 minutes.

Decision rule (Phase 1 Step 5 — single-source after column drop): a
phone is idle iff GeeLark reports it as Started/Starting AND there is
no in-flight phone_sessions row (stopped_at IS NULL).

The overdue-session watchdog (Phase 1 Step 4) handles in-flight
sessions that legitimately exceed their budget by finalizing them
first, after which the sweeper picks them up cleanly.
"""
import asyncio
import logging

from sqlalchemy import text

from app.db import session_scope
from app.services import geelark_service
from app.tasks import procrastinate_app

logger = logging.getLogger(__name__)

# GeeLark phone status: 0=Started, 1=Starting, 2=Shut down, 3=Expired.
_RUNNING_STATUSES = (0, 1)
_STATUS_BATCH_SIZE = 100


def _sweep_idle_phones_one_sync(workspace_id: int) -> tuple[int, list[str]]:
    """Returns (stopped_count, stopped_phone_ids)."""
    with session_scope(workspace_id) as session:
        rows = session.execute(text(
            "SELECT a.id, a.profile_name, a.geelark_phone_id, "
            "       ps.id AS active_session_id "
            "FROM accounts a "
            "LEFT JOIN phone_sessions ps "
            "  ON ps.account_id = a.id "
            "  AND ps.stopped_at IS NULL "
            "WHERE a.geelark_phone_id IS NOT NULL"
        )).fetchall()
    if not rows:
        return 0, []

    by_pid = {r.geelark_phone_id: r for r in rows}
    phone_ids = list(by_pid.keys())

    with session_scope(workspace_id) as session:
        client = geelark_service.workspace_client(session, workspace_id)
    running_ids: list[str] = []
    for i in range(0, len(phone_ids), _STATUS_BATCH_SIZE):
        chunk = phone_ids[i:i + _STATUS_BATCH_SIZE]
        try:
            data = client.get_phone_status(chunk)
        except Exception as exc:
            logger.warning(
                "idle_phone_sweeper: get_phone_status batch failed: %s", exc)
            continue
        for d in (data.get("successDetails") or []):
            if d.get("status") in _RUNNING_STATUSES:
                running_ids.append(d["id"])

    if not running_ids:
        return 0, []

    idle: list[str] = []
    for pid in running_ids:
        r = by_pid.get(pid)
        if r is None:
            continue  # not one of our phones
        # phone_sessions is the source of truth: stop a running phone
        # only when no in-flight session claims it.
        if r.active_session_id is None:
            idle.append(pid)
            logger.warning(
                "idle_phone_sweeper: stopping idle phone %s "
                "(account %s '%s', no active session)",
                pid, r.id, r.profile_name)

    if not idle:
        return 0, []

    # Re-check against a FRESH snapshot right before pulling the
    # trigger. The snapshot at the top is minutes old by now (batched
    # status HTTP round-trips) — a session started in that gap would
    # get its phone killed mid-flight. This shrinks the
    # decide-vs-act window from minutes to milliseconds.
    with session_scope(workspace_id) as session:
        claimed = {
            r[0] for r in session.execute(text(
                "SELECT a.geelark_phone_id "
                "FROM accounts a "
                "JOIN phone_sessions ps ON ps.account_id = a.id "
                "  AND ps.stopped_at IS NULL "
                "WHERE a.geelark_phone_id = ANY(:pids)"
            ), {"pids": idle}).fetchall()
        }
    if claimed:
        logger.info(
            "idle_phone_sweeper: %d phone(s) claimed a session since the "
            "scan snapshot, skipping: %s", len(claimed), sorted(claimed))
        idle = [pid for pid in idle if pid not in claimed]
    if not idle:
        return 0, []

    try:
        client.stop_phones(idle)
    except Exception as exc:
        logger.warning("idle_phone_sweeper: stop_phones failed: %s", exc)
        return 0, []

    return len(idle), idle


def _sweep_idle_phones_sync() -> tuple[int, list[str]]:
    from app.workspace_secrets import list_configured_workspaces
    with session_scope() as session:
        workspace_ids = list_configured_workspaces(session, "GEELARK_TOKEN")
    stopped = 0
    ids: list[str] = []
    for workspace_id in workspace_ids:
        count, workspace_ids_stopped = _sweep_idle_phones_one_sync(workspace_id)
        stopped += count
        ids.extend(workspace_ids_stopped)
    return stopped, ids


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(queue="default", name="idle_phone_sweeper",
                        queueing_lock="idle_phone_sweeper")
async def idle_phone_sweeper_task(timestamp: int) -> str:
    stopped, ids = await asyncio.to_thread(_sweep_idle_phones_sync)
    return f"idle_phone_sweeper: stopped {stopped} idle phones {ids}"
