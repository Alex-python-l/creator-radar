from __future__ import annotations

import json
import tempfile
from pathlib import Path

from video_blacklist import InvalidVideoUrl, VideoBlacklist, normalize_video_url


def test_video_blacklist_lifecycle() -> None:
    first = normalize_video_url("https://www.tiktok.com/@first/video/123456789?lang=en#top")
    second = normalize_video_url("https://m.tiktok.com/@renamed/video/123456789/")
    assert first.key == second.key == "tiktok:123456789"

    photo = normalize_video_url("https://www.tiktok.com/@name/photo/99887766?is_from_webapp=1")
    assert photo.key == "tiktok:99887766"

    try:
        normalize_video_url("https://example.com/video/123")
    except InvalidVideoUrl:
        pass
    else:
        raise AssertionError("非 TikTok 链接必须被拒绝")

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "ignored.jsonl"
        blacklist = VideoBlacklist(path)
        added, record = blacklist.add(
            "https://www.tiktok.com/@first/video/123456789?lang=en",
            "manual test",
        )
        assert added is True
        assert record["videoKey"] == "tiktok:123456789"

        duplicate, duplicate_record = blacklist.add("https://tiktok.com/@other/video/123456789")
        assert duplicate is False
        assert duplicate_record["videoKey"] == record["videoKey"]
        assert blacklist.find("https://www.tiktok.com/@changed/video/123456789?x=1") is not None
        assert len(blacklist.list()) == 1

        removed, _ = blacklist.remove("https://www.tiktok.com/@changed/video/123456789")
        assert removed is True
        assert blacklist.find("https://www.tiktok.com/@first/video/123456789") is None
        assert blacklist.list() == []

        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert [event["action"] for event in events] == ["add", "remove"]
