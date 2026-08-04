"""Phase 2 — task_runs business state machine paired with phone_sessions.

phone_runtime.start_session called with `kind=` creates a task_run row
alongside the phone_session. attach_geelark_task transitions the
task_run to SUBMITTED_TO_GEELARK. finalize_success / finalize_failure /
mark_timeout transition to SUCCESS / FAILURE / TIMEOUT and stamp
finished_at + failure_reason.

Manual hold + external_detected sessions (kind=None) DO NOT create
task_runs — those have no business referent.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models import Account
from app.repositories import phone_sessions as ps_repo
from app.repositories import task_runs as tr_repo
from app.services import phone_runtime as runtime


@pytest.fixture
def account(db):
    a = Account(profile_name="task-run-test", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_start_session_without_kind_does_not_create_task_run(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="manual", owner_type="account", owner_id=account.id,
        budget_seconds=3600, created_by="ops",
    )
    assert ps_repo.get(db, sid) is not None
    assert tr_repo.get_by_phone_session(db, sid) is None


def test_start_session_with_kind_creates_phone_starting_task_run(
        db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", owner_type="publication", owner_id=42,
        budget_seconds=900, created_by="publish",
        kind="publish",
    )
    tr = tr_repo.get_by_phone_session(db, sid)
    assert tr is not None
    assert tr["kind"] == "publish"
    assert tr["ref_type"] == "publication"
    assert tr["ref_id"] == 42
    assert tr["account_id"] == account.id
    assert tr["state"] == "PHONE_STARTING"
    assert tr["attempt"] == 1
    assert tr["finished_at"] is None
    # planned_at, started_at, deadline_at are populated; finished_at is not.
    assert tr["planned_at"] is not None
    assert tr["started_at"] is not None
    assert tr["deadline_at"] is not None


def test_start_session_kind_without_owner_or_ref_raises(db, account):
    with pytest.raises(ValueError):
        runtime.start_session(
            db, account_id=account.id, env_id="ph-1",
            source="publish",
            budget_seconds=900, created_by="publish",
            kind="publish",
            # no owner_*, no ref_*
        )


def test_attach_geelark_task_transitions_to_submitted(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="warmup", owner_type="account", owner_id=account.id,
        budget_seconds=600, created_by="warmup",
        kind="warmup",
    )
    runtime.attach_geelark_task(db, sid, "geelark-task-xyz")
    tr = tr_repo.get_by_phone_session(db, sid)
    assert tr["state"] == "SUBMITTED_TO_GEELARK"
    assert tr["geelark_task_id"] == "geelark-task-xyz"


def test_finalize_success_marks_task_run_success(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", owner_type="publication", owner_id=7,
        budget_seconds=600, created_by="publish",
        kind="publish",
    )
    runtime.attach_geelark_task(db, sid, "tid")
    runtime.finalize_success(db, sid, observed_status="3")

    tr = tr_repo.get_by_phone_session(db, sid)
    assert tr["state"] == "SUCCESS"
    assert tr["finished_at"] is not None
    assert tr["failure_reason"] is None


def test_finalize_failure_records_reason_in_task_run(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="engagement", owner_type="engagement_run", owner_id=11,
        budget_seconds=600, created_by="engagement",
        kind="engagement",
    )
    runtime.finalize_failure(db, sid, reason="GeeLark task not found",
                              observed_status="4")
    tr = tr_repo.get_by_phone_session(db, sid)
    assert tr["state"] == "FAILURE"
    assert tr["failure_reason"] == "GeeLark task not found"
    assert tr["finished_at"] is not None


def test_mark_timeout_marks_task_run_timeout(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", owner_type="publication", owner_id=99,
        budget_seconds=60, created_by="publish",
        kind="publish",
    )
    runtime.mark_timeout(db, sid, reason="budget exceeded",
                          observed_status="2")
    tr = tr_repo.get_by_phone_session(db, sid)
    assert tr["state"] == "TIMEOUT"
    assert tr["failure_reason"] == "budget exceeded"
    assert tr["finished_at"] is not None


def test_finalize_is_idempotent_for_task_run_too(db, account):
    """A second finalize call must not flip task_run state back to a
    different terminal value."""
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="warmup", owner_type="account", owner_id=account.id,
        budget_seconds=600, created_by="warmup",
        kind="warmup",
    )
    runtime.finalize_success(db, sid)
    tr_after_success = tr_repo.get_by_phone_session(db, sid)
    runtime.finalize_failure(db, sid, reason="ignored second close")
    tr_after_failure = tr_repo.get_by_phone_session(db, sid)
    assert tr_after_failure["state"] == "SUCCESS"
    assert tr_after_failure["finished_at"] == tr_after_success["finished_at"]


def test_get_by_geelark_task_resolves_task_run(db, account):
    sid = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", owner_type="publication", owner_id=55,
        budget_seconds=600, created_by="publish",
        kind="publish",
    )
    runtime.attach_geelark_task(db, sid, "gtid-55")
    tr = tr_repo.get_by_geelark_task(db, "gtid-55")
    assert tr is not None
    assert tr["ref_id"] == 55
    assert tr["kind"] == "publish"


def test_list_for_ref_returns_all_attempts(db, account):
    """Future retries will allocate one task_run per attempt — verify
    list_for_ref returns them newest-first."""
    sid1 = runtime.start_session(
        db, account_id=account.id, env_id="ph-1",
        source="publish", owner_type="publication", owner_id=77,
        budget_seconds=600, created_by="publish",
        kind="publish",
    )
    runtime.finalize_failure(db, sid1, reason="attempt 1 failed")
    # Second attempt against the same publication.
    tr_repo.create(
        db,
        kind="publish", ref_type="publication", ref_id=77,
        account_id=account.id,
        state="PHONE_STARTING",
        attempt=2,
        planned_at=datetime.utcnow(),
    )
    db.commit()
    runs = tr_repo.list_for_ref(db, "publication", 77)
    assert len(runs) == 2
    assert runs[0]["attempt"] == 2
    assert runs[1]["attempt"] == 1
