"""Cron reclaim behaviour."""
from datetime import datetime, timedelta

import pytest

from app.repositories import accounts as acc_repo
from app.services import phone_runtime


def _backdate_session(db, account_id: int, minutes_overdue: int = 30) -> None:
    db.execute(
        __import__("sqlalchemy").text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :i AND stopped_at IS NULL"
        ),
        {"p": datetime.utcnow() - timedelta(minutes=minutes_overdue),
         "i": account_id},
    )
    db.commit()


def test_reclaim_clears_pending_sentinel(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    """T1↔T3 crash: in-flight warmup phone_session with no GeeLark task
    id attached. Reclaim must finalize it as FAILED."""
    import asyncio
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account_id
    a = acc_repo.get(db, aid)
    phone_runtime.start_session(
        db, account_id=aid, env_id=a["geelark_phone_id"],
        source="warmup", owner_type="account", owner_id=aid,
        budget_seconds=1200, created_by="warmup",
    )
    # No attach_geelark_task → geelark_task_id stays NULL → crash window.
    out = asyncio.run(warmup_tasks.reclaim_in_flight_warmup_task(timestamp=0))
    assert "cleared=1" in out


def test_reclaim_resumes_real_task(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    """Real GeeLark task id attached + budget expired. Reclaim must
    renew the budget and defer a check_warmup_status_task."""
    import asyncio
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account_id
    a = acc_repo.get(db, aid)
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id=a["geelark_phone_id"],
        source="warmup", owner_type="account", owner_id=aid,
        budget_seconds=1200, created_by="warmup",
    )
    phone_runtime.attach_geelark_task(db, sid, "real-task-xyz")
    _backdate_session(db, aid)
    out = asyncio.run(warmup_tasks.reclaim_in_flight_warmup_task(timestamp=0))
    assert "resumed=1" in out
    jobs = in_memory_procrastinate.jobs
    check_jobs = [j for j in jobs.values() if j["task_name"] == "check_warmup_status"]
    assert len(check_jobs) == 1
    assert check_jobs[0]["args"]["task_id"] == "real-task-xyz"
    assert check_jobs[0]["args"]["account_id"] == aid


def test_reclaim_ignores_active_session(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    """A non-overdue session is not stranded — reclaim must skip it."""
    import asyncio
    from app.tasks import warmup as warmup_tasks
    aid = warmup_eligible_account_id
    a = acc_repo.get(db, aid)
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id=a["geelark_phone_id"],
        source="warmup", owner_type="account", owner_id=aid,
        budget_seconds=1200, created_by="warmup",
    )
    phone_runtime.attach_geelark_task(db, sid, "real-task-xyz")
    # No backdate — session is fresh.
    out = asyncio.run(warmup_tasks.reclaim_in_flight_warmup_task(timestamp=0))
    assert "cleared=0" in out and "resumed=0" in out
