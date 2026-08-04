"""Phase 4c — Ops API exposing in-flight phone_sessions."""
import pytest
from sqlalchemy import text
from datetime import datetime, timedelta

from app.models import Account
from app.services import phone_runtime as runtime


@pytest.fixture
def authed_client(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    return client


def _make_account(db, *, profile: str, env_id: str) -> int:
    a = Account(profile_name=profile, geelark_phone_id=env_id)
    db.add(a); db.commit(); db.refresh(a)
    return a.id


def test_list_sessions_requires_auth(client):
    assert client.get("/api/ops/sessions").status_code == 401


def test_list_active_sessions_returns_in_flight(authed_client, db):
    aid = _make_account(db, profile="alice", env_id="ph-A")
    bid = _make_account(db, profile="bob", env_id="ph-B")
    sid_a = runtime.start_session(
        db, account_id=aid, env_id="ph-A", source="publish",
        owner_type="publication", owner_id=42,
        budget_seconds=600, created_by="publish", kind="publish",
    )
    runtime.start_session(
        db, account_id=bid, env_id="ph-B", source="warmup",
        owner_type="account", owner_id=bid,
        budget_seconds=600, created_by="warmup", kind="warmup",
    )

    r = authed_client.get("/api/ops/sessions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    by_source = {it["source"]: it for it in items}
    assert by_source["publish"]["id"] == sid_a
    assert by_source["publish"]["account_id"] == aid
    assert by_source["publish"]["profile_name"] == "alice"
    assert by_source["publish"]["env_id"] == "ph-A"
    assert by_source["publish"]["overdue"] is False


def test_list_filters_by_overdue(authed_client, db):
    aid = _make_account(db, profile="overdue", env_id="ph-od")
    bid = _make_account(db, profile="ok", env_id="ph-ok")
    sid_od = runtime.start_session(
        db, account_id=aid, env_id="ph-od", source="publish",
        owner_type="publication", owner_id=1,
        budget_seconds=600, created_by="publish", kind="publish",
    )
    runtime.start_session(
        db, account_id=bid, env_id="ph-ok", source="publish",
        owner_type="publication", owner_id=2,
        budget_seconds=600, created_by="publish", kind="publish",
    )
    db.execute(
        text("UPDATE phone_sessions SET budget_deadline_at = :past "
             "WHERE id = :id"),
        {"past": datetime.utcnow() - timedelta(minutes=5), "id": sid_od},
    )
    db.commit()

    r = authed_client.get("/api/ops/sessions?status=overdue")
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == sid_od
    assert items[0]["overdue"] is True


def test_list_rejects_invalid_status(authed_client):
    r = authed_client.get("/api/ops/sessions?status=bogus")
    assert r.status_code == 400


def test_release_session_finalizes(authed_client, db):
    aid = _make_account(db, profile="rel", env_id="ph-rel")
    sid = runtime.start_session(
        db, account_id=aid, env_id="ph-rel",
        source="external_detected", owner_type=None, owner_id=None,
        budget_seconds=3600, created_by="watchdog",
    )

    r = authed_client.post(f"/api/ops/sessions/{sid}/release")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Verify the session is now STOPPED.
    row = db.execute(
        text("SELECT status, final_status FROM phone_sessions "
             "WHERE id = :id"),
        {"id": sid},
    ).first()
    assert row.status == "STOPPED"
    assert row.final_status == "STOPPED"


def test_release_already_stopped_is_idempotent(authed_client, db):
    aid = _make_account(db, profile="done", env_id="ph-done")
    sid = runtime.start_session(
        db, account_id=aid, env_id="ph-done", source="warmup",
        owner_type="account", owner_id=aid,
        budget_seconds=600, created_by="warmup", kind="warmup",
    )
    runtime.finalize_success(db, sid)

    r = authed_client.post(f"/api/ops/sessions/{sid}/release")
    assert r.status_code == 200
    assert r.json()["already_stopped"] is True


def test_release_unknown_returns_404(authed_client):
    r = authed_client.post("/api/ops/sessions/99999/release")
    assert r.status_code == 404


def _enable_rpa_watchdog(monkeypatch):
    from app.api import ops as ops_api
    from app.services import rpa_watchdog_client

    monkeypatch.setattr(rpa_watchdog_client, "configured", lambda: True)
    monkeypatch.setattr(
        ops_api,
        "_watchdog_geelark_token",
        lambda request: "workspace-geelark-token",
    )
    return rpa_watchdog_client


def test_rpa_watchdog_requires_configuration(authed_client, monkeypatch):
    from app.services import rpa_watchdog_client

    monkeypatch.setattr(rpa_watchdog_client, "configured", lambda: False)
    response = authed_client.get("/api/ops/rpa-watchdog/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "RPA watchdog is not configured"


def test_rpa_watchdog_uses_active_workspace_geelark_token(
    authed_client, monkeypatch,
):
    from app import workspace_secrets
    from app.services import rpa_watchdog_client

    seen = {}
    monkeypatch.setattr(rpa_watchdog_client, "configured", lambda: True)

    def fake_get_secret(session, workspace_id, name):
        seen.update(workspace_id=workspace_id, name=name)
        return "workspace-1-geelark-token"

    monkeypatch.setattr(workspace_secrets, "get_secret", fake_get_secret)
    monkeypatch.setattr(
        rpa_watchdog_client,
        "health",
        lambda token: {"success": True, "receivedToken": token},
    )

    response = authed_client.get("/api/ops/rpa-watchdog/health")

    assert response.status_code == 200
    assert response.json()["receivedToken"] == "workspace-1-geelark-token"
    assert seen == {"workspace_id": 1, "name": "GEELARK_TOKEN"}


def test_rpa_watchdog_health_and_tasks_proxy(authed_client, monkeypatch):
    watchdog = _enable_rpa_watchdog(monkeypatch)
    monkeypatch.setattr(
        watchdog,
        "health",
        lambda token: {
            "success": True,
            "service": "geelark-rpa-watchdog",
            "token": token,
        },
    )
    monkeypatch.setattr(
        watchdog,
        "list_tasks",
        lambda token: {
            "success": True,
            "count": 1,
            "tasks": [{"taskId": "task-1", "token": token}],
        },
    )

    health = authed_client.get("/api/ops/rpa-watchdog/health")
    tasks = authed_client.get("/api/ops/rpa-watchdog/tasks")

    assert health.status_code == 200
    assert health.json()["service"] == "geelark-rpa-watchdog"
    assert health.json()["token"] == "workspace-geelark-token"
    assert tasks.status_code == 200
    assert tasks.json()["tasks"] == [
        {"taskId": "task-1", "token": "workspace-geelark-token"}
    ]


def test_rpa_watchdog_start_stop_and_delete_proxy(authed_client, monkeypatch):
    watchdog = _enable_rpa_watchdog(monkeypatch)
    calls = []
    monkeypatch.setattr(
        watchdog,
        "start_task",
        lambda token, body: calls.append(("start", token, body))
        or {"taskId": "task-1"},
    )
    monkeypatch.setattr(
        watchdog,
        "stop_task",
        lambda token, task_id: calls.append(("stop", token, task_id))
        or {"scheduled": True},
    )
    monkeypatch.setattr(
        watchdog,
        "delete_task",
        lambda token, task_id: calls.append(("delete", token, task_id))
        or {"deleted": True},
    )

    started = authed_client.post(
        "/api/ops/rpa-watchdog/start",
        json={
            "deviceId": "phone-1",
            "flowId": "flow-1",
            "paramMap": {"视频链接": "https://www.tiktok.com/@owner/video/1"},
        },
    )
    stopped = authed_client.post("/api/ops/rpa-watchdog/tasks/task-1/stop")
    deleted = authed_client.delete("/api/ops/rpa-watchdog/tasks/task-1")

    assert started.status_code == 200
    assert stopped.status_code == 200
    assert deleted.status_code == 200
    assert calls == [
        (
            "start",
            "workspace-geelark-token",
            {
                "deviceId": "phone-1",
                "flowId": "flow-1",
                "scheduleAt": None,
                "paramMap": {"视频链接": "https://www.tiktok.com/@owner/video/1"},
            },
        ),
        ("stop", "workspace-geelark-token", "task-1"),
        ("delete", "workspace-geelark-token", "task-1"),
    ]


def test_rpa_watchdog_upstream_auth_error_does_not_log_out_operator(
    authed_client, monkeypatch,
):
    watchdog = _enable_rpa_watchdog(monkeypatch)

    def fail(token):
        raise watchdog.WatchdogClientError("RPA watchdog is unavailable")

    monkeypatch.setattr(watchdog, "list_tasks", fail)
    response = authed_client.get("/api/ops/rpa-watchdog/tasks")

    assert response.status_code == 502
