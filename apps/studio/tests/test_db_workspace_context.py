from sqlalchemy import text

from app.db import session_scope, set_workspace_context


def test_workspace_context_survives_commit():
    with session_scope(workspace_id=7) as session:
        before = session.execute(text(
            "SELECT current_setting('app.workspace_id', true)"
        )).scalar_one()
        session.commit()
        after = session.execute(text(
            "SELECT current_setting('app.workspace_id', true)"
        )).scalar_one()

    assert before == "7"
    assert after == "7"


def test_system_session_can_narrow_to_workspace_across_commit():
    with session_scope() as session:
        bypass_before = session.execute(text(
            "SELECT current_setting('app.workspace_bypass', true)"
        )).scalar_one()
        set_workspace_context(session, 9)
        session.commit()
        workspace_after = session.execute(text(
            "SELECT current_setting('app.workspace_id', true)"
        )).scalar_one()
        bypass_after = session.execute(text(
            "SELECT current_setting('app.workspace_bypass', true)"
        )).scalar_one()

    assert bypass_before == "on"
    assert workspace_after == "9"
    assert bypass_after == "off"
