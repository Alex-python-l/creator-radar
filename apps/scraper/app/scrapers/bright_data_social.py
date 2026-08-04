"""Bright Data fallback client for TikTok, Instagram, and YouTube."""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.scrapers.bright_data_facebook import _require_api_key, _wait_for_snapshot
from app.scrapers.errors import CredsMissing, RateLimited, ScrapeError
from app.scrapers.tiktok import ScrapedVideo
from app.scrapers.types import ScrapedProfile
from app.scrapers.youtube import _shorts_url

logger = structlog.get_logger(__name__)

_API_BASE = "https://api.brightdata.com/datasets/v3"


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = f"{s[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return None


def _first_str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _instagram_profile_url(username: str) -> str:
    return f"https://www.instagram.com/{username.lstrip('@').strip('/')}/"


def _tiktok_profile_url(username: str) -> str:
    return f"https://www.tiktok.com/@{username.lstrip('@').strip('/')}"


def _snapshot_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("snapshot_id", "snapshot", "id"):
        value = data.get(key)
        # BD 异步快照 id 前缀实测是 sd_(如 sd_mpqqlv2syn8qn7dpk);旧 s_ 也保留兼容
        if isinstance(value, str) and value.startswith(("s_", "sd_")):
            return value
    return None


async def scrape_bright_data_records(
    *,
    dataset_id: str,
    inputs: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Call Bright Data `/datasets/v3/scrape` and return JSON records."""
    api_key = _require_api_key()
    try:
        async with httpx.AsyncClient(timeout=settings.bright_data_timeout_seconds) as client:
            resp = await client.post(
                f"{_API_BASE}/scrape",
                params={
                    "dataset_id": dataset_id,
                    "format": "json",
                    "include_errors": "true",
                    **(params or {}),
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"input": inputs},
            )
        if resp.status_code == 202:
            snapshot_id = _snapshot_id(resp.json())
            if snapshot_id:
                return await _wait_for_snapshot(api_key=api_key, snapshot_id=snapshot_id)
            raise RateLimited("Bright Data returned 202 without snapshot_id")
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise CredsMissing(f"Bright Data authentication failed: HTTP {status}") from exc
        raise ScrapeError(f"Bright Data API failed: HTTP {status}") from exc
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise RateLimited(f"Bright Data API temporary network failure: {exc}") from exc

    snapshot_id = _snapshot_id(data)
    if snapshot_id:
        return await _wait_for_snapshot(api_key=api_key, snapshot_id=snapshot_id)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        records = data.get("data") or data.get("records") or data.get("result")
        if isinstance(records, list):
            return records
        return [data]
    return []


async def trigger_bright_data_records(
    *,
    dataset_id: str,
    inputs: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """discover 类任务用异步 `/datasets/v3/trigger` 发起 + 轮询快照。

    IG 按账号名 discover 是多页导航任务，同步 `/scrape` 会卡在 `starting`
    永不就绪（实测 15min+ 抓不到）；BD 要求 discover 走异步 `/trigger`
    （实测 ~60s ready）。TikTok/FB 的 collect-by-url 仍用同步 scrape。
    """
    api_key = _require_api_key()
    try:
        async with httpx.AsyncClient(timeout=settings.bright_data_timeout_seconds) as client:
            resp = await client.post(
                f"{_API_BASE}/trigger",
                params={"dataset_id": dataset_id, "include_errors": "true", **(params or {})},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=inputs,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise CredsMissing(f"Bright Data authentication failed: HTTP {status}") from exc
        raise ScrapeError(f"Bright Data trigger failed: HTTP {status}") from exc
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise RateLimited(f"Bright Data trigger temporary network failure: {exc}") from exc

    snapshot_id = _snapshot_id(resp.json())
    if not snapshot_id:
        raise RateLimited("Bright Data trigger returned no snapshot_id")
    return await _wait_for_snapshot(api_key=api_key, snapshot_id=snapshot_id)


def normalize_tiktok_records(records: list[dict[str, Any]]) -> list[ScrapedVideo]:
    videos: list[ScrapedVideo] = []
    seen: set[str] = set()
    for record in records:
        video_id = _first_str(record, "post_id", "video_id", "id")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        username = _first_str(record, "profile_username", "author", "username")
        canonical_url = (
            f"https://www.tiktok.com/@{username.lstrip('@')}/video/{video_id}"
            if username
            else _first_str(record, "url", "post_url", "webpage_url")
        )
        videos.append(
            ScrapedVideo(
                video_id=video_id,
                video_url=canonical_url,
                description=_first_str(record, "description", "text", "title"),
                thumbnail_url=_first_str(record, "cover", "thumbnail", "thumbnail_url"),
                duration_seconds=_to_int(record.get("video_duration") or record.get("duration"))
                or None,
                play_count=_to_int(record.get("play_count") or record.get("view_count")),
                like_count=_to_int(
                    record.get("digg_count") or record.get("like_count") or record.get("likes")
                ),
                comment_count=_to_int(record.get("comment_count") or record.get("num_comments")),
                share_count=_to_int(record.get("share_count") or record.get("num_shares")),
                published_at=_to_datetime(record.get("create_time") or record.get("date_posted")),
                raw=record,
            )
        )
    return videos


_TIKTOK_GONE_CODES = {
    "10221",
    "404",
    "not_found",
    "user_not_found",
    "account_gone",
    "dead_page",
}
_TIKTOK_GONE_PHRASES = (
    "not found",
    "not exist",
    "doesn't exist",
    "does not exist",
    "couldn't find",
    "could not find",
    "user banned",
    "user is banned",
    "profile unavailable",
    "account unavailable",
)


def tiktok_records_confirm_account_gone(records: list[dict[str, Any]]) -> bool:
    """Return True only when Bright Data records explicitly say profile is gone.

    Empty records are not enough: a valid account may have no public posts or the
    dataset may have returned no rows without a typed error.
    """
    for record in records:
        if not isinstance(record, dict):
            continue
        if _first_str(record, "post_id", "video_id"):
            return False

        code = _first_str(
            record,
            "error_code",
            "status_code",
            "statusCode",
            "code",
            "http_status",
        )
        if code and code.strip().lower() in _TIKTOK_GONE_CODES:
            return True

        text = " ".join(
            v
            for v in (
                _first_str(record, "error"),
                _first_str(record, "error_message"),
                _first_str(record, "message"),
                _first_str(record, "status_msg", "statusMsg"),
                _first_str(record, "reason"),
                _first_str(record, "status"),
            )
            if v
        ).lower()
        if text and any(phrase in text for phrase in _TIKTOK_GONE_PHRASES):
            return True
    return False


def _instagram_record_content_type(record: dict[str, Any]) -> str:
    raw = _first_str(record, "content_type", "post_type", "type") or ""
    raw_lower = raw.lower()
    url = _first_str(record, "url", "display_url", "post_url") or ""
    url_path = urlparse(url).path
    if "carousel" in raw_lower:
        return "carousel"
    if "reel" in raw_lower or "/reel/" in url_path:
        return "reel"
    if (
        record.get("video_view_count")
        or record.get("video_play_count")
        or record.get("video_duration")
    ):
        return "reel"
    return "post"


def _is_instagram_post_record(record: dict[str, Any]) -> bool:
    url = _first_str(record, "url", "display_url", "post_url")
    if url:
        path = urlparse(url).path
        if "/p/" in path or "/reel/" in path:
            return True
    return bool(_first_str(record, "post_id", "shortcode"))


def _instagram_reel_play_count(record: dict[str, Any]) -> int:
    return _to_int(
        record.get("video_view_count")
        or record.get("video_play_count")
        or record.get("play_count")
        or record.get("views")
        or record.get("view_count")
    )


def _flatten_instagram_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for record in records:
        posts = record.get("posts")
        if isinstance(posts, list):
            inherited = {
                "account": record.get("account")
                or record.get("user_name")
                or record.get("profile_name"),
                "avatar_url": record.get("avatar_url"),
                "bio": record.get("bio"),
                "biography": record.get("biography"),
                "followers": record.get("followers"),
                "follower_count": record.get("follower_count"),
                "following_count": record.get("following_count"),
                "following": record.get("following"),
                "full_name": record.get("full_name"),
                "profile_name": record.get("profile_name"),
                "posts_count": record.get("posts_count"),
                "profile_image_link": record.get("profile_image_link"),
                "profile_pic_url": record.get("profile_pic_url"),
                "user_name": record.get("user_name"),
                "user_posted": record.get("user_posted"),
                "username": record.get("username"),
            }
            for post in posts:
                if isinstance(post, dict):
                    flat.append({**inherited, **post})
        elif _is_instagram_post_record(record):
            flat.append(record)
    return flat


def normalize_instagram_records(
    records: list[dict[str, Any]],
) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    source_records = records
    records = _flatten_instagram_records(records)
    first = records[0] if records else (source_records[0] if source_records else {})
    profile = ScrapedProfile(
        follower_count=_to_int(first.get("followers") or first.get("follower_count")),
        following_count=_to_int(first.get("following") or first.get("following_count")),
        video_count=_to_int(first.get("posts_count") or first.get("media_count")),
        display_name=_first_str(first, "full_name", "user_posted", "account", "username"),
        avatar_url=_first_str(first, "profile_image_link", "profile_pic_url", "avatar_url"),
        bio=_first_str(first, "biography", "bio"),
    )

    videos: list[ScrapedVideo] = []
    seen: set[str] = set()
    for record in records:
        video_id = _first_str(record, "post_id", "id", "shortcode")
        if not video_id or video_id in seen:
            continue
        content_type = _instagram_record_content_type(record)
        play_count = _instagram_reel_play_count(record) if content_type == "reel" else 0
        if content_type == "reel" and play_count <= 0:
            continue
        seen.add(video_id)
        videos.append(
            ScrapedVideo(
                video_id=video_id,
                video_url=_first_str(record, "url", "display_url", "post_url"),
                description=_first_str(record, "description", "caption", "text"),
                thumbnail_url=_first_str(record, "thumbnail", "display_image", "image_url"),
                duration_seconds=(
                    _to_int(
                        record.get("video_duration")
                        or record.get("duration")
                        or record.get("video_length")
                    )
                    or None
                    if content_type == "reel"
                    else None
                ),
                play_count=play_count,
                like_count=_to_int(record.get("likes") or record.get("like_count")),
                comment_count=_to_int(
                    record.get("num_comments")
                    or record.get("comment_count")
                    or record.get("comments")
                ),
                share_count=0,
                published_at=_to_datetime(
                    record.get("date_posted") or record.get("taken_at") or record.get("datetime")
                ),
                content_type=content_type,
                raw=record,
            )
        )
    return profile, videos


def normalize_youtube_records(records: list[dict[str, Any]]) -> list[ScrapedVideo]:
    videos: list[ScrapedVideo] = []
    seen: set[str] = set()
    for record in records:
        video_id = _first_str(record, "video_id", "shortcode", "id")
        if not video_id or video_id in seen:
            continue
        url = _first_str(record, "url", "webpage_url")
        duration = _to_int(record.get("video_length") or record.get("duration"))
        if not _is_youtube_short(url, duration):
            continue
        seen.add(video_id)
        videos.append(
            ScrapedVideo(
                video_id=video_id,
                video_url=url or f"https://www.youtube.com/shorts/{video_id}",
                description=_first_str(record, "description", "title"),
                thumbnail_url=_first_str(record, "preview_image", "thumbnail", "thumbnail_url"),
                duration_seconds=duration or None,
                play_count=_to_int(record.get("views") or record.get("view_count")),
                like_count=_to_int(record.get("likes") or record.get("like_count")),
                comment_count=_to_int(record.get("num_comments") or record.get("comment_count")),
                share_count=0,
                published_at=_to_datetime(record.get("date_posted") or record.get("timestamp")),
                raw=record,
            )
        )
    return videos


def _is_youtube_short(url: str | None, duration_seconds: int) -> bool:
    if url and "/shorts/" in urlparse(url).path:
        return True
    return 0 < duration_seconds <= 180


async def fetch_tiktok_videos_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> list[ScrapedVideo]:
    records = await fetch_tiktok_records_via_bright_data(username, max_count)
    videos = normalize_tiktok_records(records)
    logger.info("tiktok.bright_data.done", username=username, count=len(videos))
    return videos


async def fetch_tiktok_records_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    return await scrape_bright_data_records(
        dataset_id=settings.bright_data_tiktok_posts_dataset_id,
        inputs=[
            {
                "url": _tiktok_profile_url(username),
                "num_of_posts": max_count or settings.max_videos_recent,
            }
        ],
        params={},
    )


async def fetch_instagram_videos_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    # discover 必须走异步 /trigger（同步 /scrape 会卡 starting 永不就绪）
    records = await trigger_bright_data_records(
        dataset_id=settings.bright_data_instagram_reels_dataset_id,
        inputs=[{"user_name": username.lstrip("@").strip("/")}],
        params={"type": "discover_new", "discover_by": "user_name"},
    )
    profile, videos = normalize_instagram_records(records)
    if max_count and max_count > 0:
        videos = videos[:max_count]
    logger.info("instagram.bright_data.done", username=username, count=len(videos))
    return profile, videos


async def fetch_youtube_shorts_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> list[ScrapedVideo]:
    records = await scrape_bright_data_records(
        dataset_id=settings.bright_data_youtube_videos_dataset_id,
        inputs=[{"url": _shorts_url(username)}],
        params={"type": "discover_new", "discover_by": "url"},
    )
    videos = normalize_youtube_records(records)
    if max_count and max_count > 0:
        videos = videos[:max_count]
    logger.info("youtube.bright_data.done", username=username, count=len(videos))
    return videos
