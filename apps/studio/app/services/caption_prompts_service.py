"""Thin business layer for the caption_prompts pool.

Every group must pick a prompt before its accounts can publish — see
posting_service._resolve_caption_config + auto_publish.publish_path_blocked.
This service owns the prompt rows themselves; group-to-prompt linking lives
in groups_service.set_caption_prompt.
"""
from sqlmodel import Session

from app.repositories import caption_prompts as repo


class PromptInUse(RuntimeError):
    """Raised when a caller tries to delete a prompt currently bound to a
    group. The dashboard shows which groups still reference it."""

    def __init__(self, prompt_id: int, group_names: list[str]):
        self.prompt_id = prompt_id
        self.group_names = group_names
        super().__init__(
            f"prompt {prompt_id} still bound to: {', '.join(group_names)}"
        )


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    if len(name) > 120:
        raise ValueError("name too long (max 120 chars)")
    return name


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValueError("body required")
    return body


def list_all(session: Session) -> list[dict]:
    return repo.list_all(session)


def get(session: Session, prompt_id: int) -> dict | None:
    return repo.get(session, prompt_id)


def create(session: Session, *, name: str, body: str) -> int:
    name = _clean_name(name)
    body = _clean_body(body)
    if repo.get_by_name(session, name) is not None:
        raise ValueError("name already used")
    return repo.create(session, name=name, body=body)


def update(session: Session, prompt_id: int, *,
           name: str | None = None, body: str | None = None) -> bool:
    if name is not None:
        name = _clean_name(name)
        other = repo.get_by_name(session, name)
        if other is not None and other["id"] != prompt_id:
            raise ValueError("name already used")
    if body is not None:
        body = _clean_body(body)
    return repo.update(session, prompt_id, name=name, body=body)


def delete(session: Session, prompt_id: int) -> bool:
    from sqlalchemy import text
    rows = session.execute(
        text("SELECT name FROM groups WHERE caption_prompt_id = :pid "
             "ORDER BY name"),
        {"pid": prompt_id},
    ).fetchall()
    if rows:
        raise PromptInUse(prompt_id, [r[0] for r in rows])
    return repo.delete(session, prompt_id)
