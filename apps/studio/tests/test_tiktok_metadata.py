def test_extract_tiktok_metadata_prefers_open_graph_tags():
    from app.services.tiktok_metadata import extract_tiktok_metadata

    html = """
    <html>
      <head>
        <title>Fallback title</title>
        <meta property="og:title" content="IELTS speaking trick from Jane" />
        <meta property="og:description" content="A short video about part 2 structure." />
      </head>
    </html>
    """

    meta = extract_tiktok_metadata(html, "https://www.tiktok.com/@jane/video/123")

    assert meta["title"] == "IELTS speaking trick from Jane"
    assert meta["description"] == "A short video about part 2 structure."
    assert meta["source_url"] == "https://www.tiktok.com/@jane/video/123"


def test_extract_tiktok_metadata_collapses_whitespace_and_falls_back_to_title():
    from app.services.tiktok_metadata import extract_tiktok_metadata

    html = """
    <html><head><title>
      TikTok - make studying less boring
    </title></head></html>
    """

    meta = extract_tiktok_metadata(html, "https://vt.tiktok.com/abc123/")

    assert meta["title"] == "TikTok - make studying less boring"
    assert meta["description"] is None


def test_fetch_tiktok_metadata_prefers_oembed(monkeypatch):
    from app.services import tiktok_metadata

    calls = []

    class Response:
        url = "https://www.tiktok.com/oembed"
        text = ""

        def __init__(self, json_body):
            self._json_body = json_body

        def raise_for_status(self):
            return None

        def json(self):
            return self._json_body

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        assert url == "https://www.tiktok.com/oembed"
        return Response({
            "title": "DOWNLOAD FOR FREE: Link in my bio! #ieltspreparation",
            "author_name": "Louise",
            "author_url": "https://www.tiktok.com/@pinaylouise_au",
        })

    monkeypatch.setattr(tiktok_metadata.requests, "get", fake_get)

    meta = tiktok_metadata.fetch_tiktok_metadata("https://vt.tiktok.com/ZS9YTWak9/")

    assert len(calls) == 1
    assert calls[0][1]["params"] == {"url": "https://vt.tiktok.com/ZS9YTWak9/"}
    assert meta["title"] == "DOWNLOAD FOR FREE: Link in my bio! #ieltspreparation"
    assert meta["description"] == "Louise"
    assert meta["source_url"] == "https://www.tiktok.com/@pinaylouise_au"


def test_fetch_tiktok_metadata_falls_back_to_html_when_oembed_fails(monkeypatch):
    from app.services import tiktok_metadata

    calls = []

    class OembedResponse:
        url = "https://www.tiktok.com/oembed"
        text = '{"code":400}'

        def raise_for_status(self):
            raise tiktok_metadata.requests.HTTPError("bad oembed")

    class HtmlResponse:
        url = "https://www.tiktok.com/@creator/video/123"
        text = '<meta property="og:title" content="Real fallback title">'

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == "https://www.tiktok.com/oembed":
            return OembedResponse()
        return HtmlResponse()

    monkeypatch.setattr(tiktok_metadata.requests, "get", fake_get)

    meta = tiktok_metadata.fetch_tiktok_metadata(
        "https://www.tiktok.com/@creator/video/123"
    )

    assert calls == [
        "https://www.tiktok.com/oembed",
        "https://www.tiktok.com/@creator/video/123",
    ]
    assert meta["title"] == "Real fallback title"


def test_fetch_tiktok_metadata_uses_proxy_from_env(monkeypatch):
    from app.services import tiktok_metadata

    monkeypatch.setenv("TIKTOK_METADATA_PROXY", "http://proxy.local:8080")
    seen = []

    class Response:
        url = "https://www.tiktok.com/oembed"

        def raise_for_status(self):
            return None

        def json(self):
            return {"title": "A useful IELTS tip"}

    def fake_get(url, **kwargs):
        seen.append(kwargs.get("proxies"))
        return Response()

    monkeypatch.setattr(tiktok_metadata.requests, "get", fake_get)

    meta = tiktok_metadata.fetch_tiktok_metadata("https://vt.tiktok.com/test/")

    assert meta["title"] == "A useful IELTS tip"
    assert seen == [{"http": "http://proxy.local:8080", "https": "http://proxy.local:8080"}]
