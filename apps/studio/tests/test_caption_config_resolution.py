"""posting_service._resolve_caption_config — prompt-pool resolution."""
import pytest

from app.repositories import caption_prompts as cp_repo
from app.repositories import settings as srepo
from app.services import groups_service as gs
from app.services import posting_service as ps
from app.services.posting_service import CaptionPromptMissing

pytestmark = pytest.mark.no_auto_prompt


def _bind_prompt(session, gid: int, *, name: str, body: str) -> int:
    pid = cp_repo.create(session, name=name, body=body)
    gs.set_caption_prompt(session, gid, pid)
    return pid


def test_uses_group_bound_prompt_body(session):
    gid = gs.create(session, name="g")
    _bind_prompt(session, gid, name="g prompt", body="bound brief")
    cfg = ps._resolve_caption_config(session, gid)
    assert cfg["brief"] == "bound brief"


def test_missing_when_group_has_no_prompt(session):
    gid = gs.create(session, name="g")
    with pytest.raises(CaptionPromptMissing):
        ps._resolve_caption_config(session, gid)


def test_missing_when_group_id_is_none(session):
    with pytest.raises(CaptionPromptMissing):
        ps._resolve_caption_config(session, None)


def test_missing_when_prompt_body_is_blank(session):
    gid = gs.create(session, name="g")
    # bypass service-level validation by writing directly via repo
    pid = cp_repo.create(session, name="empty", body="placeholder")
    gs.set_caption_prompt(session, gid, pid)
    cp_repo.update(session, pid, body="   ")
    with pytest.raises(CaptionPromptMissing):
        ps._resolve_caption_config(session, gid)


def test_missing_when_group_not_found(session):
    with pytest.raises(CaptionPromptMissing):
        ps._resolve_caption_config(session, 99999)


def test_cfg_has_no_max_len_field(session):
    """max_len was removed — captions always run with generate_caption's
    built-in CAPTION_MAX_LEN_DEFAULT (220)."""
    gid = gs.create(session, name="g")
    _bind_prompt(session, gid, name="p", body="b")
    cfg = ps._resolve_caption_config(session, gid)
    assert "max_len" not in cfg


def test_thinking_budget_zero_when_off(session):
    gid = gs.create(session, name="g")
    _bind_prompt(session, gid, name="p", body="b")
    srepo.update(session, {"gemini_high_quality": "0"})
    cfg = ps._resolve_caption_config(session, gid)
    assert cfg["thinking_budget"] == 0


def test_thinking_budget_4096_when_on(session):
    gid = gs.create(session, name="g")
    _bind_prompt(session, gid, name="p", body="b")
    srepo.update(session, {"gemini_high_quality": "1"})
    cfg = ps._resolve_caption_config(session, gid)
    assert cfg["thinking_budget"] == 4096


def test_model_passed_through(session):
    gid = gs.create(session, name="g")
    _bind_prompt(session, gid, name="p", body="b")
    srepo.update(session, {"gemini_model": "gemini-3-flash-preview"})
    cfg = ps._resolve_caption_config(session, gid)
    assert cfg["model"] == "gemini-3-flash-preview"


def test_cfg_has_no_banned_terms_field(session):
    """banned_terms hard-filter was removed — prompt-only content control."""
    gid = gs.create(session, name="g")
    _bind_prompt(session, gid, name="p", body="b")
    cfg = ps._resolve_caption_config(session, gid)
    assert "banned_terms" not in cfg


def test_group_has_caption_prompt_truthy(session):
    gid = gs.create(session, name="g")
    _bind_prompt(session, gid, name="p", body="b")
    assert ps.group_has_caption_prompt(session, gid) is True


def test_group_has_caption_prompt_false_when_missing(session):
    gid = gs.create(session, name="g")
    assert ps.group_has_caption_prompt(session, gid) is False


def test_group_has_caption_prompt_false_for_none(session):
    assert ps.group_has_caption_prompt(session, None) is False
