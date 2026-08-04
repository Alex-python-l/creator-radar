from collections import Counter
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.models import Account, EngagementRun, EngagementTask

TERMINAL_RESULTS = ("SUCCESS", "FAILURE", "SKIPPED")
INFLIGHT_RESULTS = ("QUEUED", "INITIATED")
# Statuses where the account is committed to this run and should be
# considered "busy" for UI selection purposes. Wider than INFLIGHT_RESULTS,
# which is the reconciliation set (a PENDING run hasn't been queued yet).
BUSY_RESULTS = ("PENDING", "QUEUED", "INITIATED")
def _dump_task(obj: EngagementTask) -> dict:
    return obj.model_dump()


def _dump_run(obj: EngagementRun) -> dict:
    return obj.model_dump()


def _summary_for(results: list[str]) -> str:
    if not results:
        return "0 runs"
    counts = Counter(result.lower() for result in results)
    return ", ".join(f"{status}={counts[status]}" for status in sorted(counts))


def create_task(session: Session, *, group_id: Optional[int], action: str,
                source: str, target_links: list[str],
                target_publication_ids: list[int],
                comment_probability: int,
                comment_mode: str = "geelark_ai",
                comment_brief: Optional[str] = None,
                comment_prompt_id: Optional[int] = None,
                comments_per_video: int = 1,
                platform: str = "tiktok",
                keywords: Optional[list[str]] = None) -> int:
    obj = EngagementTask(
        group_id=group_id,
        action=action,
        source=source,
        target_links=target_links,
        target_publication_ids=target_publication_ids,
        comment_probability=comment_probability,
        comment_mode=comment_mode,
        comment_brief=comment_brief,
        comment_prompt_id=comment_prompt_id,
        comments_per_video=comments_per_video,
        platform=platform,
        keywords=keywords or [],
    )
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def get_task(session: Session, task_id: int) -> dict | None:
    obj = session.get(EngagementTask, task_id)
    return _dump_task(obj) if obj else None


def list_tasks(session: Session, *, group_id: Optional[int] = None,
               platform: Optional[str] = None,
               limit: int = 50) -> list[dict]:
    stmt = select(EngagementTask).order_by(EngagementTask.id.desc())
    if group_id is not None:
        stmt = stmt.where(EngagementTask.group_id == group_id)
    if platform is not None:
        stmt = stmt.where(EngagementTask.platform == platform)
    stmt = stmt.limit(max(1, min(limit, 200)))
    return [_dump_task(row) for row in session.exec(stmt).all()]


def create_run(session: Session, *, engagement_task_id: int,
               account_id: Optional[int], target_link: str, action: str,
               target_publication_id: Optional[int] = None,
               target_account_id: Optional[int] = None) -> int:
    obj = EngagementRun(
        engagement_task_id=engagement_task_id,
        account_id=account_id,
        target_publication_id=target_publication_id,
        target_account_id=target_account_id,
        target_link=target_link,
        action=action,
    )
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def get_run(session: Session, run_id: int) -> dict | None:
    obj = session.get(EngagementRun, run_id)
    return _dump_run(obj) if obj else None


def list_runs_for_task(session: Session, task_id: int) -> list[dict]:
    stmt = (
        select(EngagementRun, Account.profile_name)
        .outerjoin(Account, Account.id == EngagementRun.account_id)
        .where(EngagementRun.engagement_task_id == task_id)
        .order_by(EngagementRun.id)
    )
    out = []
    for run, profile_name in session.exec(stmt).all():
        row = _dump_run(run)
        row["account_profile_name"] = profile_name
        out.append(row)
    return out


_STATUS_RESULT_MAP: dict[str, tuple[str, ...]] = {
    "success": ("SUCCESS",),
    "failed": ("FAILURE",),
    "running": ("INITIATED",),
    "queued": ("QUEUED", "PENDING"),
}


def list_runs(session: Session, *, group_id: Optional[int] = None,
              limit: int = 100,
              before_id: Optional[int] = None,
              ids: Optional[list[int]] = None,
              before_activity_at: Optional[datetime] = None,
              status: Optional[str] = None,
              source: Optional[str] = None,
              platform: Optional[str] = None) -> list[dict]:
    """List runs newest-activity-first.

    Sort key is `COALESCE(finished_at, started_at, created_at)` desc with
    id desc as tie-breaker. "Activity" means the last meaningful timestamp
    on the row — SUCCESS finished_at climbs above older inflight, freshly
    PENDING jumps above stale ones.

    Pagination cursor is the compound `(before_activity_at, before_id)`.
    Pure `before_id` (legacy single-arg) still works but skips rows whose
    activity_at < cursor's activity_at but id > cursor id; callers should
    upgrade to the compound form."""
    activity_at = func.coalesce(
        EngagementRun.finished_at,
        EngagementRun.started_at,
        EngagementRun.created_at,
    ).label("activity_at")
    stmt = (
        select(EngagementRun, EngagementTask, Account.profile_name, activity_at)
        .join(EngagementTask, EngagementTask.id == EngagementRun.engagement_task_id)
        .outerjoin(Account, Account.id == EngagementRun.account_id)
        .order_by(activity_at.desc(), EngagementRun.id.desc())
    )
    if group_id is not None:
        stmt = stmt.where(EngagementTask.group_id == group_id)
    if platform is not None:
        stmt = stmt.where(EngagementTask.platform == platform)
    if ids is not None:
        if not ids:
            return []
        unique_ids = list(dict.fromkeys(ids))
        stmt = stmt.where(EngagementRun.id.in_(unique_ids))
    if before_activity_at is not None and before_id is not None:
        stmt = stmt.where(
            or_(
                activity_at < before_activity_at,
                and_(activity_at == before_activity_at,
                     EngagementRun.id < before_id),
            )
        )
    elif before_id is not None:
        stmt = stmt.where(EngagementRun.id < before_id)
    if status and status != "all":
        results = _STATUS_RESULT_MAP.get(status)
        if results is None:
            return []
        stmt = stmt.where(EngagementRun.result.in_(results))
    if source == "owned":
        stmt = stmt.where(EngagementTask.source == "publication_share_links")
    elif source == "manual":
        stmt = stmt.where(EngagementTask.source != "publication_share_links")
    stmt = stmt.limit(max(1, min(limit, 500)))

    rows = []
    for run, task, profile_name, act_at in session.exec(stmt).all():
        row = _dump_run(run)
        row["account_profile_name"] = profile_name
        row["task_status"] = task.status
        row["task_source"] = task.source
        row["task_comment_mode"] = task.comment_mode
        row["task_created_at"] = task.created_at
        row["task_updated_at"] = task.updated_at
        row["task_finished_at"] = task.finished_at
        row["activity_at"] = act_at
        rows.append(row)
    return rows


def runs_stats(session: Session, *, group_id: Optional[int] = None,
               source: Optional[str] = None,
               platform: Optional[str] = None) -> dict[str, int]:
    """Counts of engagement runs by status bucket, filtered by group/source/platform.

    Buckets match the `status` filter keys used by /engagement/runs:
    success, running, queued, failed. PENDING rolls into queued.
    """
    stmt = (
        select(EngagementRun.result, func.count(EngagementRun.id))
        .join(EngagementTask, EngagementTask.id == EngagementRun.engagement_task_id)
        .group_by(EngagementRun.result)
    )
    if group_id is not None:
        stmt = stmt.where(EngagementTask.group_id == group_id)
    if platform is not None:
        stmt = stmt.where(EngagementTask.platform == platform)
    if source == "owned":
        stmt = stmt.where(EngagementTask.source == "publication_share_links")
    elif source == "manual":
        stmt = stmt.where(EngagementTask.source != "publication_share_links")

    out = {"total": 0, "success": 0, "running": 0, "queued": 0, "failed": 0}
    for result, count in session.exec(stmt).all():
        n = int(count)
        out["total"] += n
        if result == "SUCCESS":
            out["success"] += n
        elif result == "FAILURE":
            out["failed"] += n
        elif result == "INITIATED":
            out["running"] += n
        elif result in ("QUEUED", "PENDING"):
            out["queued"] += n
    return out


def _touch_parent(session: Session, task_id: int, status: str) -> None:
    task = session.get(EngagementTask, task_id)
    if task is None:
        return
    task.status = status
    task.updated_at = datetime.utcnow()
    session.add(task)


def mark_queued(session: Session, run_id: int) -> bool:
    run = session.get(EngagementRun, run_id)
    if run is None or run.result not in ("PENDING", "QUEUED"):
        return False
    run.result = "QUEUED"
    session.add(run)
    _touch_parent(session, run.engagement_task_id, "QUEUED")
    session.commit()
    return True


def unqueue(session: Session, run_id: int) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None or run.result != "QUEUED":
        return
    task_id = run.engagement_task_id
    run.result = "PENDING"
    session.add(run)
    results = session.exec(
        select(EngagementRun.result)
        .where(EngagementRun.engagement_task_id == task_id)
    ).all()
    task_status = "PENDING"
    if any(result == "INITIATED" for result in results):
        task_status = "RUNNING"
    elif any(result == "QUEUED" for result in results):
        task_status = "QUEUED"
    _touch_parent(session, task_id, task_status)
    session.commit()


def get_run_accounts(
        session: Session, run_ids: list[int]) -> dict[int, int]:
    """run_id -> account_id for the given runs (missing ids omitted).
    Used to group runs by phone when computing submit stagger."""
    if not run_ids:
        return {}
    rows = session.exec(
        select(EngagementRun.id, EngagementRun.account_id)
        .where(EngagementRun.id.in_(run_ids))
    ).all()
    return {rid: aid for rid, aid in rows}


def set_task_placeholder(session: Session, run_id: int) -> None:
    """Stamp geelark_task_id='pending' right before the GeeLark create
    call. mark_initiated overwrites it with the real task id; the submit
    gate refuses runs still carrying it (a crash left the GeeLark-side
    outcome unknown — re-running would risk a duplicate comment)."""
    run = session.get(EngagementRun, run_id)
    if run is None or run.result not in ("PENDING", "QUEUED"):
        return
    run.geelark_task_id = "pending"
    session.add(run)
    session.commit()


def mark_initiated(session: Session, run_id: int, *, task_id: str,
                   schedule_at: int, notes: str) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.result = "INITIATED"
    run.geelark_task_id = task_id
    run.schedule_at = schedule_at
    run.notes = notes
    run.started_at = datetime.utcnow()
    session.add(run)
    _touch_parent(session, run.engagement_task_id, "RUNNING")
    session.commit()


def set_target_context(session: Session, run_id: int, *,
                       title: Optional[str],
                       description: Optional[str]) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.target_title = title
    run.target_description = description
    session.add(run)
    session.commit()


def set_generated_comment(session: Session, run_id: int, *,
                          comment_text: str,
                          source: str) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.comment_text = comment_text
    run.comment_source = source
    run.comment_generated_at = datetime.utcnow()
    session.add(run)
    session.commit()


def set_comment_candidates(session: Session, run_id: int, *,
                           candidates: list[str],
                           source: str) -> None:
    """Write FB candidate pool; comment_text shows the first one."""
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    run.comment_candidates = list(candidates)
    run.comment_text = candidates[0] if candidates else None
    run.comment_source = source
    run.comment_generated_at = datetime.utcnow()
    session.add(run)
    session.commit()


def set_result(session: Session, run_id: int, result: str, notes: str, *,
               fail_code: Optional[int] = None,
               fail_screenshot: Optional[str] = None,
               result_screenshot: Optional[str] = None,
               cost: Optional[int] = None) -> None:
    run = session.get(EngagementRun, run_id)
    if run is None:
        return
    now = datetime.utcnow()
    run.result = result
    run.notes = notes
    if fail_code is not None:
        run.fail_code = fail_code
    if fail_screenshot is not None:
        run.fail_screenshot = fail_screenshot
    if result_screenshot is not None:
        run.result_screenshot = result_screenshot
    if cost is not None:
        run.cost = cost
    run.finished_at = now
    run.last_checked_at = now
    session.add(run)
    session.commit()


def mark_skipped(session: Session, run_id: int, notes: str) -> None:
    set_result(session, run_id, "SKIPPED", notes)


def count_account_attempts_today(session: Session, *, account_id: int,
                                 platform: str,
                                 exclude_run_id: Optional[int] = None) -> int:
    """Runs charged against today's per-phone per-platform comment cap.

    A row consumes quota only after submission starts. PENDING/QUEUED
    rows are backlog, not attempts; counting them deadlocks any batch
    larger than the daily limit because even its head sees a full quota.
    SUCCESS rows created before started_at existed use created_at as a
    compatibility fallback.
    """
    start = datetime.utcnow().replace(hour=0, minute=0, second=0,
                                      microsecond=0)
    stmt = (
        select(func.count(EngagementRun.id))
        .join(EngagementTask,
              EngagementTask.id == EngagementRun.engagement_task_id)
        .where(
            EngagementRun.account_id == account_id,
            EngagementTask.platform == platform,
            or_(
                EngagementRun.started_at >= start,
                and_(
                    EngagementRun.result == "SUCCESS",
                    EngagementRun.started_at.is_(None),
                    EngagementRun.created_at >= start,
                ),
            ),
        )
    )
    if exclude_run_id is not None:
        stmt = stmt.where(EngagementRun.id != exclude_run_id)
    return int(session.exec(stmt).one() or 0)


def account_has_inflight(session: Session, account_id: int, *,
                         exclude_run_id: Optional[int] = None) -> bool:
    stmt = (
        select(EngagementRun.id)
        .where(EngagementRun.account_id == account_id)
        .where(EngagementRun.result.in_(INFLIGHT_RESULTS))
    )
    if exclude_run_id is not None:
        stmt = stmt.where(EngagementRun.id != exclude_run_id)
    return session.exec(stmt).first() is not None


def account_has_predecessor_inflight(session: Session, account_id: int, run_id: int) -> bool:
    """True iff another engagement run for the same account with a SMALLER id
    is still inflight (QUEUED/INITIATED). Used to serialize batch siblings —
    the smallest-id sibling proceeds; later siblings wait."""
    stmt = (
        select(EngagementRun.id)
        .where(EngagementRun.account_id == account_id)
        .where(EngagementRun.result.in_(INFLIGHT_RESULTS))
        .where(EngagementRun.id < run_id)
    )
    return session.exec(stmt).first() is not None


def recompute_task_status(session: Session, task_id: int) -> None:
    task = session.get(EngagementTask, task_id)
    if task is None:
        return

    results = session.exec(
        select(EngagementRun.result)
        .where(EngagementRun.engagement_task_id == task_id)
    ).all()
    now = datetime.utcnow()
    task.summary = _summary_for(results)
    task.updated_at = now

    if not results:
        task.status = "FAILURE"
        task.finished_at = now
    elif any(result in INFLIGHT_RESULTS for result in results):
        task.status = "RUNNING"
    elif all(result == "SUCCESS" for result in results):
        task.status = "SUCCESS"
        task.finished_at = now
    elif any(result == "SUCCESS" for result in results) and any(
        result in ("FAILURE", "SKIPPED") for result in results
    ):
        task.status = "PARTIAL_FAILURE"
        task.finished_at = now
    elif all(result == "SKIPPED" for result in results):
        task.status = "FAILURE"
        task.finished_at = now
    else:
        task.status = "FAILURE"
        task.finished_at = now

    session.add(task)
    session.commit()
