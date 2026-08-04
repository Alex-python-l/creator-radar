"""HTTP response compression (GZipMiddleware).

The SPA bundle (~400KB) and the /api/videos JSON (~160KB in prod) were
served uncompressed, which dominated /videos page-load time over the
wire. These tests lock in that large responses are gzipped, while
Server-Sent Event streams are left uncompressed so the real-time log
flush that publish-stream / phones-create rely on is not buffered away.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as cfg
    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(cfg, "UPLOAD_DIR", up)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_large_json_response_is_gzipped(client):
    """A large /api/videos payload must come back gzip-encoded. httpx
    transparently decompresses the body but keeps the Content-Encoding
    header, so we can assert on the header directly."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    # Three videos × four platforms = a payload comfortably above the
    # 500-byte gzip threshold, regardless of how short individual fields
    # serialize in the test DB.
    client.post(
        "/api/videos/upload",
        data={"platforms": "tiktok,youtube,instagram,facebook"},
        files=[("files", (f"compress-{i}.mp4", b"x", "video/mp4"))
               for i in range(3)],
    )
    r = client.get("/api/videos", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and len(body["items"]) == 3
    assert r.headers.get("content-encoding") == "gzip"


def test_sse_stream_is_not_gzipped(client):
    """text/event-stream must NOT be gzipped — gzip buffering would break
    the real-time flush behavior phones-create depends on. Starlette
    excludes it by default; this pins that guarantee."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "sse-gzip"}).json()["id"]
    with client.stream(
        "POST", f"/api/groups/{gid}/phones/create",
        # One syntactically valid proxy gets us past validation and into
        # the event stream; the generator failing inside the stream (no
        # GeeLark credentials in tests) doesn't matter — the SSE response
        # headers are what this test pins.
        json={"proxies": ["socks5://u:p@127.0.0.1:1080"]},
        headers={"Accept-Encoding": "gzip"},
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        assert r.headers.get("content-encoding") != "gzip"
        "".join(chunk for chunk in r.iter_text())
