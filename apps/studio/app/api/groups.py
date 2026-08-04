from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session
from sqlalchemy import text
from app.db import get_session

router = APIRouter(prefix="/api/groups")


@router.post("")
def create_group(body: dict, session: Session = Depends(get_session)):
    from app.services import groups_service as gs
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        gid = gs.create(session, name=name, notes=body.get("notes"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": gid}


@router.get("")
def list_groups(session: Session = Depends(get_session)):
    from app.services import groups_service as gs
    return gs.list_all(session)


@router.get("/{group_id}")
def get_group(group_id: int, session: Session = Depends(get_session)):
    from app.services import groups_service as gs
    row = gs.get(session, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="group not found")
    return row


@router.patch("/{group_id}")
def patch_group(group_id: int, body: dict,
                session: Session = Depends(get_session)):
    from app.services import groups_service as gs
    if gs.get(session, group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    if "name" in body:
        try:
            gs.rename(session, group_id, body["name"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if "caption_prompt_id" in body:
        pid = body["caption_prompt_id"]
        if pid is not None:
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="caption_prompt_id must be an integer or null",
                )
        try:
            gs.set_caption_prompt(session, group_id, pid)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/{group_id}/proxies/check")
def check_proxies(group_id: int, body: dict,
                  session: Session = Depends(get_session)):
    from app.services import groups_service as gs
    from app.services import proxy_intake as pi
    if gs.get(session, group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    proxies = body.get("proxies") or []
    if not isinstance(proxies, list):
        raise HTTPException(status_code=400, detail="proxies must be a list")
    return pi.check_batch(proxies)


@router.post("/{group_id}/phones/create")
def create_phones(group_id: int, body: dict,
                  session: Session = Depends(get_session)):
    from app.services import groups_service as gs
    from app.services import geelark_service
    from app.sse import sse_from_generator, locked_generator
    if gs.get(session, group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    proxies = body.get("proxies") or []
    if not isinstance(proxies, list) or not proxies:
        raise HTTPException(status_code=400,
                            detail="proxies must be a non-empty list")
    count = body.get("count")
    if count is not None:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise HTTPException(status_code=400,
                                detail="count must be a positive integer")
    return sse_from_generator(locked_generator(
        geelark_service.create_profiles_for_group(
            session, geelark_service.workspace_client(session), group_id, proxies,
            count=count)))


_LOGIN_INFO_FIELDS = (
    "tiktok_login_info",
    "youtube_login_info",
    "instagram_login_info",
    "facebook_login_info",
)


@router.post("/{group_id}/accounts/assign")
def assign_accounts(group_id: int, body: dict,
                    session: Session = Depends(get_session)):
    """Apply login-info blobs to this group's unassigned account rows.

    Accepts either the legacy structured `accounts` rows or a raw pasted blob
    in `raw_login_info`, where each non-empty line becomes one TikTok row.
    """
    from app.services import groups_service as gs
    from app.services.credentials_parser import parse_login_info_lines
    from app.repositories import accounts as acc_repo
    if gs.get(session, group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    raw_login_info = body.get("raw_login_info")
    if raw_login_info is not None:
        if not isinstance(raw_login_info, str):
            raise HTTPException(status_code=400,
                                detail="raw_login_info must be a string")
        try:
            parsed = parse_login_info_lines(
                raw_login_info,
                platform=body.get("platform") or "tiktok",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        parsed = body.get("accounts") or []
        if not isinstance(parsed, list):
            raise HTTPException(status_code=400,
                                detail="accounts must be a list")

    def _has_any_login_info(row: dict) -> bool:
        return any((row.get(f) or "").strip() for f in _LOGIN_INFO_FIELDS)

    targets = [a for a in acc_repo.list_by_group(session, group_id)
               if not _has_any_login_info(a)]
    assigned = 0
    skipped = 0
    for src in parsed:
        if not targets:
            break
        blobs = {f: (src.get(f) or "").strip() or None
                 for f in _LOGIN_INFO_FIELDS}
        if not any(blobs.values()):
            skipped += 1
            continue
        slot = targets.pop(0)
        acc_repo.update(session, slot["id"], blobs)
        assigned += 1
    return {"assigned": assigned, "skipped": skipped}


@router.delete("/{group_id}")
def delete_group(group_id: int, session: Session = Depends(get_session)):
    """Hard-delete a group ONLY if it has no accounts attached. Used by the
    wizard cancel path to clean up the empty group it created in step 1."""
    from app.services import groups_service as gs
    from app.repositories import accounts as acc_repo
    if gs.get(session, group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    if acc_repo.list_by_group(session, group_id):
        raise HTTPException(status_code=400,
                            detail="group still has accounts; reassign them first")
    video_count = session.execute(
        text("SELECT COUNT(*) FROM videos WHERE group_id=:id"),
        {"id": group_id},
    ).scalar() or 0
    if video_count:
        raise HTTPException(
            status_code=400,
            detail="group still has videos; move or delete them first",
        )
    session.execute(text("DELETE FROM groups WHERE id=:id"), {"id": group_id})
    session.commit()
    return {"ok": True}
