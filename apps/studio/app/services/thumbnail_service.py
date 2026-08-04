"""ffmpeg-driven thumbnail + duration extraction.

Used at upload time to capture a single frame around the 1-second
mark and the video's duration. Any failure (ffmpeg missing, bad
input, timeout) degrades silently to (None, None) — the frontend
has a placeholder. Never raise from this module.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from app import config

logger = logging.getLogger(__name__)

THUMBNAIL_DIR = config.DATA_DIR / "thumbnails"
TIMEOUT_SECONDS = 60
THUMB_WIDTH = 480
_LOGGED_MISSING_FFMPEG = False


def _ffmpeg_thumb(video: Path, out: Path) -> bool:
    """Run ffmpeg → JPEG. Returns True on success."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", "00:00:01",
        "-i", str(video),
        "-vframes", "1",
        "-vf", f"scale={THUMB_WIDTH}:-2",
        "-q:v", "4",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True,
                           timeout=TIMEOUT_SECONDS)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        global _LOGGED_MISSING_FFMPEG
        if isinstance(e, FileNotFoundError) and not _LOGGED_MISSING_FFMPEG:
            logger.warning("ffmpeg not on PATH — thumbnails disabled")
            _LOGGED_MISSING_FFMPEG = True
        return False
    if r.returncode != 0:
        logger.info("ffmpeg failed video=%s stderr=%s",
                    video, r.stderr[-200:].decode(errors="replace"))
        return False
    return out.exists() and out.stat().st_size > 0


def _ffprobe_duration(video: Path) -> Optional[int]:
    """Probe duration → integer seconds, or None on any failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(video),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True,
                           timeout=TIMEOUT_SECONDS)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    raw = r.stdout.decode(errors="replace").strip()
    try:
        return int(round(float(raw)))
    except ValueError:
        return None


def generate_thumbnail(
    video_path: Path | str, *, video_id: int, workspace_id: int | None = None,
) -> tuple[Optional[str], Optional[int]]:
    """Extract a JPEG thumbnail + duration. Never raises.

    Returns (relative_path, duration_seconds) where relative_path is
    'thumbnails/<id>.jpg' on success or None on any failure.
    duration_seconds may still be returned even when thumbnail
    extraction failed (e.g. ffprobe present, ffmpeg not).
    """
    video = Path(video_path)
    directory = THUMBNAIL_DIR / str(workspace_id) if workspace_id else THUMBNAIL_DIR
    out = directory / f"{video_id}.jpg"
    ok = _ffmpeg_thumb(video, out)
    duration = _ffprobe_duration(video)
    rel = (
        f"thumbnails/{workspace_id}/{video_id}.jpg"
        if ok and workspace_id else f"thumbnails/{video_id}.jpg" if ok else None
    )
    return rel, duration
