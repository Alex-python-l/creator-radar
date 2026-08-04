import pytest


@pytest.fixture
def fake_client(monkeypatch):
    from app.services import geelark_service

    class FakeC:
        def create_tiktok_ai_comment_task(self, env, links, **kwargs):
            return "gee-eng-1"
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3, "cost": 12}]
        def stop_phones(self, ids):
            return {}

    monkeypatch.setattr(geelark_service, "make_client", lambda: FakeC())
    return FakeC


def _make_run(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid = group_repo.create(session, name="Worker Engage")
    aid = acc_repo.create(session, {
        "profile_name": "M1",
        "geelark_phone_id": "ph-m1",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[aid],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="geelark_ai",
    )
    return result["task_id"], eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]


def _make_two_runs_same_account(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid = group_repo.create(session, name="Worker Engage Busy")
    aid = acc_repo.create(session, {
        "profile_name": "M2",
        "geelark_phone_id": "ph-m2",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[aid],
        target_links=[
            "https://www.tiktok.com/@owner/video/1234567890",
            "https://www.tiktok.com/@owner/video/1234567891",
        ],
        target_publication_ids=[],
        comment_probability=100,
    )
    runs = [
        eng_repo.list_runs_for_task(session, task_id)[0]
        for task_id in result["task_ids"]
    ]
    return result["task_ids"][0], runs[0]["id"], runs[1]["id"]


def _deferred_jobs(procrastinate_app):
    return list(procrastinate_app.connector.jobs.values())


def test_submit_engagement_run_task_defers_check(
        session, procrastinate_app, fake_client):
    import asyncio
    from app.repositories import engagement as eng_repo
    from app.tasks.engagement import submit_engagement_run_task

    task_id, run_id = _make_run(session)

    result = asyncio.run(submit_engagement_run_task(run_id=run_id))

    assert "INITIATED" in result
    assert eng_repo.get_run(session, run_id)["result"] == "INITIATED"
    pending = _deferred_jobs(procrastinate_app)
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert any("check_engagement_run" in n for n in names)


def test_check_engagement_run_task_marks_success(
        session, procrastinate_app, fake_client):
    import asyncio
    from app.repositories import engagement as eng_repo
    from app.tasks.engagement import check_engagement_run_task

    task_id, run_id = _make_run(session)
    eng_repo.mark_initiated(session, run_id, task_id="gee-eng-1",
                            schedule_at=123, notes="submitted")

    outcome = asyncio.run(check_engagement_run_task(
        run_id=run_id, attempts_remaining=5))

    assert outcome == "success"
    assert eng_repo.get_run(session, run_id)["result"] == "SUCCESS"


def test_check_engagement_run_task_times_out_and_stops_phone(
        session, procrastinate_app, monkeypatch):
    import asyncio
    from app.repositories import engagement as eng_repo
    from app.services import geelark_service
    from app.tasks.engagement import check_engagement_run_task

    stopped = []

    class RunningClient:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 2}]

        def stop_phones(self, ids):
            stopped.extend(ids)
            return {}

    monkeypatch.setattr(geelark_service, "make_client", lambda: RunningClient())
    task_id, run_id = _make_run(session)
    eng_repo.mark_initiated(session, run_id, task_id="gee-running",
                            schedule_at=123, notes="submitted")

    outcome = asyncio.run(check_engagement_run_task(
        run_id=run_id, attempts_remaining=0))
    row = eng_repo.get_run(session, run_id)

    assert outcome == "failure"
    assert row["result"] == "FAILURE"
    assert "timed out" in row["notes"]
    assert stopped == ["ph-m1"]


def test_submit_engagement_run_task_waits_without_burning_retry_budget(
        session, procrastinate_app, fake_client):
    import asyncio
    from app.repositories import engagement as eng_repo
    from app.tasks.engagement import submit_engagement_run_task

    task_id, first_run_id, second_run_id = _make_two_runs_same_account(session)
    eng_repo.mark_initiated(session, first_run_id, task_id="gee-inflight",
                            schedule_at=123, notes="submitted")

    result = asyncio.run(submit_engagement_run_task(
        run_id=second_run_id, attempts_remaining=2))

    assert result.startswith("QUEUED:")
    assert eng_repo.get_run(session, second_run_id)["result"] == "QUEUED"
    pending = _deferred_jobs(procrastinate_app)
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert any("submit_engagement_run" in n for n in names)
    assert any(
        j.get("args", {}).get("attempts_remaining") == 2
        for j in pending
    )


def test_submit_engagement_run_task_never_fails_expected_queue_wait(
        session, procrastinate_app, monkeypatch):
    import asyncio
    from app.repositories import engagement as eng_repo
    from app.services import geelark_service
    from app.tasks.engagement import submit_engagement_run_task

    stopped = []

    class BusyClient:
        def create_tiktok_ai_comment_task(self, env, links, **kwargs):
            return "should-not-submit"

        def stop_phones(self, ids):
            stopped.extend(ids)
            return {}

    monkeypatch.setattr(geelark_service, "make_client", lambda: BusyClient())
    task_id, first_run_id, second_run_id = _make_two_runs_same_account(session)
    eng_repo.mark_initiated(session, first_run_id, task_id="gee-inflight",
                            schedule_at=123, notes="submitted")

    result = asyncio.run(submit_engagement_run_task(
        run_id=second_run_id, attempts_remaining=0))
    row = eng_repo.get_run(session, second_run_id)

    assert result.startswith("QUEUED:")
    assert row["result"] == "QUEUED"
    assert stopped == []
    pending = _deferred_jobs(procrastinate_app)
    submits = [
        j for j in pending
        if "submit_engagement_run" in j.get("task_name", j.get("name", ""))
    ]
    assert len(submits) == 1
    assert submits[0]["args"]["attempts_remaining"] == 0


def test_submit_engagement_run_task_waits_for_proxy_recovery(
        session, procrastinate_app, monkeypatch):
    import asyncio
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.tasks.engagement import submit_engagement_run_task

    task_id, run_id = _make_run(session)
    account_id = eng_repo.get_run(session, run_id)["account_id"]
    acc_repo.update(session, account_id, {"proxy_status": "fail"})

    result = asyncio.run(submit_engagement_run_task(
        run_id=run_id, attempts_remaining=0,
    ))

    assert result.startswith("PROXY_UNAVAILABLE:")
    assert eng_repo.get_run(session, run_id)["result"] == "QUEUED"
    submits = [
        j for j in _deferred_jobs(procrastinate_app)
        if "submit_engagement_run" in j.get("task_name", j.get("name", ""))
    ]
    assert len(submits) == 1
    assert submits[0]["args"]["attempts_remaining"] == 0


def test_submit_engagement_run_task_reschedules_when_over_daily_limit(
        session, procrastinate_app, monkeypatch):
    """When DailyLimitReached fires, the task should mark the run QUEUED,
    not consume attempts_remaining, and re-defer itself with schedule_in
    landing past the next UTC midnight (so it picks back up tomorrow)."""
    import asyncio
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service, geelark_service
    from app.tasks.engagement import submit_engagement_run_task

    gid = group_repo.create(session, name="Cap Group")
    aid = acc_repo.create(session, {
        "profile_name": "Capped",
        "geelark_phone_id": "ph-cap",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    srepo.update(session, {"daily_comment_limit_tiktok": "2"})

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[aid],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]

    # Two prior SUCCESS runs exhaust the strict limit (>=2). The PENDING
    # candidate is backlog and does not count until submission starts.
    for i in range(2):
        tid = eng_repo.create_task(
            session, group_id=gid, action="tiktok_ai_comment",
            source="manual_links",
            target_links=[f"https://x/prior/{i}"],
            target_publication_ids=[], comment_probability=100,
        )
        rid = eng_repo.create_run(
            session, engagement_task_id=tid, account_id=aid,
            target_link=f"https://x/prior/{i}", action="tiktok_ai_comment",
        )
        eng_repo.set_result(session, rid, "SUCCESS", "ok")

    class NoNetwork:
        def create_tiktok_ai_comment_task(self, *a, **k):
            raise AssertionError("must not call GeeLark when over limit")
        def stop_phones(self, ids):
            return {}

    monkeypatch.setattr(geelark_service, "make_client", lambda: NoNetwork())

    result_text = asyncio.run(submit_engagement_run_task(
        run_id=run_id, attempts_remaining=5))

    # Run is queued (not SKIPPED, not FAILURE).
    assert eng_repo.get_run(session, run_id)["result"] == "QUEUED"

    # No check_engagement_run task chained.
    pending = _deferred_jobs(procrastinate_app)
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert not any("check_engagement_run" in n for n in names)

    # A submit_engagement_run task was re-deferred and attempts_remaining
    # was NOT decremented (daily-limit waits aren't transient errors).
    submits = [
        j for j in pending
        if "submit_engagement_run" in j.get("task_name", j.get("name", ""))
    ]
    assert len(submits) == 1
    assert submits[0]["args"]["attempts_remaining"] == 5

    # And it returned a recognisable result string.
    assert "DAILY_LIMIT" in result_text or "daily limit" in result_text.lower()
