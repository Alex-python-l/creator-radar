from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ApiPost = Callable[[str, dict[str, Any]], Awaitable[Any]]


def _safe_filename_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return cleaned[:80] or fallback


def _failure(message: str, screenshot_task_id: str | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "screenshotTaskId": screenshot_task_id,
        "status": None,
        "fileName": None,
        "path": None,
        "sizeBytes": None,
        "capturedAt": None,
        "error": message,
    }


async def capture_error_screenshot(
    *,
    api_post: ApiPost,
    phone_id: str,
    rpa_task_id: str,
    reason: str,
    output_dir: Path,
    result_timeout_seconds: float = 20,
    poll_interval_seconds: float = 1,
    download_timeout_seconds: float = 20,
    download_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Capture and persist the phone screen before watchdog cleanup.

    The GeeLark screenshot API is asynchronous. This function creates the
    screenshot job, polls until completion, downloads the signed image URL,
    and saves the bytes locally. It never exposes the signed download URL in
    its return value or logs.
    """

    screenshot_task_id: str | None = None
    try:
        create_result = await api_post(
            "/open/v1/phone/screenShot",
            {"id": phone_id},
        )
        if not create_result.ok:
            return _failure(
                f"创建异常截图失败 code={create_result.code} msg={create_result.msg}",
            )

        create_data = create_result.data if isinstance(create_result.data, dict) else {}
        raw_screenshot_task_id = create_data.get("taskId")
        if raw_screenshot_task_id is None:
            return _failure("创建异常截图成功，但响应中没有 taskId")
        screenshot_task_id = str(raw_screenshot_task_id)

        deadline = time.monotonic() + max(1.0, result_timeout_seconds)
        download_link: str | None = None
        final_status: int | None = None

        while time.monotonic() < deadline:
            query_result = await api_post(
                "/open/v1/phone/screenShot/result",
                {"taskId": screenshot_task_id},
            )
            if not query_result.ok:
                return _failure(
                    f"查询异常截图失败 code={query_result.code} msg={query_result.msg}",
                    screenshot_task_id,
                )

            query_data = query_result.data if isinstance(query_result.data, dict) else {}
            raw_status = query_data.get("status")
            try:
                final_status = int(raw_status)
            except (TypeError, ValueError):
                final_status = None

            if final_status == 2:
                raw_download_link = query_data.get("downloadLink")
                if not isinstance(raw_download_link, str) or not raw_download_link:
                    return _failure(
                        "异常截图状态成功，但响应中没有 downloadLink",
                        screenshot_task_id,
                    )
                download_link = raw_download_link
                break

            if final_status in {0, 3}:
                return _failure(
                    f"异常截图任务执行失败 status={final_status}",
                    screenshot_task_id,
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(max(0.2, poll_interval_seconds), remaining))

        if download_link is None:
            result = _failure(
                "等待异常截图结果超时",
                screenshot_task_id,
            )
            result["status"] = final_status
            return result

        owns_client = download_client is None
        client = download_client or httpx.AsyncClient(
            timeout=httpx.Timeout(max(1.0, download_timeout_seconds)),
            follow_redirects=True,
        )
        try:
            response = await client.get(download_link)
            response.raise_for_status()
            image_bytes = response.content
            content_type = response.headers.get("content-type", "").lower()
        finally:
            if owns_client:
                await client.aclose()

        is_png = image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpeg = image_bytes.startswith(b"\xff\xd8\xff")
        if not image_bytes or not (content_type.startswith("image/") or is_png or is_jpeg):
            return _failure(
                "截图下载结果不是有效图片",
                screenshot_task_id,
            )

        extension = ".jpg" if "jpeg" in content_type or is_jpeg else ".png"
        timestamp = datetime.now(timezone.utc)
        safe_reason = _safe_filename_part(reason, "watchdog")
        safe_phone_id = _safe_filename_part(phone_id, "phone")
        safe_rpa_task_id = _safe_filename_part(rpa_task_id, "task")
        file_name = (
            f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}_"
            f"{safe_reason}_{safe_phone_id}_{safe_rpa_task_id}{extension}"
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / file_name
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            temporary.write_bytes(image_bytes)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

        return {
            "success": True,
            "screenshotTaskId": screenshot_task_id,
            "status": 2,
            "fileName": file_name,
            "path": str(destination.resolve()),
            "sizeBytes": len(image_bytes),
            "capturedAt": timestamp.isoformat(),
            "error": None,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _failure(
            f"异常截图发生异常：{type(exc).__name__}: {exc}",
            screenshot_task_id,
        )
