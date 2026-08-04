"""facebook_reel.fetch_reel 测试 — mock yt-dlp,喂真实 fixture JSON。"""
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.scrapers.errors import RateLimited
from app.scrapers.facebook_reel import fetch_reel, parse_reactions_from_title

FIXTURE_DIR = Path(__file__).parent / "fixtures"
REAL_REEL = json.loads((FIXTURE_DIR / "facebook_reel.json").read_text())


# ---------- parse_reactions_from_title 单元测试 ----------

def test_reactions_k_suffix():
    assert parse_reactions_from_title("4.8K views · 51K reactions | Foo") == 51000


def test_reactions_m_suffix():
    assert parse_reactions_from_title("100K views · 1.2M reactions | Bar") == 1200000


def test_reactions_plain_int():
    assert parse_reactions_from_title("123 views · 7 reactions | Foo") == 7


def test_reactions_missing_returns_zero():
    """低互动 reel 的 title 可能没 'X reactions' 段。"""
    assert parse_reactions_from_title("4.8K views | Foo") == 0
    assert parse_reactions_from_title("Some random title") == 0


def test_reactions_none_input():
    assert parse_reactions_from_title(None) == 0
    assert parse_reactions_from_title("") == 0


# ---------- fetch_reel 用 mocked YoutubeDL ----------

class _FakeYoutubeDL:
    """模拟 yt_dlp.YoutubeDL 上下文管理器。"""
    def __init__(self, opts):
        self.opts = opts
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def extract_info(self, url, download=False):
        return REAL_REEL  # 永远返回真实 fixture


@patch("app.scrapers.facebook_reel.YoutubeDL", _FakeYoutubeDL)
def test_fetch_reel_basic_fields():
    video = fetch_reel("1678287819999038", proxy=None)
    assert video.video_id == "1678287819999038"
    assert video.video_url == REAL_REEL["webpage_url"]
    assert video.thumbnail_url == REAL_REEL["thumbnail"]
    assert video.duration_seconds == 21  # 21.522 → 21
    assert video.play_count == REAL_REEL["view_count"]
    assert video.like_count == 51000  # "51K reactions" → 51000
    assert video.comment_count == 0   # FB 不暴露
    assert video.share_count == 0     # FB 不暴露


@patch("app.scrapers.facebook_reel.YoutubeDL", _FakeYoutubeDL)
def test_fetch_reel_published_at_is_utc_datetime():
    video = fetch_reel("1678287819999038", proxy=None)
    assert isinstance(video.published_at, datetime)
    assert video.published_at.tzinfo == UTC
    # timestamp 1771884475 → 2026-02-23
    assert video.published_at.year == 2026


@patch("app.scrapers.facebook_reel.YoutubeDL", _FakeYoutubeDL)
def test_fetch_reel_raw_includes_full_json():
    video = fetch_reel("1678287819999038", proxy=None)
    assert video.raw["id"] == "1678287819999038"
    assert video.raw["uploader"] == "Social Distortion"


class _ExtractorErrorYoutubeDL:
    """模拟 yt-dlp 抛 ExtractorError(常见 'Cannot parse data')。"""
    def __init__(self, opts): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=False):
        from yt_dlp.utils import ExtractorError
        raise ExtractorError("Cannot parse data")


@patch("app.scrapers.facebook_reel.YoutubeDL", _ExtractorErrorYoutubeDL)
def test_fetch_reel_raises_rate_limited_on_extractor_error():
    """yt-dlp 偶发 'Cannot parse data' — 当 transient(换 IP 可能能过)。"""
    with pytest.raises(RateLimited):
        fetch_reel("1678287819999038", proxy=None)


def test_fetch_reel_passes_proxy_to_ytdlp():
    """proxy 参数应该塞进 YoutubeDL opts 的 'proxy' 字段。"""
    captured: dict = {}
    class _CapturingYDL(_FakeYoutubeDL):
        def __init__(self, opts):
            captured.update(opts)
            super().__init__(opts)

    with patch("app.scrapers.facebook_reel.YoutubeDL", _CapturingYDL):
        fetch_reel("1678287819999038", proxy="http://u:p@host:1234")
    assert captured.get("proxy") == "http://u:p@host:1234"
