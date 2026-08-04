"""FB Page Reels 抓取 orchestrator。

两阶段:
[A] httpx 拉 reels 页 HTML → 提取 reel ID + profile
[C] 每条 reel 独立 yt-dlp 抓详情;失败换代理重试,单条失败不阻断其他

代理用 pooled_proxy_candidates(仿 TK),FB 无 session 绑 IP 概念。
"""
import asyncio
import random

import httpx
import structlog

from app.config import settings
from app.scrapers.bright_data_facebook import (
    fetch_facebook_reels_via_bright_data,
    is_bright_data_configured,
)
from app.scrapers.errors import RateLimited, ScrapeError
from app.scrapers.facebook_html import parse_page_html
from app.scrapers.facebook_reel import fetch_reel
from app.scrapers.proxies import effective_proxy_pool, pooled_proxy_candidates
from app.scrapers.tiktok import ScrapedVideo
from app.scrapers.types import ScrapedProfile

logger = structlog.get_logger(__name__)

# A 步 / C 步各自独立的重试次数 — 与 TK _MAX_PROXY_ATTEMPTS 对齐
_MAX_PROXY_ATTEMPTS = 4

# spike 实测:裸 UA 被 1.5KB Error 页软封,必须完整 Chrome header
_CHROME_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _page_url(identifier: str) -> str:
    """拼 FB Page reels 入口 URL。

    用 ?sk=reels_tab 而不是 /reels/ 子路径 —— 后者只对 vanity username 工作
    (数字 ID 返回 500、pfbid 返回 404),sk=reels_tab 是 FB 通用 reels tab,
    对任何 identifier 形态都返回 reels 列表 HTML(parse_page_html URL 无关)。

    支持三种 identifier 形态(_clean_username 已归一化):
    - vanity:        "Cristiano"               → facebook.com/Cristiano?sk=reels_tab
    - profile.php:   "profile.php?id=12345"    → facebook.com/profile.php?id=12345&sk=reels_tab
    - people path:   "people/Name/pfbid02XYZ"  → facebook.com/people/Name/pfbid02XYZ/?sk=reels_tab
    """
    s = identifier.lstrip("@").strip()
    if s.isdigit():
        return f"https://www.facebook.com/profile.php?id={s}&sk=reels_tab"
    base = f"https://www.facebook.com/{s}"
    if "?" in s:
        # 已带 query(profile.php?id=X),用 & 连接
        return f"{base}&sk=reels_tab"
    if "/" in s:
        # path-only 多级(people/<n>/<token>),补尾斜杠避免 FB 内部 redirect
        return f"{base}/?sk=reels_tab"
    return f"{base}?sk=reels_tab"


def _bright_data_profile_url(identifier: str) -> str:
    """Bright Data FB Reels API wants the account/profile URL, not `?sk=reels_tab`."""
    s = identifier.lstrip("@").strip()
    if s.isdigit():
        return f"https://www.facebook.com/profile.php?id={s}"
    base = f"https://www.facebook.com/{s}"
    if "?" in s:
        return base
    if "/" in s:
        return f"{base}/"
    return base


async def _httpx_get(url: str, proxy: str | None) -> httpx.Response:
    """httpx 拉一次,带 Chrome header + 可选代理。
    抽成单函数方便 mock。

    httpx >=0.28 用 AsyncClient(proxy=...);旧的 transport=AsyncHTTPTransport
    传 proxy 是 0.27 API,新版会报 deprecation warning。
    """
    async with httpx.AsyncClient(
        headers=_CHROME_HEADERS,
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
        proxy=proxy,
    ) as client:
        return await client.get(url)


async def _fetch_reel_async(reel_id: str, proxy: str | None) -> ScrapedVideo:
    """fetch_reel 是同步(yt-dlp),包到线程池避免阻塞 event loop。"""
    return await asyncio.to_thread(fetch_reel, reel_id, proxy)


async def fetch_facebook_videos(
    username: str,
    max_count: int | None = None,
) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    """抓 Page 首屏 reels(8-20 条)。

    max_count: 截断到这么多条;None / 0 → 拿全部首屏。
    """
    username = username.lstrip("@").strip()

    # ---------- A 步:列 reel ID + profile ----------
    proxy_pool = effective_proxy_pool(settings.facebook_proxy_pool, settings.common_proxy_pool)
    proxies_a = pooled_proxy_candidates(proxy_pool, settings.proxy_url, _MAX_PROXY_ATTEMPTS)
    last_a_err: str = "no attempt"
    profile: ScrapedProfile | None = None
    reel_ids: list[str] = []
    a_succeeded = False
    for attempt, proxy in enumerate(proxies_a, start=1):
        try:
            resp = await _httpx_get(_page_url(username), proxy)
            resp.raise_for_status()
            profile, reel_ids = parse_page_html(resp.text)
            logger.info(
                "fb.html.ok",
                username=username,
                attempt=attempt,
                reel_count=len(reel_ids),
            )
            a_succeeded = True
            break
        except (RateLimited, httpx.TransportError) as exc:
            last_a_err = str(exc)[:200]
            logger.warning("fb.html.transient", username=username, attempt=attempt, error=last_a_err)
            continue
        except Exception as exc:
            # AccountGone / 其他 fatal — 不重试直接抛,顺手 log context
            logger.error(
                "fb.html.fatal",
                username=username,
                attempt=attempt,
                error=str(exc)[:200],
            )
            raise
    if not a_succeeded:
        if is_bright_data_configured():
            logger.warning(
                "fb.html.fallback_bright_data",
                username=username,
                error=last_a_err,
            )
            return await fetch_facebook_reels_via_bright_data(
                _bright_data_profile_url(username),
                max_count=max_count,
            )
        raise RateLimited(f"fb html fetch failed after {len(proxies_a)} attempt(s): {last_a_err}")
    assert profile is not None  # type narrowing for type checker; a_succeeded=True 蕴含 profile 已赋值

    # max_count 截断
    if max_count and max_count > 0:
        reel_ids = reel_ids[:max_count]

    if not reel_ids:
        # Page 没发过 reel — 不当 error
        return profile, []

    # ---------- C 步:逐条抓详情 ----------
    # 每条 reel 独立调一次 pooled_proxy_candidates 拿 fresh shuffle —— 不同 reel
    # 用不同 proxy 序列,分散单 IP 压力(否则每条都先撞同一条 proxy)。
    videos: list[ScrapedVideo] = []
    for reel_id in reel_ids:
        proxies_c_pool = pooled_proxy_candidates(proxy_pool, settings.proxy_url, _MAX_PROXY_ATTEMPTS)
        # 抖动 1-2s 防 burst
        await asyncio.sleep(random.uniform(1, 2))
        success = False
        last_c_err = "no attempt"
        for c_attempt, c_proxy in enumerate(proxies_c_pool, start=1):
            try:
                video = await _fetch_reel_async(reel_id, c_proxy)
                videos.append(video)
                success = True
                break
            except RateLimited as exc:
                last_c_err = str(exc)[:200]
                logger.warning(
                    "fb.reel.transient",
                    reel_id=reel_id,
                    attempt=c_attempt,
                    error=last_c_err,
                )
                continue
            except ScrapeError as exc:
                # 非 transient → 不重试,跳过
                logger.warning("fb.reel.permanent", reel_id=reel_id, error=str(exc)[:200])
                break
        if not success:
            logger.warning("fb.reel.skipped", reel_id=reel_id, last_error=last_c_err)

    if not videos and reel_ids:
        # 列表拿到了但一条都没抓成 — 整体当 transient(rate_limited)
        raise RateLimited(f"fb all {len(reel_ids)} reels failed to fetch")

    logger.info("fb.fetch.done", username=username, found=len(videos), requested=len(reel_ids))
    return profile, videos
