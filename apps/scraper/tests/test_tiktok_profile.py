import pytest

from app.scrapers.errors import AccountGone, RateLimited


def _profile_html(detail: dict) -> str:
    import json

    data = {"__DEFAULT_SCOPE__": {"webapp.user-detail": detail}}
    return (
        '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" '
        f'type="application/json">{json.dumps(data)}</script></html>'
    )


def test_parse_profile_html_detects_existing_account():
    from app.scrapers.tiktok_profile import parse_tiktok_profile_html

    result = parse_tiktok_profile_html(
        _profile_html(
            {
                "statusCode": 0,
                "statusMsg": "",
                "userInfo": {"user": {"uniqueId": "tiktok"}},
            }
        )
    )

    assert result.status == "exists"
    assert result.status_code == 0
    assert result.unique_id == "tiktok"


def test_parse_profile_html_detects_missing_account():
    from app.scrapers.tiktok_profile import parse_tiktok_profile_html

    result = parse_tiktok_profile_html(
        _profile_html({"statusCode": 10221, "statusMsg": "", "needFix": True})
    )

    assert result.status == "gone"
    assert result.status_code == 10221


def test_parse_profile_html_detects_10202_missing_account():
    from app.scrapers.tiktok_profile import parse_tiktok_profile_html

    result = parse_tiktok_profile_html(
        _profile_html({"statusCode": 10202, "statusMsg": "", "needFix": True})
    )

    assert result.status == "gone"
    assert result.status_code == 10202


def test_parse_profile_html_keeps_missing_json_unknown():
    from app.scrapers.tiktok_profile import parse_tiktok_profile_html

    result = parse_tiktok_profile_html("<html></html>")

    assert result.status == "unknown"
    assert result.reason == "rehydration_json_missing"


def test_bright_data_records_confirm_explicit_missing_account():
    from app.scrapers.bright_data_social import tiktok_records_confirm_account_gone

    assert tiktok_records_confirm_account_gone(
        [{"error": "Profile not found", "status": "failed"}]
    )


def test_bright_data_records_confirm_dead_page_account():
    from app.scrapers.bright_data_social import tiktok_records_confirm_account_gone

    assert tiktok_records_confirm_account_gone(
        [{"error_code": "dead_page", "error": "User is banned"}]
    )


def test_bright_data_records_confirm_user_is_banned_account():
    from app.scrapers.bright_data_social import tiktok_records_confirm_account_gone

    assert tiktok_records_confirm_account_gone([{"error": "User is banned"}])


def test_bright_data_records_do_not_treat_empty_posts_as_gone():
    from app.scrapers.bright_data_social import tiktok_records_confirm_account_gone

    assert not tiktok_records_confirm_account_gone([])
    assert not tiktok_records_confirm_account_gone([{"post_id": "7553300000000000000"}])


@pytest.mark.asyncio
async def test_fetch_tiktok_requires_native_and_bright_data_to_mark_account_gone(monkeypatch):
    from app.config import settings
    from app.scrapers.tiktok import fetch_tiktok_videos
    from app.scrapers.tiktok_profile import TikTokProfileProbeResult

    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    monkeypatch.setattr(
        "app.scrapers.tiktok.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1"],
    )

    async def fail_fetch(username: str, max_count: int | None, proxy: str | None):
        raise RuntimeError("Unable to extract secondary user ID")

    async def native_gone(username: str):
        return TikTokProfileProbeResult(status="gone", status_code=10221, reason="user_detail_gone")

    async def bd_records(username: str, max_count: int | None):
        return [{"error": "User not found", "status": "failed"}]

    monkeypatch.setattr("app.scrapers.tiktok._fetch_videos_ytdlp", fail_fetch)
    monkeypatch.setattr("app.scrapers.tiktok.probe_tiktok_profile", native_gone)
    monkeypatch.setattr("app.scrapers.tiktok.fetch_tiktok_records_via_bright_data", bd_records)

    with pytest.raises(AccountGone):
        await fetch_tiktok_videos("missingcreator", max_count=3)


@pytest.mark.asyncio
async def test_fetch_tiktok_does_not_mark_gone_when_bright_data_is_uncertain(monkeypatch):
    from app.config import settings
    from app.scrapers.tiktok import fetch_tiktok_videos
    from app.scrapers.tiktok_profile import TikTokProfileProbeResult

    monkeypatch.setattr(settings, "bright_data_api_key", "bd_test_token")
    monkeypatch.setattr(
        "app.scrapers.tiktok.pooled_proxy_candidates",
        lambda pool, proxy_url, limit: ["http://p1"],
    )

    async def fail_fetch(username: str, max_count: int | None, proxy: str | None):
        raise RuntimeError("Unable to extract secondary user ID")

    async def native_gone(username: str):
        return TikTokProfileProbeResult(status="gone", status_code=10221, reason="user_detail_gone")

    async def bd_records(username: str, max_count: int | None):
        return []

    monkeypatch.setattr("app.scrapers.tiktok._fetch_videos_ytdlp", fail_fetch)
    monkeypatch.setattr("app.scrapers.tiktok.probe_tiktok_profile", native_gone)
    monkeypatch.setattr("app.scrapers.tiktok.fetch_tiktok_records_via_bright_data", bd_records)

    with pytest.raises(RateLimited):
        await fetch_tiktok_videos("missingcreator", max_count=3)
