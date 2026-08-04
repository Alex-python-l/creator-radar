"""Bright Data Facebook Reels fallback client.

Native FB scraping stays the primary path. This module is only used when
anonymous HTML listing is blocked by FB login walls / rate limits.
"""
import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.scrapers.errors import CredsMissing, RateLimited, ScrapeError
from app.scrapers.tiktok import ScrapedVideo
from app.scrapers.types import ScrapedProfile

logger = structlog.get_logger(__name__)

_API_BASE = "https://api.brightdata.com/datasets/v3"
_READY_STATUSES = {"ready", "done", "completed", "complete", "success"}
_FAILED_STATUSES = {"failed", "error", "canceled", "cancelled"}


def is_bright_data_configured() -> bool:
    return bool((settings.bright_data_api_key or "").strip())


def _require_api_key() -> str:
    api_key = (settings.bright_data_api_key or "").strip()
    if not api_key:
        raise CredsMissing("BRIGHT_DATA_API_KEY is required for Bright Data FB fallback")
    return api_key


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


def _video_id_from_record(record: dict[str, Any]) -> str | None:
    for key in ("shortcode", "video_id", "post_id", "id"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    url = str(record.get("url") or record.get("post_url") or "").strip()
    if not url:
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "reel" in parts:
        idx = parts.index("reel")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1] if parts else None


def _first_str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _likes_from_record(record: dict[str, Any]) -> int:
    for key in ("likes", "like_count", "num_likes", "reaction_count"):
        value = _to_int(record.get(key))
        if value:
            return value

    total = 0
    for item in record.get("count_reactions_type") or []:
        if isinstance(item, dict):
            total += _to_int(item.get("reaction_count") or item.get("count") or item.get("num"))
    if total:
        return total

    num_likes_type = record.get("num_likes_type")
    if isinstance(num_likes_type, dict):
        return _to_int(num_likes_type.get("num") or num_likes_type.get("count"))
    return 0


def normalize_bright_data_records(
    records: list[dict[str, Any]],
) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    """Convert Bright Data FB Reels records to the scraper's internal shape."""
    first = records[0] if records else {}
    profile = ScrapedProfile(
        follower_count=_to_int(first.get("page_followers") or first.get("followers")),
        following_count=0,
        video_count=0,
        display_name=_first_str(first, "page_name", "profile_name", "author_name"),
        avatar_url=_first_str(first, "page_logo", "profile_image", "avatar_url"),
        bio=_first_str(first, "page_intro", "bio"),
    )

    videos: list[ScrapedVideo] = []
    seen: set[str] = set()
    for record in records:
        video_id = _video_id_from_record(record)
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        videos.append(
            ScrapedVideo(
                video_id=video_id,
                video_url=_first_str(record, "url", "post_url", "webpage_url"),
                description=_first_str(record, "content", "description", "text", "title"),
                thumbnail_url=_first_str(record, "thumbnail", "thumbnail_url", "image"),
                duration_seconds=_to_int(record.get("length") or record.get("duration")) or None,
                play_count=_to_int(
                    record.get("play_count")
                    or record.get("video_view_count")
                    or record.get("view_count")
                ),
                like_count=_likes_from_record(record),
                comment_count=_to_int(record.get("num_comments") or record.get("comment_count")),
                share_count=_to_int(record.get("num_shares") or record.get("share_count")),
                published_at=_to_datetime(
                    record.get("date_posted") or record.get("timestamp") or record.get("published_at")
                ),
                raw=record,
            )
        )
    return profile, videos


async def fetch_facebook_reels_via_bright_data(
    profile_url: str,
    max_count: int | None = None,
) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    """Fetch FB Reels through Bright Data's synchronous scraper API."""
    api_key = _require_api_key()
    dataset_id = settings.bright_data_facebook_reels_dataset_id
    try:
        records, snapshot_id = await _post_scrape(
            api_key=api_key,
            dataset_id=dataset_id,
            profile_url=profile_url,
            max_count=max_count,
        )
        if records is None and snapshot_id:
            records = await _wait_for_snapshot(api_key=api_key, snapshot_id=snapshot_id)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise CredsMissing(f"Bright Data authentication failed: HTTP {status}") from exc
        raise ScrapeError(f"Bright Data API failed: HTTP {status}") from exc
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise RateLimited(f"Bright Data API temporary network failure: {exc}") from exc

    records = records or []
    profile, videos = normalize_bright_data_records(records)
    logger.info(
        "fb.bright_data.done",
        profile_url=profile_url,
        count=len(videos),
        snapshot_id=snapshot_id,
    )
    return profile, videos


async def _post_scrape(
    *,
    api_key: str,
    dataset_id: str,
    profile_url: str,
    max_count: int | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    payload: dict[str, Any] = {
        "input": [
            {
                "url": profile_url,
                "num_of_posts": max_count or settings.max_videos_recent,
            }
        ]
    }
    async with httpx.AsyncClient(timeout=settings.bright_data_timeout_seconds) as client:
        resp = await client.post(
            f"{_API_BASE}/scrape",
            params={
                "dataset_id": dataset_id,
                "format": "json",
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code == 202:
        data = resp.json()
        return None, _snapshot_id(data)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        snapshot_id = _snapshot_id(data)
        if snapshot_id:
            return None, snapshot_id
        records = data.get("data") or data.get("records") or data.get("result")
        if isinstance(records, list):
            return records, None
        return [data], None
    return [], None


# progress 报 ready 后，数据文件可能还在打包，此时 /snapshot 返回
# {"status":"building","message":"...not ready..."} 而非记录列表 —— 需继续轮询。
_SNAPSHOT_REAL_FIELDS = ("post_id", "video_id", "shortcode", "url", "id", "account", "posts")


def _snapshot_still_building(records: list[dict[str, Any]]) -> bool:
    if len(records) != 1 or not isinstance(records[0], dict):
        return False
    rec = records[0]
    if any(k in rec for k in _SNAPSHOT_REAL_FIELDS):
        return False
    blob = f"{rec.get('status', '')} {rec.get('message', '')}".lower()
    return "building" in blob or "not ready" in blob or "not_ready" in blob


async def _wait_for_snapshot(api_key: str, snapshot_id: str) -> list[dict[str, Any]]:
    for _ in range(settings.bright_data_max_polls):
        status = (await _get_progress(api_key=api_key, snapshot_id=snapshot_id)).lower()
        if status in _READY_STATUSES:
            data = await _download_snapshot(api_key=api_key, snapshot_id=snapshot_id)
            if not _snapshot_still_building(data):
                return data
        elif status in _FAILED_STATUSES:
            raise ScrapeError(f"Bright Data snapshot {snapshot_id} failed with status {status}")
        await asyncio.sleep(settings.bright_data_poll_interval_seconds)
    raise RateLimited(f"Bright Data snapshot {snapshot_id} not ready")


async def _get_progress(*, api_key: str, snapshot_id: str) -> str:
    async with httpx.AsyncClient(timeout=settings.bright_data_timeout_seconds) as client:
        resp = await client.get(
            f"{_API_BASE}/progress/{snapshot_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return str(data.get("status") or data.get("state") or "unknown")
    return "unknown"


async def _download_snapshot(*, api_key: str, snapshot_id: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=settings.bright_data_timeout_seconds) as client:
        resp = await client.get(
            f"{_API_BASE}/snapshot/{snapshot_id}",
            params={"format": "json"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        records = data.get("data") or data.get("records") or data.get("result")
        if isinstance(records, list):
            return records
        return [data]
    return []


def _snapshot_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("snapshot_id", "snapshot", "id"):
        value = data.get(key)
        # BD 异步快照 id 前缀实测是 sd_(如 sd_mpqp5zu21yfl0txlpv);旧 s_ 也保留兼容
        if isinstance(value, str) and value.startswith(("s_", "sd_")):
            return value
    return None
