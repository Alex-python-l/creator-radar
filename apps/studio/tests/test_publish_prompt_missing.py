"""Groups without a caption_prompt are blocked from publishing:
- plan_assignments skips them
- next_publish_scan_ready_at skips them
- /videos/{vid}/publish returns 400
- /videos/publications/{pub_id}/retry returns 400
- submit_publication_to_geelark raises CaptionPromptMissing at preflight
"""
import pytest
from sqlalchemy import text

from app import config as cfg
from app.repositories import caption_prompts as cp_repo
from app.services import groups_service as gs
from app.services import posting_service as ps

pytestmark = pytest.mark.no_auto_prompt


@pytest.fixture
def authed_client(client, monkeypatch):
    r = client.post("/api/login", json={"username": "tester", "password": "pw"})
    assert r.status_code == 200
    return client


def _make_account_and_pub(session, *, group_id: int) -> tuple[int, int, int]:
    """Insert one warmed-up, logged-in TikTok account + one video + one
    PENDING publication targeting that account's group. Returns
    (account_id, video_id, publication_id)."""
    session.execute(text(
        "INSERT INTO accounts (profile_name, geelark_phone_id, group_id, "
        "tiktok_logged_in, warmed_up, pic_status, warmup_count, "
        "target_warmup_count, warmup_mode, youtube_logged_in, "
        "instagram_logged_in, facebook_logged_in) "
        "VALUES ('a1', 'env1', :gid, 1, 1, 'none', 0, 10, 'count', 0, 0, 0)"
    ), {"gid": group_id})
    aid = session.execute(
        text("SELECT id FROM accounts WHERE profile_name='a1'")
    ).scalar_one()
    session.execute(text(
        "INSERT INTO videos (original_filename, stored_path, group_id) "
        "VALUES ('v.mp4', '/tmp/v.mp4', :gid)"
    ), {"gid": group_id})
    vid = session.execute(
        text("SELECT id FROM videos WHERE original_filename='v.mp4'")
    ).scalar_one()
    session.execute(text(
        "INSERT INTO publications (video_id, platform, result) "
        "VALUES (:vid, 'tiktok', 'PENDING')"
    ), {"vid": vid})
    pid = session.execute(
        text("SELECT id FROM publications WHERE video_id = :vid"),
        {"vid": vid},
    ).scalar_one()
    session.commit()
    return aid, vid, pid


def test_plan_assignments_skips_groups_without_prompt(session):
    gid = gs.create(session, name="g")
    _make_account_and_pub(session, group_id=gid)
    assert ps.plan_assignments(session, platform="tiktok") == []


def test_plan_assignments_includes_groups_with_prompt(session):
    gid = gs.create(session, name="g")
    pid = cp_repo.create(session, name="p", body="b")
    gs.set_caption_prompt(session, gid, pid)
    _make_account_and_pub(session, group_id=gid)
    queued = ps.plan_assignments(session, platform="tiktok")
    assert len(queued) == 1


def test_next_publish_scan_ready_at_skips_groups_without_prompt(session):
    gid = gs.create(session, name="g")
    _make_account_and_pub(session, group_id=gid)
    assert ps.next_publish_scan_ready_at(
        session, cooldown_hours=2.0) is None


def test_publish_one_endpoint_returns_400_when_no_prompt(
        authed_client, session):
    gid = gs.create(session, name="g")
    aid, vid, _ = _make_account_and_pub(session, group_id=gid)
    r = authed_client.post(
        f"/api/videos/{vid}/publish?platform=tiktok",
        json={"account_id": aid},
    )
    assert r.status_code == 400
    assert "caption prompt" in r.json()["detail"]


def test_retry_endpoint_returns_400_when_no_prompt(authed_client, session):
    gid = gs.create(session, name="g")
    _aid, _vid, pid = _make_account_and_pub(session, group_id=gid)
    session.execute(text(
        "UPDATE publications SET result='FAILURE' WHERE id = :pid"
    ), {"pid": pid})
    session.commit()
    r = authed_client.post(f"/api/videos/publications/{pid}/retry")
    assert r.status_code == 400
    assert "caption prompt" in r.json()["detail"]


def test_submit_publication_preflight_raises_when_no_prompt(session):
    """Even if a publication gets to submit by some race (manual SQL,
    out-of-date job), the per-publication submit catches the missing
    prompt at preflight and marks the publication FAILURE."""
    gid = gs.create(session, name="g")
    _aid, _vid, pid = _make_account_and_pub(session, group_id=gid)
    session.execute(text(
        "UPDATE publications SET result='QUEUED' WHERE id = :pid"
    ), {"pid": pid})
    session.commit()

    class _DummyClient:
        pass

    with pytest.raises(ps.CaptionPromptMissing):
        ps.submit_publication_to_geelark(
            session, _DummyClient(), publication_id=pid)
    # publication should now be FAILURE
    row = session.execute(
        text("SELECT result, notes FROM publications WHERE id = :pid"),
        {"pid": pid},
    ).first()
    assert row[0] == "FAILURE"
    assert row[1] and "caption prompt" in row[1]
