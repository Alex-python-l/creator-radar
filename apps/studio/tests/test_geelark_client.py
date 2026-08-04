"""Boundary-layer tests for geelark_client.

GeeLark's `/open/v1/upload/getUrl` rejects file extensions outside a small
allowlist with [40004] wrong argument (e.g. `.mov` from iPhone exports). The
upload helper must normalize common aliases before hitting the API.
"""
import types

import pytest

import geelark_client


class _StubClient(geelark_client.GeeLarkClient):
    """Captures the `file_type` actually sent to /upload/getUrl."""

    def __init__(self):
        super().__init__("t")
        self.calls: list[str] = []

    def get_upload_url(self, file_type: str) -> dict:  # type: ignore[override]
        self.calls.append(file_type)
        return {"uploadUrl": "http://x", "resourceUrl": "http://r"}


@pytest.fixture
def stub_put(monkeypatch):
    """Stub out the binary PUT so tests stay offline."""
    def _put(url, data=None, timeout=None):
        return types.SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(geelark_client.requests, "put", _put)


@pytest.fixture
def tmp_file(tmp_path):
    p = tmp_path / "blob"
    p.write_bytes(b"x")
    return str(p)


@pytest.mark.parametrize("input_ext,expected", [
    ("mov", "mp4"),
    ("MOV", "mp4"),
    ("m4v", "mp4"),
    ("qt",  "mp4"),
    ("jpeg", "jpg"),
    ("JPEG", "jpg"),
    ("mp4", "mp4"),
    ("MP4", "mp4"),
    ("jpg", "jpg"),
    ("png", "png"),
])
def test_upload_file_normalizes_filetype(tmp_file, stub_put, input_ext, expected):
    c = _StubClient()
    c.upload_file(tmp_file, input_ext)
    assert c.calls == [expected], (
        f"upload_file({input_ext!r}) should call get_upload_url({expected!r}), "
        f"got {c.calls}"
    )


def test_create_video_post_task_payload_and_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskIds": ["tt-1"]}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_video_post_task("env-1", "https://res/v.mp4",
                                   video_desc="hello")
    assert tid == "tt-1"
    assert captured["path"] == "/open/v1/task/add"
    p = captured["payload"]
    assert p["taskType"] == 1
    assert len(p["list"]) == 1
    item = p["list"][0]
    assert item["envId"] == "env-1"
    assert item["video"] == "https://res/v.mp4"
    assert item["videoDesc"] == "hello"
    assert item["needShareLink"] is True
    assert isinstance(item["scheduleAt"], int)


def test_create_youtube_post_task_payload_and_endpoint(monkeypatch):
    captured = {}
    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskId": "yt-1"}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_youtube_post_task("env-1", "https://res/v.mp4",
                                      title="My Short")
    assert tid == "yt-1"
    assert captured["path"] == "/open/v1/rpa/task/youtubePubShort"
    p = captured["payload"]
    assert p["id"] == "env-1"
    assert p["video"] == "https://res/v.mp4"
    assert p["title"] == "My Short"
    assert p["sameStyleVoice"] == 0
    assert p["originalVoice"] == 100
    assert p["isDisclosureMandatory"] is False
    assert isinstance(p["scheduleAt"], int)


def test_update_phone_detail_name_payload_and_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")
    result = c.update_phone_detail("env-1", name="New Name")

    assert result == {}
    assert captured["path"] == "/open/v1/phone/detail/update"
    assert captured["payload"] == {"id": "env-1", "name": "New Name"}


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def test_create_youtube_post_task_truncates_title_to_safe_utf16_budget(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "yt-2"})
    c = geelark_client.GeeLarkClient("token")
    c.create_youtube_post_task("env-2", "https://res/v.mp4",
                                title="x" * 250)
    assert captured["payload"]["title"] == "x" * 90
    assert _utf16_units(captured["payload"]["title"]) <= 90


def test_create_youtube_post_task_truncates_before_emoji_over_budget(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "yt-emoji"})
    c = geelark_client.GeeLarkClient("token")
    c.create_youtube_post_task("env-emoji", "https://res/v.mp4",
                                title=("x" * 89) + "😅 extra")
    assert captured["payload"]["title"] == "x" * 89
    assert _utf16_units(captured["payload"]["title"]) <= 90


def test_create_youtube_post_task_uses_fallback_for_blank_title(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "yt-blank"})
    c = geelark_client.GeeLarkClient("token")
    c.create_youtube_post_task("env-blank", "https://res/v.mp4",
                                title=" \n\t ")
    assert captured["payload"]["title"] == "Short"


def test_create_youtube_post_task_raises_when_taskid_missing(monkeypatch):
    import pytest
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="taskId"):
        c.create_youtube_post_task("env-3", "https://res/v.mp4", title="hi")


def test_create_instagram_post_task_payload_and_endpoint(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(path=path, payload=payload)
                         or {"taskId": "ig-1"})
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_instagram_post_task("env-4", ["https://res/a.mp4"],
                                        description="my IG reel")
    assert tid == "ig-1"
    assert captured["path"] == "/open/v1/rpa/task/instagramPubReels"
    p = captured["payload"]
    assert p["id"] == "env-4"
    assert p["video"] == ["https://res/a.mp4"]
    assert p["description"] == "my IG reel"
    assert p["aiTag"] is False
    assert p["needShareLink"] is False
    assert isinstance(p["scheduleAt"], int)


def test_create_instagram_post_task_truncates_description(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "ig-2"})
    c = geelark_client.GeeLarkClient("token")
    c.create_instagram_post_task("env-5", ["https://res/a.mp4"],
                                  description="x" * 3000)
    assert captured["payload"]["description"] == "x" * 2200


def test_create_instagram_post_task_raises_when_taskid_missing(monkeypatch):
    import pytest
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="taskId"):
        c.create_instagram_post_task("env-6", ["https://res/a.mp4"],
                                      description="hi")


def test_create_facebook_post_task_payload_and_endpoint(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(path=path, payload=payload)
                         or {"taskId": "fb-1"})
    c = geelark_client.GeeLarkClient("token")
    tid = c.create_facebook_post_task("env-7", "https://res/a.mp4",
                                       description="my FB reel")
    assert tid == "fb-1"
    assert captured["path"] == "/open/v1/rpa/task/faceBookPubReels"
    p = captured["payload"]
    assert p["id"] == "env-7"
    assert p["video"] == "https://res/a.mp4"
    assert p["description"] == "my FB reel"
    assert p["needShareLink"] is False
    assert "page" not in p


def test_create_facebook_post_task_truncates_description(monkeypatch):
    captured = {}
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: captured.update(payload=payload)
                         or {"taskId": "fb-2"})
    c = geelark_client.GeeLarkClient("token")
    c.create_facebook_post_task("env-8", "https://res/a.mp4",
                                 description="y" * 800)
    assert captured["payload"]["description"] == "y" * 500


def test_create_facebook_post_task_raises_when_taskid_missing(monkeypatch):
    import pytest
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                         lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="taskId"):
        c.create_facebook_post_task("env-9", "https://res/a.mp4",
                                     description="hi")


def test_create_tiktok_ai_comment_task_payload_and_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskId": "comment-1"}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")

    tid = c.create_tiktok_ai_comment_task(
        "env-1",
        ["https://www.tiktok.com/@owner/video/1234567890"],
        schedule_at=123,
        comment_probability=100,
        name="AI comment",
        remark="owned video engagement",
    )

    assert tid == "comment-1"
    assert captured["path"] == "/open/v1/rpa/task/tiktokRandomComment"
    assert captured["payload"] == {
        "scheduleAt": 123,
        "id": "env-1",
        "useAi": 1,
        "links": ["https://www.tiktok.com/@owner/video/1234567890"],
        "commentProbability": 100,
        "name": "AI comment",
        "remark": "owned video engagement",
    }


def test_create_tiktok_ai_comment_task_uses_manual_comment(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskId": "comment-manual-1"}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")

    tid = c.create_tiktok_ai_comment_task(
        "env-1",
        ["https://www.tiktok.com/@owner/video/1234567890"],
        schedule_at=123,
        comment="This tip is actually useful for practice.",
    )

    assert tid == "comment-manual-1"
    assert captured["path"] == "/open/v1/rpa/task/tiktokRandomComment"
    assert captured["payload"]["useAi"] == 2
    assert captured["payload"]["comment"] == "This tip is actually useful for practice."


def test_create_tiktok_ai_comment_task_defaults_schedule(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient,
        "_post",
        lambda self, path, payload: captured.update(payload=payload) or {"taskId": "comment-2"},
    )
    c = geelark_client.GeeLarkClient("token")
    c.create_tiktok_ai_comment_task(
        "env-2",
        ["https://www.tiktok.com/@owner/video/1234567890"],
    )
    assert isinstance(captured["payload"]["scheduleAt"], int)
    assert captured["payload"]["commentProbability"] == 100


def test_create_tiktok_ai_comment_task_raises_when_taskid_missing(monkeypatch):
    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post",
                        lambda self, path, payload: {})
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="tiktok AI comment task"):
        c.create_tiktok_ai_comment_task(
            "env-3",
            ["https://www.tiktok.com/@owner/video/1234567890"],
        )


def test_get_wallet_endpoint_and_response(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"balance": 12.34, "giftMoney": 0, "availableTimeAddOn": 5}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")
    data = c.get_wallet()
    assert captured["path"] == "/open/v1/pay/wallet"
    assert captured["payload"] == {}
    assert data == {"balance": 12.34, "giftMoney": 0, "availableTimeAddOn": 5}


def test_create_facebook_auto_comment_task_payload_and_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"taskId": "fb-comment-1"}

    monkeypatch.setattr(geelark_client.GeeLarkClient, "_post", fake_post)
    c = geelark_client.GeeLarkClient("token")

    tid = c.create_facebook_auto_comment_task(
        "env-1",
        post_address="https://www.facebook.com/zuck/posts/abc",
        comments=["Nice!", "Great!"],
        keywords=["english", "tip"],
        schedule_at=999,
        name="FB Comment Test",
        remark="engagement_run_id=42",
    )

    assert tid == "fb-comment-1"
    assert captured["path"] == "/open/v1/rpa/task/faceBookAutoComment"
    assert captured["payload"] == {
        "scheduleAt": 999,
        "id": "env-1",
        "postAddress": "https://www.facebook.com/zuck/posts/abc",
        "comment": ["Nice!", "Great!"],
        "keyword": ["english", "tip"],
        "name": "FB Comment Test",
        "remark": "engagement_run_id=42",
    }


def test_create_facebook_auto_comment_task_defaults_schedule(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient,
        "_post",
        lambda self, path, payload: captured.update(payload=payload) or {"taskId": "fb-x"},
    )
    c = geelark_client.GeeLarkClient("token")
    c.create_facebook_auto_comment_task(
        "env-2",
        post_address="https://www.facebook.com/a/posts/b",
        comments=["c1"],
        keywords=["k1"],
    )
    assert isinstance(captured["payload"]["scheduleAt"], int)
    # name / remark omitted → not in payload
    assert "name" not in captured["payload"]
    assert "remark" not in captured["payload"]


def test_create_facebook_auto_comment_task_truncates_long_strings(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient, "_post",
        lambda self, path, payload: captured.update(payload=payload) or {"taskId": "fb-trunc"},
    )
    c = geelark_client.GeeLarkClient("token")
    long_comment = "x" * 9000
    long_keyword = "k" * 150
    c.create_facebook_auto_comment_task(
        "env-3",
        post_address="https://www.facebook.com/a/posts/b",
        comments=[long_comment],
        keywords=[long_keyword],
        name="n" * 200,
        remark="r" * 300,
    )
    p = captured["payload"]
    assert len(p["comment"][0]) == 8000
    assert len(p["keyword"][0]) == 100
    assert len(p["name"]) == 128
    assert len(p["remark"]) == 200


def test_create_facebook_auto_comment_task_raises_when_taskid_missing(monkeypatch):
    monkeypatch.setattr(
        geelark_client.GeeLarkClient, "_post",
        lambda self, path, payload: {},
    )
    c = geelark_client.GeeLarkClient("token")
    with pytest.raises(RuntimeError, match="facebook comment task"):
        c.create_facebook_auto_comment_task(
            "env-x",
            post_address="https://www.facebook.com/a/posts/b",
            comments=["c"], keywords=["k"],
        )
