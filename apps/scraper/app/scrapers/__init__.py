"""Scraper 模块入口。"""
import asyncio
import random

import structlog

from app.config import settings
from app.scrapers.errors import ScrapeError
from app.scrapers.facebook import fetch_facebook_videos
from app.scrapers.instagram import fetch_instagram_videos
from app.scrapers.tiktok import fetch_tiktok_videos
from app.scrapers.types import ScrapedProfile
from app.scrapers.youtube import fetch_youtube_shorts

_logger = structlog.get_logger(__name__)


async def random_pre_fetch_delay(platform: str) -> None:
    """
    每个账号抓取前的随机延迟，分散流量降低风控触发概率。
    handler 在调任何 fetch_* 之前都 await 一下。
    """
    max_seconds = settings.min_fetch_interval_seconds
    if max_seconds <= 0:
        return
    delay = random.uniform(0, max_seconds)
    _logger.info("scrape.pre_fetch_delay", platform=platform, seconds=round(delay, 1))
    await asyncio.sleep(delay)


__all__ = [
    "ScrapeError",
    "ScrapedProfile",
    "fetch_facebook_videos",
    "fetch_instagram_videos",
    "fetch_tiktok_videos",
    "fetch_youtube_shorts",
    "random_pre_fetch_delay",
]
