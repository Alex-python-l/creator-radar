"""generate_caption._get_client wiring tests.

We can't and shouldn't actually talk to Gemini in unit tests — but we can
spy on genai.Client to verify the proxy plumbing is right, since this is
the bit that decides whether production calls even leave the box.
"""
import pytest


@pytest.fixture
def spy_client(monkeypatch):
    """Replace google.genai.Client with a spy that records its kwargs."""
    captured: dict = {}

    class _SpyClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # genai is imported inside _get_client, so patch on the actual module
    from google import genai
    monkeypatch.setattr(genai, "Client", _SpyClient)

    import generate_caption
    monkeypatch.setattr(generate_caption, "GEMINI_API_KEY", "k-test")
    return captured


def test_get_client_without_proxy_omits_client_args(spy_client, monkeypatch):
    monkeypatch.delenv("DRIVE_IMPORT_PROXY", raising=False)
    import generate_caption
    generate_caption._get_client()
    opts = spy_client["http_options"]
    assert "client_args" not in opts
    assert "async_client_args" not in opts


def test_get_client_with_proxy_threads_it_into_httpx(spy_client, monkeypatch):
    monkeypatch.setenv("DRIVE_IMPORT_PROXY", "http://u:p@127.0.0.1:8118")
    monkeypatch.delenv("GEMINI_PROXY", raising=False)
    import generate_caption
    generate_caption._get_client()
    opts = spy_client["http_options"]
    # Both sync and async clients must get the proxy — google-genai builds
    # both at construction time and we don't know which call path the SDK
    # will pick for any given request.
    assert opts["client_args"] == {"proxy": "http://u:p@127.0.0.1:8118"}
    assert opts["async_client_args"] == {"proxy": "http://u:p@127.0.0.1:8118"}


def test_get_client_prefers_gemini_proxy(spy_client, monkeypatch):
    monkeypatch.setenv("DRIVE_IMPORT_PROXY", "http://drive:p@127.0.0.1:8118")
    monkeypatch.setenv("GEMINI_PROXY", "http://gem:p@proxy.example:10008")
    import generate_caption
    generate_caption._get_client()
    opts = spy_client["http_options"]
    assert opts["client_args"] == {"proxy": "http://gem:p@proxy.example:10008"}
    assert opts["async_client_args"] == {"proxy": "http://gem:p@proxy.example:10008"}


def test_get_client_blank_gemini_proxy_falls_back_to_drive_proxy(
        spy_client, monkeypatch):
    monkeypatch.setenv("DRIVE_IMPORT_PROXY", "http://drive:p@127.0.0.1:8118")
    monkeypatch.setenv("GEMINI_PROXY", "   ")
    import generate_caption
    generate_caption._get_client()
    opts = spy_client["http_options"]
    assert opts["client_args"] == {"proxy": "http://drive:p@127.0.0.1:8118"}
    assert opts["async_client_args"] == {"proxy": "http://drive:p@127.0.0.1:8118"}


def test_get_client_treats_blank_proxy_as_unset(spy_client, monkeypatch):
    monkeypatch.setenv("DRIVE_IMPORT_PROXY", "   ")
    monkeypatch.delenv("GEMINI_PROXY", raising=False)
    import generate_caption
    generate_caption._get_client()
    assert "client_args" not in spy_client["http_options"]


def test_get_client_raises_when_key_missing(monkeypatch):
    import generate_caption
    monkeypatch.setattr(generate_caption, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        generate_caption._get_client()
