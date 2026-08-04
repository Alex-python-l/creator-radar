"""Video repository — material metadata only.
Publishing state lives in publications (see app/repositories/publications.py)."""
import json
from collections import defaultdict
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from app.models import Account, Publication, Video
from app.repositories import publications as pub_repo


def _group_filter_sql(group_id: int | str) -> tuple[str, dict]:
    """Returns (sql_fragment, params) for the WHERE clause."""
    if group_id == "all":
        return "TRUE", {}
    if group_id == "ungrouped":
        return "v.group_id IS NULL", {}
    return "v.group_id = :gid", {"gid": int(group_id)}


def _status_having_sql(status: str) -> str:
    """Returns the HAVING fragment (or 'TRUE') for the 3-bucket model.

    Supported buckets (mutually exclusive, exhaustive over all videos):

    - ``all``         — no filter
    - ``pending``     — no publications OR every publication is PENDING
    - ``in_progress`` — ≥1 publication is INITIATED or QUEUED
    - ``handled``     — no in-flight AND ≥1 publication is SUCCESS or FAILURE
    """
    if status == "all":
        return "TRUE"
    if status == "pending":
        return ("COUNT(p.id) = 0 OR ("
                "SUM(CASE WHEN p.result IN ('INITIATED','QUEUED') "
                "THEN 1 ELSE 0 END) = 0 AND "
                "SUM(CASE WHEN p.result = 'SUCCESS' THEN 1 ELSE 0 END) = 0 AND "
                "SUM(CASE WHEN p.result = 'FAILURE' THEN 1 ELSE 0 END) = 0)")
    if status == "in_progress":
        return ("COUNT(p.id) > 0 AND "
                "SUM(CASE WHEN p.result IN ('INITIATED','QUEUED') "
                "THEN 1 ELSE 0 END) > 0")
    if status == "handled":
        return ("COUNT(p.id) > 0 AND "
                "SUM(CASE WHEN p.result IN ('INITIATED','QUEUED') "
                "THEN 1 ELSE 0 END) = 0 AND "
                "(SUM(CASE WHEN p.result = 'SUCCESS' THEN 1 ELSE 0 END) > 0 "
                " OR SUM(CASE WHEN p.result = 'FAILURE' THEN 1 ELSE 0 END) > 0)")
    raise ValueError(f"unknown status: {status!r}")


def create(session: Session, filename: str, stored_path: str,
           drive_file_id: Optional[str] = None,
           group_id: Optional[int] = None) -> int:
    v = Video(
        original_filename=filename,
        stored_path=stored_path,
        drive_file_id=drive_file_id,
        group_id=group_id,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return v.id


def bulk_set_group(session: Session, ids: list[int],
                   group_id: Optional[int]) -> int:
    if not ids:
        return 0
    rows = session.exec(
        select(Video).where(Video.id.in_(ids))).all()
    for v in rows:
        v.group_id = group_id
        session.add(v)
    session.commit()
    return len(rows)


def existing_drive_file_ids(session: Session) -> set[str]:
    rows = session.exec(
        select(Video.drive_file_id)
        .where(Video.drive_file_id.is_not(None))
    ).all()
    return {r for r in rows if r}


def get(session: Session, vid: int) -> Optional[dict]:
    v = session.get(Video, vid)
    return v.model_dump() if v else None


def list_all(session: Session) -> list[dict]:
    """Each video carries an embedded `publications` array and the
    profile_name of its assigned account (which GeeLark phone).

    Implemented as two queries (videos+video-owner, then publications+
    publication-owner) so the per-row cost stays flat as the library
    grows — the previous per-video fetch made this O(N) round-trips.
    """
    video_owner = aliased(Account)
    video_rows = session.exec(
        select(Video, video_owner.profile_name)
        .outerjoin(video_owner, video_owner.id == Video.account_id)
        .order_by(Video.id.desc())
    ).all()

    pub_owner = aliased(Account)
    pub_rows = session.exec(
        select(Publication, pub_owner.profile_name)
        .outerjoin(pub_owner, pub_owner.id == Publication.account_id)
        .order_by(Publication.video_id, Publication.platform)
    ).all()

    pubs_by_video: dict[int, list[dict]] = defaultdict(list)
    for pub, profile_name in pub_rows:
        d = pub.model_dump()
        d["account_profile_name"] = profile_name
        pubs_by_video[pub.video_id].append(d)

    out = []
    for v, profile_name in video_rows:
        d = v.model_dump()
        d["account_profile_name"] = profile_name
        d["publications"] = pubs_by_video.get(v.id, [])
        out.append(d)
    return out


def get_detail(session: Session, vid: int) -> Optional[dict]:
    """Single-video full detail: video metadata + complete publications
    array (with account_profile_name joined). Returns None when the
    video doesn't exist. Shape mirrors a single row of the old list_all
    so existing frontend display code can reuse helpers."""
    # Lazy import to avoid circular dependency:
    # posting_service imports this repository at module load time.
    from app.services.posting_service import format_failure_hint

    video_owner = aliased(Account)
    row = session.exec(
        select(Video, video_owner.profile_name)
        .outerjoin(video_owner, video_owner.id == Video.account_id)
        .where(Video.id == vid)
    ).first()
    if row is None:
        return None
    v, profile_name = row
    pub_owner = aliased(Account)
    pub_rows = session.exec(
        select(Publication, pub_owner.profile_name)
        .outerjoin(pub_owner, pub_owner.id == Publication.account_id)
        .where(Publication.video_id == vid)
        .order_by(Publication.platform)
    ).all()
    pubs = []
    for pub, pn in pub_rows:
        d = pub.model_dump()
        d["account_profile_name"] = pn
        d["fail_hint"] = format_failure_hint(d.get("fail_code"))
        pubs.append(d)
    out = v.model_dump()
    out["account_profile_name"] = profile_name
    out["publications"] = pubs
    return out


def set_account(session: Session, vid: int,
                account_id: Optional[int]) -> None:
    """Legacy bulk assignment helper.

    New publishing code assigns accounts per publication. This helper is
    kept for the old row-level API and applies the selected phone to all
    pending publications for the video.
    """
    v = session.get(Video, vid)
    if v is None:
        return
    v.account_id = account_id
    session.add(v)
    from app.models import Publication
    mutable = session.exec(
        select(Publication)
        .where(Publication.video_id == vid)
        .where(Publication.result.in_(("PENDING", "QUEUED")))
    ).all()
    for p in mutable:
        p.account_id = account_id
        if account_id is None and p.result == "QUEUED":
            p.result = "PENDING"
        session.add(p)
    session.commit()


def clear_stored_path(session: Session, vid: int) -> None:
    v = session.get(Video, vid)
    if v is None:
        return
    v.stored_path = None
    session.add(v)
    session.commit()


def _json_or_null(facts: dict | None) -> str | None:
    return None if facts is None else json.dumps(facts)


def set_understanding(session: Session, video_id: int, *,
                      facts: dict | None,
                      status: str) -> None:
    """Persist Layer 1 output on the videos row.

    status: 'pending' | 'ok' | 'failed'. When status='failed', pass
    facts=None — the understanding column is cleared (set to NULL) and
    the status reflects the latest attempt. (Caller may also pass
    facts=None for 'ok' if the schema was relaxed; right now Phase 1
    always pairs facts with 'ok'.)
    """
    session.execute(
        text("""UPDATE videos
                SET understanding        = CAST(:facts AS JSONB),
                    understanding_status = :status
                WHERE id = :vid"""),
        {"facts": _json_or_null(facts), "status": status, "vid": video_id},
    )
    session.commit()


def update_media_meta(session: Session, vid: int, *,
                     thumbnail_path: Optional[str],
                     duration_seconds: Optional[int]) -> None:
    """Persist ffmpeg-derived metadata. Safe to call with both None
    (no-op write — keeps existing values). Designed to be called once
    per video after upload completes."""
    v = session.get(Video, vid)
    if v is None:
        return
    if thumbnail_path is not None:
        v.thumbnail_path = thumbnail_path
    if duration_seconds is not None:
        v.duration_seconds = duration_seconds
    session.add(v)
    session.commit()


def delete(session: Session, vid: int) -> None:
    v = session.get(Video, vid)
    if v is None:
        return
    session.delete(v)  # publications cascade via FK ON DELETE CASCADE
    session.commit()


def count_filtered(session: Session, *, group_id: int | str, status: str) -> int:
    gfrag, gparams = _group_filter_sql(group_id)
    hfrag = _status_having_sql(status)
    sql = f"""
        SELECT COUNT(*) FROM (
          SELECT v.id
          FROM videos v
          LEFT JOIN publications p ON p.video_id = v.id
          WHERE {gfrag}
          GROUP BY v.id
          HAVING {hfrag}
        ) sub
    """
    row = session.execute(sa.text(sql), gparams).first()
    return int(row[0]) if row else 0


def summary_counts_for(
    session: Session, *, group_id: int | str,
) -> dict:
    """Counts for the page-level summary line — three mutually exclusive
    buckets that always sum to ``total``.

    total       – number of videos in the current group
    pending     – no publications OR every publication is PENDING
    in_progress – ≥1 publication is INITIATED or QUEUED
    handled     – no in-flight AND ≥1 SUCCESS or FAILURE
    """
    gfrag, gparams = _group_filter_sql(group_id)
    sql = f"""
        WITH v_pub AS (
          SELECT v.id,
                 COUNT(p.id) AS n,
                 SUM(CASE WHEN p.result = 'SUCCESS'
                          THEN 1 ELSE 0 END) AS ok,
                 SUM(CASE WHEN p.result = 'FAILURE'
                          THEN 1 ELSE 0 END) AS fail,
                 SUM(CASE WHEN p.result IN ('INITIATED','QUEUED')
                          THEN 1 ELSE 0 END) AS in_flight
            FROM videos v
            LEFT JOIN publications p ON p.video_id = v.id
            WHERE {gfrag}
            GROUP BY v.id
        )
        SELECT
          COUNT(*)                                                              AS total,
          SUM(CASE WHEN n = 0 OR (in_flight = 0 AND ok = 0 AND fail = 0)
                   THEN 1 ELSE 0 END)                                           AS pending,
          SUM(CASE WHEN n > 0 AND in_flight > 0
                   THEN 1 ELSE 0 END)                                           AS in_progress,
          SUM(CASE WHEN n > 0 AND in_flight = 0 AND (ok > 0 OR fail > 0)
                   THEN 1 ELSE 0 END)                                           AS handled
        FROM v_pub
    """
    row = session.execute(sa.text(sql), gparams).first()
    if row is None:
        return {"total": 0, "pending": 0, "in_progress": 0, "handled": 0}
    return {
        "total":       int(row[0] or 0),
        "pending":     int(row[1] or 0),
        "in_progress": int(row[2] or 0),
        "handled":     int(row[3] or 0),
    }


# `list_page` returns `phone_label`, while `list_all` / `get_detail` keep
# the legacy `account_profile_name` field. Same underlying column
# (accounts.profile_name); the compact list uses the friendlier name.
def list_page(
    session: Session,
    *,
    group_id: int | str,
    status: str,
    cursor: Optional[int | str],
    limit: int,
) -> tuple[list[dict], Optional[int | str]]:
    """Cursor-paginated list rows shaped for the videos page UI.

    Args:
      cursor: opaque pagination token from the previous page, or None
        for the first page. Non-handled tabs sort by `videos.id DESC`
        and use the last row's id as the cursor (int). The handled tab
        sorts by the latest per-video terminal timestamp (newest first)
        with id as tiebreaker, and encodes the cursor as
        ``"<iso-ts>|<id>"``.
      limit: target page size.

    Returns:
      (items, next_cursor). `next_cursor` is the pagination token to
      pass into the next call, or None when there are no more pages.
      Internally fetches `limit + 1` rows to detect the no-more case
      without a follow-up COUNT.
    """
    gfrag, gparams = _group_filter_sql(group_id)
    hfrag = _status_having_sql(status)
    params: dict = dict(gparams)
    params["limit_plus_one"] = limit + 1

    is_handled = status == "handled"
    # Per-video "when was this finished" timestamp — last_checked_at is
    # set when SUCCESS/FAILURE is reconciled; posted_at is set when the
    # job is first submitted (and on direct mark_failure). COALESCE
    # prefers the more specific terminal timestamp.
    sort_expr = "MAX(COALESCE(p.last_checked_at, p.posted_at))"

    cursor_frag = "TRUE"
    cursor_having = "TRUE"
    if is_handled:
        order_by = f"{sort_expr} DESC NULLS LAST, v.id DESC"
        if cursor is not None:
            from datetime import datetime
            ts_str, sep, id_str = str(cursor).partition("|")
            if not sep:
                raise ValueError(
                    f"handled cursor must be '<iso-ts>|<id>': {cursor!r}")
            params["cursor_ts"] = datetime.fromisoformat(ts_str)
            params["cursor_id"] = int(id_str)
            cursor_having = (
                f"({sort_expr} < :cursor_ts "
                f"OR ({sort_expr} = :cursor_ts "
                f"AND v.id < :cursor_id))"
            )
    else:
        order_by = "v.id DESC"
        if cursor is not None:
            cursor_frag = "v.id < :cursor"
            params["cursor"] = int(cursor)

    sql = f"""
        SELECT
          v.id,
          v.original_filename,
          v.uploaded_at,
          v.thumbnail_path,
          v.duration_seconds,
          v.group_id,
          owner.profile_name AS phone_label,
          COALESCE(
            jsonb_object_agg(
              p.platform,
              jsonb_build_object(
                'result', p.result,
                'has_phone', p.account_id IS NOT NULL,
                'phone_label', pub_owner.profile_name
              )
            ) FILTER (WHERE p.platform IS NOT NULL),
            '{{}}'::jsonb
          ) AS platforms,
          -- Pick any non-empty caption: MAX is deterministic but arbitrary
          -- across platforms (captions usually agree anyway).
          LEFT(MAX(NULLIF(p.caption, '')), 80) AS caption_preview,
          CASE
            WHEN COUNT(p.id) = 0 THEN 'PENDING'
            WHEN BOOL_OR(p.result NOT IN ('SUCCESS','FAILURE')) THEN 'PENDING'
            ELSE 'TERMINAL'
          END AS summary,
          {sort_expr} AS sort_ts
        FROM videos v
        LEFT JOIN accounts owner ON owner.id = v.account_id
        LEFT JOIN publications p ON p.video_id = v.id
        LEFT JOIN accounts pub_owner ON pub_owner.id = p.account_id
        WHERE {cursor_frag} AND {gfrag}
        GROUP BY v.id, owner.profile_name
        HAVING {hfrag} AND {cursor_having}
        ORDER BY {order_by}
        LIMIT :limit_plus_one
    """
    rows = session.execute(sa.text(sql), params).mappings().all()
    next_cursor: Optional[int | str] = None
    if len(rows) > limit:
        last_row = rows[limit - 1]
        if is_handled:
            ts = last_row["sort_ts"]
            next_cursor = f"{ts.isoformat()}|{int(last_row['id'])}"
        else:
            next_cursor = int(last_row["id"])
        rows = rows[:limit]
    items: list[dict] = []
    for r in rows:
        items.append({
            "id": int(r["id"]),
            "original_filename": r["original_filename"],
            "uploaded_at": r["uploaded_at"].isoformat()
                            if r["uploaded_at"] is not None else None,
            "group_id": r["group_id"],
            "phone_label": r["phone_label"],
            "thumbnail_path": r["thumbnail_path"],
            "duration_seconds": r["duration_seconds"],
            "platforms": dict(r["platforms"] or {}),
            "caption_preview": r["caption_preview"],
            "summary": r["summary"],
        })
    return items, next_cursor


def count_for_group(session: Session, group_id: int) -> dict:
    """Mutually-exclusive {published, pending, failed} for every video
    in `group_id`. Each video gets exactly one status by priority:
      SUCCESS publication       -> published
      any in-flight publication -> pending
      only FAILURE publications -> failed
      no publications at all    -> pending
    The three counts sum to len(group.videos)."""
    row = session.execute(sa.text("""
        WITH per_video AS (
          SELECT
            v.id,
            COALESCE(BOOL_OR(p.result = 'SUCCESS'), FALSE)                        AS has_success,
            COALESCE(BOOL_OR(p.result IN ('PENDING','QUEUED','INITIATED')), FALSE) AS has_inflight,
            COALESCE(BOOL_OR(p.result = 'FAILURE'), FALSE)                        AS has_failure,
            COUNT(p.id) = 0                                                       AS no_pubs
          FROM videos v
          LEFT JOIN publications p ON p.video_id = v.id
          WHERE v.group_id = :gid
          GROUP BY v.id
        )
        SELECT
          COUNT(*) FILTER (WHERE has_success)                                   AS published,
          COUNT(*) FILTER (WHERE NOT has_success
                              AND (has_inflight OR no_pubs))                    AS pending,
          COUNT(*) FILTER (WHERE NOT has_success
                              AND NOT has_inflight
                              AND has_failure)                                  AS failed
        FROM per_video
    """), {"gid": group_id}).first()
    if row is None:
        return {"published": 0, "pending": 0, "failed": 0}
    return {"published": row[0] or 0,
            "pending":   row[1] or 0,
            "failed":    row[2] or 0}
