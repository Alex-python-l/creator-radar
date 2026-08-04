"""facebook.fetch_facebook_videos orchestrator 测试 — mock httpx + fetch_reel。

测试覆盖:
- 正常路径:HTML OK + 多条 reel 都成功
- A 步失败:全代理都拿不到 HTML → RateLimited
- C 步单条失败:不阻断其他,整体仍 success
- C 步全失败:抛 RateLimited
- max_count 截断 reel ID 列表
"""
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.config import settings
from app.scrapers.errors import RateLimited
from app.scrapers.facebook import fetch_facebook_videos
from app.scrapers.tiktok import ScrapedVideo
from app.scrapers.types import ScrapedProfile

FIXTURE_DIR = Path(__file__).parent / "fixtures"
REAL_HTML = (FIXTURE_DIR / "facebook_reels_page.html").read_text()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """C 步 jitter 在测试里 zero-out,suite 跑 <1s 而不是 35s。"""
    async def _instant(*a, **kw):
        return None
    monkeypatch.setattr("app.scrapers.facebook.asyncio.sleep", _instant)


def _fake_reel(reel_id: str) -> ScrapedVideo:
    return ScrapedVideo(
        video_id=reel_id,
        video_url=f"https://www.facebook.com/reel/{reel_id}/",
        description="",
        thumbnail_url="https://thumb",
        duration_seconds=21,
        play_count=1000,
        like_count=50,
        comment_count=0,
        share_count=0,
        published_at=datetime(2026, 2, 23, tzinfo=UTC),
        raw={"id": reel_id},
    )


class _MockResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)


@pytest.mark.asyncio
async def test_happy_path_returns_profile_and_videos():
    """A 步首次成功 + C 步全部成功 → (profile, 10 videos)。"""
    async def fake_get(url, proxy):
        return _MockResponse(REAL_HTML)

    async def fake_reel_async(rid, p):
        return _fake_reel(rid)

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         patch("app.scrapers.facebook._fetch_reel_async", side_effect=fake_reel_async):
        profile, videos = await fetch_facebook_videos("Cristiano")
    assert isinstance(profile, ScrapedProfile)
    assert profile.display_name and "Cristiano" in profile.display_name
    assert len(videos) == 10


@pytest.mark.asyncio
async def test_a_step_all_proxies_fail_raises_rate_limited():
    """所有代理拿到 Error 页 → RateLimited。"""
    error_html = "<html><head><title>Error</title></head><body>fail</body></html>"
    async def fake_get(url, proxy):
        return _MockResponse(error_html, status_code=200)

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         pytest.raises(RateLimited):
        await fetch_facebook_videos("Cristiano")


@pytest.mark.asyncio
async def test_a_step_rate_limited_falls_back_to_bright_data(monkeypatch):
    """HTML 列表撞登录墙后,如果配置了 Bright Data,改走付费 API 兜底。"""
    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    error_html = "<html><head><title>Log into Facebook</title></head><body>login</body></html>"

    async def fake_get(url, proxy):
        return _MockResponse(error_html, status_code=200)

    async def fake_bright_data(profile_url, max_count):
        assert profile_url == "https://www.facebook.com/Cristiano"
        assert max_count == 2
        return (
            ScrapedProfile(
                follower_count=100,
                following_count=0,
                video_count=0,
                display_name="Cristiano",
                avatar_url=None,
                bio=None,
            ),
            [_fake_reel("bd-1"), _fake_reel("bd-2")],
        )

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         patch(
             "app.scrapers.facebook.fetch_facebook_reels_via_bright_data",
             side_effect=fake_bright_data,
         ) as bd:
        profile, videos = await fetch_facebook_videos("Cristiano", max_count=2)

    assert profile.display_name == "Cristiano"
    assert [v.video_id for v in videos] == ["bd-1", "bd-2"]
    bd.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_step_transport_error_falls_back_to_bright_data(monkeypatch):
    """代理连接失败也是临时错误，应继续尝试并最终走 Bright Data 兜底。"""
    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")

    async def fake_get(url, proxy):
        raise httpx.ConnectError("All connection attempts failed")

    async def fake_bright_data(profile_url, max_count):
        assert profile_url == "https://www.facebook.com/Cristiano"
        assert max_count == 2
        return (
            ScrapedProfile(
                follower_count=100,
                following_count=0,
                video_count=0,
                display_name="Cristiano",
                avatar_url=None,
                bio=None,
            ),
            [_fake_reel("bd-1"), _fake_reel("bd-2")],
        )

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         patch(
             "app.scrapers.facebook.fetch_facebook_reels_via_bright_data",
             side_effect=fake_bright_data,
         ) as bd:
        profile, videos = await fetch_facebook_videos("Cristiano", max_count=2)

    assert profile.display_name == "Cristiano"
    assert [v.video_id for v in videos] == ["bd-1", "bd-2"]
    bd.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_step_rate_limited_without_bright_data_keeps_rate_limited(monkeypatch):
    """未配置 Bright Data 时保持旧行为,让 worker 按 transient 重试。"""
    monkeypatch.setattr(settings, "bright_data_api_key", None)
    error_html = "<html><head><title>Log into Facebook</title></head><body>login</body></html>"

    async def fake_get(url, proxy):
        return _MockResponse(error_html, status_code=200)

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         patch("app.scrapers.facebook.fetch_facebook_reels_via_bright_data") as bd, \
         pytest.raises(RateLimited):
        await fetch_facebook_videos("Cristiano")

    bd.assert_not_called()


@pytest.mark.asyncio
async def test_c_step_single_reel_fail_does_not_block_others():
    """1 条 reel 全代理都炸 → 跳过该条,其他 9 条仍入结果。
    使用 fixture HTML 解析出的第一条 reel_id,避免 fixture 改动后测试 silent 退化。"""
    from app.scrapers.facebook_html import parse_page_html

    _, fixture_reel_ids = parse_page_html(REAL_HTML)
    target_failing_id = fixture_reel_ids[0]

    async def fake_get(url, proxy):
        return _MockResponse(REAL_HTML)

    async def fake_fetch_reel(rid, proxy):
        if rid == target_failing_id:
            raise RateLimited("simulated")
        return _fake_reel(rid)

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         patch("app.scrapers.facebook._fetch_reel_async", side_effect=fake_fetch_reel):
        profile, videos = await fetch_facebook_videos("Cristiano")
    assert len(videos) == len(fixture_reel_ids) - 1
    assert all(v.video_id != target_failing_id for v in videos)


@pytest.mark.asyncio
async def test_max_count_truncates_reel_list():
    async def fake_get(url, proxy):
        return _MockResponse(REAL_HTML)

    async def fake_reel_async(rid, p):
        return _fake_reel(rid)

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         patch("app.scrapers.facebook._fetch_reel_async", side_effect=fake_reel_async):
        _, videos = await fetch_facebook_videos("Cristiano", max_count=3)
    assert len(videos) == 3


@pytest.mark.asyncio
async def test_c_step_all_reels_fail_raises_rate_limited():
    """A 步成功拿到 reel 列表,但每条 reel 全代理都炸 → 整体 RateLimited。"""
    async def fake_get(url, proxy):
        return _MockResponse(REAL_HTML)

    async def fake_fetch_reel(rid, proxy):
        raise RateLimited(f"simulated reel fail {rid}")

    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get), \
         patch("app.scrapers.facebook._fetch_reel_async", side_effect=fake_fetch_reel), \
         pytest.raises(RateLimited):
        await fetch_facebook_videos("Cristiano")


# ---------- _page_url:三种 identifier 形态 ----------
# 旧的 /reels/ 子路径只对 vanity username 工作;数字 ID / pfbid 返回 500/404。
# 改用 ?sk=reels_tab 通用 reels tab 入口,FB 对任何 identifier 形态都返回 reels HTML。

def test_page_url_vanity_uses_sk_reels_tab():
    from app.scrapers.facebook import _page_url
    assert _page_url("Cristiano") == "https://www.facebook.com/Cristiano?sk=reels_tab"


def test_page_url_strips_at_prefix():
    from app.scrapers.facebook import _page_url
    assert _page_url("@Cristiano") == "https://www.facebook.com/Cristiano?sk=reels_tab"


def test_page_url_profile_php_uses_ampersand():
    """profile.php?id=<id> 已带 query,sk 用 & 连接而不是 ?。"""
    from app.scrapers.facebook import _page_url
    assert (
        _page_url("profile.php?id=61589956126863")
        == "https://www.facebook.com/profile.php?id=61589956126863&sk=reels_tab"
    )


def test_page_url_numeric_identifier_uses_profile_php():
    """纯数字 FB id 不是 vanity,应按 profile.php?id= 形态访问。"""
    from app.scrapers.facebook import _page_url

    assert (
        _page_url("61589956126863")
        == "https://www.facebook.com/profile.php?id=61589956126863&sk=reels_tab"
    )


def test_page_url_people_path_keeps_trailing_slash():
    """people/<n>/<pfbid> 这种 path-only identifier,加尾斜杠 + ?sk=reels_tab。"""
    from app.scrapers.facebook import _page_url
    pfbid = "pfbid02pb5DGo8aGp71dmeQmv85sP4GotPPM9FzbfvcayHUfXHsGLQ6jcqKBiEy1gBJQUKHl"
    assert (
        _page_url(f"people/Xshushx-Studied/{pfbid}")
        == f"https://www.facebook.com/people/Xshushx-Studied/{pfbid}/?sk=reels_tab"
    )


def test_bright_data_profile_url_numeric_identifier_uses_profile_php():
    from app.scrapers.facebook import _bright_data_profile_url

    assert (
        _bright_data_profile_url("61589956126863")
        == "https://www.facebook.com/profile.php?id=61589956126863"
    )


@pytest.mark.asyncio
async def test_page_with_no_reels_returns_empty_videos_not_error():
    """fixture 没 reel(空 video_id 列表)但含 profile sentinel → (profile, [])。

    sentinel(profile_picture / profile_header_renderer)区分"真账号没发 reel"和
    "通用页/已删账号"两种 0 reel 情形 — 后者按 facebook_html 测试抛 AccountGone。
    """
    no_reels_html = """<html><head><title>Foo Reels</title></head>
<body><script>{"profile_picture":{"uri":"https://x/y.jpg"},"user":{"name":"Foo"}}</script></body></html>"""
    async def fake_get(url, proxy):
        return _MockResponse(no_reels_html)
    with patch("app.scrapers.facebook._httpx_get", side_effect=fake_get):
        profile, videos = await fetch_facebook_videos("Foo")
    assert videos == []
    assert profile.display_name == "Foo"
