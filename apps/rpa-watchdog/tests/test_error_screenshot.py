from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import httpx

from error_screenshot import capture_error_screenshot


class FakeResult:
    def __init__(
        self,
        *,
        ok: bool = True,
        data: dict[str, Any] | None = None,
        code: int = 0,
        msg: str = "success",
    ) -> None:
        self.ok = ok
        self.data = data
        self.code = code
        self.msg = msg


async def _run_test() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def api_post(
        endpoint: str,
        body: dict[str, Any],
    ) -> FakeResult:
        calls.append((endpoint, body))
        if endpoint.endswith("/screenShot"):
            return FakeResult(data={"taskId": "shot-123"})
        return FakeResult(
            data={
                "status": 2,
                "downloadLink": "https://example.test/screen.png",
            }
        )

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nmock-image",
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with tempfile.TemporaryDirectory() as directory:
            result = await capture_error_screenshot(
                api_post=api_post,
                phone_id="phone-1",
                rpa_task_id="task-1",
                reason="heartbeat_timeout",
                output_dir=Path(directory),
                download_client=client,
            )
            assert result["success"] is True, result
            assert Path(result["path"]).is_file(), result
            assert "downloadLink" not in result, result
            assert [call[0] for call in calls] == [
                "/open/v1/phone/screenShot",
                "/open/v1/phone/screenShot/result",
            ]

    async def failed_post(
        endpoint: str,
        body: dict[str, Any],
    ) -> FakeResult:
        return FakeResult(
            ok=False,
            code=42002,
            msg="phone not running",
        )

    failed = await capture_error_screenshot(
        api_post=failed_post,
        phone_id="phone-2",
        rpa_task_id="task-2",
        reason="startup_timeout",
        output_dir=Path("unused"),
    )
    assert failed["success"] is False, failed


def test_capture_error_screenshot() -> None:
    asyncio.run(_run_test())
