"""PATCH /api/groups/{id} accepts caption_prompt_id."""
import pytest

from app import config as cfg
from app.repositories import caption_prompts as cp_repo
from app.services import groups_service as gs

pytestmark = pytest.mark.no_auto_prompt


@pytest.fixture
def authed_client(client, monkeypatch):
    r = client.post("/api/login", json={"username": "tester", "password": "pw"})
    assert r.status_code == 200
    return client


def test_patch_caption_prompt_id_only(authed_client, session):
    gid = gs.create(session, name="g")
    pid = cp_repo.create(session, name="p", body="b")
    r = authed_client.patch(
        f"/api/groups/{gid}", json={"caption_prompt_id": pid}
    )
    assert r.status_code == 200
    assert gs.get(session, gid)["caption_prompt_id"] == pid


def test_patch_name_and_prompt_in_one_request(authed_client, session):
    gid = gs.create(session, name="g")
    pid = cp_repo.create(session, name="p", body="b")
    r = authed_client.patch(
        f"/api/groups/{gid}",
        json={"name": "renamed", "caption_prompt_id": pid},
    )
    assert r.status_code == 200
    row = gs.get(session, gid)
    assert row["name"] == "renamed"
    assert row["caption_prompt_id"] == pid


def test_patch_prompt_null_clears(authed_client, session):
    gid = gs.create(session, name="g")
    pid = cp_repo.create(session, name="p", body="b")
    gs.set_caption_prompt(session, gid, pid)
    r = authed_client.patch(
        f"/api/groups/{gid}", json={"caption_prompt_id": None}
    )
    assert r.status_code == 200
    assert gs.get(session, gid)["caption_prompt_id"] is None


def test_patch_unknown_prompt_id_returns_400(authed_client, session):
    gid = gs.create(session, name="g")
    r = authed_client.patch(
        f"/api/groups/{gid}", json={"caption_prompt_id": 99999}
    )
    assert r.status_code == 400


def test_patch_nonint_prompt_id_returns_400(authed_client, session):
    gid = gs.create(session, name="g")
    r = authed_client.patch(
        f"/api/groups/{gid}", json={"caption_prompt_id": "abc"}
    )
    assert r.status_code == 400


def test_patch_404_for_unknown_group(authed_client):
    r = authed_client.patch(
        "/api/groups/99999", json={"caption_prompt_id": None}
    )
    assert r.status_code == 404
