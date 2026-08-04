"""CRUD over /api/comment-prompts + delete-in-use 409."""
import pytest

from app import config as cfg
from app.repositories import accounts as acc_repo
from app.repositories import comment_prompts as cp_repo
from app.repositories import engagement as eng_repo


@pytest.fixture
def authed_client(client, monkeypatch):
    r = client.post("/api/login", json={"username": "tester", "password": "pw"})
    assert r.status_code == 200
    return client


def test_create_then_list(authed_client, session):
    r = authed_client.post(
        "/api/comment-prompts",
        json={"name": "viral", "body": "Big creator viral video"},
    )
    assert r.status_code == 200
    pid = r.json()["id"]

    rows = authed_client.get("/api/comment-prompts").json()
    assert any(p["id"] == pid and p["name"] == "viral" for p in rows)


def test_create_rejects_empty_name(authed_client):
    r = authed_client.post(
        "/api/comment-prompts", json={"name": " ", "body": "x"}
    )
    assert r.status_code == 400


def test_create_rejects_empty_body(authed_client):
    r = authed_client.post(
        "/api/comment-prompts", json={"name": "x", "body": ""}
    )
    assert r.status_code == 400


def test_create_rejects_duplicate_name(authed_client):
    authed_client.post(
        "/api/comment-prompts", json={"name": "dup", "body": "a"}
    )
    r = authed_client.post(
        "/api/comment-prompts", json={"name": "dup", "body": "b"}
    )
    assert r.status_code == 400


def test_patch_updates_body(authed_client, session):
    pid = cp_repo.create(session, name="p", body="old")
    r = authed_client.patch(
        f"/api/comment-prompts/{pid}", json={"body": "new"}
    )
    assert r.status_code == 200
    assert cp_repo.get(session, pid)["body"] == "new"


def test_patch_404_for_unknown(authed_client):
    r = authed_client.patch(
        "/api/comment-prompts/99999", json={"body": "x"}
    )
    assert r.status_code == 404


def test_delete_unused(authed_client, session):
    pid = cp_repo.create(session, name="p", body="b")
    r = authed_client.delete(f"/api/comment-prompts/{pid}")
    assert r.status_code == 200
    assert cp_repo.get(session, pid) is None


def test_delete_blocked_by_inflight_run(authed_client, session):
    pid = cp_repo.create(session, name="p", body="b")
    acc = acc_repo.create(session, {
        "profile_name": "ph",
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    tid = eng_repo.create_task(
        session,
        group_id=None,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_prompt_id=pid,
        comments_per_video=1,
    )
    eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=acc,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )
    r = authed_client.delete(f"/api/comment-prompts/{pid}")
    assert r.status_code == 409
    body = r.json()
    assert "runs" in body["detail"]
    assert cp_repo.get(session, pid) is not None
