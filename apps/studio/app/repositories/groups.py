from sqlmodel import Session, select

from app.models import Group


def create(session: Session, name: str,
           notes: str | None = None) -> int:
    obj = Group(name=name, notes=notes)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def get(session: Session, group_id: int) -> dict | None:
    obj = session.get(Group, group_id)
    return obj.model_dump() if obj else None


def get_by_name(session: Session, name: str) -> dict | None:
    obj = session.exec(select(Group).where(Group.name == name)).first()
    return obj.model_dump() if obj else None


def list_all(session: Session) -> list[dict]:
    stmt = select(Group).order_by(Group.id.desc())
    rows = session.exec(stmt).all()
    return [r.model_dump() for r in rows]
