import asyncio
import logging

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.handlers import enqueue_daily_refresh
from app.handlers.resolve_fb import resolve_fb_identifier
from app.worker import worker_loop

logging.basicConfig(level=settings.log_level.upper())
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger(__name__)

app = FastAPI(title="Short Video Monitor Scraper")

# 每个平台一条 worker lane：跨平台并发、同平台串行。
WORKER_PLATFORMS = ("tiktok", "instagram", "facebook", "youtube")

_stop_event = asyncio.Event()
_worker_tasks: list[asyncio.Task] = []
_scheduler: AsyncIOScheduler | None = None

DAILY_REFRESH_TIMEZONE = "Asia/Shanghai"
DAILY_REFRESH_HOUR = 5
DAILY_REFRESH_MINUTE = 0


def build_daily_refresh_trigger() -> CronTrigger:
    return CronTrigger(
        hour=DAILY_REFRESH_HOUR,
        minute=DAILY_REFRESH_MINUTE,
        timezone=DAILY_REFRESH_TIMEZONE,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class ResolveFbRequest(BaseModel):
    url: str = Field(min_length=1)


class ResolveFbResponse(BaseModel):
    identifier: str
    finalUrl: str  # camelCase 跨语言 — web 端 TS 直接 JSON.parse 不用 snake→camel 转换


@app.post("/resolve-fb-url", response_model=ResolveFbResponse)
async def resolve_fb_url(
    req: ResolveFbRequest,
    x_internal_token: str | None = Header(default=None),
) -> ResolveFbResponse:
    """FB share / pfbid raw URL → normalized identifier(供 web 端入库前调用)。

    400: 不是 facebook URL / 输入异常
    401: 配了 SCRAPER_INTERNAL_TOKEN 但调用方 token 不匹配
    502: 全代理 follow 失败 / final URL 无可提取 identifier
    """
    if settings.scraper_internal_token and x_internal_token != settings.scraper_internal_token:
        raise HTTPException(status_code=401, detail="unauthorized")
    if "facebook.com" not in req.url.lower():
        raise HTTPException(status_code=400, detail="url must be a facebook.com URL")
    try:
        identifier, final_url = await resolve_fb_identifier(req.url)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ResolveFbResponse(identifier=identifier, finalUrl=final_url)


@app.on_event("startup")
async def _on_startup() -> None:
    global _scheduler
    for platform in WORKER_PLATFORMS:
        concurrency = getattr(settings, f"{platform}_workers", 1) or 1
        for idx in range(concurrency):
            _worker_tasks.append(
                asyncio.create_task(worker_loop(_stop_event, platform, idx))
            )

    _scheduler = AsyncIOScheduler(timezone=DAILY_REFRESH_TIMEZONE)
    _scheduler.add_job(enqueue_daily_refresh, build_daily_refresh_trigger())
    _scheduler.start()
    logger.info(
        "scraper.startup",
        workers=list(WORKER_PLATFORMS),
        scheduler="started",
        daily_refresh_time=f"{DAILY_REFRESH_HOUR:02d}:{DAILY_REFRESH_MINUTE:02d}",
        daily_refresh_timezone=DAILY_REFRESH_TIMEZONE,
    )


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    _stop_event.set()
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
    if _worker_tasks:
        done, pending = await asyncio.wait(_worker_tasks, timeout=10)
        for task in pending:
            task.cancel()
    logger.info("scraper.shutdown")
