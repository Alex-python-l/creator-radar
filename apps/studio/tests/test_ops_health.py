"""Tests for the inbox/outbox/youtube-audit ops endpoints (Phase 8-10).

These are observability endpoints — the assertions check that the
numbers a dashboard would read off match the underlying tables, and
that the manual-override path (mark-failed) does what its docstring
promises.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def authed_client(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    return client


# ---------------------------------------------------------------------------
# Inbox health
# ---------------------------------------------------------------------------
def test_inbox_health_counts_24h_and_pending(authed_client, db):
    now = datetime.utcnow()
    # 3 callbacks in the last 24h, 2 of them processed.
    for i in range(3):
        db.execute(
            text(
                "INSERT INTO callback_inbox "
                "(task_id, status, body_hash, received_at, processed_at, "
                " process_outcome) "
                "VALUES (:tid, :st, :bh, :recv, :proc, :out)"
            ),
            {
                "tid": f"tk-inbox-{i}",
                "st": 3,
                "bh": f"hash-{i}",
                "recv": now - timedelta(minutes=i),
                "proc": (now - timedelta(minutes=i)) if i < 2 else None,
                "out": "wake:publish" if i < 2 else None,
            },
        )
    # 1 callback older than 24h — should NOT count in total_24h.
    db.execute(
        text(
            "INSERT INTO callback_inbox "
            "(task_id, status, body_hash, received_at, processed_at, "
            " process_outcome) "
            "VALUES (:tid, 3, 'old-hash', :recv, :proc, 'wake:publish')"
        ),
        {
            "tid": "tk-ancient",
            "recv": now - timedelta(days=2),
            "proc": now - timedelta(days=2),
        },
    )
    db.commit()

    r = authed_client.get("/api/ops/inbox/health")
    assert r.status_code == 200
    body = r.json()
    assert body["total_24h"] == 3
    assert body["unprocessed"] == 1
    assert body["outcomes_24h"]["wake:publish"] == 2
    assert body["outcomes_24h"]["pending"] == 1


def test_inbox_health_oldest_unprocessed_seconds(authed_client, db):
    now = datetime.utcnow()
    db.execute(
        text(
            "INSERT INTO callback_inbox "
            "(task_id, status, body_hash, received_at) "
            "VALUES ('tk-old', 3, 'h', :recv)"
        ),
        {"recv": now - timedelta(minutes=10)},
    )
    db.commit()
    r = authed_client.get("/api/ops/inbox/health")
    body = r.json()
    assert body["oldest_unprocessed_seconds"] is not None
    # Within ±30s slack of 10 minutes.
    assert 570 <= body["oldest_unprocessed_seconds"] <= 630


# ---------------------------------------------------------------------------
# Outbox health
# ---------------------------------------------------------------------------
def test_outbox_health_groups_by_intent(authed_client, db):
    from app.repositories import outbox as outbox_repo
    outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:1", payload={"publication_id": 1},
    )
    outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:2", payload={"publication_id": 2},
    )
    outbox_repo.enqueue(
        db, intent="submit_engagement_run",
        business_ref="engagement_run:9", payload={"run_id": 9},
    )
    db.commit()

    r = authed_client.get("/api/ops/outbox/health")
    body = r.json()
    assert body["pending"] == 3
    intents = {row["intent"]: row for row in body["per_intent"]}
    assert intents["publish_publication"]["pending"] == 2
    assert intents["submit_engagement_run"]["pending"] == 1
    assert intents["publish_publication"]["avg_attempts"] == 0.0


def test_outbox_health_pending_overdue(authed_client, db):
    """pending_overdue counts rows whose next_attempt_at is in the past
    — the dispatcher should have picked these up by now."""
    from app.repositories import outbox as outbox_repo
    overdue_id = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:101", payload={},
    )
    fresh_id = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:102", payload={},
    )
    # Force one row's next_attempt_at into the past.
    db.execute(
        text(
            "UPDATE outbox SET next_attempt_at = :past WHERE id = :id"
        ),
        {"past": datetime.utcnow() - timedelta(minutes=5), "id": overdue_id},
    )
    # And another into the far future (so it doesn't count as overdue).
    db.execute(
        text(
            "UPDATE outbox SET next_attempt_at = :future WHERE id = :id"
        ),
        {"future": datetime.utcnow() + timedelta(hours=1), "id": fresh_id},
    )
    db.commit()
    r = authed_client.get("/api/ops/outbox/health")
    body = r.json()
    assert body["pending"] == 2
    assert body["pending_overdue"] == 1


# ---------------------------------------------------------------------------
# YouTube audit + mark-failed
# ---------------------------------------------------------------------------
def _seed_youtube_publication(db, *, video_id: int, account_id: int,
                                fail_screenshot: str | None) -> int:
    from app.models import Publication
    pub = Publication(
        video_id=video_id, platform="youtube",
        result="SUCCESS", account_id=account_id,
        fail_screenshot=fail_screenshot,
        posted_at=datetime.utcnow(),
        share_link="https://yt.example/abc",
    )
    db.add(pub); db.commit(); db.refresh(pub)
    return pub.id


def test_youtube_audit_lists_only_success_with_screenshot(
        authed_client, db, sample_video):
    from app.models import Video
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "YT", "geelark_phone_id": "ph-yt"})
    other = Video(
        original_filename="other.mp4", stored_path="/tmp/other.mp4",
        thumbnail_path="thumb/other.jpg", duration_seconds=10,
    )
    db.add(other); db.commit(); db.refresh(other)
    with_shot = _seed_youtube_publication(
        db, video_id=sample_video.id, account_id=aid,
        fail_screenshot="https://geelark.example/final.jpg",
    )
    # A SUCCESS row WITHOUT a screenshot — should NOT appear.
    no_shot = _seed_youtube_publication(
        db, video_id=other.id, account_id=aid,
        fail_screenshot=None,
    )

    r = authed_client.get("/api/ops/youtube-audit")
    assert r.status_code == 200
    items = r.json()["items"]
    ids = [it["id"] for it in items]
    assert with_shot in ids
    assert no_shot not in ids
    item = next(it for it in items if it["id"] == with_shot)
    assert item["fail_screenshot"] == "https://geelark.example/final.jpg"
    assert item["share_link"] == "https://yt.example/abc"


def test_mark_failed_resets_publication_for_retry(
        authed_client, db, sample_video):
    from app.repositories import accounts as acc_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(db, {"profile_name": "YTR", "geelark_phone_id": "ph-ytr"})
    pid = _seed_youtube_publication(
        db, video_id=sample_video.id, account_id=aid,
        fail_screenshot="https://geelark.example/final.jpg",
    )
    # Pre-seed a geelark_task_id so we can check it gets wiped.
    db.execute(text(
        "UPDATE publications SET geelark_task_id = 'old-task' WHERE id = :id"
    ), {"id": pid})
    db.commit()

    r = authed_client.post(
        f"/api/ops/publications/{pid}/mark-failed",
        json={"reason": "operator: youtube title over limit"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["retry"] is True

    db.expire_all()
    pub = pub_repo.get(db, pid)
    assert pub["result"] == "PENDING"
    assert pub["geelark_task_id"] is None
    assert "title over limit" in (pub["notes"] or "")
    assert pub["fail_screenshot"] is None


def test_mark_failed_with_retry_false_is_terminal(
        authed_client, db, sample_video):
    from app.repositories import accounts as acc_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(db, {"profile_name": "YTT", "geelark_phone_id": "ph-ytt"})
    pid = _seed_youtube_publication(
        db, video_id=sample_video.id, account_id=aid,
        fail_screenshot="https://x/img.jpg",
    )
    r = authed_client.post(
        f"/api/ops/publications/{pid}/mark-failed",
        json={"reason": "operator: real failure", "retry": False},
    )
    assert r.status_code == 200
    assert r.json()["retry"] is False
    db.expire_all()
    pub = pub_repo.get(db, pid)
    assert pub["result"] == "FAILURE"


def test_mark_failed_404_on_missing(authed_client):
    r = authed_client.post(
        "/api/ops/publications/99999/mark-failed",
        json={"reason": "x"},
    )
    assert r.status_code == 404
