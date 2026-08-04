from fastapi import APIRouter, HTTPException, Depends, Request
from sqlmodel import Session
from app.db import get_session
from app.repositories import settings as srepo

router = APIRouter(prefix="/api/settings")

_HIDDEN_SETTING_KEYS = {
    "warmup_times",
    "default_max_warm_time",
    "default_group",
    "default_os",
    "caption_banned_terms",
}


def _without_hidden_settings(values: dict) -> dict:
    return {k: v for k, v in values.items() if k not in _HIDDEN_SETTING_KEYS}


@router.get("")
def get_settings(session: Session = Depends(get_session)):
    from app import config
    actual = _without_hidden_settings(srepo.get_all(session))
    return {**config.SETTINGS_DEFAULTS, **actual}

@router.patch("")
def update_settings(body: dict, session: Session = Depends(get_session)):
    srepo.update(session, _without_hidden_settings(body))
    return get_settings(session)


@router.get("/gemini-models")
def list_gemini_models(request: Request,
                       session: Session = Depends(get_session)):
    """Lazy-fetched list for the Settings page dropdown. Returns
    [{"id", "display_name", "status"}]. Errors surface as 502 so the UI can
    keep showing the saved value with an inline retry."""
    try:
        import generate_caption
        from app.workspace_secrets import get_secret
        workspace_id = int(request.state.workspace["id"])
        api_key = get_secret(session, workspace_id, "GEMINI_API_KEY")
        return generate_caption.list_models(api_key=api_key) if api_key \
            else generate_caption.list_models()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/secrets")
def get_secrets(request: Request, session: Session = Depends(get_session)):
    """Return masks only; members cannot inspect team credentials."""
    from app import workspace_secrets
    workspace = request.state.workspace
    if not workspace.get("canManageSecrets"):
        raise HTTPException(status_code=403, detail="secret management forbidden")
    return workspace_secrets.get_status(session, workspace["id"])


@router.patch("/secrets")
def update_secrets(body: dict, request: Request,
                   session: Session = Depends(get_session)):
    """Encrypt non-empty credential updates for the current workspace."""
    from app import workspace_secrets
    workspace = request.state.workspace
    if not workspace.get("canManageSecrets"):
        raise HTTPException(status_code=403, detail="secret management forbidden")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected object")
    updates = {
        name: value.strip()
        for name, value in body.items()
        if name in workspace_secrets.ALLOWED_SECRET_KEYS
        and isinstance(value, str) and value.strip()
    }
    if not updates:
        return {"updated": []}
    return {"updated": workspace_secrets.update(session, workspace["id"], updates)}
