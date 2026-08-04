from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.models import (
    Account, Group, EngagementRun, EngagementTask, PhoneSession, Publication,
)
from app.config import SETTINGS_DEFAULTS
from app.repositories import settings as set_repo
from app.repositories.engagement import BUSY_RESULTS
from app.services.platforms import LOGGED_IN_COL, validate as validate_platform


_DAILY_LIMIT_SETTING = {
    "tiktok":   "daily_comment_limit_tiktok",
    "facebook": "daily_comment_limit_facebook",
}


def _daily_limit_for(session: Session, platform: str) -> int:
    key = _DAILY_LIMIT_SETTING.get(platform)
    if not key:
        return 0
    raw = (
        set_repo.get_all(session).get(key)
        or SETTINGS_DEFAULTS.get(key)
        or "0"
    )
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def list_eligible_commenters(session: Session, *,
                             platform: str = "tiktok") -> list[dict]:
    platform = validate_platform(platform)
    logged_in_col = getattr(Account, LOGGED_IN_COL[platform])
    rows = session.exec(
        select(Account, Group.name)
        .join(Group, Group.id == Account.group_id, isouter=True)
        .where(
            logged_in_col == 1,
            Account.warmed_up == 1,
            Account.geelark_phone_id.is_not(None),
            Account.geelark_phone_id != "",
            or_(
                Account.proxy_status.is_(None),
                Account.proxy_status != "fail",
            ),
            or_(
                Account.proxy_expires_at.is_(None),
                Account.proxy_expires_at > datetime.utcnow(),
            ),
        )
        .order_by(Group.name.nullslast(), Account.profile_name)
    ).all()

    busy_ids = set(
        session.exec(
            select(EngagementRun.account_id).where(
                EngagementRun.result.in_(BUSY_RESULTS),
                EngagementRun.account_id.is_not(None),
            )
        ).all()
    )
    busy_ids.update(
        session.exec(
            select(Publication.account_id).where(
                Publication.result.in_(("QUEUED", "INITIATED")),
                Publication.account_id.is_not(None),
            )
        ).all()
    )
    busy_ids.update(
        session.exec(
            select(PhoneSession.account_id).where(
                PhoneSession.stopped_at.is_(None),
            )
        ).all()
    )

    limit = _daily_limit_for(session, platform)
    today_counts: dict[int, int] = {}
    if limit > 0:
        start = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        # Same charging rule as count_account_attempts_today: backlog does
        # not consume quota; a row is charged once submission starts.
        count_rows = session.exec(
            select(EngagementRun.account_id, func.count(EngagementRun.id))
            .join(EngagementTask,
                  EngagementTask.id == EngagementRun.engagement_task_id)
            .where(
                EngagementTask.platform == platform,
                or_(
                    EngagementRun.started_at >= start,
                    and_(
                        EngagementRun.result == "SUCCESS",
                        EngagementRun.started_at.is_(None),
                        EngagementRun.created_at >= start,
                    ),
                ),
                EngagementRun.account_id.is_not(None),
            )
            .group_by(EngagementRun.account_id)
        ).all()
        today_counts = {acc_id: count for acc_id, count in count_rows}

    items = []
    for account, group_name in rows:
        used_today = today_counts.get(account.id, 0)
        items.append({
            "id": account.id,
            "profile_name": account.profile_name,
            "group_id": account.group_id,
            "group_name": group_name,
            "busy": account.id in busy_ids,
            "daily_limit_reached": (
                limit > 0 and used_today >= limit
            ),
            "daily_used": used_today,
            "daily_limit": limit,
        })
    return items
