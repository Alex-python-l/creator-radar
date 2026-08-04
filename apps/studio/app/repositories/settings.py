from sqlalchemy import text
from sqlmodel import Session, select

from app.models import Setting


def get_all(session: Session) -> dict:
    rows = session.exec(select(Setting)).all()
    return {r.key: r.value for r in rows}


def update(session: Session, values: dict) -> None:
    workspace_id = session.exec(
        select(Setting.workspace_id).limit(1)
    ).first()
    if workspace_id is None:
        # The DB default stamps the current request workspace on insert.
        configured = session.execute(text(
            "SELECT NULLIF(current_setting('app.workspace_id', true), '')::bigint"
        )).scalar_one_or_none()
        if configured is None:
            from app.config import STUDIO_DEFAULT_WORKSPACE_ID
            configured = STUDIO_DEFAULT_WORKSPACE_ID
        workspace_id = int(configured)
    for k, v in values.items():
        obj = session.get(Setting, (workspace_id, k))
        if obj is None:
            obj = Setting(workspace_id=workspace_id, key=k, value=str(v))
        else:
            obj.value = str(v)
        session.add(obj)
    session.commit()
