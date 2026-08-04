"""yt-dlp 抓 FB 单条 reel 详情。

yt-dlp 自身是同步 API(不支持 asyncio)。orchestrator 用
asyncio.to_thread() 把这个函数包到异步。

reactions(=likes)需要从 title 字符串 regex 提取 —— yt-dlp 没有
独立的 like_count 字段(FB 不在 reel 详情 JSON 里给这个数字)。
"""
import re
from datetime import UTC, datetime
from typing import Any

import structlog
from yt_dlp import YoutubeDL
from yt_dlp.utils import ExtractorError

from app.scrapers._kmb import parse_kmb
from app.scrapers.errors import RateLimited, ScrapeError
from app.scrapers.tiktok import ScrapedVideo  # 复用 TK 已定义的归一类型

logger = structlog.get_logger(__name__)


# title 形如 "4.8K views · 51K reactions | Foo on Reels"
# 抽 "51K reactions" 这一段的数字 + 缩写
_REACTIONS_RE = re.compile(r"(\d+(?:\.\d+)?[KMBkmb]?)\s+reactions", re.I)


def parse_reactions_from_title(title: str | None) -> int:
    """title 里没 'reactions' 段 → 返回 0(已知低互动 reel 没这字段)。"""
    if not title:
        return 0
    m = _REACTIONS_RE.search(title)
    if not m:
        return 0
    return parse_kmb(m.group(1))


def _reel_url(reel_id: str) -> str:
    return f"https://www.facebook.com/reel/{reel_id}/"


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_datetime(ts: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC)
    except (TypeError, ValueError):
        return None


def fetch_reel(reel_id: str, proxy: str | None) -> ScrapedVideo:
    """同步抓单条 reel。失败抛 RateLimited(transient,orchestrator 据此换 IP)。"""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if proxy:
        opts["proxy"] = proxy

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(_reel_url(reel_id), download=False)
    except ExtractorError as exc:
        # 包括 "Cannot parse data" 等间歇性失败 — 当 transient
        raise RateLimited(f"yt-dlp extractor failed: {exc}") from exc
    except Exception as exc:
        # 网络层 / 其他未分类 — 不归 transient 让上层兜底
        raise ScrapeError(f"yt-dlp unexpected: {exc}") from exc

    if not info:
        raise RateLimited("yt-dlp returned empty info")

    return ScrapedVideo(
        video_id=str(info.get("id", reel_id)),
        video_url=info.get("webpage_url"),
        description=info.get("description"),
        thumbnail_url=info.get("thumbnail"),
        duration_seconds=_to_int(info.get("duration")) or None,
        play_count=_to_int(info.get("view_count")),
        like_count=parse_reactions_from_title(info.get("title")),
        comment_count=0,  # FB reel 详情 JSON 不暴露
        share_count=0,    # FB reel 详情 JSON 不暴露
        published_at=_to_datetime(info.get("timestamp")),
        raw=info,
    )
