from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    monkeypatch.setattr(settings, "bright_data_tiktok_posts_dataset_id", "gd_m7n5v2gq296pex2f5m")
    monkeypatch.setattr(settings, "bright_data_instagram_reels_dataset_id", "gd_l1vikfch901nx3by4")
    monkeypatch.setattr(settings, "bright_data_youtube_videos_dataset_id", "gd_lk56epmy2i5g7lzu0k")


def test_normalizes_tiktok_profile_posts():
    from app.scrapers.bright_data_social import normalize_tiktok_records

    videos = normalize_tiktok_records(
        [
            {
                "post_id": "7553300000000000000",
                "description": "New challenge video is here #challenge",
                "create_time": "2025-02-01T10:00:00.000Z",
                "share_count": 95000,
                "comment_count": 18000,
                "play_count": 42000000,
                "digg_count": 1234,
                "video_duration": 90,
                "cover": "https://example.com/tk.jpg",
                "profile_username": "examplecreator",
            }
        ]
    )

    assert len(videos) == 1
    assert videos[0].video_id == "7553300000000000000"
    assert videos[0].video_url == "https://www.tiktok.com/@examplecreator/video/7553300000000000000"
    assert videos[0].description == "New challenge video is here #challenge"
    assert videos[0].thumbnail_url == "https://example.com/tk.jpg"
    assert videos[0].duration_seconds == 90
    assert videos[0].play_count == 42000000
    assert videos[0].like_count == 1234
    assert videos[0].comment_count == 18000
    assert videos[0].share_count == 95000
    assert videos[0].published_at == datetime(2025, 2, 1, 10, tzinfo=UTC)


def test_normalizes_instagram_profile_posts_with_content_types():
    from app.scrapers.bright_data_social import normalize_instagram_records

    profile, videos = normalize_instagram_records(
        [
            {
                "account": "ieltswithglad",
                "full_name": "IELTS with Glad",
                "followers": 50,
                "posts_count": 34,
                "profile_image_link": "https://example.com/avatar.jpg",
                "posts": [
                    {
                        "id": "3906766724415088193",
                        "url": "https://www.instagram.com/p/DY3n6MNE-ZB/",
                        "caption": "Carousel caption",
                        "content_type": "Carousel",
                        "datetime": "2026-05-28T04:35:07.000Z",
                        "likes": 5,
                        "comments": 2,
                        "image_url": "https://example.com/carousel.jpg",
                    },
                    {
                        "post_id": "3900268079272056380",
                        "url": "https://www.instagram.com/reel/DYgiSdRzlY8/",
                        "description": "Reel caption",
                        "content_type": "Video",
                        "video_view_count": 115,
                        "video_duration": 18.8,
                    },
                ],
            }
        ]
    )

    assert profile.display_name == "IELTS with Glad"
    assert profile.follower_count == 50
    assert profile.video_count == 34
    assert profile.avatar_url == "https://example.com/avatar.jpg"
    assert [v.video_id for v in videos] == ["3906766724415088193", "3900268079272056380"]
    assert [v.content_type for v in videos] == ["carousel", "reel"]
    assert videos[0].play_count == 0
    assert videos[0].comment_count == 2
    assert videos[0].published_at == datetime(2026, 5, 28, 4, 35, 7, tzinfo=UTC)
    assert videos[1].play_count == 115
    assert videos[1].duration_seconds == 18


def test_instagram_profile_posts_keep_feed_videos_without_metrics():
    from app.scrapers.bright_data_social import normalize_instagram_records

    profile, videos = normalize_instagram_records(
        [
            {
                "account": "creator",
                "full_name": "Creator Name",
                "followers": 10,
                "posts_count": 2,
                "profile_pic_url": "https://example.com/avatar.jpg",
                "posts": [
                    {
                        "id": "image-post",
                        "url": "https://www.instagram.com/p/IMAGE/",
                        "caption": "Image caption",
                        "content_type": "Image",
                        "datetime": "2026-05-01T10:00:00.000Z",
                        "comments": 7,
                        "likes": 11,
                    },
                    {
                        "id": "video-post",
                        "url": "https://www.instagram.com/p/VIDEO/",
                        "caption": "Video caption",
                        "content_type": "Video",
                        "datetime": "2026-05-02T10:00:00.000Z",
                        "video_url": "https://example.com/video.mp4",
                    },
                ],
            }
        ]
    )

    assert profile.display_name == "Creator Name"
    assert profile.follower_count == 10
    assert [v.video_id for v in videos] == ["image-post", "video-post"]
    assert [v.content_type for v in videos] == ["post", "post"]
    assert videos[0].comment_count == 7
    assert videos[0].published_at == datetime(2026, 5, 1, 10, tzinfo=UTC)
    assert videos[1].play_count == 0
    assert videos[1].published_at == datetime(2026, 5, 2, 10, tzinfo=UTC)


def test_instagram_profile_empty_posts_preserves_profile():
    from app.scrapers.bright_data_social import normalize_instagram_records

    profile, videos = normalize_instagram_records(
        [
            {
                "account": "creator",
                "full_name": "Creator Name",
                "followers": 10,
                "posts_count": 0,
                "posts": [],
            }
        ]
    )

    assert profile.display_name == "Creator Name"
    assert profile.follower_count == 10
    assert profile.video_count == 0
    assert videos == []


def test_instagram_profile_missing_posts_does_not_create_fake_content():
    from app.scrapers.bright_data_social import normalize_instagram_records

    profile, videos = normalize_instagram_records(
        [
            {
                "id": "profile-id",
                "account": "creator",
                "full_name": "Creator Name",
                "followers": 10,
                "url": "https://www.instagram.com/creator/",
                "posts": None,
            }
        ]
    )

    assert profile.display_name == "Creator Name"
    assert profile.follower_count == 10
    assert videos == []


def test_instagram_duplicate_reel_keeps_record_with_metrics():
    from app.scrapers.bright_data_social import normalize_instagram_records

    _, videos = normalize_instagram_records(
        [
            {
                "id": "reel-post",
                "url": "https://www.instagram.com/reel/VIDEO/",
                "content_type": "Video",
                "video_url": "https://example.com/video.mp4",
            },
            {
                "id": "reel-post",
                "url": "https://www.instagram.com/reel/VIDEO/",
                "content_type": "Video",
                "video_view_count": 42,
            },
        ]
    )

    assert len(videos) == 1
    assert videos[0].video_id == "reel-post"
    assert videos[0].content_type == "reel"
    assert videos[0].play_count == 42


def test_normalizes_youtube_records_and_filters_non_shorts():
    from app.scrapers.bright_data_social import normalize_youtube_records

    videos = normalize_youtube_records(
        [
            {
                "url": "https://www.youtube.com/shorts/abc123",
                "title": "Short title",
                "description": "Short description",
                "video_length": 59,
                "likes": 18,
                "views": 1648,
                "num_comments": 3,
                "date_posted": "2025-04-18T05:26:16.000Z",
                "video_id": "abc123",
                "preview_image": "https://example.com/yt.jpg",
            },
            {
                "url": "https://www.youtube.com/watch?v=long123",
                "video_id": "long123",
                "video_length": 600,
                "views": 999,
            },
        ]
    )

    assert len(videos) == 1
    assert videos[0].video_id == "abc123"
    assert videos[0].video_url == "https://www.youtube.com/shorts/abc123"
    assert videos[0].description == "Short description"
    assert videos[0].thumbnail_url == "https://example.com/yt.jpg"
    assert videos[0].duration_seconds == 59
    assert videos[0].play_count == 1648
    assert videos[0].like_count == 18
    assert videos[0].comment_count == 3


@pytest.mark.asyncio
async def test_fetch_tiktok_uses_profile_posts_dataset(monkeypatch):
    from app.scrapers.bright_data_social import fetch_tiktok_videos_via_bright_data

    scrape = AsyncMock(return_value=[])
    monkeypatch.setattr("app.scrapers.bright_data_social.scrape_bright_data_records", scrape)

    await fetch_tiktok_videos_via_bright_data("examplecreator", max_count=5)

    scrape.assert_awaited_once_with(
        dataset_id="gd_m7n5v2gq296pex2f5m",
        inputs=[{"url": "https://www.tiktok.com/@examplecreator", "num_of_posts": 5}],
        params={},
    )


@pytest.mark.asyncio
async def test_fetch_instagram_uses_user_name_discover_params(monkeypatch):
    from app.scrapers.bright_data_social import fetch_instagram_videos_via_bright_data

    # IG discover 走异步 trigger（同步 scrape 对 discover 会卡 starting 永不就绪）
    trigger = AsyncMock(return_value=[])
    monkeypatch.setattr("app.scrapers.bright_data_social.trigger_bright_data_records", trigger)

    await fetch_instagram_videos_via_bright_data("creator", max_count=7)

    trigger.assert_awaited_once_with(
        dataset_id="gd_l1vikfch901nx3by4",
        inputs=[{"user_name": "creator"}],
        params={"type": "discover_new", "discover_by": "user_name"},
    )


@pytest.mark.asyncio
async def test_fetch_youtube_uses_shorts_url_and_discover_params(monkeypatch):
    from app.scrapers.bright_data_social import fetch_youtube_shorts_via_bright_data

    scrape = AsyncMock(return_value=[])
    monkeypatch.setattr("app.scrapers.bright_data_social.scrape_bright_data_records", scrape)

    await fetch_youtube_shorts_via_bright_data("mkbhd", max_count=9)

    scrape.assert_awaited_once_with(
        dataset_id="gd_lk56epmy2i5g7lzu0k",
        inputs=[{"url": "https://www.youtube.com/@mkbhd/shorts"}],
        params={"type": "discover_new", "discover_by": "url"},
    )


def test_snapshot_id_accepts_sd_prefix():
    """Bright Data 实际返回的 snapshot_id 前缀是 sd_(非 s_),必须被识别。"""
    from app.scrapers.bright_data_social import _snapshot_id

    assert _snapshot_id({"snapshot_id": "sd_mpqqlv2syn8qn7dpk"}) == "sd_mpqqlv2syn8qn7dpk"
    assert _snapshot_id({"snapshot_id": "s_123"}) == "s_123"
