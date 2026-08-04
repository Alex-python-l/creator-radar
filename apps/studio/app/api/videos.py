"""Video API — publications-aware. Each video is the durable artifact;
each (video, platform) is one Publication carrying publishing state."""
import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session
from app import config
from app.db import get_session
from app.models import Publication
from app.repositories import videos as vid_repo
from app.repositories import accounts as acc_repo
from app.repositories import publications as pub_repo
from app.services.platforms import PLATFORMS, validate as validate_platform
from app.services.thumbnail_service import generate_thumbnail
from app.tasks import procrastinate_app

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos")

# GeeLark's RPA failure screenshots are JPEGs served from a slow Singapore
# origin (singapore-upgrade.geelark.com, 1-4s TTFB even for ~30KB images)
# and expire after ~3 days. We fetch each once, cache it on disk keyed on
# the URL's identity (so a re-failure with a fresh URL never serves a stale
# frame), and serve the local copy thereafter.
_SCREENSHOT_FETCH_TIMEOUT = 15.0
_SCREENSHOT_MAX_BYTES = 20 * 1024 * 1024


def _screenshot_cache_path(url: str, workspace_id: int) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    path = Path(config.SCREENSHOT_DIR) / str(workspace_id) / f"{digest}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fetch_screenshot_bytes(url: str) -> bytes:
    """Fetch a GeeLark screenshot server-side. Raises on any HTTP error or
    if the payload exceeds the size cap. Kept thin (the only network I/O)
    so the endpoint's cache/serve/fallback logic stays unit-testable."""
    import httpx
    with httpx.stream("GET", url, timeout=_SCREENSHOT_FETCH_TIMEOUT,
                      follow_redirects=True) as resp:
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > _SCREENSHOT_MAX_BYTES:
                raise ValueError("screenshot too large")
            chunks.append(chunk)
    return b"".join(chunks)


def _parse_platforms(raw: str | None) -> list[str]:
    """Parse the upload form's `platforms` field. None / empty -> ['tiktok'].

    Accepts comma-separated platforms. Raises HTTPException 400 on
    unknown platform string."""
    if raw is None or not raw.strip():
        return ["tiktok"]
    out: list[str] = []
    for piece in raw.split(","):
        piece = piece.strip().lower()
        if not piece:
            continue
        if piece not in PLATFORMS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown platform: {piece!r}; "
                       f"expected one of {list(PLATFORMS)}")
        if piece not in out:
            out.append(piece)
    return out or ["tiktok"]


def _required_platform(value: str | None) -> str:
    """Validate the `platform` query parameter — required, must be known."""
    if value is None or not value.strip():
        raise HTTPException(
            status_code=400,
            detail="platform query parameter is required "
                   "(one of tiktok/youtube/instagram/facebook)")
    try:
        return validate_platform(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _assert_account_matches_video_group(video: dict, account: dict) -> None:
    video_group_id = video.get("group_id")
    if video_group_id is None:
        raise HTTPException(
            status_code=400,
            detail="video has no group; assign it to a group before publishing",
        )
    if account.get("group_id") != video_group_id:
        raise HTTPException(
            status_code=400,
            detail="account must belong to the video's group",
        )


def _save_upload_file(upload: UploadFile, safe_name: str, workspace_id: int) -> Path:
    upload_dir = Path(config.UPLOAD_DIR) / str(workspace_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
    tmp = dest.with_name(f"{dest.name}.part")
    try:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(upload.file, out)
        tmp.replace(dest)
    except Exception:
        for path in (tmp, dest):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return dest


def _parse_group_filter(raw: str) -> int | str:
    if raw == "all" or raw == "ungrouped":
        return raw
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="group_id must be 'all', 'ungrouped', or an integer")


_STATUS_FILTER_VALUES = (
    "all", "pending", "in_progress", "handled",
)


def _parse_status_filter(raw: str) -> str:
    if raw not in _STATUS_FILTER_VALUES:
        raise HTTPException(
            status_code=422,
            detail=(
                "status must be one of "
                + ", ".join(f"'{v}'" for v in _STATUS_FILTER_VALUES)
            ),
        )
    return raw


@router.get("")
def list_videos(
    group_id: str = "all",
    status: str = "all",
    cursor: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    gf = _parse_group_filter(group_id)
    sf = _parse_status_filter(status)
    limit = max(1, min(int(limit), 200))
    # Cursor is opaque to the API layer: int-as-string for non-handled
    # tabs, "<iso-ts>|<id>" for handled. Repository parses by `status`.
    parsed_cursor: int | str | None
    if cursor is None or cursor == "":
        parsed_cursor = None
    elif sf == "handled":
        parsed_cursor = cursor
    else:
        try:
            parsed_cursor = int(cursor)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="cursor must be an integer")
    items, next_cursor = vid_repo.list_page(
        session, group_id=gf, status=sf, cursor=parsed_cursor, limit=limit)
    for it in items:
        rel = it.pop("thumbnail_path", None)
        it["thumbnail_url"] = f"/static/{rel}" if rel else None
    body: dict = {"items": items, "next_cursor": next_cursor}
    if cursor is None:
        body["total"] = vid_repo.count_filtered(
            session, group_id=gf, status=sf)
        body["summary_counts"] = vid_repo.summary_counts_for(
            session, group_id=gf)
    return body


@router.get("/{vid}/detail")
def get_video_detail(vid: int, session: Session = Depends(get_session)):
    """Full publications array for a single video. Used by the row-expand
    UI; the list endpoint returns only a compact summary so the first
    page stays small."""
    d = vid_repo.get_detail(session, vid)
    if d is None:
        raise HTTPException(status_code=404, detail="video not found")
    return d


@router.get("/publications/{pub_id}/screenshot")
def get_failure_screenshot(pub_id: int,
                           request: Request,
                           session: Session = Depends(get_session)):
    """Serve a publication's GeeLark failure screenshot from a local cache,
    fetching + persisting it on the first miss. On any upstream fetch error
    (slow origin, expired URL) we redirect to the raw URL, so the result is
    never worse than loading GeeLark directly."""
    pub = session.get(Publication, pub_id)
    url = (pub.fail_screenshot or "").strip() if pub else ""
    if not url:
        raise HTTPException(status_code=404, detail="no screenshot")
    cache = _screenshot_cache_path(url, int(request.state.workspace["id"]))
    if not cache.exists():
        try:
            data = _fetch_screenshot_bytes(url)
        except Exception:
            return RedirectResponse(url, status_code=302)
        tmp = cache.with_name(f"{cache.name}.part")
        try:
            tmp.write_bytes(data)
            tmp.replace(cache)
        except OSError:
            tmp.unlink(missing_ok=True)
            return RedirectResponse(url, status_code=302)
    return FileResponse(
        path=str(cache),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/publications/{pub_id}/attempts")
def get_publication_attempts(pub_id: int,
                             session: Session = Depends(get_session)):
    if pub_repo.get(session, pub_id) is None:
        raise HTTPException(status_code=404, detail="publication not found")
    return pub_repo.list_attempts(session, pub_id)


@router.post("/publications/{pub_id}/retry")
async def retry_publication(pub_id: int,
                            session: Session = Depends(get_session)):
    from app.services import posting_service
    pub = pub_repo.get(session, pub_id)
    if pub is None:
        raise HTTPException(status_code=404, detail="publication not found")
    if pub.get("result") != "FAILURE":
        raise HTTPException(status_code=400,
                            detail="only failed publications can be retried")
    v = vid_repo.get(session, pub["video_id"])
    group_id = v.get("group_id") if v else None
    if not posting_service.group_has_caption_prompt(session, group_id):
        raise HTTPException(
            status_code=400,
            detail="this video's group has no caption prompt assigned; "
                   "pick one in Group settings before retrying",
        )
    ok = pub_repo.reset_failed_for_retry(session, pub_id)
    scan_queued = False
    if ok:
        scan_queued = await _wake_auto_publish_scan()
    return {"ok": ok, "publish_scan_queued": scan_queued}


@router.post("/upload")
async def upload(request: Request,
                 files: list[UploadFile] = File(...),
                 group_id: int | None = Form(None),
                 platforms: str | None = Form(None),
                 session: Session = Depends(get_session)):
    workspace_id = int(request.state.workspace["id"])
    plats = _parse_platforms(platforms)
    n = 0
    for f in files:
        if not f.filename:
            raise HTTPException(status_code=400, detail="filename required")
        safe_name = Path(f.filename).name
        if not safe_name:
            raise HTTPException(status_code=400, detail="invalid filename")
        dest: Path | None = None
        vid: int | None = None
        try:
            dest = _save_upload_file(f, safe_name, workspace_id)
            vid = vid_repo.create(session, safe_name, str(dest), group_id=group_id)
            for plat in plats:
                pub_repo.find_or_create(session, video_id=vid, platform=plat)
            rel, dur = generate_thumbnail(
                dest, video_id=vid, workspace_id=workspace_id,
            )
            if rel is not None or dur is not None:
                vid_repo.update_media_meta(session, vid,
                                           thumbnail_path=rel,
                                           duration_seconds=dur)
            # Layer 1 of writer factory — queue understanding extraction
            # via the outbox so a defer hiccup doesn't strand the upload
            # without analysis. The dispatcher periodic re-defers any
            # row whose prompt dispatch fails here.
            from app.repositories import outbox as outbox_repo
            from app.tasks.outbox_dispatcher import try_prompt_dispatch
            outbox_id = outbox_repo.enqueue(
                session,
                intent="extract_understanding",
                business_ref=f"video:{vid}",
                payload={"video_id": vid},
            )
            session.commit()
            if outbox_id is not None:
                await try_prompt_dispatch(
                    outbox_id, "extract_understanding",
                    {"video_id": vid},
                )
        except Exception:
            # Roll back the partial commit so we don't leave a video
            # with no publication for auto_publish_scan to ever find.
            if vid is not None:
                vid_repo.delete(session, vid)
            if dest is not None:
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        n += 1
    scan_queued = False
    if n and group_id is not None:
        scan_queued = await _wake_auto_publish_scan()
    return {"uploaded": n, "publish_scan_queued": scan_queued}


@router.delete("/{vid}")
def delete_video(vid: int, session: Session = Depends(get_session)):
    row = vid_repo.get(session, vid)
    vid_repo.delete(session, vid)
    if row and row.get("stored_path"):
        try:
            os.remove(row["stored_path"])
        except OSError:
            pass
    return {"ok": True}


@router.patch("/{vid}")
def update_video(vid: int, body: dict,
                 session: Session = Depends(get_session)):
    """Legacy bulk-assign (or un-assign) the GeeLark phone for a video.

    New auto-publish assignment is per publication/platform. This endpoint
    applies the selected phone to all PENDING/QUEUED publications for the
    video for backwards compatibility with the older row-level picker.
    """
    video = vid_repo.get(session, vid)
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")
    body = body or {}
    if "account_id" not in body:
        raise HTTPException(status_code=400, detail="account_id required")
    account_id = body.get("account_id")
    if account_id is not None:
        acc = acc_repo.get(session, account_id)
        if acc is None:
            raise HTTPException(status_code=400, detail="account not found")
        _assert_account_matches_video_group(video, acc)
    vid_repo.set_account(session, vid, account_id)
    return {"ok": True}


async def _wake_auto_publish_scan() -> bool:
    from app.tasks.auto_publish import wake_auto_publish_scan
    return await wake_auto_publish_scan()


@router.post("/assign")
async def assign_videos(body: dict, session: Session = Depends(get_session)):
    """Batch-assign selected videos to a target group.

    Videos with group_id IS NULL are 'drafts' and are skipped by the
    auto_publish scheduler job. Calling this endpoint promotes them to
    'ready' by writing group_id, then wakes the scheduler once so eligible
    publications can be queued immediately.
    """
    from app.services import groups_service as gs
    ids = (body or {}).get("video_ids") or []
    gid = (body or {}).get("group_id")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="video_ids required")
    if gid is None or gs.get(session, int(gid)) is None:
        raise HTTPException(status_code=400, detail="group not found")
    n = vid_repo.bulk_set_group(session, [int(i) for i in ids], int(gid))
    scan_queued = False
    if n:
        scan_queued = await _wake_auto_publish_scan()
    return {"assigned": n, "publish_scan_queued": scan_queued}


@router.post("/import")
def import_drive(body: dict, request: Request):
    from app.services import drive_import
    files = (body or {}).get("files") or []
    group_id = (body or {}).get("group_id")
    platforms = (body or {}).get("platforms")
    if platforms is not None and not isinstance(platforms, list):
        raise HTTPException(
            status_code=400, detail="platforms must be a list of strings")
    try:
        try:
            drive_import.start_import(
                files, group_id=group_id, platforms=platforms,
                workspace_id=int(request.state.workspace["id"]))
        except TypeError:
            drive_import.start_import(
                files, group_id=group_id, platforms=platforms,
            )
    except drive_import.ProxyNotConfigured:
        raise HTTPException(status_code=503,
                            detail="DRIVE_IMPORT_PROXY is not configured")
    except drive_import.ImportAlreadyRunning:
        raise HTTPException(status_code=409,
                            detail="an import is already running")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"started": True}


@router.post("/import/list")
def import_list(body: dict, request: Request):
    from app.services import drive_import
    url = (body or {}).get("folder_url", "")
    try:
        try:
            files = drive_import.list_folder(
                url, int(request.state.workspace["id"]),
            )
        except TypeError:
            files = drive_import.list_folder(url)
    except drive_import.ProxyNotConfigured:
        raise HTTPException(status_code=503,
                            detail="DRIVE_IMPORT_PROXY is not configured")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not list folder: {e}")
    return {"files": files}


@router.get("/import/status")
def import_status(request: Request):
    from app.services import drive_import
    return drive_import.get_state(int(request.state.workspace["id"]))


@router.post("/{vid}/publish")
async def publish_one(
    vid: int,
    body: dict,
    platform: str | None = None,
    session: Session = Depends(get_session),
):
    """Defer one (video, platform) publication to the Procrastinate worker.

    Returns {"status": "QUEUED", "job_id": ..., "publication_id": ...}.
    The actual GeeLark submission happens in the background worker.
    """
    from app.services import posting_service
    platform = _required_platform(platform)
    video = vid_repo.get(session, vid)
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")
    account_id = (body or {}).get("account_id")
    if account_id is None:
        raise HTTPException(status_code=400,
                            detail="account_id required")
    acc = acc_repo.get(session, account_id)
    if acc is None:
        raise HTTPException(status_code=400,
                            detail="account not found")
    _assert_account_matches_video_group(video, acc)
    if not posting_service.group_has_caption_prompt(
            session, video.get("group_id")):
        raise HTTPException(
            status_code=400,
            detail="this video's group has no caption prompt assigned; "
                   "pick one in Group settings before publishing",
        )
    # Find or create the publication for (vid, platform) — race-safe.
    pid = pub_repo.find_or_create(session, video_id=vid, platform=platform)
    pub_repo.set_account(session, pid, account_id)
    pub_repo.mark_queued(session, pid)
    # Enqueue the publish event in the outbox so a defer hiccup doesn't
    # leave the publication QUEUED forever. The dispatcher periodic
    # picks it up within ~2 minutes; the prompt dispatch below collapses
    # the happy-path latency to zero.
    from app.repositories import outbox as outbox_repo
    from app.tasks.outbox_dispatcher import try_prompt_dispatch
    outbox_id = outbox_repo.enqueue(
        session,
        intent="publish_publication",
        business_ref=f"publication:{pid}",
        payload={"publication_id": pid},
    )
    session.commit()
    if outbox_id is not None:
        await try_prompt_dispatch(
            outbox_id, "publish_publication",
            {"publication_id": pid},
        )
    return {"status": "QUEUED", "publication_id": pid}
