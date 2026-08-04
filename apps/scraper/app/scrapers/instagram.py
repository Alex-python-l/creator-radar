"""
Instagram 抓取 — instagrapi 完整账密登录 + device 持久化 + 自动节流。

关键设计（防风控）：
- 用 cl.login(username, password)，让 instagrapi 自己生成稳定的 device id /
  phone id / advertising id 等指纹，第一次登录后 dump 到 settings.json
- 之后跑：load_settings(file) + login(user, pass) — 复用同一 device，
  IG 看到的是"同一台手机持续使用同一账号"，不会反复触发 challenge
- cl.delay_range = [1, 3]：每个 API 调用之间自动插入 1-3 秒随机延迟
- worker 单 loop 串行（concurrency=1）+ 全局 min-interval，控制抓取频率
- 强烈建议用专门测试小号，不要用主号或受过警告的号

工作流：
- 第一次启动 → 若配了 sessionid（买号自带的已登录 session）用 login_by_sessionid
  直接接入（不碰密码 / 2FA，风控最低）；否则账密完整 login → 生成 settings.json
- 之后每次抓取 → 复用 client（lazy 单例）→ 调 user_info_by_username + user_medias
- 异常（PleaseWaitFewMinutesError / LoginRequired / ChallengeRequired）→ 抛
  ScrapeError 子类，handler 写 fetch_logs error_code
- session 失效自愈：抓取拿到 LoginRequired（IG 私有 API 403）后置
  `_force_relogin`，下次建 client 用 `relogin=True` 走密码完整重登 —— 否则
  instagrapi 见 load_settings 带回的 user_id 会直接复用失效 session，403 死循环。
  relogin 仍复用同一 device 指纹，只刷认证态。
"""
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from app.config import settings
from app.scrapers.errors import (
    AuthChallenge,
    CredsMissing,
    RateLimited,
    ScrapeError,
    SessionExpired,
)
from app.scrapers.proxies import instagram_proxy
from app.scrapers.tiktok import ScrapedVideo
from app.scrapers.types import ScrapedProfile

logger = structlog.get_logger(__name__)


# apps/scraper/data/ig-instagrapi.json
SETTINGS_FILE = Path(__file__).resolve().parents[2] / "data" / "ig-instagrapi.json"


def classify_instagrapi_error(exc: Exception) -> ScrapeError:
    """把 instagrapi 异常类型映射成确定的 ScrapeError。
    instagrapi 的异常类型是可靠信号，比正则字符串准。无法确定 → ScrapeError(unknown)。"""
    if isinstance(exc, ChallengeRequired):
        return AuthChallenge(str(exc))
    if isinstance(exc, PleaseWaitFewMinutes):
        return RateLimited(str(exc))
    if isinstance(exc, LoginRequired):
        return SessionExpired(str(exc))
    return ScrapeError(str(exc))


_client: Client | None = None
_client_lock = asyncio.Lock()
# session 失效（抓取拿到 LoginRequired）或登录失败后置 True：下次建 client
# 强制用账密完整重登，而不是复用失效 session / 重复用失败的 sessionid。
_force_relogin: bool = False


def _password_login(cl: Client, relogin: bool) -> None:
    """账密登录。账号开了 2FA（配了 totp_seed）时附带 TOTP 验证码。"""
    kwargs: dict[str, Any] = {"relogin": relogin}
    if settings.instagram_totp_seed:
        kwargs["verification_code"] = cl.totp_generate_code(
            settings.instagram_totp_seed
        )
    cl.login(settings.instagram_username, settings.instagram_password, **kwargs)


async def _get_client() -> Client:
    """Lazy 单例 — 全局复用同一个 instagrapi Client。"""
    global _client, _force_relogin
    async with _client_lock:
        if _client is not None:
            return _client

        if not settings.instagram_username or not settings.instagram_password:
            raise CredsMissing(
                "INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD 未配置。"
                "在 apps/scraper/.env 加这两个字段（用专门测试小号），"
                "然后重启 scraper。"
            )

        cl = Client()
        # 自动在每个 API 调用前后加 2-6 秒随机延迟（instagrapi 内置）
        # 调高（之前 1-3 秒）让多次内部 API 调用之间更慢，模拟真人翻页
        cl.delay_range = [2, 6]

        # IG 必须 sticky：优先 INSTAGRAM_PROXY_URL，否则 PROXY_URL，最后 common pool 第一条
        ig_proxy = instagram_proxy(
            settings.instagram_proxy_url,
            settings.proxy_url,
            settings.common_proxy_pool,
        )
        if ig_proxy:
            cl.set_proxy(ig_proxy)

        # 复用之前 dump 的 device + cookies（如果有）
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        had_settings = SETTINGS_FILE.exists()
        if had_settings:
            try:
                cl.load_settings(str(SETTINGS_FILE))
                logger.info("instagram.settings.loaded", path=str(SETTINGS_FILE))
            except Exception as exc:
                logger.warning("instagram.settings.load_failed", error=str(exc))
                had_settings = False

        try:
            if not had_settings and not _force_relogin and settings.instagram_sessionid:
                # 首次启动：用账号自带的 sessionid 直接建立 session（买号场景）。
                # IG 已信任这个 session，不碰密码 / 2FA，风控概率最低。
                cl.login_by_sessionid(settings.instagram_sessionid)
                logger.info("instagram.login.by_sessionid")
            else:
                # 已有 session 复用；session 失效（_force_relogin）则用账密
                # 完整重登 —— 账号开了 2FA 时附带 TOTP 验证码。
                _password_login(cl, relogin=_force_relogin)
                if _force_relogin:
                    logger.info("instagram.relogin.done")
        except (ChallengeRequired, PleaseWaitFewMinutes, LoginRequired) as exc:
            # 本次登录失败 → 下次强制走账密重登，别再重复失败的方式
            _force_relogin = True
            raise classify_instagrapi_error(exc) from exc
        _force_relogin = False

        # 登录成功后立刻 dump，下次启动可复用 device
        try:
            cl.dump_settings(str(SETTINGS_FILE))
            logger.info("instagram.settings.dumped", path=str(SETTINGS_FILE))
        except Exception as exc:
            logger.warning("instagram.settings.dump_failed", error=str(exc))

        _client = cl
        return cl


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _first_positive_int(*values: Any) -> int:
    for value in values:
        parsed = _to_int(value)
        if parsed > 0:
            return parsed
    return 0


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_compatible(item) for item in value]
    return str(value)


def _media_id(media: Any) -> str | None:
    pk = getattr(media, "pk", None) or getattr(media, "id", None)
    return str(pk) if pk else None


def _media_raw(media: Any) -> dict[str, Any]:
    if hasattr(media, "model_dump"):
        raw = media.model_dump(mode="json")
    elif hasattr(media, "__dict__"):
        raw = vars(media)
    else:
        media_id = _media_id(media)
        raw = {"pk": media_id} if media_id else {}

    if isinstance(raw, dict):
        return _json_compatible(raw)
    return {"value": _json_compatible(raw)}


def _instagram_content_type(media: Any) -> str:
    media_type = _to_int(getattr(media, "media_type", None))
    product_type = str(getattr(media, "product_type", "") or "").lower()
    if media_type == 8 or "carousel" in product_type:
        return "carousel"
    if media_type == 2 or product_type in {"clips", "igtv"}:
        return "reel"
    return "post"


def _instagram_content_url(code: str | None, content_type: str) -> str | None:
    if not code:
        return None
    path = "reel" if content_type == "reel" else "p"
    return f"https://www.instagram.com/{path}/{code}/"


def _media_play_count(media: Any) -> int:
    return _first_positive_int(
        getattr(media, "play_count", None),
        getattr(media, "view_count", None),
    )


def _media_to_video(
    media: Any, metrics_media: Any | None = None
) -> ScrapedVideo | None:
    media_id = _media_id(media)
    if not media_id:
        return None
    code = getattr(media, "code", None)
    content_type = _instagram_content_type(media)
    has_metrics_overlay = content_type == "reel" and metrics_media is not None
    metrics_source = metrics_media if has_metrics_overlay else media
    play_count = 0
    duration_seconds = None
    if content_type == "reel":
        play_count = _first_positive_int(
            getattr(metrics_source, "play_count", None),
            getattr(metrics_source, "view_count", None),
            getattr(media, "play_count", None),
            getattr(media, "view_count", None),
        )
        duration_seconds = _first_positive_int(
            getattr(metrics_source, "video_duration", None),
            getattr(media, "video_duration", None),
        ) or None
    raw = _media_raw(media)
    if has_metrics_overlay:
        raw["_metrics_source"] = "clips"
        raw["_metrics_media"] = _media_raw(metrics_media)

    return ScrapedVideo(
        video_id=media_id,
        video_url=_instagram_content_url(code, content_type),
        description=getattr(media, "caption_text", None) or None,
        thumbnail_url=str(getattr(media, "thumbnail_url", "") or "") or None,
        duration_seconds=duration_seconds,
        play_count=play_count,
        like_count=_to_int(getattr(media, "like_count", None)),
        comment_count=_to_int(getattr(media, "comment_count", None)),
        share_count=0,  # IG 不公开分享数；media_repost_count 在 instagrapi Media 对象里没暴露
        published_at=_to_datetime(getattr(media, "taken_at", None)),
        content_type=content_type,
        # _media_raw 把 Pydantic / namespace media 转成 JSON-compatible dict。
        raw=raw,
    )


async def fetch_instagram_videos_via_bright_data(
    username: str,
    max_count: int | None = None,
) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    """Lazy wrapper for Bright Data fallback."""
    from app.scrapers.bright_data_social import (
        fetch_instagram_videos_via_bright_data as _impl,
    )

    return await _impl(username, max_count)


def _fetch_sync(username: str, max_count: int) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    """同步执行 instagrapi 调用，在线程池里跑。"""
    # 这里不能直接 await，因为 _fetch_sync 是同步的
    # _get_client 是 async，所以在调用方先解决 client。把 client 通过 closure 传进来更干净
    raise NotImplementedError  # 占位，下面会替换


async def fetch_instagram_videos(
    username: str,
    max_count: int | None = 12,
) -> tuple[ScrapedProfile, list[ScrapedVideo]]:
    """
    返回 (profile, videos)。max_count=None / 0 → 拉账号全部 media。

    profile 含 follower/following/display_name/avatar/bio，
    handler 用来 update influencers 表；videos 用来 upsert videos 表。

    handler 调用前应已加 random sleep 控制全局抓取频率。
    """
    username = username.lstrip("@")
    # IG 登录小号已被 IG 深度风控（登录即连坐拉黑出口 IP），改为直接走
    # Bright Data 抓取、彻底不碰 instagrapi 登录。无 BD key（本地开发）时
    # 才回退到账密登录（登录异常由上层 handler 分类写 fetch_logs）。
    if settings.bright_data_api_key:
        logger.info("instagram.via_bright_data", username=username)
        return await fetch_instagram_videos_via_bright_data(username, max_count)
    cl = await _get_client()
    # instagrapi user_medias 用 amount=0 表示拉全部
    amount = max_count if max_count and max_count > 0 else 0
    logger.info("instagram.instagrapi.fetch.start", username=username, amount=amount or 'ALL')

    def _do_fetch() -> tuple[ScrapedProfile, list[ScrapedVideo]]:
        user = cl.user_info_by_username(username)
        profile = ScrapedProfile(
            follower_count=_to_int(user.follower_count),
            following_count=_to_int(user.following_count),
            video_count=_to_int(user.media_count),
            display_name=user.full_name or None,
            avatar_url=str(user.profile_pic_url_hd or user.profile_pic_url or "") or None,
            bio=user.biography or None,
        )
        # feed 枚举全部 IG 内容（post/carousel/reel）；clips 仅用于覆盖 Reel
        # 的播放量和时长，因为 feed 接口不给稳定的播放指标。
        feed_medias = cl.user_medias_v1(user.pk, amount=amount)
        clip_medias = cl.user_clips_v1(user.pk, amount=amount)
        clips_by_id = {
            media_id: m
            for m in clip_medias
            if (media_id := _media_id(m)) is not None
        }
        videos: list[ScrapedVideo] = []
        for m in feed_medias:
            media_id = _media_id(m)
            metrics_media = clips_by_id.get(media_id or "")
            v = _media_to_video(m, metrics_media=metrics_media)
            if v is None:
                continue
            if v.content_type == "reel" and v.play_count == 0:
                continue
            videos.append(v)
        return profile, videos

    try:
        result = await asyncio.to_thread(_do_fetch)
    except (PleaseWaitFewMinutes, LoginRequired, ChallengeRequired) as exc:
        # 触发风控类异常：清空单例，下次抓取重新登录
        global _client, _force_relogin
        _client = None
        if isinstance(exc, LoginRequired):
            # session 失效：标记下次强制密码重登，否则又复用失效 session 死循环
            _force_relogin = True
        raise classify_instagrapi_error(exc) from exc

    profile, videos = result
    logger.info(
        "instagram.instagrapi.fetch.done",
        username=username,
        followers=profile.follower_count,
        videos=len(videos),
    )
    return profile, videos
