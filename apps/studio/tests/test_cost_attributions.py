"""Phase 3 — cost_attributions persisted ledger.

ensure_attributions runs the existing attribution CTE filtered to a
list of transaction ids and upserts a row into cost_attributions per
result. phone_session matches win over the legacy posted_at /
last_checked_at / publication_attempts / engagement_runs.finished_at
heuristics via the priority column.
"""
import calendar
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models import Account, Publication, Video
from app.repositories import cost_attributions as ca_repo
from app.repositories import transactions as tx_repo
from app.services import phone_runtime as runtime


def _txn(*, txn_id, env_id, created_at_epoch, used_min=7, amount=-0.049):
    return {
        "id": txn_id, "envId": env_id, "amount": amount,
        "usedTime": used_min, "type": 2, "chargeType": 2,
        "createdTime": created_at_epoch,
    }


def _epoch(dt):
    return calendar.timegm(dt.timetuple())


@pytest.fixture
def account_with_phone(db):
    a = Account(profile_name="cost-attr", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_ensure_attributions_writes_unattributed_for_no_match(
        db, account_with_phone):
    """A transaction with no phone_session, no publish, no warmup,
    no engagement should land in cost_attributions as
    kind='unattributed' with confidence='low'."""
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    tx_repo.upsert_many(
        db, [_txn(txn_id="tx-idle", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    written = tx_repo.ensure_attributions(db, ["tx-idle"])
    assert written == 1

    row = ca_repo.get(db, "tx-idle")
    assert row is not None
    assert row["kind"] == "unattributed"
    assert row["confidence"] == "low"
    assert "no phone_session" in row["reason"]
    assert row["phone_session_id"] is None
    assert row["task_run_id"] is None


def test_phone_session_wins_over_legacy_publish_heuristic(
        db, account_with_phone):
    """When BOTH a phone_session AND a publication.posted_at match the
    tx window, attribution must use the phone_session (Phase 3
    priority=0). The persisted row carries phone_session_id (and
    task_run_id when kind was set)."""
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)

    t0 = datetime(2026, 6, 1, 12, 0, 0)
    # phone_session covers the billing window.
    sid = runtime.start_session(
        db, account_id=a.id, env_id="ph-1",
        source="publish", owner_type="publication", owner_id=999,
        budget_seconds=600, created_by="test",
        kind="publish",
    )
    # Backdate started_at into the tx window.
    db.execute(
        text("UPDATE phone_sessions SET started_at = :ts WHERE id = :id"),
        {"ts": t0 - timedelta(minutes=3), "id": sid},
    )
    db.commit()

    # ALSO add a publication whose posted_at lands in the window.
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS", posted_at=t0 - timedelta(minutes=2),
    )
    db.add(p); db.commit(); db.refresh(p)

    tx_repo.upsert_many(
        db, [_txn(txn_id="tx-overlap", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    tx_repo.ensure_attributions(db, ["tx-overlap"])

    row = ca_repo.get(db, "tx-overlap")
    assert row["kind"] == "publish"
    assert row["confidence"] == "high"
    assert row["phone_session_id"] == sid
    # task_run_id is set because we created the session with kind=
    assert row["task_run_id"] is not None
    assert "phone_session #" in row["reason"]


def test_ensure_attributions_is_idempotent_upsert(db, account_with_phone):
    """Re-running ensure_attributions on the same ids must overwrite
    the existing row, not error. updated_at advances."""
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    tx_repo.upsert_many(
        db, [_txn(txn_id="tx-rerun", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    tx_repo.ensure_attributions(db, ["tx-rerun"])
    first = ca_repo.get(db, "tx-rerun")
    tx_repo.ensure_attributions(db, ["tx-rerun"])
    second = ca_repo.get(db, "tx-rerun")
    assert second["transaction_id"] == first["transaction_id"]
    assert second["created_at"] == first["created_at"]  # PK unchanged


def test_ensure_attributions_empty_list_is_noop(db):
    assert tx_repo.ensure_attributions(db, []) == 0


def test_ensure_attributions_persists_warmup_session(db, account_with_phone):
    """A warmup phone_session anchored in the tx window should land in
    cost_attributions with kind='warmup'."""
    a = account_with_phone
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    sid = runtime.start_session(
        db, account_id=a.id, env_id="ph-1",
        source="warmup", owner_type="account", owner_id=a.id,
        budget_seconds=600, created_by="warmup",
        kind="warmup",
    )
    db.execute(
        text("UPDATE phone_sessions SET started_at = :ts WHERE id = :id"),
        {"ts": t0 - timedelta(minutes=3), "id": sid},
    )
    db.commit()

    tx_repo.upsert_many(
        db, [_txn(txn_id="tx-warm", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    tx_repo.ensure_attributions(db, ["tx-warm"])

    row = ca_repo.get(db, "tx-warm")
    assert row["kind"] == "warmup"
    assert row["phone_session_id"] == sid


def test_summary_by_kind_aggregates_from_persisted_rows(
        db, account_with_phone):
    """summary_by_kind groups + sums amounts straight from the
    persisted ledger (used by the Costs overview pie chart)."""
    a = account_with_phone
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    sid = runtime.start_session(
        db, account_id=a.id, env_id="ph-1",
        source="publish", owner_type="publication", owner_id=1,
        budget_seconds=600, created_by="test",
        kind="publish",
    )
    # Anchor the session to a 10-minute window around the FIRST tx so
    # only that one matches; close it before the orphan tx fires.
    db.execute(
        text(
            "UPDATE phone_sessions SET started_at = :ts, stopped_at = :st "
            "WHERE id = :id"
        ),
        {
            "ts": t0 - timedelta(minutes=3),
            "st": t0 + timedelta(minutes=1),
            "id": sid,
        },
    )
    db.commit()
    tx_repo.upsert_many(db, [
        _txn(txn_id="tx-a", env_id="ph-1", created_at_epoch=_epoch(t0),
             amount=-0.10),
        _txn(txn_id="tx-b", env_id="ph-1",
             created_at_epoch=_epoch(t0 + timedelta(hours=3)),
             amount=-0.05),
    ])
    tx_repo.ensure_attributions(db, ["tx-a", "tx-b"])

    rows = ca_repo.summary_by_kind(db)
    keyed = {r["key"]: r for r in rows}
    assert pytest.approx(keyed["publish"]["total"], abs=1e-4) == -0.10
    assert pytest.approx(keyed["unattributed"]["total"], abs=1e-4) == -0.05
