"""CRUD over /api/caption-prompts + delete-in-use 409."""
import pytest

from app import config as cfg
from app.repositories import caption_prompts as cp_repo
from app.services import groups_service as gs


@pytest.fixture
def authed_client(client, monkeypatch):
    r = client.post("/api/login", json={"username": "tester", "password": "pw"})
    assert r.status_code == 200
    return client


def test_create_then_list(authed_client, session):
    r = authed_client.post(
        "/api/caption-prompts",
        json={"name": "IELTS", "body": "Real-person IELTS lane"},
    )
    assert r.status_code == 200
    pid = r.json()["id"]

    rows = authed_client.get("/api/caption-prompts").json()
    assert any(p["id"] == pid and p["name"] == "IELTS" for p in rows)


def test_create_rejects_empty_name(authed_client):
    r = authed_client.post(
        "/api/caption-prompts", json={"name": "  ", "body": "x"}
    )
    assert r.status_code == 400


def test_create_rejects_empty_body(authed_client):
    r = authed_client.post(
        "/api/caption-prompts", json={"name": "x", "body": ""}
    )
    assert r.status_code == 400


def test_create_rejects_duplicate_name(authed_client):
    authed_client.post(
        "/api/caption-prompts", json={"name": "dup", "body": "a"}
    )
    r = authed_client.post(
        "/api/caption-prompts", json={"name": "dup", "body": "b"}
    )
    assert r.status_code == 400


def test_patch_updates_body(authed_client, session):
    pid = cp_repo.create(session, name="p", body="old")
    r = authed_client.patch(
        f"/api/caption-prompts/{pid}", json={"body": "new"}
    )
    assert r.status_code == 200
    assert cp_repo.get(session, pid)["body"] == "new"


def test_patch_404_for_unknown(authed_client):
    r = authed_client.patch(
        "/api/caption-prompts/99999", json={"body": "x"}
    )
    assert r.status_code == 404


def test_delete_unused(authed_client, session):
    pid = cp_repo.create(session, name="p", body="b")
    r = authed_client.delete(f"/api/caption-prompts/{pid}")
    assert r.status_code == 200
    assert cp_repo.get(session, pid) is None


def test_delete_blocked_when_bound_to_group(authed_client, session):
    pid = cp_repo.create(session, name="p", body="b")
    gid = gs.create(session, name="g")
    gs.set_caption_prompt(session, gid, pid)
    r = authed_client.delete(f"/api/caption-prompts/{pid}")
    assert r.status_code == 409
    body = r.json()
    assert "g" in body["detail"]["groups"]
    assert cp_repo.get(session, pid) is not None
