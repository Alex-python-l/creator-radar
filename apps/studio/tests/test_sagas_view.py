"""Tests for the saga read-side view.

The view layer is a thin shaping over task_runs. Pin:
  - In-flight sagas surface, terminal sagas don't.
  - Time math (elapsed, budget_remaining) is correct.
  - Stuck detection lights up only when the deadline has passed AND
    the saga has not finished.
  - Endpoints under /api/ops/sagas/* return the same data shape.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.services import sagas as saga_views
from app.services.phone_runtime import (
    TASK_STATE_PHONE_STARTING,
    TASK_STATE_SUCCESS,
    TASK_STATE_SUBMITTED,
)


@pytest.fixture
def authed_client(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    return client


def _seed_task_run(
        db, *,
        kind: str,
        ref_type: str,
        ref_id: int,
        account_id: int,
        state: str,
        planned_at: datetime,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        deadline_at: datetime | None = None,
        geelark_task_id: str | None = None,
        attempt: int = 1,
        failure_reason: str | None = None,
) -> int:
    row = db.execute(
        text(
            "INSERT INTO task_runs ("
            "kind, ref_type, ref_id, account_id, state, attempt, "
            "planned_at, started_at, finished_at, deadline_at, "
            "geelark_task_id, failure_reason) "
            "VALUES (:kind, :rt, :ri, :aid, :st, :att, :p, :s, :f, "
            ":d, :gt, :fr) RETURNING id"
        ),
        {
            "kind": kind, "rt": ref_type, "ri": ref_id, "aid": account_id,
            "st": state, "att": attempt, "p": planned_at, "s": started_at,
            "f": finished_at, "d": deadline_at, "gt": geelark_task_id,
            "fr": failure_reason,
        },
    ).first()
    db.commit()
    return row[0]


@pytest.fixture
def seeded_account_id(db):
    from app.repositories import accounts as acc_repo
    return acc_repo.create(db, {
        "profile_name": "saga-test-acc",
        "geelark_phone_id": "ph-saga-test",
    })


# ---------------------------------------------------------------------------
# list_in_flight_sagas
# ---------------------------------------------------------------------------
def test_in_flight_includes_unfinished_excludes_terminal(
        db, seeded_account_id):
    now = datetime.utcnow()
    in_flight = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=1,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now - timedelta(minutes=1),
        started_at=now - timedelta(minutes=1),
        deadline_at=now + timedelta(minutes=14),
    )
    finished = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=2,
        account_id=seeded_account_id, state=TASK_STATE_SUCCESS,
        planned_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=10),
        finished_at=now - timedelta(minutes=5),
        deadline_at=now - timedelta(minutes=4),
    )

    views = saga_views.list_in_flight_sagas(db, now=now)
    ids = [v.saga_id for v in views]
    assert in_flight in ids
    assert finished not in ids


def test_in_flight_filters_by_kind(db, seeded_account_id):
    now = datetime.utcnow()
    pub_id = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=10,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=15),
    )
    warm_id = _seed_task_run(
        db, kind="warmup", ref_type="account", ref_id=seeded_account_id,
        account_id=seeded_account_id,
        state=TASK_STATE_PHONE_STARTING,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )

    pubs = saga_views.list_in_flight_sagas(db, kind="publish", now=now)
    warmups = saga_views.list_in_flight_sagas(db, kind="warmup", now=now)
    assert [v.saga_id for v in pubs] == [pub_id]
    assert [v.saga_id for v in warmups] == [warm_id]


def test_in_flight_sorts_by_deadline_then_nulls_last(db, seeded_account_id):
    """The dashboard wants the closest-to-stuck first. Rows without a
    deadline (manual holds) sit at the bottom."""
    now = datetime.utcnow()
    late = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=100,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=20),
    )
    soon = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=101,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=2),
    )
    no_dl = _seed_task_run(
        db, kind="warmup", ref_type="account", ref_id=seeded_account_id,
        account_id=seeded_account_id, state=TASK_STATE_PHONE_STARTING,
        planned_at=now, started_at=now,
        deadline_at=None,
    )

    views = saga_views.list_in_flight_sagas(db, now=now)
    ids = [v.saga_id for v in views]
    assert ids.index(soon) < ids.index(late) < ids.index(no_dl)


# ---------------------------------------------------------------------------
# Time math + stuck detection
# ---------------------------------------------------------------------------
def test_elapsed_and_budget_remaining_use_provided_now(
        db, seeded_account_id):
    started = datetime(2026, 6, 19, 12, 0, 0)
    deadline = datetime(2026, 6, 19, 12, 15, 0)
    sid = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=200,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=started, started_at=started, deadline_at=deadline,
    )
    now = datetime(2026, 6, 19, 12, 5, 0)
    views = saga_views.list_in_flight_sagas(db, now=now)
    view = next(v for v in views if v.saga_id == sid)
    assert view.elapsed_seconds == 5 * 60
    assert view.budget_remaining_seconds == 10 * 60
    assert view.is_stuck is False


def test_stuck_when_deadline_passed_but_not_finished(
        db, seeded_account_id):
    started = datetime(2026, 6, 19, 12, 0, 0)
    deadline = datetime(2026, 6, 19, 12, 5, 0)
    sid = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=300,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=started, started_at=started, deadline_at=deadline,
    )
    now = datetime(2026, 6, 19, 12, 7, 0)
    views = saga_views.list_in_flight_sagas(db, now=now)
    view = next(v for v in views if v.saga_id == sid)
    assert view.is_stuck is True
    assert view.budget_remaining_seconds == -2 * 60


def test_no_deadline_means_no_budget_and_not_stuck(
        db, seeded_account_id):
    now = datetime.utcnow()
    sid = _seed_task_run(
        db, kind="warmup", ref_type="account", ref_id=seeded_account_id,
        account_id=seeded_account_id, state=TASK_STATE_PHONE_STARTING,
        planned_at=now, started_at=now,
        deadline_at=None,
    )
    views = saga_views.list_in_flight_sagas(db, now=now)
    view = next(v for v in views if v.saga_id == sid)
    assert view.budget_remaining_seconds is None
    assert view.is_stuck is False


# ---------------------------------------------------------------------------
# describe_saga
# ---------------------------------------------------------------------------
def test_describe_saga_returns_latest_attempt(db, seeded_account_id):
    now = datetime.utcnow()
    older = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=999,
        account_id=seeded_account_id, state=TASK_STATE_SUCCESS,
        planned_at=now - timedelta(hours=1),
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=58),
        deadline_at=now - timedelta(minutes=45),
        attempt=1,
    )
    newer = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=999,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=15),
        attempt=2,
    )
    view = saga_views.describe_saga(
        db, kind="publish", business_ref="publication:999",
    )
    assert view is not None
    assert view.saga_id == newer
    assert view.attempt == 2
    assert older != newer  # sanity


def test_describe_saga_missing_returns_none(db):
    view = saga_views.describe_saga(
        db, kind="publish", business_ref="publication:404",
    )
    assert view is None


def test_describe_saga_bad_business_ref_returns_none(db):
    view = saga_views.describe_saga(
        db, kind="publish", business_ref="not-a-valid-shape",
    )
    assert view is None


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------
def test_count_in_flight_by_kind(db, seeded_account_id):
    now = datetime.utcnow()
    for _ in range(2):
        _seed_task_run(
            db, kind="publish", ref_type="publication",
            ref_id=int(now.timestamp() * 1000) % 1000000,
            account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
            planned_at=now, started_at=now,
            deadline_at=now + timedelta(minutes=10),
        )
    _seed_task_run(
        db, kind="warmup", ref_type="account",
        ref_id=seeded_account_id, account_id=seeded_account_id,
        state=TASK_STATE_PHONE_STARTING, planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=8),
    )
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=99999,
        account_id=seeded_account_id, state=TASK_STATE_SUCCESS,
        planned_at=now - timedelta(minutes=30),
        started_at=now - timedelta(minutes=30),
        finished_at=now - timedelta(minutes=25),
    )

    counts = saga_views.count_in_flight_by_kind(db)
    assert counts.get("publish") == 2
    assert counts.get("warmup") == 1


def test_count_stuck(db, seeded_account_id):
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=11,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now - timedelta(minutes=30),
        started_at=now - timedelta(minutes=30),
        deadline_at=now - timedelta(minutes=5),  # past deadline
    )
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=12,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),  # healthy
    )
    assert saga_views.count_stuck(db, now=now) == 1


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def test_in_flight_endpoint_returns_jsonable(authed_client, db,
                                              seeded_account_id):
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=501,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),
        geelark_task_id="gt-501",
    )
    r = authed_client.get("/api/ops/sagas/in-flight")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["business_ref"] == "publication:501" for it in items)
    item = next(it for it in items
                if it["business_ref"] == "publication:501")
    assert item["geelark_task_id"] == "gt-501"
    assert item["current_step"] == TASK_STATE_SUBMITTED
    assert item["planned_at"].endswith("Z")


def test_summary_endpoint(authed_client, db, seeded_account_id):
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="warmup", ref_type="account",
        ref_id=seeded_account_id, account_id=seeded_account_id,
        state=TASK_STATE_PHONE_STARTING, planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=8),
    )
    r = authed_client.get("/api/ops/sagas/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["in_flight_by_kind"].get("warmup") == 1
    assert body["stuck"] == 0


def test_describe_endpoint_404_on_missing(authed_client):
    r = authed_client.get("/api/ops/sagas/publish/publication:99999")
    assert r.status_code == 404


def test_describe_endpoint_returns_view(authed_client, db,
                                         seeded_account_id):
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=600,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=12),
    )
    r = authed_client.get("/api/ops/sagas/publish/publication:600")
    assert r.status_code == 200
    body = r.json()
    assert body["business_ref"] == "publication:600"
    assert body["kind"] == "publish"
    assert body["current_step"] == TASK_STATE_SUBMITTED


# ---------------------------------------------------------------------------
# PublishSaga driver class
# ---------------------------------------------------------------------------
def test_publish_saga_step_names_match_phone_runtime_constants():
    """Phase 7b documents the step sequence on the PublishSaga class.
    The names must match the strings phone_runtime / posting_service
    actually persist into task_runs.state — otherwise the ops view
    can't render them and the saga descriptor lies about reality."""
    from app.services.sagas import PublishSaga
    from app.services import phone_runtime
    names = set(PublishSaga.step_names())
    # Every phone_runtime TASK_STATE_* string must show up in the
    # publish saga descriptor.
    assert "PHONE_STARTING" in names
    assert phone_runtime.TASK_STATE_UPLOADING in names
    assert phone_runtime.TASK_STATE_SUBMITTED in names
    assert phone_runtime.TASK_STATE_VERIFYING in names
    assert phone_runtime.TASK_STATE_SUCCESS in names
    assert phone_runtime.TASK_STATE_FAILURE in names
    assert phone_runtime.TASK_STATE_TIMEOUT in names


def test_publish_saga_current_step_returns_latest_state(
        db, seeded_account_id):
    from app.services.sagas import PublishSaga
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=701,
        account_id=seeded_account_id, state=TASK_STATE_SUBMITTED,
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )
    saga = PublishSaga(701)
    assert saga.current_step(db) == TASK_STATE_SUBMITTED
    assert saga.business_ref == "publication:701"


def test_publish_saga_current_step_returns_none_when_no_attempt(db):
    from app.services.sagas import PublishSaga
    assert PublishSaga(99999).current_step(db) is None


def test_publish_saga_view_carries_full_metadata(db, seeded_account_id):
    from app.services.sagas import PublishSaga
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=702,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=15),
        geelark_task_id="gt-702",
    )
    view = PublishSaga(702).view(db)
    assert view is not None
    assert view.kind == "publish"
    assert view.business_ref == "publication:702"
    assert view.current_step == "UPLOADING_RESOURCE"
    assert view.geelark_task_id == "gt-702"
    assert view.is_stuck is False


def test_advance_task_run_state_walks_saga_through_steps(
        db, seeded_account_id):
    """Wire-level test: phone_runtime.advance_task_run_state should
    surface on PublishSaga.current_step within the same DB session."""
    from app.services import phone_runtime
    from app.services.sagas import PublishSaga

    # Create a phone_session + task_run via the runtime (the supported
    # public path).
    sid = phone_runtime.start_session(
        db,
        account_id=seeded_account_id,
        env_id="ph-saga-test",
        source="publish",
        owner_type="publication",
        owner_id=903,
        budget_seconds=900,
        created_by="test",
        kind="publish",
    )
    saga = PublishSaga(903)
    db.expire_all()
    assert saga.current_step(db) == "PHONE_STARTING"

    phone_runtime.advance_task_run_state(
        db, sid, phone_runtime.TASK_STATE_UPLOADING)
    db.expire_all()
    assert saga.current_step(db) == "UPLOADING_RESOURCE"

    phone_runtime.advance_task_run_state(
        db, sid, phone_runtime.TASK_STATE_VERIFYING)
    db.expire_all()
    assert saga.current_step(db) == "VERIFYING"

    phone_runtime.finalize_success(db, sid)
    db.expire_all()
    assert saga.current_step(db) == "SUCCESS"


def test_advance_task_run_state_noop_when_no_task_run(
        db, seeded_account_id):
    """A session created without `kind` skips task_run creation (manual
    holds + external_detected). advance_task_run_state must not raise."""
    from app.services import phone_runtime
    sid = phone_runtime.start_session(
        db,
        account_id=seeded_account_id,
        env_id="ph-saga-test",
        source="manual",
        owner_type="account",
        owner_id=seeded_account_id,
        budget_seconds=900,
        created_by="operator",
    )
    # No `kind` was passed → no task_run row exists. advance is a no-op.
    phone_runtime.advance_task_run_state(
        db, sid, phone_runtime.TASK_STATE_UPLOADING)


# ---------------------------------------------------------------------------
# WarmupSaga + EngagementSaga descriptors
# ---------------------------------------------------------------------------
def test_all_three_sagas_registered():
    from app.services.sagas import all_saga_descriptors
    descriptors = all_saga_descriptors()
    assert set(descriptors.keys()) == {"publish", "warmup", "engagement"}


def test_warmup_saga_step_vocabulary():
    """WarmupSaga has no UPLOADING_RESOURCE (nothing to upload) and no
    VERIFYING (GeeLark status is trusted). It's deliberately shorter
    than publish."""
    from app.services.sagas import WarmupSaga
    from app.services import phone_runtime
    names = set(WarmupSaga.step_names())
    assert "PHONE_STARTING" in names
    assert phone_runtime.TASK_STATE_SUBMITTED in names
    assert phone_runtime.TASK_STATE_SUCCESS in names
    assert phone_runtime.TASK_STATE_FAILURE in names
    assert phone_runtime.TASK_STATE_TIMEOUT in names
    # Steps that should NOT be on the warmup saga:
    assert "UPLOADING_RESOURCE" not in names
    assert "VERIFYING" not in names
    assert "PREPARING_COMMENT" not in names


def test_engagement_saga_step_vocabulary():
    """EngagementSaga adds PREPARING_COMMENT (Gemini fires here) and
    keeps VERIFYING (post-terminal screenshot fetch). No
    UPLOADING_RESOURCE."""
    from app.services.sagas import EngagementSaga
    from app.services import phone_runtime
    names = set(EngagementSaga.step_names())
    assert "PHONE_STARTING" in names
    assert phone_runtime.TASK_STATE_PREPARING_COMMENT in names
    assert phone_runtime.TASK_STATE_SUBMITTED in names
    assert phone_runtime.TASK_STATE_VERIFYING in names
    assert phone_runtime.TASK_STATE_SUCCESS in names
    assert phone_runtime.TASK_STATE_FAILURE in names
    assert phone_runtime.TASK_STATE_TIMEOUT in names
    # Step that should NOT be on the engagement saga:
    assert "UPLOADING_RESOURCE" not in names


def test_warmup_saga_business_ref_and_current_step(db, seeded_account_id):
    from app.services.sagas import WarmupSaga
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="warmup", ref_type="account",
        ref_id=seeded_account_id, account_id=seeded_account_id,
        state=TASK_STATE_SUBMITTED, planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )
    saga = WarmupSaga(seeded_account_id)
    assert saga.business_ref == f"account:{seeded_account_id}"
    assert saga.current_step(db) == TASK_STATE_SUBMITTED


def test_engagement_saga_business_ref_and_current_step(
        db, seeded_account_id):
    from app.services.sagas import EngagementSaga
    now = datetime.utcnow()
    _seed_task_run(
        db, kind="engagement", ref_type="engagement_run", ref_id=123,
        account_id=seeded_account_id, state="PREPARING_COMMENT",
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )
    saga = EngagementSaga(123)
    assert saga.business_ref == "engagement_run:123"
    assert saga.current_step(db) == "PREPARING_COMMENT"


def test_warmup_saga_view_through_phone_runtime_advance(
        db, seeded_account_id):
    """End-to-end: PhoneRuntime drives the saga through PHONE_STARTING
    → SUBMITTED_TO_GEELARK → SUCCESS via the public API."""
    from app.services import phone_runtime
    from app.services.sagas import WarmupSaga

    sid = phone_runtime.start_session(
        db,
        account_id=seeded_account_id,
        env_id="ph-saga-test",
        source="warmup",
        owner_type="account",
        owner_id=seeded_account_id,
        budget_seconds=900,
        created_by="test",
        kind="warmup",
    )
    saga = WarmupSaga(seeded_account_id)
    db.expire_all()
    assert saga.current_step(db) == "PHONE_STARTING"

    phone_runtime.attach_geelark_task(db, sid, "gt-warm-1")
    db.expire_all()
    assert saga.current_step(db) == "SUBMITTED_TO_GEELARK"

    phone_runtime.finalize_success(db, sid)
    db.expire_all()
    assert saga.current_step(db) == "SUCCESS"


def test_engagement_saga_walks_through_preparing_and_verifying(
        db, seeded_account_id):
    from app.services import phone_runtime
    from app.services.sagas import EngagementSaga

    sid = phone_runtime.start_session(
        db,
        account_id=seeded_account_id,
        env_id="ph-saga-test",
        source="engagement",
        owner_type="engagement_run",
        owner_id=4242,
        budget_seconds=900,
        created_by="test",
        kind="engagement",
    )
    saga = EngagementSaga(4242)
    db.expire_all()
    assert saga.current_step(db) == "PHONE_STARTING"

    phone_runtime.advance_task_run_state(
        db, sid, phone_runtime.TASK_STATE_PREPARING_COMMENT)
    db.expire_all()
    assert saga.current_step(db) == "PREPARING_COMMENT"

    phone_runtime.attach_geelark_task(db, sid, "gt-eng-42")
    db.expire_all()
    assert saga.current_step(db) == "SUBMITTED_TO_GEELARK"

    phone_runtime.advance_task_run_state(
        db, sid, phone_runtime.TASK_STATE_VERIFYING)
    db.expire_all()
    assert saga.current_step(db) == "VERIFYING"

    phone_runtime.finalize_success(db, sid)
    db.expire_all()
    assert saga.current_step(db) == "SUCCESS"


# ---------------------------------------------------------------------------
# Step-level stuck detection (Phase 7e)
# ---------------------------------------------------------------------------
def test_step_budget_table_covers_every_non_terminal_state():
    """Every step name that's not in _TERMINAL_STATES must have an
    entry in STEP_BUDGETS_SECONDS — otherwise a saga sitting at that
    step would never count as step_stuck."""
    from app.services.sagas import (
        STEP_BUDGETS_SECONDS, _TERMINAL_STATES, PublishSaga,
        WarmupSaga, EngagementSaga,
    )
    every_step = (
        set(PublishSaga.step_names())
        | set(WarmupSaga.step_names())
        | set(EngagementSaga.step_names())
    )
    actionable = every_step - _TERMINAL_STATES - {"PLANNED"}
    missing = actionable - STEP_BUDGETS_SECONDS.keys()
    assert not missing, f"missing budgets: {missing}"


def test_seconds_at_current_step_uses_task_run_updated_at(
        db, seeded_account_id):
    """The "how long has the saga been at this step" reading comes off
    task_runs.updated_at — which phone_runtime bumps on every state
    write. Backdating updated_at by hand should be reflected verbatim."""
    now = datetime.utcnow()
    sid = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=800,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=10),
        deadline_at=now + timedelta(minutes=10),
    )
    db.execute(
        text("UPDATE task_runs SET updated_at = :u WHERE id = :id"),
        {"u": now - timedelta(seconds=400), "id": sid},
    )
    db.commit()
    views = saga_views.list_in_flight_sagas(db, now=now)
    view = next(v for v in views if v.saga_id == sid)
    # Backdated 400s; budget is 300s for UPLOADING_RESOURCE.
    assert view.seconds_at_current_step >= 395
    assert view.seconds_at_current_step <= 410
    assert view.step_budget_seconds == 300
    assert view.is_step_stuck is True
    # Lease deadline hasn't passed — is_stuck (lease) stays False.
    assert view.is_stuck is False


def test_is_step_stuck_only_lights_when_past_budget(
        db, seeded_account_id):
    now = datetime.utcnow()
    fresh = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=801,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )
    # Backdate to JUST under the 300s budget.
    db.execute(
        text("UPDATE task_runs SET updated_at = :u WHERE id = :id"),
        {"u": now - timedelta(seconds=250), "id": fresh},
    )
    db.commit()
    views = saga_views.list_in_flight_sagas(db, now=now)
    view = next(v for v in views if v.saga_id == fresh)
    assert view.is_step_stuck is False
    assert view.seconds_at_current_step == 250


def test_terminal_saga_never_step_stuck(db, seeded_account_id):
    """A SUCCESS / FAILURE / TIMEOUT row is by definition not stuck —
    even if it sat at the terminal state for hours."""
    now = datetime.utcnow()
    sid = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=802,
        account_id=seeded_account_id, state=TASK_STATE_SUCCESS,
        planned_at=now - timedelta(hours=2),
        started_at=now - timedelta(hours=2),
        finished_at=now - timedelta(hours=1),
        deadline_at=now - timedelta(minutes=58),
    )
    # SUCCESS rows are excluded from list_in_flight_sagas; build view
    # directly via describe_saga so we can still assert the flag.
    view = saga_views.describe_saga(
        db, kind="publish", business_ref="publication:802", now=now,
    )
    assert view is not None
    assert view.is_step_stuck is False
    assert view.is_terminal is True


def test_count_step_stuck_separates_from_lease_stuck(
        db, seeded_account_id):
    """Two stuck signals are distinct: step stuck (current step over
    its budget) vs lease stuck (overall deadline_at passed)."""
    now = datetime.utcnow()

    step_only = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=810,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now - timedelta(minutes=20),
        started_at=now - timedelta(minutes=20),
        deadline_at=now + timedelta(minutes=30),  # lease healthy
    )
    db.execute(
        text("UPDATE task_runs SET updated_at = :u WHERE id = :id"),
        {"u": now - timedelta(minutes=10), "id": step_only},
    )
    db.commit()

    lease_only = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=811,
        account_id=seeded_account_id, state="SUBMITTED_TO_GEELARK",
        planned_at=now - timedelta(minutes=30),
        started_at=now - timedelta(minutes=30),
        deadline_at=now - timedelta(minutes=5),  # lease expired
    )
    # Set updated_at to recent so it's NOT step-stuck.
    db.execute(
        text("UPDATE task_runs SET updated_at = :u WHERE id = :id"),
        {"u": now - timedelta(seconds=120), "id": lease_only},
    )
    db.commit()

    assert saga_views.count_stuck(db, now=now) == 1  # lease_only
    assert saga_views.count_step_stuck(db, now=now) == 1  # step_only


def test_summary_endpoint_includes_step_stuck(authed_client, db,
                                                seeded_account_id):
    now = datetime.utcnow()
    sid = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=820,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now - timedelta(minutes=15),
        started_at=now - timedelta(minutes=15),
        deadline_at=now + timedelta(minutes=10),
    )
    db.execute(
        text("UPDATE task_runs SET updated_at = :u WHERE id = :id"),
        {"u": now - timedelta(minutes=15), "id": sid},
    )
    db.commit()
    r = authed_client.get("/api/ops/sagas/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["step_stuck"] >= 1
    # in_flight_by_kind still works.
    assert body["in_flight_by_kind"].get("publish", 0) >= 1


def test_in_flight_endpoint_surfaces_step_stuck_field(
        authed_client, db, seeded_account_id):
    now = datetime.utcnow()
    sid = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=821,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now - timedelta(minutes=15),
        started_at=now - timedelta(minutes=15),
        deadline_at=now + timedelta(minutes=10),
    )
    db.execute(
        text("UPDATE task_runs SET updated_at = :u WHERE id = :id"),
        {"u": now - timedelta(minutes=15), "id": sid},
    )
    db.commit()
    r = authed_client.get("/api/ops/sagas/in-flight")
    body = r.json()
    item = next(it for it in body["items"]
                if it["business_ref"] == "publication:821")
    assert item["is_step_stuck"] is True
    assert item["step_budget_seconds"] == 300
    assert item["seconds_at_current_step"] >= 800


# ---------------------------------------------------------------------------
# saga_stuck_logger periodic task
# ---------------------------------------------------------------------------
def test_saga_stuck_logger_warns_for_step_stuck_rows(
        procrastinate_app, db, seeded_account_id, caplog):
    """The periodic logger emits one WARNING per stuck saga so it shows
    up in journalctl / Loki for the operator to grep."""
    import asyncio as _asyncio
    import logging
    from app.tasks.saga_stuck_logger import saga_stuck_logger_task

    now = datetime.utcnow()
    sid = _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=900,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now - timedelta(minutes=20),
        started_at=now - timedelta(minutes=20),
        deadline_at=now + timedelta(minutes=30),  # lease healthy
    )
    db.execute(
        text("UPDATE task_runs SET updated_at = :u WHERE id = :id"),
        {"u": now - timedelta(minutes=15), "id": sid},
    )
    db.commit()

    with caplog.at_level(logging.WARNING):
        out = _asyncio.run(saga_stuck_logger_task(timestamp=0))
    assert "warned=1" in out
    warn_messages = [
        rec.getMessage() for rec in caplog.records
        if rec.levelno >= logging.WARNING
    ]
    assert any("saga_step_stuck" in m and "publication:900" in m
               for m in warn_messages)


def test_saga_stuck_logger_skips_healthy_rows(
        procrastinate_app, db, seeded_account_id, caplog):
    import asyncio as _asyncio
    import logging
    from app.tasks.saga_stuck_logger import saga_stuck_logger_task

    now = datetime.utcnow()
    _seed_task_run(
        db, kind="publish", ref_type="publication", ref_id=910,
        account_id=seeded_account_id, state="UPLOADING_RESOURCE",
        planned_at=now, started_at=now,
        deadline_at=now + timedelta(minutes=10),
    )
    db.commit()

    with caplog.at_level(logging.WARNING):
        out = _asyncio.run(saga_stuck_logger_task(timestamp=0))
    assert "warned=0" in out
