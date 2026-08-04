from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def media(**overrides):
    base = {
        "pk": "1",
        "id": "1",
        "code": "ABC123",
        "media_type": 2,
        "product_type": "clips",
        "caption_text": "caption",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "video_duration": 12.4,
        "play_count": 0,
        "view_count": 0,
        "like_count": 3,
        "comment_count": 4,
        "taken_at": datetime(2026, 5, 28, 4, 35, tzinfo=UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_media_to_video_marks_carousel_content():
    from app.scrapers.instagram import _media_to_video

    item = _media_to_video(
        media(
            pk="3906766724415088193",
            media_type=8,
            product_type="carousel_container",
            play_count=999,
            video_duration=21,
        )
    )

    assert item is not None
    assert item.video_id == "3906766724415088193"
    assert item.video_url == "https://www.instagram.com/p/ABC123/"
    assert item.content_type == "carousel"
    assert item.play_count == 0
    assert item.duration_seconds is None
    assert item.like_count == 3
    assert item.comment_count == 4
    assert item.published_at == datetime(2026, 5, 28, 4, 35, tzinfo=UTC)


def test_media_to_video_marks_regular_post_content():
    from app.scrapers.instagram import _media_to_video

    item = _media_to_video(media(media_type=1, product_type="feed", play_count=99))

    assert item is not None
    assert item.content_type == "post"
    assert item.video_url == "https://www.instagram.com/p/ABC123/"
    assert item.play_count == 0
    assert item.duration_seconds is None


def test_media_to_video_overlays_clip_metrics_for_reel():
    from app.scrapers.instagram import _media_to_video

    feed = media(pk="3900268079272056380", play_count=0, view_count=0, video_duration=0)
    clip = media(pk="3900268079272056380", play_count=115, video_duration=18.8)

    item = _media_to_video(feed, metrics_media=clip)

    assert item is not None
    assert item.content_type == "reel"
    assert item.video_url == "https://www.instagram.com/reel/ABC123/"
    assert item.play_count == 115
    assert item.duration_seconds == 18


def test_media_to_video_keeps_overlay_raw_for_reel():
    from app.scrapers.instagram import _media_to_video

    feed = media(pk="3900268079272056380", play_count=0, view_count=0, video_duration=0)
    clip = media(pk="3900268079272056380", play_count=115, video_duration=18.8)

    item = _media_to_video(feed, metrics_media=clip)

    assert item is not None
    assert item.raw["pk"] == "3900268079272056380"
    assert item.raw["_metrics_source"] == "clips"
    assert item.raw["_metrics_media"]["play_count"] == 115


def test_media_to_video_falls_back_to_feed_metrics_when_clip_metrics_are_zero():
    from app.scrapers.instagram import _media_to_video

    feed = media(pk="3900268079272056380", play_count=321, video_duration=14.5)
    clip = media(
        pk="3900268079272056380",
        play_count=0,
        view_count=0,
        video_duration=0,
    )

    item = _media_to_video(feed, metrics_media=clip)

    assert item is not None
    assert item.play_count == 321
    assert item.duration_seconds == 14
    assert item.raw["_metrics_source"] == "clips"


@pytest.mark.asyncio
async def test_fetch_instagram_uses_feed_and_clips_overlay(monkeypatch):
    import app.scrapers.instagram as ig

    calls: list[tuple[str, int, int]] = []

    class FakeClient:
        def user_info_by_username(self, username):
            assert username == "creator"
            return SimpleNamespace(
                pk=42,
                follower_count=10,
                following_count=2,
                media_count=3,
                full_name="Creator",
                profile_pic_url_hd="https://example.com/a.jpg",
                profile_pic_url="",
                biography="bio",
            )

        def user_medias_v1(self, user_pk, amount):
            calls.append(("feed", user_pk, amount))
            return [
                media(pk="1", media_type=8, product_type="carousel_container"),
                media(pk="2", media_type=2, product_type="clips", play_count=0),
            ]

        def user_clips_v1(self, user_pk, amount):
            calls.append(("clips", user_pk, amount))
            return [media(pk="2", media_type=2, product_type="clips", play_count=500)]

    async def fake_get_client():
        return FakeClient()

    async def immediate_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(ig, "_get_client", fake_get_client)
    monkeypatch.setattr(ig.asyncio, "to_thread", immediate_to_thread)

    profile, videos = await ig.fetch_instagram_videos("creator", max_count=2)

    assert profile.video_count == 3
    assert calls == [("feed", 42, 2), ("clips", 42, 2)]
    assert [v.content_type for v in videos] == ["carousel", "reel"]
    assert [v.play_count for v in videos] == [0, 500]


@pytest.mark.asyncio
async def test_fetch_instagram_skips_reel_when_clip_metrics_missing_and_feed_has_zero_views(
    monkeypatch,
):
    import app.scrapers.instagram as ig

    class FakeClient:
        def user_info_by_username(self, username):
            assert username == "creator"
            return SimpleNamespace(
                pk=42,
                follower_count=10,
                following_count=2,
                media_count=3,
                full_name="Creator",
                profile_pic_url_hd="https://example.com/a.jpg",
                profile_pic_url="",
                biography="bio",
            )

        def user_medias_v1(self, user_pk, amount):
            return [
                media(pk="1", media_type=8, product_type="carousel_container"),
                media(pk="2", media_type=2, product_type="clips", play_count=0),
            ]

        def user_clips_v1(self, user_pk, amount):
            return []

    async def fake_get_client():
        return FakeClient()

    async def immediate_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(ig, "_get_client", fake_get_client)
    monkeypatch.setattr(ig.asyncio, "to_thread", immediate_to_thread)

    _profile, videos = await ig.fetch_instagram_videos("creator", max_count=2)

    assert [v.content_type for v in videos] == ["carousel"]


@pytest.mark.asyncio
async def test_fetch_instagram_skips_reel_when_clip_and_feed_metrics_are_zero(
    monkeypatch,
):
    import app.scrapers.instagram as ig

    class FakeClient:
        def user_info_by_username(self, username):
            assert username == "creator"
            return SimpleNamespace(
                pk=42,
                follower_count=10,
                following_count=2,
                media_count=3,
                full_name="Creator",
                profile_pic_url_hd="https://example.com/a.jpg",
                profile_pic_url="",
                biography="bio",
            )

        def user_medias_v1(self, user_pk, amount):
            return [
                media(pk="1", media_type=8, product_type="carousel_container"),
                media(pk="2", media_type=2, product_type="clips", play_count=0),
            ]

        def user_clips_v1(self, user_pk, amount):
            return [
                media(
                    pk="2",
                    media_type=2,
                    product_type="clips",
                    play_count=0,
                    view_count=0,
                    video_duration=0,
                )
            ]

    async def fake_get_client():
        return FakeClient()

    async def immediate_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(ig, "_get_client", fake_get_client)
    monkeypatch.setattr(ig.asyncio, "to_thread", immediate_to_thread)

    _profile, videos = await ig.fetch_instagram_videos("creator", max_count=2)

    assert [v.content_type for v in videos] == ["carousel"]
