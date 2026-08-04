from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.scrapers.errors import CredsMissing, RateLimited


def _sample_record(**overrides):
    record = {
        "url": "https://www.facebook.com/reel/2128461008004479",
        "post_id": "1417440860423673",
        "content": "Increase in US-Iran tensions...",
        "date_posted": "2026-04-06T00:00:00.000Z",
        "num_comments": 2,
        "num_shares": 4,
        "video_view_count": 582,
        "likes": 3,
        "page_name": "Radio Transamerica FM",
        "profile_id": "100064733164731",
        "page_logo": "https://logo.example/fb.jpg",
        "page_followers": 1200000,
        "thumbnail": "https://thumb.example/fb.jpg",
        "page_url": "https://www.facebook.com/tmcnewsbr",
        "profile_handle": "tmcnewsbr",
        "shortcode": "2128461008004479",
        "length": 13.888,
        "video_url": "https://video.example/fb.mp4",
    }
    record.update(overrides)
    return record


@pytest.fixture(autouse=True)
def _reset_bright_data_settings(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", None)
    monkeypatch.setattr(
        settings,
        "bright_data_facebook_reels_dataset_id",
        "gd_lyclm3ey2q6rww027t",
    )
    monkeypatch.setattr(settings, "bright_data_timeout_seconds", 120)
    monkeypatch.setattr(settings, "bright_data_poll_interval_seconds", 0)
    monkeypatch.setattr(settings, "bright_data_max_polls", 2)


def test_maps_bright_data_record_to_video_and_profile():
    from app.scrapers.bright_data_facebook import normalize_bright_data_records

    profile, videos = normalize_bright_data_records([_sample_record()])

    assert profile.display_name == "Radio Transamerica FM"
    assert profile.avatar_url == "https://logo.example/fb.jpg"
    assert profile.follower_count == 1200000
    assert profile.following_count == 0
    assert profile.video_count == 0

    assert len(videos) == 1
    video = videos[0]
    assert video.video_id == "2128461008004479"
    assert video.video_url == "https://www.facebook.com/reel/2128461008004479"
    assert video.description == "Increase in US-Iran tensions..."
    assert video.thumbnail_url == "https://thumb.example/fb.jpg"
    assert video.duration_seconds == 13
    assert video.play_count == 582
    assert video.like_count == 3
    assert video.comment_count == 2
    assert video.share_count == 4
    assert video.published_at == datetime(2026, 4, 6, tzinfo=UTC)
    assert video.raw["profile_handle"] == "tmcnewsbr"


def test_likes_fallback_sums_reaction_counts():
    from app.scrapers.bright_data_facebook import normalize_bright_data_records

    record = _sample_record(
        likes=None,
        num_likes_type={"num": 34, "type": "Like"},
        count_reactions_type=[
            {"reaction_count": 34, "type": "Like"},
            {"reaction_count": 4, "type": "Haha"},
        ],
    )

    _, videos = normalize_bright_data_records([record])

    assert videos[0].like_count == 38


def test_missing_api_key_raises_creds_missing():
    from app.scrapers.bright_data_facebook import _require_api_key

    with pytest.raises(CredsMissing):
        _require_api_key()


@pytest.mark.asyncio
async def test_fetch_reels_sync_200(monkeypatch):
    from app.scrapers.bright_data_facebook import fetch_facebook_reels_via_bright_data

    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    post = AsyncMock(return_value=([_sample_record()], None))
    monkeypatch.setattr("app.scrapers.bright_data_facebook._post_scrape", post)

    profile, videos = await fetch_facebook_reels_via_bright_data(
        "https://www.facebook.com/tmcnewsbr",
        max_count=7,
    )

    assert profile.display_name == "Radio Transamerica FM"
    assert [v.video_id for v in videos] == ["2128461008004479"]
    post.assert_awaited_once_with(
        api_key="bd_test_token",
        dataset_id="gd_lyclm3ey2q6rww027t",
        profile_url="https://www.facebook.com/tmcnewsbr",
        max_count=7,
    )


@pytest.mark.asyncio
async def test_fetch_reels_polls_and_downloads_202_snapshot(monkeypatch):
    from app.scrapers.bright_data_facebook import fetch_facebook_reels_via_bright_data

    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    monkeypatch.setattr(
        "app.scrapers.bright_data_facebook._post_scrape",
        AsyncMock(return_value=(None, "s_123")),
    )
    progress = AsyncMock(return_value="ready")
    download = AsyncMock(return_value=[_sample_record(shortcode="999")])
    monkeypatch.setattr("app.scrapers.bright_data_facebook._get_progress", progress)
    monkeypatch.setattr("app.scrapers.bright_data_facebook._download_snapshot", download)

    _, videos = await fetch_facebook_reels_via_bright_data(
        "https://www.facebook.com/tmcnewsbr",
        max_count=1,
    )

    assert [v.video_id for v in videos] == ["999"]
    progress.assert_awaited_once_with(api_key="bd_test_token", snapshot_id="s_123")
    download.assert_awaited_once_with(api_key="bd_test_token", snapshot_id="s_123")


@pytest.mark.asyncio
async def test_fetch_reels_snapshot_not_ready_raises_rate_limited(monkeypatch):
    from app.scrapers.bright_data_facebook import fetch_facebook_reels_via_bright_data

    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    monkeypatch.setattr(
        "app.scrapers.bright_data_facebook._post_scrape",
        AsyncMock(return_value=(None, "s_123")),
    )
    monkeypatch.setattr(
        "app.scrapers.bright_data_facebook._get_progress",
        AsyncMock(return_value="running"),
    )

    with pytest.raises(RateLimited):
        await fetch_facebook_reels_via_bright_data(
            "https://www.facebook.com/tmcnewsbr",
            max_count=1,
        )


def test_snapshot_id_accepts_sd_prefix():
    """Bright Data 实际返回的 snapshot_id 前缀是 sd_(非 s_),必须被识别。"""
    from app.scrapers.bright_data_facebook import _snapshot_id

    assert _snapshot_id({"snapshot_id": "sd_mpqp5zu21yfl0txlpv"}) == "sd_mpqp5zu21yfl0txlpv"
    assert _snapshot_id({"snapshot_id": "s_123"}) == "s_123"
