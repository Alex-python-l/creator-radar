import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from sqlmodel import select

from app.models import DiscoveredVideo, EngagementRun, EngagementTask
from app.services import discovery_service

FIXTURE = Path(__file__).parent / "fixtures" / "brightdata_keyword_ielts_sample.json"


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_settings_uses_defaults(monkeypatch):
    sess = MagicMock()
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {},
    )
    cfg = discovery_service._parse_settings(sess)
    # Default keyword set was widened to 45 IELTS-adjacent terms to drive
    # discovery toward ~1000+ candidate videos.
    assert len(cfg.keywords) == 45
    assert "ielts" in cfg.keywords
    assert "ieltsspeaking" in cfg.keywords
    assert "studyenglish" in cfg.keywords
    assert "toefl" in cfg.keywords
    assert cfg.min_views == 100
    assert cfg.window_days == 3650
    assert cfg.retain_days == 90
    assert cfg.max_per_keyword == 500


def test_parse_settings_splits_keyword_csv(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": "ielts, ieltsspeaking, "},
    )
    cfg = discovery_service._parse_settings(MagicMock())
    assert cfg.keywords == ["ielts", "ieltsspeaking"]


def test_parse_settings_overrides_numerics(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {
            "tiktok_discover_min_views": "50000",
            "tiktok_discover_window_days": "7",
            "tiktok_discover_retain_days": "30",
            "tiktok_discover_max_per_keyword": "200",
        },
    )
    cfg = discovery_service._parse_settings(MagicMock())
    assert (cfg.min_views, cfg.window_days, cfg.retain_days,
            cfg.max_per_keyword) == (50000, 7, 30, 200)


def test_filter_drops_missing_post_url():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=10000, window_days=365, max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    urls = {d.url for d in drafts}
    assert all(u for u in urls)
    # 5 records → drop missing-url (1) + below-threshold (1) → 3 left
    assert len(drafts) == 3


def test_filter_enforces_min_views():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=600000,  # only the 2.1M-view record passes
        window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    assert len(drafts) == 1
    assert drafts[0].play_count == 2100000


def test_filter_enforces_window_days():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1, window_days=60,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    # 5 records → drop missing-url (1) + 2025-09 old (1) → 3 remain
    assert len(drafts) == 3
    for d in drafts:
        delta = datetime(2026, 6, 9, tzinfo=timezone.utc) - d.create_time
        assert delta <= timedelta(days=60)


def test_filter_caps_at_max_per_keyword():
    records = _load_fixture() * 10  # 50 records, many duplicates by post_id
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1, window_days=365,
        max_per_keyword=2,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    assert len(drafts) == 2


def test_filter_dedup_by_post_id_within_batch():
    records = _load_fixture() + _load_fixture()  # double up
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1, window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    # Same dedup count as single-load
    single = discovery_service._filter_and_normalize(
        _load_fixture(), "ielts",
        min_views=1, window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    assert len(drafts) == len(single)


def test_filter_parses_full_record():
    records = _load_fixture()
    drafts = discovery_service._filter_and_normalize(
        records, "ielts",
        min_views=1000000, window_days=365,
        max_per_keyword=500,
        now=datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    d = drafts[0]
    assert d.url == "https://www.tiktok.com/@a/video/7600000000000000001"
    assert d.video_id == "7600000000000000001"
    assert d.title == "IELTS speaking tips"
    assert d.play_count == 2100000
    assert d.like_count == 103800
    assert d.comment_count == 262
    assert d.share_count == 17700
    assert d.collect_count == 102200
    assert d.author_unique_id == "gia.studs"
    assert d.author_nickname == "studying with gia"
    assert d.author_followers == 2923
    assert d.thumbnail_url == "https://example.com/thumb1.jpg"
    assert d.country == "DE"
    assert d.search_keyword == "ielts"
    assert d.create_time == datetime(2026, 4, 18, 13, 37, 28,
                                     tzinfo=timezone.utc)


def _draft(url, video_id="vid_1", play_count=100, like_count=10,
           title="t", search_keyword="ielts"):
    return discovery_service.DiscoveredVideoDraft(
        url=url, video_id=video_id,
        author_unique_id="a", author_nickname="A",
        author_followers=10, title=title,
        play_count=play_count, like_count=like_count,
        comment_count=1, share_count=1, collect_count=1,
        thumbnail_url="https://t/1.jpg",
        create_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        search_keyword=search_keyword, country="DE",
    )


def test_upsert_inserts_new_rows(session):
    drafts = [_draft("https://x/1", "vid_1"), _draft("https://x/2", "vid_2")]
    inserted, updated = discovery_service._upsert_videos(session, drafts)
    assert (inserted, updated) == (2, 0)
    rows = session.exec(select(DiscoveredVideo)).all()
    assert {r.url for r in rows} == {"https://x/1", "https://x/2"}


def test_upsert_updates_counts_but_preserves_search_keyword(session):
    first = _draft("https://x/1", "vid_1", play_count=100,
                   search_keyword="ielts")
    discovery_service._upsert_videos(session, [first])
    second = _draft("https://x/1", "vid_1", play_count=999,
                    search_keyword="ieltsspeaking")
    inserted, updated = discovery_service._upsert_videos(session, [second])
    assert (inserted, updated) == (0, 1)
    row = session.exec(select(DiscoveredVideo)).one()
    assert row.play_count == 999
    assert row.search_keyword == "ielts"   # preserved


def test_upsert_coalesces_nullable_fields(session):
    populated = _draft("https://x/1", "vid_1", title="real title")
    discovery_service._upsert_videos(session, [populated])
    nulled = discovery_service.DiscoveredVideoDraft(
        url="https://x/1", video_id="vid_1",
        author_unique_id=None, author_nickname=None, author_followers=None,
        title=None, play_count=200, like_count=20,
        comment_count=2, share_count=2, collect_count=2,
        thumbnail_url=None,
        create_time=datetime(2026, 6, 2, tzinfo=timezone.utc),
        search_keyword="ielts", country=None,
    )
    discovery_service._upsert_videos(session, [nulled])
    row = session.exec(select(DiscoveredVideo)).one()
    assert row.title == "real title"          # preserved by COALESCE
    assert row.play_count == 200              # overwritten


def _make_engagement_run(session, target_link, result="SUCCESS"):
    task = EngagementTask(action="comment", source="manual", status="PENDING")
    session.add(task); session.commit(); session.refresh(task)
    run = EngagementRun(
        engagement_task_id=task.id,
        target_link=target_link,
        action="comment",
        result=result,
    )
    session.add(run); session.commit()


def test_prune_deletes_stale_unreferenced(session):
    old = DiscoveredVideo(
        url="https://x/old", video_id="1", search_keyword="ielts",
        last_seen_at=datetime.utcnow() - timedelta(days=90),
    )
    fresh = DiscoveredVideo(
        url="https://x/fresh", video_id="2", search_keyword="ielts",
    )
    session.add(old); session.add(fresh); session.commit()
    deleted = discovery_service._prune_stale(session, retain_days=60)
    assert deleted == 1
    remaining = {r.url for r in session.exec(select(DiscoveredVideo)).all()}
    assert remaining == {"https://x/fresh"}


def test_prune_preserves_stale_but_referenced(session):
    old_used = DiscoveredVideo(
        url="https://x/old_used", video_id="3", search_keyword="ielts",
        last_seen_at=datetime.utcnow() - timedelta(days=90),
    )
    session.add(old_used); session.commit()
    _make_engagement_run(session, "https://x/old_used", result="SUCCESS")
    deleted = discovery_service._prune_stale(session, retain_days=60)
    assert deleted == 0


def test_run_discovery_cycle_aggregates_per_keyword(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": "ielts,ieltsspeaking",
                   "tiktok_discover_min_views": "1",
                   "tiktok_discover_window_days": "3650",
                   "tiktok_discover_retain_days": "60",
                   "tiktok_discover_max_per_keyword": "500"},
    )
    calls = []

    class FakeClient:
        def trigger_keyword_searches(self, keywords, *, country=""):
            calls.append(("trigger", tuple(keywords)))
            return "snap_batch"

        def poll_until_ready(self, snap, **kwargs):
            calls.append(("poll", snap))
            # BD echoes search_keyword on each row; bucket by it. The fixture
            # is all "ielts"; the cycle should still process both keywords
            # (ieltsspeaking gets an empty bucket → 0 drafts but processed).
            return _load_fixture()

    stats = discovery_service.run_discovery_cycle(session, FakeClient())
    assert stats.keywords_processed == 2
    assert stats.keywords_failed == 0
    assert stats.inserted >= 1
    assert calls[0] == ("trigger", ("ielts", "ieltsspeaking"))


def test_run_discovery_cycle_handles_batch_trigger_failure(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": "kw_a,kw_b",
                   "tiktok_discover_min_views": "1",
                   "tiktok_discover_window_days": "3650",
                   "tiktok_discover_retain_days": "60",
                   "tiktok_discover_max_per_keyword": "500"},
    )

    class CrashingClient:
        def trigger_keyword_searches(self, keywords, *, country=""):
            raise RuntimeError("BD down")

        def poll_until_ready(self, snap, **kwargs):
            raise AssertionError("should not be called")

    stats = discovery_service.run_discovery_cycle(session, CrashingClient())
    assert stats.keywords_processed == 0
    assert stats.keywords_failed == 2
    assert stats.failed_keywords == ["kw_a", "kw_b"]


def test_run_discovery_cycle_falls_back_to_legacy_client(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": "good,bad",
                   "tiktok_discover_min_views": "1",
                   "tiktok_discover_window_days": "3650",
                   "tiktok_discover_retain_days": "60",
                   "tiktok_discover_max_per_keyword": "500"},
    )

    class FlakyLegacyClient:
        # Intentionally no trigger_keyword_searches → triggers fallback.
        def trigger_keyword_search(self, kw, *, country=""):
            if kw == "bad":
                raise RuntimeError("boom")
            return f"snap_{kw}"

        def poll_until_ready(self, snap, **kwargs):
            return _load_fixture()

    stats = discovery_service.run_discovery_cycle(session, FlakyLegacyClient())
    assert stats.keywords_processed == 1
    assert stats.keywords_failed == 1
    assert "bad" in stats.failed_keywords


def test_bucket_by_keyword_routes_recognized_and_fallback():
    records = [
        {"search_keyword": "ielts", "post_id": "1"},
        {"search_keyword": "ieltsspeaking", "post_id": "2"},
        {"search_keyword": "unknown-kw", "post_id": "3"},  # fallback to first
        {"search_keyword": "", "post_id": "4"},            # fallback
        {"post_id": "5"},                                   # missing → fallback
    ]
    buckets = discovery_service._bucket_by_keyword(
        records, ["ielts", "ieltsspeaking"]
    )
    assert {r["post_id"] for r in buckets["ielts"]} == {"1", "3", "4", "5"}
    assert {r["post_id"] for r in buckets["ieltsspeaking"]} == {"2"}


def test_run_discovery_cycle_chunks_over_100_keywords(session, monkeypatch):
    # Bright Data /trigger accepts at most 100 inputs per POST. A merged
    # multi-vertical keyword list (e.g. IELTS 45 + ArenaPlus 73 = 118) must
    # be split into ≤100-keyword batches rather than sent in one over-limit
    # call that BD rejects wholesale.
    kws = [f"kw{i}" for i in range(118)]
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": ",".join(kws),
                   "tiktok_discover_min_views": "1",
                   "tiktok_discover_window_days": "3650",
                   "tiktok_discover_retain_days": "60",
                   "tiktok_discover_max_per_keyword": "500"},
    )
    trigger_sizes = []

    class FakeClient:
        def trigger_keyword_searches(self, keywords, *, country=""):
            trigger_sizes.append(len(keywords))
            return f"snap_{len(trigger_sizes)}"

        def poll_until_ready(self, snap, **kwargs):
            return []  # no rows; assert batching + full processing only

    stats = discovery_service.run_discovery_cycle(session, FakeClient())
    assert trigger_sizes == [100, 18]
    assert stats.keywords_processed == 118
    assert stats.keywords_failed == 0


def test_run_discovery_cycle_isolates_failed_chunk(session, monkeypatch):
    # A failing batch must not abort the whole cycle: its keywords are marked
    # failed, remaining batches still run (the old code `return`ed on the
    # first batch error, silently dropping every later keyword).
    kws = [f"kw{i}" for i in range(150)]  # 2 batches: 100 + 50
    monkeypatch.setattr(
        "app.services.discovery_service.srepo.get_all",
        lambda s: {"tiktok_discover_keywords": ",".join(kws),
                   "tiktok_discover_min_views": "1",
                   "tiktok_discover_window_days": "3650",
                   "tiktok_discover_retain_days": "60",
                   "tiktok_discover_max_per_keyword": "500"},
    )

    class PartialClient:
        def __init__(self):
            self.n = 0

        def trigger_keyword_searches(self, keywords, *, country=""):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("first batch down")
            return "snap2"

        def poll_until_ready(self, snap, **kwargs):
            return []

    stats = discovery_service.run_discovery_cycle(session, PartialClient())
    assert stats.keywords_failed == 100    # first batch's keywords
    assert stats.keywords_processed == 50  # second batch still ran
