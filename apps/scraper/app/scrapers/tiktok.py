"""
TikTok 抓取：yt-dlp 拉 user page 元数据（只抓视频列表）。

yt-dlp 走服务端 HTTP，不带浏览器自动化特征，比 Playwright 更不容易触发
TikTok 反爬。yt-dlp 偶尔撞反爬（"Unable to extract secondary user ID" /
429 / 空结果）是间歇性的 —— 换一条代理 IP 重试通常能过，故 fetch_tiktok_videos
会逐条换 IP 重试。

账号 profile（follower / 头像 / bio）需要浏览器渲染，已随 Playwright 一起
移除：TikTok 只抓视频列表，profile 字段留空。
"""
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
import yt_dlp

from app.config import settings
from app.scrapers.errors import AccountGone, RateLimited
from app.scrapers.proxies import effective_proxy_pool, pooled_proxy_candidates
from app.scrapers.tiktok_profile import TikTokProfileProbeResult, probe_tiktok_profile

logger = structlog.get_logger(__name__)

# yt-dlp 撞反爬时，逐条换代理 IP 重试，最多试这么多条
_MAX_PROXY_ATTEMPTS = 4


@dataclass
class ScrapedVideo:
    """归一化的视频条目。"""

    video_id: str
    video_url: str | None
    description: str | None
    thumbnail_url: str | None
    duration_seconds: int | None
    play_count: int
    like_count: int
    comment_count: int
    share_count: int
    published_at: datetime | None
    raw: dict[str, Any]
    content_type: str = "video"


def _profile_url(username: str) -> str:
    return f"https://www.tiktok.com/@{username.lstrip('@')}"


def _to_int(value: Any) -> int:
    """统一把 stats 字段转 int。"""
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


def _entry_to_video_ytdlp(entry: dict[str, Any]) -> ScrapedVideo | None:
    """把 yt-dlp 的 entry 归一化成 ScrapedVideo。"""
    video_id = entry.get("id")
    if not video_id:
        return None
    return ScrapedVideo(
        video_id=str(video_id),
        video_url=entry.get("webpage_url") or entry.get("url"),
        description=entry.get("description") or entry.get("title"),
        thumbnail_url=entry.get("thumbnail"),
        duration_seconds=_to_int(entry.get("duration")) or None,
        play_count=_to_int(entry.get("view_count") or entry.get("play_count")),
        like_count=_to_int(entry.get("like_count")),
        comment_count=_to_int(entry.get("comment_count")),
        share_count=_to_int(entry.get("repost_count") or entry.get("share_count")),
        published_at=_to_datetime(entry.get("timestamp")),
        raw=entry,
    )


def _ytdlp_extract_sync(
    username: str, max_count: int | None, proxy: str | None
) -> list[dict[str, Any]]:
    """yt-dlp 同步 API，由 asyncio.to_thread 包着调。

    max_count=None / 0 → 不限制 playlist_items，拉账号能拿到的全部。
    extract_flat='in_playlist' → 只拿 user playlist 元数据，不 fetch 每个视频。
    """
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
        info = ydl.extract_info(_profile_url(username), download=False)

    if not info:
        return []
    if "entries" in info:
        return [e for e in info["entries"] if e]
    return [info]


async def _fetch_videos_ytdlp(
    username: str, max_count: int | None, proxy: str | None
) -> list[ScrapedVideo]:
    """yt-dlp 单次抓取（走一条代理）。失败抛异常，由 fetch_tiktok_videos 接住换 IP。"""
    entries = await asyncio.to_thread(_ytdlp_extract_sync, username, max_count, proxy)
    return [v for v in (_entry_to_video_ytdlp(e) for e in entries) if v is not None]


async def fetch_tiktok_videos_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> list[ScrapedVideo]:
    """Lazy wrapper to avoid importing Bright Data client while this module initializes."""
    from app.scrapers.bright_data_social import (
        fetch_tiktok_videos_via_bright_data as _impl,
    )

    return await _impl(username, max_count)


async def fetch_tiktok_records_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Lazy wrapper to avoid importing Bright Data client while this module initializes."""
    from app.scrapers.bright_data_social import (
        fetch_tiktok_records_via_bright_data as _impl,
    )

    return await _impl(username, max_count)


def tiktok_records_confirm_account_gone(records: list[dict[str, Any]]) -> bool:
    """Lazy wrapper for Bright Data account-gone confirmation."""
    from app.scrapers.bright_data_social import (
        tiktok_records_confirm_account_gone as _impl,
    )

    return _impl(records)


def normalize_tiktok_records_from_bright_data(records: list[dict[str, Any]]) -> list[ScrapedVideo]:
    """Lazy wrapper for Bright Data TikTok record normalization."""
    from app.scrapers.bright_data_social import normalize_tiktok_records

    return normalize_tiktok_records(records)


async def _fallback_after_ytdlp_failures(
    username: str,
    max_count: int | None,
    native_probe: TikTokProfileProbeResult,
    last_error: str,
) -> list[ScrapedVideo]:
    """Bright Data fallback with double-confirmed account-gone handling."""
    if not settings.bright_data_api_key:
        if native_probe.status == "gone":
            raise RateLimited(
                "tiktok profile probe says account is gone, but Bright Data is not configured "
                "to confirm it"
            )
        raise RateLimited(last_error)

    if native_probe.status == "gone":
        logger.warning(
            "tiktok.fetch.double_confirm_bright_data",
            username=username,
            status_code=native_probe.status_code,
        )
        records = await fetch_tiktok_records_via_bright_data(username, max_count)
        if tiktok_records_confirm_account_gone(records):
            raise AccountGone(
                "tiktok account gone confirmed by profile page and Bright Data"
            )
        videos = normalize_tiktok_records_from_bright_data(records)
        if videos:
            logger.info("tiktok.bright_data.done", username=username, count=len(videos))
            return videos
        raise RateLimited(
            "tiktok profile probe says account is gone, but Bright Data did not confirm it"
        )

    logger.warning(
        "tiktok.fetch.fallback_bright_data",
        username=username,
        native_probe=native_probe.status,
        error=last_error,
    )
    return await fetch_tiktok_videos_via_bright_data(username, max_count)


async def fetch_tiktok_videos(
    username: str,
    max_count: int | None = None,
) -> list[ScrapedVideo]:
    """yt-dlp 拉 TikTok 用户视频列表。

    yt-dlp 走服务端 HTTP，绕开浏览器自动化检测。偶尔撞 TikTok 反爬
    （"Unable to extract secondary user ID" / 429 / 网络抖动）是间歇性的，
    所以逐条换代理 IP 重试，最多 _MAX_PROXY_ATTEMPTS 条。全部失败 → 抛
    RateLimited（transient，worker 退避后会再 retry）。

    yt-dlp 不抛异常但返回空列表 → 视为账号无视频，直接返回空（不重试）。
    max_count=None / 0 → 字面意思全部（用户偏好"全量"）。
    """
    username = username.lstrip("@")
    logger.info("tiktok.fetch.start", username=username, max_count=max_count)

    proxy_pool = effective_proxy_pool(settings.tiktok_proxy_pool, settings.common_proxy_pool)
    proxies = pooled_proxy_candidates(proxy_pool, settings.proxy_url, _MAX_PROXY_ATTEMPTS)
    last_error = "no attempt"
    for attempt, proxy in enumerate(proxies, start=1):
        try:
            videos = await _fetch_videos_ytdlp(username, max_count, proxy)
            logger.info(
                "tiktok.fetch.done",
                username=username,
                count=len(videos),
                attempt=attempt,
            )
            return videos
        except Exception as exc:
            last_error = str(exc)[:200]
            logger.warning(
                "tiktok.fetch.attempt_failed",
                username=username,
                attempt=attempt,
                error=last_error,
            )

    native_probe = await probe_tiktok_profile(username)
    if native_probe.status == "gone" or settings.bright_data_api_key:
        return await _fallback_after_ytdlp_failures(
            username=username,
            max_count=max_count,
            native_probe=native_probe,
            last_error=last_error,
        )

    # 所有代理都撞反爬 / 失败 —— 抛 transient，worker 退避后再 retry
    raise RateLimited(
        f"tiktok fetch failed after {len(proxies)} proxy attempt(s): {last_error}"
    )
