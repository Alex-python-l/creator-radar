"""每日定时刷新：找所有 active 账号，逐个 enqueue 一个 account.fetch job（mode='recent'）。
由 main.py 的 APScheduler 在北京时间 05:00 调用。
去重：已在 queued/running 的账号 skip。
"""
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, text

from app.db import async_session
from app.db.models import Influencer, Job

logger = structlog.get_logger(__name__)

ACCOUNT_FETCH_LOCK_NAMESPACE = 20260617


async def _lock_account_fetch(session, influencer_id: int) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :influencer_id)"),
        {"namespace": ACCOUNT_FETCH_LOCK_NAMESPACE, "influencer_id": influencer_id},
    )


async def _has_active_job(session, influencer_id: int) -> bool:
    row = (
        await session.execute(
            select(Job.id)
            .where(
                Job.kind == "account.fetch",
                Job.status.in_(("queued", "running")),
                Job.payload["influencerId"].astext == str(influencer_id),
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def enqueue_daily_refresh() -> dict:
    enqueued = 0
    skipped = 0
    async with async_session() as session:
        active = (
            await session.execute(
                select(Influencer.id, Influencer.platform, Influencer.username).where(
                    Influencer.is_active.is_(True)
                )
            )
        ).all()

    for inf_id, platform, username in active:
        async with async_session() as session:
            await _lock_account_fetch(session, inf_id)
            if await _has_active_job(session, inf_id):
                skipped += 1
                continue
            session.add(
                Job(
                    kind="account.fetch",
                    payload={
                        "influencerId": inf_id,
                        "platform": platform,
                        "username": username,
                        "mode": "recent",
                    },
                    status="queued",
                    next_run_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            enqueued += 1

    logger.info("daily.refresh.done", enqueued=enqueued, skipped=skipped)
    return {"enqueued": enqueued, "skipped": skipped}
