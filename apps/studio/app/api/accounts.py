from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlmodel import Session
from app.db import get_session
from app.repositories import accounts as acc_repo

router = APIRouter(prefix="/api/accounts")


def _utc_iso_z(value):
    if value is None or not isinstance(value, datetime):
        return value
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat() + "Z"


def _serialize_account(row: dict) -> dict:
    return {**row, "phone_busy_until": _utc_iso_z(row.get("phone_busy_until"))}


@router.get("")
def list_accounts(session: Session = Depends(get_session)):
    return [_serialize_account(r) for r in acc_repo.list_all(session)]


@router.get("/{account_id}/publications")
def list_account_publications(
    account_id: int,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
):
    if acc_repo.get(session, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    from app.repositories import publications as pub_repo
    return pub_repo.list_completed_for_account(session, account_id, limit=limit)


def _normalize_proxy_in_body(body: dict) -> dict:
    """Return `body` with `proxy` rewritten into a form httpx (and the
    daily probe job) can dial. Empty string passes through as 'unset' —
    both the wizard and cleanup flows rely on being able to blank a
    proxy without tripping validation. Unrecognized non-empty input
    raises 400 so users see what's wrong instead of the failure
    surfacing days later in a scheduler log."""
    if "proxy" not in body or not (body["proxy"] or "").strip():
        return body
    from app.services.proxy_intake import normalize_proxy
    normalized = normalize_proxy(body["proxy"])
    if normalized is None:
        raise HTTPException(status_code=400,
                            detail="unrecognized proxy format "
                                   "(expected user:pass@host:port, "
                                   "host:port:user:pass, or scheme://...)")
    return {**body, "proxy": normalized}


@router.post("")
def create_account(body: dict, session: Session = Depends(get_session)):
    body = _normalize_proxy_in_body(body)
    try:
        aid = acc_repo.create(session, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": aid}


@router.post("/mark-warmed-up")
def mark_warmed_up(body: dict, session: Session = Depends(get_session)):
    account_ids = body.get("account_ids") or body.get("ids") or []
    if (not isinstance(account_ids, list)
            or not account_ids
            or not all(isinstance(i, int) for i in account_ids)):
        raise HTTPException(status_code=400,
                            detail="account_ids must be a non-empty list")
    rows = [acc_repo.get(session, aid) for aid in account_ids]
    if any(r is None for r in rows):
        raise HTTPException(status_code=400, detail="account not found")
    return {"updated": acc_repo.mark_warmed_up(session, account_ids)}


@router.post("/warmup-mode")
def set_warmup_mode(body: dict, session: Session = Depends(get_session)):
    account_ids = body.get("account_ids") or body.get("ids") or []
    mode = (body.get("mode") or "").strip()
    if mode not in {"count", "daily"}:
        raise HTTPException(status_code=400,
                            detail="mode must be 'count' or 'daily'")
    if (not isinstance(account_ids, list)
            or not account_ids
            or not all(isinstance(i, int) for i in account_ids)):
        raise HTTPException(status_code=400,
                            detail="account_ids must be a non-empty list")
    rows = [acc_repo.get(session, aid) for aid in account_ids]
    if any(r is None for r in rows):
        raise HTTPException(status_code=400, detail="account not found")
    return {"updated": acc_repo.bulk_set_warmup_mode(session, account_ids, mode)}


@router.patch("/{account_id}")
def update_account(account_id: int, body: dict,
                   session: Session = Depends(get_session)):
    body = _normalize_proxy_in_body(body)
    cur = acc_repo.get(session, account_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="account not found")
    if "profile_name" in body:
        new_name = (body.get("profile_name") or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="profile_name required")
        if len(new_name) > 100:
            raise HTTPException(status_code=400,
                                detail="profile_name must be <= 100 characters")
        if acc_repo.profile_name_exists(session, new_name,
                                        exclude_account_id=account_id):
            raise HTTPException(status_code=400,
                                detail="profile_name already used")
        body = {**body, "profile_name": new_name}
        if (cur.get("geelark_phone_id")
                and (cur.get("profile_name") or "") != new_name):
            from app.services import geelark_service
            from app.locks import OPERATION_LOCK
            with OPERATION_LOCK:
                try:
                    geelark_service.workspace_client(session).update_phone_detail(
                        cur["geelark_phone_id"], name=new_name)
                except Exception as e:
                    raise HTTPException(status_code=502, detail=str(e))
    new_proxy = (body.get("proxy") or "").strip() if "proxy" in body else None
    if (
        new_proxy
        and new_proxy != (cur.get("proxy") or "").strip()
        and (cur.get("geelark_phone_id") or "").strip()
    ):
        # Editing the proxy in the account drawer used to write only the
        # local row: the daily probe then green-lit the NEW string while
        # the cloud phone kept routing through the OLD proxy — monitoring
        # all green, phone effectively blind. Push GeeLark first; only a
        # successful push lets the DB write proceed.
        from app.services import phone_runtime
        if phone_runtime.get_active_for_account(session, account_id):
            raise HTTPException(
                status_code=409,
                detail="phone has an in-flight session "
                       "(warmup/publish/engagement) — changing its proxy "
                       "mid-run would swap the exit IP under a live RPA; "
                       "retry when it finishes")
        from app.services import geelark_service
        from app.locks import OPERATION_LOCK
        from geelark_client import proxy_url_to_static_config
        with OPERATION_LOCK:
            try:
                cfg = proxy_url_to_static_config(new_proxy)
                geelark_service.workspace_client(session).update_phone_detail(
                    cur["geelark_phone_id"], proxy_config=cfg)
            except Exception as e:
                raise HTTPException(status_code=502,
                                    detail=f"geelark proxy sync failed: {e}")
    if not acc_repo.update(session, account_id, body):
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}

@router.post("/replace-proxies")
def replace_proxies(body: dict, session: Session = Depends(get_session)):
    """Bulk-replace proxies on a set of accounts and sync each cloud phone.

    Body: `{"account_ids": [int,...], "proxies": ["socks5://...", ...]}`.
    Proxies are round-robin assigned (`proxies[i % len(proxies)]`) so a
    smaller pool of new proxies can refresh a larger group of expiring
    accounts.

    For every (account, proxy) pair we:
      1. push the new proxy to the GeeLark cloud phone via
         /phone/detail/update, and only on success
      2. write proxy_expires_at (now+30d) + clear the probe-derived
         columns via acc_repo.update.

    A per-account result list comes back so the UI can highlight failures
    (e.g. GeeLark rejected the proxy, phone has no envId, etc.) without
    losing partial successes.
    """
    account_ids = body.get("account_ids") or []
    raw_proxies = body.get("proxies") or []
    if (not isinstance(account_ids, list)
            or not account_ids
            or not all(isinstance(i, int) for i in account_ids)):
        raise HTTPException(status_code=400,
                            detail="account_ids must be a non-empty list of ints")
    if (not isinstance(raw_proxies, list)
            or not raw_proxies
            or not all(isinstance(p, str) for p in raw_proxies)):
        raise HTTPException(status_code=400,
                            detail="proxies must be a non-empty list of strings")

    from app.services.proxy_intake import normalize_proxy
    normalized: list[str] = []
    for idx, raw in enumerate(raw_proxies, start=1):
        n = normalize_proxy(raw)
        if n is None:
            raise HTTPException(status_code=400,
                                detail=f"proxy #{idx} ({raw!r}) is not a "
                                       "recognizable format")
        normalized.append(n)

    accounts: list[dict] = []
    for aid in account_ids:
        row = acc_repo.get(session, aid)
        if row is None:
            raise HTTPException(status_code=404,
                                detail=f"account {aid} not found")
        accounts.append(row)

    from app.services import geelark_service
    from app.locks import OPERATION_LOCK
    from geelark_client import proxy_url_to_static_config

    from app.services import phone_runtime
    from app.repositories.accounts import PROXY_DEFAULT_TTL
    from datetime import datetime

    results: list[dict] = []
    with OPERATION_LOCK:
        client = geelark_service.workspace_client(session)
        for i, account in enumerate(accounts):
            new_proxy = normalized[i % len(normalized)]
            phone_id = (account.get("geelark_phone_id") or "").strip()
            result = {
                "account_id": account["id"],
                "profile_name": account.get("profile_name"),
                "proxy": new_proxy,
                "geelark_synced": False,
                "db_updated": False,
                "error": None,
            }
            # Same-string re-paste means "renewed the subscription on the
            # same endpoints" — the lifecycle stamp hook only fires on a
            # CHANGED value, so pass the new expiry explicitly (explicit
            # wins) or a monthly renewal leaves the dashboard red forever.
            patch = {
                "proxy": new_proxy,
                "proxy_expires_at": datetime.utcnow() + PROXY_DEFAULT_TTL,
            }
            if not phone_id:
                # DB is the source of truth — apply the change anyway so
                # the user's pasted assignment isn't lost. UI surfaces
                # "no envId" so they know GeeLark wasn't touched.
                acc_repo.update(session, account["id"], patch)
                result["db_updated"] = True
                result["error"] = "no geelark_phone_id"
                results.append(result)
                continue
            if phone_runtime.get_active_for_account(session, account["id"]):
                # Swapping the proxy under a live RPA changes the exit IP
                # mid-session — a platform risk flag worse than failing.
                result["error"] = ("phone busy (in-flight session) — "
                                   "retry when the run finishes")
                results.append(result)
                continue
            try:
                cfg = proxy_url_to_static_config(new_proxy)
                client.update_phone_detail(phone_id, proxy_config=cfg)
                result["geelark_synced"] = True
            except Exception as e:
                result["error"] = f"geelark sync failed: {e}"
                results.append(result)
                continue
            acc_repo.update(session, account["id"], patch)
            result["db_updated"] = True
            results.append(result)

    ok_count = sum(1 for r in results if r["geelark_synced"] and r["db_updated"])
    return {
        "items": results,
        "total": len(results),
        "synced": ok_count,
        "failed": len(results) - ok_count,
    }


@router.delete("/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    return _delete_accounts([account_id], session)


@router.post("/batch-delete")
def batch_delete_accounts(body: dict, session: Session = Depends(get_session)):
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=400,
                            detail="ids must be a list of ints")
    if not ids:
        return {"deleted": 0}
    return _delete_accounts(ids, session)


def _delete_accounts(ids: list[int], session: Session) -> dict:
    from app.services import geelark_service
    from app.locks import OPERATION_LOCK
    with OPERATION_LOCK:
        try:
            return geelark_service.delete_accounts(
                session, geelark_service.workspace_client(session), ids)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))

@router.patch("/{account_id}/login-toggle")
def login_toggle(account_id: int, session: Session = Depends(get_session)):
    cur = acc_repo.get(session, account_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="account not found")
    acc_repo.update(session, account_id,
                    {"tiktok_logged_in": 0 if cur["tiktok_logged_in"] else 1})
    return {"ok": True}

@router.post("/import-from-geelark")
def import_geelark(session: Session = Depends(get_session)):
    from app.services import geelark_service
    from app.locks import OPERATION_LOCK
    with OPERATION_LOCK:
        n = geelark_service.import_from_geelark(
            session, geelark_service.workspace_client(session))
    return {"imported": n}


@router.api_route("/{account_id}/avatar", methods=["GET", "POST", "DELETE"])
def avatar_removed(account_id: int):
    raise HTTPException(status_code=404, detail="avatar feature removed")


_HOLD_MIN_HOURS = 1
_HOLD_MAX_HOURS = 12
_HOLD_DEFAULT_HOURS = 2


@router.post("/{account_id}/phone/hold")
def hold_phone(account_id: int, body: dict,
               session: Session = Depends(get_session)):
    """Mark a phone as "manually in use" so idle_phone_sweeper leaves it
    alone.

    Phase 4a: creates a phone_session(source='manual') via
    PhoneRuntimeManager — the same dual-write that backs warmup /
    publish / engagement. The sweeper's dual-read rule then naturally
    skips this account because an active session row exists.
    """
    from app.services import phone_runtime
    cur = acc_repo.get(session, account_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="account not found")
    env_id = cur.get("geelark_phone_id")
    if not env_id:
        raise HTTPException(status_code=400,
                            detail="account has no GeeLark phone")

    hours = body.get("hours", _HOLD_DEFAULT_HOURS)
    if isinstance(hours, bool) or not isinstance(hours, int):
        raise HTTPException(status_code=400, detail="hours must be an integer")
    if hours < _HOLD_MIN_HOURS or hours > _HOLD_MAX_HOURS:
        raise HTTPException(
            status_code=400,
            detail=f"hours must be between {_HOLD_MIN_HOURS} "
                   f"and {_HOLD_MAX_HOURS}")

    try:
        sid = phone_runtime.start_session(
            session,
            account_id=account_id,
            env_id=env_id,
            source="manual",
            owner_type="account",
            owner_id=account_id,
            budget_seconds=hours * 3600,
            created_by="operator",
        )
    except phone_runtime.PhoneBusy:
        raise HTTPException(
            status_code=409,
            detail="phone is already in use by another task or hold")
    # The response key is kept for API compat (frontend reads it as
    # "when this hold expires"); the value comes from
    # phone_session.budget_deadline_at — the column on accounts has
    # been retired.
    from app.repositories import phone_sessions as ps_repo
    sess_row = ps_repo.get(session, sid)
    return {"phone_busy_until": _utc_iso_z(sess_row["budget_deadline_at"])}


@router.post("/{account_id}/phone/release")
def release_phone(account_id: int, session: Session = Depends(get_session)):
    """Release a manual phone hold.

    If the account has an active phone_session whose source='manual',
    finalize it via the runtime manager. If the active session belongs
    to a different source (warmup / publish / engagement), DO NOT touch
    the session — those are owned by their respective services.

    Idempotent: with no active manual session, this endpoint is a
    no-op and safe to call repeatedly.
    """
    from app.services import phone_runtime
    if acc_repo.get(session, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is not None and active["source"] == "manual":
        phone_runtime.finalize_success(session, active["id"])
    return {"ok": True}


@router.post("/bulk-mark-logged-in")
def bulk_mark_logged_in(body: dict, session: Session = Depends(get_session)):
    from app.repositories import accounts as acc_repo
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=400,
                            detail="ids must be a list of ints")
    n = acc_repo.bulk_set_logged_in(session, ids)
    return {"updated": n}
