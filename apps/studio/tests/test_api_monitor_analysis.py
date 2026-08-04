from fastapi import HTTPException

from app.api.monitor_analysis import AiNarrative


def _snapshot():
    return {
        "generatedAt": "2026-07-19T12:00:00Z",
        "scope": {"groupId": None, "platform": "all", "range": "7d"},
        "health": {
            "status": "issues", "totalAccounts": 2,
            "successAttempts": 1, "failedAttempts": 1, "successRate": 0.5,
            "currentIssues": [], "processing": [], "recovered": [],
            "errorBreakdown": [],
        },
        "publishing": {
            "postedCount": 1, "silentCount": 1, "uncertainCount": 0,
            "postedAccounts": [], "silentAccounts": [], "uncertainAccounts": [],
        },
        "performance": {"cohorts": []},
    }


def _login(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})


def test_monitor_analysis_requires_login(client, monkeypatch):
    from app.api import monitor_analysis as module

    monkeypatch.setattr(module, "_fetch_snapshot", lambda *_: (_ for _ in ()).throw(
        AssertionError("snapshot should not be requested")))
    response = client.post("/api/monitor/analysis", json={
        "platform": "all", "range": "7d", "locale": "zh",
    })
    assert response.status_code == 401


def test_monitor_analysis_missing_key_keeps_deterministic_report(client, monkeypatch):
    from app.api import monitor_analysis as module
    from app import workspace_secrets

    captured = {}
    monkeypatch.setattr(module, "_fetch_snapshot", lambda cookie, payload: (
        captured.update({"cookie": cookie, "payload": payload}) or _snapshot()))
    monkeypatch.setattr(workspace_secrets, "get_secret", lambda *_: None)
    monkeypatch.setattr(module, "_generate_ai", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("Gemini must not run without a key")))
    _login(client)

    response = client.post("/api/monitor/analysis", json={
        "groupId": 3, "platform": "tiktok", "range": "7d", "locale": "zh",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["health"]["totalAccounts"] == 2
    assert body["ai"] == {
        "status": "missing_key", "narrative": None, "recommendations": [],
    }
    assert captured["payload"] == {"groupId": 3, "platform": "tiktok", "range": "7d"}
    assert captured["cookie"]


def test_monitor_analysis_uses_workspace_ai_config(client, monkeypatch):
    from app.api import monitor_analysis as module
    from app import workspace_secrets

    captured = {}
    monkeypatch.setattr(module, "_fetch_snapshot", lambda *_: _snapshot())
    monkeypatch.setattr(workspace_secrets, "get_secret", lambda *_: "workspace-key")

    def fake_generate(snapshot, **kwargs):
        captured.update(kwargs)
        return AiNarrative(
            healthSummary="抓取有异常",
            publishingSummary="一个账号发布",
            performanceSummary="样本不足",
            recommendations=["检查失败账号"],
        )

    monkeypatch.setattr(module, "_generate_ai", fake_generate)
    _login(client)
    response = client.post("/api/monitor/analysis", json={
        "platform": "all", "range": "30d", "locale": "zh",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["ai"]["status"] == "ready"
    assert body["ai"]["narrative"]["healthSummary"] == "抓取有异常"
    assert body["ai"]["recommendations"] == ["检查失败账号"]
    assert captured["api_key"] == "workspace-key"
    assert captured["locale"] == "zh"
    assert captured["thinking_budget"] == 0


def test_monitor_analysis_invalid_ai_json_degrades_to_facts(client, monkeypatch):
    from app.api import monitor_analysis as module
    from app import workspace_secrets

    monkeypatch.setattr(module, "_fetch_snapshot", lambda *_: _snapshot())
    monkeypatch.setattr(workspace_secrets, "get_secret", lambda *_: "workspace-key")
    monkeypatch.setattr(module, "_generate_ai", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(ValueError("invalid JSON")))
    _login(client)
    response = client.post("/api/monitor/analysis", json={
        "platform": "all", "range": "7d", "locale": "en",
    })

    assert response.status_code == 200
    assert response.json()["health"]["totalAccounts"] == 2
    assert response.json()["ai"] == {
        "status": "failed", "narrative": None, "recommendations": [],
    }


def test_monitor_analysis_forwards_group_not_found(client, monkeypatch):
    from app.api import monitor_analysis as module

    def not_found(*_args):
        raise HTTPException(status_code=404, detail="group not found")

    monkeypatch.setattr(module, "_fetch_snapshot", not_found)
    _login(client)
    response = client.post("/api/monitor/analysis", json={
        "groupId": 999, "platform": "all", "range": "7d", "locale": "zh",
    })
    assert response.status_code == 404


def test_monitor_analysis_rejects_invalid_scope(client):
    _login(client)
    response = client.post("/api/monitor/analysis", json={
        "platform": "unknown", "range": "90d", "locale": "zh",
    })
    assert response.status_code == 422
