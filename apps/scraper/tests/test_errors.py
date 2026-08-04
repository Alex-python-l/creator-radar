import pytest

from app.scrapers.errors import (
    AccountGone,
    AuthChallenge,
    BrowserUnavailable,
    CredsMissing,
    NetworkError,
    RateLimited,
    ScrapeError,
    SessionExpired,
)


def test_base_default_code():
    assert ScrapeError().error_code == "unknown"


@pytest.mark.parametrize(
    "cls,code",
    [
        (AccountGone, "account_gone"),
        (RateLimited, "rate_limited"),
        (AuthChallenge, "auth_challenge"),
        (SessionExpired, "login_required"),
        (CredsMissing, "creds_missing"),
        (BrowserUnavailable, "browser_unavailable"),
        (NetworkError, "network_error"),
    ],
)
def test_subclass_codes(cls, code):
    exc = cls("boom")
    assert exc.error_code == code
    assert isinstance(exc, ScrapeError)
    assert str(exc) == "boom"
