from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.repositories import discovered_videos as discovered_repo
from app.repositories import engagement as eng_repo
from app.repositories import eligible_phones as eph_repo
from app.repositories import publications as pub_repo
from app.services import engagement_service
from app.services.engagement_service import COMMENT_MODE_GEELARK_AI

router = APIRouter(prefix="/api/engagement")


def _parse_run_ids(raw_ids: list[str] | None) -> list[int] | None:
    if raw_ids is None:
        return None

    parsed: list[int] = []
    for raw in raw_ids:
        for part in raw.split(","):
            value = part.strip()
            if not value:
                continue
            try:
                run_id = int(value)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="ids must contain positive integers",
                )
            if run_id < 1:
                raise HTTPException(
                    status_code=422,
                    detail="ids must contain positive integers",
                )
            parsed.append(run_id)

    unique_ids = list(dict.fromkeys(parsed))
    if len(unique_ids) > 500:
        raise HTTPException(status_code=422, detail="ids supports at most 500 runs")
    return unique_ids


# Same-phone runs are serialized by account_has_predecessor_inflight
# (lowest id first), and each run occupies its phone for ~4-7 minutes.
# Submitting a same-phone batch all at t=0 burns every later run's
# 40 x 30s PhoneBusy retry budget while it waits its turn — run #5+
# mathematically exhausts the 20-min budget and dies (the 2026-06-16
# incident lost 2657 runs to exactly this; the fix had only landed in
# the revive script until now). Staggering the FIRST submit attempt of
# the k-th same-phone run by k slots means it usually finds the phone
# free on its first try.
SUBMIT_SLOT_SECONDS = 600
ONE_DAY_SECONDS = 86_400


def _per_phone_submit_delays(
        session, run_ids: list[int], *, platform: str) -> dict[int, int]:
    """Spread each phone's runs across quota days and ten-minute slots."""
    accounts = eng_repo.get_run_accounts(session, run_ids)
    daily_limit = engagement_service.daily_comment_limit(session, platform)
    counts: dict[int, int] = {}
    used: dict[int, int] = {}
    delays: dict[int, int] = {}
    for run_id in run_ids:
        aid = accounts.get(run_id)
        idx = counts.get(aid, 0)
        counts[aid] = idx + 1
        if aid not in used:
            used[aid] = (
                eng_repo.count_account_attempts_today(
                    session, account_id=aid, platform=platform,
                )
                if daily_limit > 0
                else 0
            )
        position = used[aid] + idx
        if daily_limit > 0:
            day = position // daily_limit
            slot = position % daily_limit
        else:
            day = 0
            slot = position
        delays[run_id] = (
            day * ONE_DAY_SECONDS + slot * SUBMIT_SLOT_SECONDS
        )
    return delays


async def _enqueue_and_dispatch_runs(
        session, run_ids: list[int], *, platform: str) -> int:
    """For each run in run_ids: mark_queued + enqueue an outbox row +
    best-effort prompt dispatch. Returns the count actually dispatched
    (the rest sit in the outbox for the periodic dispatcher to retry).
    """
    from app.repositories import outbox as outbox_repo
    from app.tasks.outbox_dispatcher import try_prompt_dispatch

    delays = _per_phone_submit_delays(
        session, run_ids, platform=platform,
    )
    enqueued: list[tuple[int, int, dict]] = []  # (run_id, outbox_id, payload)
    for run_id in run_ids:
        if not eng_repo.mark_queued(session, run_id):
            continue
        payload = {"run_id": run_id,
                   "delay_seconds": delays.get(run_id, 0)}
        outbox_id = outbox_repo.enqueue(
            session,
            intent="submit_engagement_run",
            business_ref=f"engagement_run:{run_id}",
            payload=payload,
        )
        if outbox_id is not None:
            enqueued.append((run_id, outbox_id, payload))
    session.commit()

    dispatched = 0
    for run_id, outbox_id, payload in enqueued:
        if await try_prompt_dispatch(
            outbox_id, "submit_engagement_run", payload,
        ):
            dispatched += 1
    return dispatched


@router.post("/comment")
async def create_comment_engagement(
    body: dict,
    session: Session = Depends(get_session),
):
    try:
        comments_per_video = int(body.get("comments_per_video", 1))
        result = engagement_service.create_comment_batch(
            session,
            account_ids=body.get("account_ids") or [],
            target_links=body.get("target_links") or [],
            target_publication_ids=body.get("target_publication_ids") or [],
            comments_per_video=comments_per_video,
            group_id=body.get("group_id"),
            comment_probability=body.get("comment_probability", 100),
            comment_mode=body.get("comment_mode", COMMENT_MODE_GEELARK_AI),
            comment_prompt_id=body.get("comment_prompt_id"),
            provided_comments=body.get("provided_comments"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    run_ids = result.get("run_ids")
    if run_ids is None:
        run_ids = [
            run["id"]
            for run in eng_repo.list_runs_for_task(session, result["task_id"])
        ]
    deferred = await _enqueue_and_dispatch_runs(
        session, run_ids, platform="tiktok",
    )
    return {**result, "deferred_runs": deferred}


@router.post("/comment/facebook")
async def create_facebook_comment_engagement(
    body: dict,
    session: Session = Depends(get_session),
):
    try:
        candidates_per_post = int(body.get("candidates_per_post", 1))
        result = engagement_service.create_comment_batch(
            session,
            platform="facebook",
            account_ids=body.get("account_ids") or [],
            target_links=body.get("target_links") or [],
            target_publication_ids=body.get("target_publication_ids") or [],
            comments_per_video=candidates_per_post,
            group_id=body.get("group_id"),
            comment_mode=body.get("comment_mode", "provided"),
            comment_prompt_id=body.get("comment_prompt_id"),
            provided_comments=body.get("provided_comments"),
            keywords=body.get("keywords") or [],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    run_ids = result.get("run_ids") or [
        run["id"]
        for run in eng_repo.list_runs_for_task(session, result["task_id"])
    ]
    deferred = await _enqueue_and_dispatch_runs(
        session, run_ids, platform="facebook",
    )
    return {**result, "deferred_runs": deferred}


@router.get("/tasks")
def list_engagement_tasks(
    group_id: int | None = None,
    platform: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    return eng_repo.list_tasks(
        session, group_id=group_id, platform=platform, limit=limit,
    )


@router.get("/runs")
def list_engagement_runs(
    group_id: int | None = None,
    platform: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    before_id: int | None = Query(None, ge=1),
    ids: list[str] | None = Query(None),
    before_activity_at: datetime | None = Query(None),
    status: str | None = None,
    source: str | None = None,
    session: Session = Depends(get_session),
):
    run_ids = _parse_run_ids(ids)
    return {
        "items": eng_repo.list_runs(
            session,
            group_id=group_id,
            platform=platform,
            limit=limit,
            before_id=before_id,
            ids=run_ids,
            before_activity_at=before_activity_at,
            status=status,
            source=source,
        )
    }


@router.get("/runs/stats")
def get_engagement_runs_stats(
    group_id: int | None = None,
    platform: str | None = None,
    source: str | None = None,
    session: Session = Depends(get_session),
):
    return eng_repo.runs_stats(
        session, group_id=group_id, platform=platform, source=source,
    )


@router.get("/eligible-phones")
def list_eligible_phones(
    platform: str = "tiktok",
    session: Session = Depends(get_session),
):
    return {"items": eph_repo.list_eligible_commenters(session, platform=platform)}


@router.get("/discover-targets")
def list_discover_targets(
    limit: int = Query(2000, ge=1, le=20000),
    session: Session = Depends(get_session),
) -> dict:
    items = discovered_repo.list_unused_targets(session, limit=limit)
    return {"items": [_serialize_discover(v) for v in items]}


def _serialize_discover(v) -> dict:
    return {
        "url": v.url,
        "title": v.title,
        "author_nickname": v.author_nickname or v.author_unique_id,
        "play_count": v.play_count,
        "like_count": v.like_count,
        "create_time": v.create_time.isoformat() if v.create_time else None,
        "search_keyword": v.search_keyword,
    }


@router.get("/targets")
def list_engagement_targets(
    group_id: int | None = None,
    platform: str = "tiktok",
    limit: int = Query(100, ge=1, le=200),
    session: Session = Depends(get_session),
):
    return {
        "items": pub_repo.list_comment_targets(
            session,
            group_id=group_id,
            platform=platform,
            limit=limit,
        )
    }


@router.get("/tasks/{task_id}")
def get_engagement_task(
    task_id: int,
    session: Session = Depends(get_session),
):
    task = eng_repo.get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="engagement task not found")
    return {"task": task, "runs": eng_repo.list_runs_for_task(session, task_id)}


@router.get("/runs/{run_id}/screenshot")
def get_engagement_failure_screenshot(
    run_id: int,
    session: Session = Depends(get_session),
):
    from app.api.videos import _fetch_screenshot_bytes, _screenshot_cache_path

    run = eng_repo.get_run(session, run_id)
    url = (
        run.get("result_screenshot")
        or run.get("fail_screenshot")
        or ""
    ).strip() if run else ""
    if not url:
        raise HTTPException(status_code=404, detail="no screenshot")
    cache = _screenshot_cache_path(url)
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
