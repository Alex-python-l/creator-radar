"""TikTok profile existence probe.

视频抓取用 yt-dlp；账号是否存在用主页 HTML 里的结构化 rehydration JSON。
TikTok 对存在/不存在主页都返回 HTTP 200，不能靠状态码判断。
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Literal

import httpx
import structlog

from app.config import settings
from app.scrapers.proxies import effective_proxy_pool, pooled_proxy_candidates

logger = structlog.get_logger(__name__)

TikTokProfileStatus = Literal["exists", "gone", "unknown"]

_REHYDRATION_RE = re.compile(
    r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s+type="application/json">'
    r"([\s\S]*?)</script>",
    re.I,
)
_PROFILE_PROBE_ATTEMPTS = 2
_GONE_STATUS_CODES = {10221, 10202}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class TikTokProfileProbeResult:
    status: TikTokProfileStatus
    status_code: int | None = None
    status_msg: str | None = None
    unique_id: str | None = None
    reason: str | None = None


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_tiktok_profile_html(body: str) -> TikTokProfileProbeResult:
    """Parse TikTok user-detail data from profile HTML."""
    m = _REHYDRATION_RE.search(body)
    if not m:
        return TikTokProfileProbeResult(status="unknown", reason="rehydration_json_missing")

    try:
        data = json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError:
        return TikTokProfileProbeResult(status="unknown", reason="rehydration_json_invalid")

    detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail")
    if not isinstance(detail, dict):
        return TikTokProfileProbeResult(status="unknown", reason="user_detail_missing")

    status_code = _to_int(detail.get("statusCode"))
    status_msg_raw = detail.get("statusMsg")
    status_msg = status_msg_raw if isinstance(status_msg_raw, str) else None
    user_info = detail.get("userInfo")
    user = user_info.get("user") if isinstance(user_info, dict) else None
    unique_id_raw = user.get("uniqueId") if isinstance(user, dict) else None
    unique_id = unique_id_raw if isinstance(unique_id_raw, str) and unique_id_raw else None

    if status_code == 0 and unique_id:
        return TikTokProfileProbeResult(
            status="exists",
            status_code=status_code,
            status_msg=status_msg,
            unique_id=unique_id,
            reason="user_detail_exists",
        )

    if status_code in _GONE_STATUS_CODES and not user_info:
        return TikTokProfileProbeResult(
            status="gone",
            status_code=status_code,
            status_msg=status_msg,
            reason="user_detail_gone",
        )

    return TikTokProfileProbeResult(
        status="unknown",
        status_code=status_code,
        status_msg=status_msg,
        reason="user_detail_unclassified",
    )


def _profile_url(username: str) -> str:
    return f"https://www.tiktok.com/@{username.lstrip('@').strip('/')}"


async def _fetch_profile_html(username: str, proxy: str | None) -> str:
    async with httpx.AsyncClient(
        proxy=proxy,
        follow_redirects=True,
        timeout=httpx.Timeout(20.0),
        headers=_HEADERS,
    ) as client:
        resp = await client.get(_profile_url(username))
        resp.raise_for_status()
        return resp.text


async def probe_tiktok_profile(username: str) -> TikTokProfileProbeResult:
    """Probe TikTok profile existence via public profile page HTML."""
    proxy_pool = effective_proxy_pool(settings.tiktok_proxy_pool, settings.common_proxy_pool)
    proxies = pooled_proxy_candidates(proxy_pool, settings.proxy_url, _PROFILE_PROBE_ATTEMPTS)
    last_reason = "no_attempt"
    for attempt, proxy in enumerate(proxies, start=1):
        try:
            body = await _fetch_profile_html(username, proxy)
            result = parse_tiktok_profile_html(body)
            logger.info(
                "tiktok.profile_probe.done",
                username=username,
                status=result.status,
                status_code=result.status_code,
                attempt=attempt,
            )
            if result.status != "unknown":
                return result
            last_reason = result.reason or "unknown"
        except (httpx.HTTPError, OSError) as exc:
            last_reason = str(exc)[:120]
            logger.warning(
                "tiktok.profile_probe.failed",
                username=username,
                attempt=attempt,
                error=last_reason,
            )

    return TikTokProfileProbeResult(status="unknown", reason=last_reason)
