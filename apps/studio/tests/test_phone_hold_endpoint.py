"""Tests for the manual phone-hold/release endpoints.

The hold button creates a manual phone_session so the idle_phone_sweeper
cron stops treating a hand-operated GeeLark phone as idle. Release
finalizes that session without disturbing warmup/publish/engagement
sessions.

Endpoints under test:
  POST /api/accounts/{id}/phone/hold     body: {"hours": int?}
  POST /api/accounts/{id}/phone/release
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture
def client(monkeypatch):
    import app.config as cfg
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def _login(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})


def _make_account(client, **overrides) -> int:
    body = {"profile_name": "hold-test", "geelark_phone_id": "ph-hold-1"}
    body.update(overrides)
    return client.post("/api/accounts", json=body).json()["id"]


def _active_session(db, account_id: int):
    return db.execute(
        text(
            "SELECT id, status, source, stopped_at, final_status, "
            "  budget_deadline_at "
            "FROM phone_sessions WHERE account_id = :aid "
            "  AND stopped_at IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"aid": account_id},
    ).fetchone()


def _any_session(db, account_id: int):
    return db.execute(
        text(
            "SELECT id, status, source, stopped_at, final_status, "
            "  budget_deadline_at "
            "FROM phone_sessions WHERE account_id = :aid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"aid": account_id},
    ).fetchone()


# ---------- hold ---------------------------------------------------------

def test_hold_default_2h_succeeds(client, db):
    _login(client)
    aid = _make_account(client)

    r = client.post(f"/api/accounts/{aid}/phone/hold", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "phone_busy_until" in body

    sess = _active_session(db, aid)
    assert sess is not None
    delta = sess.budget_deadline_at - datetime.utcnow()
    assert timedelta(minutes=115) < delta < timedelta(minutes=125)


def test_hold_response_and_accounts_list_use_utc_suffix(client):
    _login(client)
    aid = _make_account(client)

    r = client.post(f"/api/accounts/{aid}/phone/hold", json={})
    assert r.status_code == 200, r.text
    assert r.json()["phone_busy_until"].endswith("Z")

    r = client.get("/api/accounts")
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["id"] == aid)
    assert row["phone_busy_until"].endswith("Z")


def test_hold_custom_hours(client, db):
    _login(client)
    aid = _make_account(client)

    r = client.post(f"/api/accounts/{aid}/phone/hold", json={"hours": 4})
    assert r.status_code == 200, r.text

    sess = _active_session(db, aid)
    assert sess is not None
    delta = sess.budget_deadline_at - datetime.utcnow()
    assert timedelta(minutes=235) < delta < timedelta(minutes=245)


def test_hold_already_held_returns_409(client):
    _login(client)
    aid = _make_account(client)

    r1 = client.post(f"/api/accounts/{aid}/phone/hold", json={})
    assert r1.status_code == 200

    r2 = client.post(f"/api/accounts/{aid}/phone/hold", json={})
    assert r2.status_code == 409


def test_hold_rejects_zero_or_negative_hours(client):
    _login(client)
    aid = _make_account(client)
    for bad in (0, -1, -5):
        r = client.post(f"/api/accounts/{aid}/phone/hold",
                        json={"hours": bad})
        assert r.status_code == 400, f"hours={bad} should 400"


def test_hold_rejects_excessive_hours(client):
    _login(client)
    aid = _make_account(client)
    r = client.post(f"/api/accounts/{aid}/phone/hold", json={"hours": 13})
    assert r.status_code == 400


def test_hold_rejects_non_int_hours(client):
    _login(client)
    aid = _make_account(client)
    r = client.post(f"/api/accounts/{aid}/phone/hold",
                    json={"hours": "two"})
    assert r.status_code == 400


def test_hold_account_without_phone_returns_400(client):
    _login(client)
    # No geelark_phone_id — meaningless to hold
    aid = _make_account(client, geelark_phone_id=None)
    r = client.post(f"/api/accounts/{aid}/phone/hold", json={})
    assert r.status_code == 400


def test_hold_missing_account_returns_404(client):
    _login(client)
    r = client.post("/api/accounts/9999/phone/hold", json={})
    assert r.status_code == 404


def test_hold_requires_auth(client):
    # No login
    r = client.post("/api/accounts/1/phone/hold", json={})
    assert r.status_code == 401


# ---------- release ------------------------------------------------------

def test_release_finalizes_manual_session(client, db):
    _login(client)
    aid = _make_account(client)
    client.post(f"/api/accounts/{aid}/phone/hold", json={"hours": 2})
    sess_id_before = _active_session(db, aid).id

    r = client.post(f"/api/accounts/{aid}/phone/release")
    assert r.status_code == 200, r.text

    assert _active_session(db, aid) is None
    closed = _any_session(db, aid)
    assert closed.id == sess_id_before
    assert closed.status == "STOPPED"
    assert closed.final_status == "STOPPED"


def test_release_idempotent_when_nothing_held(client):
    _login(client)
    aid = _make_account(client)
    r = client.post(f"/api/accounts/{aid}/phone/release")
    assert r.status_code == 200


def test_release_missing_account_returns_404(client):
    _login(client)
    r = client.post("/api/accounts/9999/phone/release")
    assert r.status_code == 404


def test_release_requires_auth(client):
    r = client.post("/api/accounts/1/phone/release")
    assert r.status_code == 401


# ---------- phone_session integration -------------------------

def test_hold_creates_manual_phone_session(client, db):
    _login(client)
    aid = _make_account(client)
    r = client.post(f"/api/accounts/{aid}/phone/hold", json={"hours": 3})
    assert r.status_code == 200, r.text

    sess = _active_session(db, aid)
    assert sess is not None
    assert sess.status == "ACTIVE"
    assert sess.source == "manual"
    assert sess.final_status is None


def test_release_does_not_touch_other_source_session(client, db):
    """If an automated service (warmup / publish / engagement) has an
    active session, /release must NOT finalize it."""
    from app.services import phone_runtime
    _login(client)
    aid = _make_account(client)
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id="ph-hold-1",
        source="warmup", owner_type="account", owner_id=aid,
        budget_seconds=600, created_by="warmup",
    )

    r = client.post(f"/api/accounts/{aid}/phone/release")
    assert r.status_code == 200

    still_active = _active_session(db, aid)
    assert still_active is not None
    assert still_active.id == sid
    assert still_active.source == "warmup"
    assert still_active.status == "ACTIVE"
