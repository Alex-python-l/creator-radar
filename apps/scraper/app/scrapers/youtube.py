"""YouTube Shorts 抓取。

第一版只抓频道 `/shorts` 列表,不登录、不下载视频文件。
实现风格对齐 TikTok:yt-dlp 服务端抓元数据 + 代理池失败换 IP 重试。
"""
import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import structlog
import yt_dlp

from app.config import settings
from app.scrapers.errors import RateLimited
from app.scrapers.proxies import effective_proxy_pool, pooled_proxy_candidates
from app.scrapers.tiktok import ScrapedVideo

logger = structlog.get_logger(__name__)

_MAX_PROXY_ATTEMPTS = 4
_DETAIL_CONCURRENCY = 4
_YOUTUBE_PATH_PREFIXES = frozenset({"channel", "c", "user"})
_NO_SHORTS_TAB = "does not have a shorts tab"


def normalize_youtube_identifier(raw: str) -> str:
    """把 YouTube 账号输入归一成 DB identifier。

    handle 账号存纯 handle(`mkbhd`),老式频道路径保留 path identifier
    (`channel/<id>` / `c/<slug>` / `user/<name>`),方便拼正确 profile/shorts URL。
    """
    s = (raw or "").strip()
    if not s:
        return ""

    probe = s if "://" in s else f"https://{s}" if "youtube.com/" in s.lower() else ""
    if probe:
        parsed = urlparse(probe)
        if "youtube.com" in parsed.netloc.lower():
            parts = [p for p in parsed.path.split("/") if p]
            if parts:
                first = parts[0]
                if first.startswith("@"):
                    return first[1:]
                if first in _YOUTUBE_PATH_PREFIXES and len(parts) >= 2:
                    return f"{first}/{parts[1]}"

    return s.lstrip("@").strip("/")


def _shorts_url(username: str) -> str:
    identifier = normalize_youtube_identifier(username)
    if any(identifier.startswith(f"{prefix}/") for prefix in _YOUTUBE_PATH_PREFIXES):
        return f"https://www.youtube.com/{identifier}/shorts"
    return f"https://www.youtube.com/@{identifier}/shorts"


async def fetch_youtube_shorts_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> list[ScrapedVideo]:
    """Lazy wrapper to avoid importing Bright Data client while this module initializes."""
    from app.scrapers.bright_data_social import (
        fetch_youtube_shorts_via_bright_data as _impl,
    )

    return await _impl(username, max_count)


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_datetime(timestamp: Any) -> datetime | None:
    try:
        ts = int(timestamp)
        return datetime.fromtimestamp(ts, tz=UTC)
    except (TypeError, ValueError):
        return None


def _video_url(video_id: str, entry: dict[str, Any]) -> str:
    raw_url = entry.get("webpage_url") or entry.get("url")
    if (
        isinstance(raw_url, str)
        and raw_url.startswith("http")
        and ("youtube.com/" in raw_url or "youtu.be/" in raw_url)
    ):
        return raw_url
    return f"https://www.youtube.com/shorts/{video_id}"


def _entry_to_video_ytdlp(entry: dict[str, Any]) -> ScrapedVideo | None:
    video_id = entry.get("id")
    if not video_id:
        return None
    video_id = str(video_id)
    return ScrapedVideo(
        video_id=video_id,
        video_url=_video_url(video_id, entry),
        description=entry.get("description") or entry.get("title"),
        thumbnail_url=entry.get("thumbnail"),
        duration_seconds=_to_int(entry.get("duration")) or None,
        play_count=_to_int(entry.get("view_count")),
        like_count=_to_int(entry.get("like_count")),
        comment_count=_to_int(entry.get("comment_count")),
        share_count=0,
        published_at=_to_datetime(entry.get("timestamp")),
        raw=entry,
    )


def _ytdlp_extract_sync(
    username: str, max_count: int | None, proxy: str | None
) -> list[dict[str, Any]]:
    opts: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if max_count:
        opts["playlist_items"] = f"1-{max_count}"
    if proxy:
        opts["proxy"] = proxy

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(_shorts_url(username), download=False)

    if not info:
        return []
    if "entries" in info:
        return [e for e in info["entries"] if e]
    return [info]


def _ytdlp_extract_video_sync(
    video_id: str, entry: dict[str, Any], proxy: str | None
) -> dict[str, Any] | None:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if proxy:
        opts["proxy"] = proxy

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(_video_url(video_id, entry), download=False)

    return info if isinstance(info, dict) else None


def _merge_entry(base: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return base
    merged = dict(base)
    for key, value in detail.items():
        if value is not None and value != "":
            merged[key] = value
    return merged


async def _fetch_videos_ytdlp(
    username: str,
    max_count: int | None,
    proxy: str | None,
    detail_proxies: list[str | None] | None = None,
) -> list[ScrapedVideo]:
    entries = await asyncio.to_thread(_ytdlp_extract_sync, username, max_count, proxy)
    detail_candidates = detail_proxies or [proxy]

    async def enrich_entry(entry: dict[str, Any]) -> ScrapedVideo | None:
        video_id = entry.get("id")
        if not video_id:
            return None
        merged = entry
        for detail_proxy in detail_candidates:
            try:
                detail = await asyncio.to_thread(
                    _ytdlp_extract_video_sync, str(video_id), entry, detail_proxy
                )
                merged = _merge_entry(entry, detail)
                break
            except Exception as exc:
                # 单条详情经常比 shorts 列表更容易触发 YouTube bot 检查。
                # 保留列表里的 view_count,不要让整个账号抓取失败。
                logger.warning(
                    "youtube.shorts.detail_failed",
                    video_id=str(video_id),
                    proxy_set=detail_proxy is not None,
                    error=str(exc)[:200],
                )
        video = _entry_to_video_ytdlp(merged)
        return video

    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def enrich_entry_with_limit(entry: dict[str, Any]) -> ScrapedVideo | None:
        async with semaphore:
            return await enrich_entry(entry)

    videos = await asyncio.gather(*(enrich_entry_with_limit(entry) for entry in entries))
    return [video for video in videos if video is not None]


async def fetch_youtube_shorts(
    username: str,
    max_count: int | None = None,
) -> list[ScrapedVideo]:
    """抓 YouTube 频道 Shorts 列表。

    空列表代表频道暂无 Shorts 或 yt-dlp 当前只拿到空 playlist,不当错误。
    所有代理都失败才抛 RateLimited,交给 worker retry/backoff。
    """
    username = normalize_youtube_identifier(username)
    logger.info("youtube.shorts.fetch.start", username=username, max_count=max_count)

    proxy_pool = effective_proxy_pool(settings.youtube_proxy_pool, settings.common_proxy_pool)
    proxies = pooled_proxy_candidates(proxy_pool, settings.proxy_url, _MAX_PROXY_ATTEMPTS)
    last_error = "no attempt"
    for attempt, proxy in enumerate(proxies, start=1):
        try:
            videos = await _fetch_videos_ytdlp(
                username, max_count, proxy, detail_proxies=proxies
            )
            logger.info(
                "youtube.shorts.fetch.done",
                username=username,
                count=len(videos),
                attempt=attempt,
            )
            return videos
        except Exception as exc:
            last_error = str(exc)[:200]
            if _NO_SHORTS_TAB in str(exc).lower():
                logger.info(
                    "youtube.shorts.fetch.no_tab",
                    username=username,
                    attempt=attempt,
                )
                return []
            logger.warning(
                "youtube.shorts.fetch.attempt_failed",
                username=username,
                attempt=attempt,
                error=last_error,
            )

    if settings.bright_data_api_key:
        logger.warning(
            "youtube.shorts.fetch.fallback_bright_data",
            username=username,
            error=last_error,
        )
        return await fetch_youtube_shorts_via_bright_data(username, max_count)
    raise RateLimited(
        f"youtube shorts fetch failed after {len(proxies)} proxy attempt(s): {last_error}"
    )
