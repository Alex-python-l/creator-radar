"""facebook_html.parse_page_html 测试 — 用真实 fixture HTML。

fixture 是从 Cristiano page 真实抓取裁剪而来,含 10 个 unique video_id +
profile 区段(name / follower / profile_picture)。
"""
from pathlib import Path

import pytest

from app.scrapers.errors import AccountGone, RateLimited
from app.scrapers.facebook_html import parse_page_html

FIXTURE_DIR = Path(__file__).parent / "fixtures"
REAL_HTML = (FIXTURE_DIR / "facebook_reels_page.html").read_text()


def test_extracts_10_unique_reel_ids():
    _, reel_ids = parse_page_html(REAL_HTML)
    assert len(reel_ids) == 10
    # 全部都是 12+ 位数字字符串
    assert all(rid.isdigit() and len(rid) >= 12 for rid in reel_ids)
    # 去重 (函数应该返回 unique 列表)
    assert len(set(reel_ids)) == 10


def test_extracts_profile_name():
    profile, _ = parse_page_html(REAL_HTML)
    assert profile.display_name is not None
    assert "Cristiano" in profile.display_name


def test_extracts_profile_avatar():
    """fixture 里真有 profile_picture URI,应该提取成功(不允许默认 None 兜底)。"""
    profile, _ = parse_page_html(REAL_HTML)
    assert profile.avatar_url is not None
    assert profile.avatar_url.startswith("http")


def test_profile_follower_count_or_zero():
    """follower_count 拿到就是 int,拿不到给 0(宽容)。"""
    profile, _ = parse_page_html(REAL_HTML)
    assert isinstance(profile.follower_count, int)
    assert profile.follower_count >= 0


def test_profile_following_and_video_count_default_zero():
    """FB 不暴露 following_count / video_count,固定 0。"""
    profile, _ = parse_page_html(REAL_HTML)
    assert profile.following_count == 0
    assert profile.video_count == 0


def test_profile_bio_is_none():
    """FB Page 没有 IG 风格的简短 bio,固定 None。"""
    profile, _ = parse_page_html(REAL_HTML)
    assert profile.bio is None


def test_error_page_raises_rate_limited():
    """FB 软封时返回的 1.5KB Error 页 — 应该抛 RateLimited(transient)。"""
    error_html = """<!DOCTYPE html><html><head><title>Error</title></head>
<body><h1>Sorry, something went wrong.</h1></body></html>"""
    with pytest.raises(RateLimited):
        parse_page_html(error_html)


def test_large_error_page_also_raises_rate_limited():
    """大尺寸 Error 页(proxy/CDN 注入额外内容)也要触发 RateLimited,不能因
    HTML > 5KB 就漏判。"""
    # 模拟 6KB Error 页(原 1.5KB Error 页 + 大段填充)
    large_error_html = (
        "<!DOCTYPE html><html><head><title>Error</title></head>"
        + "<body><h1>Sorry, something went wrong.</h1>"
        + ("<!-- proxy filler -->" * 300)  # ~6KB 填充
        + "</body></html>"
    )
    assert len(large_error_html) > 5000
    with pytest.raises(RateLimited):
        parse_page_html(large_error_html)


def test_login_wall_raises_rate_limited():
    """重定向到 /login/ 的页面 — 当 transient(换 IP 重试)。"""
    # 注: 实际 httpx 已 follow redirect,这里测的是 FB 直接渲染登录页(没 redirect)
    login_html = """<!DOCTYPE html><html><head><title>Log into Facebook</title></head>
<body><form action="/login/"><input name="email" /></form></body></html>"""
    with pytest.raises(RateLimited):
        parse_page_html(login_html)


def test_page_not_found_raises_account_gone():
    """Page 不存在 — FB 渲染 'This content isn't available'。"""
    notfound_html = """<!DOCTYPE html><html><head><title>Facebook</title></head>
<body><div>This content isn't available right now</div></body></html>"""
    with pytest.raises(AccountGone):
        parse_page_html(notfound_html)


def test_page_not_found_curly_apostrophe_raises_account_gone():
    """FB 真实页面用 U+2019 弯引号 (isn't),正则必须能匹配。"""
    curly_html = "<html><body><div>This content isn't available right now</div></body></html>"
    with pytest.raises(AccountGone):
        parse_page_html(curly_html)


def test_page_no_reels_returns_empty_list():
    """Page 真的没发过 reel — title OK 但 video_id 正则空。
    不抛错,返回空列表(handler 据此入 fetch_logs success+found=0)。

    必须含 profile sentinel(profile_picture / profile_header_renderer),否则按
    test_generic_page_without_profile_sentinel_raises_account_gone 应该抛 AccountGone。
    """
    valid_no_reels = """<!DOCTYPE html><html><head>
<title>Some Page Reels</title></head>
<body><script>{"profile_picture":{"uri":"https://x/y.jpg"},"user":{"name":"SomePage"}}</script></body></html>"""
    profile, reel_ids = parse_page_html(valid_no_reels)
    assert reel_ids == []
    assert profile.display_name == "SomePage"


# ---------- 2026-05 事故回归:通用页 / 已删账号判 account_gone ----------
# id 438 (profile.php?id=61565022071183) 首次抓 account_gone 但后续两次回归 success 0/0:
# FB 把不存在的 numeric profile redirect 到通用页(没 "This content isn't available" 文案,
# 也没 reel 数据,但页面里夹杂的 JS bundle 名 "connection_quality" 被当 display_name)。
# 修复:reel_ids 为空且无 profile sentinel(profile_picture / profile_header_renderer)
# 时直接判 AccountGone,而不是 success 0/0。

def test_generic_page_without_profile_sentinel_raises_account_gone():
    """通用页/重定向后的非 profile 页 — 无 reel + 无 profile sentinel → AccountGone。
    模拟 facebook.com/Rinko%20Yamato 这类不存在 vanity 抓到的页面。"""
    generic_html = """<!DOCTYPE html><html><head><title>Facebook</title></head>
<body><script>{"name":"connection_quality","data":[]}</script>
<script>{"name":"VideoPlayerNextgendashWorkerEntrypointBundle"}</script></body></html>"""
    with pytest.raises(AccountGone):
        parse_page_html(generic_html)


def test_page_with_reels_passes_even_without_profile_sentinel():
    """已经能解出 reel JSON 的页面 — 100% 是真 page,不强求 profile sentinel。
    保护 FB 渲染样式以后变更的情况下不误判。"""
    reels_only = """<html><body><script>
{"video_id":"123456789012","other":"x"}
{"video_id":"234567890123"}
</script></body></html>"""
    profile, reel_ids = parse_page_html(reels_only)
    assert reel_ids == ["123456789012", "234567890123"]
    # profile 可以是 fallback 值,不抛错
    assert profile is not None


def test_real_fixture_still_passes_sentinel():
    """Cristiano fixture 是 page 真账号 — 改动后必须仍能解出 profile + reels。"""
    profile, reel_ids = parse_page_html(REAL_HTML)
    assert len(reel_ids) == 10
    assert profile.display_name is not None
