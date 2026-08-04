"""Phase 1 Step 4: the overdue-session watchdog times out in-flight
phone_sessions whose budget_deadline_at has passed and best-effort
stops the cloud phone.
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.repositories import accounts as acc_repo
from app.repositories import phone_sessions as ps_repo
from app.services import phone_runtime


class FakeWatchdogClient:
    def __init__(self, *, fail_stop=False, running_set=None):
        self.stopped: list[str] = []
        self.fail_stop = fail_stop
        self.running_set = set(running_set or [])

    def stop_phones(self, ids):
        self.stopped.extend(ids)
        if self.fail_stop:
            raise RuntimeError("simulated stop_phones failure")
        self.running_set.difference_update(ids)

    def get_phone_status(self, ids):
        # 0 = Started (running), 2 = Shut down.
        return {
            "successDetails": [
                {"id": pid, "status": 0 if pid in self.running_set else 2}
                for pid in ids
            ]
        }


def _account_with_phone(db, *, profile: str, env_id: str) -> int:
    return acc_repo.create(db, {
        "profile_name": profile,
        "geelark_phone_id": env_id,
        "tiktok_logged_in": 1,
        "warmup_mode": "daily",
    })


def _backdate_session(db, session_id: int, *, minutes_overdue: int = 5):
    """Push budget_deadline_at into the past so list_overdue picks it up."""
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :past "
            "WHERE id = :id"
        ),
        {"past": datetime.utcnow() - timedelta(minutes=minutes_overdue),
         "id": session_id},
    )
    db.commit()


def test_watchdog_no_overdue_sessions_returns_zero(db, monkeypatch):
    """Empty input → nothing happens, no GeeLark call."""
    from app.services import geelark_service
    fake = FakeWatchdogClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "timed out 0" in out
    assert fake.stopped == []


def test_watchdog_times_out_single_overdue_session(db, monkeypatch):
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="overdue-1", env_id="ph-od-1")
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id="ph-od-1", source="publish",
        owner_type="publication", owner_id=42,
        budget_seconds=600, created_by="test",
    )
    _backdate_session(db, sid, minutes_overdue=10)

    fake = FakeWatchdogClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "timed out 1" in out
    assert fake.stopped == ["ph-od-1"]

    row = ps_repo.get(db, sid)
    assert row["status"] == "TIMEOUT"
    assert row["final_status"] == "TIMEOUT"
    assert row["stopped_at"] is not None
    assert "watchdog" in (row["stop_reason"] or "").lower()


def test_watchdog_continues_when_stop_phones_fails(db, monkeypatch):
    """A stop_phones exception must NOT prevent the session from being
    marked TIMEOUT — accounting integrity beats the cloud-phone side
    effect (which the idle_phone_sweeper will retry anyway)."""
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="stop-fail",
                              env_id="ph-stop-fail")
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id="ph-stop-fail", source="warmup",
        owner_type="account", owner_id=aid,
        budget_seconds=600, created_by="test",
    )
    _backdate_session(db, sid)

    fake = FakeWatchdogClient(fail_stop=True)
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    # The session is still timed out — the watchdog counts it as
    # processed even though stop_phones threw.
    assert "timed out 1" in out
    assert fake.stopped == ["ph-stop-fail"]  # was attempted
    row = ps_repo.get(db, sid)
    assert row["status"] == "TIMEOUT"
    assert row["final_status"] == "TIMEOUT"


def test_watchdog_skips_already_finalized_session(db, monkeypatch):
    """A stopped session must not be touched again. list_overdue's
    WHERE stopped_at IS NULL excludes it; the watchdog should report
    zero processed even if backdating an old budget_deadline_at."""
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="done", env_id="ph-done")
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id="ph-done", source="engagement",
        owner_type="engagement_run", owner_id=99,
        budget_seconds=600, created_by="test",
    )
    phone_runtime.finalize_success(db, sid)
    # Backdate budget_deadline_at after finalize to simulate stale row.
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :past "
            "WHERE id = :id"
        ),
        {"past": datetime.utcnow() - timedelta(hours=1), "id": sid},
    )
    db.commit()

    fake = FakeWatchdogClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "timed out 0" in out
    assert fake.stopped == []


def test_watchdog_processes_multiple_overdue(db, monkeypatch):
    """A backlog gets cleaned in one tick (within batch size). Each
    session's account-level lease must be cleared independently."""
    from app.services import geelark_service
    sids = []
    for i in range(3):
        aid = _account_with_phone(
            db, profile=f"od-{i}", env_id=f"ph-od-{i}")
        sid = phone_runtime.start_session(
            db, account_id=aid, env_id=f"ph-od-{i}", source="publish",
            owner_type="publication", owner_id=i + 1,
            budget_seconds=60, created_by="test",
        )
        _backdate_session(db, sid, minutes_overdue=i + 1)
        sids.append(sid)

    fake = FakeWatchdogClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "timed out 3" in out
    assert set(fake.stopped) == {"ph-od-0", "ph-od-1", "ph-od-2"}
    for sid in sids:
        row = ps_repo.get(db, sid)
        assert row["status"] == "TIMEOUT"
        assert row["final_status"] == "TIMEOUT"


def test_watchdog_processes_manual_source_too(db, monkeypatch):
    """A manual hold whose budget ran out (operator forgot to release)
    still gets timed out — operator can always grab a fresh hold."""
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="manual",
                              env_id="ph-manual")
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id="ph-manual", source="manual",
        owner_type="account", owner_id=aid,
        budget_seconds=60, created_by="ops",
    )
    _backdate_session(db, sid, minutes_overdue=30)

    fake = FakeWatchdogClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    asyncio.run(overdue_session_watchdog_task(timestamp=0))

    row = ps_repo.get(db, sid)
    assert row["status"] == "TIMEOUT"
    assert row["source"] == "manual"


# ---------------------------------------------------------------------------
# Phase 4b: external running-phone stop
# ---------------------------------------------------------------------------

def test_watchdog_stops_unowned_running_phone(
        db, monkeypatch):
    """A GeeLark phone reported as Started with no active local
    phone_session must be stopped immediately. Manual hold is the only
    way an operator can intentionally keep a phone running."""
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="ext-1", env_id="ph-ext-1")

    fake = FakeWatchdogClient(running_set={"ph-ext-1"})
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "stopped 1 external" in out
    assert fake.stopped == ["ph-ext-1"]
    assert phone_runtime.get_active_for_account(db, aid) is None


def test_watchdog_does_not_stop_when_active_session_exists(
        db, monkeypatch):
    """A running phone that already has an active session (warmup /
    publish / manual / etc.) must NOT be stopped by external detection."""
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="busy", env_id="ph-busy")
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id="ph-busy", source="warmup",
        owner_type="account", owner_id=aid,
        budget_seconds=600, created_by="warmup",
    )

    fake = FakeWatchdogClient(running_set={"ph-busy"})
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "stopped 0 external" in out
    assert fake.stopped == []
    # The warmup session stays untouched.
    sess = phone_runtime.get_active_for_account(db, aid)
    assert sess is not None
    assert sess["id"] == sid
    assert sess["source"] == "warmup"


def test_watchdog_does_not_stop_non_running_phone(db, monkeypatch):
    """A GeeLark phone reported as Shut down needs no action."""
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="off", env_id="ph-off")

    fake = FakeWatchdogClient(running_set=set())  # nothing running
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "stopped 0 external" in out
    assert fake.stopped == []
    assert phone_runtime.get_active_for_account(db, aid) is None


def test_watchdog_times_out_overdue_external_detected(db, monkeypatch):
    """external_detected sessions still get timed out and stopped when
    their short budget expires. Otherwise a system-external phone start
    can bill forever."""
    from app.services import geelark_service
    aid = _account_with_phone(db, profile="ext-overdue",
                              env_id="ph-ext-od")
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id="ph-ext-od",
        source="external_detected", owner_type=None, owner_id=None,
        budget_seconds=60, created_by="watchdog",
    )
    # Backdate so a naive list_overdue would catch it.
    _backdate_session(db, sid, minutes_overdue=30)

    fake = FakeWatchdogClient(running_set={"ph-ext-od"})
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.overdue_session_watchdog import (
        overdue_session_watchdog_task,
    )
    out = asyncio.run(overdue_session_watchdog_task(timestamp=0))

    assert "timed out 1" in out
    assert fake.stopped == ["ph-ext-od"]
    sess = ps_repo.get(db, sid)
    assert sess is not None
    assert sess["status"] == "TIMEOUT"
    assert sess["final_status"] == "TIMEOUT"
