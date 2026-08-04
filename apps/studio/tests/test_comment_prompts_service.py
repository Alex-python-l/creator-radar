"""Validation + delete-in-use behaviour for comment_prompts_service."""
import pytest

from app.repositories import accounts as acc_repo
from app.repositories import comment_prompts as cp_repo
from app.repositories import engagement as eng_repo
from app.services import comment_prompts_service as svc


def test_create_strips_and_persists(session):
    pid = svc.create(session, name="  IELTS lane ", body=" tone: snarky ")
    row = cp_repo.get(session, pid)
    assert row["name"] == "IELTS lane"
    assert row["body"] == "tone: snarky"


def test_create_rejects_empty_name(session):
    with pytest.raises(ValueError, match="name required"):
        svc.create(session, name="   ", body="b")


def test_create_rejects_empty_body(session):
    with pytest.raises(ValueError, match="body required"):
        svc.create(session, name="n", body="   ")


def test_create_rejects_overlong_name(session):
    with pytest.raises(ValueError, match="name too long"):
        svc.create(session, name="x" * 121, body="b")


def test_create_rejects_duplicate_name(session):
    svc.create(session, name="dup", body="a")
    with pytest.raises(ValueError, match="name already used"):
        svc.create(session, name="dup", body="b")


def test_update_can_rename_to_unused_name(session):
    pid = svc.create(session, name="old", body="b")
    assert svc.update(session, pid, name="new") is True
    assert cp_repo.get(session, pid)["name"] == "new"


def test_update_rejects_rename_to_used_name(session):
    a = svc.create(session, name="a", body="x")
    svc.create(session, name="b", body="y")
    with pytest.raises(ValueError, match="name already used"):
        svc.update(session, a, name="b")


def test_delete_unused(session):
    pid = svc.create(session, name="p", body="b")
    assert svc.delete(session, pid) is True
    assert cp_repo.get(session, pid) is None


def test_delete_blocked_when_referenced_by_inflight_run(session):
    pid = svc.create(session, name="p", body="b")
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

    with pytest.raises(svc.PromptInUse) as exc:
        svc.delete(session, pid)
    assert exc.value.prompt_id == pid
    assert len(exc.value.run_ids) == 1
    assert cp_repo.get(session, pid) is not None


def test_delete_allowed_when_referenced_run_is_terminal(session):
    pid = svc.create(session, name="p", body="b")
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
    rid = eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=acc,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )
    eng_repo.set_result(session, rid, "SUCCESS", "ok")

    assert svc.delete(session, pid) is True
    assert cp_repo.get(session, pid) is None
