"""Import videos from a public Google Drive folder.

Google Drive is unreachable from the app's host. gdown traffic is routed
through an HTTP proxy on an overseas server (config.DRIVE_IMPORT_PROXY).
"""
import re
import time
import threading
from pathlib import Path

import gdown

from app import config
from app.db import session_scope
from app.repositories import videos as vid_repo
from app.repositories import publications as pub_repo
from app.repositories import job_runs as jobs_repo
from app.services.platforms import PLATFORMS
from app.services.thumbnail_service import generate_thumbnail

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


class ProxyNotConfigured(Exception):
    """DRIVE_IMPORT_PROXY is not set."""


class ImportAlreadyRunning(Exception):
    """An import was requested while one is already running."""


def parse_folder_id(url: str) -> str:
    """Extract the Drive folder ID from a folder URL (or a bare ID).

    Raises ValueError if the input is not a recognizable Drive folder.
    """
    text = (url or "").strip()
    if not text:
        raise ValueError("folder URL is empty")
    m = _FOLDER_RE.search(text)
    if m:
        return m.group(1)
    if _BARE_ID_RE.match(text):
        return text
    raise ValueError("not a Google Drive folder URL")


def is_video(name: str) -> bool:
    """True if the filename has a recognized video extension."""
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def unique_path(upload_dir, name: str, file_id: str) -> Path:
    """A non-colliding destination path inside upload_dir.

    Uses the original name; if taken, disambiguates with the Drive file ID
    (globally unique). Raises FileExistsError if even the disambiguated path
    is already taken.
    """
    upload_dir = Path(upload_dir)
    safe = Path(name).name or f"{file_id}.mp4"
    dest = upload_dir / safe
    if not dest.exists():
        return dest
    alt = upload_dir / f"{Path(safe).stem}_{file_id}{Path(safe).suffix}"
    if alt.exists():
        raise FileExistsError(f"destination already exists: {alt}")
    return alt


_state_lock = threading.Lock()
_EMPTY_STATE = {
    "state": "idle",        # idle | running | done | error
    "total": 0,
    "done": 0,
    "skipped": 0,
    "failed": 0,
    "current": None,
    "errors": [],
    "started_at": None,
    "finished_at": None,
}
_import_states: dict[int, dict] = {}


def _state(workspace_id: int) -> dict:
    return _import_states.setdefault(workspace_id, {
        **_EMPTY_STATE, "errors": [],
    })


# Compatibility alias for legacy tests and local diagnostics (workspace 1).
_import_state = _state(1)


def get_state(workspace_id: int = 1) -> dict:
    """A snapshot copy of the current import progress state."""
    with _state_lock:
        current = _state(workspace_id)
        snap = dict(current)
        snap["errors"] = list(current["errors"])
        return snap


def _update(workspace_id: int, **fields) -> None:
    with _state_lock:
        _state(workspace_id).update(fields)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def list_folder(folder_url: str, workspace_id: int = 1) -> list[dict]:
    """List the video files in a public Drive folder, without downloading.

    Returns [{"id", "name", "already_imported"}] for video files only.
    Raises ProxyNotConfigured, ValueError (bad URL), or RuntimeError (list failed).
    """
    if not config.DRIVE_IMPORT_PROXY:
        raise ProxyNotConfigured()
    parse_folder_id(folder_url)  # raises ValueError on a bad URL
    listing = gdown.download_folder(
        url=folder_url, skip_download=True, quiet=True,
        proxy=config.DRIVE_IMPORT_PROXY or None)
    if not listing:
        raise RuntimeError("folder is empty or could not be listed")
    with session_scope(workspace_id) as session:
        existing = vid_repo.existing_drive_file_ids(session)
    files = []
    for f in listing:
        name = Path(f.path).name
        if is_video(name):
            files.append({"id": f.id, "name": name,
                          "already_imported": f.id in existing})
    return files


def run_import(
    files: list[dict],
    group_id: int | None = None,
    platforms: list[str] | None = None,
    workspace_id: int = 1,
) -> None:
    """Background worker: download the selected Drive files, record them.

    `files` is a list of {"id", "name"} dicts. `platforms` selects which
    publication rows to create (defaults to all known PLATFORMS). Opens
    its own DB session (runs in a background thread). Updates progress
    state, writes a job_runs row.
    """
    plat_list = list(platforms) if platforms else list(PLATFORMS)
    _update(workspace_id, state="running", total=0, done=0, skipped=0, failed=0,
            current=None, errors=[], started_at=_now(), finished_at=None)
    errors: list[str] = []
    done = skipped = failed = 0
    with session_scope(workspace_id) as session:
        jid = jobs_repo.start(session, "drive_import")
        try:
            proxy = config.DRIVE_IMPORT_PROXY or None
            existing = vid_repo.existing_drive_file_ids(session)
            wanted = [(f["id"], f["name"]) for f in files
                      if f["id"] not in existing]
            skipped = len(files) - len(wanted)
            _update(workspace_id, total=len(wanted), skipped=skipped)
            for file_id, name in wanted:
                _update(workspace_id, current=name)
                upload_dir = config.UPLOAD_DIR / str(workspace_id)
                upload_dir.mkdir(parents=True, exist_ok=True)
                dest = unique_path(upload_dir, name, file_id)
                try:
                    gdown.download(id=file_id, output=str(dest),
                                   quiet=True, proxy=proxy)
                    vid = vid_repo.create(session, name, str(dest),
                                          drive_file_id=file_id,
                                          group_id=group_id)
                    rel, dur = generate_thumbnail(
                        dest, video_id=vid, workspace_id=workspace_id,
                    )
                    if rel is not None or dur is not None:
                        vid_repo.update_media_meta(session, vid,
                                                   thumbnail_path=rel,
                                                   duration_seconds=dur)
                    try:
                        for platform in plat_list:
                            pub_repo.find_or_create(session, video_id=vid,
                                                     platform=platform)
                    except Exception:
                        # vid_repo.create already committed — roll it
                        # back so auto_publish_scan never sees a video
                        # with no publication. The outer except logs
                        # the re-raised error and removes the file.
                        vid_repo.delete(session, vid)
                        raise
                    done += 1
                except Exception as e:  # per-file isolation: skip and continue
                    if dest.exists():
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                    failed += 1
                    errors.append(f"{name}: {e}")
                _update(workspace_id, done=done, failed=failed, errors=list(errors))
            summary = f"{done} imported, {skipped} skipped, {failed} failed"
            jobs_repo.finish(session, jid, "ok" if failed == 0 else "error", summary)
            _update(workspace_id, state="done", current=None, finished_at=_now())
        except Exception as e:  # whole-import failure
            jobs_repo.finish(session, jid, "error", str(e))
            _update(workspace_id, state="error", current=None,
                    errors=list(errors) + [str(e)], finished_at=_now())


def start_import(files: list[dict], group_id: int | None = None,
                 platforms: list[str] | None = None,
                 workspace_id: int = 1) -> threading.Thread:
    """Validate input and launch a background import of the selected files.

    Raises ProxyNotConfigured, ValueError (empty selection / unknown
    platform), or ImportAlreadyRunning. Returns the started daemon Thread.
    """
    if not config.DRIVE_IMPORT_PROXY:
        raise ProxyNotConfigured()
    if not files:
        raise ValueError("no files selected")
    if platforms is not None:
        for plat in platforms:
            if plat not in PLATFORMS:
                raise ValueError(f"unknown platform: {plat!r}")
        if not platforms:
            raise ValueError("no platforms selected")
    with _state_lock:
        if _state(workspace_id)["state"] == "running":
            raise ImportAlreadyRunning()
        _state(workspace_id)["state"] = "running"
    def target() -> None:
        try:
            run_import(
                files, group_id=group_id, platforms=platforms,
                workspace_id=workspace_id,
            )
        except TypeError:
            run_import(files, group_id=group_id, platforms=platforms)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t
