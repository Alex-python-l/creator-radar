from __future__ import annotations

import httpx
import pytest

from app import config
from app.services import rpa_watchdog_client as watchdog


@pytest.fixture
def configured_watchdog(monkeypatch):
    monkeypatch.setattr(config, "RPA_WATCHDOG_BASE_URL", "http://127.0.0.1:8002")
    monkeypatch.setattr(config, "RPA_WATCHDOG_TIMEOUT_SECONDS", 8.0)


def test_request_keeps_watchdog_token_server_side(configured_watchdog, monkeypatch):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(method=method, url=url, **kwargs)
        return httpx.Response(200, json={"success": True, "tasks": []})

    monkeypatch.setattr(httpx, "request", fake_request)

    result = watchdog.list_tasks("workspace-geelark-token")

    assert result == {"success": True, "tasks": []}
    assert seen["url"] == "http://127.0.0.1:8002/api/rpa/tasks"
    assert seen["headers"]["Authorization"] == "Bearer workspace-geelark-token"
    assert seen["timeout"] == 8.0


def test_upstream_token_failure_maps_to_bad_gateway(configured_watchdog, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            401,
            json={"detail": "invalid token"},
        ),
    )

    with pytest.raises(watchdog.WatchdogClientError) as exc_info:
        watchdog.list_tasks("workspace-geelark-token")

    assert exc_info.value.status_code == 502


def test_start_validation_error_is_preserved(configured_watchdog, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            422,
            json={"detail": "invalid video URL"},
        ),
    )

    with pytest.raises(watchdog.WatchdogClientError) as exc_info:
        watchdog.start_task(
            "workspace-geelark-token",
            {"deviceId": "phone-1"},
        )

    assert exc_info.value.status_code == 422
    assert str(exc_info.value) == "invalid video URL"


def test_upstream_geelark_error_message_is_preserved(configured_watchdog, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(
            502,
            json={
                "detail": {
                    "code": 40004,
                    "msg": "scheduleAt is required",
                }
            },
        ),
    )

    with pytest.raises(watchdog.WatchdogClientError) as exc_info:
        watchdog.start_task(
            "workspace-geelark-token",
            {"deviceId": "phone-1", "flowId": "flow-1"},
        )

    assert exc_info.value.status_code == 502
    assert str(exc_info.value) == "scheduleAt is required"
