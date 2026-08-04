"""
账号抓取 handler：
1. 收到 account.fetch job（由 worker 调用）
2. 调对应平台 scraper 拉视频列表
3. upsert videos + 更新 influencer.last_fetched_at + 写 fetch_logs
4. 返回 (ok, transient, error) 三元组给 worker
"""
import asyncio
import re
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session
from app.db.models import FetchLog, Influencer, Video
from app.scrapers import (
    ScrapedProfile,
    ScrapeError,
    fetch_facebook_videos,
    fetch_instagram_videos,
    fetch_tiktok_videos,
    fetch_youtube_shorts,
    random_pre_fetch_delay,
)
from app.scrapers.youtube import normalize_youtube_identifier

logger = structlog.get_logger(__name__)


# Transient（临时性）错误关键字白名单。匹配到任意一个 worker 就自动重试（指数退避）。
# 涵盖：HTTP 429 限流 / 网络抖动 / 上游临时故障 / IG 平台暂时性 challenge。
TRANSIENT_KEYWORDS: tuple[str, ...] = (
    # 通用网络 / HTTP
    "429",
    "too many requests",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "connection refused",
    "temporarily",
    "temporary",
    "rate limit",
    "rate-limit",
    "service unavailable",
    "503",
    "502",
    "504",
    "econnreset",
    "read timeout",
    # Instagram 特有 transient
    "feedback_required",  # IG 风控临时性 challenge
    "please wait a few",  # IG "please wait a few minutes/moments"
    "loginrequired",  # session 偶尔过期，re-login 可恢复
    "jsondecodeerror",  # instagrapi 拿到空响应 / HTML 占位页
    "expecting value",  # 同 JSONDecodeError 的常见文本
)


def _is_transient(error_msg: str | None) -> bool:
    """判断错误是否值得重试。命中 TRANSIENT_KEYWORDS 任一关键字就算 transient。"""
    if not error_msg:
        return False
    msg_lower = error_msg.lower()
    return any(k in msg_lower for k in TRANSIENT_KEYWORDS)


# code → 是否值得重试。account_gone/creds_missing/auth_challenge/
# browser_unavailable 是永久错，重试无意义；rate_limited/login_required/
# network_error 是临时错。unknown 不在表里 → 调用方回退旧关键字白名单。
_TRANSIENT_CODES: frozenset[str] = frozenset(
    {"rate_limited", "login_required", "network_error"}
)
_PERMANENT_CODES: frozenset[str] = frozenset(
    {"account_gone", "creds_missing", "auth_challenge", "browser_unavailable"}
)


def _is_transient_code(code: str | None) -> bool:
    return code in _TRANSIENT_CODES


def _now() -> datetime:
    return datetime.now(UTC)


def _build_influencer_update_values(
    captured_at: datetime,
    scraped_count: int,
    profile: ScrapedProfile | None,
) -> dict:
    """构造 influencers UPDATE 字段。

    数值字段(video_count / follower_count / following_count)取舍统一规则:
    "0 当哨兵" —— profile 给 0 就当没给(不写入 update),给真值(>0)才覆盖。
    取舍理由按字段:
    - video_count:profile 给真值(IG instagrapi media_count)反映账号总历史 >
      本次抓到的 ≤30 条 recent;profile 没给/给 0 占位(FB Page HTML 不暴露
      总视频数) → 用本次 scraped_count,至少 UI 不显示 "0 视频"
    - follower_count / following_count:profile 给真值就用;给 0 占位(FB 未
      登录 HTML 不嵌 follower 数据,facebook_html._FOLLOWER_RE 全 fixture +
      26/26 生产 0 命中) → 不覆盖,保留 DB 现有真值(手动录入 / 将来登录
      cookie 工程拿到的值不被中间一次未登录抓取写 0 回去)
    - profile=None(TK/YT scraper 不返 profile)→ 整段跳过,follower/display_name
      等字段全部保留 DB 旧值
    """
    values: dict = {"last_fetched_at": captured_at, "video_count": scraped_count}
    if profile is not None:
        if profile.video_count > 0:
            values["video_count"] = profile.video_count
        if profile.follower_count > 0:
            values["follower_count"] = profile.follower_count
        if profile.following_count > 0:
            values["following_count"] = profile.following_count
        if profile.display_name:
            values["display_name"] = profile.display_name
        if profile.avatar_url:
            values["avatar_url"] = profile.avatar_url
        if profile.bio is not None:
            values["bio"] = profile.bio
    return values


# yt-dlp 抓取整体超时 — 卡住时中断、抛错让 job 重试，避免单条 worker lane
# 被永久堵塞。fetch_tiktok_videos 内部会逐条换代理重试，300s 留足空间。
TIKTOK_VIDEOS_TIMEOUT_SEC = 300

# FB 抓取 worst case: A 步 4 代理 × 20s timeout + C 步 ~20 reel × yt-dlp 慢响应,
# 600s 留足空间。整体超时防 worker lane 被卡死(单条 reel 重试逻辑在 facebook.py 内层)。
FACEBOOK_VIDEOS_TIMEOUT_SEC = 600
YOUTUBE_SHORTS_TIMEOUT_SEC = 300


_TIKTOK_USERNAME_RE = re.compile(r"tiktok\.com/@?([\w.\-]+)", re.I)
_INSTAGRAM_USERNAME_RE = re.compile(r"instagram\.com/([\w.\-]+)", re.I)
_IG_RESERVED_PATHS = frozenset({"p", "reel", "reels", "explore", "accounts"})

# FB 三种 identifier 形态(优先级从高到低 — _clean_username 按序匹配第一个命中):
# 1. profile.php?id=<numeric>  — 没设 vanity 的 Page,FB 默认 URL 形态
# 2. people/<name>/<token>     — token 是 pfbid<...> 或 numeric_id;share 短链 redirect 常落到这种形态
# 3. vanity (兜底)             — facebook.com/<vanity>
_FB_PROFILE_PHP_RE = re.compile(r"(?:facebook\.com/)?profile\.php\?id=(\d+)", re.I)
_FB_STORY_ID_RE = re.compile(
    r"(?:facebook\.com/)?story\.php\?(?:[^#&]*&)*id=(\d+)(?:[&#]|$)",
    re.I,
)
_FB_NUMERIC_PATH_RE = re.compile(r"facebook\.com/(\d{5,})(?:[/?#]|$)", re.I)
_FB_PEOPLE_PATH_RE = re.compile(r"facebook\.com/people/([\w.\-]+)/(pfbid\w+|\d+)", re.I)
_FB_VANITY_RE = re.compile(r"facebook\.com/([\w.\-]+)", re.I)
# vanity 兜底前要拦掉的系统路径。前两组(routing / identifier 前缀)是 FB 网站
# 内部 path,出现在 vanity 槽位说明 URL 没指向账号主页(可能是登录墙 redirect)。
# profile.php / people / share 由前面两条特殊规则吃掉,加进 reserved 是双保险。
_FB_RESERVED_PATHS = frozenset(
    {
        # reels / 内容路由
        "reel", "reels", "watch", "videos", "video", "photo", "photos", "story", "stories",
        "story.php",
        # 社交对象
        "groups", "events", "marketplace", "pages", "p",
        # 账户 / 系统页面(FB 登录墙 / challenge 常 redirect 到这些)
        "login", "signup", "recover", "checkpoint", "security", "settings",
        "help", "business", "ads", "gaming", "search", "hashtag",
        # identifier 前缀(让 _FB_PROFILE_PHP_RE / _FB_PEOPLE_PATH_RE 不命中时兜底拦掉)
        "profile.php", "people", "share",
    }
)


def _clean_username(raw: str, platform: str) -> str:
    """防御性收敛 username。

    早期 web 单条录入缺 URL 解析，会把整条主页 URL（含 `?_r=...&_t=...`
    分享参数）原样存进 influencers.username，下游 `_profile_url` 再套一层
    前缀就拼出 `tiktok.com/@<整条URL>` 的双重 URL，抓取必败。
    根因已在 web 录入侧修复，此处是兜底：把 URL 形态的 username 按平台
    规则收回纯 username；正常 username 原样返回。"""
    s = (raw or "").strip()
    if platform == "tiktok":
        m = _TIKTOK_USERNAME_RE.search(s)
        if m:
            return m.group(1)
    elif platform == "instagram":
        m = _INSTAGRAM_USERNAME_RE.search(s)
        if m and m.group(1).lower() not in _IG_RESERVED_PATHS:
            return m.group(1)
    elif platform == "facebook":
        # 唯一标识 = 纯数字 profile id。各形态统一收敛到纯数字。
        # 1. profile.php?id=<numeric>
        m = _FB_PROFILE_PHP_RE.search(s)
        if m:
            return m.group(1)
        # 2. story.php?...&id=<numeric> 是 share/r 短链常见 final URL
        m = _FB_STORY_ID_RE.search(s)
        if m:
            return m.group(1)
        # 3. facebook.com/<numeric> 数字路径
        m = _FB_NUMERIC_PATH_RE.search(s)
        if m:
            return m.group(1)
        # 4. 已是纯数字 id
        if s.isdigit():
            return s
        # 5. people/<name>/<token>:numeric token 即 profile id;
        #    pfbid 无法直接转纯数字,防御保留原形态(回填前残留 job)
        m = _FB_PEOPLE_PATH_RE.search(s)
        if m:
            token = m.group(2)
            return token if token.isdigit() else f"people/{m.group(1)}/{token}"
        # 6. vanity 兜底(回填后不应出现,残留 job 防御)
        m = _FB_VANITY_RE.search(s)
        if m and m.group(1).lower() not in _FB_RESERVED_PATHS:
            return m.group(1)
    elif platform == "youtube":
        return normalize_youtube_identifier(s)
    return s.lstrip("@")


async def _run_fetch(
    influencer_id: int,
    platform: str,
    username: str,
    mode: str = "full",
) -> dict:
    """实际抓取 + 写库。返回 {found, new, error?, profile?}。

    mode='full': max_count=None → scraper 拉账号全部视频（无上限）
    mode='recent': max_count=settings.max_videos_recent（默认 30，够覆盖最近 7 天）
    """
    from app.config import settings

    started_at = _now()
    profile: ScrapedProfile | None = None
    max_count: int | None = None if mode == "full" else settings.max_videos_recent

    logger.info(
        "scrape.start",
        platform=platform,
        username=username,
        mode=mode,
        max_count=max_count if max_count is not None else "ALL",
    )

    try:
        if platform == "tiktok":
            await random_pre_fetch_delay("tiktok")
            # 只抓视频列表（yt-dlp）；profile（follower/头像/bio）需浏览器
            # 渲染，已随 Playwright 一起移除。整体超时防 worker lane 被卡死。
            scraped = await asyncio.wait_for(
                fetch_tiktok_videos(username, max_count=max_count),
                timeout=TIKTOK_VIDEOS_TIMEOUT_SEC,
            )
            profile = None
        elif platform == "instagram":
            await random_pre_fetch_delay("instagram")
            profile, scraped = await fetch_instagram_videos(username, max_count=max_count)
        elif platform == "facebook":
            await random_pre_fetch_delay("facebook")
            profile, scraped = await asyncio.wait_for(
                fetch_facebook_videos(username, max_count=max_count),
                timeout=FACEBOOK_VIDEOS_TIMEOUT_SEC,
            )
        elif platform == "youtube":
            await random_pre_fetch_delay("youtube")
            scraped = await asyncio.wait_for(
                fetch_youtube_shorts(username, max_count=max_count),
                timeout=YOUTUBE_SHORTS_TIMEOUT_SEC,
            )
            profile = None
        else:
            return {
                "found": 0,
                "new": 0,
                "error": f"platform {platform} not yet supported in MVP",
                "error_code": "unknown",
                "started_at": started_at,
            }
    except ScrapeError as exc:
        logger.warning(
            "scrape.classified_error",
            username=username,
            platform=platform,
            error_code=exc.error_code,
            msg=str(exc),
        )
        return {
            "found": 0,
            "new": 0,
            "error": str(exc),
            "error_code": exc.error_code,
            "started_at": started_at,
        }
    except TimeoutError:
        logger.warning("scrape.timeout", username=username, platform=platform)
        return {
            "found": 0,
            "new": 0,
            "error": f"scrape timeout ({platform})",
            "error_code": "network_error",
            "started_at": started_at,
        }
    except Exception as exc:
        logger.exception("scrape.failed", username=username, platform=platform)
        return {
            "found": 0,
            "new": 0,
            "error": str(exc),
            "error_code": "unknown",
            "started_at": started_at,
        }

    async with async_session() as session:
        if scraped:
            scraped_ids = [v.video_id for v in scraped]
            existing_q = await session.execute(
                select(Video.video_id).where(
                    Video.platform == platform,
                    Video.video_id.in_(scraped_ids),
                )
            )
            existing_ids = {row[0] for row in existing_q.all()}
        else:
            existing_ids = set()

        new_count = sum(1 for v in scraped if v.video_id not in existing_ids)
        logger.info(
            "scrape.upsert",
            platform=platform,
            scraped=len(scraped),
            already_in_db=len(existing_ids),
            new=new_count,
        )

        captured_at = _now()
        for v in scraped:
            stmt = (
                pg_insert(Video)
                .values(
                    influencer_id=influencer_id,
                    platform=platform,
                    video_id=v.video_id,
                    video_url=v.video_url,
                    description=v.description,
                    thumbnail_url=v.thumbnail_url,
                    duration_seconds=v.duration_seconds,
                    play_count=v.play_count,
                    like_count=v.like_count,
                    comment_count=v.comment_count,
                    share_count=v.share_count,
                    content_type=v.content_type,
                    published_at=v.published_at,
                    captured_at=captured_at,
                    raw=v.raw,
                )
                .on_conflict_do_update(
                    index_elements=["platform", "video_id"],
                    set_={
                        "play_count": v.play_count,
                        "like_count": v.like_count,
                        "comment_count": v.comment_count,
                        "share_count": v.share_count,
                        "content_type": v.content_type,
                        "captured_at": captured_at,
                        "raw": v.raw,
                    },
                )
            )
            await session.execute(stmt)

        # 更新 influencer：基础字段（last_fetched_at + video_count），
        # 如果 scraper 返回了 profile（IG 必给、FB 部分字段、TK/YT 无），把对应
        # 字段一起更。video_count 取舍见 _build_influencer_update_values docstring。
        update_values = _build_influencer_update_values(
            captured_at=captured_at,
            scraped_count=len(scraped),
            profile=profile,
        )

        await session.execute(
            Influencer.__table__.update()
            .where(Influencer.id == influencer_id)
            .values(**update_values)
        )
        await session.commit()

    return {"found": len(scraped), "new": new_count, "started_at": started_at}


async def run_account_fetch_job(payload: dict) -> tuple[bool, bool, str | None]:
    """worker 调用：处理一个 account.fetch job。

    返回 (ok, transient, error)：
      - 成功 → (True, False, None)
      - transient 错（429/timeout/auth）→ (False, True, err)  worker 据此 retry/dead
      - 永久错 → (False, False, err)  worker 标 failed

    fetch_logs：本函数自建一条日志行（worker 模式无预置 fetchLogId），
    记 started/found/new/error/finished。job 状态由 worker 写回 jobs 表。
    """
    influencer_id = int(payload.get("influencerId", 0))
    platform = str(payload.get("platform", "tiktok"))
    username = _clean_username(str(payload.get("username", "")), platform)
    mode = str(payload.get("mode") or "full")
    if mode not in ("full", "recent"):
        mode = "full"

    if not influencer_id or not username:
        return (False, False, "missing influencerId/username")

    logger.info(
        "account.fetch.start",
        influencer_id=influencer_id,
        platform=platform,
        username=username,
        mode=mode,
    )

    started_at = _now()
    try:
        result = await _run_fetch(influencer_id, platform, username, mode=mode)
    except Exception as exc:
        logger.exception("account.fetch.run_failed", influencer_id=influencer_id)
        result = {
            "found": 0,
            "new": 0,
            "error": f"unhandled: {type(exc).__name__}: {exc}",
            "error_code": "unknown",
            "started_at": started_at,
        }

    finished_at = _now()
    err_msg = result.get("error")
    err_code = result.get("error_code")
    # 确定类别按 code 判重试性；unknown 回退旧关键字白名单（行为不回退）。
    if err_code in _PERMANENT_CODES:
        transient = False
    elif err_code in _TRANSIENT_CODES:
        transient = True
    else:
        transient = _is_transient(err_msg)
    final_status = "failed" if err_msg else "success"

    try:
        async with async_session() as session:
            session.add(
                FetchLog(
                    influencer_id=influencer_id,
                    platform=platform,
                    status=final_status,
                    found_count=result["found"],
                    new_count=result["new"],
                    error_message=err_msg,
                    error_code=err_code,
                    started_at=result.get("started_at", started_at),
                    finished_at=finished_at,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("account.fetch.log_write_failed", influencer_id=influencer_id)

    if err_msg and transient:
        logger.warning(
            "account.fetch.transient", influencer_id=influencer_id, error=err_msg
        )
        return (False, True, err_msg)
    if err_msg:
        return (False, False, err_msg)
    return (True, False, None)
