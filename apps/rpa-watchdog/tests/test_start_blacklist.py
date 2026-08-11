from __future__ import annotations

import asyncio
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

from fastapi import HTTPException

import main as app_module
from video_blacklist import VideoBlacklist

VIDEO_URL = "https://www.tiktok.com/@owner/video/123456789"


class FakeClient:
    def __init__(
        self,
        result: app_module.APIResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def add_rpa_task(self, **_: object) -> app_module.APIResult:
        self.calls += 1
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def api_result(*, code: int = 0, data: object = None) -> app_module.APIResult:
    return app_module.APIResult(
        endpoint="/open/v1/task/add",
        sent_trace_id="trace-1",
        http_status=200,
        code=code,
        msg="ok" if code == 0 else "rejected",
        response_trace_id="trace-2",
        data=data,
        raw={"code": code, "data": data},
    )


def start_payload(video_url: str = VIDEO_URL) -> app_module.StartRpaIn:
    return app_module.StartRpaIn(
        deviceId="phone-1",
        flowId="flow-1",
        paramMap={"视频链接": video_url},
    )


def request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(geelark_token="workspace-token"))


@contextmanager
def installed(
    blacklist: VideoBlacklist,
    client: FakeClient,
) -> Iterator[None]:
    old_blacklist = app_module.video_blacklist
    old_client = app_module.geelark_client
    try:
        app_module.video_blacklist = blacklist
        app_module.geelark_client = client
        yield
    finally:
        app_module.video_blacklist = old_blacklist
        app_module.geelark_client = old_client


def test_start_rejects_manual_blacklist_before_geelark_call() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as directory:
            blacklist = VideoBlacklist(Path(directory) / "ignored.jsonl")
            blacklist.add(VIDEO_URL, "manual test")
            client = FakeClient(api_result(data={"taskId": "task-1"}))
            with installed(blacklist, client):
                try:
                    await app_module.start_rpa(
                        start_payload(
                            "https://www.tiktok.com/@renamed/video/123456789?lang=en"
                        ),
                        request(),
                    )
                except HTTPException as exc:
                    assert exc.status_code == 409, exc
                    assert exc.detail["code"] == "VIDEO_BLACKLISTED", exc
                else:
                    raise AssertionError("命中黑名单必须返回 HTTP 409")

            assert client.calls == 0

    asyncio.run(run())


def test_successful_start_keeps_automatic_blacklist_and_blocks_duplicate() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as directory:
            blacklist = VideoBlacklist(Path(directory) / "ignored.jsonl")
            client = FakeClient(api_result(data={"taskId": "task-success"}))
            with installed(blacklist, client):
                response = await app_module.start_rpa(start_payload(), request())
                assert response["taskId"] == "task-success"
                assert response["videoBlacklist"]["source"] == "automatic_rpa_start"

                try:
                    await app_module.start_rpa(start_payload(), request())
                except HTTPException as exc:
                    assert exc.status_code == 409, exc
                else:
                    raise AssertionError("启动成功的视频必须阻止再次启动")

            assert client.calls == 1
            record = blacklist.find(VIDEO_URL)
            assert record is not None
            assert record["deviceId"] == "phone-1"
            assert record["flowId"] == "flow-1"
            assert record["reservationId"]

    asyncio.run(run())


def test_clear_geelark_rejection_rolls_back_automatic_blacklist() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as directory:
            blacklist = VideoBlacklist(Path(directory) / "ignored.jsonl")
            client = FakeClient(api_result(code=40001, data={"reason": "invalid flow"}))
            with installed(blacklist, client):
                try:
                    await app_module.start_rpa(start_payload(), request())
                except HTTPException as exc:
                    assert exc.status_code == 502, exc
                else:
                    raise AssertionError("GeeLark 拒绝创建必须返回 HTTP 502")

            assert client.calls == 1
            assert blacklist.find(VIDEO_URL) is None

    asyncio.run(run())


def test_network_failure_rolls_back_automatic_blacklist() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as directory:
            blacklist = VideoBlacklist(Path(directory) / "ignored.jsonl")
            client = FakeClient(error=app_module.GeeLarkNetworkError("network down"))
            with installed(blacklist, client):
                try:
                    await app_module.start_rpa(start_payload(), request())
                except HTTPException as exc:
                    assert exc.status_code == 502, exc
                else:
                    raise AssertionError("网络失败必须返回 HTTP 502")

            assert client.calls == 1
            assert blacklist.find(VIDEO_URL) is None

    asyncio.run(run())


def test_ambiguous_success_without_task_id_keeps_blacklist() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as directory:
            blacklist = VideoBlacklist(Path(directory) / "ignored.jsonl")
            client = FakeClient(api_result(data={"accepted": True}))
            with installed(blacklist, client):
                try:
                    await app_module.start_rpa(start_payload(), request())
                except HTTPException as exc:
                    assert exc.status_code == 502, exc
                    assert "未找到 taskId" in exc.detail["message"]
                else:
                    raise AssertionError("缺少 taskId 必须返回 HTTP 502")

            assert client.calls == 1
            assert blacklist.find(VIDEO_URL) is not None

    asyncio.run(run())
