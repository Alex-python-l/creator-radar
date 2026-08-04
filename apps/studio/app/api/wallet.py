"""GeeLark account wallet endpoint.

Surfaces the operator's prepaid balance so the dashboard can warn when
phones are about to stop running. GeeLark rate-limits `/open/v1/pay/wallet`
to 10 req/min; the dashboard polls every 15s and the sidebar mounts on
every page, so we cache the response in-process for 60s. Web tier is
pinned to a single worker (see CLAUDE.md), so a process-local cache is
enough — no cross-process coordination needed.
"""
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.db import get_session
from app.services.geelark_service import make_client

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wallet")

_TTL_SECONDS = 60.0
_cache: dict[int, dict[str, Any]] = {}


@router.get("")
def get_wallet(request: Request, session: Session = Depends(get_session)) -> dict[str, Any]:
    workspace_id = int(request.state.workspace["id"])
    cached = _cache.setdefault(workspace_id, {"value": None, "fetched_at": 0.0})
    now = time.monotonic()
    if cached["value"] is not None and now - cached["fetched_at"] < _TTL_SECONDS:
        return cached["value"]
    try:
        try:
            client = make_client(session, workspace_id)
        except TypeError:
            client = make_client()
        data = client.get_wallet()
    except Exception as e:
        log.warning("wallet fetch failed: %s", e)
        if cached["value"] is not None:
            return cached["value"]
        return {"error": str(e)}
    cached["value"] = data
    cached["fetched_at"] = now
    return data
