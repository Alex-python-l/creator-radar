"""groups_service.set_caption_prompt — bind/unbind/error semantics."""
import pytest

from app.repositories import caption_prompts as cp_repo
from app.services import groups_service as gs

pytestmark = pytest.mark.no_auto_prompt


def test_bind_existing_prompt(session):
    gid = gs.create(session, name="g")
    pid = cp_repo.create(session, name="p", body="body")
    gs.set_caption_prompt(session, gid, pid)
    assert gs.get(session, gid)["caption_prompt_id"] == pid


def test_clear_with_none(session):
    gid = gs.create(session, name="g")
    pid = cp_repo.create(session, name="p", body="body")
    gs.set_caption_prompt(session, gid, pid)
    gs.set_caption_prompt(session, gid, None)
    assert gs.get(session, gid)["caption_prompt_id"] is None


def test_bind_unknown_prompt_raises(session):
    gid = gs.create(session, name="g")
    with pytest.raises(ValueError):
        gs.set_caption_prompt(session, gid, 99999)


def test_nonexistent_group_is_noop(session):
    gs.set_caption_prompt(session, 99999, None)  # should not raise


def test_deleting_prompt_clears_group_fk(session):
    """FK on caption_prompts(id) is ON DELETE SET NULL — verifies the
    migration's wiring so a hard delete doesn't orphan the row."""
    from app.repositories import caption_prompts as cp_repo
    from sqlalchemy import text
    gid = gs.create(session, name="g")
    pid = cp_repo.create(session, name="p", body="body")
    gs.set_caption_prompt(session, gid, pid)
    session.execute(text("DELETE FROM caption_prompts WHERE id = :pid"),
                    {"pid": pid})
    session.commit()
    assert gs.get(session, gid)["caption_prompt_id"] is None
