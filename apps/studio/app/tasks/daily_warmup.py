"""Periodic scan that re-defers start_warmup_task for any account that
still needs warmup (warmed_up=0) and whose phone lease is free.

Covers both modes:
  - daily: warmed_up is never set, so every daily account is always in scope
  - count: in scope until warmup_count reaches target_warmup_count, at which
    point start_warmup_one stamps warmed_up=1 and this scan stops picking it

cutoff (~86400/daily_warmup_count seconds) gates re-defer cadence the same
way for both modes. An account is eligible only when it has no
in-flight phone_session. Procrastinate locks are gone; mutual
exclusion lives entirely on phone_sessions.
"""
import random
from datetime import datetime, timedelta

from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import text

from app.db import session_scope
from app.repositories import settings as srepo
from app.tasks import procrastinate_app
from app.tasks.warmup import start_warmup_task

# Groups whose proxy supplier enforces 1-device-per-exit-IP. Scan must
# not enqueue a second account while another account on the same
# proxy_exit_ip is already active — empirically the second device
# collides and the warmup RPA fails (status 4). 14a4c6808b9eb supplier
# on 175.29.*/147.125.* exits.
PROXY_ISOLATION_GROUP_IDS = (18, 19)


@procrastinate_app.periodic(cron="*/10 * * * *")
@procrastinate_app.task(queue="default", name="daily_warmup_scan",
                        queueing_lock="daily_warmup_scan")
async def daily_warmup_scan(timestamp: int) -> str:
    with session_scope() as session:
        n = int(srepo.get_all(session).get("daily_warmup_count") or 4)
        interval_seconds = max(1, 86400 // max(n, 1))
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=interval_seconds)
        gids = PROXY_ISOLATION_GROUP_IDS
        gids_sql = (
            "(" + ",".join(str(int(g)) for g in gids) + ")" if gids else "(-1)"
        )
        # Single-read: phone_sessions is the source of truth for busy.
        # An account is eligible only if it has no in-flight session.
        rows = session.execute(
            text(
                "SELECT a.id FROM accounts a "
                "LEFT JOIN phone_sessions ps "
                "  ON ps.account_id = a.id "
                "  AND ps.stopped_at IS NULL "
                "WHERE (a.warmed_up = 0 OR a.warmup_mode = 'daily') "
                "AND a.tiktok_logged_in = 1 "
                "AND a.geelark_phone_id IS NOT NULL "
                "AND (a.last_warm_at IS NULL OR a.last_warm_at < :cutoff) "
                "AND ps.id IS NULL "
                f"AND (a.group_id NOT IN {gids_sql} "
                "     OR a.proxy_exit_ip IS NULL "
                "     OR NOT EXISTS ("
                "         SELECT 1 FROM phone_sessions ps2 "
                "         JOIN accounts a2 ON a2.id = ps2.account_id "
                "         WHERE ps2.stopped_at IS NULL "
                "           AND a2.proxy_exit_ip = a.proxy_exit_ip "
                "           AND a2.id <> a.id))"
            ),
            {"cutoff": cutoff},
        ).fetchall()
        candidate_ids = [r[0] for r in rows]

    deferred = 0
    for aid in candidate_ids:
        try:
            await start_warmup_task.configure(
                queueing_lock=f"start_warmup_{aid}",
                schedule_in={"seconds": random.randint(0, 1800)},
            ).defer_async(account_id=aid)
            deferred += 1
        except AlreadyEnqueued:
            pass
    return f"daily_warmup_scan: queued {deferred} of {len(candidate_ids)}"
