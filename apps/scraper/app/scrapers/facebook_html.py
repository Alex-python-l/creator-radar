"""解析 FB Page reels 页 HTML(httpx 直拉的原文)。

纯函数 — 不联网,喂 fixture 直接单测。

设计:
- reel ID 提取走 `"video_id":"(\\d+)"` 正则。FB 渲染时把 reel ID 嵌进
  内联 JSON,这个 key 比 URL 形式(/reel/<id>)更稳(URL 形式可能省略)。
- profile 字段拿到就好,拿不到给默认值;不做强约束(FB 不同 page 渲染结构略有差)。
- 异常分类是为了让 handler 写正确 error_code:
  * RateLimited → transient,worker 退避后重试
  * AccountGone → permanent
"""
import re

from app.scrapers.errors import AccountGone, RateLimited
from app.scrapers.types import ScrapedProfile

# FB 渲染软封时返回 1.5KB <title>Error</title> 页(代理 IP 撞反爬常见)
_SOFT_BLOCK_TITLE = re.compile(r"<title>\s*Error\s*</title>", re.I)

# FB 强登录墙 — 直接渲染登录页(没 redirect 时)
_LOGIN_WALL_RE = re.compile(r"log\s*(?:in|into)\s*facebook|action=[\"']/login/", re.I)

# Page 不存在的标准文案 — U+0027(ASCII 直引) + U+2019(弯引号)
_NOT_FOUND_RE = re.compile(r"This content isn['’']t available right now|content not found", re.I)

# Reel ID:FB 内联 JSON 里 "video_id":"<12-19 位数字>"
_REEL_ID_RE = re.compile(r'"video_id":"(\d{12,19})"')

# 真账号锚点 — 任一存在即认定 HTML 渲染了 user/page profile,反之是通用页/重定向。
# 2026-05 事故:不存在的 numeric profile (如 id 438) 被 FB redirect 到通用页,
# 旧版没此锚点检查时把页面里 JS bundle 的 "name":"connection_quality" 当 display_name,
# fetch_logs 反复记 success 0/0,误导调度继续刷新幽灵账号。
_PROFILE_SENTINEL_RE = re.compile(r'profile_picture(?:_for_sticky_bar)?"\s*:|profile_header_renderer')

# Profile name — 先试 nested user/node/owner.name
_NAME_RE = re.compile(r'"(?:user|node|owner)"\s*:\s*\{[^}]{0,200}"name"\s*:\s*"([^"]+)"', re.I)
# 备用 name(直接 "name" 顶层,取第一个)
_NAME_FALLBACK_RE = re.compile(r'"name"\s*:\s*"([^"]+)"')

# profile_picture URI — FB JSON 里 URI 用 \/ 转义,需要 unescape
_AVATAR_RE = re.compile(
    r'"profile_picture(?:_for_sticky_bar)?"\s*:\s*\{[^}]{0,1000}"uri"\s*:\s*"([^"]+)"',
    re.I,
)

# follower_count 在 page meta JSON 里
_FOLLOWER_RE = re.compile(
    r'"(?:follower_count|follower_count_for_display|page_likers)"\s*:\s*(\d+)',
    re.I,
)


def parse_page_html(html: str) -> tuple[ScrapedProfile, list[str]]:
    """从 Page reels 页 HTML 抽 profile + 首屏 reel ID 列表。

    Raises:
        RateLimited — 软封 Error 页或登录墙
        AccountGone — Page 不存在
    """
    # 反爬检测优先
    if _SOFT_BLOCK_TITLE.search(html):
        raise RateLimited("fb soft-block: <title>Error</title>")
    if _LOGIN_WALL_RE.search(html):
        raise RateLimited("fb login wall (rendered, no redirect)")
    if _NOT_FOUND_RE.search(html):
        raise AccountGone("fb page not found")

    # 提取 unique reel ID(按 HTML 出现顺序 dedup)
    seen: set[str] = set()
    reel_ids: list[str] = []
    for m in _REEL_ID_RE.finditer(html):
        rid = m.group(1)
        if rid not in seen:
            seen.add(rid)
            reel_ids.append(rid)

    # 无 reel 且无 profile sentinel → 通用页/不存在账号,避免把 JS bundle 名当 display_name
    if not reel_ids and not _PROFILE_SENTINEL_RE.search(html):
        raise AccountGone("fb generic page (no reels, no profile sentinel)")

    # Profile
    name = _extract_name(html)
    avatar = _extract_avatar(html)
    follower = _extract_first_int(_FOLLOWER_RE, html)

    profile = ScrapedProfile(
        follower_count=follower,
        following_count=0,  # FB 不暴露
        video_count=0,      # FB 不暴露 page 总视频数,首版不抓
        display_name=name,
        avatar_url=avatar,
        bio=None,           # FB Page 没有 IG 风格 bio
    )
    return profile, reel_ids


def _extract_first_int(pattern: re.Pattern[str], html: str) -> int:
    m = pattern.search(html)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def _extract_name(html: str) -> str | None:
    """page name 提取 — 先试 nested user.name,失败 fallback 到任意顶层 name。"""
    m = _NAME_RE.search(html)
    if m:
        return m.group(1)
    # fallback: 第一个出现的 "name":"..."
    m = _NAME_FALLBACK_RE.search(html)
    if m:
        return m.group(1)
    return None


def _extract_avatar(html: str) -> str | None:
    """提取 profile_picture URI,处理 FB JSON 里的 \\/ 转义。"""
    m = _AVATAR_RE.search(html)
    if not m:
        return None
    uri = m.group(1)
    # FB 在 JSON 里将 / 转义为 \/,反转义还原
    uri = uri.replace("\\/", "/")
    if uri.startswith("http"):
        return uri
    return None
