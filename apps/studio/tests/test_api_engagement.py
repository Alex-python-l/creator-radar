import pytest


@pytest.fixture
def authed_client(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    return client


def _seed_account(session, *, group_id=None, group_name="API Engage",
                  profile_name="M1",
                  phone_id="ph-m1", tiktok_logged_in=1, warmed_up=1):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    gid = group_id or group_repo.create(session, name=group_name)
    aid = acc_repo.create(session, {
        "profile_name": profile_name,
        "geelark_phone_id": phone_id,
        "group_id": gid,
        "tiktok_logged_in": tiktok_logged_in,
        "warmed_up": warmed_up,
    })
    return gid, aid


def _seed_publication_target(session, *, group_id: int, account_id: int,
                             filename: str, platform: str = "tiktok",
                             result: str = "SUCCESS",
                             share_link: str | None = None):
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo

    vid = vid_repo.create(session, filename, f"/tmp/{filename}",
                          group_id=group_id)
    pid = pub_repo.create(session, video_id=vid, platform=platform)
    pub_repo.set_account(session, pid, account_id)
    pub_repo.set_result(session, pid, result, "done")
    if share_link is not None:
        pub_repo.set_share_link(session, pid, share_link)
    return pid


def test_engagement_targets_list_owned_tiktok_successes(
        authed_client, session):
    gid, aid = _seed_account(session)
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    other_gid = group_repo.create(session, name="Other Engage")
    other_aid = acc_repo.create(session, {
        "profile_name": "M2",
        "geelark_phone_id": "ph-m2",
        "group_id": other_gid,
        "tiktok_logged_in": 1,
    })
    target_id = _seed_publication_target(
        session,
        group_id=gid,
        account_id=aid,
        filename="owned.mp4",
        share_link="https://www.tiktok.com/@owner/video/1234567890",
    )
    _seed_publication_target(
        session,
        group_id=gid,
        account_id=aid,
        filename="no-link.mp4",
        share_link=None,
    )
    _seed_publication_target(
        session,
        group_id=gid,
        account_id=aid,
        filename="youtube.mp4",
        platform="youtube",
        share_link="https://www.tiktok.com/@owner/video/2222222222",
    )
    _seed_publication_target(
        session,
        group_id=gid,
        account_id=aid,
        filename="failed.mp4",
        result="FAILURE",
        share_link="https://www.tiktok.com/@owner/video/3333333333",
    )
    _seed_publication_target(
        session,
        group_id=other_gid,
        account_id=other_aid,
        filename="other-group.mp4",
        share_link="https://www.tiktok.com/@owner/video/4444444444",
    )

    r = authed_client.get(f"/api/engagement/targets?group_id={gid}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["publication_id"] == target_id
    assert row["video_filename"] == "owned.mp4"
    assert row["account_profile_name"] == "M1"
    assert row["share_link"] == "https://www.tiktok.com/@owner/video/1234567890"


def test_engagement_targets_requires_auth(client):
    r = client.get("/api/engagement/targets")
    assert r.status_code == 401


def test_create_engagement_comment_defers_runs(
        authed_client, session, monkeypatch):
    gid, aid = _seed_account(session)
    deferred = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("Job", (), {"id": "job-1"})()

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        fake_defer_async)
    # .configure(...).defer_async(...) must also reach the fake.
    fake_deferrer = type("FakeDeferrer", (),
                        {"defer_async": staticmethod(fake_defer_async)})()
    monkeypatch.setattr(submit_engagement_run_task, "configure",
                        lambda **kw: fake_deferrer)

    r = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "target_publication_ids": [],
        "comment_probability": 100,
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_runs"] == 1
    assert body["skipped_runs"] == 0
    assert deferred and "run_id" in deferred[0]


def test_create_engagement_comment_creates_one_task_per_target(
        authed_client, session, monkeypatch):
    gid, owner = _seed_account(session, profile_name="Owner")
    _, commenter = _seed_account(session, group_id=gid, profile_name="Commenter",
                                 phone_id="ph-commenter")
    deferred = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("Job", (), {"id": f"job-{len(deferred)}"})()

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        fake_defer_async)
    # .configure(...).defer_async(...) must also reach the fake.
    fake_deferrer = type("FakeDeferrer", (),
                        {"defer_async": staticmethod(fake_defer_async)})()
    monkeypatch.setattr(submit_engagement_run_task, "configure",
                        lambda **kw: fake_deferrer)

    r = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [owner, commenter],
        "target_links": [
            "https://www.tiktok.com/@owner/video/1234567890",
            "https://www.tiktok.com/@owner/video/1234567891",
        ],
        "target_publication_ids": [],
        "comment_probability": 100,
    })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_runs"] == 2
    assert body["deferred_runs"] == 2
    assert len(body["task_ids"]) == 2
    assert body["task_id"] == body["task_ids"][0]
    assert len(deferred) == 2

    from app.repositories import engagement as eng_repo
    tasks = eng_repo.list_tasks(session, group_id=gid)
    assert [task["target_links"] for task in sorted(tasks, key=lambda x: x["id"])] == [
        ["https://www.tiktok.com/@owner/video/1234567890"],
        ["https://www.tiktok.com/@owner/video/1234567891"],
    ]
    assert all(
        len(eng_repo.list_runs_for_task(session, task_id)) == 1
        for task_id in body["task_ids"]
    )


def test_per_phone_submit_delays_spread_large_batch_across_quota_days(session):
    from app.api.engagement import _per_phone_submit_delays
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, aid = _seed_account(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "2"})
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[aid],
        target_links=[
            "https://www.tiktok.com/@owner/video/1234567890",
            "https://www.tiktok.com/@owner/video/1234567891",
            "https://www.tiktok.com/@owner/video/1234567892",
        ],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_ids = [
        eng_repo.list_runs_for_task(session, task_id)[0]["id"]
        for task_id in result["task_ids"]
    ]

    delays = _per_phone_submit_delays(
        session, run_ids, platform="tiktok",
    )

    assert [delays[run_id] for run_id in run_ids] == [0, 600, 86_400]


def test_create_engagement_comment_falls_back_to_outbox_on_defer_failure(
        authed_client, session, monkeypatch):
    """When the prompt defer raises, the endpoint still returns 200 and
    the outbox row sits waiting for the dispatcher. deferred_runs counts
    only runs whose prompt dispatch succeeded; everything else converges
    via the dispatcher tick."""
    gid, aid = _seed_account(session)

    async def boom_defer(**kwargs):
        raise RuntimeError("queue unavailable")

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        boom_defer)
    # .configure(...).defer_async(...) must also blow up.
    boom_deferrer = type("BoomDeferrer", (),
                         {"defer_async": staticmethod(boom_defer)})()
    monkeypatch.setattr(submit_engagement_run_task, "configure",
                        lambda **kw: boom_deferrer)

    r = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "target_publication_ids": [],
        "comment_probability": 100,
    })

    assert r.status_code == 200, r.text
    assert r.json()["deferred_runs"] == 0

    from app.repositories import engagement as eng_repo
    tasks = eng_repo.list_tasks(session, group_id=gid)
    runs = eng_repo.list_runs_for_task(session, tasks[0]["id"])
    # Runs are QUEUED — the outbox owns retry semantics, not the run state.
    assert runs[0]["result"] == "QUEUED"

    # Outbox row exists, not yet sent.
    from sqlalchemy import text
    from app.db import engine
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT intent, sent_at FROM outbox "
            "WHERE business_ref = :ref"
        ), {"ref": f"engagement_run:{runs[0]['id']}"}).first()
    assert row is not None
    assert row.intent == "submit_engagement_run"
    assert row.sent_at is None


def test_create_engagement_comment_rejects_bad_link(authed_client, session):
    gid, aid = _seed_account(session)
    r = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://example.com/nope"],
        "target_publication_ids": [],
        "comment_probability": 100,
    })
    assert r.status_code == 400
    assert "TikTok video URL" in r.json()["detail"]


def test_create_engagement_comment_outbox_row_persists_on_defer_failure(
        authed_client, session, monkeypatch):
    """Companion to the fall-back test above: confirm the outbox row
    survives the failure (UNIQUE on (intent, business_ref) WHERE
    sent_at IS NULL keeps it pending for the dispatcher)."""
    gid, aid = _seed_account(session)

    async def fake_defer_async(**kwargs):
        raise RuntimeError("queue unavailable")

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        fake_defer_async)
    # .configure(...).defer_async(...) must also blow up.
    boom_deferrer = type("BoomDeferrer", (),
                         {"defer_async": staticmethod(fake_defer_async)})()
    monkeypatch.setattr(submit_engagement_run_task, "configure",
                        lambda **kw: boom_deferrer)

    r = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "target_publication_ids": [],
        "comment_probability": 100,
    })

    assert r.status_code == 200

    from sqlalchemy import text
    from app.db import engine
    with engine.begin() as conn:
        pending = conn.execute(text(
            "SELECT count(*) FROM outbox "
            "WHERE intent = 'submit_engagement_run' AND sent_at IS NULL"
        )).scalar()
    assert pending == 1


def test_engagement_task_detail_returns_runs(
        authed_client, session, monkeypatch):
    gid, aid = _seed_account(session)

    async def fake_defer_async(**kwargs):
        return type("Job", (), {"id": "job-1"})()

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        fake_defer_async)
    # .configure(...).defer_async(...) must also reach the fake.
    fake_deferrer = type("FakeDeferrer", (),
                        {"defer_async": staticmethod(fake_defer_async)})()
    monkeypatch.setattr(submit_engagement_run_task, "configure",
                        lambda **kw: fake_deferrer)

    created = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "target_publication_ids": [],
        "comment_probability": 100,
    }).json()

    r = authed_client.get(f"/api/engagement/tasks/{created['task_id']}")

    assert r.status_code == 200
    assert r.json()["task"]["id"] == created["task_id"]
    assert len(r.json()["runs"]) == 1


def test_engagement_runs_endpoint_returns_comment_details(
        authed_client, session, monkeypatch):
    gid, aid = _seed_account(session)

    async def fake_defer_async(**kwargs):
        return type("Job", (), {"id": "job-1"})()

    from app.tasks.engagement import submit_engagement_run_task
    monkeypatch.setattr(submit_engagement_run_task, "defer_async",
                        fake_defer_async)
    # .configure(...).defer_async(...) must also reach the fake.
    fake_deferrer = type("FakeDeferrer", (),
                        {"defer_async": staticmethod(fake_defer_async)})()
    monkeypatch.setattr(submit_engagement_run_task, "configure",
                        lambda **kw: fake_deferrer)

    created = authed_client.post("/api/engagement/comment", json={
        "group_id": gid,
        "account_ids": [aid],
        "target_links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "target_publication_ids": [],
        "comment_probability": 100,
    }).json()

    r = authed_client.get(f"/api/engagement/runs?group_id={gid}")

    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["engagement_task_id"] == created["task_id"]
    assert row["target_link"] == "https://www.tiktok.com/@owner/video/1234567890"
    assert row["account_profile_name"] == "M1"
    assert row["task_status"] == "QUEUED"


def test_discover_targets_returns_unused(authed_client, session):
    from datetime import datetime, timezone

    from app.models import DiscoveredVideo

    session.add(DiscoveredVideo(
        url="https://www.tiktok.com/@a/video/1",
        video_id="1",
        search_keyword="ielts",
        title="hello",
        author_nickname="A",
        play_count=2_100_000,
        like_count=100_000,
        create_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
    ))
    session.commit()
    r = authed_client.get("/api/engagement/discover-targets")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["url"] == "https://www.tiktok.com/@a/video/1"
    assert item["title"] == "hello"
    assert item["author_nickname"] == "A"
    assert item["play_count"] == 2_100_000
    assert item["like_count"] == 100_000
    assert item["create_time"].startswith("2026-06-01")
    assert item["search_keyword"] == "ielts"


def _seed_run_with_result(session, *, gid: int, aid: int, source: str,
                          result: str, link: str) -> int:
    from app.repositories import engagement as eng_repo

    tid = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source=source,
        target_links=[link],
        target_publication_ids=[],
        comment_probability=100,
    )
    rid = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=aid,
        target_link=link, action="tiktok_ai_comment",
    )
    if result == "SUCCESS":
        eng_repo.set_result(session, rid, "SUCCESS", "ok")
    elif result == "FAILURE":
        eng_repo.set_result(session, rid, "FAILURE", "boom")
    elif result == "QUEUED":
        eng_repo.mark_queued(session, rid)
    elif result == "INITIATED":
        eng_repo.mark_queued(session, rid)
        eng_repo.mark_initiated(
            session, rid, task_id="gl-task", schedule_at=0, notes="started",
        )
    return rid


def test_engagement_runs_endpoint_supports_before_id_cursor(
        authed_client, session):
    gid, aid = _seed_account(session)
    older = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="SUCCESS", link="https://x/v/1",
    )
    middle = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="QUEUED", link="https://x/v/2",
    )
    newest = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="QUEUED", link="https://x/v/3",
    )

    page1 = authed_client.get("/api/engagement/runs?limit=2").json()
    assert [r["id"] for r in page1["items"]] == [newest, middle]

    page2 = authed_client.get(
        f"/api/engagement/runs?limit=2&before_id={middle}"
    ).json()
    assert [r["id"] for r in page2["items"]] == [older]


def test_engagement_runs_endpoint_filters_by_ids(authed_client, session):
    gid, aid = _seed_account(session)
    older = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="QUEUED", link="https://x/v/1",
    )
    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="SUCCESS", link="https://x/v/2",
    )
    newest = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="INITIATED", link="https://x/v/3",
    )

    page = authed_client.get(
        f"/api/engagement/runs?ids={older}&ids={newest}&limit=50"
    ).json()

    assert [r["id"] for r in page["items"]] == [newest, older]
    assert [r["result"] for r in page["items"]] == ["INITIATED", "QUEUED"]


def test_engagement_runs_endpoint_filters_by_status_and_source(
        authed_client, session):
    gid, aid = _seed_account(session)
    owned_success = _seed_run_with_result(
        session, gid=gid, aid=aid, source="publication_share_links",
        result="SUCCESS", link="https://x/v/1",
    )
    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="FAILURE", link="https://x/v/2",
    )
    manual_success = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="SUCCESS", link="https://x/v/3",
    )

    success = authed_client.get(
        "/api/engagement/runs?status=success"
    ).json()
    assert [r["id"] for r in success["items"]] == [
        manual_success, owned_success,
    ]

    owned = authed_client.get(
        "/api/engagement/runs?source=owned"
    ).json()
    assert [r["id"] for r in owned["items"]] == [owned_success]

    manual_success_only = authed_client.get(
        "/api/engagement/runs?status=success&source=manual"
    ).json()
    assert [r["id"] for r in manual_success_only["items"]] == [manual_success]


def test_engagement_runs_stats_endpoint(authed_client, session):
    gid, aid = _seed_account(session)
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="publication_share_links", result="SUCCESS",
                          link="https://x/v/1")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="publication_share_links", result="INITIATED",
                          link="https://x/v/2")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="manual_links", result="FAILURE",
                          link="https://x/v/3")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="manual_links", result="QUEUED",
                          link="https://x/v/4")

    all_stats = authed_client.get("/api/engagement/runs/stats").json()
    assert all_stats == {
        "total": 4, "success": 1, "running": 1, "queued": 1, "failed": 1,
    }
    owned = authed_client.get(
        "/api/engagement/runs/stats?source=owned"
    ).json()
    assert owned == {
        "total": 2, "success": 1, "running": 1, "queued": 0, "failed": 0,
    }
    manual = authed_client.get(
        "/api/engagement/runs/stats?source=manual"
    ).json()
    assert manual == {
        "total": 2, "success": 0, "running": 0, "queued": 1, "failed": 1,
    }


def test_discover_targets_respects_limit_param(authed_client, session):
    from app.models import DiscoveredVideo

    for i in range(5):
        session.add(DiscoveredVideo(
            url=f"https://x/{i}", video_id=str(i),
            search_keyword="ielts", play_count=i,
        ))
    session.commit()
    r = authed_client.get("/api/engagement/discover-targets?limit=2")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2
