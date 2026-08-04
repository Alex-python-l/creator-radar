"""代理选择：IG 固定单出口（sticky）；匿名平台池化轮换。

为什么分开：
- IG 把账号 session 与 IP 绑定 —— 短时间换 IP 会被当作账号被盗，立即
  触发 ChallengeRequired / LoginRequired。所以 IG 必须 sticky。
- TikTok / Facebook / YouTube（yt-dlp / 匿名浏览）IP 池化耐受，反而能
  分散风控压力，减少单 IP 被 429 / 登录墙。

env 配置（见 app/config.py）：
- INSTAGRAM_PROXY_URL  → IG 单出口（可不配，缺省时 IG 自动取 COMMON_PROXY_POOL 第一条）
- COMMON_PROXY_POOL    → 所有平台共用池，逗号分隔；也是 IG sticky 兜底来源
- TIKTOK_PROXY_POOL 等 → 平台专属池，逗号分隔，优先于 COMMON_PROXY_POOL
- PROXY_URL            → 兼容旧字段，所有池缺省时使用（退化为单代理）
"""
import random


def normalize_proxy_url(raw: str | None) -> str | None:
    """空 → None；已带 scheme（http://、https://、socks5://、socks5h://）→ 原样；
    裸 user:pass@host:port 或 host:port → 补 http://（kookeey 等 HTTP 代理默认）。"""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if "://" in raw:
        return raw
    return f"http://{raw}"


def _parse_pool(raw: str | None) -> list[str]:
    """逗号分隔的池子拆成列表；每条 normalize，丢弃空项。"""
    if not raw:
        return []
    out: list[str] = []
    for item in raw.split(","):
        n = normalize_proxy_url(item)
        if n:
            out.append(n)
    return out


def instagram_proxy(
    instagram_proxy_url: str | None,
    proxy_url: str | None,
    common_pool: str | None = None,
) -> str | None:
    """IG 的 sticky 代理。优先级：INSTAGRAM_PROXY_URL → PROXY_URL → common_pool 第一条。

    回退到 common_pool 时**固定取第一条**（不随机），保 sticky 语义：
    IG 把 session 与 IP 绑定，跨调用换 IP 会被当账号被盗触发 challenge。
    """
    dedicated = normalize_proxy_url(instagram_proxy_url) or normalize_proxy_url(proxy_url)
    if dedicated:
        return dedicated
    pool = _parse_pool(common_pool)
    return pool[0] if pool else None


def effective_proxy_pool(platform_pool: str | None, common_pool: str | None) -> str | None:
    """平台专属池优先；未配置时回退到 COMMON_PROXY_POOL。"""
    if _parse_pool(platform_pool):
        return platform_pool
    if _parse_pool(common_pool):
        return common_pool
    return None


def pick_tiktok_proxy(
    tiktok_proxy_pool: str | None, proxy_url: str | None
) -> str | None:
    """TikTok 每次抓取调一次：池非空 → 随机挑一条；池空 → 回退到旧 PROXY_URL。
    随机比轮询更稳：worker 重启不丢游标，多 worker 也不需要协调。"""
    pool = _parse_pool(tiktok_proxy_pool)
    if pool:
        return random.choice(pool)
    return normalize_proxy_url(proxy_url)


def pooled_proxy_candidates(
    pool: str | None, proxy_url: str | None, limit: int
) -> list[str | None]:
    """从池子里随机取最多 limit 条**不同**的 IP,供失败时逐条换 IP 重试。

    池为空 → 回退到单个 proxy_url(可能是 [None],表示直连)。
    返回列表至少 1 项。

    适用所有"无 session 绑 IP 概念"的平台(TK / FB / YT)。
    IG 必须 sticky,用 `instagram_proxy()` 而不是这个。
    """
    parsed = _parse_pool(pool)
    if parsed:
        random.shuffle(parsed)
        return parsed[:limit]
    return [normalize_proxy_url(proxy_url)]
