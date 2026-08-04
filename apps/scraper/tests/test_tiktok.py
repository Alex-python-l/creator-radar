from datetime import UTC, datetime

import pytest

from app.config import settings
from app.scrapers.errors import RateLimited
from app.scrapers.tiktok import ScrapedVideo, fetch_tiktok_videos


def _video(video_id: str) -> ScrapedVideo:
    return ScrapedVideo(
        video_id=video_id,
        video_url=f"https://www.tiktok.com/@creator/video/{video_id}",
        description="",
        thumbnail_url=None,
        duration_seconds=12,
        play_count=1,
        like_count=0,
        comment_count=0,
        share_count=0,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        raw={"id": video_id},
    )


@pytest.mark.asyncio
async def test_fetch_tiktok_falls_back_to_bright_data_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    monkeypatch.setattr(
        "app.scrapers.tiktok.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1"],
    )

    async def fail_fetch(username: str, max_count: int | None, proxy: str | None):
        raise RuntimeError("Unable to extract secondary user ID")

    async def fake_bright_data(username: str, max_count: int | None):
        assert username == "creator"
        assert max_count == 3
        return [_video("bd-tk-1")]

    monkeypatch.setattr("app.scrapers.tiktok._fetch_videos_ytdlp", fail_fetch)
    monkeypatch.setattr(
        "app.scrapers.tiktok.fetch_tiktok_videos_via_bright_data",
        fake_bright_data,
        raising=False,
    )

    videos = await fetch_tiktok_videos("creator", max_count=3)

    assert [v.video_id for v in videos] == ["bd-tk-1"]


@pytest.mark.asyncio
async def test_fetch_tiktok_without_bright_data_keeps_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", None)
    monkeypatch.setattr(
        "app.scrapers.tiktok.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1"],
    )

    async def fail_fetch(username: str, max_count: int | None, proxy: str | None):
        raise RuntimeError("Unable to extract secondary user ID")

    monkeypatch.setattr("app.scrapers.tiktok._fetch_videos_ytdlp", fail_fetch)

    with pytest.raises(RateLimited):
        await fetch_tiktok_videos("creator", max_count=3)
