"""Tests for the failure-screenshot caching proxy.

GET /api/videos/publications/{pub_id}/screenshot resolves the
publication's stored GeeLark screenshot URL, fetches the bytes once
(server-side), caches them under config.SCREENSHOT_DIR, and serves the
cached copy on every subsequent request. This shields marketers in
mainland China from GeeLark's slow Singapore origin (singapore-upgrade.
geelark.com, 1-4s TTFB even for 20-40KB images) and survives GeeLark's
~3-day file TTL.
"""

import pytest

from app.models import Publication, Video


@pytest.fixture
def authed_client(client, monkeypatch):
    """A logged-in client, mirroring test_publish_endpoint.authed_client."""
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    return client


def _make_publication(session, url):
    """Insert a Video + FAILURE Publication carrying `url`; return pub id."""
    video = Video(original_filename="clip.mp4")
    session.add(video)
    session.commit()
    session.refresh(video)
    pub = Publication(video_id=video.id, platform="tiktok",
                      result="FAILURE", fail_screenshot=url)
    session.add(pub)
    session.commit()
    session.refresh(pub)
    return pub.id


def test_screenshot_fetches_caches_and_serves(
        authed_client, db, monkeypatch, tmp_path):
    """First request fetches+caches the bytes; second request is served
    from the on-disk cache without re-fetching the slow upstream."""
    import app.api.videos as videos_api
    import app.config as cfg
    monkeypatch.setattr(cfg, "SCREENSHOT_DIR", tmp_path)

    calls = {"n": 0}

    def fake_fetch(url):
        calls["n"] += 1
        return b"\xff\xd8\xff-jpeg-bytes"

    monkeypatch.setattr(videos_api, "_fetch_screenshot_bytes", fake_fetch)
    pid = _make_publication(
        db, "https://singapore-upgrade.geelark.com/geeUserUix/a/b.jpg")

    r1 = authed_client.get(f"/api/videos/publications/{pid}/screenshot")
    assert r1.status_code == 200, r1.text
    assert r1.content == b"\xff\xd8\xff-jpeg-bytes"
    assert r1.headers["content-type"] == "image/jpeg"
    assert calls["n"] == 1

    r2 = authed_client.get(f"/api/videos/publications/{pid}/screenshot")
    assert r2.status_code == 200
    assert r2.content == b"\xff\xd8\xff-jpeg-bytes"
    assert calls["n"] == 1, "second request must hit the cache, not refetch"


def test_screenshot_redirects_to_original_on_fetch_failure(
        authed_client, db, monkeypatch, tmp_path):
    """If the upstream fetch fails (timeout / expired URL), the endpoint
    degrades to a redirect to the raw GeeLark URL — never worse than the
    pre-proxy behavior, and the image can still load via the slow path."""
    import app.api.videos as videos_api
    import app.config as cfg
    monkeypatch.setattr(cfg, "SCREENSHOT_DIR", tmp_path)

    def boom(url):
        raise RuntimeError("upstream timeout")

    monkeypatch.setattr(videos_api, "_fetch_screenshot_bytes", boom)
    url = "https://singapore-upgrade.geelark.com/geeUserUix/c/d.jpg"
    pid = _make_publication(db, url)

    r = authed_client.get(
        f"/api/videos/publications/{pid}/screenshot",
        follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == url


def test_screenshot_404_when_publication_has_no_url(
        authed_client, db, monkeypatch, tmp_path):
    """A publication with no stored screenshot returns a 404 with our
    detail (not the generic unknown-route 404)."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "SCREENSHOT_DIR", tmp_path)
    pid = _make_publication(db, None)
    r = authed_client.get(f"/api/videos/publications/{pid}/screenshot")
    assert r.status_code == 404
    assert r.json()["detail"] == "no screenshot"


def test_screenshot_404_for_missing_publication(
        authed_client, db, monkeypatch, tmp_path):
    """An unknown publication id returns our 404 detail."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "SCREENSHOT_DIR", tmp_path)
    r = authed_client.get("/api/videos/publications/99999999/screenshot")
    assert r.status_code == 404
    assert r.json()["detail"] == "no screenshot"
