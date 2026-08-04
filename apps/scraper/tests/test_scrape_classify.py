from datetime import UTC, datetime

import pytest

from app.config import settings
from app.handlers.scrape import (
    _build_influencer_update_values,
    _is_transient_code,
    _run_fetch,
)
from app.scrapers.errors import RateLimited
from app.scrapers.types import ScrapedProfile


@pytest.mark.parametrize(
    "code,transient",
    [
        ("rate_limited", True),
        ("login_required", True),
        ("network_error", True),
        ("account_gone", False),
        ("creds_missing", False),
        ("auth_challenge", False),
        ("browser_unavailable", False),
    ],
)
def test_code_transient_mapping(code, transient):
    assert _is_transient_code(code) is transient


# --- _build_influencer_update_values: video_count 取舍 ---
# Bug 回归:FB parse_page_html 永远填 profile.video_count=0(FB 不暴露 page 总视频数),
# 旧逻辑直接覆盖 → influencers.video_count 永远是 0 → UI 显示 "0 视频"。

_CAPTURED_AT = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)


def test_build_update_values_fb_zero_profile_count_keeps_scraped_count():
    """FB 场景:profile.video_count=0 时,保留本次抓到的条数,不能被覆盖成 0。"""
    fb_profile = ScrapedProfile(
        follower_count=12345,
        following_count=0,  # FB 不给
        video_count=0,      # FB parse_page_html 永远填 0(page 不暴露真值)
        display_name="Foo Page",
        avatar_url=None,
        bio=None,
    )
    values = _build_influencer_update_values(
        captured_at=_CAPTURED_AT,
        scraped_count=7,
        profile=fb_profile,
    )
    assert values["video_count"] == 7  # 不被 profile.video_count=0 覆盖
    assert values["follower_count"] == 12345
    assert values["display_name"] == "Foo Page"


def test_build_update_values_ig_real_profile_count_wins():
    """IG 场景:instagrapi 给真实 media_count(账号总历史),优先用 profile 值。"""
    ig_profile = ScrapedProfile(
        follower_count=1000,
        following_count=200,
        video_count=523,  # IG media_count 真值
        display_name="X",
        avatar_url="https://a",
        bio="hi",
    )
    values = _build_influencer_update_values(
        captured_at=_CAPTURED_AT,
        scraped_count=30,  # 本次只抓 30 条最近
        profile=ig_profile,
    )
    assert values["video_count"] == 523  # 用 profile 真值,反映账号总历史
    assert values["bio"] == "hi"


def test_build_update_values_fb_zero_follower_count_not_overwritten():
    """FB 场景:未登录抓取的 HTML 不嵌 follower 数(facebook_html 里 _FOLLOWER_RE
    全 fixture/全生产 0 命中,跟 video_count 同款占位 0)→ 不能覆盖,否则手动录入
    或将来登录 cookie 工程拿到的真值会被中间一次未登录抓取写 0 回去。

    following_count 同款处理。"""
    fb_profile = ScrapedProfile(
        follower_count=0,   # FB 未登录 HTML 不给(已验证 26/26 全 0)
        following_count=0,  # FB Page 没 following 概念占位 0
        video_count=0,
        display_name="Foo Page",
        avatar_url=None,
        bio=None,
    )
    values = _build_influencer_update_values(
        captured_at=_CAPTURED_AT,
        scraped_count=5,
        profile=fb_profile,
    )
    # follower/following 没值 → 不写入 update,保留 DB 现有真值
    assert "follower_count" not in values
    assert "following_count" not in values
    # 其他字段照常
    assert values["video_count"] == 5
    assert values["display_name"] == "Foo Page"


def test_build_update_values_ig_real_follower_count_writes():
    """IG 场景:instagrapi 给真实 follower_count > 0 → 正常写入。"""
    ig_profile = ScrapedProfile(
        follower_count=1500,
        following_count=300,
        video_count=42,
        display_name="X",
        avatar_url=None,
        bio=None,
    )
    values = _build_influencer_update_values(
        captured_at=_CAPTURED_AT,
        scraped_count=10,
        profile=ig_profile,
    )
    assert values["follower_count"] == 1500
    assert values["following_count"] == 300


def test_build_update_values_no_profile_uses_scraped_count():
    """TK/YT 场景:scraper 不返 profile,只能用本次抓到的条数;不动 follower 等字段。"""
    values = _build_influencer_update_values(
        captured_at=_CAPTURED_AT,
        scraped_count=15,
        profile=None,
    )
    assert values["video_count"] == 15
    assert values["last_fetched_at"] == _CAPTURED_AT
    # profile=None 不应触碰 follower/display_name 等字段 — 防止把已抓到的字段清空
    assert "follower_count" not in values
    assert "display_name" not in values


@pytest.mark.asyncio
async def test_run_fetch_dispatches_youtube(monkeypatch):
    called: dict[str, object] = {}

    async def fake_delay(platform: str) -> None:
        called["delay_platform"] = platform

    async def fake_fetch(username: str, max_count: int | None):
        called["username"] = username
        called["max_count"] = max_count
        raise RateLimited("simulated youtube block")

    monkeypatch.setattr("app.handlers.scrape.random_pre_fetch_delay", fake_delay)
    monkeypatch.setattr(
        "app.handlers.scrape.fetch_youtube_shorts",
        fake_fetch,
        raising=False,
    )

    result = await _run_fetch(1, "youtube", "mkbhd", mode="recent")

    assert called == {
        "delay_platform": "youtube",
        "username": "mkbhd",
        "max_count": settings.max_videos_recent,
    }
    assert result["error_code"] == "rate_limited"
