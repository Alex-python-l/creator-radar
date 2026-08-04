"""PlatformAdapter contract tests.

Every registered adapter must:
  - Validate captions to ≤ max_caption_len (units, not chars, for YouTube).
  - Submit via the correct GeeLark client method.
  - Map raw GeeLark task status to the right outcome state.

YouTubeAdapter has extra behaviour:
  - Empty title → "Short" fallback.
  - Truncate to UTF-16 unit count (handles emoji / multibyte safely).
  - verify_real records the final RPA screenshot as audit_screenshot
    on every status=3 completion (even when shareLink is empty).
"""
import pytest

from app.infra import platforms as platform_adapters
from app.infra.platforms import (
    EngagementContext,
    EngagementNotSupported,
    PublishContext,
    RawTaskStatus,
    ValidatedCaption,
)
from app.infra.platforms.facebook import (
    FB_KEYWORD_MAX_COUNT,
    FB_KEYWORD_MAX_LEN,
    FB_POST_ADDRESS_MAX_LEN,
)
from app.infra.platforms.youtube import (
    YOUTUBE_TITLE_FALLBACK,
    YOUTUBE_TITLE_SAFE_UTF16_UNITS,
    _utf16_units,
)


def test_registry_has_four_platforms():
    assert sorted(platform_adapters.all_platforms()) == [
        "facebook", "instagram", "tiktok", "youtube",
    ]


def test_get_unknown_platform_raises():
    with pytest.raises(KeyError):
        platform_adapters.get("snapchat")


# ---------------------------------------------------------------------------
# validate_caption
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("platform,expected_max", [
    ("tiktok", 2200),
    ("instagram", 2200),
    ("facebook", 500),
])
def test_truncating_adapters_respect_max_caption_len(platform, expected_max):
    adapter = platform_adapters.get(platform)
    assert adapter.max_caption_len == expected_max
    result = adapter.validate_caption("a" * 5000)
    assert isinstance(result, ValidatedCaption)
    assert len(result.text) == expected_max


def test_truncating_adapters_pass_through_short_caption():
    for p in ("tiktok", "instagram", "facebook"):
        adapter = platform_adapters.get(p)
        result = adapter.validate_caption("hello world")
        assert result.text == "hello world"


def test_truncating_adapters_normalize_none_to_empty():
    for p in ("tiktok", "instagram", "facebook"):
        adapter = platform_adapters.get(p)
        assert adapter.validate_caption(None).text == ""


def test_youtube_validate_caption_truncates_to_utf16_units():
    adapter = platform_adapters.get("youtube")
    title = "x" * 200
    result = adapter.validate_caption(title)
    assert _utf16_units(result.text) <= YOUTUBE_TITLE_SAFE_UTF16_UNITS


def test_youtube_validate_caption_handles_emoji_safely():
    """Each emoji is 2 UTF-16 units. 50 emojis = 100 units, must fit
    under the 90-unit cap."""
    adapter = platform_adapters.get("youtube")
    emoji_title = "⚽" * 50
    result = adapter.validate_caption(emoji_title)
    assert _utf16_units(result.text) <= YOUTUBE_TITLE_SAFE_UTF16_UNITS


def test_youtube_validate_caption_empty_uses_fallback():
    adapter = platform_adapters.get("youtube")
    assert adapter.validate_caption("").text == YOUTUBE_TITLE_FALLBACK
    assert adapter.validate_caption("   ").text == YOUTUBE_TITLE_FALLBACK
    assert adapter.validate_caption(None).text == YOUTUBE_TITLE_FALLBACK


# ---------------------------------------------------------------------------
# submit_publish — each adapter routes to the right GeeLark client method
# ---------------------------------------------------------------------------
class _CapturingClient:
    """Records which create_*_post_task got called and with what args."""
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def create_video_post_task(self, env_id, video_url, video_desc=None):
        self.calls.append(("tiktok", {
            "env_id": env_id, "video_url": video_url,
            "video_desc": video_desc,
        }))
        return "tk-1"

    def create_youtube_post_task(self, env_id, video_url, title):
        self.calls.append(("youtube", {
            "env_id": env_id, "video_url": video_url, "title": title,
        }))
        return "yt-1"

    def create_instagram_post_task(self, env_id, video_urls, description):
        self.calls.append(("instagram", {
            "env_id": env_id, "video_urls": video_urls,
            "description": description,
        }))
        return "ig-1"

    def create_facebook_post_task(self, env_id, video_url, description):
        self.calls.append(("facebook", {
            "env_id": env_id, "video_url": video_url,
            "description": description,
        }))
        return "fb-1"


def _publish_ctx(caption_text: str) -> PublishContext:
    return PublishContext(
        env_id="env-1",
        resource_url="https://geelark.example/video.mp4",
        caption=ValidatedCaption(text=caption_text),
    )


def test_tiktok_submit_publish():
    adapter = platform_adapters.get("tiktok")
    client = _CapturingClient()
    task_id = adapter.submit_publish(client, _publish_ctx("hello"))
    assert task_id == "tk-1"
    assert client.calls == [("tiktok", {
        "env_id": "env-1",
        "video_url": "https://geelark.example/video.mp4",
        "video_desc": "hello",
    })]


def test_youtube_submit_publish():
    adapter = platform_adapters.get("youtube")
    client = _CapturingClient()
    task_id = adapter.submit_publish(client, _publish_ctx("Hi"))
    assert task_id == "yt-1"
    assert client.calls == [("youtube", {
        "env_id": "env-1",
        "video_url": "https://geelark.example/video.mp4",
        "title": "Hi",
    })]


def test_instagram_submit_publish_wraps_url_in_list():
    adapter = platform_adapters.get("instagram")
    client = _CapturingClient()
    adapter.submit_publish(client, _publish_ctx("caption"))
    assert client.calls == [("instagram", {
        "env_id": "env-1",
        "video_urls": ["https://geelark.example/video.mp4"],
        "description": "caption",
    })]


def test_facebook_submit_publish():
    adapter = platform_adapters.get("facebook")
    client = _CapturingClient()
    adapter.submit_publish(client, _publish_ctx("desc"))
    assert client.calls == [("facebook", {
        "env_id": "env-1",
        "video_url": "https://geelark.example/video.mp4",
        "description": "desc",
    })]


# ---------------------------------------------------------------------------
# verify_real — the shared "trust GeeLark" path
# ---------------------------------------------------------------------------
class _StubClient:
    def __init__(self, detail=None, raises=False):
        self._detail = detail or {}
        self._raises = raises

    def get_task_detail(self, task_id):
        if self._raises:
            raise RuntimeError("detail down")
        return self._detail


@pytest.mark.parametrize("platform", ["tiktok", "instagram", "facebook"])
def test_default_verify_real_success(platform):
    adapter = platform_adapters.get(platform)
    raw = RawTaskStatus(
        task_id="t1", status=3, share_link="https://share.x/abc")
    outcome = adapter.verify_real(_StubClient(), raw)
    assert outcome.state == "success"
    assert outcome.share_link == "https://share.x/abc"
    assert outcome.observed_status == "3"
    # No audit screenshot for non-YouTube platforms.
    assert outcome.audit_screenshot is None


@pytest.mark.parametrize("platform", ["tiktok", "instagram", "facebook"])
def test_default_verify_real_running(platform):
    adapter = platform_adapters.get(platform)
    raw = RawTaskStatus(task_id="t1", status=2)
    outcome = adapter.verify_real(_StubClient(), raw)
    assert outcome.state == "running"
    assert outcome.observed_status == "2"


@pytest.mark.parametrize("platform,status", [
    ("tiktok", 4), ("tiktok", 7),
    ("instagram", 4), ("facebook", 4),
])
def test_default_verify_real_failure_with_detail(platform, status):
    adapter = platform_adapters.get(platform)
    raw = RawTaskStatus(task_id="t1", status=status)
    client = _StubClient(detail={
        "failCode": 20267,
        "failDesc": "Instagram REEL too long",
        "resultImages": ["a.png", "b.png", "final.png"],
    })
    outcome = adapter.verify_real(client, raw)
    assert outcome.state == "failure"
    assert outcome.fail_code == 20267
    assert outcome.fail_reason == "Instagram REEL too long"
    assert outcome.fail_screenshot == "final.png"
    assert outcome.observed_status == str(status)


def test_default_verify_real_failure_without_detail_endpoint():
    """A client whose get_task_detail raises must still resolve to
    failure — never block reconciliation."""
    adapter = platform_adapters.get("tiktok")
    raw = RawTaskStatus(task_id="t1", status=4)
    outcome = adapter.verify_real(_StubClient(raises=True), raw)
    assert outcome.state == "failure"
    assert outcome.fail_code is None
    assert outcome.fail_reason == "task status 4"
    assert outcome.observed_status == "4"


# ---------------------------------------------------------------------------
# YouTube false-success audit
# ---------------------------------------------------------------------------
def test_youtube_verify_real_records_audit_screenshot():
    """When YouTube reports status=3, the final RPA screenshot MUST land
    on the outcome so operators can spot 101/100 dialogs after the fact.
    (Plan 2026-06-18 §Task 3.)"""
    adapter = platform_adapters.get("youtube")
    raw = RawTaskStatus(
        task_id="yt-1", status=3, share_link="")
    client = _StubClient(detail={
        "status": 3,
        "resultImages": ["intermediate.jpg", "final.jpg"],
    })
    outcome = adapter.verify_real(client, raw)
    assert outcome.state == "success"
    assert outcome.observed_status == "3"
    assert outcome.audit_screenshot == "final.jpg"
    # share_link is empty string → outcome.share_link normalized to None
    assert outcome.share_link is None


def test_youtube_verify_real_handles_detail_outage():
    """A transient detail-API hiccup must NOT block the success path —
    audit_screenshot just stays None."""
    adapter = platform_adapters.get("youtube")
    raw = RawTaskStatus(task_id="yt-1", status=3, share_link="https://yt/x")
    outcome = adapter.verify_real(_StubClient(raises=True), raw)
    assert outcome.state == "success"
    assert outcome.share_link == "https://yt/x"
    assert outcome.audit_screenshot is None


def test_youtube_verify_real_failure_path_unchanged():
    adapter = platform_adapters.get("youtube")
    raw = RawTaskStatus(task_id="yt-1", status=4)
    client = _StubClient(detail={
        "failCode": 20290,
        "failDesc": "video upload rejected",
        "resultImages": ["a.png"],
    })
    outcome = adapter.verify_real(client, raw)
    assert outcome.state == "failure"
    assert outcome.fail_code == 20290
    assert outcome.fail_reason == "video upload rejected"
    assert outcome.fail_screenshot == "a.png"


# ---------------------------------------------------------------------------
# Engagement capability flags
# ---------------------------------------------------------------------------
def test_supports_engagement_flags():
    """Only TikTok and Facebook have a GeeLark comment action today."""
    assert platform_adapters.get("tiktok").supports_engagement is True
    assert platform_adapters.get("facebook").supports_engagement is True
    assert platform_adapters.get("youtube").supports_engagement is False
    assert platform_adapters.get("instagram").supports_engagement is False


def test_engagement_action_names_match_geelark_actions():
    assert platform_adapters.get("tiktok").engagement_action == "tiktok_ai_comment"
    assert platform_adapters.get("facebook").engagement_action == "facebook_auto_comment"
    assert platform_adapters.get("youtube").engagement_action == ""
    assert platform_adapters.get("instagram").engagement_action == ""


def test_daily_limit_setting_keys():
    assert platform_adapters.get("tiktok").daily_limit_setting_key == "daily_comment_limit_tiktok"
    assert platform_adapters.get("facebook").daily_limit_setting_key == "daily_comment_limit_facebook"
    assert platform_adapters.get("youtube").daily_limit_setting_key is None
    assert platform_adapters.get("instagram").daily_limit_setting_key is None


# ---------------------------------------------------------------------------
# TikTok engagement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("link", [
    "https://www.tiktok.com/@alice/video/7234567890123456789",
    "https://tiktok.com/@bob/video/7234567890123456789",
    "https://www.tiktok.com/@charlie/video/123/?_r=1",
    "https://vt.tiktok.com/ZSeXXXX",
    "https://vt.tiktok.com/ZSe1234/",
])
def test_tiktok_normalize_target_link_accepts_valid(link):
    adapter = platform_adapters.get("tiktok")
    assert adapter.normalize_target_link(link) == link


@pytest.mark.parametrize("link", [
    "",
    "not-a-url",
    "https://www.instagram.com/reel/abc",
    "https://www.tiktok.com/foo",
    "https://www.tiktok.com/@user/photo/123",
])
def test_tiktok_normalize_target_link_rejects_invalid(link):
    adapter = platform_adapters.get("tiktok")
    with pytest.raises(ValueError, match="TikTok video URL"):
        adapter.normalize_target_link(link)


class _EngagementCapturingClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def create_tiktok_ai_comment_task(
            self, env_id, links, *, schedule_at,
            comment_probability, comment, name, remark):
        self.calls.append(("tiktok", {
            "env_id": env_id, "links": links,
            "schedule_at": schedule_at,
            "comment_probability": comment_probability,
            "comment": comment, "name": name, "remark": remark,
        }))
        return "tt-eng-1"

    def create_facebook_auto_comment_task(
            self, env_id, *, post_address, comments,
            keywords, schedule_at, name, remark):
        self.calls.append(("facebook", {
            "env_id": env_id, "post_address": post_address,
            "comments": comments, "keywords": keywords,
            "schedule_at": schedule_at, "name": name, "remark": remark,
        }))
        return "fb-eng-1"


def test_tiktok_submit_engagement_routes_with_comment():
    adapter = platform_adapters.get("tiktok")
    client = _EngagementCapturingClient()
    ctx = EngagementContext(
        env_id="env-1",
        target_link="https://www.tiktok.com/@a/video/123",
        schedule_at=1700000000,
        name="Studio Comment",
        remark="engagement_run_id=42",
        comment_probability=80,
        comment_text="hello world",
    )
    task_id = adapter.submit_engagement(client, ctx)
    assert task_id == "tt-eng-1"
    assert client.calls == [("tiktok", {
        "env_id": "env-1",
        "links": ["https://www.tiktok.com/@a/video/123"],
        "schedule_at": 1700000000,
        "comment_probability": 80,
        "comment": "hello world",
        "name": "Studio Comment",
        "remark": "engagement_run_id=42",
    })]


def test_tiktok_submit_engagement_routes_without_comment():
    """comment_text=None means GeeLark's own AI authors the comment."""
    adapter = platform_adapters.get("tiktok")
    client = _EngagementCapturingClient()
    ctx = EngagementContext(
        env_id="env-1",
        target_link="https://www.tiktok.com/@a/video/123",
        schedule_at=1700000000,
        name="GeeLark AI",
        remark="x",
    )
    adapter.submit_engagement(client, ctx)
    assert client.calls[0][1]["comment"] is None
    assert client.calls[0][1]["comment_probability"] == 100  # default


# ---------------------------------------------------------------------------
# Facebook engagement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("link", [
    "https://www.facebook.com/zuck/posts/12345",
    "https://facebook.com/zuck/posts/abc",
    "https://web.facebook.com/groups/devs/posts/789",
    "https://m.facebook.com/reel/abcdef",
    "https://fb.watch/AbCdEfGh",
    "https://fb.com/xyz",
])
def test_facebook_normalize_target_link_accepts_valid(link):
    adapter = platform_adapters.get("facebook")
    assert adapter.normalize_target_link(link) == link


@pytest.mark.parametrize("link,err", [
    ("", "Facebook post/reel URL"),
    ("https://www.tiktok.com/@a/video/123", "Facebook post/reel URL"),
    ("https://example.com/post", "Facebook post/reel URL"),
])
def test_facebook_normalize_target_link_rejects_invalid(link, err):
    adapter = platform_adapters.get("facebook")
    with pytest.raises(ValueError, match=err):
        adapter.normalize_target_link(link)


def test_facebook_normalize_target_link_rejects_too_long_url():
    adapter = platform_adapters.get("facebook")
    base = "https://www.facebook.com/zuck/posts/"
    long_link = base + "x" * (FB_POST_ADDRESS_MAX_LEN + 1)
    assert len(long_link) > FB_POST_ADDRESS_MAX_LEN
    with pytest.raises(ValueError, match="exceeds GeeLark API limit"):
        adapter.normalize_target_link(long_link)


def test_facebook_submit_engagement_routes():
    adapter = platform_adapters.get("facebook")
    client = _EngagementCapturingClient()
    ctx = EngagementContext(
        env_id="env-2",
        target_link="https://www.facebook.com/zuck/posts/abc",
        schedule_at=1700000000,
        name="FB Comment",
        remark="engagement_run_id=88",
        candidates=["Nice", "Cool"],
        keywords=["dev", "english"],
    )
    task_id = adapter.submit_engagement(client, ctx)
    assert task_id == "fb-eng-1"
    assert client.calls == [("facebook", {
        "env_id": "env-2",
        "post_address": "https://www.facebook.com/zuck/posts/abc",
        "comments": ["Nice", "Cool"],
        "keywords": ["dev", "english"],
        "schedule_at": 1700000000,
        "name": "FB Comment",
        "remark": "engagement_run_id=88",
    })]


# ---------------------------------------------------------------------------
# YouTube / Instagram are explicitly engagement-incapable
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("platform", ["youtube", "instagram"])
def test_engagement_unsupported_platforms_raise(platform):
    adapter = platform_adapters.get(platform)
    with pytest.raises(EngagementNotSupported):
        adapter.normalize_target_link("https://any/")
    with pytest.raises(EngagementNotSupported):
        adapter.submit_engagement(_EngagementCapturingClient(), EngagementContext(
            env_id="env-y", target_link="https://any/",
            schedule_at=0, name="x", remark="y",
        ))
