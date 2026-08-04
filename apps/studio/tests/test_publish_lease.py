"""Publish pipeline acquires phone lease + releases on terminal."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.repositories import accounts as acc_repo
from app.services import phone_runtime


def _fake_client(*, status=2):
    class C:
        def start_phones(self, ids): pass
        def stop_phones(self, ids): pass
        def upload_file(self, *a, **kw): return "https://fake/resource"
        def create_video_post_task(self, *a, **kw): return "geelark-task-pub-1"
        def create_youtube_post_task(self, *a, **kw): return "geelark-task-pub-1"
        def create_instagram_post_task(self, *a, **kw): return "geelark-task-pub-1"
        def create_facebook_post_task(self, *a, **kw): return "geelark-task-pub-1"
        def query_tasks(self, ids): return [{"status": status}]
        def get_task_detail(self, tid): return {"status": status}
    return C()


def _phone_session(db, account_id: int):
    return db.execute(
        text(
            "SELECT id, status, final_status, stopped_at, "
            "  source, owner_type, owner_id, geelark_task_id, stop_reason "
            "FROM phone_sessions WHERE account_id = :aid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"aid": account_id},
    ).fetchone()


def _active_session(db, account_id: int):
    return db.execute(
        text(
            "SELECT id FROM phone_sessions WHERE account_id = :aid "
            "  AND stopped_at IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"aid": account_id},
    ).fetchone()


def _hold_phone(db, account_id: int, env_id: str, *, source: str = "manual"):
    """Simulate the phone being already busy with a non-publish session."""
    phone_runtime.start_session(
        db, account_id=account_id, env_id=env_id,
        source=source, owner_type="account", owner_id=account_id,
        budget_seconds=600, created_by="test",
    )


def _setup_initiated_publication(db, pub_id, task_id="fake-tid"):
    from app.repositories import publications as pub_repo
    pub_repo.set_result(db, pub_id, "INITIATED", "submitted")
    db.execute(
        text("UPDATE publications SET geelark_task_id = :t WHERE id = :i"),
        {"t": task_id, "i": pub_id},
    )
    db.commit()


def _attach_initiated_publish_session(db, account_id, env_id, pub_id,
                                       task_id="fake-tid"):
    """Mirror what submit_publication_to_geelark would do so check_* tests
    that start at INITIATED have a live phone_session to finalize."""
    sid = phone_runtime.start_session(
        db, account_id=account_id, env_id=env_id, source="publish",
        owner_type="publication", owner_id=pub_id,
        budget_seconds=900, created_by="publish",
    )
    phone_runtime.attach_geelark_task(db, sid, task_id)
    return sid


def test_submit_publication_raises_phone_busy_when_session_active(
        db, sample_publication_with_account, monkeypatch):
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    monkeypatch.setattr(posting_service, "_caption_for_publication",
                        lambda *a, **kw: "test caption #test")
    pub_repo.mark_queued(db, pub_id)
    a = acc_repo.get(db, aid)
    _hold_phone(db, aid, a["geelark_phone_id"])
    with pytest.raises(posting_service.PhoneBusy):
        posting_service.submit_publication_to_geelark(
            db, _fake_client(), publication_id=pub_id)


def test_submit_publication_creates_active_session(
        db, sample_publication_with_account, monkeypatch):
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    monkeypatch.setattr(posting_service, "_caption_for_publication",
                        lambda *a, **kw: "test caption #test")
    pub_repo.mark_queued(db, pub_id)
    posting_service.submit_publication_to_geelark(
        db, _fake_client(), publication_id=pub_id)
    sess = _phone_session(db, aid)
    assert sess is not None
    assert sess.stopped_at is None
    assert sess.source == "publish"
    assert sess.owner_type == "publication"
    assert sess.owner_id == pub_id


class _SubmitCountingClient:
    """Tracks how many times create_video_post_task is called so we can
    assert that a duplicate submit_publication_to_geelark call does NOT
    create a second GeeLark task."""
    def __init__(self):
        self.submit_calls = 0
    def start_phones(self, ids): pass
    def stop_phones(self, ids): pass
    def upload_file(self, *a, **kw): return "https://fake/resource"
    def create_video_post_task(self, *a, **kw):
        self.submit_calls += 1
        return f"geelark-task-{self.submit_calls}"
    def create_youtube_post_task(self, *a, **kw):
        return self.create_video_post_task(*a, **kw)
    def create_instagram_post_task(self, *a, **kw):
        return self.create_video_post_task(*a, **kw)
    def create_facebook_post_task(self, *a, **kw):
        return self.create_video_post_task(*a, **kw)
    def query_tasks(self, ids): return [{"status": 2}]
    def get_task_detail(self, tid): return {"status": 2}


def test_submit_publication_is_idempotent_when_already_initiated(
        db, sample_publication_with_account, monkeypatch):
    """Regression: pub 6180 incident (2026-06-09). A stale defer that runs
    while a publication is already INITIATED must NOT create a second
    GeeLark task."""
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    monkeypatch.setattr(posting_service, "_caption_for_publication",
                        lambda *a, **kw: "test caption #test")
    client = _SubmitCountingClient()

    pub_repo.mark_queued(db, pub_id)
    task_id_1 = posting_service.submit_publication_to_geelark(
        db, client, publication_id=pub_id)
    assert client.submit_calls == 1

    task_id_2 = posting_service.submit_publication_to_geelark(
        db, client, publication_id=pub_id)
    assert client.submit_calls == 1
    assert task_id_2 == task_id_1
    pub = pub_repo.get(db, pub_id)
    assert pub["geelark_task_id"] == task_id_1
    assert pub["result"] == "INITIATED"


def test_submit_publication_noop_when_already_success(
        db, sample_publication_with_account, monkeypatch):
    """A publication that has reached SUCCESS must never be re-submitted."""
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    monkeypatch.setattr(posting_service, "_caption_for_publication",
                        lambda *a, **kw: "test caption #test")
    client = _SubmitCountingClient()

    pub_repo.mark_queued(db, pub_id)
    posting_service.submit_publication_to_geelark(
        db, client, publication_id=pub_id)
    pub_repo.set_result(db, pub_id, "SUCCESS", "completed")

    posting_service.submit_publication_to_geelark(
        db, client, publication_id=pub_id)
    assert client.submit_calls == 1
    pub = pub_repo.get(db, pub_id)
    assert pub["result"] == "SUCCESS"


def test_submit_publication_releases_session_on_caption_failure(
        db, sample_publication_with_account, monkeypatch):
    """If caption generation fails (e.g., Gemini outage), the
    phone_session MUST be finalized so the phone isn't stranded busy."""
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    pub_repo.mark_queued(db, pub_id)

    def boom(*a, **kw):
        raise RuntimeError("simulated caption failure")
    monkeypatch.setattr(posting_service, "_caption_for_publication", boom)

    with pytest.raises(RuntimeError):
        posting_service.submit_publication_to_geelark(
            db, _fake_client(), publication_id=pub_id)
    assert _active_session(db, aid) is None


def test_check_publication_finalizes_session_on_success(
        db, sample_publication_with_account):
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)

    class C:
        def query_tasks(self, ids): return [{"status": 3, "shareLink": "https://x"}]
        def stop_phones(self, ids): pass
        def get_task_detail(self, tid): return {"status": 3}
    posting_service.check_publication_one(db, C(), pub_id)
    sess = _phone_session(db, aid)
    assert sess.stopped_at is not None
    assert sess.final_status == "STOPPED"


def test_check_publication_finalizes_session_on_failure(
        db, sample_publication_with_account):
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)

    class C:
        def query_tasks(self, ids): return [{"status": 4, "failDesc": "boom"}]
        def stop_phones(self, ids): pass
        def get_task_detail(self, tid): return {"status": 4, "failDesc": "boom"}
    posting_service.check_publication_one(db, C(), pub_id)
    sess = _phone_session(db, aid)
    assert sess.stopped_at is not None
    assert sess.final_status == "FAILED"


def test_check_publication_finalizes_session_on_task_not_found(
        db, sample_publication_with_account):
    """Empty result from GeeLark (rare) means task lost; treat as failure."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)

    class C:
        def query_tasks(self, ids): return []
    posting_service.check_publication_one(db, C(), pub_id)
    sess = _phone_session(db, aid)
    assert sess.stopped_at is not None


class _StopTrackingClient:
    """check_publication_one client that records stop_phones calls."""
    def __init__(self, response):
        self._response = response
        self.stopped = []

    def query_tasks(self, ids):
        return self._response

    def stop_phones(self, ids):
        self.stopped.extend(ids)

    def get_task_detail(self, tid):
        return {"failCode": 99, "failDesc": "test-fail", "resultImages": []}


def test_check_publication_stops_phone_on_success(
        db, sample_publication_with_account):
    """status=3 must STOP the phone — finalizing the session alone is
    not enough, GeeLark keeps billing until we explicitly stop the env."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)

    client = _StopTrackingClient(
        response=[{"id": "fake-tid", "status": 3, "shareLink": "https://x"}])
    posting_service.check_publication_one(db, client, pub_id)

    assert client.stopped == [a["geelark_phone_id"]]


def test_check_publication_stops_phone_on_failure(
        db, sample_publication_with_account):
    """status=4 must STOP the phone too."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)

    client = _StopTrackingClient(
        response=[{"id": "fake-tid", "status": 4, "failDesc": "boom"}])
    posting_service.check_publication_one(db, client, pub_id)

    assert client.stopped == [a["geelark_phone_id"]]


def test_check_publication_stops_phone_on_task_not_found(
        db, sample_publication_with_account):
    """Empty GeeLark response (task purged) must still STOP the phone."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)

    client = _StopTrackingClient(response=[])
    posting_service.check_publication_one(db, client, pub_id)

    assert client.stopped == [a["geelark_phone_id"]]


def test_check_publication_times_out_when_budget_expired(
        db, sample_publication_with_account):
    """Once the phone_session budget is expired, status checks must
    stop the phone and mark the publication timed out instead of letting
    an overlong GeeLark RPA continue billing."""
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :i AND stopped_at IS NULL"),
        {"p": datetime.utcnow() - timedelta(minutes=1), "i": aid})
    db.commit()

    client = _StopTrackingClient(
        response=[{"id": "fake-tid", "status": 2}])
    outcome = posting_service.check_publication_one(db, client, pub_id)

    pub = pub_repo.get(db, pub_id)
    assert outcome == "timeout"
    assert client.stopped == [a["geelark_phone_id"]]
    sess = _phone_session(db, aid)
    assert sess.final_status == "TIMEOUT"
    assert pub["result"] == "FAILURE"
    assert "lease expired" in (pub["notes"] or "").lower()


def test_check_initiated_stops_phone_and_finalizes_on_success(
        db, sample_publication_with_account):
    """The batch initiated scan can see a terminal GeeLark status before
    the per-publication check does. It must perform the same phone cleanup."""
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)

    client = _StopTrackingClient(
        response=[{"id": "fake-tid", "status": 3, "shareLink": "https://x"}])
    outcome = posting_service.check_initiated(db, client)

    pub = pub_repo.get(db, pub_id)
    assert outcome == "1 success, 0 failure"
    assert client.stopped == [a["geelark_phone_id"]]
    sess = _phone_session(db, aid)
    assert sess.stopped_at is not None


def test_check_initiated_times_out_when_budget_expired(
        db, sample_publication_with_account):
    from app.services import posting_service
    from app.repositories import publications as pub_repo
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(db, aid, a["geelark_phone_id"], pub_id)
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :i AND stopped_at IS NULL"),
        {"p": datetime.utcnow() - timedelta(minutes=1), "i": aid})
    db.commit()

    client = _StopTrackingClient(
        response=[{"id": "fake-tid", "status": 2}])
    posting_service.check_initiated(db, client)

    sess = _phone_session(db, aid)
    assert sess.final_status == "TIMEOUT"


def test_reclaim_in_flight_publish_defers_check(
        in_memory_procrastinate, db, sample_publication_with_account):
    import asyncio
    from app.tasks.publish import reclaim_in_flight_publish_task
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id, task_id="stranded-tid")
    _attach_initiated_publish_session(
        db, aid, a["geelark_phone_id"], pub_id, task_id="stranded-tid")
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :i AND stopped_at IS NULL"),
        {"p": datetime.utcnow() - timedelta(minutes=30), "i": aid})
    db.commit()

    out = asyncio.run(reclaim_in_flight_publish_task(timestamp=0))
    assert "1" in out
    jobs = in_memory_procrastinate.jobs
    check_jobs = [j for j in jobs.values()
                  if j["task_name"] == "check_publication_status"]
    assert len(check_jobs) == 1


def test_reclaim_in_flight_publish_ignores_active_session(
        in_memory_procrastinate, db, sample_publication_with_account):
    """A non-overdue session is not stranded — reclaim must skip it."""
    import asyncio
    from app.tasks.publish import reclaim_in_flight_publish_task
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id, task_id="fresh-tid")
    _attach_initiated_publish_session(
        db, aid, a["geelark_phone_id"], pub_id, task_id="fresh-tid")

    out = asyncio.run(reclaim_in_flight_publish_task(timestamp=0))
    jobs = in_memory_procrastinate.jobs
    check_jobs = [j for j in jobs.values()
                  if j["task_name"] == "check_publication_status"]
    assert len(check_jobs) == 0


# ---------------------------------------------------------------------------
# Timeout verdicts must ask GeeLark one last time (double-post guard)
# ---------------------------------------------------------------------------
class _RecordingClient:
    """Fake client that records cancel/stop and serves a fixed status."""

    def __init__(self, *, status=2, query_error=False):
        self._status = status
        self._query_error = query_error
        self.cancelled, self.stopped = [], []

    def query_tasks(self, ids):
        if self._query_error:
            raise RuntimeError("geelark unreachable")
        return [{"status": self._status}]

    def get_task_detail(self, tid):
        return {"status": self._status}

    def cancel_tasks(self, ids):
        self.cancelled.extend(ids)

    def stop_phones(self, ids):
        self.stopped.extend(ids)


def test_timed_out_last_look_spares_terminal_task(
        db, sample_publication_with_account):
    """A publication whose GeeLark task went terminal inside the polling
    blind spot must NOT be declared FAILURE by the timeout path — the
    retry pipeline would re-post a video that is already live. The
    verdict defers to the normal convergence path instead."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(
        db, aid, a["geelark_phone_id"], pub_id)

    client = _RecordingClient(status=3)  # terminal on GeeLark's side
    outcome = posting_service.mark_publication_timed_out(
        db, client, pub_id, reason="poll chain exhausted")

    assert outcome == "running"
    from app.repositories import publications as pub_repo
    assert pub_repo.get(db, pub_id)["result"] == "INITIATED"
    assert client.cancelled == []
    assert client.stopped == []
    sess = _phone_session(db, aid)
    assert sess.stopped_at is None  # left for the convergence path


def test_timed_out_confirmed_running_still_fails(
        db, sample_publication_with_account):
    """When GeeLark confirms the task is genuinely still running, the
    timeout verdict proceeds: FAILURE + cancel + stop + lease release."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(
        db, aid, a["geelark_phone_id"], pub_id)

    client = _RecordingClient(status=2)
    outcome = posting_service.mark_publication_timed_out(
        db, client, pub_id, reason="poll chain exhausted")

    assert outcome == "timeout"
    from app.repositories import publications as pub_repo
    assert pub_repo.get(db, pub_id)["result"] == "FAILURE"
    assert client.cancelled == ["fake-tid"]
    assert client.stopped == [a["geelark_phone_id"]]


def test_timed_out_query_failure_keeps_verdict(
        db, sample_publication_with_account):
    """If the last-look query itself fails we cannot prove the task is
    terminal — proceed with the timeout verdict (conservative: better a
    rare false FAILURE than a phone billing forever)."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    _attach_initiated_publish_session(
        db, aid, a["geelark_phone_id"], pub_id)

    client = _RecordingClient(query_error=True)
    outcome = posting_service.mark_publication_timed_out(
        db, client, pub_id, reason="poll chain exhausted")

    assert outcome == "timeout"
    from app.repositories import publications as pub_repo
    assert pub_repo.get(db, pub_id)["result"] == "FAILURE"


def test_expired_lease_with_terminal_geelark_converges_normally(
        db, sample_publication_with_account):
    """check_publication_one with an expired lease must still process a
    terminal GeeLark answer as a normal outcome instead of short-
    circuiting to the timeout verdict (the old shortcut re-posted
    already-live videos)."""
    from app.services import posting_service
    pub_id = sample_publication_with_account["publication_id"]
    aid = sample_publication_with_account["account_id"]
    a = acc_repo.get(db, aid)
    _setup_initiated_publication(db, pub_id)
    sid = _attach_initiated_publish_session(
        db, aid, a["geelark_phone_id"], pub_id)
    db.execute(
        text("UPDATE phone_sessions SET budget_deadline_at = :p "
             "WHERE id = :sid"),
        {"p": datetime.utcnow() - timedelta(minutes=1), "sid": sid},
    )
    db.commit()

    outcome = posting_service.check_publication_one(
        db, _fake_client(status=3), pub_id)

    assert outcome == "success"
    from app.repositories import publications as pub_repo
    assert pub_repo.get(db, pub_id)["result"] == "SUCCESS"


# ---------------------------------------------------------------------------
# Orphaned QUEUED reaper
# ---------------------------------------------------------------------------
def test_reclaim_unclaims_orphaned_queued(
        db, sample_publication_with_account):
    """A QUEUED publication with no live session and a stale claim stamp
    lost its job (defer crash / failed-out submit). The reaper must
    return it to PENDING so auto_publish_scan can re-plan it — otherwise
    account_has_inflight freezes the account's publish AND engagement
    scheduling forever."""
    from app.repositories import publications as pub_repo
    from app.tasks.publish import (
        QUEUED_ORPHAN_MINUTES, _unclaim_orphaned_queued_sync,
    )
    pub_id = sample_publication_with_account["publication_id"]
    pub_repo.mark_queued(db, pub_id)
    db.execute(
        text("UPDATE publications SET last_checked_at = :t WHERE id = :i"),
        {"t": datetime.utcnow()
             - timedelta(minutes=QUEUED_ORPHAN_MINUTES + 10),
         "i": pub_id},
    )
    db.commit()

    n = _unclaim_orphaned_queued_sync()

    assert n == 1
    assert pub_repo.get(db, pub_id)["result"] == "PENDING"


def test_reclaim_leaves_fresh_queued_alone(
        db, sample_publication_with_account):
    """A recently claimed QUEUED row is normal in-flight work — the
    reaper must not touch it."""
    from app.repositories import publications as pub_repo
    from app.tasks.publish import _unclaim_orphaned_queued_sync
    pub_id = sample_publication_with_account["publication_id"]
    pub_repo.mark_queued(db, pub_id)  # stamps last_checked_at = now

    n = _unclaim_orphaned_queued_sync()

    assert n == 0
    assert pub_repo.get(db, pub_id)["result"] == "QUEUED"
