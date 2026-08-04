"""Tests for the GeeLark callback inbox + dispatcher.

The endpoint (app/api/geelark.py) writes one inbox row per unique
(task_id, status, body_hash). The dispatcher
(app/tasks/geelark_callback.py) resolves that row to a business entity
via phone_sessions.geelark_task_id and wakes the matching
check_*_status task.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text


@pytest.fixture
def token(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "GEELARK_CALLBACK_TOKEN", "s3cret-test-token")
    return "s3cret-test-token"


def _count_inbox(db) -> int:
    return db.execute(
        text("SELECT count(*) FROM callback_inbox")
    ).scalar() or 0


def _last_inbox(db) -> dict | None:
    # Worker writes happen in a separate session; expire so we re-read
    # from the DB instead of returning a stale snapshot.
    db.expire_all()
    db.commit()
    row = db.execute(
        text(
            "SELECT id, task_id, status, body_hash, "
            "processed_at, process_outcome "
            "FROM callback_inbox ORDER BY id DESC LIMIT 1"
        )
    ).first()
    if row is None:
        return None
    return {
        "id": row.id, "task_id": row.task_id,
        "status": row.status, "body_hash": row.body_hash,
        "processed_at": row.processed_at,
        "process_outcome": row.process_outcome,
    }


# ---------------------------------------------------------------------------
# Endpoint side: inbox dedup
# ---------------------------------------------------------------------------
def test_callback_writes_inbox_row_for_new_event(client, db, token):
    r = client.post(
        f"/api/geelark/callback?token={token}",
        json={"taskId": "tk-inbox-1", "status": 3},
    )
    assert r.status_code == 200
    assert _count_inbox(db) == 1
    row = _last_inbox(db)
    assert row["task_id"] == "tk-inbox-1"
    assert row["status"] == 3
    assert len(row["body_hash"]) == 64  # sha256 hex
    # process_outcome is set by the worker; the in-memory connector
    # fixture is not active in this test, so the defer call is a no-op
    # without the procrastinate_app fixture and the row stays unprocessed.
    assert row["processed_at"] is None


def test_callback_dedups_identical_replay(client, db, token):
    """Same task_id+status+body lands once even when GeeLark retries."""
    body = {"taskId": "tk-dup", "status": 3}
    for _ in range(3):
        r = client.post(f"/api/geelark/callback?token={token}", json=body)
        assert r.status_code == 200
    assert _count_inbox(db) == 1


def test_callback_distinct_statuses_each_get_their_own_row(client, db, token):
    """Same task_id but distinct status fields are distinct events."""
    client.post(
        f"/api/geelark/callback?token={token}",
        json={"taskId": "tk-multi", "status": 2},
    )
    client.post(
        f"/api/geelark/callback?token={token}",
        json={"taskId": "tk-multi", "status": 3},
    )
    rows = db.execute(
        text(
            "SELECT status FROM callback_inbox "
            "WHERE task_id = 'tk-multi' ORDER BY status"
        )
    ).fetchall()
    assert [r.status for r in rows] == [2, 3]


def test_callback_without_task_id_skips_inbox(client, db, token):
    """Payloads we can't parse a task_id from never produce an inbox
    row — only the raw log captures them."""
    r = client.post(
        f"/api/geelark/callback?token={token}",
        content=b"not json at all",
    )
    assert r.status_code == 200
    assert _count_inbox(db) == 0


# ---------------------------------------------------------------------------
# Dispatcher: wake the right check_*_status_task
# ---------------------------------------------------------------------------
def _insert_inbox(db, *, task_id: str, status: int = 3,
                   body_hash: str = "abc") -> int:
    row = db.execute(
        text(
            "INSERT INTO callback_inbox (task_id, status, body_hash) "
            "VALUES (:tid, :st, :bh) RETURNING id"
        ),
        {"tid": task_id, "st": status, "bh": body_hash},
    ).first()
    db.commit()
    return row.id


def _create_session_with_task(
        db, *, source: str, owner_type: str, owner_id: int,
        geelark_task_id: str, account_id: int, env_id: str = "ph-test",
) -> int:
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    row = db.execute(
        text(
            "INSERT INTO phone_sessions "
            "(account_id, env_id, source, owner_type, owner_id, "
            " geelark_task_id, started_at, budget_deadline_at, "
            " status, created_by) "
            "VALUES (:aid, :env, :src, :ot, :oi, :gt, :st, :dl, "
            " 'ACTIVE', 'test') RETURNING id"
        ),
        {
            "aid": account_id, "env": env_id,
            "src": source, "ot": owner_type, "oi": owner_id,
            "gt": geelark_task_id,
            "st": now, "dl": now + timedelta(minutes=10),
        },
    ).first()
    db.commit()
    return row.id


def test_dispatch_wakes_publish_check(
        procrastinate_app, db):
    from app.repositories import accounts as acc_repo
    from app.tasks.geelark_callback import process_geelark_callback_task

    aid = acc_repo.create(db, {
        "profile_name": "cbk-pub",
        "geelark_phone_id": "ph-cbk-pub",
    })
    _create_session_with_task(
        db, source="publish", owner_type="publication", owner_id=777,
        geelark_task_id="tk-pub", account_id=aid,
        env_id="ph-cbk-pub",
    )
    inbox_id = _insert_inbox(db, task_id="tk-pub", status=3)

    outcome = asyncio.run(process_geelark_callback_task(inbox_id=inbox_id))
    assert outcome == "wake:publish"

    jobs = procrastinate_app.connector.jobs
    publish_jobs = [
        j for j in jobs.values()
        if j["task_name"] == "check_publication_status"
    ]
    assert len(publish_jobs) == 1
    assert publish_jobs[0]["args"]["publication_id"] == 777

    row = _last_inbox(db)
    assert row["process_outcome"] == "wake:publish"
    assert row["processed_at"] is not None


def test_dispatch_wakes_engagement_check(procrastinate_app, db):
    from app.repositories import accounts as acc_repo
    from app.tasks.geelark_callback import process_geelark_callback_task

    aid = acc_repo.create(db, {
        "profile_name": "cbk-eng",
        "geelark_phone_id": "ph-cbk-eng",
    })
    _create_session_with_task(
        db, source="engagement", owner_type="engagement_run", owner_id=42,
        geelark_task_id="tk-eng", account_id=aid,
        env_id="ph-cbk-eng",
    )
    inbox_id = _insert_inbox(db, task_id="tk-eng", status=3)

    outcome = asyncio.run(process_geelark_callback_task(inbox_id=inbox_id))
    assert outcome == "wake:engagement"

    jobs = procrastinate_app.connector.jobs
    eng_jobs = [
        j for j in jobs.values()
        if j["task_name"] == "check_engagement_run"
    ]
    assert len(eng_jobs) == 1
    assert eng_jobs[0]["args"]["run_id"] == 42


def test_dispatch_wakes_warmup_check(procrastinate_app, db):
    from app.repositories import accounts as acc_repo
    from app.tasks.geelark_callback import process_geelark_callback_task

    aid = acc_repo.create(db, {
        "profile_name": "cbk-wm",
        "geelark_phone_id": "ph-cbk-wm",
    })
    _create_session_with_task(
        db, source="warmup", owner_type="account", owner_id=aid,
        geelark_task_id="tk-warmup", account_id=aid,
        env_id="ph-cbk-wm",
    )
    inbox_id = _insert_inbox(db, task_id="tk-warmup", status=3)

    outcome = asyncio.run(process_geelark_callback_task(inbox_id=inbox_id))
    assert outcome == "wake:warmup"

    jobs = procrastinate_app.connector.jobs
    wm_jobs = [
        j for j in jobs.values()
        if j["task_name"] == "check_warmup_status"
    ]
    assert len(wm_jobs) == 1
    assert wm_jobs[0]["args"]["account_id"] == aid
    assert wm_jobs[0]["args"]["task_id"] == "tk-warmup"


def test_dispatch_no_session_marks_outcome(procrastinate_app, db):
    """A callback for a task_id we never saw locally must not error —
    just mark the inbox row so an operator can spot lost mappings."""
    from app.tasks.geelark_callback import process_geelark_callback_task

    inbox_id = _insert_inbox(db, task_id="tk-stray", status=3)

    outcome = asyncio.run(process_geelark_callback_task(inbox_id=inbox_id))
    assert outcome == "no_dispatch"
    row = _last_inbox(db)
    assert row["process_outcome"] == "no_session"
    assert row["processed_at"] is not None
    # No jobs deferred.
    jobs = procrastinate_app.connector.jobs
    assert not any(
        j["task_name"].startswith("check_") for j in jobs.values()
    )


def test_dispatch_manual_session_is_unhandled(procrastinate_app, db):
    """Manual phone holds don't have a polling chain to wake; the
    operator owns the session."""
    from app.repositories import accounts as acc_repo
    from app.tasks.geelark_callback import process_geelark_callback_task

    aid = acc_repo.create(db, {
        "profile_name": "cbk-manual",
        "geelark_phone_id": "ph-cbk-manual",
    })
    _create_session_with_task(
        db, source="manual", owner_type="account", owner_id=aid,
        geelark_task_id="tk-manual", account_id=aid,
        env_id="ph-cbk-manual",
    )
    inbox_id = _insert_inbox(db, task_id="tk-manual", status=3)

    outcome = asyncio.run(process_geelark_callback_task(inbox_id=inbox_id))
    assert outcome == "no_dispatch"
    row = _last_inbox(db)
    assert row["process_outcome"].startswith("unhandled:")


def test_inbox_reaper_redefers_old_unprocessed_rows(procrastinate_app, db):
    """An inbox row whose original defer was lost (endpoint crashed,
    App not open) must get re-deferred once it's older than the
    reaper's min-age cutoff."""
    from datetime import datetime, timedelta
    import asyncio as _asyncio
    from app.tasks.geelark_callback import reclaim_callback_inbox_task

    fresh_id = _insert_inbox(db, task_id="tk-fresh", status=3,
                              body_hash="fresh")
    stale_id = _insert_inbox(db, task_id="tk-stale", status=3,
                              body_hash="stale")
    # Backdate the stale row past the 120s cutoff.
    db.execute(
        text(
            "UPDATE callback_inbox SET received_at = :past "
            "WHERE id = :id"
        ),
        {"past": datetime.utcnow() - timedelta(minutes=5),
         "id": stale_id},
    )
    db.commit()

    out = _asyncio.run(reclaim_callback_inbox_task(timestamp=0))
    assert "deferred=1" in out
    jobs = procrastinate_app.connector.jobs
    process_jobs = [
        j for j in jobs.values()
        if j["task_name"] == "process_geelark_callback"
    ]
    assert len(process_jobs) == 1
    assert process_jobs[0]["args"]["inbox_id"] == stale_id


def test_inbox_reaper_skips_processed_rows(procrastinate_app, db):
    """A row marked processed_at must not be re-deferred even if it
    is older than the cutoff."""
    from datetime import datetime, timedelta
    import asyncio as _asyncio
    from app.tasks.geelark_callback import reclaim_callback_inbox_task

    processed_id = _insert_inbox(db, task_id="tk-done", status=3,
                                  body_hash="done")
    db.execute(
        text(
            "UPDATE callback_inbox SET received_at = :past, "
            "processed_at = :now, process_outcome = 'wake:publish' "
            "WHERE id = :id"
        ),
        {
            "past": datetime.utcnow() - timedelta(minutes=5),
            "now": datetime.utcnow(),
            "id": processed_id,
        },
    )
    db.commit()

    out = _asyncio.run(reclaim_callback_inbox_task(timestamp=0))
    assert "deferred=0" in out


def test_dispatch_already_processed_is_noop(procrastinate_app, db):
    """A second defer for the same inbox row (rare but possible if a
    timeout retry sneaks past) must not double-wake."""
    from app.tasks.geelark_callback import process_geelark_callback_task

    inbox_id = _insert_inbox(db, task_id="tk-already", status=3)
    db.execute(
        text(
            "UPDATE callback_inbox SET processed_at = now(), "
            "process_outcome = 'wake:publish' WHERE id = :id"
        ),
        {"id": inbox_id},
    )
    db.commit()

    outcome = asyncio.run(process_geelark_callback_task(inbox_id=inbox_id))
    assert outcome == "no_dispatch"
    # No new wakes.
    jobs = procrastinate_app.connector.jobs
    assert not any(
        j["task_name"].startswith("check_") for j in jobs.values()
    )
