import pytest
from sqlmodel import Session

from app.db import engine
from app.repositories import accounts as acc_repo


@pytest.fixture(autouse=True)
def _authed(client, monkeypatch):
    """Authenticate the shared TestClient for every test in this file.

    Engagement routes are mounted with `Depends(require_auth)` so the
    plan's tests would 401 without a session cookie. We monkeypatch the
    dashboard password to a known value and POST /api/login once.
    """
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    yield


def _seed_fb_phone(session, *, group_id, profile="fb-acc"):
    return acc_repo.create(session, {
        "profile_name": profile,
        "facebook_logged_in": 1, "warmed_up": 1,
        "geelark_phone_id": f"ph-{profile}",
        "group_id": group_id,
    })


def test_post_comment_facebook_creates_runs(
        client, default_group_id, in_memory_procrastinate):
    with Session(engine) as s:
        aid = _seed_fb_phone(s, group_id=default_group_id)
    body = {
        "account_ids": [aid],
        "target_links": ["https://www.facebook.com/zuck/posts/abc"],
        "candidates_per_post": 2,
        "keywords": ["english", "tip"],
        "comment_mode": "provided",
        "provided_comments": ["Nice!", "Great!"],
    }
    r = client.post("/api/engagement/comment/facebook", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    # FB: candidates_per_post is the pool size inside ONE run (GeeLark picks
    # one). 1 phone × 1 target × 1 inner = 1 run.
    assert data["created_runs"] == 1
    assert isinstance(data["task_id"], int)
    assert data["deferred_runs"] == 1
    assert len(in_memory_procrastinate.jobs) == 1


def test_post_comment_facebook_rejects_invalid_keywords(client, default_group_id):
    with Session(engine) as s:
        aid = _seed_fb_phone(s, group_id=default_group_id)
    body = {
        "account_ids": [aid],
        "target_links": ["https://www.facebook.com/zuck/posts/abc"],
        "candidates_per_post": 1,
        "keywords": [],
        "comment_mode": "provided",
        "provided_comments": ["c1"],
    }
    r = client.post("/api/engagement/comment/facebook", json=body)
    assert r.status_code == 400
    assert "keywords" in r.json()["detail"].lower()


def test_post_comment_facebook_rejects_geelark_ai_mode(client, default_group_id):
    with Session(engine) as s:
        aid = _seed_fb_phone(s, group_id=default_group_id)
    body = {
        "account_ids": [aid],
        "target_links": ["https://www.facebook.com/zuck/posts/abc"],
        "candidates_per_post": 1,
        "keywords": ["k1"],
        "comment_mode": "geelark_ai",
    }
    r = client.post("/api/engagement/comment/facebook", json=body)
    assert r.status_code == 400
    assert "comment_mode" in r.json()["detail"].lower()


def test_get_engagement_runs_filters_by_platform(client, default_group_id):
    from app.repositories import engagement as eng_repo

    with Session(engine) as s:
        tt_aid = acc_repo.create(s, {
            "profile_name": "tt-acc",
            "tiktok_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-tt",
            "group_id": default_group_id,
        })
        fb_aid = _seed_fb_phone(s, group_id=default_group_id, profile="fb-acc-runs")
        tt_link = "https://www.tiktok.com/@x/video/1"
        fb_link = "https://www.facebook.com/zuck/posts/abc"

        tt_task = eng_repo.create_task(
            s, group_id=default_group_id, action="tiktok_ai_comment",
            source="manual_links", target_links=[tt_link],
            target_publication_ids=[], comment_probability=100,
            platform="tiktok",
        )
        eng_repo.create_run(
            s, engagement_task_id=tt_task, account_id=tt_aid,
            target_link=tt_link, action="tiktok_ai_comment",
        )
        fb_task = eng_repo.create_task(
            s, group_id=default_group_id, action="facebook_auto_comment",
            source="manual_links", target_links=[fb_link],
            target_publication_ids=[], comment_probability=100,
            platform="facebook", keywords=["k1"],
        )
        eng_repo.create_run(
            s, engagement_task_id=fb_task, account_id=fb_aid,
            target_link=fb_link, action="facebook_auto_comment",
        )

    r = client.get("/api/engagement/runs?platform=facebook")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["target_link"] == fb_link

    r2 = client.get("/api/engagement/runs?platform=tiktok")
    assert r2.status_code == 200
    items2 = r2.json()["items"]
    assert len(items2) == 1
    assert items2[0]["target_link"] == tt_link

    r_all = client.get("/api/engagement/runs")
    assert r_all.status_code == 200
    assert len(r_all.json()["items"]) == 2

    stats_fb = client.get("/api/engagement/runs/stats?platform=facebook")
    assert stats_fb.status_code == 200
    assert stats_fb.json()["total"] == 1
    assert stats_fb.json()["queued"] == 1

    stats_tt = client.get("/api/engagement/runs/stats?platform=tiktok")
    assert stats_tt.status_code == 200
    assert stats_tt.json()["total"] == 1
    assert stats_tt.json()["queued"] == 1


def test_get_eligible_phones_filters_by_platform(client, default_group_id):
    with Session(engine) as s:
        tt_aid = acc_repo.create(s, {
            "profile_name": "tt-only",
            "tiktok_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-tt-only",
            "group_id": default_group_id,
        })
        fb_aid = _seed_fb_phone(s, group_id=default_group_id, profile="fb-only")

    r_fb = client.get("/api/engagement/eligible-phones?platform=facebook")
    assert r_fb.status_code == 200, r_fb.text
    fb_ids = {it["id"] for it in r_fb.json()["items"]}
    assert fb_aid in fb_ids
    assert tt_aid not in fb_ids

    r_tt = client.get("/api/engagement/eligible-phones?platform=tiktok")
    assert r_tt.status_code == 200
    tt_ids = {it["id"] for it in r_tt.json()["items"]}
    assert tt_aid in tt_ids
    assert fb_aid not in tt_ids

    # Default (no param) keeps backward-compat tiktok behavior.
    r_default = client.get("/api/engagement/eligible-phones")
    assert r_default.status_code == 200
    default_ids = {it["id"] for it in r_default.json()["items"]}
    assert tt_aid in default_ids
    assert fb_aid not in default_ids


def test_get_engagement_targets_filters_by_platform(client, default_group_id):
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo

    with Session(engine) as s:
        tt_aid = acc_repo.create(s, {
            "profile_name": "tt-target",
            "tiktok_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-tt-target",
            "group_id": default_group_id,
        })
        fb_aid = _seed_fb_phone(s, group_id=default_group_id, profile="fb-target")

        tt_vid = vid_repo.create(s, "tt.mp4", "/tmp/tt.mp4",
                                  group_id=default_group_id)
        tt_pid = pub_repo.create(s, video_id=tt_vid, platform="tiktok")
        pub_repo.set_account(s, tt_pid, tt_aid)
        pub_repo.set_result(s, tt_pid, "SUCCESS", "done")
        pub_repo.set_share_link(
            s, tt_pid, "https://www.tiktok.com/@x/video/9999",
        )

        fb_vid = vid_repo.create(s, "fb.mp4", "/tmp/fb.mp4",
                                  group_id=default_group_id)
        fb_pid = pub_repo.create(s, video_id=fb_vid, platform="facebook")
        pub_repo.set_account(s, fb_pid, fb_aid)
        pub_repo.set_result(s, fb_pid, "SUCCESS", "done")
        pub_repo.set_share_link(
            s, fb_pid, "https://www.facebook.com/zuck/posts/8888",
        )

    r_fb = client.get(
        f"/api/engagement/targets?group_id={default_group_id}&platform=facebook"
    )
    assert r_fb.status_code == 200, r_fb.text
    fb_items = r_fb.json()["items"]
    assert len(fb_items) == 1
    assert fb_items[0]["publication_id"] == fb_pid

    r_tt = client.get(
        f"/api/engagement/targets?group_id={default_group_id}&platform=tiktok"
    )
    assert r_tt.status_code == 200
    tt_items = r_tt.json()["items"]
    assert len(tt_items) == 1
    assert tt_items[0]["publication_id"] == tt_pid

    # Default (no param) keeps backward-compat tiktok behavior.
    r_default = client.get(
        f"/api/engagement/targets?group_id={default_group_id}"
    )
    assert r_default.status_code == 200
    default_items = r_default.json()["items"]
    assert len(default_items) == 1
    assert default_items[0]["publication_id"] == tt_pid
