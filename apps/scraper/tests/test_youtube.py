"""YouTube Shorts scraper tests.

第一版只抓频道 Shorts,不登录、不下载视频文件。
"""
import threading
import time
from datetime import UTC, datetime

import pytest

from app.config import settings
from app.scrapers.errors import RateLimited
from app.scrapers.youtube import (
    _entry_to_video_ytdlp,
    _fetch_videos_ytdlp,
    _shorts_url,
    fetch_youtube_shorts,
)


def test_shorts_url_for_handle_and_at_handle():
    assert _shorts_url("mkbhd") == "https://www.youtube.com/@mkbhd/shorts"
    assert _shorts_url("@MrBeast") == "https://www.youtube.com/@MrBeast/shorts"


def test_shorts_url_for_channel_c_and_user_identifiers():
    assert (
        _shorts_url("channel/UC_x5XG1OV2P6uZZ5FSM9Ttw")
        == "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw/shorts"
    )
    assert _shorts_url("c/Veritasium") == "https://www.youtube.com/c/Veritasium/shorts"
    assert _shorts_url("user/Google") == "https://www.youtube.com/user/Google/shorts"


def test_entry_to_video_maps_ytdlp_fields():
    video = _entry_to_video_ytdlp(
        {
            "id": "abc123",
            "webpage_url": "https://www.youtube.com/shorts/abc123",
            "title": "Short title",
            "description": "Short description",
            "thumbnail": "https://img.youtube.com/vi/abc123/hqdefault.jpg",
            "duration": 41,
            "view_count": 12345,
            "like_count": 678,
            "comment_count": 9,
            "timestamp": 1767225600,
        }
    )

    assert video is not None
    assert video.video_id == "abc123"
    assert video.video_url == "https://www.youtube.com/shorts/abc123"
    assert video.description == "Short description"
    assert video.thumbnail_url == "https://img.youtube.com/vi/abc123/hqdefault.jpg"
    assert video.duration_seconds == 41
    assert video.play_count == 12345
    assert video.like_count == 678
    assert video.comment_count == 9
    assert video.share_count == 0
    assert video.published_at == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fetch_youtube_shorts_tries_proxy_candidates_then_rate_limited(monkeypatch):
    attempts: list[str | None] = []

    monkeypatch.setattr(
        "app.scrapers.youtube.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1", "http://p2"],
    )

    async def fail_fetch(
        username: str,
        max_count: int | None,
        proxy: str | None,
        detail_proxies: list[str | None] | None = None,
    ):
        attempts.append(proxy)
        raise RuntimeError(f"blocked via {proxy}")

    monkeypatch.setattr("app.scrapers.youtube._fetch_videos_ytdlp", fail_fetch)

    with pytest.raises(RateLimited) as excinfo:
        await fetch_youtube_shorts("mkbhd", max_count=12)

    assert attempts == ["http://p1", "http://p2"]
    assert "youtube shorts fetch failed after 2 proxy attempt(s)" in str(excinfo.value)


@pytest.mark.asyncio
async def test_fetch_youtube_shorts_falls_back_to_bright_data_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    monkeypatch.setattr(
        "app.scrapers.youtube.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1"],
    )

    async def fail_fetch(
        username: str,
        max_count: int | None,
        proxy: str | None,
        detail_proxies: list[str | None] | None = None,
    ):
        raise RuntimeError("Sign in to confirm you’re not a bot")

    async def fake_bright_data(username: str, max_count: int | None):
        assert username == "mkbhd"
        assert max_count == 2
        return [
            _entry_to_video_ytdlp(
                {
                    "id": "bd123",
                    "webpage_url": "https://www.youtube.com/shorts/bd123",
                    "view_count": 10,
                }
            )
        ]

    monkeypatch.setattr("app.scrapers.youtube._fetch_videos_ytdlp", fail_fetch)
    monkeypatch.setattr(
        "app.scrapers.youtube.fetch_youtube_shorts_via_bright_data",
        fake_bright_data,
        raising=False,
    )

    videos = await fetch_youtube_shorts("mkbhd", max_count=2)

    assert [v.video_id for v in videos if v is not None] == ["bd123"]


@pytest.mark.asyncio
async def test_fetch_youtube_shorts_without_bright_data_keeps_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", None)
    monkeypatch.setattr(
        "app.scrapers.youtube.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1"],
    )

    async def fail_fetch(
        username: str,
        max_count: int | None,
        proxy: str | None,
        detail_proxies: list[str | None] | None = None,
    ):
        raise RuntimeError("Sign in to confirm you’re not a bot")

    monkeypatch.setattr("app.scrapers.youtube._fetch_videos_ytdlp", fail_fetch)

    with pytest.raises(RateLimited):
        await fetch_youtube_shorts("mkbhd", max_count=2)


@pytest.mark.asyncio
async def test_fetch_youtube_shorts_no_shorts_tab_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "app.scrapers.youtube.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1"],
    )

    async def no_shorts_tab(
        username: str,
        max_count: int | None,
        proxy: str | None,
        detail_proxies: list[str | None] | None = None,
    ):
        raise RuntimeError("ERROR: [youtube:tab] abc: This channel does not have a shorts tab")

    monkeypatch.setattr("app.scrapers.youtube._fetch_videos_ytdlp", no_shorts_tab)

    videos = await fetch_youtube_shorts("channel/abc", max_count=3)

    assert videos == []


@pytest.mark.asyncio
async def test_fetch_videos_enriches_like_and_comment_counts(monkeypatch):
    def fake_list(username: str, max_count: int | None, proxy: str | None):
        return [
            {
                "id": "abc123",
                "title": "List title",
                "view_count": 2500,
                "url": "https://www.youtube.com/shorts/abc123",
            }
        ]

    def fake_detail(video_id: str, entry: dict, proxy: str | None):
        assert video_id == "abc123"
        assert proxy == "http://p1"
        return {
            "id": "abc123",
            "webpage_url": "https://www.youtube.com/shorts/abc123",
            "title": "Detail title",
            "description": "Detail description",
            "view_count": 2600,
            "like_count": 321,
            "comment_count": 17,
            "duration": 28,
            "timestamp": 1767225600,
        }

    monkeypatch.setattr("app.scrapers.youtube._ytdlp_extract_sync", fake_list)
    monkeypatch.setattr(
        "app.scrapers.youtube._ytdlp_extract_video_sync",
        fake_detail,
        raising=False,
    )

    videos = await _fetch_videos_ytdlp("mkbhd", 1, "http://p1")

    assert len(videos) == 1
    assert videos[0].play_count == 2600
    assert videos[0].like_count == 321
    assert videos[0].comment_count == 17
    assert videos[0].duration_seconds == 28
    assert videos[0].description == "Detail description"


@pytest.mark.asyncio
async def test_fetch_videos_retries_detail_with_next_proxy(monkeypatch):
    attempts: list[str | None] = []

    def fake_list(username: str, max_count: int | None, proxy: str | None):
        return [{"id": "abc123", "title": "List title", "view_count": 2500}]

    def fake_detail(video_id: str, entry: dict, proxy: str | None):
        attempts.append(proxy)
        if proxy == "http://p1":
            raise RuntimeError("Sign in to confirm you’re not a bot")
        return {
            "id": "abc123",
            "view_count": 2600,
            "like_count": 321,
            "comment_count": 17,
        }

    monkeypatch.setattr("app.scrapers.youtube._ytdlp_extract_sync", fake_list)
    monkeypatch.setattr("app.scrapers.youtube._ytdlp_extract_video_sync", fake_detail)

    videos = await _fetch_videos_ytdlp(
        "mkbhd",
        1,
        "http://p1",
        detail_proxies=["http://p1", "http://p2"],
    )

    assert attempts == ["http://p1", "http://p2"]
    assert videos[0].play_count == 2600
    assert videos[0].like_count == 321
    assert videos[0].comment_count == 17


@pytest.mark.asyncio
async def test_fetch_videos_enriches_details_with_four_way_concurrency(monkeypatch):
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_list(username: str, max_count: int | None, proxy: str | None):
        return [
            {"id": f"short-{i}", "title": f"Short {i}", "view_count": i}
            for i in range(8)
        ]

    def fake_detail(video_id: str, entry: dict, proxy: str | None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return {
            "id": video_id,
            "view_count": 100,
            "like_count": 10,
            "comment_count": 1,
        }

    monkeypatch.setattr("app.scrapers.youtube._ytdlp_extract_sync", fake_list)
    monkeypatch.setattr("app.scrapers.youtube._ytdlp_extract_video_sync", fake_detail)

    videos = await _fetch_videos_ytdlp(
        "mkbhd",
        8,
        "http://p1",
        detail_proxies=["http://p1"],
    )

    assert len(videos) == 8
    assert max_active == 4
