"""Validation + in-use guard for the comment_prompts pool.

CommentModal picks a prompt when starting a studio_ai batch. This service
owns the prompt rows themselves; the FK on engagement_tasks does the
linking. Deletion is refused while any engagement_runs row tied to the
prompt is still inflight (PENDING / QUEUED / INITIATED).
"""
from sqlalchemy import text
from sqlmodel import Session

from app.repositories import comment_prompts as repo


class PromptInUse(RuntimeError):
    """Raised when a caller tries to delete a prompt that is still referenced
    by an inflight engagement run. The dashboard surfaces the run_ids."""

    def __init__(self, prompt_id: int, run_ids: list[int]):
        self.prompt_id = prompt_id
        self.run_ids = run_ids
        super().__init__(
            f"prompt {prompt_id} still in use by run ids: {run_ids}"
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
    rows = session.execute(
        text(
            "SELECT r.id FROM engagement_runs r "
            "JOIN engagement_tasks t ON r.engagement_task_id = t.id "
            "WHERE t.comment_prompt_id = :pid "
            "  AND r.result IN ('PENDING', 'QUEUED', 'INITIATED') "
            "ORDER BY r.id"
        ),
        {"pid": prompt_id},
    ).fetchall()
    if rows:
        raise PromptInUse(prompt_id, [r[0] for r in rows])
    return repo.delete(session, prompt_id)
