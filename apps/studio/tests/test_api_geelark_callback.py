"""Tests for the GeeLark callback receiver (Phase 0: log-only).

Covers the auth model and the persistence path. Schema discovery
(parsed_task_id / parsed_status) is best-effort here — Phase 0's
contract is "we don't lose the raw envelope", not "we parse it
correctly". The Phase 1 parser will be tested against real callbacks
captured by this endpoint.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text


@pytest.fixture
def token(monkeypatch):
    """Configure a known callback token for the duration of the test."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "GEELARK_CALLBACK_TOKEN", "s3cret-test-token")
    return "s3cret-test-token"


def _count(db) -> int:
    return db.execute(
        text("SELECT count(*) FROM geelark_callback_log")
    ).scalar() or 0


def _last(db) -> dict:
    row = db.execute(
        text(
            "SELECT source_ip, headers_json, body_text, "
            "parsed_task_id, parsed_status, handled "
            "FROM geelark_callback_log "
            "ORDER BY received_at DESC LIMIT 1"
        )
    ).first()
    assert row is not None
    return {
        "source_ip": row.source_ip,
        "headers_json": row.headers_json,
        "body_text": row.body_text,
        "parsed_task_id": row.parsed_task_id,
        "parsed_status": row.parsed_status,
        "handled": row.handled,
    }


def test_callback_requires_token(client, db, token):
    # Missing token → 401, nothing logged.
    r = client.post("/api/geelark/callback", content="{}")
    assert r.status_code == 401
    assert _count(db) == 0


def test_callback_rejects_wrong_token(client, db, token):
    r = client.post("/api/geelark/callback?token=nope", content="{}")
    assert r.status_code == 401
    assert _count(db) == 0


def test_callback_503_when_token_not_configured(client, db, monkeypatch):
    """An environment that hasn't generated a callback token must refuse
    inbound traffic, not silently log under an empty-string secret."""
    import app.config as cfg
    monkeypatch.setattr(cfg, "GEELARK_CALLBACK_TOKEN", "")
    r = client.post(
        "/api/geelark/callback?token=anything", content="{}"
    )
    assert r.status_code == 503
    assert _count(db) == 0


def test_callback_logs_arbitrary_body(client, db, token):
    """Phase 0 contract: any POST with a valid token lands raw, even if
    we cannot parse anything out of it. GeeLark gets 200 either way so
    it doesn't retry against our parser limitations."""
    body = b"not json at all <<garbage>>"
    r = client.post(
        f"/api/geelark/callback?token={token}",
        content=body,
        headers={"X-Custom": "from-geelark"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert _count(db) == 1
    row = _last(db)
    assert row["body_text"] == body.decode()
    assert row["parsed_task_id"] is None
    assert row["parsed_status"] is None
    # headers_json is a JSON-typed column — psycopg returns a dict.
    headers = row["headers_json"]
    if isinstance(headers, str):
        headers = json.loads(headers)
    assert headers.get("x-custom") == "from-geelark"


def test_callback_parses_well_formed_payload(client, db, token):
    payload = {"taskId": "tk-12345", "status": 3}
    r = client.post(
        f"/api/geelark/callback?token={token}",
        json=payload,
    )
    assert r.status_code == 200
    row = _last(db)
    assert row["parsed_task_id"] == "tk-12345"
    assert row["parsed_status"] == 3
    assert row["handled"] is False  # Phase 0 never marks handled


def test_callback_accepts_snake_case_field(client, db, token):
    """Schema is undocumented — accept any of {taskId, task_id, ID, id}
    until production samples settle the actual field name."""
    r = client.post(
        f"/api/geelark/callback?token={token}",
        json={"task_id": "tk-9", "status": 7},
    )
    assert r.status_code == 200
    row = _last(db)
    assert row["parsed_task_id"] == "tk-9"
    assert row["parsed_status"] == 7


def test_callback_boolean_result_does_not_break_insert(client, db, token):
    """Regression for prod 2026-06-16 incident: GeeLark sent payloads where
    the status/result field was a JSON boolean. Because Python's `bool` is a
    subclass of `int`, the old `isinstance(candidate, int)` branch accepted
    `True`/`False` and bound them as PG `boolean`, blowing up the whole
    INSERT against the `parsed_status INTEGER` column ("column ... is of
    type integer but expression is of type boolean"). The Phase 0 contract
    is "raw envelope must survive", so the row MUST land — just with
    `parsed_status IS NULL`."""
    r = client.post(
        f"/api/geelark/callback?token={token}",
        json={"taskId": "tk-bool", "result": True},
    )
    assert r.status_code == 200
    assert _count(db) == 1
    row = _last(db)
    assert row["parsed_task_id"] == "tk-bool"
    assert row["parsed_status"] is None
    assert "true" in row["body_text"]


def test_callback_persistence_failure_does_not_5xx(
        client, db, token, monkeypatch):
    """If the INSERT raises, the endpoint must still return 200 — a 5xx
    would make GeeLark retry, and we'd duplicate the side effects when
    Phase 1 wires this into stop_phones."""
    from app.api import geelark as ga

    original = ga.text

    def boom(query):
        if "INSERT INTO geelark_callback_log" in query:
            raise RuntimeError("simulated DB failure")
        return original(query)

    monkeypatch.setattr(ga, "text", boom)
    r = client.post(
        f"/api/geelark/callback?token={token}",
        json={"taskId": "tk-x", "status": 3},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
