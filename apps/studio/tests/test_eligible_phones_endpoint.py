from fastapi.testclient import TestClient


def test_eligible_phones_returns_commenter_eligible_accounts(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.main import create_app

    gid = group_repo.create(session, name="G1")
    eligible = acc_repo.create(session, {
        "profile_name": "good_phone",
        "geelark_phone_id": "ph-good",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    acc_repo.create(session, {
        "profile_name": "warmed_but_logged_out",
        "geelark_phone_id": "ph-out",
        "group_id": gid,
        "tiktok_logged_in": 0,
        "warmed_up": 1,
    })
    acc_repo.create(session, {
        "profile_name": "logged_in_not_warmed",
        "geelark_phone_id": "ph-cold",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 0,
    })

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "tester", "password": "pw"})
        r = client.get("/api/engagement/eligible-phones")
    assert r.status_code == 200
    items = r.json()["items"]
    profile_names = {it["profile_name"] for it in items}
    assert "good_phone" in profile_names
    assert "warmed_but_logged_out" not in profile_names
    assert "logged_in_not_warmed" not in profile_names

    good = next(it for it in items if it["profile_name"] == "good_phone")
    assert good["id"] == eligible
    assert good["group_name"] == "G1"
    assert good["busy"] is False


def test_eligible_phones_marks_busy_when_inflight_run_exists(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo
    from app.main import create_app

    gid = group_repo.create(session, name="G1")
    acc_id = acc_repo.create(session, {
        "profile_name": "busy_phone",
        "geelark_phone_id": "ph-busy",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })

    tid = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_brief=None,
        comments_per_video=1,
    )
    eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=acc_id,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "tester", "password": "pw"})
        r = client.get("/api/engagement/eligible-phones")
    busy = next(it for it in r.json()["items"] if it["profile_name"] == "busy_phone")
    assert busy["busy"] is True


def test_eligible_phones_excludes_known_bad_proxy(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.main import create_app

    gid = group_repo.create(session, name="G1")
    acc_repo.create(session, {
        "profile_name": "bad_proxy",
        "geelark_phone_id": "ph-bad-proxy",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
        "proxy_status": "fail",
    })

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "tester", "password": "pw"})
        response = client.get("/api/engagement/eligible-phones")

    assert response.status_code == 200
    assert "bad_proxy" not in {
        item["profile_name"] for item in response.json()["items"]
    }


def test_eligible_phones_marks_daily_limit_reached(session):
    """When a phone has met the configured daily comment limit for the
    queried platform, the response should flag it so the UI can disable it."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo
    from app.repositories import settings as srepo
    from app.main import create_app

    gid = group_repo.create(session, name="G1")
    capped = acc_repo.create(session, {
        "profile_name": "capped_phone",
        "geelark_phone_id": "ph-capped",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    fresh = acc_repo.create(session, {
        "profile_name": "fresh_phone",
        "geelark_phone_id": "ph-fresh",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })

    srepo.update(session, {"daily_comment_limit_tiktok": "2"})

    # Two SUCCESS runs to exhaust the capped phone's TikTok quota.
    for i in range(2):
        tid = eng_repo.create_task(
            session, group_id=gid, action="tiktok_ai_comment",
            source="manual_links",
            target_links=[f"https://www.tiktok.com/@x/video/{i}"],
            target_publication_ids=[], comment_probability=100,
        )
        rid = eng_repo.create_run(
            session, engagement_task_id=tid, account_id=capped,
            target_link=f"https://www.tiktok.com/@x/video/{i}",
            action="tiktok_ai_comment",
        )
        eng_repo.set_result(session, rid, "SUCCESS", "ok")

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "tester", "password": "pw"})
        r = client.get("/api/engagement/eligible-phones")

    by_name = {it["profile_name"]: it for it in r.json()["items"]}
    assert by_name["capped_phone"]["daily_limit_reached"] is True
    assert by_name["fresh_phone"]["daily_limit_reached"] is False


def test_eligible_phones_daily_limit_zero_disables_flag(session):
    """daily_comment_limit_tiktok='0' disables the cap; flag stays False
    even for phones with many runs today."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo
    from app.repositories import settings as srepo
    from app.main import create_app

    gid = group_repo.create(session, name="G1")
    aid = acc_repo.create(session, {
        "profile_name": "heavy_phone",
        "geelark_phone_id": "ph-heavy",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })

    srepo.update(session, {"daily_comment_limit_tiktok": "0"})
    for i in range(5):
        tid = eng_repo.create_task(
            session, group_id=gid, action="tiktok_ai_comment",
            source="manual_links",
            target_links=[f"https://www.tiktok.com/@x/video/{i}"],
            target_publication_ids=[], comment_probability=100,
        )
        rid = eng_repo.create_run(
            session, engagement_task_id=tid, account_id=aid,
            target_link=f"https://www.tiktok.com/@x/video/{i}",
            action="tiktok_ai_comment",
        )
        eng_repo.set_result(session, rid, "SUCCESS", "ok")

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "tester", "password": "pw"})
        r = client.get("/api/engagement/eligible-phones")

    heavy = next(it for it in r.json()["items"] if it["profile_name"] == "heavy_phone")
    assert heavy["daily_limit_reached"] is False
