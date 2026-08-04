from datetime import datetime

from sqlmodel import Session, select

from app.models import CommentPrompt


def list_all(session: Session) -> list[dict]:
    rows = session.exec(select(CommentPrompt).order_by(CommentPrompt.name)).all()
    return [r.model_dump() for r in rows]


def get(session: Session, prompt_id: int) -> dict | None:
    obj = session.get(CommentPrompt, prompt_id)
    return obj.model_dump() if obj else None


def get_by_name(session: Session, name: str) -> dict | None:
    obj = session.exec(
        select(CommentPrompt).where(CommentPrompt.name == name)
    ).first()
    return obj.model_dump() if obj else None


def create(session: Session, *, name: str, body: str) -> int:
    obj = CommentPrompt(name=name, body=body)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def update(session: Session, prompt_id: int, *,
           name: str | None = None, body: str | None = None) -> bool:
    obj = session.get(CommentPrompt, prompt_id)
    if obj is None:
        return False
    if name is not None:
        obj.name = name
    if body is not None:
        obj.body = body
    obj.updated_at = datetime.utcnow()
    session.add(obj)
    session.commit()
    return True


def delete(session: Session, prompt_id: int) -> bool:
    obj = session.get(CommentPrompt, prompt_id)
    if obj is None:
        return False
    session.delete(obj)
    session.commit()
    return True
