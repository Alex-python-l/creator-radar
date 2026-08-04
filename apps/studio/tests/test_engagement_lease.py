"""Engagement pipeline mirrors publish lease behavior."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.repositories import accounts as acc_repo
from app.services import phone_runtime


def _fake_client(*, status=2):
    class C:
        def start_phones(self, ids): pass
        def stop_phones(self, ids): pass
        def create_tiktok_ai_comment_task(self, *a, **kw): return "geelark-eng-1"
        def query_tasks(self, ids): return [{"status": status}]
        def get_task_detail(self, tid): return {"status": status}
    return C()


@pytest.fixture
def warmed_account_id(db):
    """A fully warmed, TikTok-logged-in account suitable for engagement."""
    aid = acc_repo.create(db, {
        "profile_name": "warmed-engage-test",
        "tiktok_logged_in": 1,
        "geelark_phone_id": "phone-engage-X",
        "warmup_mode": "daily",
        "warmed_up": 1,
    })
    return aid


@pytest.fixture
def sample_engagement_run(db, warmed_account_id):
    from app.models import EngagementTask, EngagementRun
    t = EngagementTask(
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@test/video/123"],
        target_publication_ids=[],
        comment_mode="geelark_ai",
        comment_probability=100,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    r = EngagementRun(
        engagement_task_id=t.id,
        account_id=warmed_account_id,
        target_link="https://www.tiktok.com/@test/video/123",
        action="tiktok_ai_comment",
        result="PENDING",
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"run_id": r.id, "account_id": warmed_account_id}


def _phone_session(db, account_id: int):
    return db.execute(
        text(
            "SELECT id, status, final_status, geelark_task_id, source, "
            "owner_type, owner_id, stopped_at, stop_reason "
            "FROM phone_sessions WHERE account_id = :aid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"aid": account_id},
    ).fetchone()


def test_submit_engagement_raises_phone_busy_when_session_active(
        db, sample_engagement_run):
    from app.services import engagement_service
    rid = sample_engagement_run["run_id"]
    aid = sample_engagement_run["account_id"]
    a = acc_repo.get(db, aid)
    phone_runtime.start_session(
        db, account_id=aid, env_id=a["geelark_phone_id"],
        source="manual", owner_type="account", owner_id=aid,
        budget_seconds=60, created_by="test",
    )
    with pytest.raises(engagement_service.PhoneBusy):
        engagement_service.submit_run_to_geelark(db, _fake_client(), run_id=rid)


def test_submit_engagement_creates_phone_session(
        db, sample_engagement_run):
    from app.services import engagement_service
    rid = sample_engagement_run["run_id"]
    aid = sample_engagement_run["account_id"]
    task_id = engagement_service.submit_run_to_geelark(
        db, _fake_client(), run_id=rid)

    sess = _phone_session(db, aid)
    assert sess is not None
    assert sess.status == "ACTIVE"
    assert sess.final_status is None
    assert sess.stopped_at is None
    assert sess.source == "engagement"
    assert sess.owner_type == "engagement_run"
    assert sess.owner_id == rid
    assert sess.geelark_task_id == task_id


def test_check_engagement_finalizes_phone_session_on_success(
        db, sample_engagement_run):
    from app.services import engagement_service
    rid = sample_engagement_run["run_id"]
    aid = sample_engagement_run["account_id"]
    engagement_service.submit_run_to_geelark(
        db, _fake_client(), run_id=rid)

    class C:
        def query_tasks(self, ids): return [{"status": 3}]
        def get_task_detail(self, tid): return {"status": 3, "resultImages": []}
        def stop_phones(self, ids): pass
    engagement_service.check_engagement_run(db, C(), rid)

    sess = _phone_session(db, aid)
    assert sess.status == "STOPPED"
    assert sess.final_status == "STOPPED"
    assert sess.stopped_at is not None


def test_check_engagement_finalizes_phone_session_on_failure(
        db, sample_engagement_run):
    from app.services import engagement_service
    rid = sample_engagement_run["run_id"]
    aid = sample_engagement_run["account_id"]
    engagement_service.submit_run_to_geelark(
        db, _fake_client(), run_id=rid)

    class C:
        def query_tasks(self, ids): return [{"status": 4, "failDesc": "boom"}]
        def get_task_detail(self, tid):
            return {"status": 4, "failDesc": "boom", "resultImages": []}
        def stop_phones(self, ids): pass
    engagement_service.check_engagement_run(db, C(), rid)

    sess = _phone_session(db, aid)
    assert sess.status == "STOPPED"
    assert sess.final_status == "FAILED"
    assert sess.stop_reason


def test_check_engagement_marks_phone_session_timeout_on_budget_expiry(
        db, sample_engagement_run):
    from app.services import engagement_service
    rid = sample_engagement_run["run_id"]
    aid = sample_engagement_run["account_id"]
    engagement_service.submit_run_to_geelark(
        db, _fake_client(), run_id=rid)
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :i AND stopped_at IS NULL"
        ),
        {"p": datetime.utcnow() - timedelta(minutes=1), "i": aid},
    )
    db.commit()

    class C:
        def query_tasks(self, ids): return [{"status": 2}]
        def stop_phones(self, ids): pass
    engagement_service.check_engagement_run(db, C(), rid)

    sess = _phone_session(db, aid)
    assert sess.status == "TIMEOUT"
    assert sess.final_status == "TIMEOUT"


def test_reclaim_in_flight_engagement_defers_check(
        in_memory_procrastinate, db, sample_engagement_run):
    import asyncio
    from app.tasks.engagement import reclaim_in_flight_engagement_task
    from app.repositories import engagement as eng_repo
    rid = sample_engagement_run["run_id"]
    aid = sample_engagement_run["account_id"]
    a = acc_repo.get(db, aid)
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id=a["geelark_phone_id"],
        source="engagement", owner_type="engagement_run", owner_id=rid,
        budget_seconds=600, created_by="test",
    )
    phone_runtime.attach_geelark_task(db, sid, "stranded-tid")
    eng_repo.mark_initiated(db, rid, task_id="stranded-tid",
                            schedule_at=int(__import__("time").time()),
                            notes="set up")
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE id = :sid"),
        {"p": datetime.utcnow() - timedelta(minutes=30), "sid": sid},
    )
    db.commit()
    out = asyncio.run(reclaim_in_flight_engagement_task(timestamp=0))
    assert "1" in out
    jobs = in_memory_procrastinate.jobs
    check_jobs = [j for j in jobs.values() if j["task_name"] == "check_engagement_run"]
    assert len(check_jobs) == 1
