from datetime import datetime

from app.models import DiscoveredVideo


def test_discovered_video_field_set():
    fields = set(DiscoveredVideo.model_fields.keys())
    required = {
        "id", "url", "video_id",
        "author_unique_id", "author_nickname", "author_followers",
        "title",
        "play_count", "like_count", "comment_count",
        "share_count", "collect_count",
        "thumbnail_url",
        "create_time",
        "search_keyword", "country",
        "discovered_at", "last_seen_at",
    }
    missing = required - fields
    assert not missing, f"missing: {missing}"


def test_discovered_video_defaults_for_timestamps():
    v = DiscoveredVideo(url="https://x", video_id="1", search_keyword="ielts")
    assert isinstance(v.discovered_at, datetime)
    assert isinstance(v.last_seen_at, datetime)
