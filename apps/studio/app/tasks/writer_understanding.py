"""Procrastinate task: extract VideoFacts for a single video.

Deferred by the upload endpoint immediately after a video row is
created (Task 5 of this plan) and by the manual refresh endpoint
(Task 6). The task is idempotent — running it again overwrites the
prior understanding with a fresh one.
"""
import asyncio
import logging
import os

from app.db import session_scope
from app.config import SETTINGS_DEFAULTS
from app.repositories import videos as vid_repo
from app.repositories import settings as set_repo
from app.tasks import procrastinate_app
from app.writer import understanding

log = logging.getLogger(__name__)


def _run_sync(video_id: int) -> str:
    """Synchronous body of extract_understanding_task.

    Runs in a thread via asyncio.to_thread so that blocking Gemini I/O
    (Files API upload + polling, up to ~300 s) does not stall the shared
    event loop and the concurrent tasks running in the same worker.
    """
    with session_scope() as session:
        row = vid_repo.get(session, video_id)
        if row is None:
            return f"skipped: video {video_id} not found"
        from app.services.geelark_service import workspace_for_row
        from app.workspace_secrets import get_secret
        workspace_id = workspace_for_row(session, "videos", video_id)

        # Idempotency gate: outbox double-defer (prompt dispatch +
        # dispatcher tick overlap, or a mark_sent failure) re-runs this
        # task. The state was already idempotent — but each run pays a
        # Gemini upload+generation, so skip when facts are fresh.
        # The writer API's manual refresh clears the status first, so
        # explicit re-extraction still works.
        if row.get("understanding_status") == "ok" and row.get("understanding"):
            return f"skipped: video {video_id} already understood"

        path = row.get("stored_path")
        if not path or not os.path.exists(path):
            log.warning("extract_understanding: file missing for video %s (%s)",
                        video_id, path)
            vid_repo.set_understanding(session, video_id,
                                       facts=None, status="failed")
            return f"failed: file missing for video {video_id}"

        try:
            settings_map = {**SETTINGS_DEFAULTS, **set_repo.get_all(session)}
            kwargs = {}
            gemini_key = get_secret(session, workspace_id, "GEMINI_API_KEY")
            if gemini_key:
                kwargs["api_key"] = gemini_key
            facts = understanding.extract_video_facts(
                path,
                model=str(settings_map.get("gemini_model") or ""),
                **kwargs,
            )
        except Exception as e:
            log.warning("extract_understanding: Gemini failed for video %s: %s",
                        video_id, e)
            vid_repo.set_understanding(session, video_id,
                                       facts=None, status="failed")
            return f"failed: {e!s}"

        vid_repo.set_understanding(session, video_id,
                                   facts=facts.model_dump(),
                                   status="ok")
        return f"ok: video {video_id} understood"


@procrastinate_app.task(queue="default", name="extract_understanding")
async def extract_understanding_task(video_id: int) -> str:
    return await asyncio.to_thread(_run_sync, video_id)
