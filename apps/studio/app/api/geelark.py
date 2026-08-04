"""GeeLark callback receiver.

The endpoint:
- is unauthenticated by cookie (GeeLark cannot send our session)
- authenticates via `?token=` against config.GEELARK_CALLBACK_TOKEN with
  constant-time compare
- never raises 4xx/5xx for parse / DB issues so GeeLark does not retry
  on our problems (auth failures still 401)
- writes the raw envelope (headers + body) to geelark_callback_log
- de-duplicates via callback_inbox (UNIQUE on task_id+status+body_hash)
  and only defers process_geelark_callback_task when the row is NEW.
  GeeLark's at-least-once retries collapse to one wake.

callback_inbox is the inbox half of the outbox/inbox pattern: the
endpoint is the only writer; the worker is the only reader. A polling
chain still runs as a fallback for the rare case where GeeLark drops a
callback entirely, so this path is a fast-path for convergence, not a
single point of failure.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlmodel import Session

from app import config
from app.db import get_system_session


router = APIRouter()
logger = logging.getLogger(__name__)


def _best_effort_parse(body_text: str) -> tuple[str | None, int | None]:
    """Try to pull (taskId, status) out of an undocumented payload.

    Phase 0a observation (2026-06-15, 6 real callbacks): the live
    schema is
        {"type": int, "packageName": str, "appVersionId": str,
         "id": str, "result": int}
    so we accept both `result` and `status` for the status field. The
    raw body is always stored, so wrong guesses are fixable without
    re-deploying.
    """
    if not body_text:
        return None, None
    try:
        payload = json.loads(body_text)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    candidate_id = (
        payload.get("taskId")
        or payload.get("task_id")
        or payload.get("ID")
        or payload.get("id")
    )
    parsed_task_id: str | None = None
    if isinstance(candidate_id, (str, int)):
        parsed_task_id = str(candidate_id)[:64]
    candidate_status = (
        payload.get("status")
        if payload.get("status") is not None
        else payload.get("result")
    )
    parsed_status: int | None = None
    if isinstance(candidate_status, int) and not isinstance(candidate_status, bool):
        parsed_status = candidate_status
    elif isinstance(candidate_status, str) and candidate_status.isdigit():
        parsed_status = int(candidate_status)
    return parsed_task_id, parsed_status


@router.post("/api/geelark/callback")
async def geelark_callback(
    request: Request,
    token: str | None = None,
    session: Session = Depends(get_system_session),
):
    expected = config.GEELARK_CALLBACK_TOKEN
    if not expected:
        # Token not configured — refuse rather than silently accept.
        # Without this an unconfigured environment would happily log any
        # POST as a "callback".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="callback not configured",
        )

    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bad token",
        )

    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")
    headers_json = dict(request.headers)
    source_ip = request.client.host if request.client else None
    parsed_task_id, parsed_status = _best_effort_parse(body_text)
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    workspace_id = None
    if parsed_task_id:
        workspace_id = session.execute(
            text(
                "SELECT workspace_id FROM phone_sessions "
                "WHERE geelark_task_id=:task_id ORDER BY id DESC LIMIT 1"
            ),
            {"task_id": parsed_task_id},
        ).scalar_one_or_none()

    try:
        session.execute(
            text(
                "INSERT INTO geelark_callback_log "
                "(workspace_id, source_ip, headers_json, body_text, "
                "parsed_task_id, parsed_status) "
                "VALUES (:wid, :src, CAST(:hdr AS json), :body, :tid, :st)"
            ),
            {
                "src": source_ip,
                "wid": workspace_id,
                "hdr": json.dumps(headers_json),
                "body": body_text,
                "tid": parsed_task_id,
                "st": parsed_status,
            },
        )
        session.commit()
    except Exception:
        # Persisting the raw log must NOT 5xx — GeeLark would retry
        # and we'd duplicate side effects. Just log + drop.
        logger.exception("geelark callback persistence failed")
        session.rollback()

    # Idempotent inbox write. Only when we have a parsable task_id is
    # there anything for the worker to dispatch on; rows without a
    # task_id stay in the raw log only.
    inbox_id: int | None = None
    if parsed_task_id:
        try:
            row = session.execute(
                text(
                    "INSERT INTO callback_inbox "
                    "(workspace_id, task_id, status, body_hash) "
                    "VALUES (:wid, :tid, :st, :bh) "
                    "ON CONFLICT DO NOTHING RETURNING id"
                ),
                {
                    "tid": parsed_task_id,
                    "wid": workspace_id,
                    "st": parsed_status,
                    "bh": body_hash,
                },
            ).first()
            session.commit()
            if row is not None:
                inbox_id = row[0]
        except Exception:
            logger.exception("geelark callback inbox insert failed")
            session.rollback()

    if inbox_id is not None:
        # Lazy import — the api package boots before the procrastinate
        # task module pins its connector at startup.
        from app.tasks.geelark_callback import (
            process_geelark_callback_task,
        )
        try:
            await process_geelark_callback_task.defer_async(
                inbox_id=inbox_id,
            )
        except Exception:
            # If defer fails, the inbox row stays unprocessed; the
            # polling chain still converges this task. A future reaper
            # over (processed_at IS NULL AND received_at < now - N) can
            # retry the dispatch.
            logger.exception(
                "geelark callback defer failed for inbox %d", inbox_id,
            )

    return {"ok": True}
