"""Publications repository — one row per (video_id, platform) publishing
action, including that platform's GeeLark phone assignment."""
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import Account, Group, Publication, PublicationAttempt, Video


def create(session: Session, *, video_id: int, platform: str) -> int:
    video = session.get(Video, video_id)
    pub = Publication(
        video_id=video_id,
        platform=platform,
        account_id=video.account_id if video else None,
    )
    session.add(pub)
    session.commit()
    session.refresh(pub)
    return pub.id


def _scalar_id(row) -> int:
    """Coerce a session.exec result to an int id (SQLModel sometimes
    yields the row tuple, sometimes the bare scalar)."""
    if row is None:
        return 0
    if isinstance(row, int):
        return row
    try:
        return int(row[0])
    except (TypeError, KeyError, IndexError):
        return int(row)


def find_or_create(session: Session, *, video_id: int,
                    platform: str) -> int:
    """Idempotent: returns the existing publication's id, or creates a new
    PENDING one. Race-safe via IntegrityError + retry — two concurrent
    requests can't both succeed at INSERT because of the UNIQUE
    (video_id, platform) constraint."""
    existing = session.exec(
        select(Publication.id)
        .where(Publication.video_id == video_id)
        .where(Publication.platform == platform)
    ).first()
    if existing is not None:
        return _scalar_id(existing)
    try:
        return create(session, video_id=video_id, platform=platform)
    except IntegrityError:
        session.rollback()
        existing = session.exec(
            select(Publication.id)
            .where(Publication.video_id == video_id)
            .where(Publication.platform == platform)
        ).first()
        return _scalar_id(existing)


def unclaim(session: Session, pub_id: int) -> None:
    """Reverse of mark_queued: back to PENDING.

    Used when defer_async raises after mark_queued — leaving the
    publication QUEUED would orphan it (auto_publish_scan only picks
    up PENDING rows). The account assignment stays on the video.

    State-guarded: only QUEUED rows get reset. A stale defer that fires
    after the publication is already INITIATED/SUCCESS/FAILURE must not
    clobber that terminal-or-in-flight state, otherwise auto_publish_scan
    treats it as fresh work and re-submits — duplicate posts."""
    pub = session.get(Publication, pub_id)
    if pub is None or pub.result != "QUEUED":
        return
    pub.result = "PENDING"
    session.add(pub)
    session.commit()


def get(session: Session, pub_id: int) -> Optional[dict]:
    p = session.get(Publication, pub_id)
    return p.model_dump() if p else None


def record_attempt(
    session: Session,
    pub_id: int,
    *,
    stage: str,
    source: str,
    message: str,
    error_code: Optional[str] = None,
    fail_screenshot: Optional[str] = None,
    geelark_task_id: Optional[str] = None,
) -> int:
    max_attempt = session.exec(
        select(sa.func.max(PublicationAttempt.attempt_no))
        .where(PublicationAttempt.publication_id == pub_id)
    ).one()
    attempt = PublicationAttempt(
        publication_id=pub_id,
        attempt_no=int(max_attempt or 0) + 1,
        stage=stage,
        source=source,
        message=message,
        error_code=error_code,
        fail_screenshot=fail_screenshot,
        geelark_task_id=geelark_task_id,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt.id


def list_attempts(session: Session, pub_id: int) -> list[dict]:
    rows = session.exec(
        select(PublicationAttempt)
        .where(PublicationAttempt.publication_id == pub_id)
        .order_by(PublicationAttempt.attempt_no)
    ).all()
    return [row.model_dump() for row in rows]


def list_for_video(session: Session, video_id: int) -> list[dict]:
    stmt = (
        select(Publication, Account.profile_name)
        .outerjoin(Account, Account.id == Publication.account_id)
        .where(Publication.video_id == video_id)
        .order_by(Publication.platform)
    )
    out = []
    for pub, profile_name in session.exec(stmt).all():
        d = pub.model_dump()
        d["account_profile_name"] = profile_name
        out.append(d)
    return out


def list_pending(session: Session, platform: str,
                 limit: int | None = None,
                 require_group: bool = False,
                 in_groups: list[int] | None = None) -> list[dict]:
    """PENDING publications joined to Video for stored_path / filename /
    group_id / publication account_id.

    require_group=True INNER-joins groups and keeps only rows whose video
    has a group AND whose group has a caption_prompt_id set. The
    auto_publish_scan / next_publish_scan_ready_at callers pass it so the
    per-platform `limit` budget is spent on actually-queueable rows.
    Without this, plan_assignments's id-ASC scan of the oldest 50 PENDINGs
    found them all ungrouped, filtered them all out in Python, and
    returned 0 — the 2026-06-12→15 silent-stall incident.

    in_groups=[...] further restricts to videos in those groups. Used by
    plan_assignments to skip groups that have no platform-eligible account
    this round — without it, a group full of fb publications with no
    fb-logged-in account permanently poisons the id-ASC LIMIT window for
    later groups that could actually post (the 2026-06-16 fb/yt stall).
    in_groups=[] returns nothing, since no group is eligible.
    """
    stmt = (
        select(Publication, Video.stored_path, Video.original_filename,
               Video.group_id)
        .join(Video, Video.id == Publication.video_id)
        .where(Publication.result == "PENDING")
        .where(Publication.platform == platform)
    )
    if require_group:
        stmt = (
            stmt.join(Group, Group.id == Video.group_id)
            .where(Group.caption_prompt_id.is_not(None))
        )
    if in_groups is not None:
        if not in_groups:
            return []
        stmt = stmt.where(Video.group_id.in_(in_groups))
    stmt = stmt.order_by(Publication.id)
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    out = []
    for pub, stored_path, filename, group_id in session.exec(stmt).all():
        d = pub.model_dump()
        d["stored_path"] = stored_path
        d["original_filename"] = filename
        d["group_id"] = group_id
        out.append(d)
    return out


def list_initiated(session: Session) -> list[dict]:
    """INITIATED publications joined to Video for stored_path."""
    stmt = (
        select(Publication, Video.stored_path)
        .join(Video, Video.id == Publication.video_id)
        .where(Publication.result == "INITIATED")
        .where(Publication.geelark_task_id != None)  # noqa: E711
        .order_by(Publication.id)
    )
    out = []
    for pub, stored_path in session.exec(stmt).all():
        d = pub.model_dump()
        d["stored_path"] = stored_path
        out.append(d)
    return out


def list_completed_for_account(session: Session, account_id: int,
                               limit: int = 100) -> list[dict]:
    """Completed post records for one account.

    Only terminal posting outcomes count as completed here. Pending and
    in-flight rows are intentionally excluded so the account drawer stays
    focused on post history, not scheduler state.
    """
    stmt = (
        select(Publication, Video.original_filename, Video.stored_path,
               Video.uploaded_at, Video.group_id)
        .join(Video, Video.id == Publication.video_id)
        .where(Publication.account_id == account_id)
        .where(Publication.result.in_(("SUCCESS", "FAILURE")))
        .order_by(
            sa.func.coalesce(
                Publication.posted_at,
                Publication.last_checked_at,
                Publication.created_at,
            ).desc(),
            Publication.id.desc(),
        )
        .limit(max(0, limit))
    )
    out = []
    for pub, filename, stored_path, uploaded_at, group_id in session.exec(stmt).all():
        d = pub.model_dump()
        d["original_filename"] = filename
        d["stored_path"] = stored_path
        d["uploaded_at"] = uploaded_at
        d["group_id"] = group_id
        out.append(d)
    return out


def list_comment_targets(session: Session, *, group_id: int | None = None,
                         platform: str = "tiktok",
                         limit: int = 100) -> list[dict]:
    """Successful publications on `platform` that have a captured public URL.

    These are the owned-video targets the Comments page can submit to
    GeeLark's per-platform comment RPA. Manual links still flow through
    engagement_service directly; this list is only for first-party posts.
    """
    from app.services.platforms import validate as validate_platform
    platform = validate_platform(platform)
    stmt = (
        select(Publication, Video.original_filename, Video.group_id,
               Account.profile_name)
        .join(Video, Video.id == Publication.video_id)
        .outerjoin(Account, Account.id == Publication.account_id)
        .where(Publication.platform == platform)
        .where(Publication.result == "SUCCESS")
        .where(Publication.share_link.is_not(None))
        .where(sa.func.btrim(Publication.share_link) != "")
        .order_by(
            sa.func.coalesce(
                Publication.posted_at,
                Publication.last_checked_at,
                Publication.created_at,
            ).desc(),
            Publication.id.desc(),
        )
        .limit(max(1, min(int(limit), 200)))
    )
    if group_id is not None:
        stmt = stmt.where(Video.group_id == group_id)

    out = []
    for pub, filename, video_group_id, profile_name in session.exec(stmt).all():
        out.append({
            "publication_id": pub.id,
            "video_id": pub.video_id,
            "video_filename": filename,
            "group_id": video_group_id,
            "account_id": pub.account_id,
            "account_profile_name": profile_name,
            "share_link": pub.share_link,
            "posted_at": pub.posted_at,
            "share_link_captured_at": pub.share_link_captured_at,
        })
    return out


def set_account(session: Session, pub_id: int,
                account_id: Optional[int]) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.account_id = account_id
    session.add(pub)
    session.commit()


def set_share_link(session: Session, pub_id: int, share_link: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.share_link = share_link
    pub.share_link_captured_at = datetime.utcnow()
    session.add(pub)
    session.commit()


def account_has_inflight(session: Session, account_id: int, *,
                         exclude_publication_id: Optional[int] = None,
                         results: tuple[str, ...] = ("QUEUED", "INITIATED"),
                         ) -> bool:
    stmt = (
        select(Publication.id)
        .where(Publication.account_id == account_id)
        .where(Publication.result.in_(results))
    )
    if exclude_publication_id is not None:
        stmt = stmt.where(Publication.id != exclude_publication_id)
    return session.exec(stmt).first() is not None


def mark_queued(session: Session, pub_id: int) -> None:
    """Move PENDING → QUEUED. No-op on any other state so a stale defer
    can't pull an INITIATED/SUCCESS/FAILURE row back into the publish
    pipeline (see pub 6180 incident 2026-06-09)."""
    pub = session.get(Publication, pub_id)
    if pub is None or pub.result != "PENDING":
        return
    if pub.account_id is None:
        video = session.get(Video, pub.video_id)
        if video is not None and video.account_id is not None:
            pub.account_id = video.account_id
    pub.result = "QUEUED"
    pub.next_retry_at = None
    # Fresh submit cycle — never carry a task id (or 'pending'
    # placeholder) over from a previous attempt into the gate.
    pub.geelark_task_id = None
    # Claim timestamp — the QUEUED-orphan reaper uses this to spot rows
    # whose defer never happened (or whose job died before submitting).
    pub.last_checked_at = datetime.utcnow()
    session.add(pub)
    session.commit()


def set_task_placeholder(session: Session, pub_id: int) -> None:
    """Stamp geelark_task_id='pending' on a QUEUED row right before the
    GeeLark create call. mark_initiated overwrites it with the real task
    id; the submit gate refuses QUEUED rows still carrying it (a crash
    left the GeeLark-side outcome unknown — re-running would risk a
    duplicate post)."""
    session.execute(
        text(
            "UPDATE publications SET geelark_task_id = 'pending' "
            "WHERE id = :id AND result = 'QUEUED'"
        ),
        {"id": pub_id},
    )
    session.commit()


def mark_initiated(session: Session, pub_id: int,
                   caption: str, task_id: str, notes: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.result = "INITIATED"
    pub.caption = caption
    pub.geelark_task_id = task_id
    pub.notes = notes
    pub.posted_at = datetime.utcnow()
    pub.next_retry_at = None
    pub.fail_code = None
    pub.fail_screenshot = None
    session.add(pub)
    session.commit()


def set_caption(session: Session, pub_id: int, caption: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.caption = caption
    session.add(pub)
    session.commit()


def mark_failure(session: Session, pub_id: int, notes: str) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.result = "FAILURE"
    pub.notes = notes
    pub.posted_at = datetime.utcnow()
    session.add(pub)
    session.commit()


def schedule_retry(session: Session, pub_id: int, *,
                   notes: str, next_retry_at: datetime,
                   fail_code: Optional[int] = None,
                   fail_screenshot: Optional[str] = None) -> bool:
    """FAILURE → PENDING with an atomic retry_count bump.

    State-guarded in SQL: concurrent resolvers (poll chain, callback
    chain, scan-side check) can all reach their FAILURE verdict for the
    same publication within one poll tick and each call this. A blind
    write would double-increment retry_count (burning retry budget —
    fatal with the default single-step delay table) or drag a row the
    scan already re-QUEUED back to PENDING and line up a duplicate
    submit. Exactly one caller wins; the rest see False.
    """
    result = session.execute(
        text(
            "UPDATE publications "
            "SET result = 'PENDING', "
            "    retry_count = COALESCE(retry_count, 0) + 1, "
            "    next_retry_at = :next_retry_at, "
            "    notes = :notes, "
            "    fail_code = :fail_code, "
            "    fail_screenshot = :fail_screenshot, "
            "    last_checked_at = :now, "
            # A retry is a fresh submit cycle — drop any stale task id or
            # 'pending' placeholder from the failed attempt so the gate
            # doesn't mistake the new cycle for an interrupted one.
            "    geelark_task_id = NULL "
            "WHERE id = :id AND result = 'FAILURE'"
        ),
        {
            "id": pub_id,
            "next_retry_at": next_retry_at,
            "notes": notes,
            "fail_code": fail_code,
            "fail_screenshot": fail_screenshot,
            "now": datetime.utcnow(),
        },
    )
    session.commit()
    return result.rowcount > 0


def reset_failed_for_retry(session: Session, pub_id: int) -> bool:
    pub = session.get(Publication, pub_id)
    if pub is None or pub.result != "FAILURE":
        return False
    pub.result = "PENDING"
    pub.retry_count = 0
    pub.next_retry_at = None
    pub.fail_code = None
    pub.fail_screenshot = None
    pub.last_checked_at = datetime.utcnow()
    session.add(pub)
    session.commit()
    return True


def set_result(session: Session, pub_id: int, result: str,
               notes: str, *, fail_code: Optional[int] = None,
               fail_screenshot: Optional[str] = None) -> None:
    pub = session.get(Publication, pub_id)
    if pub is None:
        return
    pub.result = result
    pub.notes = notes
    if result == "SUCCESS":
        pub.fail_code = None
        pub.fail_screenshot = None
        pub.next_retry_at = None
    elif result == "FAILURE":
        pub.fail_code = fail_code
        pub.fail_screenshot = fail_screenshot
    pub.last_checked_at = datetime.utcnow()
    session.add(pub)
    session.commit()


def video_ids_all_terminal(session: Session) -> set[int]:
    """Video IDs where at least one publication exists and none are
    PENDING/QUEUED/INITIATED. Used to gate local stored_path cleanup."""
    rows = session.exec(sa.text("""
        SELECT v.id FROM videos v
        WHERE v.stored_path IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM publications p WHERE p.video_id = v.id
          )
          AND NOT EXISTS (
            SELECT 1 FROM publications p
            WHERE p.video_id = v.id
              AND p.result IN ('PENDING','QUEUED','INITIATED')
          )
    """)).all()
    return {r[0] for r in rows}


def video_ids_all_success(session: Session) -> set[int]:
    """Video IDs where every publication is SUCCESS.

    Only these videos are safe to remove from local storage. FAILURE rows
    need their source file kept for diagnostics and retry.
    """
    rows = session.exec(sa.text("""
        SELECT v.id FROM videos v
        WHERE v.stored_path IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM publications p WHERE p.video_id = v.id
          )
          AND NOT EXISTS (
            SELECT 1 FROM publications p
            WHERE p.video_id = v.id
              AND p.result != 'SUCCESS'
          )
    """)).all()
    return {r[0] for r in rows}


def list_unassigned_pending_video_ids(session: Session) -> list[int]:
    """Video IDs that have at least one PENDING publication and no
    account assigned yet. Drives the auto-publish scheduler."""
    rows = session.exec(sa.text("""
        SELECT DISTINCT v.id FROM videos v
        JOIN publications p ON p.video_id = v.id
        WHERE p.account_id IS NULL
          AND p.result = 'PENDING'
        ORDER BY v.id
    """)).all()
    return [r[0] for r in rows]
