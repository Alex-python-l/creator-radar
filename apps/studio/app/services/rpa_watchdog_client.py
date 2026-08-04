"""Server-side client for the standalone GeeLark RPA watchdog."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app import config


class WatchdogClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def configured() -> bool:
    return bool(config.RPA_WATCHDOG_BASE_URL)


def _request(
    method: str,
    path: str,
    *,
    geelark_token: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not configured():
        raise WatchdogClientError(
            "RPA watchdog is not configured",
            status_code=503,
        )

    try:
        response = httpx.request(
            method,
            f"{config.RPA_WATCHDOG_BASE_URL}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {geelark_token}",
            },
            json=body,
            timeout=config.RPA_WATCHDOG_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise WatchdogClientError("RPA watchdog is unavailable") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise WatchdogClientError("RPA watchdog returned invalid JSON") from exc

    if not response.is_success:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, str):
            message = detail
        elif isinstance(detail, dict):
            upstream_message = detail.get("message") or detail.get("msg")
            message = (
                str(upstream_message)
                if upstream_message
                else f"RPA watchdog returned {response.status_code}"
            )
        else:
            message = f"RPA watchdog returned {response.status_code}"
        # Do not turn an upstream token error into a Studio 401; that would
        # incorrectly log the operator out of Radar.
        passthrough = response.status_code in {404, 409, 422}
        raise WatchdogClientError(
            message,
            status_code=response.status_code if passthrough else 502,
        )
    if not isinstance(payload, dict):
        raise WatchdogClientError("RPA watchdog returned an invalid payload")
    return payload


def health(geelark_token: str) -> dict[str, Any]:
    # /health itself needs no token, but keeping one call shape makes it
    # explicit that every Ops request is scoped to the active workspace.
    return _request("GET", "/health", geelark_token=geelark_token)


def list_tasks(geelark_token: str) -> dict[str, Any]:
    return _request("GET", "/api/rpa/tasks", geelark_token=geelark_token)


def start_task(geelark_token: str, body: dict[str, Any]) -> dict[str, Any]:
    return _request(
        "POST",
        "/api/rpa/start",
        geelark_token=geelark_token,
        body=body,
    )


def stop_task(geelark_token: str, task_id: str) -> dict[str, Any]:
    encoded = quote(task_id, safe="")
    return _request(
        "POST",
        f"/api/rpa/tasks/{encoded}/stop",
        geelark_token=geelark_token,
    )


def delete_task(geelark_token: str, task_id: str) -> dict[str, Any]:
    encoded = quote(task_id, safe="")
    return _request(
        "DELETE",
        f"/api/rpa/tasks/{encoded}",
        geelark_token=geelark_token,
    )
