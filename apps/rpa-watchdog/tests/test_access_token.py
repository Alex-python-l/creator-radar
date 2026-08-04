from __future__ import annotations

from fastapi.testclient import TestClient

import main as app_module


def test_api_requires_geelark_bearer_token() -> None:
    app_module.tasks.clear()

    with TestClient(app_module.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["tokenMode"] == "per-request-geelark-token"
        assert client.get("/api/rpa/tasks").status_code == 401
        response = client.get(
            "/api/rpa/tasks",
            headers={"Authorization": "Bearer workspace-geelark-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "count": 0, "tasks": []}


def test_tasks_are_isolated_by_workspace_geelark_token() -> None:
    app_module.tasks.clear()
    token_a = {"Authorization": "Bearer workspace-a-token"}
    token_b = {"Authorization": "Bearer workspace-b-token"}

    with TestClient(app_module.app) as client:
        registered = client.post(
            "/api/rpa/register",
            headers=token_a,
            json={"taskId": "task-a", "deviceId": "phone-a"},
        )
        tasks_a = client.get("/api/rpa/tasks", headers=token_a)
        tasks_b = client.get("/api/rpa/tasks", headers=token_b)
        cross_workspace_heartbeat = client.post(
            "/api/rpa/heartbeat",
            headers=token_b,
            json={
                "taskId": "task-a",
                "deviceId": "phone-a",
                "progressId": "user-a",
            },
        )
        deleted = client.delete("/api/rpa/tasks/task-a", headers=token_a)

    assert registered.status_code == 200
    assert tasks_a.json()["count"] == 1
    assert tasks_b.json()["count"] == 0
    assert cross_workspace_heartbeat.status_code == 404
    assert deleted.json()["deleted"] is True
    app_module.tasks.clear()
