"""Resolve the current Radar user and workspace for every Studio request."""

import httpx
from fastapi import HTTPException, Request

from app import config

_VERIFY_TIMEOUT = 2.0
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=_VERIFY_TIMEOUT)
    return _client


def _fetch_radar_session(cookie_header: str) -> dict | None:
    """Return Radar's authoritative user + workspace payload."""
    response = _http().get(
        config.RADAR_AUTH_VERIFY_URL,
        headers={"cookie": cookie_header, "accept": "application/json"},
    )
    if response.status_code == 401:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"radar verify returned {response.status_code}")
    data = response.json()
    if not isinstance(data, dict) or not data.get("user"):
        return None
    return data


def _reset_cache() -> None:
    """Backward-compatible test hook; workspace auth is intentionally uncached."""


def require_auth(request: Request) -> dict:
    """Fail closed unless Radar returns a valid, current workspace context."""
    cookie_header = request.headers.get("cookie", "")
    if not cookie_header:
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        payload = _fetch_radar_session(cookie_header)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="auth backend unavailable") from exc
    if payload is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    # Old test doubles returned the user directly. Production only accepts the
    # new payload shape, while this compatibility branch remains fail-closed
    # unless the test user also carries a workspace.
    if "user" in payload:
        user = payload.get("user")
        workspace = payload.get("workspace")
    else:
        user = payload
        workspace = payload.get("workspace")
    if not isinstance(user, dict) or not isinstance(workspace, dict):
        raise HTTPException(status_code=503, detail="invalid workspace context")

    workspace_id = workspace.get("id")
    if not isinstance(workspace_id, int) or workspace_id <= 0:
        raise HTTPException(status_code=503, detail="invalid workspace context")

    request.state.workspace = workspace
    request.state.radar_user = user
    return {**user, "workspace": workspace}
