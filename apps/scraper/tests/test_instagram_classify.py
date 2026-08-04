import pytest
from instagrapi.exceptions import (
    ChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from app.config import settings
from app.scrapers.errors import (
    AuthChallenge,
    RateLimited,
    ScrapeError,
    SessionExpired,
)
from app.scrapers.instagram import classify_instagrapi_error, fetch_instagram_videos
from app.scrapers.types import ScrapedProfile


def test_challenge_required_maps_auth_challenge():
    assert isinstance(
        classify_instagrapi_error(ChallengeRequired("verify")), AuthChallenge
    )


def test_please_wait_maps_rate_limited():
    assert isinstance(
        classify_instagrapi_error(PleaseWaitFewMinutes("slow down")), RateLimited
    )


def test_login_required_maps_session_expired():
    assert isinstance(
        classify_instagrapi_error(LoginRequired("re-login")), SessionExpired
    )


def test_unknown_instagrapi_error_maps_base_scrape_error():
    out = classify_instagrapi_error(Exception("weird ig error"))
    assert isinstance(out, ScrapeError)
    assert out.error_code == "unknown"


@pytest.mark.asyncio
async def test_fetch_instagram_falls_back_to_bright_data_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")

    async def fail_get_client():
        raise RateLimited("please wait")

    async def fake_bright_data(username: str, max_count: int | None):
        assert username == "creator"
        assert max_count == 4
        return (
            ScrapedProfile(
                follower_count=10,
                following_count=1,
                video_count=2,
                display_name="creator",
                avatar_url=None,
                bio=None,
            ),
            [],
        )

    monkeypatch.setattr("app.scrapers.instagram._get_client", fail_get_client)
    monkeypatch.setattr(
        "app.scrapers.instagram.fetch_instagram_videos_via_bright_data",
        fake_bright_data,
        raising=False,
    )

    profile, videos = await fetch_instagram_videos("creator", max_count=4)

    assert profile.display_name == "creator"
    assert videos == []


@pytest.mark.asyncio
async def test_fetch_instagram_without_bright_data_keeps_native_error(monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", None)

    async def fail_get_client():
        raise RateLimited("please wait")

    monkeypatch.setattr("app.scrapers.instagram._get_client", fail_get_client)

    with pytest.raises(RateLimited):
        await fetch_instagram_videos("creator", max_count=4)
