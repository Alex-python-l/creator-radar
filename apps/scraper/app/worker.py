"""自建 PG 队列 worker：轮询 jobs 表，认领并处理，retry/退避/reap 自管。

每个平台一条独立 loop（lane）：TikTok 和 Instagram 互不阻塞、可同时跑，
但**同平台内仍串行**——IG 串行防风控，TK 串行控制对代理池的抓取节奏。
`_claim_one` 按 payload.platform 过滤。
FOR UPDATE SKIP LOCKED 是防御性：即使误起重复 worker 也不会重复认领同一 job。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update

from app.db import async_session
from app.db.models import Job

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SEC = 3
REAP_INTERVAL_SEC = 60
RUNNING_STALE_SEC = 600
HEARTBEAT_INTERVAL_SEC = 60
BACKOFF_BASE_SEC = 60
BACKOFF_CAP_SEC = 1800


def _backoff(retry_count: int) -> int:
    return min(BACKOFF_BASE_SEC * (2 ** retry_count), BACKOFF_CAP_SEC)


async def _claim_one(platform: str, worker_id: str) -> dict | None:
    """认领一个该平台的 queued job。按 payload.platform 过滤，
    让 TK / IG 两条 lane 各取各的，互不抢占。"""
    async with async_session() as session:
        row = (
            await session.execute(
                select(Job)
                .where(
                    Job.status == "queued",
                    Job.next_run_at <= datetime.now(UTC),
                    Job.payload["platform"].astext == platform,
                )
                .order_by(Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.locked_by = worker_id
        row.locked_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        claimed = {"id": row.id, "kind": row.kind, "payload": dict(row.payload)}
        await session.commit()
    return claimed


async def _finalize(
    job_id: int,
    worker_id: str,
    ok: bool,
    transient: bool,
    error: str | None,
) -> bool:
    async with async_session() as session:
        job = (
            await session.execute(
                select(Job).where(
                    Job.id == job_id,
                    Job.status == "running",
                    Job.locked_by == worker_id,
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            logger.warning("worker.finalize_skipped_stale_lock", job_id=job_id, worker_id=worker_id)
            return False
        now = datetime.now(UTC)
        if ok:
            job.status = "success"
        elif transient and job.retry_count < job.max_retries:
            job.retry_count += 1
            job.status = "queued"
            job.next_run_at = now + timedelta(seconds=_backoff(job.retry_count))
            job.last_error = error
        elif transient:
            job.status = "dead"
            job.last_error = error
        else:
            job.status = "failed"
            job.last_error = error
        job.locked_by = None
        job.locked_at = None
        job.updated_at = now
        await session.commit()
    return True


async def _heartbeat(job_id: int, worker_id: str) -> None:
    """Keep long-running jobs from being reaped while the owning worker is alive."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
        now = datetime.now(UTC)
        async with async_session() as session:
            await session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == "running",
                    Job.locked_by == worker_id,
                )
                .values(locked_at=now, updated_at=now)
            )
            await session.commit()


async def _reap_stale() -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=RUNNING_STALE_SEC)
    async with async_session() as session:
        await session.execute(
            update(Job)
            .where(Job.status == "running", Job.locked_at <= cutoff)
            .values(
                status="queued",
                locked_by=None,
                locked_at=None,
                next_run_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _process(kind: str, payload: dict) -> tuple[bool, bool, str | None]:
    if kind == "account.fetch":
        from app.handlers.scrape import run_account_fetch_job

        return await run_account_fetch_job(payload)
    logger.warning("worker.unknown_kind", kind=kind)
    return (False, False, f"unknown job kind: {kind}")


async def worker_loop(stop_event: asyncio.Event, platform: str, idx: int = 0) -> None:
    """一条平台 lane 的处理循环。多个平台 lane 并发跑；同平台可起多条 lane
    并行认领（_claim_one 的 FOR UPDATE SKIP LOCKED 防重复认领），idx 区分 worker_id。"""
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{platform}-{idx}"
    logger.info("worker.start", worker_id=worker_id, platform=platform)
    loop = asyncio.get_event_loop()
    last_reap = 0.0
    while not stop_event.is_set():
        try:
            # reap 是全局的；两条 lane 都会跑，UPDATE 幂等、重复无害。
            if loop.time() - last_reap > REAP_INTERVAL_SEC:
                await _reap_stale()
                last_reap = loop.time()

            claimed = await _claim_one(platform, worker_id)
            if claimed is None:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            logger.info(
                "worker.claim",
                job_id=claimed["id"],
                kind=claimed["kind"],
                platform=platform,
            )
            heartbeat = asyncio.create_task(_heartbeat(claimed["id"], worker_id))
            try:
                try:
                    ok, transient, error = await _process(claimed["kind"], claimed["payload"])
                except Exception as exc:
                    logger.exception(
                        "worker.process_error",
                        job_id=claimed["id"],
                        platform=platform,
                    )
                    ok, transient, error = False, True, str(exc) or exc.__class__.__name__
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            finalized = await _finalize(claimed["id"], worker_id, ok, transient, error)
            logger.info(
                "worker.done",
                job_id=claimed["id"],
                ok=ok,
                transient=transient,
                platform=platform,
                finalized=finalized,
            )
        except Exception:
            logger.exception("worker.loop_error", platform=platform)
            await asyncio.sleep(POLL_INTERVAL_SEC)
    logger.info("worker.stop", worker_id=worker_id, platform=platform)
