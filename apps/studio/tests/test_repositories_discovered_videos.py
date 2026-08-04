from datetime import datetime, timezone

from sqlmodel import select

from app.models import DiscoveredVideo, EngagementTask, EngagementRun
from app.repositories import discovered_videos as repo


def _make_video(session, url, play_count=100):
    v = DiscoveredVideo(
        url=url, video_id=url.rsplit("/", 1)[-1],
        search_keyword="ielts", play_count=play_count,
        create_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    session.add(v); session.commit()
    return v


def _make_run(session, target_link, result):
    task = EngagementTask(action="comment", source="manual", status="PENDING")
    session.add(task); session.commit(); session.refresh(task)
    run = EngagementRun(
        engagement_task_id=task.id,
        target_link=target_link,
        action="comment",
        result=result,
    )
    session.add(run); session.commit()


def test_list_unused_returns_videos_with_no_engagement(session):
    _make_video(session, "https://x/1", play_count=100)
    _make_video(session, "https://x/2", play_count=200)
    items = repo.list_unused_targets(session)
    urls = [v.url for v in items]
    assert set(urls) == {"https://x/1", "https://x/2"}


def test_list_unused_hides_success_referenced(session):
    _make_video(session, "https://x/1")
    _make_run(session, "https://x/1", "SUCCESS")
    items = repo.list_unused_targets(session)
    assert items == []


def test_list_unused_hides_pending_queued_initiated(session):
    for url, status in [
        ("https://x/p", "PENDING"),
        ("https://x/q", "QUEUED"),
        ("https://x/i", "INITIATED"),
    ]:
        _make_video(session, url)
        _make_run(session, url, status)
    items = repo.list_unused_targets(session)
    assert items == []


def test_list_unused_shows_failure_referenced(session):
    _make_video(session, "https://x/f")
    _make_run(session, "https://x/f", "FAILURE")
    items = repo.list_unused_targets(session)
    assert len(items) == 1
    assert items[0].url == "https://x/f"


def test_list_unused_orders_by_play_count_desc(session):
    _make_video(session, "https://x/low", play_count=100)
    _make_video(session, "https://x/mid", play_count=500)
    _make_video(session, "https://x/high", play_count=999)
    items = repo.list_unused_targets(session)
    assert [v.url for v in items] == [
        "https://x/high", "https://x/mid", "https://x/low"
    ]


def test_list_unused_respects_limit(session):
    for i in range(5):
        _make_video(session, f"https://x/{i}", play_count=i * 10)
    items = repo.list_unused_targets(session, limit=2)
    assert len(items) == 2
