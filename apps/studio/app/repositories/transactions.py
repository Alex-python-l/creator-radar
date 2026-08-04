"""Repository for the mirrored GeeLark billing transactions.

Each row in `geelark_transactions` reflects one cloud-phone start→stop
billing event. The attribution logic ties a transaction back to the
business task (publish / warmup / engagement) it paid for, by joining
on the cloud phone id (`env_id`) and a time window around the
transaction's stop moment.

Known limitation: `accounts.last_warm_at` only stores the *most recent*
warmup completion, so older warmup-driven transactions fall into the
`idle` bucket. This is intentional: the audit page should not present a
guess as a real warmup cost. A future `warmup_runs` table would let us
reattribute historical warmups precisely.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session


# Tolerance applied to both ends of the [start, end] window when joining
# back to business rows. Compensates for clock drift between our DB
# writes and GeeLark's bill-out timestamp.
WINDOW_PAD_SECONDS = 30


def _public_kind(kind: Optional[str]) -> str:
    """Map persisted attribution names to the API's historical kind set."""
    if kind in (None, "unattributed"):
        return "idle"
    return kind


def _public_kind_sql(alias: str = "ca") -> str:
    return (
        f"CASE WHEN {alias}.kind = 'unattributed' "
        f"THEN 'idle' ELSE {alias}.kind END"
    )


def upsert_many(session: Session, rows: list[dict]) -> int:
    """Insert each GeeLark transaction row, skipping duplicates.

    Returns the number of rows that were already in the database (i.e.
    the conflict count). The sync loop uses `>0` to stop paginating.
    """
    if not rows:
        return 0

    ids = [str(r["id"]) for r in rows]
    existing_ids = set(
        session.execute(
            text(
                "SELECT id FROM geelark_transactions "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        ).scalars().all()
    )

    payload = []
    for r in rows:
        payload.append({
            "id": str(r["id"]),
            "env_id": str(r["envId"]),
            "amount": float(r["amount"]),
            "used_time_min": int(r["usedTime"]),
            "type": int(r["type"]),
            "charge_type": int(r["chargeType"]),
            "geelark_created_at": datetime.utcfromtimestamp(int(r["createdTime"])),
        })

    session.execute(
        text(
            "INSERT INTO geelark_transactions "
            "(id, env_id, amount, used_time_min, type, charge_type, "
            "geelark_created_at) "
            "VALUES (:id, :env_id, :amount, :used_time_min, :type, "
            ":charge_type, :geelark_created_at) "
            "ON CONFLICT (workspace_id, id) DO NOTHING"
        ),
        payload,
    )
    session.commit()
    return sum(1 for tx_id in ids if tx_id in existing_ids)


# Tagged subquery shape used inside the lateral attribution lookup. Each
# branch must SELECT (kind, label, ref_type, ref_id, diff_s) in that order.
_ATTRIBUTION_SQL = """
WITH base AS (
    SELECT t.*,
           a.id          AS account_id,
           a.profile_name,
           a.group_id,
           g.name        AS group_name
    FROM geelark_transactions t
    LEFT JOIN accounts a ON a.geelark_phone_id = t.env_id
    LEFT JOIN groups g  ON g.id = a.group_id
    WHERE (CAST(:account_id AS integer) IS NULL
           OR a.id = CAST(:account_id AS integer))
      AND (CAST(:group_id   AS integer) IS NULL
           OR a.group_id = CAST(:group_id AS integer))
      AND (CAST(:date_from  AS timestamp) IS NULL
           OR t.geelark_created_at >= CAST(:date_from AS timestamp))
      AND (CAST(:date_to    AS timestamp) IS NULL
           OR t.geelark_created_at <  CAST(:date_to   AS timestamp))
), windowed AS (
    SELECT
        b.*,
        b.geelark_created_at
            - make_interval(secs => (b.used_time_min + 1) * 60 + :pad)
            AS win_start,
        b.geelark_created_at + make_interval(secs => :pad)
            AS win_end
    FROM base b
), attributed AS (
    SELECT w.*, attr.kind, attr.label, attr.ref_type, attr.ref_id,
           attr.confidence, attr.reason,
           attr.phone_session_id, attr.task_run_id
    FROM windowed w
    LEFT JOIN LATERAL (
        SELECT * FROM (
            -- 0. Phone session match (Phase 3, highest priority).
            -- The phone_sessions ledger directly records "this phone
            -- was on, for this reason, at this time" — wins over any
            -- inferred-from-business-tables signal via the priority
            -- column below.
            SELECT
                0 AS priority,
                ps.source::text AS kind,
                'phone_session #' || ps.id
                    || ' (' || ps.source || ')' AS label,
                COALESCE(ps.owner_type, 'phone_session')::text AS ref_type,
                COALESCE(ps.owner_id, ps.id)::bigint AS ref_id,
                abs(extract(epoch from
                  (ps.started_at - w.geelark_created_at))) AS diff_s,
                'high'::text AS confidence,
                ('phone_session #' || ps.id
                    || ' overlap (source=' || ps.source || ')')::text
                    AS reason,
                ps.id AS phone_session_id,
                tr.id AS task_run_id
            FROM phone_sessions ps
            LEFT JOIN task_runs tr ON tr.phone_session_id = ps.id
            WHERE ps.env_id = w.env_id
              AND ps.started_at <= w.win_end
              AND (ps.stopped_at IS NULL
                   OR ps.stopped_at >= w.win_start)

            UNION ALL
            -- 1. Publish via posted_at / last_checked_at / attempts
            -- (legacy heuristic for transactions older than Phase 1).
            SELECT
                1 AS priority,
                'publish'::text  AS kind,
                'Publish video #' || p.video_id
                    || ' → ' || p.platform AS label,
                'publication'::text AS ref_type,
                p.id AS ref_id,
                abs(extract(epoch from (
                    COALESCE(p.posted_at, p.last_checked_at, pa.created_at)
                    - w.geelark_created_at
                ))) AS diff_s,
                CASE
                    WHEN p.posted_at BETWEEN w.win_start AND w.win_end
                        THEN 'high'
                    WHEN p.last_checked_at BETWEEN w.win_start AND w.win_end
                        THEN 'medium'
                    WHEN pa.created_at IS NOT NULL
                        THEN 'medium'
                    ELSE 'low'
                END::text AS confidence,
                CASE
                    WHEN p.posted_at BETWEEN w.win_start AND w.win_end
                        THEN 'posted_at in tx window'
                    WHEN p.last_checked_at BETWEEN w.win_start AND w.win_end
                        THEN 'last_checked_at in tx window'
                    WHEN pa.created_at IS NOT NULL
                        THEN 'publication_attempts.' || pa.stage
                             || ' in tx window'
                    ELSE NULL
                END::text AS reason,
                NULL::bigint AS phone_session_id,
                NULL::bigint AS task_run_id
            FROM publications p
            LEFT JOIN LATERAL (
                SELECT created_at, stage
                FROM publication_attempts
                WHERE publication_id = p.id
                  AND created_at BETWEEN w.win_start AND w.win_end
                  AND stage IN ('submit', 'status_check')
                ORDER BY created_at DESC
                LIMIT 1
            ) pa ON TRUE
            WHERE p.account_id = w.account_id
              AND (
                p.posted_at BETWEEN w.win_start AND w.win_end
                OR p.last_checked_at BETWEEN w.win_start AND w.win_end
                OR pa.created_at IS NOT NULL
              )

            UNION ALL
            SELECT
                1 AS priority,
                'engagement'::text, 'Engagement run #' || er.id,
                'engagement_run'::text, er.id,
                abs(extract(epoch from (er.finished_at - w.geelark_created_at))),
                'high'::text,
                'engagement_run.finished_at in tx window'::text,
                NULL::bigint, NULL::bigint
            FROM engagement_runs er
            WHERE er.account_id = w.account_id
              AND er.finished_at BETWEEN w.win_start AND w.win_end

            UNION ALL
            SELECT
                1 AS priority,
                'warmup'::text, 'Warmup'::text,
                'account'::text, acc.id,
                abs(extract(epoch from (acc.last_warm_at - w.geelark_created_at))),
                'high'::text,
                'account.last_warm_at in tx window'::text,
                NULL::bigint, NULL::bigint
            FROM accounts acc
            WHERE acc.id = w.account_id
              AND acc.last_warm_at BETWEEN w.win_start AND w.win_end

        ) candidates
        ORDER BY priority ASC, diff_s ASC
        LIMIT 1
    ) attr ON TRUE
)
SELECT * FROM attributed
"""


def _row(r) -> dict:
    kind = r.kind or "idle"
    label = r.label or "Unattributed"
    confidence = getattr(r, "confidence", None)
    reason = getattr(r, "reason", None)
    if kind == "idle":
        # No LATERAL match — record the absence in the same shape as
        # matched rows so the UI can render it without branching.
        confidence = "low"
        reason = "no publish/warmup/engagement timestamp matched tx window"
    return {
        "id": r.id,
        "time": r.geelark_created_at.isoformat() + "Z",
        "amount_usd": float(r.amount),
        "used_min": r.used_time_min,
        "env_id": r.env_id,
        "account": (
            {"id": r.account_id, "profile_name": r.profile_name}
            if r.account_id is not None else None
        ),
        "group": (
            {"id": r.group_id, "name": r.group_name}
            if r.group_id is not None else None
        ),
        "attribution": {
            "kind": kind,
            "label": label,
            "ref_type": r.ref_type,
            "ref_id": r.ref_id,
            "confidence": confidence,
            "reason": reason,
            "phone_session_id": getattr(r, "phone_session_id", None),
            "task_run_id": getattr(r, "task_run_id", None),
        },
    }


def _ledger_label(kind: str, r) -> str:
    if kind == "idle":
        return "Unattributed"
    if getattr(r, "task_run_id", None) is not None:
        ref_type = getattr(r, "task_ref_type", None) or kind
        ref_id = getattr(r, "task_ref_id", None) or r.task_run_id
        return f"{kind.title()} {ref_type} #{ref_id}"
    if getattr(r, "phone_session_id", None) is not None:
        return f"phone_session #{r.phone_session_id} ({kind})"
    return {
        "publish": "Publish",
        "warmup": "Warmup",
        "engagement": "Engagement",
    }.get(kind, kind.replace("_", " ").title())


def _ledger_row(r) -> dict:
    kind = _public_kind(r.kind)
    ref_type = getattr(r, "task_ref_type", None) or getattr(
        r, "owner_type", None
    )
    ref_id = getattr(r, "task_ref_id", None) or getattr(r, "owner_id", None)
    return {
        "id": r.id,
        "time": r.geelark_created_at.isoformat() + "Z",
        "amount_usd": float(r.amount),
        "used_min": r.used_time_min,
        "env_id": r.env_id,
        "account": (
            {"id": r.account_id, "profile_name": r.profile_name}
            if r.account_id is not None else None
        ),
        "group": (
            {"id": r.group_id, "name": r.group_name}
            if r.group_id is not None else None
        ),
        "attribution": {
            "kind": kind,
            "label": _ledger_label(kind, r),
            "ref_type": ref_type,
            "ref_id": ref_id,
            "confidence": r.confidence,
            "reason": r.reason,
            "phone_session_id": r.phone_session_id,
            "task_run_id": r.task_run_id,
        },
    }


def _scope_fully_attributed(
    session: Session,
    *,
    account_id: Optional[int] = None,
    group_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> bool:
    row = session.execute(
        text(
            "SELECT count(*) AS total, count(ca.transaction_id) AS attributed "
            "FROM geelark_transactions t "
            "LEFT JOIN accounts a ON a.geelark_phone_id = t.env_id "
            "LEFT JOIN cost_attributions ca ON ca.transaction_id = t.id "
            "WHERE (CAST(:account_id AS integer) IS NULL "
            "       OR a.id = CAST(:account_id AS integer)) "
            "  AND (CAST(:group_id AS integer) IS NULL "
            "       OR a.group_id = CAST(:group_id AS integer)) "
            "  AND (CAST(:date_from AS timestamp) IS NULL "
            "       OR t.geelark_created_at >= CAST(:date_from AS timestamp)) "
            "  AND (CAST(:date_to AS timestamp) IS NULL "
            "       OR t.geelark_created_at < CAST(:date_to AS timestamp))"
        ),
        {
            "account_id": account_id,
            "group_id": group_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    ).first()
    return bool(row and int(row.total) == int(row.attributed))


def _list_with_ledger_attribution(
    session: Session,
    *,
    page: int,
    page_size: int,
    account_id: Optional[int],
    group_id: Optional[int],
    kind: Optional[str],
    date_from: Optional[datetime],
    date_to: Optional[datetime],
) -> tuple[list[dict], int]:
    params = {
        "account_id": account_id,
        "group_id": group_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    public_kind = _public_kind_sql("ca")
    where_kind = ""
    if kind is not None:
        where_kind = f" AND {public_kind} = CAST(:kind AS text)"
        params["kind"] = kind

    base_from = (
        "FROM geelark_transactions t "
        "LEFT JOIN accounts a ON a.geelark_phone_id = t.env_id "
        "LEFT JOIN groups g ON g.id = a.group_id "
        "JOIN cost_attributions ca ON ca.transaction_id = t.id "
        "LEFT JOIN phone_sessions ps ON ps.id = ca.phone_session_id "
        "LEFT JOIN task_runs tr ON tr.id = ca.task_run_id "
        "WHERE (CAST(:account_id AS integer) IS NULL "
        "       OR a.id = CAST(:account_id AS integer)) "
        "  AND (CAST(:group_id AS integer) IS NULL "
        "       OR a.group_id = CAST(:group_id AS integer)) "
        "  AND (CAST(:date_from AS timestamp) IS NULL "
        "       OR t.geelark_created_at >= CAST(:date_from AS timestamp)) "
        "  AND (CAST(:date_to AS timestamp) IS NULL "
        "       OR t.geelark_created_at < CAST(:date_to AS timestamp))"
        f"{where_kind}"
    )
    total = session.execute(
        text(f"SELECT count(*) {base_from}"), params,
    ).scalar() or 0

    params["limit"] = page_size
    params["offset"] = max(0, (page - 1) * page_size)
    rows = session.execute(
        text(
            "SELECT t.id, t.geelark_created_at, t.amount, "
            "       t.used_time_min, t.env_id, "
            "       a.id AS account_id, a.profile_name, "
            "       a.group_id, g.name AS group_name, "
            "       ca.kind, ca.confidence, ca.reason, "
            "       ca.phone_session_id, ca.task_run_id, "
            "       ps.owner_type, ps.owner_id, "
            "       tr.ref_type AS task_ref_type, tr.ref_id AS task_ref_id "
            f"{base_from} "
            "ORDER BY t.geelark_created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        params,
    ).all()
    return [_ledger_row(r) for r in rows], int(total)


def _list_with_legacy_attribution(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    account_id: Optional[int] = None,
    group_id: Optional[int] = None,
    kind: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> tuple[list[dict], int]:
    """Return one page of transactions by recomputing legacy attribution."""
    params = {
        "account_id": account_id,
        "group_id": group_id,
        "date_from": date_from,
        "date_to": date_to,
        "pad": WINDOW_PAD_SECONDS,
    }

    # Filter on kind after the lateral lookup. Use a string compare with
    # the COALESCE on `kind` so the `idle` bucket (NULL kind) is filterable.
    sql = _ATTRIBUTION_SQL
    where_kind = ""
    if kind is not None:
        where_kind = (
            " WHERE COALESCE(kind, 'idle') = CAST(:kind AS text)"
        )
        params["kind"] = kind

    count_sql = f"SELECT count(*) FROM ({sql}{where_kind}) c"
    total = session.execute(text(count_sql), params).scalar() or 0

    page_sql = (
        f"{sql}{where_kind} "
        "ORDER BY geelark_created_at DESC "
        "LIMIT :limit OFFSET :offset"
    )
    params["limit"] = page_size
    params["offset"] = max(0, (page - 1) * page_size)
    rows = session.execute(text(page_sql), params).all()
    return [_row(r) for r in rows], int(total)


def _summary_kind_from_ledger(
    session: Session,
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[dict]:
    public_kind = _public_kind_sql("ca")
    rows = session.execute(
        text(
            f"SELECT {public_kind} AS k, "
            "       count(*) AS n, "
            "       sum(t.amount) AS total "
            "FROM cost_attributions ca "
            "JOIN geelark_transactions t ON t.id = ca.transaction_id "
            "WHERE (CAST(:date_from AS timestamp) IS NULL "
            "       OR t.geelark_created_at >= CAST(:date_from AS timestamp)) "
            "  AND (CAST(:date_to AS timestamp) IS NULL "
            "       OR t.geelark_created_at <  CAST(:date_to AS timestamp)) "
            f"GROUP BY {public_kind} ORDER BY total ASC"
        ),
        {"date_from": date_from, "date_to": date_to},
    ).all()
    return [
        {
            "key": r.k,
            "label": r.k,
            "count": int(r.n),
            "total": float(r.total or 0),
        }
        for r in rows
    ]


def _idle_pct_from_ledger(
    session: Session,
    *,
    date_from: datetime,
    date_to: Optional[datetime] = None,
) -> float:
    public_kind = _public_kind_sql("ca")
    rows = session.execute(
        text(
            f"SELECT {public_kind} AS k, sum(t.amount) AS s "
            "FROM cost_attributions ca "
            "JOIN geelark_transactions t ON t.id = ca.transaction_id "
            "WHERE t.geelark_created_at >= :date_from "
            "  AND (CAST(:date_to AS timestamp) IS NULL "
            "       OR t.geelark_created_at < CAST(:date_to AS timestamp)) "
            f"GROUP BY {public_kind}"
        ),
        {"date_from": date_from, "date_to": date_to},
    ).all()
    idle_total = 0.0
    grand_total = 0.0
    for r in rows:
        s = float(r.s or 0)
        grand_total += s
        if r.k == "idle":
            idle_total = s
    return (
        round(idle_total / grand_total * 100.0, 1)
        if grand_total != 0 else 0.0
    )


def list_with_attribution(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    account_id: Optional[int] = None,
    group_id: Optional[int] = None,
    kind: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> tuple[list[dict], int]:
    """Return one page of transactions with attribution + total."""
    if _scope_fully_attributed(
        session,
        account_id=account_id,
        group_id=group_id,
        date_from=date_from,
        date_to=date_to,
    ):
        return _list_with_ledger_attribution(
            session,
            page=page,
            page_size=page_size,
            account_id=account_id,
            group_id=group_id,
            kind=kind,
            date_from=date_from,
            date_to=date_to,
        )
    return _list_with_legacy_attribution(
        session,
        page=page,
        page_size=page_size,
        account_id=account_id,
        group_id=group_id,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
    )


def summary(
    session: Session,
    *,
    group_by: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[dict]:
    """Aggregate spend; group_by ∈ {day, account, group, kind}."""
    params = {
        "account_id": None,
        "group_id": None,
        "date_from": date_from,
        "date_to": date_to,
        "pad": WINDOW_PAD_SECONDS,
    }

    if group_by == "day":
        sql = (
            "SELECT date_trunc('day', geelark_created_at) AS bucket, "
            "       count(*) AS n, sum(amount) AS total "
            "FROM geelark_transactions "
            "WHERE (CAST(:date_from AS timestamp) IS NULL "
            "       OR geelark_created_at >= CAST(:date_from AS timestamp)) "
            "  AND (CAST(:date_to   AS timestamp) IS NULL "
            "       OR geelark_created_at <  CAST(:date_to AS timestamp)) "
            "GROUP BY bucket ORDER BY bucket DESC"
        )
        out = []
        for r in session.execute(text(sql), params).all():
            out.append({
                "key": r.bucket.date().isoformat(),
                "label": r.bucket.date().isoformat(),
                "count": int(r.n),
                "total": float(r.total or 0),
            })
        return out

    if group_by == "account":
        sql = (
            "SELECT a.id AS aid, a.profile_name AS pname, "
            "       count(*) AS n, sum(t.amount) AS total "
            "FROM geelark_transactions t "
            "LEFT JOIN accounts a ON a.geelark_phone_id = t.env_id "
            "WHERE (CAST(:date_from AS timestamp) IS NULL "
            "       OR t.geelark_created_at >= CAST(:date_from AS timestamp)) "
            "  AND (CAST(:date_to   AS timestamp) IS NULL "
            "       OR t.geelark_created_at <  CAST(:date_to   AS timestamp)) "
            "GROUP BY a.id, a.profile_name ORDER BY total ASC"
        )
        return [
            {
                "key": str(r.aid) if r.aid is not None else "unknown",
                "label": r.pname or "(unknown / deleted)",
                "count": int(r.n),
                "total": float(r.total or 0),
            }
            for r in session.execute(text(sql), params).all()
        ]

    if group_by == "group":
        sql = (
            "SELECT g.id AS gid, g.name AS gname, "
            "       count(*) AS n, sum(t.amount) AS total "
            "FROM geelark_transactions t "
            "LEFT JOIN accounts a ON a.geelark_phone_id = t.env_id "
            "LEFT JOIN groups   g ON g.id = a.group_id "
            "WHERE (CAST(:date_from AS timestamp) IS NULL "
            "       OR t.geelark_created_at >= CAST(:date_from AS timestamp)) "
            "  AND (CAST(:date_to   AS timestamp) IS NULL "
            "       OR t.geelark_created_at <  CAST(:date_to   AS timestamp)) "
            "GROUP BY g.id, g.name ORDER BY total ASC"
        )
        return [
            {
                "key": str(r.gid) if r.gid is not None else "ungrouped",
                "label": r.gname or "(ungrouped)",
                "count": int(r.n),
                "total": float(r.total or 0),
            }
            for r in session.execute(text(sql), params).all()
        ]

    if group_by == "kind":
        if _scope_fully_attributed(
            session, date_from=date_from, date_to=date_to,
        ):
            return _summary_kind_from_ledger(
                session, date_from=date_from, date_to=date_to,
            )
        sql = (
            f"SELECT COALESCE(kind, 'idle') AS k, "
            "       count(*) AS n, sum(amount) AS total "
            f"FROM ({_ATTRIBUTION_SQL}) attr "
            "GROUP BY k ORDER BY total ASC"
        )
        return [
            {
                "key": r.k,
                "label": r.k,
                "count": int(r.n),
                "total": float(r.total or 0),
            }
            for r in session.execute(text(sql), params).all()
        ]

    raise ValueError(f"unsupported group_by={group_by!r}")


def kpis(session: Session, *, now: datetime) -> dict:
    """Today / month-to-date / 30d avg / 30d idle %.

    All sums ignore `amount = 0` rows; these are `chargeType = 8`
    free-tier minutes that GeeLark records as transactions but does
    not actually charge for. Counting them as spend would mislead the
    operator about today/MTD totals.
    """
    today_start = datetime(now.year, now.month, now.day)
    month_start = datetime(now.year, now.month, 1)
    win_30d_start = today_start.replace(day=1)  # placeholder; overwritten below
    from datetime import timedelta
    win_30d_start = today_start - timedelta(days=30)

    def _sum(date_from: datetime, date_to: Optional[datetime] = None) -> float:
        params = {"date_from": date_from, "date_to": date_to}
        r = session.execute(
            text(
                "SELECT COALESCE(sum(amount), 0) "
                "FROM geelark_transactions "
                "WHERE geelark_created_at >= :date_from "
                "  AND (CAST(:date_to AS timestamp) IS NULL "
                "       OR geelark_created_at < CAST(:date_to AS timestamp))"
            ),
            params,
        ).scalar()
        return float(r or 0)

    today_spent = _sum(today_start)
    mtd_spent = _sum(month_start)
    win_30d_total = _sum(win_30d_start)
    day_avg_30d = win_30d_total / 30.0

    if _scope_fully_attributed(session, date_from=win_30d_start):
        idle_pct = _idle_pct_from_ledger(session, date_from=win_30d_start)
    else:
        # idle % over 30d via the attribution CTE filtered to that window.
        idle_sql = (
            f"SELECT COALESCE(kind, 'idle') AS k, sum(amount) AS s "
            f"FROM ({_ATTRIBUTION_SQL}) attr GROUP BY k"
        )
        params = {
            "account_id": None,
            "group_id": None,
            "date_from": win_30d_start,
            "date_to": None,
            "pad": WINDOW_PAD_SECONDS,
        }
        idle_total = 0.0
        grand_total = 0.0
        for r in session.execute(text(idle_sql), params).all():
            s = float(r.s or 0)
            grand_total += s
            if r.k == "idle":
                idle_total = s
        idle_pct = (
            round(idle_total / grand_total * 100.0, 1)
            if grand_total != 0 else 0.0
        )

    return {
        "today_spent": round(today_spent, 4),
        "mtd_spent": round(mtd_spent, 4),
        "day_avg_30d": round(day_avg_30d, 4),
        "idle_30d_pct": idle_pct,
    }


def ensure_attributions(session: Session, transaction_ids: list[str]) -> int:
    """Compute attribution via _ATTRIBUTION_SQL filtered to the given
    transaction ids and upsert each result into cost_attributions.

    Returns the number of rows written. Used by:
      - sync_transactions task, immediately after upserting fresh rows
        so the Costs page can read from the persisted ledger from the
        next render onward.
      - Lazy backfill paths that find a transaction without a persisted
        attribution.

    Idempotent: re-running on the same transaction_ids overwrites the
    cost_attributions row with a fresh compute (ON CONFLICT updates).
    """
    if not transaction_ids:
        return 0
    from app.repositories import cost_attributions as ca_repo

    # Reuse _ATTRIBUTION_SQL so the persistence path computes exactly
    # the same answer as the render path. Wrap to filter by tx id.
    sql = (
        "SELECT * FROM (" + _ATTRIBUTION_SQL + ") full_attr "
        "WHERE full_attr.id = ANY(:ids)"
    )
    params = {
        "account_id": None,
        "group_id": None,
        "date_from": None,
        "date_to": None,
        "pad": WINDOW_PAD_SECONDS,
        "ids": list(transaction_ids),
    }
    rows = session.execute(text(sql), params).all()
    written = 0
    for r in rows:
        kind = r.kind or "unattributed"
        confidence = getattr(r, "confidence", None)
        reason = getattr(r, "reason", None)
        if r.kind is None:
            confidence = "low"
            reason = (
                "no phone_session, publish, warmup, or engagement "
                "signal matched the tx window"
            )
        ca_repo.upsert(
            session, r.id,
            kind=kind,
            confidence=confidence or "low",
            reason=reason or "",
            phone_session_id=getattr(r, "phone_session_id", None),
            task_run_id=getattr(r, "task_run_id", None),
        )
        written += 1
    session.commit()
    return written


def iter_for_export(
    session: Session,
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    account_id: Optional[int] = None,
):
    """Yield dicts for CSV export (no pagination)."""
    params = {
        "account_id": account_id,
        "group_id": None,
        "date_from": date_from,
        "date_to": date_to,
        "pad": WINDOW_PAD_SECONDS,
    }
    sql = f"{_ATTRIBUTION_SQL} ORDER BY geelark_created_at DESC"
    for r in session.execute(text(sql), params).all():
        yield _row(r)
