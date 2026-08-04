"""Outbox repository + dispatcher tests.

The outbox guarantees that any business transaction whose API handler
or scanner expects a follow-up worker task gets exactly that task,
even when the prompt defer fails. These tests pin the three things
that matter:

  - enqueue is idempotent on (intent, business_ref) while pending.
  - record_failure walks the exponential backoff schedule.
  - the dispatcher honors next_attempt_at + mark_sent semantics.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.repositories import outbox as outbox_repo


# ---------------------------------------------------------------------------
# enqueue + uniqueness
# ---------------------------------------------------------------------------
def test_enqueue_returns_id_and_persists(db):
    new_id = outbox_repo.enqueue(
        db,
        intent="publish_publication",
        business_ref="publication:1",
        payload={"publication_id": 1},
    )
    db.commit()
    assert new_id is not None
    row = db.execute(
        text(
            "SELECT intent, business_ref, payload, attempts, sent_at "
            "FROM outbox WHERE id = :id"
        ),
        {"id": new_id},
    ).first()
    assert row.intent == "publish_publication"
    assert row.business_ref == "publication:1"
    assert row.payload == {"publication_id": 1}
    assert row.attempts == 0
    assert row.sent_at is None


def test_enqueue_dedupes_same_business_ref_while_pending(db):
    """A repeated enqueue for the same (intent, business_ref) returns
    None and does NOT create a second row — the partial UNIQUE index
    rejects the insert."""
    first = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:42", payload={"publication_id": 42},
    )
    db.commit()
    second = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:42", payload={"publication_id": 42},
    )
    db.commit()
    assert first is not None
    assert second is None
    count = db.execute(text(
        "SELECT count(*) FROM outbox WHERE business_ref = 'publication:42'"
    )).scalar()
    assert count == 1


def test_enqueue_distinguishes_intents(db):
    """Same business_ref but different intent is a different event."""
    id_a = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:7", payload={},
    )
    id_b = outbox_repo.enqueue(
        db, intent="extract_understanding",
        business_ref="publication:7", payload={},
    )
    db.commit()
    assert id_a and id_b
    assert id_a != id_b


def test_enqueue_after_mark_sent_allows_new_row(db):
    """Once the previous row is marked sent, a fresh enqueue for the
    same (intent, business_ref) succeeds."""
    first = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:99", payload={},
    )
    db.commit()
    outbox_repo.mark_sent(db, first)
    second = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:99", payload={},
    )
    db.commit()
    assert first and second
    assert second != first


# ---------------------------------------------------------------------------
# mark_sent + record_failure
# ---------------------------------------------------------------------------
def test_mark_sent_sets_timestamp_and_is_idempotent(db):
    new_id = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:5", payload={},
    )
    db.commit()
    outbox_repo.mark_sent(db, new_id)
    sent_at_1 = db.execute(text(
        "SELECT sent_at FROM outbox WHERE id = :id"
    ), {"id": new_id}).scalar()
    assert sent_at_1 is not None

    # Second call must not overwrite the original timestamp.
    outbox_repo.mark_sent(db, new_id)
    sent_at_2 = db.execute(text(
        "SELECT sent_at FROM outbox WHERE id = :id"
    ), {"id": new_id}).scalar()
    assert sent_at_2 == sent_at_1


def test_record_failure_increments_attempts_and_backs_off(db):
    new_id = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:11", payload={},
    )
    db.commit()

    # First failure: attempts=1, retry in ~30s.
    before = datetime.utcnow()
    outbox_repo.record_failure(db, new_id, "boom 1")
    row = db.execute(text(
        "SELECT attempts, next_attempt_at, last_error "
        "FROM outbox WHERE id = :id"
    ), {"id": new_id}).first()
    assert row.attempts == 1
    assert row.last_error == "boom 1"
    delta = (row.next_attempt_at - before).total_seconds()
    assert 25 <= delta <= 45  # 30s ± slack

    # Second failure: attempts=2, retry in ~2 min.
    outbox_repo.record_failure(db, new_id, "boom 2")
    row = db.execute(text(
        "SELECT attempts, next_attempt_at FROM outbox WHERE id = :id"
    ), {"id": new_id}).first()
    assert row.attempts == 2
    delta = (row.next_attempt_at - datetime.utcnow()).total_seconds()
    assert 100 <= delta <= 140  # 120s ± slack


def test_record_failure_caps_backoff_at_one_hour(db):
    new_id = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:13", payload={},
    )
    db.commit()
    for _ in range(8):
        outbox_repo.record_failure(db, new_id, "still broken")
    row = db.execute(text(
        "SELECT attempts, next_attempt_at FROM outbox WHERE id = :id"
    ), {"id": new_id}).first()
    assert row.attempts == 8
    delta = (row.next_attempt_at - datetime.utcnow()).total_seconds()
    # After many failures the backoff caps at 1 hour.
    assert 3500 <= delta <= 3700


# ---------------------------------------------------------------------------
# list_pending honors next_attempt_at
# ---------------------------------------------------------------------------
def test_list_pending_excludes_future_rows(db):
    soon = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:21", payload={},
    )
    later = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:22", payload={},
    )
    db.execute(text(
        "UPDATE outbox SET next_attempt_at = :future WHERE id = :id"
    ), {"future": datetime.utcnow() + timedelta(hours=1), "id": later})
    db.commit()

    pending = outbox_repo.list_pending(db, limit=10)
    pending_ids = [r["id"] for r in pending]
    assert soon in pending_ids
    assert later not in pending_ids


def test_list_pending_excludes_sent_rows(db):
    sent_id = outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:31", payload={},
    )
    db.commit()
    outbox_repo.mark_sent(db, sent_id)
    pending = outbox_repo.list_pending(db, limit=10)
    assert sent_id not in {r["id"] for r in pending}


# ---------------------------------------------------------------------------
# Dispatcher routes intents to the right Procrastinate task
# ---------------------------------------------------------------------------
def test_dispatcher_publish_intent_defers_publish_task(
        procrastinate_app, db):
    """The dispatcher walks each pending row → task.defer_async →
    mark_sent. Publish intent must hit publish_publication_task with
    the right publication_id."""
    from app.tasks.outbox_dispatcher import dispatch_outbox_task

    outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:101",
        payload={"publication_id": 101},
    )
    db.commit()

    out = asyncio.run(dispatch_outbox_task(timestamp=0))
    assert "sent=1" in out

    jobs = procrastinate_app.connector.jobs
    publish_jobs = [
        j for j in jobs.values()
        if j["task_name"] == "publish_publication"
    ]
    assert len(publish_jobs) == 1
    assert publish_jobs[0]["args"]["publication_id"] == 101

    # Row is now marked sent.
    sent_at = db.execute(text(
        "SELECT sent_at FROM outbox WHERE business_ref = :ref"
    ), {"ref": "publication:101"}).scalar()
    assert sent_at is not None


def test_dispatcher_engagement_intent_defers_engagement_task(
        procrastinate_app, db):
    from app.tasks.outbox_dispatcher import dispatch_outbox_task

    outbox_repo.enqueue(
        db, intent="submit_engagement_run",
        business_ref="engagement_run:55", payload={"run_id": 55},
    )
    db.commit()

    asyncio.run(dispatch_outbox_task(timestamp=0))
    jobs = procrastinate_app.connector.jobs
    eng_jobs = [
        j for j in jobs.values()
        if j["task_name"] == "submit_engagement_run"
    ]
    assert len(eng_jobs) == 1
    assert eng_jobs[0]["args"]["run_id"] == 55


def test_dispatcher_understanding_intent_defers_extract_task(
        procrastinate_app, db):
    from app.tasks.outbox_dispatcher import dispatch_outbox_task

    outbox_repo.enqueue(
        db, intent="extract_understanding",
        business_ref="video:77", payload={"video_id": 77},
    )
    db.commit()

    asyncio.run(dispatch_outbox_task(timestamp=0))
    jobs = procrastinate_app.connector.jobs
    ext_jobs = [
        j for j in jobs.values()
        if j["task_name"] == "extract_understanding"
    ]
    assert len(ext_jobs) == 1
    assert ext_jobs[0]["args"]["video_id"] == 77


def test_dispatcher_records_failure_on_unknown_intent(
        procrastinate_app, db):
    """An intent the dispatcher doesn't recognise is a wiring bug; the
    row sits with attempts > 0 and last_error populated."""
    from app.tasks.outbox_dispatcher import dispatch_outbox_task

    outbox_repo.enqueue(
        db, intent="not_a_real_intent",
        business_ref="x:1", payload={},
    )
    db.commit()

    out = asyncio.run(dispatch_outbox_task(timestamp=0))
    assert "failed=1" in out

    row = db.execute(text(
        "SELECT attempts, last_error, sent_at FROM outbox "
        "WHERE business_ref = 'x:1'"
    )).first()
    assert row.attempts == 1
    assert row.last_error and "unknown outbox intent" in row.last_error
    assert row.sent_at is None


def test_dispatcher_records_failure_when_underlying_defer_raises(
        procrastinate_app, db, monkeypatch):
    """A transient defer error must NOT mark the row sent — record the
    failure and let the next tick retry."""
    from app.tasks.outbox_dispatcher import dispatch_outbox_task

    async def boom(**kwargs):
        raise RuntimeError("queue down")

    from app.tasks.publish import publish_publication_task
    monkeypatch.setattr(publish_publication_task, "defer_async", boom)

    outbox_repo.enqueue(
        db, intent="publish_publication",
        business_ref="publication:444", payload={"publication_id": 444},
    )
    db.commit()

    out = asyncio.run(dispatch_outbox_task(timestamp=0))
    assert "failed=1" in out

    row = db.execute(text(
        "SELECT attempts, sent_at FROM outbox "
        "WHERE business_ref = 'publication:444'"
    )).first()
    assert row.attempts == 1
    assert row.sent_at is None
