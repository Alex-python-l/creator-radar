"""FB share / pfbid URL → normalized identifier resolver。

给 web 端 Server Action 在批量导入时调用:用户粘贴的 share 短链
不含 username,必须 follow 302 拿真实 profile URL 再二次解析。

复用 scraper 已搞定 FB anti-bot 的基础设施(_httpx_get + Chrome 头 +
代理池),Vercel 端无此基础设施,放过去会被风控。
"""
import re

import httpx
import structlog

from app.config import settings
from app.handlers.scrape import _clean_username
from app.scrapers.facebook import _httpx_get
from app.scrapers.proxies import effective_proxy_pool, pooled_proxy_candidates

logger = structlog.get_logger(__name__)

# resolve 是一次性请求,不抓正文 — 试 3 个代理就够,比 reels 抓取的 4 次少
_MAX_PROXY_ATTEMPTS = 3
# fb://profile/<id> 是 FB 给原生 App 的 OG deep link(al:android:url / al:ios:url),
# 任何 profile 页都有,精确指向账号纯数字 id;"userID" 做次选。
# 旧正则用 .{0,3000}? 大跨度匹配会跨进 cover_photo.photo.id 抽错,已弃用。
_FB_PROFILE_DEEPLINK_RE = re.compile(r"fb://profile/(\d+)")
_FB_USERID_RE = re.compile(r'"userID"\s*:\s*"(\d+)"')
_NUMERIC_PROFILE_PATH_RE = re.compile(
    r"^(https?://(?:www\.|m\.)?facebook\.com)/(\d{5,})(?:[/?#].*)?$",
    re.I,
)


async def resolve_fb_identifier(raw_url: str) -> tuple[str, str]:
    """跟 FB 302 拿 final URL,提取 normalized identifier(给入库用)。

    Returns:
        (identifier, final_url) — identifier 优先是纯数字 profile id(fb://profile 抽取);
        HTML 抽取失败时从 final URL 兜底(profile.php?id= / people numeric → 纯数字;
        pfbid / vanity 无数字时退回 _clean_username 形态)。

    Raises:
        RuntimeError — 全代理失败 / final URL 无可提取 identifier(登录墙等)
    """
    lookup_url = _profile_php_lookup_url(raw_url)
    proxy_pool = effective_proxy_pool(settings.facebook_proxy_pool, settings.common_proxy_pool)
    proxies = pooled_proxy_candidates(
        proxy_pool, settings.proxy_url, _MAX_PROXY_ATTEMPTS
    )
    last_err = "no proxy attempted"
    for attempt, proxy in enumerate(proxies, start=1):
        try:
            resp = await _httpx_get(lookup_url, proxy)
            if resp.status_code >= 400:
                last_err = f"upstream HTTP {resp.status_code}"
                logger.info("resolve.upstream_err", attempt=attempt, status=resp.status_code)
                continue
            final_url = str(resp.url)
            ident = _stable_identifier_from_html(resp.text) or _identifier_from_url(final_url)
            if ident:
                logger.info("resolve.ok", raw=raw_url, final=final_url, identifier=ident)
                return ident, final_url
            last_err = f"no extractable identifier in final URL: {final_url}"
        except (httpx.HTTPError, OSError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            logger.info("resolve.attempt_failed", attempt=attempt, err=last_err)
    raise RuntimeError(f"fb resolve failed after {attempt} attempts: {last_err}")


def _profile_php_lookup_url(url: str) -> str:
    """FB 数字路径会重定向登录墙;改走等价的 profile.php?id= 入口。"""
    m = _NUMERIC_PROFILE_PATH_RE.match(url.strip())
    if not m:
        return url
    return f"{m.group(1)}/profile.php?id={m.group(2)}"


def _identifier_from_url(url: str) -> str | None:
    """复用 _clean_username 的 FB 三规则;返回 None 表示无法提取。"""
    cleaned = _clean_username(url, "facebook")
    # _clean_username 兜底是 lstrip("@") — 没匹配到任何规则时原样返回(可能仍是 URL)
    if cleaned == url or cleaned.startswith(("http://", "https://", "www.")):
        return None
    return cleaned


def _stable_identifier_from_html(html: str) -> str | None:
    """从 FB profile HTML 抽稳定纯数字 profile id。

    首选 fb://profile/<id>(OG deep link,普适且精确),次选 "userID":"<id>"。
    返回纯数字 id;抽不到返回 None,由 _identifier_from_url 从 final URL 兜底。
    """
    for pattern in (_FB_PROFILE_DEEPLINK_RE, _FB_USERID_RE):
        m = pattern.search(html or "")
        if m:
            return m.group(1)
    return None
