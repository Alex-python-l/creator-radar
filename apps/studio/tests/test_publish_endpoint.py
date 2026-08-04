"""Tests for the per-publication publish endpoint.

POST /api/videos/{vid}/publish?platform=<plat> defers a single
(video, platform) publication to the Procrastinate worker and returns
{"status": "QUEUED", "job_id": ..., "publication_id": ...}.

Phase-2 history: the original endpoint (Phase 2 Task 5) deferred a
per-video task (publish_video_task) and required the video row to have
account_id + result=PENDING. Task 7/8 removed both columns from Video
(state moved to publications) and Task 9 reshaped the endpoint to take
a per-call account_id + platform. The 409-when-not-PENDING and
500-when-no-account-column tests have been deleted with their Phase-2
behavior; the rest were rewritten around the publications shape.
"""

import pytest


@pytest.fixture
def authed_client(client):
    """A test client logged in as the conftest-seeded 'tester' account."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    return client


def test_publish_endpoint_returns_queued(
        authed_client, procrastinate_app, tmp_path, monkeypatch,
        default_group_id):
    """POST /api/videos/{vid}/publish?platform=tiktok returns
    {"status": "QUEUED", "job_id": ..., "publication_id": ...}, and the
    target publication is QUEUED in the DB."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)

    # Create an account in the prompt-bound default group.
    acc_resp = authed_client.post(
        "/api/accounts", json={"profile_name": "pub-test-acc"})
    assert acc_resp.status_code == 200, acc_resp.text
    aid = acc_resp.json()["id"]
    # Stamp group_id directly — the /api/accounts POST body doesn't
    # accept it, but the publish endpoint reads it via account_id.
    from app.db import engine as _eng
    from sqlalchemy import text as _t
    with _eng.begin() as _c:
        _c.execute(_t("UPDATE accounts SET group_id = :gid WHERE id = :aid"),
                   {"gid": default_group_id, "aid": aid})

    # Upload a video — auto-creates one PENDING tiktok publication.
    # We avoid passing group_id at upload time (which would also wake the
    # scan job and pollute connector.jobs) — instead stamp it via SQL.
    vid_resp = authed_client.post(
        "/api/videos/upload",
        files={"files": ("clip.mp4", b"fake-mp4-bytes", "video/mp4")},
    )
    assert vid_resp.status_code == 200
    vids = authed_client.get("/api/videos").json()["items"]
    assert len(vids) == 1
    vid_id = vids[0]["id"]
    with _eng.begin() as _c:
        _c.execute(_t("UPDATE videos SET group_id = :gid WHERE id = :vid"),
                   {"gid": default_group_id, "vid": vid_id})

    # Trigger the per-publication publish endpoint.
    pub_resp = authed_client.post(
        f"/api/videos/{vid_id}/publish?platform=tiktok",
        json={"account_id": aid})
    assert pub_resp.status_code == 200, pub_resp.text
    data = pub_resp.json()
    assert data["status"] == "QUEUED", f"unexpected response: {data}"
    assert "publication_id" in data, f"publication_id missing: {data}"

    # The publication row must be QUEUED, and the phone stamp lives on
    # that platform publication.
    # publications/account_id not in list envelope — query DB directly
    from app.db import engine
    from app.repositories import videos as vr
    from sqlmodel import Session
    with Session(engine) as sess:
        db_videos = vr.list_all(sess)
    row = next(v for v in db_videos if v["id"] == vid_id)
    pub = next(p for p in row["publications"] if p["platform"] == "tiktok")
    assert pub["result"] == "QUEUED", f"expected QUEUED, got {pub['result']}"
    assert pub["account_id"] == aid
    assert row["account_id"] is None
    assert pub["id"] == data["publication_id"]

    # The Procrastinate InMemoryConnector got the deferred publish job.
    # /api/videos/upload also defers an extract_understanding job per
    # video (aae0521) so filter to the task we actually care about.
    connector = procrastinate_app.connector
    publish_jobs = [
        j for j in connector.jobs.values()
        if "publish_publication" in j["task_name"]
    ]
    assert len(publish_jobs) == 1, \
        f"expected 1 publish job, got {len(publish_jobs)}"
    job = publish_jobs[0]
    assert job["args"]["publication_id"] == data["publication_id"]


def _publish_jobs(connector):
    """Filter out the extract_understanding job that /api/videos/upload
    defers per video — the tests below assert on the publish flow only."""
    return [j for j in connector.jobs.values()
            if "publish_publication" in j["task_name"]]


def test_publish_endpoint_404_on_missing_video(
        authed_client, procrastinate_app):
    """Publishing a nonexistent video returns 404."""
    resp = authed_client.post(
        "/api/videos/99999/publish?platform=tiktok",
        json={"account_id": 1})
    assert resp.status_code == 404
    assert _publish_jobs(procrastinate_app.connector) == []


def test_publish_endpoint_requires_platform(
        authed_client, procrastinate_app, tmp_path, monkeypatch):
    """Calling /publish without a platform query parameter must 400."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    authed_client.post(
        "/api/videos/upload",
        files={"files": ("c.mp4", b"x", "video/mp4")},
    )
    vid_id = authed_client.get("/api/videos").json()["items"][0]["id"]
    resp = authed_client.post(
        f"/api/videos/{vid_id}/publish", json={"account_id": 1})
    assert resp.status_code == 400
    assert _publish_jobs(procrastinate_app.connector) == []


def test_publish_endpoint_400_without_account(
        authed_client, procrastinate_app, tmp_path, monkeypatch):
    """Publishing without an account_id in the body returns 400."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    authed_client.post(
        "/api/videos/upload",
        files={"files": ("d.mp4", b"x", "video/mp4")},
    )
    vid_id = authed_client.get("/api/videos").json()["items"][0]["id"]
    resp = authed_client.post(
        f"/api/videos/{vid_id}/publish?platform=tiktok", json={})
    assert resp.status_code == 400
    assert "account" in resp.json()["detail"].lower()
    assert _publish_jobs(procrastinate_app.connector) == []


def test_publish_endpoint_requires_auth(client, procrastinate_app):
    """The endpoint must reject unauthenticated requests."""
    resp = client.post(
        "/api/videos/1/publish?platform=tiktok",
        json={"account_id": 1})
    assert resp.status_code == 401
