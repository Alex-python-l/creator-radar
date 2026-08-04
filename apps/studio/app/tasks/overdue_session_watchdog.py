"""Phone-session watchdog — Phase 1 Step 4 + Phase 4b.

Single Procrastinate cron that runs two scans on every tick:

1. **Overdue (Step 4)** — phone_sessions whose budget_deadline_at
   expired while still in flight (stopped_at IS NULL). For each:
     - mark session TIMEOUT via phone_runtime.mark_timeout
     - best-effort stop the cloud phone
     - log the bookkeeping fields so the audit page shows what happened

2. **External detection (Phase 4b)** — phones running on GeeLark that
   have no active phone_session. Stop them immediately. Operators who
   need more time should use the explicit manual hold flow.

Why this is separate from idle_phone_sweeper (Step 3):
  - sweeper stops phones with NO active session AND no lease.
    Conservative by design — the dual-read rule means an in-flight
    session protects the phone.
  - watchdog is the timer that finalizes overdue sessions AND the
    detector that immediately stops unowned external runs.

Why this is separate from per-service lease-expiry short-circuit
(advance_warmup_one / check_publication_one / check_engagement_run):
  - that path only fires when something wakes up — a poll task, a
    manual scan. Worker crash, procrastinate stall, queue backup →
    nothing wakes up. The watchdog is the backstop that fires anyway.

Why no GeeLark task cancel:
  - Phase 0a still hasn't verified /open/v1/task/cancel semantics.
    Until it does, stop_phones halts billing but the underlying RPA
    might still mark itself completed/failed on GeeLark's side.
    Phase 2 wires cancel once the probe lands.
"""
import asyncio
import logging

from sqlalchemy import text

from app.db import session_scope
from app.repositories import phone_sessions as ps_repo
from app.services import geelark_service, phone_runtime
from app.tasks import procrastinate_app

logger = logging.getLogger(__name__)

# Bounded scan per tick. If real load ever exceeds this on a single
# tick, the next */5 cron picks up the rest. List_overdue orders by
# budget_deadline_at ASC so the longest-overdue ones win the race.
_WATCHDOG_BATCH_SIZE = 100
_STATUS_BATCH_SIZE = 100

# GeeLark phone status: 0=Started, 1=Starting, 2=Shut down, 3=Expired.
_RUNNING_STATUSES = (0, 1)


def _sweep_overdue_sessions_sync(client, workspace_id: int | None = None) -> tuple[int, list[int]]:
    """Returns (timed_out_count, session_ids).

    External-detected sessions are included here. They are visibility
    markers, but still get a short runtime budget so accidental external
    starts do not bill indefinitely.
    """
    with session_scope(workspace_id) as session:
        from datetime import datetime
        rows = session.execute(
            text(
                "SELECT * FROM phone_sessions "
                "WHERE stopped_at IS NULL "
                "  AND budget_deadline_at < :now "
                "ORDER BY budget_deadline_at ASC "
                "LIMIT :lim"
            ),
            {"now": datetime.utcnow(), "lim": _WATCHDOG_BATCH_SIZE},
        ).all()
        # Materialize rows out of the session scope so the loop below
        # can open its own transactions without sharing this one.
        overdue = [
            {
                "id": r.id, "account_id": r.account_id, "env_id": r.env_id,
                "source": r.source, "owner_type": r.owner_type,
                "owner_id": r.owner_id,
                "geelark_task_id": r.geelark_task_id,
                "budget_deadline_at": r.budget_deadline_at,
            }
            for r in rows
        ]
    if not overdue:
        return 0, []

    timed_out: list[int] = []
    for s in overdue:
        sid = s["id"]
        env_id = s["env_id"]
        reason = (
            f"watchdog: session {sid} budget exceeded "
            f"(deadline {s['budget_deadline_at']})"
        )
        # Physical teardown FIRST, lease release LAST. mark_timeout
        # commits stopped_at, which releases the account lease — the
        # instant that lands, a queued submit retry can grab a fresh
        # session on the same env_id. A stop_phones issued after that
        # would kill the new session's phone mid-RPA, so the phone must
        # already be down (and its RPA task cancelled) before the lease
        # is ever released. Both teardown calls stay best-effort; a
        # failed stop is retried by the next watchdog tick since the
        # session then stays past-deadline and gets re-listed.
        gtid = s.get("geelark_task_id") if isinstance(s, dict) else None
        if gtid:
            try:
                client.cancel_tasks([gtid])
            except Exception as exc:
                logger.warning(
                    "watchdog: cancel_tasks failed "
                    "task=%s sid=%s: %s", gtid, sid, exc,
                )
        try:
            client.stop_phones([env_id])
        except Exception as exc:
            logger.warning(
                "watchdog: stop_phones failed env_id=%s sid=%s: %s",
                env_id, sid, exc,
            )
        try:
            with session_scope(workspace_id) as ses2:
                phone_runtime.mark_timeout(ses2, sid, reason=reason)
        except Exception as exc:
            logger.warning(
                "watchdog: mark_timeout failed sid=%s: %s", sid, exc,
            )
            continue
        logger.warning(
            "watchdog: timed out session %s "
            "(account=%s, env_id=%s, source=%s, owner=%s/%s)",
            sid, s["account_id"], env_id, s["source"],
            s["owner_type"], s["owner_id"],
        )
        timed_out.append(sid)
    return len(timed_out), timed_out


def _detect_external_phones_sync(client, workspace_id: int | None = None) -> tuple[int, list[str]]:
    """Stop GeeLark phones currently running with no active local
    phone_session.

    Returns (stopped_count, env_ids).
    """
    with session_scope(workspace_id) as session:
        rows = session.execute(text(
            "SELECT a.id AS account_id, a.profile_name, "
            "       a.geelark_phone_id, "
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

    running_ids: list[str] = []
    for i in range(0, len(phone_ids), _STATUS_BATCH_SIZE):
        chunk = phone_ids[i:i + _STATUS_BATCH_SIZE]
        try:
            data = client.get_phone_status(chunk)
        except Exception as exc:
            logger.warning(
                "watchdog: external_detect get_phone_status failed: %s",
                exc,
            )
            continue
        for d in (data.get("successDetails") or []):
            if d.get("status") in _RUNNING_STATUSES:
                running_ids.append(d["id"])

    external: list[str] = []
    for pid in running_ids:
        r = by_pid.get(pid)
        if r is None or r.active_session_id is not None:
            # Either not one of ours, or already accounted for by an
            # in-flight session (warmup / publish / engagement / manual).
            continue
        external.append(pid)

    if external:
        # Same fresh-snapshot re-check as idle_phone_sweeper: the scan
        # snapshot is minutes stale after the batched status calls, and
        # a session started in the gap must not get its phone stopped.
        with session_scope(workspace_id) as session:
            claimed = {
                r[0] for r in session.execute(text(
                    "SELECT a.geelark_phone_id "
                    "FROM accounts a "
                    "JOIN phone_sessions ps ON ps.account_id = a.id "
                    "  AND ps.stopped_at IS NULL "
                    "WHERE a.geelark_phone_id = ANY(:pids)"
                ), {"pids": external}).fetchall()
            }
        if claimed:
            logger.info(
                "watchdog: external_detect skipping %d freshly claimed "
                "phone(s): %s", len(claimed), sorted(claimed))
            external = [pid for pid in external if pid not in claimed]

    stopped: list[str] = []
    for i in range(0, len(external), _STATUS_BATCH_SIZE):
        chunk = external[i:i + _STATUS_BATCH_SIZE]
        if not chunk:
            continue
        try:
            client.stop_phones(chunk)
        except Exception as exc:
            logger.warning(
                "watchdog: external_detect stop_phones failed ids=%s: %s",
                chunk, exc,
            )
            continue
        stopped.extend(chunk)
        for pid in chunk:
            r = by_pid.get(pid)
            if r is None:
                continue
            logger.warning(
                "watchdog: stopped external running phone %s "
                "(account=%s, profile=%s)",
                pid, r.account_id, r.profile_name,
            )
    return len(stopped), stopped


@procrastinate_app.periodic(cron="*/5 * * * *")
@procrastinate_app.task(
    queue="default", name="overdue_session_watchdog",
    queueing_lock="overdue_session_watchdog",
)
async def overdue_session_watchdog_task(timestamp: int) -> str:
    def _both():
        from app.workspace_secrets import list_configured_workspaces
        with session_scope() as session:
            workspace_ids = list_configured_workspaces(session, "GEELARK_TOKEN")
        out_count, ext_count = 0, 0
        sids: list[int] = []
        eids: list[str] = []
        for workspace_id in workspace_ids:
            with session_scope(workspace_id) as session:
                client = geelark_service.workspace_client(session, workspace_id)
            oc, osids = _sweep_overdue_sessions_sync(client, workspace_id)
            ec, oeids = _detect_external_phones_sync(client, workspace_id)
            out_count += oc
            ext_count += ec
            sids.extend(osids)
            eids.extend(oeids)
        return out_count, sids, ext_count, eids

    out_count, sids, ext_count, eids = await asyncio.to_thread(_both)
    return (
        f"watchdog: timed out {out_count} sessions {sids}; "
        f"stopped {ext_count} external {eids}"
    )
