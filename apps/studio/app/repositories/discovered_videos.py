"""Query layer over discovered_videos table. Used by the engagement
discover-targets API endpoint to surface candidate TikTok URLs that no
account has comment-attempted (other than failed attempts)."""
from __future__ import annotations

from sqlmodel import Session, select
from sqlalchemy import not_, exists

from app.models import DiscoveredVideo, EngagementRun


def list_unused_targets(
    session: Session, *, limit: int = 2000
) -> list[DiscoveredVideo]:
    used_results = ("PENDING", "QUEUED", "INITIATED", "SUCCESS")
    stmt = (
        select(DiscoveredVideo)
        .where(
            not_(exists().where(
                (EngagementRun.target_link == DiscoveredVideo.url)
                & (EngagementRun.result.in_(used_results))
            ))
        )
        .order_by(DiscoveredVideo.play_count.desc().nullslast())
        .limit(limit)
    )
    return list(session.exec(stmt).all())
