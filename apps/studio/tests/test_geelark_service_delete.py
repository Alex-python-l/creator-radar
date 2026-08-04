"""Tests for geelark_service.delete_accounts — the GeeLark-aware delete
flow that stops phones, removes them remotely, and only then drops local
rows. See app/services/geelark_service.py."""
import pytest


def _fake_client(initial_status=None, fail_on_delete=None):
    """Build a fake GeeLarkClient.

    initial_status: dict {phone_id: status_code}. Status follows GeeLark's
      Query_status doc: 0=Started, 1=Starting, 2=Shut down, 3=Expired.
      After a stop_phones call, every running phone in this dict flips to 2.
    fail_on_delete: dict {phone_id: (code, msg)} for phones that should
      appear in failDetails on delete_phones.
    """
    initial_status = dict(initial_status or {})
    fail_on_delete = fail_on_delete or {}

    calls = {"stop_phones": [], "get_phone_status": [],
             "delete_phones": []}

    class _C:
        def stop_phones(self, ids):
            calls["stop_phones"].append(list(ids))
            for i in ids:
                if initial_status.get(i) in (0, 1):
                    initial_status[i] = 2
            return {}

        def get_phone_status(self, ids):
            calls["get_phone_status"].append(list(ids))
            return {"successDetails": [
                {"id": i, "status": initial_status[i]}
                for i in ids if i in initial_status
            ]}

        def delete_phones(self, ids):
            calls["delete_phones"].append(list(ids))
            fail = [{"code": fail_on_delete[i][0], "id": i,
                     "msg": fail_on_delete[i][1]}
                    for i in ids if i in fail_on_delete]
            return {"totalAmount": len(ids),
                    "successAmount": len(ids) - len(fail),
                    "failAmount": len(fail),
                    "failDetails": fail}

    return _C(), calls, initial_status


def _make_account(db, name, phone_id):
    from app.repositories import accounts as acc
    return acc.create(db, {"profile_name": name,
                           "geelark_phone_id": phone_id})


def test_delete_accounts_happy_path_phone_already_stopped(db, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from app.services import geelark_service as gs
    aid = _make_account(db, "a1", "ph-1")
    client, calls, _ = _fake_client(initial_status={"ph-1": 2})

    res = gs.delete_accounts(db, client, [aid])

    assert res == {"deleted": 1}
    assert calls["delete_phones"] == [["ph-1"]]
    # Phone was already shut down — no stop required.
    assert calls["stop_phones"] == []
    # Local row is gone.
    from app.repositories import accounts as _acc; assert len(_acc.list_all(db)) == 0


def test_delete_accounts_stops_running_phone_before_deleting(db, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from app.services import geelark_service as gs
    aid = _make_account(db, "a1", "ph-running")
    client, calls, _ = _fake_client(initial_status={"ph-running": 0})

    res = gs.delete_accounts(db, client, [aid])

    assert res == {"deleted": 1}
    assert calls["stop_phones"] == [["ph-running"]]
    # Order: stop → poll(running) → poll(stopped) → delete.
    assert calls["delete_phones"] == [["ph-running"]]
    from app.repositories import accounts as _acc; assert len(_acc.list_all(db)) == 0


def test_delete_accounts_aborts_local_when_geelark_fails(db, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from app.services import geelark_service as gs
    aid = _make_account(db, "a1", "ph-bad")
    client, _, _ = _fake_client(
        initial_status={"ph-bad": 2},
        fail_on_delete={"ph-bad": (43009, "env is started")},
    )

    with pytest.raises(RuntimeError, match="env is started"):
        gs.delete_accounts(db, client, [aid])

    # Local row MUST still exist — caller can retry.
    from app.repositories import accounts as acc_repo
    rows = acc_repo.list_all(db)
    assert len(rows) == 1 and rows[0]["id"] == aid


def test_delete_accounts_treats_env_not_found_as_already_gone(db, monkeypatch):
    """code 42001 ("env not found") means GeeLark already lost the phone.
    That should NOT block deleting the local row — the goal is to clean it
    up, and there's nothing left to delete on GeeLark."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from app.services import geelark_service as gs
    aid = _make_account(db, "a1", "ph-orphan")
    client, _, _ = _fake_client(
        initial_status={"ph-orphan": 2},
        fail_on_delete={"ph-orphan": (42001, "env not found")},
    )

    res = gs.delete_accounts(db, client, [aid])

    assert res == {"deleted": 1}
    from app.repositories import accounts as _acc; assert len(_acc.list_all(db)) == 0


def test_delete_accounts_skips_geelark_when_no_phone_id(db, monkeypatch):
    """Accounts without a geelark_phone_id (e.g. created manually before any
    GeeLark sync) must not hit the GeeLark API at all."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from app.services import geelark_service as gs
    from app.repositories import accounts as acc
    aid = acc.create(db, {"profile_name": "local-only"})

    class _Boom:
        def __getattr__(self, n):
            raise AssertionError(f"GeeLark API touched: {n}")

    res = gs.delete_accounts(db, _Boom(), [aid])
    assert res == {"deleted": 1}
    from app.repositories import accounts as _acc; assert len(_acc.list_all(db)) == 0


def test_delete_accounts_batches_at_100(db, monkeypatch):
    """GeeLark's phone/delete caps at 100 IDs per call. 150 phones → 2
    calls."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from app.services import geelark_service as gs
    ids = [_make_account(db, f"a{i}", f"ph-{i}") for i in range(150)]
    status = {f"ph-{i}": 2 for i in range(150)}
    client, calls, _ = _fake_client(initial_status=status)

    res = gs.delete_accounts(db, client, ids)
    assert res == {"deleted": 150}
    assert len(calls["delete_phones"]) == 2
    assert len(calls["delete_phones"][0]) == 100
    assert len(calls["delete_phones"][1]) == 50


def test_delete_accounts_ignores_missing_account_ids(db, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from app.services import geelark_service as gs
    res = gs.delete_accounts(db, object(), [9999])
    assert res == {"deleted": 0}


def test_wait_until_stopped_times_out_when_phone_stays_running(db, monkeypatch):
    """Defensive guard — if GeeLark never reports the phone as shut down,
    we surface a clear error instead of looping forever."""
    # Make the monotonic clock jump 100s every read so the 60s deadline
    # trips on the first post-stop iteration.
    clock = [0.0]
    def _tick():
        clock[0] += 100.0
        return clock[0]
    monkeypatch.setattr("time.monotonic", _tick)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    from app.services import geelark_service as gs

    class _StuckRunning:
        def stop_phones(self, ids):
            return {}
        def get_phone_status(self, ids):
            return {"successDetails": [{"id": i, "status": 0} for i in ids]}
        def delete_phones(self, ids):
            raise AssertionError("delete must not be reached on timeout")

    aid = _make_account(db, "stuck", "ph-stuck")
    with pytest.raises(RuntimeError, match="Timed out"):
        gs.delete_accounts(db, _StuckRunning(), [aid])

    # Row preserved on timeout — same atomicity guarantee.
    from app.repositories import accounts as _acc2; assert len(_acc2.list_all(db)) == 1
