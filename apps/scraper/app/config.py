from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 .env 或环境变量读配置。仓库根的 .env.example 是字段清单的来源。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 数据库 — 本地 docker Postgres，部署时换 Neon
    # 接受裸 postgresql:// URL（与 web 共用一份），内部加 asyncpg 后缀
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/short_video_monitor"

    # 抓取代理 —— 五档配置：
    # - instagram_proxy_url: IG 单出口（**必须 sticky**，IG 把短时间换 IP 当账号被盗
    #   触发 challenge / login_required）
    # - common_proxy_pool: TK / FB / YT 共用代理池，逗号分隔；平台专属池未配置时使用
    # - tiktok_proxy_pool: TK 代理池，逗号分隔多条；每次抓取随机挑一条分散流量
    # - facebook_proxy_pool: FB 代理池，逗号分隔多条；FB 无 session 绑 IP 概念，
    #   用池化轮换分散流量（跟 TK 一致，不像 IG 必须 sticky）
    # - youtube_proxy_pool: YT 代理池，逗号分隔多条；无登录态，跟 TK/FB 一样池化轮换
    # - proxy_url: 兼容旧字段，前面字段未设时所有平台都用它（退化为单代理 sticky）
    # 代理字符串可带 scheme（http://user:pass@host:port）也可裸 user:pass@host:port，
    # 后者由 scraper 内的 _normalize_proxy_url 自动补 http://。
    proxy_url: str | None = None
    instagram_proxy_url: str | None = None
    common_proxy_pool: str | None = None
    tiktok_proxy_pool: str | None = None
    facebook_proxy_pool: str | None = None
    youtube_proxy_pool: str | None = None

    # Instagram 凭据（用 instagrapi；device 持久化到 data/ig-instagrapi.json）
    # 强烈建议用专门测试小号，不要用主号或受过警告的号
    # - sessionid：买号自带的已登录 session，首次启动直接复用（不碰密码/2FA，
    #   风控概率最低）。从 cookies 里取 sessionid 的值（: 不要 URL 编码成 %3A）
    # - totp_seed：账号 2FA 密钥，session 失效后用账密重登时生成验证码
    instagram_username: str | None = None
    instagram_password: str | None = None
    instagram_sessionid: str | None = None
    instagram_totp_seed: str | None = None

    # 抓取节流（防风控）— 适用于 IG 和 TikTok 的 pre-fetch random delay
    # 每个账号抓取前 random.uniform(0, N) 秒延迟，让请求间隔不均匀，更像真人
    # 调到 180s 是因为之前 TikTok 频繁触发 HTTP 429 限流
    min_fetch_interval_seconds: int = 180

    # 每平台并发 worker 数（同平台起多条 lane 并行认领 jobs；worker.py 的
    # FOR UPDATE SKIP LOCKED 保证不重复认领）。默认 1 = 原串行行为。
    # 注意：多 worker 需配套多代理 IP，否则同 IP 并发抓取更易触发风控。
    # IG 走 Bright Data（不登录、无 sticky 约束）后可安全并发。
    tiktok_workers: int = 1
    facebook_workers: int = 1
    instagram_workers: int = 1
    youtube_workers: int = 1

    # 单次抓取拉多少条视频
    # full：账号有多少拉多少，无上限（None / 0 → 拉全部）
    # recent：每日定时，够覆盖最近 7 天即可
    max_videos_recent: int = 30

    # Bright Data Web Scraper API — FB 原生匿名抓取撞登录墙时的付费兜底。
    # Facebook Reels dataset:
    # https://docs.brightdata.com/api-reference/scrapers/social-media-apis/facebook-reels-collect-by-url
    bright_data_api_key: str | None = None
    bright_data_facebook_reels_dataset_id: str = "gd_lyclm3ey2q6rww027t"
    bright_data_tiktok_posts_dataset_id: str = "gd_m7n5v2gq296pex2f5m"
    bright_data_instagram_reels_dataset_id: str = "gd_l1vikfch901nx3by4"
    bright_data_youtube_videos_dataset_id: str = "gd_lk56epmy2i5g7lzu0k"
    bright_data_timeout_seconds: int = 120
    bright_data_poll_interval_seconds: float = 3.0
    # 20×3s=60s 对 TikTok/FB 够；IG 的 discover 快照慢（常 2-3min），
    # 加大到 60×3s=180s，让 IG 走 BD 能等到快照 ready 再取数据。
    bright_data_max_polls: int = 60

    # 内部服务密钥
    scraper_internal_token: str | None = None

    # 日志
    log_level: str = "info"

    def __init__(self, **kw):
        super().__init__(**kw)
        # 裸 postgresql:// / postgres:// URL 自动加 asyncpg 驱动后缀。
        # postgres:// 是 Neon/Heroku/Railway 常见短写法，tunnel 测试不会暴露，
        # 不处理的话生产接 Neon 时 scraper 必崩。已带 +driver 的原样不动。
        url = self.database_url
        if url.startswith(("postgresql+", "postgres+")):
            pass
        elif url.startswith("postgresql://"):
            self.database_url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        elif url.startswith("postgres://"):
            self.database_url = "postgresql+asyncpg://" + url[len("postgres://"):]


settings = Settings()
