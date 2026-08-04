"""Thin orchestration over the groups repository.

`counts_for` is the single source of truth for per-group progress (we never
store a state machine; progress is always aggregated from accounts/videos)."""
from sqlalchemy import text
from sqlmodel import Session
from app.repositories import accounts as acc_repo
from app.repositories import videos as vid_repo
from app.repositories import groups as grp_repo


def create(session: Session, name: str,
           notes: str | None = None) -> int:
    return grp_repo.create(session, name=name, notes=notes)


def _videos_by_group(session: Session, group_id: int) -> list[dict]:
    rows = session.execute(
        text("SELECT * FROM videos WHERE group_id=:gid ORDER BY id DESC"),
        {"gid": group_id}).fetchall()
    return [dict(r._mapping) for r in rows]


def get(session: Session, group_id: int) -> dict | None:
    row = grp_repo.get(session, group_id)
    if row is None:
        return None
    row["counts"] = counts_for(session, group_id)
    row["accounts"] = acc_repo.list_by_group(session, group_id)
    row["videos"] = _videos_by_group(session, group_id)
    return row


def list_all(session: Session) -> list[dict]:
    rows = grp_repo.list_all(session)
    for r in rows:
        r["counts"] = counts_for(session, r["id"])
    return rows


def counts_for(session: Session, group_id: int) -> dict:
    acc = acc_repo.count_for_group(session, group_id)
    vid = vid_repo.count_for_group(session, group_id)
    return {**acc, **vid}


def rename(session: Session, group_id: int, new_name: str):
    """Rename a group. Raises ValueError on empty or duplicate name."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("name required")
    cur = session.execute(
        text("SELECT id FROM groups WHERE name=:name AND id<>:id"),
        {"name": new_name, "id": group_id}).fetchone()
    if cur:
        raise ValueError("name already used")
    session.execute(text("UPDATE groups SET name=:name WHERE id=:id"),
                    {"name": new_name, "id": group_id})
    session.commit()


def set_caption_prompt(session: Session, group_id: int,
                       prompt_id: int | None) -> None:
    """Bind a group to a caption_prompts row, or clear it (None ==
    unbound -> publishing is blocked by posting_service)."""
    from app.models import Group
    obj = session.get(Group, group_id)
    if obj is None:
        return
    if prompt_id is not None:
        from app.repositories import caption_prompts as cp_repo
        if cp_repo.get(session, prompt_id) is None:
            raise ValueError("prompt not found")
    obj.caption_prompt_id = prompt_id
    session.add(obj)
    session.commit()
