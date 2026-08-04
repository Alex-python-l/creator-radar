"""抓取错误体系。每个子类带一个 100% 确定的 error_code，
写入 fetch_logs.error_code 供看板按类别展示。拿不准的错误不归类，
由 handler 兜底成 "unknown"（原文仍保留在 error_message）。"""


class ScrapeError(Exception):
    """所有可分类抓取错误的基类。默认 unknown。"""

    error_code: str = "unknown"


class AccountGone(ScrapeError):
    """账号不存在 / 被封（yt-dlp 报 secondary user ID）。"""

    error_code = "account_gone"


class RateLimited(ScrapeError):
    """被限流：yt-dlp/Playwright 显式 429 或 IG PleaseWaitFewMinutes。"""

    error_code = "rate_limited"


class AuthChallenge(ScrapeError):
    """IG 触发验证（邮箱/短信 challenge）。"""

    error_code = "auth_challenge"


class SessionExpired(ScrapeError):
    """IG 登录态失效，需重新登录（LoginRequired）。"""

    error_code = "login_required"


class CredsMissing(ScrapeError):
    """未配置平台账号/密码。"""

    error_code = "creds_missing"


class BrowserUnavailable(ScrapeError):
    """Playwright Chromium 二进制缺失 —— 部署环境问题，非平台封禁。"""

    error_code = "browser_unavailable"


class NetworkError(ScrapeError):
    """明确的连接重置/拒绝/传输层超时。"""

    error_code = "network_error"
