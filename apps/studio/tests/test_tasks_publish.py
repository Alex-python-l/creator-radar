"""End-to-end tests for the per-publication task chain and the
auto-publish scan. Uses the conftest `procrastinate_app` fixture
(InMemoryConnector) so no real PG queue is touched."""
import pytest
from datetime import datetime, timezone


@pytest.fixture
def fake_client(monkeypatch):
    """A minimal GeeLark client that records submit + poll calls."""
    from app.services import geelark_service

    class FakeC:
        def upload_file(self, path, ext): return "https://res/x.mp4"
        def create_video_post_task(self, env, url, video_desc=None,
                                    cover_url=None):
            return "tk-fake"
        def create_youtube_post_task(self, env, url, title):
            return "yt-fake"
        def create_instagram_post_task(self, env, urls, description):
            return "ig-fake"
        def create_facebook_post_task(self, env, url, description):
            return "fb-fake"
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]

    monkeypatch.setattr(geelark_service, "make_client", lambda: FakeC())
    return FakeC


def test_publish_publication_task_calls_submit_and_defers_check(
        session, procrastinate_app, fake_client, tmp_path, monkeypatch,
        default_group_id):
    """publish_publication_task should submit to GeeLark, transition
    the publication to INITIATED, and defer one check_publication_status
    task with schedule_in 30s."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import posting_service

    aid = acc_repo.create(session, {"profile_name": "PT",
                                     "geelark_phone_id": "phPT",
                                     "warmed_up": 1,
                                     "tiktok_logged_in": 1,
                                     "group_id": default_group_id})
    f = tmp_path / "p.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "p.mp4", str(f), group_id=default_group_id)
    # submit_publication_to_geelark reads video.account_id.
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(session, pid)

    # _caption signature: (session, local_path, acc). Use *args so this
    # test stays robust if posting_service._caption is ever refactored.
    monkeypatch.setattr(posting_service, "_caption", lambda *a, **kw: "c")

    # Run the task body directly (procrastinate's InMemoryConnector
    # makes defer_async safe to call without a real worker loop).
    import asyncio
    from app.tasks.publish import publish_publication_task
    result = asyncio.run(publish_publication_task(publication_id=pid))
    assert "INITIATED" in result
    assert pub_repo.get(session, pid)["result"] == "INITIATED"

    # check_publication_status was deferred. The InMemoryConnector's
    # job list should now have one entry.
    pending = list(procrastinate_app.connector.jobs.values())
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert any("check_publication_status" in n for n in names)


def test_check_publication_status_task_chains_when_running(
        session, procrastinate_app, monkeypatch, tmp_path):
    """When the GeeLark task is still running and attempts remain, the
    status check defers itself with schedule_in 30s."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import geelark_service

    aid = acc_repo.create(session, {"profile_name": "CP",
                                     "geelark_phone_id": "phCP"})
    f = tmp_path / "c.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "c.mp4", str(f))
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, "c", "ck1", "s")

    class Running:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 2}]
    monkeypatch.setattr(geelark_service, "make_client", lambda: Running())

    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))
    assert outcome == "running"
    pending = list(procrastinate_app.connector.jobs.values())
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert any("check_publication_status" in n for n in names)
    # Regression (2026-05-29 prod incident): the self-chained re-defer must
    # also schedule via .configure(), not leak schedule_in into task kwargs.
    chained = [j for j in pending if "check_publication_status" in
               j.get("task_name", j.get("name", ""))][0]
    chained_kwargs = chained.get("task_kwargs",
                                 chained.get("kwargs", chained.get("args", {})))
    assert "schedule_in" not in chained_kwargs
    assert chained.get("scheduled_at") is not None


def test_auto_publish_scan_defers_one_task_per_eligible_publication(
        session, procrastinate_app, fake_client, tmp_path, monkeypatch):
    """The hourly scan should walk each platform, find eligible
    publications, mark them QUEUED, and defer one task each."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr

    for platform in ("tiktok", "youtube"):
        gid = gr.create(session, name=f"G-{platform}")
        aid = acc_repo.create(session, {
            "profile_name": f"A-{platform}",
            "geelark_phone_id": f"ph-{platform}",
            "group_id": gid,
            "warmed_up": 1,
            f"{platform}_logged_in": 1,
        })
        f = tmp_path / f"{platform}.mp4"; f.write_bytes(b"x")
        vid = vid_repo.create(session, f"{platform}.mp4", str(f),
                              group_id=gid)
        pub_repo.create(session, video_id=vid, platform=platform)

    import asyncio
    from app.tasks.auto_publish import auto_publish_scan_task
    result = asyncio.run(auto_publish_scan_task(timestamp=0))
    assert "2 publication(s) deferred" in result
    # Both publications should now be QUEUED.
    import sqlalchemy as sa
    statuses = [r[0] for r in session.exec(
        sa.text("SELECT result FROM publications")).all()]
    assert set(statuses) == {"QUEUED"}


def test_auto_publish_scan_skips_when_another_scan_is_running():
    import asyncio
    from app.tasks import auto_publish as ap

    assert ap._SCAN_LOCK.acquire(blocking=False)
    try:
        result = asyncio.run(ap.auto_publish_scan_task(timestamp=0))
    finally:
        ap._SCAN_LOCK.release()

    # The colliding tick re-defers itself instead of silently dropping —
    # it may be a precisely-timed retry wake-up that must not be lost.
    assert result.startswith("auto_publish_scan: already running")


def test_auto_publish_scan_uses_configured_cooldown(
        session, procrastinate_app, fake_client, tmp_path):
    """The hourly auto-publish scan must honor post_cooldown_hours from
    settings, not the assignment helper's default."""
    from datetime import datetime, timedelta
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr
    from app.repositories import settings as settings_repo

    settings_repo.update(session, {"post_cooldown_hours": "12"})
    gid = gr.create(session, name="cooldown")
    acc_repo.create(session, {
        "profile_name": "recent",
        "geelark_phone_id": "ph-recent",
        "group_id": gid,
        "warmed_up": 1,
        "tiktok_logged_in": 1,
        "last_posted_at": datetime.utcnow() - timedelta(hours=6),
    })
    f = tmp_path / "cooldown.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "cooldown.mp4", str(f), group_id=gid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")

    import asyncio
    from app.tasks.auto_publish import auto_publish_scan_task
    result = asyncio.run(auto_publish_scan_task(timestamp=0))

    assert "0 publication(s) deferred" in result
    assert pub_repo.get(session, pid)["result"] == "PENDING"
    assert vid_repo.get(session, vid)["account_id"] is None
    pending = list(procrastinate_app.connector.jobs.values())
    scan_jobs = [
        j for j in pending
        if "auto_publish_scan" in j.get("task_name", j.get("name", ""))
    ]
    assert len(scan_jobs) == 1
    assert scan_jobs[0].get("scheduled_at") is not None


def test_auto_publish_scan_reconciles_stale_initiated_before_planning(
        session, procrastinate_app, monkeypatch, tmp_path):
    """The hourly scanner should clear completed stale INITIATED rows before
    deciding whether an account is still blocked by in-flight work."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr
    from app.services import geelark_service

    gid = gr.create(session, name="stale-initiated")
    aid = acc_repo.create(session, {
        "profile_name": "M5",
        "geelark_phone_id": "ph-m5",
        "group_id": gid,
        "warmed_up": 1,
        "instagram_logged_in": 1,
    })

    old_file = tmp_path / "old.mp4"; old_file.write_bytes(b"old")
    old_vid = vid_repo.create(session, "old.mp4", str(old_file),
                              group_id=gid)
    old_pid = pub_repo.create(session, video_id=old_vid,
                              platform="instagram")
    pub_repo.set_account(session, old_pid, aid)
    pub_repo.mark_initiated(session, old_pid, "c", "task-old", "s")

    new_file = tmp_path / "new.mp4"; new_file.write_bytes(b"new")
    new_vid = vid_repo.create(session, "new.mp4", str(new_file),
                              group_id=gid)
    new_pid = pub_repo.create(session, video_id=new_vid,
                              platform="instagram")

    class Completed:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3}]

    monkeypatch.setattr(geelark_service, "make_client",
                         lambda: Completed())

    import asyncio
    from app.tasks.auto_publish import auto_publish_scan_task
    result = asyncio.run(auto_publish_scan_task(timestamp=0))

    assert "1 publication(s) deferred" in result
    assert pub_repo.get(session, old_pid)["result"] == "SUCCESS"
    assert pub_repo.get(session, new_pid)["result"] == "QUEUED"


def test_publish_publication_task_schedules_retry_on_submit_failure(
        session, procrastinate_app, monkeypatch, tmp_path, default_group_id):
    """A retriable submit failure should return the publication to PENDING
    with next_retry_at instead of leaving it terminal FAILURE."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import settings as settings_repo
    from app.services import geelark_service, posting_service

    settings_repo.update(session, {"publish_retry_delays_seconds": "60"})
    aid = acc_repo.create(session, {"profile_name": "FP",
                                     "geelark_phone_id": "phFP",
                                     "warmed_up": 1,
                                     "tiktok_logged_in": 1,
                                     "group_id": default_group_id})
    f = tmp_path / "fp.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "fp.mp4", str(f), group_id=default_group_id)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(session, pid)

    class BrokenClient:
        def upload_file(self, p, t):
            raise RuntimeError("CDN down")
    monkeypatch.setattr(geelark_service, "make_client",
                         lambda: BrokenClient())
    # _caption signature: (session, local_path, acc). Use *args so this
    # test stays robust if posting_service._caption is ever refactored.
    monkeypatch.setattr(posting_service, "_caption",
                         lambda *a, **kw: "c")

    import asyncio
    from app.tasks.publish import publish_publication_task
    result = asyncio.run(publish_publication_task(publication_id=pid))

    assert "RETRY" in result
    row = pub_repo.get(session, pid)
    assert row["result"] == "PENDING"
    assert row["retry_count"] == 1
    assert row["next_retry_at"] is not None
    assert row["notes"] == "upload failed: CDN down"
    # No chained status-check task was deferred.
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert not any("check_publication_status" in n for n in names)
    assert any("auto_publish_scan" in n for n in names)


def test_publish_publication_task_retries_when_phone_busy(
        session, procrastinate_app, monkeypatch, tmp_path, default_group_id):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import geelark_service, posting_service

    aid = acc_repo.create(session, {"profile_name": "busy",
                                    "geelark_phone_id": "ph-busy",
                                    "warmed_up": 1,
                                    "tiktok_logged_in": 1,
                                    "youtube_logged_in": 1,
                                    "group_id": default_group_id})
    active = tmp_path / "active.mp4"; active.write_bytes(b"x")
    active_vid = vid_repo.create(session, "active.mp4", str(active),
                                  group_id=default_group_id)
    active_pid = pub_repo.create(session, video_id=active_vid,
                                 platform="tiktok")
    pub_repo.set_account(session, active_pid, aid)
    pub_repo.mark_initiated(session, active_pid, "c", "task-active", "s")

    blocked = tmp_path / "blocked.mp4"; blocked.write_bytes(b"x")
    blocked_vid = vid_repo.create(session, "blocked.mp4", str(blocked),
                                   group_id=default_group_id)
    blocked_pid = pub_repo.create(session, video_id=blocked_vid,
                                  platform="youtube")
    pub_repo.set_account(session, blocked_pid, aid)
    pub_repo.mark_queued(session, blocked_pid)

    class ShouldNotSubmit:
        def upload_file(self, *a, **k):
            raise AssertionError("busy phone should not upload")

    monkeypatch.setattr(geelark_service, "make_client",
                        lambda: ShouldNotSubmit())
    monkeypatch.setattr(posting_service, "_caption", lambda *a, **k: "c")

    import asyncio
    from app.tasks.publish import publish_publication_task
    result = asyncio.run(publish_publication_task(publication_id=blocked_pid))
    assert "QUEUED" in result
    # Row keeps its QUEUED claim so the deferred retry can reach the
    # submit gate (PhoneBusy no longer unclaims to PENDING).
    assert pub_repo.get(session, blocked_pid)["result"] == "QUEUED"
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert not any("check_publication_status" in n for n in names)
    assert any("publish_publication" in n for n in names)


def test_check_publication_status_task_times_out_at_attempts_zero(
        session, procrastinate_app, monkeypatch, tmp_path, default_group_id):
    """When the 40 × 30s poll window expires with GeeLark still reporting
    a non-terminal status, the task must drop into the timeout handler:
    stop the phone, release the lease, mark the publication FAILURE.
    Otherwise the reclaim cron just restarts the poll window forever and
    the phone keeps billing."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import settings as settings_repo
    from app.services import geelark_service

    # Disable retries so the timeout path stays terminal — the assertion
    # below checks for FAILURE, which retry-on-timeout would flip back to
    # PENDING.
    settings_repo.update(session, {"publish_retry_delays_seconds": ""})
    aid = acc_repo.create(session, {"profile_name": "TZ",
                                     "geelark_phone_id": "phTZ",
                                     "group_id": default_group_id})
    from app.services import phone_runtime as runtime
    f = tmp_path / "tz.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "tz.mp4", str(f), group_id=default_group_id)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.set_account(session, pid, aid)
    pub_repo.mark_initiated(session, pid, "c", "tz1", "s")
    runtime.start_session(
        session, account_id=aid, env_id="phTZ", source="publish",
        owner_type="publication", owner_id=pid,
        budget_seconds=60, created_by="test",
    )

    class StillRunning:
        def __init__(self): self.stopped = []
        def query_tasks(self, ids): return [{"id": ids[0], "status": 2}]
        def stop_phones(self, ids): self.stopped.extend(ids)
    fake = StillRunning()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=0))

    assert outcome == "timeout"
    assert fake.stopped == ["phTZ"]
    row = pub_repo.get(session, pid)
    assert row["result"] == "FAILURE"
    assert "timeout" in (row["notes"] or "").lower()
    # NO additional check_publication_status task should have been deferred.
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert not any("check_publication_status" in n for n in names)


def test_check_publication_status_task_terminates_on_success(
        session, procrastinate_app, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import geelark_service

    aid = acc_repo.create(session, {"profile_name": "Done",
                                     "geelark_phone_id": "ph-done"})
    f = tmp_path / "done.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "done.mp4", str(f))
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, "c", "done-task", "s")

    class Done:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]

    monkeypatch.setattr(geelark_service, "make_client", lambda: Done())

    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))

    assert outcome == "success"
    assert pub_repo.get(session, pid)["result"] == "SUCCESS"
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    names = [j.get("task_name", j.get("name", "")) for j in pending]
    assert not any("check_publication_status" in n for n in names)


def _deferred_task_names(procrastinate_app):
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    return [j.get("task_name", j.get("name", "")) for j in pending]


def _prep_terminal_status_pub(session, tmp_path, monkeypatch, *, status: int):
    """Set up a pub in INITIATED, then mock GeeLark to report the given
    status (3=success, 4=failure, 2=running)."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import geelark_service

    aid = acc_repo.create(session, {"profile_name": "WS",
                                     "geelark_phone_id": "ph-ws"})
    f = tmp_path / "ws.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "ws.mp4", str(f))
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, "c", "ws-task", "s")

    class Reply:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": status}]
        def get_task_detail(self, _id):
            return {"failCode": 99, "failDesc": "test-fail",
                    "resultImages": []}

    monkeypatch.setattr(geelark_service, "make_client", lambda: Reply())
    return pid


def test_check_status_wakes_auto_publish_scan_on_success(
        session, procrastinate_app, monkeypatch, tmp_path):
    """Account just freed up — the status check should defer
    auto_publish_scan so the next eligible publication can fire without
    waiting for the next hourly cron tick."""
    pid = _prep_terminal_status_pub(session, tmp_path, monkeypatch, status=3)
    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))
    assert outcome == "success"
    assert any("auto_publish_scan" in n
               for n in _deferred_task_names(procrastinate_app))


def test_check_status_wakes_auto_publish_scan_on_failure(
        session, procrastinate_app, monkeypatch, tmp_path):
    """Same wake-up should fire on FAILURE — the account is free even
    if the post itself failed."""
    pid = _prep_terminal_status_pub(session, tmp_path, monkeypatch, status=4)
    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))
    assert outcome == "failure"
    assert any("auto_publish_scan" in n
               for n in _deferred_task_names(procrastinate_app))


def test_check_status_schedules_retry_on_geelark_failure(
        session, procrastinate_app, monkeypatch, tmp_path):
    from app.repositories import publications as pub_repo
    from app.repositories import settings as settings_repo

    settings_repo.update(session, {"publish_retry_delays_seconds": "60"})
    pid = _prep_terminal_status_pub(session, tmp_path, monkeypatch, status=4)

    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))

    assert outcome == "failure"
    row = pub_repo.get(session, pid)
    assert row["result"] == "PENDING"
    assert row["retry_count"] == 1
    assert row["next_retry_at"] is not None
    assert any("auto_publish_scan" in n
               for n in _deferred_task_names(procrastinate_app))


def test_retry_scan_delay_treats_naive_datetime_as_utc():
    """DB datetimes are naive UTC; production runs in Asia/Shanghai.

    Calling datetime.timestamp() directly on a naive retry_at interprets it
    as local time and schedules retry scans 8 hours too early in production.
    """
    from app.tasks.publish import (
        _retry_at_epoch_seconds,
        _retry_delay_seconds,
    )

    retry_at = datetime(2026, 6, 16, 6, 46, 0)
    now = datetime(2026, 6, 16, 6, 33, 0, tzinfo=timezone.utc)

    assert _retry_delay_seconds(retry_at, now=now) == 13 * 60
    assert _retry_at_epoch_seconds(retry_at) == int(
        datetime(2026, 6, 16, 6, 46, 0, tzinfo=timezone.utc).timestamp()
    )


def test_check_status_does_not_wake_scan_while_running(
        session, procrastinate_app, monkeypatch, tmp_path):
    """If the GeeLark task is still running, the account is still busy
    and we must NOT wake auto_publish_scan."""
    pid = _prep_terminal_status_pub(session, tmp_path, monkeypatch, status=2)
    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))
    assert outcome == "running"
    assert not any("auto_publish_scan" in n
                   for n in _deferred_task_names(procrastinate_app))


def test_check_status_swallows_already_enqueued(
        session, procrastinate_app, monkeypatch, tmp_path):
    """When multiple checks complete in a burst, only the first
    auto_publish_scan defer should land; the rest hit AlreadyEnqueued
    and must NOT bubble out and fail the check task."""
    pid = _prep_terminal_status_pub(session, tmp_path, monkeypatch, status=3)
    from procrastinate.exceptions import AlreadyEnqueued
    from app.tasks import auto_publish as ap_mod

    class StubScan:
        async def defer_async(self, **kwargs):
            raise AlreadyEnqueued("queued already")
    monkeypatch.setattr(ap_mod, "auto_publish_scan_task", StubScan())

    import asyncio
    from app.tasks.publish import check_publication_status_task
    outcome = asyncio.run(check_publication_status_task(
        publication_id=pid, attempts_remaining=5))
    assert outcome == "success"  # check still reports success


def test_publish_publication_task_defers_check_with_30s_schedule_in(
        session, procrastinate_app, monkeypatch, tmp_path, default_group_id):
    """The chained status-check defer must use schedule_in 30s and
    attempts_remaining=MAX_CHECK_RETRIES (mirrors phase-2 cadence)."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import geelark_service, posting_service

    aid = acc_repo.create(session, {"profile_name": "SI",
                                     "geelark_phone_id": "phSI",
                                     "warmed_up": 1,
                                     "tiktok_logged_in": 1,
                                     "group_id": default_group_id})
    f = tmp_path / "si.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(session, "si.mp4", str(f), group_id=default_group_id)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(session, pid)

    class FakeC:
        def upload_file(self, p, t): return "https://res/si.mp4"
        def create_video_post_task(self, env, url, video_desc=None,
                                    cover_url=None):
            return "tk-si-1"
    monkeypatch.setattr(geelark_service, "make_client",
                         lambda: FakeC())
    # _caption signature: (session, local_path, acc). Use *args so this
    # test stays robust if posting_service._caption is ever refactored.
    monkeypatch.setattr(posting_service, "_caption",
                         lambda *a, **kw: "c")

    import asyncio
    from app.tasks.publish import publish_publication_task
    result = asyncio.run(publish_publication_task(publication_id=pid))
    assert "INITIATED" in result

    # Look up the chained task and verify its kwargs.
    pending = list(procrastinate_app.connector.jobs.values()) \
        if hasattr(procrastinate_app.connector, "jobs") else []
    check_jobs = [j for j in pending
                  if "check_publication_status" in
                     j.get("task_name", j.get("name", ""))]
    assert len(check_jobs) == 1
    j = check_jobs[0]
    # task_kwargs key name has varied across procrastinate versions.
    kwargs = j.get("task_kwargs", j.get("kwargs", j.get("args", {})))
    assert kwargs.get("publication_id") == pid
    from app.tasks.publish import MAX_CHECK_RETRIES, CHECK_INTERVAL_SECONDS
    assert kwargs.get("attempts_remaining") == MAX_CHECK_RETRIES
    # Regression (2026-06-16 yt fail-cascade): the poll window has to cover
    # the slowest happy-path publish — YouTube uploads observed at 8-9 min
    # in production. A window shorter than 9 min causes mark_publication_
    # timed_out to fire and cancel_tasks() the GeeLark task mid-upload, so
    # the publication lands in FAILURE even though YouTube would have
    # succeeded. tiktok/instagram/facebook complete in <4 min, so a 9 min
    # ceiling costs nothing on the happy path; stuck-task phone burn is
    # still 2.2× shorter than the pre-238deeb 20-min window.
    assert MAX_CHECK_RETRIES * CHECK_INTERVAL_SECONDS >= 9 * 60
    # Regression (2026-05-29 prod incident): `schedule_in` is a Procrastinate
    # scheduling directive and MUST be applied via .configure(), never passed
    # as a task kwarg. If it leaks into task_kwargs the real worker invokes
    # check_publication_status_task(schedule_in=..., ...) and dies with
    # `TypeError: got an unexpected keyword argument 'schedule_in'`. Guard
    # both sides: no stray kwarg, and the 30s delay actually scheduled it.
    assert "schedule_in" not in kwargs
    assert j.get("scheduled_at") is not None


def test_auto_publish_scan_skips_account_with_inflight_engagement(
        session, procrastinate_app, fake_client, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr
    from app.repositories import engagement as eng_repo

    gid = gr.create(session, name="busy-engagement")
    aid = acc_repo.create(session, {
        "profile_name": "busy-eng",
        "geelark_phone_id": "ph-busy-eng",
        "group_id": gid,
        "warmed_up": 1,
        "tiktok_logged_in": 1,
    })
    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.create_run(
        session,
        engagement_task_id=task_id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )
    eng_repo.mark_initiated(session, run_id, task_id="eng-active",
                            schedule_at=123, notes="running")

    f = tmp_path / "new.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(session, "new.mp4", str(f), group_id=gid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")

    import asyncio
    from app.tasks.auto_publish import auto_publish_scan_task
    result = asyncio.run(auto_publish_scan_task(timestamp=0))

    assert "0 publication(s) deferred" in result
    assert pub_repo.get(session, pid)["result"] == "PENDING"
