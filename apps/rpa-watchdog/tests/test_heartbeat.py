from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import main as app_module


def request_for(token: str = "workspace-token"):
    return SimpleNamespace(state=SimpleNamespace(geelark_token=token))


def test_recent_user_cache_hit_schedules_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        app_module.tasks.clear()
        app_module.cleanup_jobs.clear()
        cleanup_calls: list[tuple[str, str]] = []

        async def fake_cleanup(task_id: str, reason: str) -> None:
            cleanup_calls.append((task_id, reason))

        monkeypatch.setattr(app_module, "cleanup_task", fake_cleanup)
        monkeypatch.setattr(app_module.config, "RECENT_USER_CACHE_SIZE", 15)
        monkeypatch.setattr(app_module.config, "RECENT_USER_HIT_THRESHOLD", 3)

        def payload(progress_id: str) -> app_module.HeartbeatIn:
            return app_module.HeartbeatIn(
                taskId="task-1",
                deviceId="phone-1",
                progressId=progress_id,
            )

        request = request_for()
        startup = await app_module.heartbeat(payload("__RPA_START__"), request)
        assert startup["startupSignal"] is True
        assert startup["status"] == "running"

        assert (await app_module.heartbeat(payload("@User_A"), request))["isNewProgress"] is True
        assert (await app_module.heartbeat(payload("user_b"), request))["isNewProgress"] is True

        first_hit = await app_module.heartbeat(payload("user_a"), request)
        assert first_hit["cacheHit"] is True
        assert first_hit["cleanupScheduled"] is False

        second_hit = await app_module.heartbeat(payload("@USER_B"), request)
        assert second_hit["cacheHit"] is True
        assert second_hit["cleanupScheduled"] is False

        third_hit = await app_module.heartbeat(payload("user_a"), request)
        assert third_hit["cacheHit"] is True
        assert third_hit["cleanupScheduled"] is True
        assert third_hit["status"] == "stopping"

        await asyncio.sleep(0)
        assert cleanup_calls == [("task-1", "recent_user_cache_hit")]
        app_module.tasks.clear()
        app_module.cleanup_jobs.clear()

    asyncio.run(scenario())


def test_task_id_cannot_switch_device() -> None:
    async def scenario() -> None:
        app_module.tasks.clear()
        await app_module.register_task(
            app_module.RegisterTaskIn(taskId="task-1", deviceId="phone-1"),
            request_for(),
        )

        with pytest.raises(HTTPException) as exc_info:
            await app_module.heartbeat(
                app_module.HeartbeatIn(
                    taskId="task-1",
                    deviceId="phone-2",
                    progressId="user-a",
                ),
                request_for(),
            )

        assert exc_info.value.status_code == 409
        app_module.tasks.clear()

    asyncio.run(scenario())


def test_extract_created_task_id_from_nested_response() -> None:
    result = app_module.APIResult(
        endpoint="/open/v1/task/rpa/add",
        sent_trace_id="trace",
        http_status=200,
        code=0,
        msg="ok",
        response_trace_id=None,
        data={"items": [{"taskId": "nested-task"}]},
        raw={},
    )

    assert app_module.extract_created_task_id(result) == "nested-task"


def test_add_rpa_task_defaults_schedule_at_to_now(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        captured: dict[str, object] = {}
        client = app_module.GeeLarkClient()

        async def fake_post(endpoint: str, body: dict, api_token: str):
            captured.update(endpoint=endpoint, body=body, api_token=api_token)
            return object()

        monkeypatch.setattr(app_module.time, "time", lambda: 1_785_727_200.9)
        monkeypatch.setattr(client, "post", fake_post)
        try:
            await client.add_rpa_task(
                phone_id="phone-1",
                flow_id="flow-1",
                schedule_at=None,
                param_map={"视频链接": "https://example.com/video"},
                api_token="workspace-token",
            )
        finally:
            await client.close()

        assert captured["endpoint"] == "/open/v1/task/rpa/add"
        assert captured["api_token"] == "workspace-token"
        assert captured["body"] == {
            "id": "phone-1",
            "flowId": "flow-1",
            "paramMap": {
                "视频链接": "https://example.com/video",
                "hasCommentButton": True,
            },
            "scheduleAt": 1_785_727_200,
        }

    asyncio.run(scenario())
