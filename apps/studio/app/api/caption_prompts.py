from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.services import caption_prompts_service as svc

router = APIRouter(prefix="/api/caption-prompts")


@router.get("")
def list_prompts(session: Session = Depends(get_session)):
    return svc.list_all(session)


@router.post("")
def create_prompt(body: dict, session: Session = Depends(get_session)):
    try:
        pid = svc.create(
            session,
            name=body.get("name") or "",
            body=body.get("body") or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": pid}


@router.patch("/{prompt_id}")
def patch_prompt(prompt_id: int, body: dict,
                 session: Session = Depends(get_session)):
    if svc.get(session, prompt_id) is None:
        raise HTTPException(status_code=404, detail="prompt not found")
    try:
        svc.update(
            session, prompt_id,
            name=body.get("name") if "name" in body else None,
            body=body.get("body") if "body" in body else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int, session: Session = Depends(get_session)):
    if svc.get(session, prompt_id) is None:
        raise HTTPException(status_code=404, detail="prompt not found")
    try:
        svc.delete(session, prompt_id)
    except svc.PromptInUse as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "prompt in use", "groups": e.group_names},
        )
    return {"ok": True}
