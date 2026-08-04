from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

import main as app_module
from video_blacklist import VideoBlacklist


class FailIfCalledClient:
    called = False

    async def add_rpa_task(self, **_: object) -> object:
        self.called = True
        raise AssertionError("命中黑名单后不应调用 GeeLark")


async def _run_test() -> None:
    old_blacklist = app_module.video_blacklist
    old_client = app_module.geelark_client
    try:
        with tempfile.TemporaryDirectory() as directory:
            blacklist = VideoBlacklist(Path(directory) / "ignored.jsonl")
            blacklist.add("https://www.tiktok.com/@owner/video/123456789")
            fake_client = FailIfCalledClient()
            app_module.video_blacklist = blacklist
            app_module.geelark_client = fake_client

            payload = app_module.StartRpaIn(
                deviceId="phone-1",
                flowId="flow-1",
                paramMap={"视频链接": ("https://www.tiktok.com/@renamed/video/123456789?lang=en")},
            )
            request = SimpleNamespace(state=SimpleNamespace(geelark_token="workspace-token"))
            try:
                await app_module.start_rpa(payload, request)
            except HTTPException as exc:
                assert exc.status_code == 409, exc
                assert exc.detail["code"] == "VIDEO_BLACKLISTED", exc
            else:
                raise AssertionError("命中黑名单必须返回 HTTP 409")

            assert fake_client.called is False
    finally:
        app_module.video_blacklist = old_blacklist
        app_module.geelark_client = old_client


def test_start_rejects_blacklisted_video_before_geelark_call() -> None:
    asyncio.run(_run_test())
