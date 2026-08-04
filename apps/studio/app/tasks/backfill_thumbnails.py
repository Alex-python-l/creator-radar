"""One-shot backfill task: extract thumbnails + duration for videos
that predate the ffmpeg-on-upload code path.

Defer manually after deploy:
  uv run python -m procrastinate -a app.tasks:procrastinate_app \\
      defer backfill_thumbnails
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlmodel import select

from app.db import session_scope
from app.models import Video
from app.repositories import videos as vid_repo
from app.services.thumbnail_service import generate_thumbnail
from app.tasks import procrastinate_app

logger = logging.getLogger(__name__)


@procrastinate_app.task(queue="default", name="backfill_thumbnails")
async def backfill_thumbnails_task() -> str:
    """Walk videos lacking a thumbnail, run ffmpeg, persist results.

    ffmpeg / ffprobe are blocking subprocess.run calls inside
    generate_thumbnail, so we hop to a worker thread per video to
    avoid stalling procrastinate's event loop.
    """
    with session_scope() as session:
        rows = session.exec(
            select(Video.id, Video.stored_path)
            .where(Video.thumbnail_path.is_(None))
            .where(Video.stored_path.is_not(None))
        ).all()

    total = len(rows)
    logger.info("backfill_thumbnails: %d candidate videos", total)

    done = 0
    for vid, path in rows:
        if path is None or not Path(path).exists():
            continue
        rel, dur = await asyncio.to_thread(
            generate_thumbnail, path, video_id=vid,
        )
        if rel is None and dur is None:
            continue
        with session_scope() as session:
            vid_repo.update_media_meta(
                session, vid,
                thumbnail_path=rel, duration_seconds=dur,
            )
        done += 1
        if done % 10 == 0:
            logger.info("backfill_thumbnails: %d/%d", done, total)
        await asyncio.sleep(0.2)  # be nice to worker CPU

    summary = f"backfill_thumbnails: completed {done}/{total}"
    logger.info(summary)
    return summary
