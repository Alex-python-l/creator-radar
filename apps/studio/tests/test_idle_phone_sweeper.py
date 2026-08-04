"""Defense-in-depth sweeper: stop any GeeLark phone running with no
active phone_session."""
import asyncio

import pytest

from app.repositories import accounts as acc_repo
from app.services import phone_runtime


class FakeSweeperClient:
    """Records get_phone_status calls and stop_phones calls.
    `running_set` is the set of phone IDs that should report as Started."""
    def __init__(self, running_set):
        self._running = set(running_set)
        self.status_calls = []
        self.stopped = []

    def get_phone_status(self, ids):
        self.status_calls.append(list(ids))
        return {"successDetails": [
            # 0 = Started (running), 2 = Shut down (not running)
            {"id": pid, "status": 0 if pid in self._running else 2}
            for pid in ids
        ]}

    def stop_phones(self, ids):
        self.stopped.extend(ids)


def _start_session(db, account_id: int, env_id: str, *,
                    budget_seconds: int = 600):
    return phone_runtime.start_session(
        db, account_id=account_id, env_id=env_id,
        source="publish", owner_type="account", owner_id=account_id,
        budget_seconds=budget_seconds, created_by="test",
    )


def test_sweeper_stops_phone_with_no_session(db, monkeypatch):
    """A Started phone with no in-flight phone_session has no business
    reason to be running — kill it."""
    from app.services import geelark_service
    acc_repo.create(db, {"profile_name": "noLease",
                          "geelark_phone_id": "ph-nolease"})

    fake = FakeSweeperClient(running_set={"ph-nolease"})
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.idle_phone_sweeper import idle_phone_sweeper_task
    out = asyncio.run(idle_phone_sweeper_task(timestamp=0))

    assert fake.stopped == ["ph-nolease"]
    assert "stopped 1" in out


def test_sweeper_skips_phone_with_active_session(db, monkeypatch):
    """The core invariant: when phone_sessions has an active row,
    sweeper must NOT stop the phone."""
    from app.services import geelark_service
    aid = acc_repo.create(db, {"profile_name": "div",
                               "geelark_phone_id": "ph-div"})
    _start_session(db, aid, "ph-div", budget_seconds=3600)

    fake = FakeSweeperClient(running_set={"ph-div"})
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.idle_phone_sweeper import idle_phone_sweeper_task
    out = asyncio.run(idle_phone_sweeper_task(timestamp=0))

    assert fake.stopped == []
    assert "stopped 0" in out


def test_sweeper_ignores_already_stopped_phones(db, monkeypatch):
    """A phone GeeLark reports as Shut down is fine — no stop call."""
    from app.services import geelark_service
    acc_repo.create(db, {"profile_name": "off",
                          "geelark_phone_id": "ph-off"})

    fake = FakeSweeperClient(running_set=set())  # nothing running
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.idle_phone_sweeper import idle_phone_sweeper_task
    out = asyncio.run(idle_phone_sweeper_task(timestamp=0))

    assert fake.stopped == []
    assert "stopped 0" in out


def test_sweeper_stops_after_session_finalized(db, monkeypatch):
    """Once the session is STOPPED, the row no longer counts as
    in-flight. If the phone is still running on GeeLark's side after a
    manual hold or rollback bug, sweeper should catch it."""
    from app.services import geelark_service
    aid = acc_repo.create(db, {"profile_name": "done",
                               "geelark_phone_id": "ph-done"})
    sid = _start_session(db, aid, "ph-done")
    phone_runtime.finalize_success(db, sid)

    fake = FakeSweeperClient(running_set={"ph-done"})
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.idle_phone_sweeper import idle_phone_sweeper_task
    out = asyncio.run(idle_phone_sweeper_task(timestamp=0))

    assert fake.stopped == ["ph-done"]
    assert "stopped 1" in out


def test_sweeper_skips_accounts_without_phone_id(db, monkeypatch):
    """Accounts that don't have a geelark_phone_id shouldn't be queried."""
    from app.services import geelark_service
    acc_repo.create(db, {"profile_name": "no-phone"})  # no geelark_phone_id

    fake = FakeSweeperClient(running_set=set())
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.idle_phone_sweeper import idle_phone_sweeper_task
    out = asyncio.run(idle_phone_sweeper_task(timestamp=0))

    assert fake.status_calls == []  # never queried
    assert fake.stopped == []
    assert "stopped 0" in out


def test_sweeper_mixed_fleet(db, monkeypatch):
    """Full picture:
      - active session                       → SKIP
      - no session                           → STOP
      - finalized session, phone still up    → STOP (the bug we catch)
    """
    from app.services import geelark_service

    a_sess = acc_repo.create(db, {"profile_name": "with-sess",
                                    "geelark_phone_id": "ph-sess"})
    _start_session(db, a_sess, "ph-sess", budget_seconds=3600)

    acc_repo.create(db, {"profile_name": "null2",
                          "geelark_phone_id": "ph-null2"})

    a_closed = acc_repo.create(db, {"profile_name": "closed",
                                     "geelark_phone_id": "ph-closed"})
    sid = _start_session(db, a_closed, "ph-closed")
    phone_runtime.finalize_success(db, sid)

    fake = FakeSweeperClient(
        running_set={"ph-sess", "ph-null2", "ph-closed"})
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.idle_phone_sweeper import idle_phone_sweeper_task
    asyncio.run(idle_phone_sweeper_task(timestamp=0))

    assert set(fake.stopped) == {"ph-null2", "ph-closed"}
    assert "ph-sess" not in fake.stopped


class RacingSweeperClient(FakeSweeperClient):
    """Simulates a session being started DURING the status scan — after
    the sweeper's DB snapshot but before stop_phones fires."""

    def __init__(self, running_set, *, on_status=None):
        super().__init__(running_set)
        self._on_status = on_status

    def get_phone_status(self, ids):
        if self._on_status is not None:
            cb, self._on_status = self._on_status, None
            cb()
        return super().get_phone_status(ids)


def test_sweeper_recheck_spares_freshly_claimed_phone(db, monkeypatch):
    """The idle verdict comes from a snapshot that is minutes old by the
    time stop_phones would fire. A session started in that gap must be
    re-detected by the pre-trigger re-check and its phone spared."""
    from app.services import geelark_service
    aid = acc_repo.create(db, {"profile_name": "racer",
                                "geelark_phone_id": "ph-racer"})

    def claim_mid_scan():
        # Runs inside the sweeper, after its DB snapshot.
        from app.db import session_scope
        with session_scope() as s:
            _start_session(s, aid, "ph-racer")

    fake = RacingSweeperClient(running_set={"ph-racer"},
                               on_status=claim_mid_scan)
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    from app.tasks.idle_phone_sweeper import idle_phone_sweeper_task
    out = asyncio.run(idle_phone_sweeper_task(timestamp=0))

    assert fake.stopped == [], (
        "sweeper stopped a phone whose session started mid-scan"
    )
    assert "stopped 0" in out
