"""Operator-facing endpoints for the Writer Factory."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.repositories import outbox as outbox_repo
from app.repositories import videos as vid_repo
from app.tasks.outbox_dispatcher import try_prompt_dispatch

router = APIRouter(prefix="/api/writer")
log = logging.getLogger(__name__)


@router.post("/understanding/{vid}/refresh")
async def refresh_understanding(vid: int,
                                 session: Session = Depends(get_session)):
    row = vid_repo.get(session, vid)
    if row is None:
        raise HTTPException(status_code=404, detail="video not found")
    # Reset first: the task's idempotency gate skips videos whose
    # understanding is already "ok" (double-defer cost guard), so an
    # explicit refresh must clear that state to get past it.
    vid_repo.set_understanding(session, vid, facts=None, status="pending")
    outbox_id = outbox_repo.enqueue(
        session,
        intent="extract_understanding",
        business_ref=f"video:{vid}",
        payload={"video_id": vid},
    )
    session.commit()
    if outbox_id is not None:
        await try_prompt_dispatch(
            outbox_id, "extract_understanding", {"video_id": vid},
        )
    return {"queued": True, "video_id": vid}
