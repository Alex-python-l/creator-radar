"""Tests for the lease-based warmup service functions.

All lease state lives in phone_sessions; the legacy accounts.phone_busy_until
and accounts.warmup_task_id columns have been retired.

Covers the four crash windows / invariants:
  1. Happy path: start_warmup_one returns gtid, in-flight phone_session
     exists with the task id, phone NOT stopped.
  2. Phone busy: pre-acquired session → PhoneBusy raised.
  3. start_phones fails: session finalized as FAILED, last_warm_result='Failed',
     phone stop attempted.
  4. create_warmup_task fails: same as (3) even though start_phones
     succeeded.
"""

import pytest
from sqlalchemy import text

from app.repositories import accounts as acc_repo
from app.services import phone_runtime
from app.services.warmup_service import (
    PhoneBusy,
    advance_warmup_one,
    mark_warmup_timed_out,
    start_warmup_one,
)


# ---------------------------------------------------------------------------
# Fake GeeLark client
# ---------------------------------------------------------------------------
class FakeClient:
    def __init__(self, *, fail_at: str | None = None):
        self.started, self.stopped, self.created = [], [], []
        self.fail_at = fail_at
        self.next_task_id = "gtid-1"

    def start_phones(self, ids):
        self.started.extend(ids)
        if self.fail_at == "start_phones":
            raise RuntimeError("simulated start_phones failure")

    def create_warmup_task(self, env_id, duration):
        self.created.append((env_id, duration))
        if self.fail_at == "create_warmup_task":
            raise RuntimeError("simulated create_warmup_task failure")
        return self.next_task_id

    def stop_phones(self, ids):
        self.stopped.extend(ids)


# ---------------------------------------------------------------------------
# Fixture: warmup-eligible account
# ---------------------------------------------------------------------------
@pytest.fixture
def warmup_account(db):
    """Create a warmup-eligible Account and return its id."""
    aid = acc_repo.create(db, {
        "profile_name": "warmup-test-phone",
        "tiktok_logged_in": 1,
        "geelark_phone_id": "phone-abc-123",
        "warmup_mode": "daily",
        "warmed_up": 0,
    })
    return aid


def _phone_session(db, account_id: int):
    return db.execute(
        text(
            "SELECT id, status, final_status, geelark_task_id, source, "
            "owner_type, owner_id, started_at, stopped_at, stop_reason, "
            "budget_deadline_at "
            "FROM phone_sessions WHERE account_id = :aid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"aid": account_id},
    ).fetchone()


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------
def test_start_warmup_one_happy_path(db, warmup_account):
    """start_warmup_one returns real gtid, in-flight phone_session
    carries the task id, phone NOT stopped."""
    from datetime import datetime

    client = FakeClient()
    gtid = start_warmup_one(db, client, warmup_account, duration_minutes=3)

    assert gtid == "gtid-1"

    sess = _phone_session(db, warmup_account)
    assert sess is not None
    assert sess.stopped_at is None
    assert sess.budget_deadline_at > datetime.utcnow()
    assert sess.geelark_task_id == "gtid-1"

    # Phone was started but NOT stopped (fire-and-forget between submit
    # and check).
    assert client.started == ["phone-abc-123"]
    assert client.stopped == []


# ---------------------------------------------------------------------------
# 2. Phone busy
# ---------------------------------------------------------------------------
def test_start_warmup_one_phone_busy_raises(db, warmup_account):
    """A pre-acquired phone_session causes PhoneBusy to be raised."""
    a = acc_repo.get(db, warmup_account)
    phone_runtime.start_session(
        db, account_id=warmup_account, env_id=a["geelark_phone_id"],
        source="manual", owner_type="account", owner_id=warmup_account,
        budget_seconds=300, created_by="test",
    )

    client = FakeClient()
    with pytest.raises(PhoneBusy):
        start_warmup_one(db, client, warmup_account, duration_minutes=3)

    # Phone should not have been touched.
    assert client.started == []
    assert client.stopped == []


# ---------------------------------------------------------------------------
# 3. start_phones fails
# ---------------------------------------------------------------------------
def test_start_warmup_one_start_phones_fails(db, warmup_account):
    """When start_phones raises: phone_session finalized as FAILED,
    last_warm_result='Failed', best-effort stop attempted."""
    client = FakeClient(fail_at="start_phones")

    with pytest.raises(RuntimeError, match="simulated start_phones failure"):
        start_warmup_one(db, client, warmup_account, duration_minutes=3)

    sess = _phone_session(db, warmup_account)
    assert sess.stopped_at is not None
    assert sess.final_status == "FAILED"

    a = acc_repo.get(db, warmup_account)
    assert a["last_warm_result"] == "Failed"

    # start_phones already extended `started` before raising; stop is
    # called from the cleanup path.
    assert client.started == ["phone-abc-123"]
    assert client.stopped == ["phone-abc-123"]


# ---------------------------------------------------------------------------
# 4. create_warmup_task fails (phone already started)
# ---------------------------------------------------------------------------
def test_start_warmup_one_create_task_fails(db, warmup_account):
    """create_warmup_task raises after start_phones succeeds: session
    finalized as FAILED, phone stopped."""
    client = FakeClient(fail_at="create_warmup_task")

    with pytest.raises(RuntimeError, match="simulated create_warmup_task failure"):
        start_warmup_one(db, client, warmup_account, duration_minutes=3)

    sess = _phone_session(db, warmup_account)
    assert sess.stopped_at is not None
    assert sess.final_status == "FAILED"

    a = acc_repo.get(db, warmup_account)
    assert a["last_warm_result"] == "Failed"

    assert client.started == ["phone-abc-123"]
    assert client.stopped == ["phone-abc-123"]


# ---------------------------------------------------------------------------
# 5. advance_warmup_one — empty GeeLark response (task purged on their side)
# ---------------------------------------------------------------------------
class PollFakeClient:
    """Minimal poll-side client used by advance_warmup_one / timeout tests."""
    def __init__(self, response):
        self._response = response
        self.stopped = []

    def query_tasks(self, ids):
        return self._response

    def stop_phones(self, ids):
        self.stopped.extend(ids)


def test_advance_warmup_one_empty_response_stops_phone(db, warmup_account):
    """If GeeLark returns an empty list for a known task_id, treat it as
    a terminal failure: stop the phone, mark Failed, finalize the
    session. Otherwise the polling chain spins forever and the phone
    keeps billing."""
    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)

    client = PollFakeClient(response=[])
    outcome = advance_warmup_one(db, client, warmup_account, "gtid-1")

    assert outcome == "failure"
    assert client.stopped == ["phone-abc-123"]

    sess = _phone_session(db, warmup_account)
    assert sess.stopped_at is not None
    assert sess.final_status == "FAILED"

    a = acc_repo.get(db, warmup_account)
    assert a["last_warm_result"] == "Failed"
    assert "not found" in (a.get("last_warm_notes") or "").lower()


def test_advance_warmup_one_times_out_when_lease_expired(db, warmup_account):
    """A warmup check that wakes after the session budget has expired
    must stop the phone immediately, even if GeeLark still reports
    running."""
    from datetime import datetime, timedelta

    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :aid AND stopped_at IS NULL"
        ),
        {"p": datetime.utcnow() - timedelta(minutes=1),
         "aid": warmup_account},
    )
    db.commit()

    client = PollFakeClient(response=[{"id": "gtid-1", "status": 2}])
    outcome = advance_warmup_one(db, client, warmup_account, "gtid-1")

    assert outcome == "timeout"
    assert client.stopped == ["phone-abc-123"]

    sess = _phone_session(db, warmup_account)
    assert sess.final_status == "TIMEOUT"

    a = acc_repo.get(db, warmup_account)
    assert a["last_warm_result"] == "Failed"
    assert "lease expired" in (a.get("last_warm_notes") or "").lower()


# ---------------------------------------------------------------------------
# 6. mark_warmup_timed_out — polling chain exhausted while GeeLark still running
# ---------------------------------------------------------------------------
def test_mark_warmup_timed_out_stops_phone_and_releases_lease(db, warmup_account):
    """When the 40-poll window expires with the GeeLark task still in a
    non-terminal state, the timeout handler must stop the phone, finalize
    the session as TIMEOUT, and record a Failed result so the row is no
    longer 'stranded' for the reclaim cron."""
    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)

    client = PollFakeClient(response=[{"id": "gtid-1", "status": 2}])
    outcome = mark_warmup_timed_out(db, client, warmup_account, "gtid-1")

    assert outcome == "timeout"
    assert client.stopped == ["phone-abc-123"]

    sess = _phone_session(db, warmup_account)
    assert sess.stopped_at is not None
    assert sess.final_status == "TIMEOUT"

    a = acc_repo.get(db, warmup_account)
    assert a["last_warm_result"] == "Failed"
    assert "timeout" in (a.get("last_warm_notes") or "").lower()


# ---------------------------------------------------------------------------
# 7. phone_runtime integration
# ---------------------------------------------------------------------------
def test_start_warmup_one_creates_active_phone_session(db, warmup_account):
    """warmup goes through phone_runtime; an ACTIVE phone_session row
    exists carrying the GeeLark task id."""
    client = FakeClient()
    gtid = start_warmup_one(db, client, warmup_account, duration_minutes=3)

    sess = _phone_session(db, warmup_account)
    assert sess is not None
    assert sess.status == "ACTIVE"
    assert sess.final_status is None
    assert sess.stopped_at is None
    assert sess.source == "warmup"
    assert sess.owner_type == "account"
    assert sess.owner_id == warmup_account
    assert sess.geelark_task_id == gtid


def test_advance_success_finalizes_phone_session(db, warmup_account):
    """Terminal status=3 → phone_session status=STOPPED, final=STOPPED."""
    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)

    client = PollFakeClient(response=[{"id": "gtid-1", "status": 3}])
    outcome = advance_warmup_one(db, client, warmup_account, "gtid-1")
    assert outcome == "success"

    sess = _phone_session(db, warmup_account)
    assert sess.status == "STOPPED"
    assert sess.final_status == "STOPPED"
    assert sess.stopped_at is not None


def test_advance_failure_finalizes_phone_session(db, warmup_account):
    """Terminal status=4 → phone_session status=STOPPED, final=FAILED,
    stop_reason populated."""
    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)

    client = PollFakeClient(response=[{"id": "gtid-1", "status": 4}])
    outcome = advance_warmup_one(db, client, warmup_account, "gtid-1")
    assert outcome == "failure"

    sess = _phone_session(db, warmup_account)
    assert sess.status == "STOPPED"
    assert sess.final_status == "FAILED"
    assert sess.stop_reason and "status 4" in sess.stop_reason


def test_advance_lease_expired_finalizes_phone_session_as_timeout(
        db, warmup_account):
    """advance_warmup_one's lease-expiry short-circuit must mark the
    phone_session as TIMEOUT, not FAILED — they're different signals
    to the audit page."""
    from datetime import datetime, timedelta

    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :aid AND stopped_at IS NULL"
        ),
        {"p": datetime.utcnow() - timedelta(minutes=1),
         "aid": warmup_account},
    )
    db.commit()

    client = PollFakeClient(response=[{"id": "gtid-1", "status": 2}])
    outcome = advance_warmup_one(db, client, warmup_account, "gtid-1")
    assert outcome == "timeout"

    sess = _phone_session(db, warmup_account)
    assert sess.status == "TIMEOUT"
    assert sess.final_status == "TIMEOUT"
    assert sess.stop_reason and "lease expired" in sess.stop_reason.lower()


def test_reclaim_pending_finalizes_phone_session(db, warmup_account):
    """An in-flight warmup phone_session with NULL geelark_task_id is
    the T1↔T3 crash window — reclaim should treat it as a failed
    session so the audit page surfaces the loss."""
    from app.services.warmup_service import reclaim_in_flight_one

    a = acc_repo.get(db, warmup_account)
    phone_runtime.start_session(
        db, account_id=warmup_account, env_id=a["geelark_phone_id"],
        source="warmup", owner_type="account", owner_id=warmup_account,
        budget_seconds=1200, created_by="warmup",
    )
    # No attach_geelark_task call → geelark_task_id stays NULL → crash window.

    client = FakeClient()
    outcome = reclaim_in_flight_one(db, client, warmup_account)
    assert outcome == "pending_cleared"

    sess = _phone_session(db, warmup_account)
    assert sess.status == "STOPPED"
    assert sess.final_status == "FAILED"
    assert sess.stop_reason and "T1↔T3" in sess.stop_reason


def test_reclaim_renews_phone_session_budget(db, warmup_account):
    """A non-pending warmup with an expired session budget should be
    refreshed so the poll chain resumes."""
    from datetime import datetime, timedelta
    from app.services.warmup_service import reclaim_in_flight_one

    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)
    past = datetime.utcnow() - timedelta(minutes=5)
    db.execute(
        text(
            "UPDATE phone_sessions SET budget_deadline_at = :p "
            "WHERE account_id = :aid AND stopped_at IS NULL"
        ),
        {"p": past, "aid": warmup_account},
    )
    db.commit()

    outcome = reclaim_in_flight_one(db, PollFakeClient([]), warmup_account)
    assert outcome.startswith("resume:")

    sess = _phone_session(db, warmup_account)
    assert sess.budget_deadline_at > datetime.utcnow()


# ---------------------------------------------------------------------------
# 5. GeeLark 48004 — TikTok build too old → auto-install upgrade
# ---------------------------------------------------------------------------
_48004_MSG = ("GeeLark API error [48004]: Please install TikTok version "
              "45.0.3 and try again. Related cloud phone id: 612 | path=/")

_CATALOG = {"items": [
    {"appName": "TikTok(Asia)", "packageName": "com.ss.android.ugc.trill",
     "appVersionInfoList": [{"id": "asia-45", "versionName": "45.0.3"}]},
    {"appName": "TikTok", "packageName": "com.zhiliaoapp.musically",
     "appVersionInfoList": [{"id": "main-44", "versionName": "44.5.3"},
                            {"id": "main-45", "versionName": "45.0.3"}]},
]}


class Fake48004Client(FakeClient):
    """create_warmup_task rejects with 48004; records install calls."""

    def __init__(self, *, catalog=None, install_error: str | None = None):
        super().__init__()
        self.catalog = catalog if catalog is not None else _CATALOG
        self.install_error = install_error
        self.install_calls: list[tuple[str, str]] = []
        self.catalog_queries: list[tuple[str, str]] = []

    def create_warmup_task(self, env_id, duration):
        raise RuntimeError(_48004_MSG)

    def get_installable_apps(self, env_id, name=""):
        self.catalog_queries.append((env_id, name))
        return self.catalog

    def install_app(self, env_id, app_version_id):
        self.install_calls.append((env_id, app_version_id))
        if self.install_error:
            raise RuntimeError(self.install_error)
        return {"code": 0}


def test_48004_submits_tiktok_upgrade_before_stopping(db, warmup_account):
    """48004 → the required build is looked up and installed while the
    phone is still running; mainline package wins over the Asia build;
    failure bookkeeping (notes, stop, finalize) is unchanged."""
    client = Fake48004Client()
    with pytest.raises(RuntimeError):
        start_warmup_one(db, client, warmup_account, duration_minutes=3)

    # Mainline musically 45.0.3 picked, not the Asia build.
    assert client.install_calls == [("phone-abc-123", "main-45")]
    # Phone stopped after the install was submitted.
    assert client.stopped == ["phone-abc-123"]

    a = acc_repo.get(db, warmup_account)
    assert a["last_warm_result"] == "Failed"
    assert "auto-install: TikTok 45.0.3 submitted" in a["last_warm_notes"]

    sess = _phone_session(db, warmup_account)
    assert sess.stopped_at is not None


def test_48004_prior_install_still_running_is_fine(db, warmup_account):
    """42003 from install_app means a previous attempt's install is still
    running — recorded as such, not as a failure."""
    client = Fake48004Client(
        install_error="GeeLark API error [42003]: app installing | path=/")
    with pytest.raises(RuntimeError):
        start_warmup_one(db, client, warmup_account, duration_minutes=3)

    a = acc_repo.get(db, warmup_account)
    assert "auto-install: TikTok 45.0.3 already installing" in a["last_warm_notes"]


def test_48004_version_missing_from_catalog(db, warmup_account):
    """Required build absent from the installable catalog — noted, no
    install call, normal failure path."""
    client = Fake48004Client(catalog={"items": [
        {"appName": "TikTok", "packageName": "com.zhiliaoapp.musically",
         "appVersionInfoList": [{"id": "main-44", "versionName": "44.5.3"}]},
    ]})
    with pytest.raises(RuntimeError):
        start_warmup_one(db, client, warmup_account, duration_minutes=3)

    assert client.install_calls == []
    a = acc_repo.get(db, warmup_account)
    assert "auto-install: TikTok 45.0.3 not in catalog" in a["last_warm_notes"]


def test_non_48004_failure_does_not_touch_install_api(db, warmup_account):
    """Ordinary create_warmup_task failures never query the app catalog."""
    client = FakeClient(fail_at="create_warmup_task")
    with pytest.raises(RuntimeError):
        start_warmup_one(db, client, warmup_account, duration_minutes=3)

    a = acc_repo.get(db, warmup_account)
    assert "auto-install" not in (a["last_warm_notes"] or "")


# ---------------------------------------------------------------------------
# 6. Duplicate poll chains (worker backlog / reclaim overlap) must not
#    double-apply side effects
# ---------------------------------------------------------------------------
class CancelPollFakeClient(PollFakeClient):
    def __init__(self, response):
        super().__init__(response)
        self.cancelled = []

    def cancel_tasks(self, ids):
        self.cancelled.extend(ids)


def test_duplicate_chain_does_not_double_count_warmup(db, warmup_account):
    """Two live poll chains for the same GeeLark task (reclaim re-deferred
    while the original chain was merely delayed): the second chain must
    not increment warmup_count again after the first converged."""
    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)

    done = PollFakeClient(response=[{"status": 3}])
    assert advance_warmup_one(db, done, warmup_account, "gtid-1") == "success"
    assert acc_repo.get(db, warmup_account)["warmup_count"] == 1

    # Chain B arrives late; GeeLark still reports the terminal status.
    outcome = advance_warmup_one(db, done, warmup_account, "gtid-1")
    assert acc_repo.get(db, warmup_account)["warmup_count"] == 1, (
        "duplicate chain double-counted warmup_count"
    )
    assert outcome != "success"


def test_stale_timeout_does_not_stop_foreign_session_phone(db, warmup_account):
    """A leftover warmup chain hitting its attempts-exhausted timeout
    path after the warmup already converged must not cancel tasks, stop
    the phone (another source may be running on it), or overwrite the
    recorded result."""
    start_warmup_one(db, FakeClient(), warmup_account, duration_minutes=3)
    done = PollFakeClient(response=[{"status": 3}])
    assert advance_warmup_one(db, done, warmup_account, "gtid-1") == "success"

    # The account has moved on: a publish session now holds the phone.
    a = acc_repo.get(db, warmup_account)
    phone_runtime.start_session(
        db, account_id=warmup_account, env_id=a["geelark_phone_id"],
        source="publish", owner_type="publication", owner_id=4242,
        budget_seconds=900, created_by="test",
    )

    stale = CancelPollFakeClient(response=[{"status": 2}])
    mark_warmup_timed_out(db, stale, warmup_account, "gtid-1")

    assert stale.stopped == [], (
        "stale warmup chain stopped a phone owned by a publish session"
    )
    assert stale.cancelled == [], (
        "stale warmup chain cancelled a task after convergence"
    )
    a = acc_repo.get(db, warmup_account)
    assert a["last_warm_result"] == "Success", (
        "stale timeout overwrote the converged Success result"
    )
