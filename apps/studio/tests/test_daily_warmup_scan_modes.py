"""daily_warmup_scan must cover both warmup modes.

count-mode accounts whose warmed_up=0 need predefer too — the manual
Warmup endpoint that used to trigger them was removed 2026-06-08, so the
periodic scan is now the only path.
"""
import asyncio
from datetime import datetime, timedelta

from app.repositories import accounts as acc_repo


def test_scan_picks_up_count_mode_unwarmed_account(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    """A count-mode account that hasn't hit target_warmup_count must be
    scheduled. Without this the only place that deferred start_warmup for
    count-mode accounts was the deleted manual endpoint."""
    from app.tasks.daily_warmup import daily_warmup_scan
    aid = warmup_eligible_account_id
    acc_repo.update(db, aid, {
        "warmup_mode": "count",
        "warmed_up": 0,
        "last_warm_at": None,
    })
    summary = asyncio.run(daily_warmup_scan(timestamp=0))
    assert "queued 1" in summary
    jobs = in_memory_procrastinate.jobs
    start_jobs = [j for j in jobs.values() if j["task_name"] == "start_warmup"]
    assert len(start_jobs) == 1
    assert start_jobs[0]["args"]["account_id"] == aid


def test_scan_skips_count_mode_already_warmed_account(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    """A count-mode account that already reached target_warmup_count
    (warmed_up=1) must NOT be re-scheduled — start_warmup_one would just
    skip it anyway, but filtering at the scan layer keeps the queue clean.
    """
    from app.tasks.daily_warmup import daily_warmup_scan
    aid = warmup_eligible_account_id
    acc_repo.update(db, aid, {
        "warmup_mode": "count",
        "warmed_up": 1,
        "last_warm_at": datetime.utcnow() - timedelta(hours=12),
    })
    summary = asyncio.run(daily_warmup_scan(timestamp=0))
    assert "queued 0" in summary
