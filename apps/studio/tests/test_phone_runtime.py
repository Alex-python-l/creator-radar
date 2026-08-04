"""Tests for the phone_sessions repo + PhoneRuntimeManager."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models import Account
from app.repositories import phone_sessions as ps_repo
from app.services import phone_runtime as runtime


@pytest.fixture
def account(db):
    a = Account(profile_name="alice", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    return a


# ── start_session ──────────────────────────────────────────────── #

def test_start_session_creates_active_row(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
        owner_type="publication", owner_id=42,
    )
    row = ps_repo.get(db, sid)
    assert row["status"] == "ACTIVE"
    assert row["final_status"] is None
    assert row["stopped_at"] is None
    assert row["account_id"] == account.id
    assert row["env_id"] == "ph-1"
    assert row["source"] == "publish"
    assert row["owner_type"] == "publication"
    assert row["owner_id"] == 42


def test_start_session_default_budget_per_source(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1", source="warmup",
    )
    row = ps_repo.get(db, sid)
    delta = row["budget_deadline_at"] - row["started_at"]
    expected = runtime.DEFAULT_BUDGET_SECONDS["warmup"]
    assert abs(delta.total_seconds() - expected) < 2


def test_start_session_unknown_source_without_budget_raises(db, account):
    with pytest.raises(ValueError):
        runtime.start_session(
            db, account_id=account.id, env_id="ph-1",
            source="weird-source",
        )


def test_start_session_second_call_same_account_is_phone_busy(db, account):
    runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    with pytest.raises(runtime.PhoneBusy):
        runtime.start_session(
            db, account_id=account.id, env_id="ph-1",
            source="warmup", budget_seconds=300,
        )


def test_start_session_after_finalize_can_start_again(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    runtime.finalize_success(db, sid)
    sid2 = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="warmup", budget_seconds=600,
    )
    assert sid2 != sid


# ── finalize / timeout ─────────────────────────────────────────── #

def test_finalize_success(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    runtime.finalize_success(db, sid, observed_status="3")
    row = ps_repo.get(db, sid)
    assert row["status"] == "STOPPED"
    assert row["final_status"] == "STOPPED"
    assert row["stopped_at"] is not None
    assert row["stop_reason"] is None
    assert row["last_observed_status"] == "3"


def test_finalize_failure_records_reason(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    runtime.finalize_failure(
        db, sid, reason="task not found", observed_status="4",
    )
    row = ps_repo.get(db, sid)
    assert row["status"] == "STOPPED"
    assert row["final_status"] == "FAILED"
    assert row["stop_reason"] == "task not found"


def test_mark_timeout(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    runtime.mark_timeout(
        db, sid, reason="budget exceeded", observed_status="2",
    )
    row = ps_repo.get(db, sid)
    assert row["status"] == "TIMEOUT"
    assert row["final_status"] == "TIMEOUT"
    assert row["stop_reason"] == "budget exceeded"


def test_finalize_is_idempotent(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    runtime.finalize_success(db, sid)
    row1 = ps_repo.get(db, sid)
    runtime.finalize_failure(db, sid, reason="ignored second time")
    row2 = ps_repo.get(db, sid)
    assert row2["status"] == "STOPPED"
    assert row2["final_status"] == "STOPPED"
    assert row2["stopped_at"] == row1["stopped_at"]


def test_finalize_unknown_session_is_noop(db):
    runtime.finalize_success(db, 99999)
    runtime.finalize_failure(db, 99999, reason="x")
    runtime.mark_timeout(db, 99999, reason="x")


# ── attach_geelark_task ────────────────────────────────────────── #

def test_attach_geelark_task(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    runtime.attach_geelark_task(db, sid, "tk-abc")
    row = ps_repo.get(db, sid)
    assert row["geelark_task_id"] == "tk-abc"
    via_task = ps_repo.get_by_geelark_task(db, "tk-abc")
    assert via_task["id"] == sid


# ── renew_budget ───────────────────────────────────────────────── #

def test_renew_budget_refreshes_deadline(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=60,
    )
    orig_deadline = ps_repo.get(db, sid)["budget_deadline_at"]
    runtime.renew_budget(db, sid, budget_seconds=3600)
    row = ps_repo.get(db, sid)
    assert row["budget_deadline_at"] > orig_deadline


def test_renew_after_finalize_raises(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=60,
    )
    runtime.finalize_success(db, sid)
    with pytest.raises(ValueError):
        runtime.renew_budget(db, sid, budget_seconds=3600)


# ── repo: list_overdue ─────────────────────────────────────────── #

def test_list_overdue_returns_only_inflight_past_deadline(db, account):
    # In-flight + future deadline → not overdue.
    runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=3600,
    )

    a2 = Account(profile_name="bob", geelark_phone_id="ph-2")
    db.add(a2); db.commit(); db.refresh(a2)
    sid2 = runtime.start_session(
        db, account_id=a2.id, env_id="ph-2",
        source="publish", budget_seconds=60,
    )
    # Backdate budget to simulate overdue.
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :past "
            "WHERE id = :id"
        ),
        {"past": datetime.utcnow() - timedelta(hours=1), "id": sid2},
    )
    db.commit()

    overdue = ps_repo.list_overdue(db)
    assert len(overdue) == 1
    assert overdue[0]["id"] == sid2


def test_list_overdue_excludes_stopped_sessions(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=60,
    )
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :past "
            "WHERE id = :id"
        ),
        {"past": datetime.utcnow() - timedelta(hours=1), "id": sid},
    )
    db.commit()
    runtime.finalize_success(db, sid)
    assert ps_repo.list_overdue(db) == []


# ── repo: create input validation ──────────────────────────────── #

def test_create_missing_required_raises(db):
    with pytest.raises(ValueError):
        ps_repo.create(
            db, account_id=1, env_id="ph", source="publish",
            # missing started_at / budget_deadline_at / status / created_by
        )


# ── get_active_for_account ─────────────────────────────────────── #

def test_get_active_for_account_returns_session_then_none_after_stop(
        db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", budget_seconds=600,
    )
    active = runtime.get_active_for_account(db, account.id)
    assert active["id"] == sid
    runtime.finalize_success(db, sid)
    assert runtime.get_active_for_account(db, account.id) is None
