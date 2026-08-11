from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from error_screenshot import capture_error_screenshot
from video_blacklist import InvalidVideoUrl, VideoBlacklist

BASE_DIR = Path(__file__).resolve().parent
COMMENT_USERS_FILE = BASE_DIR / "comment_users.txt"
LOG_FILE = BASE_DIR / "rpa_watchdog.log"
ERROR_SCREENSHOT_DIR = Path(config.ERROR_SCREENSHOT_DIR)
if not ERROR_SCREENSHOT_DIR.is_absolute():
    ERROR_SCREENSHOT_DIR = BASE_DIR / ERROR_SCREENSHOT_DIR
VIDEO_BLACKLIST_FILE = Path(config.VIDEO_BLACKLIST_FILE)
if not VIDEO_BLACKLIST_FILE.is_absolute():
    VIDEO_BLACKLIST_FILE = BASE_DIR / VIDEO_BLACKLIST_FILE
video_blacklist = VideoBlacklist(VIDEO_BLACKLIST_FILE)

TERMINAL_TASK_STATUSES = {3, 4, 7}
FINISHED_LOCAL_STATUSES = {"stopped", "stop_failed"}

logger = logging.getLogger("rpa_watchdog")
logger.setLevel(logging.INFO)
if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


class CommentUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    videoUrl: str | None = None


class HeartbeatIn(BaseModel):
    # 必须传 GeeLark 创建任务接口返回的真实 taskId
    taskId: str = Field(min_length=1, max_length=128)
    # 必须传 GeeLark 云手机真实 ID
    deviceId: str = Field(min_length=1, max_length=128)
    progressId: str | None = Field(default=None, max_length=500)
    # 兼容旧请求体
    username: str | None = Field(default=None, max_length=500)


class RegisterTaskIn(BaseModel):
    """在首个心跳到达前，先把真实任务登记到看门狗。"""

    taskId: str = Field(min_length=1, max_length=128)
    deviceId: str = Field(min_length=1, max_length=128)
    source: str = Field(default="manual_register", min_length=1, max_length=100)


class StartRpaIn(BaseModel):
    """由服务器创建 GeeLark RPA，并立即开始启动阶段计时。"""

    deviceId: str = Field(min_length=1, max_length=128)
    flowId: str = Field(min_length=1, max_length=128)
    scheduleAt: int | None = None
    paramMap: dict[str, Any] = Field(default_factory=dict)


@dataclass
class APIResult:
    endpoint: str
    sent_trace_id: str
    http_status: int | None
    code: int | None
    msg: str
    response_trace_id: str | None
    data: Any
    raw: Any

    @property
    def ok(self) -> bool:
        return self.code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "sentTraceId": self.sent_trace_id,
            "httpStatus": self.http_status,
            "code": self.code,
            "msg": self.msg,
            "responseTraceId": self.response_trace_id,
            "data": self.data,
            "raw": self.raw,
        }


@dataclass
class TaskState:
    task_id: str
    device_id: str
    geelark_api_token: str = field(repr=False)
    last_progress_id: str = ""
    registered_monotonic: float = field(default_factory=time.monotonic)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_new_progress_monotonic: float = field(default_factory=time.monotonic)
    last_new_progress_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_request_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    first_heartbeat_at: datetime | None = None
    first_heartbeat_received: bool = False
    first_user_received: bool = False
    startup_source: str = "heartbeat_auto_register"
    status: str = "starting"
    heartbeat_count: int = 0
    new_progress_count: int = 0
    recent_users: list[str] = field(default_factory=list)
    consecutive_cache_hits: int = 0
    total_cache_hits: int = 0
    cleanup_reason: str | None = None
    cleanup_error: str | None = None
    geelark_task_status: int | None = None
    cancel_result: dict[str, Any] | None = None
    phone_stop_result: dict[str, Any] | None = None
    create_task_result: dict[str, Any] | None = None
    error_screenshot_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "taskId": self.task_id,
            "deviceId": self.device_id,
            "registeredAt": self.registered_at.isoformat(),
            "secondsSinceRegistered": round(max(0.0, now - self.registered_monotonic), 2),
            "startupSource": self.startup_source,
            "firstHeartbeatReceived": self.first_heartbeat_received,
            "firstHeartbeatAt": (
                self.first_heartbeat_at.isoformat() if self.first_heartbeat_at is not None else None
            ),
            "firstUserReceived": self.first_user_received,
            "lastProgressId": self.last_progress_id,
            "lastNewProgressAt": self.last_new_progress_at.isoformat(),
            "lastRequestAt": self.last_request_at.isoformat(),
            "secondsSinceNewProgress": (
                round(max(0.0, now - self.last_new_progress_monotonic), 2)
                if self.first_heartbeat_received
                else None
            ),
            "status": self.status,
            "heartbeatCount": self.heartbeat_count,
            "newProgressCount": self.new_progress_count,
            "recentUsers": list(self.recent_users),
            "recentUserCount": len(self.recent_users),
            "recentUserCacheSize": config.RECENT_USER_CACHE_SIZE,
            "consecutiveCacheHits": self.consecutive_cache_hits,
            "cacheHitThreshold": config.RECENT_USER_HIT_THRESHOLD,
            "totalCacheHits": self.total_cache_hits,
            "cleanupReason": self.cleanup_reason,
            "cleanupError": self.cleanup_error,
            "geelarkTaskStatus": self.geelark_task_status,
            "createTaskResult": self.create_task_result,
            "cancelResult": self.cancel_result,
            "phoneStopResult": self.phone_stop_result,
            "errorScreenshot": self.error_screenshot_result,
        }


class GeeLarkNetworkError(RuntimeError):
    pass


class GeeLarkClient:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=config.GEELARK_BASE_URL,
            timeout=httpx.Timeout(config.GEELARK_HTTP_TIMEOUT_SECONDS),
        )

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _trace_id() -> str:
        return str(uuid.uuid4()).upper()

    @staticmethod
    def _int_code(value: Any) -> int | None:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    async def post(
        self,
        endpoint: str,
        body: dict[str, Any],
        api_token: str,
    ) -> APIResult:
        trace_id = self._trace_id()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_token}",
            "traceId": trace_id,
        }

        try:
            response = await self.client.post(
                endpoint,
                headers=headers,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise GeeLarkNetworkError(f"GeeLark 请求失败 endpoint={endpoint}: {exc}") from exc

        try:
            payload: Any = response.json()
        except ValueError:
            payload = {
                "code": None,
                "msg": response.text,
                "data": None,
            }

        if isinstance(payload, dict):
            code = self._int_code(payload.get("code"))
            msg = str(payload.get("msg", ""))
            data = payload.get("data")
            response_trace_id = payload.get("traceId")
        else:
            code = None
            msg = "响应不是 JSON 对象"
            data = None
            response_trace_id = None

        result = APIResult(
            endpoint=endpoint,
            sent_trace_id=trace_id,
            http_status=response.status_code,
            code=code,
            msg=msg,
            response_trace_id=(str(response_trace_id) if response_trace_id is not None else None),
            data=data,
            raw=payload,
        )

        fail_details = data.get("failDetails") if isinstance(data, dict) else None
        log_func = logger.info if result.ok else logger.warning
        log_func(
            "GeeLark响应 endpoint=%s http=%s code=%s msg=%s "
            "sentTraceId=%s responseTraceId=%s failDetails=%s",
            endpoint,
            response.status_code,
            result.code,
            result.msg,
            result.sent_trace_id,
            result.response_trace_id,
            fail_details,
        )
        return result

    async def cancel_task(self, task_id: str, api_token: str) -> APIResult:
        # 修改操作：只请求一次
        return await self.post(
            "/open/v1/task/cancel",
            {"ids": [task_id]},
            api_token,
        )

    def task_query_body(self, task_id: str) -> dict[str, Any]:
        style = config.TASK_QUERY_BODY_STYLE
        if style == "ids":
            return {"ids": [task_id]}
        if style == "taskId":
            return {"taskId": task_id}
        if style == "id":
            return {"id": task_id}
        raise ValueError("GEELARK_TASK_QUERY_BODY_STYLE 只支持 ids、taskId、id")

    async def query_task(self, task_id: str, api_token: str) -> APIResult:
        return await self.post(
            "/open/v1/task/query",
            self.task_query_body(task_id),
            api_token,
        )

    async def stop_phone(self, phone_id: str, api_token: str) -> APIResult:
        return await self.post(
            "/open/v1/phone/stop",
            {"ids": [phone_id]},
            api_token,
        )

    async def add_rpa_task(
        self,
        phone_id: str,
        flow_id: str,
        schedule_at: int | None,
        param_map: dict[str, Any],
        api_token: str,
    ) -> APIResult:
        normalized_param_map = dict(param_map)
        normalized_param_map.setdefault("hasCommentButton", True)
        body: dict[str, Any] = {
            "id": phone_id,
            "flowId": flow_id,
            "paramMap": normalized_param_map,
            "scheduleAt": int(schedule_at if schedule_at is not None else time.time()),
        }
        return await self.post("/open/v1/task/rpa/add", body, api_token)


def collect_statuses(
    value: Any,
    task_id: str,
    output: list[tuple[bool, int]],
) -> None:
    if isinstance(value, dict):
        object_id = value.get("taskId", value.get("id"))
        status = value.get("status")
        if isinstance(status, str):
            try:
                status = int(status)
            except ValueError:
                status = None
        if isinstance(status, int):
            output.append(
                (
                    object_id is not None and str(object_id) == str(task_id),
                    status,
                )
            )
        for child in value.values():
            collect_statuses(child, task_id, output)
    elif isinstance(value, list):
        for child in value:
            collect_statuses(child, task_id, output)


def extract_task_status(result: APIResult, task_id: str) -> int | None:
    candidates: list[tuple[bool, int]] = []
    collect_statuses(result.data, task_id, candidates)

    for matches_id, status in candidates:
        if matches_id:
            return status

    unique_statuses = {status for _, status in candidates}
    if len(unique_statuses) == 1:
        return next(iter(unique_statuses))
    return None


def extract_created_task_id(result: APIResult) -> str | None:
    """从创建任务响应中提取 taskId，兼容常见嵌套结构。"""
    data = result.data
    if isinstance(data, dict):
        direct = data.get("taskId")
        if direct is not None and str(direct).strip():
            return str(direct).strip()

    def walk(value: Any) -> str | None:
        if isinstance(value, dict):
            if "taskId" in value and str(value["taskId"]).strip():
                return str(value["taskId"]).strip()
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return None

    return walk(data)


def new_registered_state(
    task_id: str,
    device_id: str,
    source: str,
    api_token: str,
) -> TaskState:
    now_utc = datetime.now(timezone.utc)
    now_mono = time.monotonic()
    return TaskState(
        task_id=task_id,
        device_id=device_id,
        geelark_api_token=api_token,
        registered_monotonic=now_mono,
        registered_at=now_utc,
        last_new_progress_monotonic=now_mono,
        last_new_progress_at=now_utc,
        last_request_at=now_utc,
        startup_source=source,
        status="starting",
    )


tasks: dict[str, TaskState] = {}
tasks_lock = asyncio.Lock()
cleanup_jobs: set[asyncio.Task[Any]] = set()
watchdog_job: asyncio.Task[Any] | None = None
geelark_client: GeeLarkClient | None = None


def get_progress_id(payload: HeartbeatIn) -> str:
    value = payload.progressId or payload.username
    if value is None or not value.strip():
        raise HTTPException(
            status_code=422,
            detail="progressId 和 username 至少提供一个非空值",
        )
    return value.strip()


def normalize_username(value: str) -> str:
    """用户名比较时忽略首尾空格、开头 @ 和大小写。"""
    normalized = value.strip().lstrip("@").strip().lower()
    if not normalized:
        raise HTTPException(status_code=422, detail="用户名不能为空")
    return normalized


def load_usernames() -> set[str]:
    if not COMMENT_USERS_FILE.exists():
        return set()
    return {
        line.strip().lower()
        for line in COMMENT_USERS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


saved_usernames = load_usernames()
saved_usernames_lock = asyncio.Lock()


def track_cleanup_job(job: asyncio.Task[Any]) -> None:
    cleanup_jobs.add(job)
    job.add_done_callback(cleanup_jobs.discard)


def owns_task(state: TaskState, api_token: str) -> bool:
    return secrets.compare_digest(state.geelark_api_token, api_token)


async def poll_until_terminal(
    task_id: str,
    api_token: str,
) -> tuple[int | None, str | None]:
    assert geelark_client is not None
    deadline = time.monotonic() + config.TASK_TERMINAL_TIMEOUT_SECONDS
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            result = await geelark_client.query_task(task_id, api_token)
            if not result.ok:
                last_error = (
                    f"task/query code={result.code} msg={result.msg} "
                    f"traceId={result.response_trace_id}"
                )
            else:
                status = extract_task_status(result, task_id)
                if status is None:
                    last_error = "task/query 成功，但无法从 data 中解析 status"
                else:
                    logger.info(
                        "GeeLark任务状态 taskId=%s status=%s",
                        task_id,
                        status,
                    )
                    if status in TERMINAL_TASK_STATUSES:
                        return status, None
        except (GeeLarkNetworkError, ValueError) as exc:
            last_error = str(exc)
            logger.warning(
                "查询任务状态异常 taskId=%s error=%s",
                task_id,
                exc,
            )

        await asyncio.sleep(config.TASK_QUERY_INTERVAL_SECONDS)

    return None, last_error or "等待任务进入终态超时"


async def stop_phone_limited(
    phone_id: str,
    api_token: str,
) -> tuple[bool, APIResult | None, str | None]:
    assert geelark_client is not None
    last_result: APIResult | None = None

    for attempt in range(1, config.PHONE_STOP_MAX_ATTEMPTS + 1):
        try:
            result = await geelark_client.stop_phone(phone_id, api_token)
            last_result = result
        except GeeLarkNetworkError as exc:
            # 网络异常时无法确认服务端是否收到修改请求，不盲目重发
            return False, last_result, (f"phone/stop 网络异常，为避免重复修改请求未自动重试：{exc}")

        if result.ok:
            if isinstance(result.data, dict):
                success_amount = result.data.get("successAmount")
                fail_details = result.data.get("failDetails")
                if isinstance(success_amount, int) and success_amount <= 0 and fail_details:
                    return False, result, ("phone/stop code=0，但 successAmount=0")
            return True, result, None

        if result.code == 43005:
            if attempt >= config.PHONE_STOP_MAX_ATTEMPTS:
                return False, result, ("设备仍被任务占用（43005），已达到最大重试次数")
            logger.warning(
                "设备未释放，等待后重试 phoneId=%s attempt=%s/%s",
                phone_id,
                attempt,
                config.PHONE_STOP_MAX_ATTEMPTS,
            )
            await asyncio.sleep(config.PHONE_STOP_RETRY_DELAY_SECONDS)
            continue

        if result.code == 43006:
            return False, result, ("云手机正在被远程连接（43006），不自动重试")

        return (
            False,
            result,
            (
                f"phone/stop 失败 code={result.code} msg={result.msg} "
                f"traceId={result.response_trace_id}"
            ),
        )

    return False, last_result, "关闭云手机失败"


async def cleanup_task(task_id: str, reason: str) -> None:
    assert geelark_client is not None

    async with tasks_lock:
        state = tasks.get(task_id)
        if state is None:
            logger.warning("本地任务不存在 taskId=%s", task_id)
            return
        if state.status in FINISHED_LOCAL_STATUSES:
            return
        state.status = "stopping"
        state.cleanup_reason = reason
        phone_id = state.device_id
        api_token = state.geelark_api_token

    logger.warning(
        "开始清理 taskId=%s phoneId=%s reason=%s",
        task_id,
        phone_id,
        reason,
    )

    # 1. 云手机仍在运行时，先保存看门狗触发现场。
    if config.ERROR_SCREENSHOT_ENABLED:
        screenshot_result = await capture_error_screenshot(
            api_post=lambda endpoint, body: geelark_client.post(
                endpoint,
                body,
                api_token,
            ),
            phone_id=phone_id,
            rpa_task_id=task_id,
            reason=reason,
            output_dir=ERROR_SCREENSHOT_DIR,
            result_timeout_seconds=(config.ERROR_SCREENSHOT_TIMEOUT_SECONDS),
            poll_interval_seconds=(config.ERROR_SCREENSHOT_POLL_INTERVAL_SECONDS),
            download_timeout_seconds=(config.ERROR_SCREENSHOT_DOWNLOAD_TIMEOUT_SECONDS),
        )
        async with tasks_lock:
            state = tasks.get(task_id)
            if state:
                state.error_screenshot_result = screenshot_result

        if screenshot_result["success"]:
            logger.warning(
                "异常截图已保存 taskId=%s phoneId=%s reason=%s path=%s",
                task_id,
                phone_id,
                reason,
                screenshot_result["path"],
            )
        else:
            logger.error(
                "异常截图失败 taskId=%s phoneId=%s reason=%s error=%s",
                task_id,
                phone_id,
                reason,
                screenshot_result["error"],
            )
    else:
        logger.info("异常截图功能已禁用 taskId=%s", task_id)

    # 2. 取消 RPA，只发一次
    try:
        cancel_result = await geelark_client.cancel_task(task_id, api_token)
        async with tasks_lock:
            state = tasks.get(task_id)
            if state:
                state.cancel_result = cancel_result.to_dict()

        if cancel_result.ok:
            logger.info("取消任务请求成功 taskId=%s", task_id)
        elif cancel_result.code == 48001:
            logger.info(
                "任务不允许取消（48001），继续查询是否已终态 taskId=%s",
                task_id,
            )
        else:
            logger.warning(
                "取消任务失败，仍继续查询 taskId=%s code=%s msg=%s traceId=%s",
                task_id,
                cancel_result.code,
                cancel_result.msg,
                cancel_result.response_trace_id,
            )
    except GeeLarkNetworkError as exc:
        logger.exception("取消任务网络异常 taskId=%s", task_id)
        async with tasks_lock:
            state = tasks.get(task_id)
            if state:
                state.cleanup_error = f"取消任务网络异常：{exc}"

    # 3. 无论取消结果如何，都查询终态
    terminal_status, query_error = await poll_until_terminal(task_id, api_token)
    if terminal_status is None:
        error = f"未能确认任务进入状态 3/4/7，因此不调用 phone/stop。详情：{query_error}"
        logger.error("%s taskId=%s", error, task_id)
        async with tasks_lock:
            state = tasks.get(task_id)
            if state:
                state.status = "stop_failed"
                state.cleanup_error = error
        return

    async with tasks_lock:
        state = tasks.get(task_id)
        if state:
            state.geelark_task_status = terminal_status

    # 4. 任务终态后关闭云手机
    success, phone_result, phone_error = await stop_phone_limited(phone_id, api_token)

    async with tasks_lock:
        state = tasks.get(task_id)
        if state:
            state.geelark_task_status = terminal_status
            state.phone_stop_result = phone_result.to_dict() if phone_result is not None else None
            if success:
                state.status = "stopped"
                state.cleanup_error = None
            else:
                state.status = "stop_failed"
                state.cleanup_error = phone_error

    if success:
        logger.warning(
            "清理成功 taskId=%s taskStatus=%s phoneId=%s",
            task_id,
            terminal_status,
            phone_id,
        )
    else:
        logger.error(
            "关闭云手机失败 taskId=%s phoneId=%s error=%s",
            task_id,
            phone_id,
            phone_error,
        )


async def watchdog_loop() -> None:
    logger.info(
        "看门狗启动 startupTimeout=%ss firstUserTimeout=%ss progressTimeout=%ss interval=%ss",
        config.RPA_STARTUP_TIMEOUT_SECONDS,
        config.FIRST_USER_TIMEOUT_SECONDS,
        config.WATCHDOG_TIMEOUT_SECONDS,
        config.WATCHDOG_CHECK_INTERVAL_SECONDS,
    )
    try:
        while True:
            await asyncio.sleep(config.WATCHDOG_CHECK_INTERVAL_SECONDS)
            now = time.monotonic()
            expired: list[tuple[str, str]] = []

            async with tasks_lock:
                for state in tasks.values():
                    if state.status not in {"starting", "running"}:
                        continue

                    reason: str | None = None
                    if (
                        not state.first_heartbeat_received
                        and now - state.registered_monotonic > config.RPA_STARTUP_TIMEOUT_SECONDS
                    ):
                        reason = "startup_timeout"
                    elif (
                        state.first_heartbeat_received
                        and not state.first_user_received
                        and now - state.last_new_progress_monotonic
                        > config.FIRST_USER_TIMEOUT_SECONDS
                    ):
                        reason = "first_user_timeout"
                    elif (
                        state.first_user_received
                        and now - state.last_new_progress_monotonic
                        > config.WATCHDOG_TIMEOUT_SECONDS
                    ):
                        reason = "heartbeat_timeout"

                    if reason is not None:
                        state.status = "stopping"
                        state.cleanup_reason = reason
                        expired.append((state.task_id, reason))

            for task_id, reason in expired:
                logger.warning(
                    "看门狗触发 taskId=%s reason=%s",
                    task_id,
                    reason,
                )
                job = asyncio.create_task(cleanup_task(task_id, reason))
                track_cleanup_job(job)
    except asyncio.CancelledError:
        logger.info("看门狗停止")
        raise
    except Exception:
        logger.exception("看门狗异常退出")
        raise


async def cleanup_on_shutdown() -> None:
    if not config.CLEANUP_ON_SHUTDOWN:
        logger.info("服务关闭清理已禁用")
        return

    async with tasks_lock:
        # 只为仍处于 running 的任务新建清理作业。
        # 已处于 stopping 的任务已有 cleanup_job，稍后统一等待，
        # 避免重复发送 cancel/phone-stop 修改请求。
        task_ids = [
            state.task_id for state in tasks.values() if state.status in {"starting", "running"}
        ]
        for task_id in task_ids:
            tasks[task_id].status = "stopping"
            tasks[task_id].cleanup_reason = "server_shutdown"

    if not task_ids:
        logger.info("服务关闭时无活跃任务")
        return

    logger.warning("服务关闭，清理 %s 个活跃任务", len(task_ids))
    jobs = [asyncio.create_task(cleanup_task(task_id, "server_shutdown")) for task_id in task_ids]
    try:
        await asyncio.wait_for(
            asyncio.gather(*jobs, return_exceptions=True),
            timeout=config.SHUTDOWN_CLEANUP_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(
            "服务关闭清理超过 %ss，停止等待",
            config.SHUTDOWN_CLEANUP_TIMEOUT_SECONDS,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global watchdog_job, geelark_client
    geelark_client = GeeLarkClient()
    watchdog_job = asyncio.create_task(watchdog_loop())

    try:
        yield
    finally:
        # Ctrl+C/SIGTERM 时 Uvicorn 会进入这里
        if watchdog_job is not None:
            watchdog_job.cancel()
            try:
                await watchdog_job
            except asyncio.CancelledError:
                pass

        await cleanup_on_shutdown()

        if cleanup_jobs:
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *list(cleanup_jobs),
                        return_exceptions=True,
                    ),
                    timeout=config.SHUTDOWN_CLEANUP_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error("已有清理任务未在关机期限内完成")

        if geelark_client is not None:
            await geelark_client.close()


app = FastAPI(
    title="GeeLark RPA Heartbeat Watchdog",
    version="3.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def require_geelark_token(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return JSONResponse(
            status_code=401,
            content={"detail": "缺少 GeeLark Bearer token"},
        )
    request.state.geelark_token = token.strip()
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "success": True,
        "service": "geelark-rpa-watchdog",
        "startupTimeoutSeconds": config.RPA_STARTUP_TIMEOUT_SECONDS,
        "firstUserTimeoutSeconds": config.FIRST_USER_TIMEOUT_SECONDS,
        "watchdogTimeoutSeconds": config.WATCHDOG_TIMEOUT_SECONDS,
        "watchdogCheckIntervalSeconds": (config.WATCHDOG_CHECK_INTERVAL_SECONDS),
        "taskQueryBodyStyle": config.TASK_QUERY_BODY_STYLE,
        "recentUserCacheSize": config.RECENT_USER_CACHE_SIZE,
        "recentUserHitThreshold": config.RECENT_USER_HIT_THRESHOLD,
        "tokenMode": "per-request-geelark-token",
        "errorScreenshotEnabled": config.ERROR_SCREENSHOT_ENABLED,
        "errorScreenshotDirectory": str(ERROR_SCREENSHOT_DIR),
        "videoBlacklistFile": str(VIDEO_BLACKLIST_FILE),
        "videoBlacklistCount": len(video_blacklist.list()),
    }


# 保留旧接口
@app.post("/api/tiktok/comment-users")
async def save_comment_user(payload: CommentUserIn) -> dict[str, Any]:
    username = payload.username.strip().lstrip("@")
    if not username:
        raise HTTPException(status_code=422, detail="username 不能为空")

    normalized = username.lower()
    async with saved_usernames_lock:
        if normalized in saved_usernames:
            return {
                "success": True,
                "saved": False,
                "username": username,
                "videoUrl": payload.videoUrl,
                "message": "用户名已存在",
            }
        with COMMENT_USERS_FILE.open("a", encoding="utf-8") as file:
            file.write(username + "\n")
        saved_usernames.add(normalized)

    return {
        "success": True,
        "saved": True,
        "username": username,
        "videoUrl": payload.videoUrl,
    }


@app.post("/api/tiktok/rpa/register")
@app.post("/api/rpa/register")
async def register_task(payload: RegisterTaskIn, request: Request) -> dict[str, Any]:
    """先登记任务，使首个心跳之前也能进行启动超时检测。"""
    api_token = request.state.geelark_token
    async with tasks_lock:
        existing = tasks.get(payload.taskId)
        if existing is not None and existing.status not in FINISHED_LOCAL_STATUSES:
            if not owns_task(existing, api_token):
                raise HTTPException(status_code=409, detail="taskId 已被登记")
            if existing.device_id != payload.deviceId:
                raise HTTPException(
                    status_code=409,
                    detail="相同 taskId 已绑定另一个 deviceId",
                )
            return {
                "success": True,
                "registered": False,
                "task": existing.to_dict(),
                "message": "任务已登记，无需重复登记",
            }

        state = new_registered_state(
            payload.taskId,
            payload.deviceId,
            payload.source,
            api_token,
        )
        tasks[payload.taskId] = state
        data = state.to_dict()

    logger.info(
        "任务预登记 taskId=%s deviceId=%s source=%s",
        payload.taskId,
        payload.deviceId,
        payload.source,
    )
    return {
        "success": True,
        "registered": True,
        "task": data,
        "message": (f"已开始 {config.RPA_STARTUP_TIMEOUT_SECONDS} 秒启动超时计时"),
    }


@app.post("/api/tiktok/rpa/start")
@app.post("/api/rpa/start")
async def start_rpa(payload: StartRpaIn, request: Request) -> dict[str, Any]:
    """由服务器创建 RPA；拿到 taskId 后立即预登记并开始计时。"""
    assert geelark_client is not None
    api_token = request.state.geelark_token
    video_url = payload.paramMap.get("视频链接")
    blacklist_record: dict[str, Any] | None = None
    reservation_id: str | None = None
    if video_url is not None:
        if not isinstance(video_url, str) or not video_url.strip():
            raise HTTPException(
                status_code=422,
                detail="paramMap.视频链接 必须是非空字符串",
            )
        reservation_id = str(uuid.uuid4())
        try:
            added, blacklist_record = video_blacklist.add(
                video_url,
                "RPA 创建前自动占用；创建成功后永久保留",
                source="automatic_rpa_start",
                device_id=payload.deviceId,
                flow_id=payload.flowId,
                reservation_id=reservation_id,
            )
        except InvalidVideoUrl as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.exception("写入视频黑名单失败")
            raise HTTPException(
                status_code=500,
                detail=f"写入视频黑名单失败：{exc}",
            ) from exc

        if not added:
            logger.warning(
                "视频链接命中黑名单，拒绝创建RPA deviceId=%s flowId=%s videoKey=%s videoUrl=%s",
                payload.deviceId,
                payload.flowId,
                blacklist_record.get("videoKey"),
                video_url,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "VIDEO_BLACKLISTED",
                    "message": "该视频链接已加入黑名单，未调用 GeeLark",
                    "record": blacklist_record,
                },
            )

    def rollback_video_reservation(reason: str) -> None:
        if not isinstance(video_url, str) or reservation_id is None:
            return
        try:
            removed, current_record = video_blacklist.remove(
                video_url,
                expected_reservation_id=reservation_id,
                reason=reason,
            )
        except RuntimeError:
            logger.exception(
                "撤销视频自动占用失败 deviceId=%s flowId=%s videoUrl=%s",
                payload.deviceId,
                payload.flowId,
                video_url,
            )
            return
        if not removed:
            logger.warning(
                "视频自动占用未撤销，记录已变化 deviceId=%s flowId=%s videoKey=%s",
                payload.deviceId,
                payload.flowId,
                current_record.get("videoKey") if current_record is not None else None,
            )

    try:
        result = await geelark_client.add_rpa_task(
            phone_id=payload.deviceId,
            flow_id=payload.flowId,
            schedule_at=payload.scheduleAt,
            param_map=payload.paramMap,
            api_token=api_token,
        )
    except GeeLarkNetworkError as exc:
        rollback_video_reservation("geelark_network_error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not result.ok:
        rollback_video_reservation("geelark_rejected_creation")
        raise HTTPException(status_code=502, detail=result.to_dict())

    task_id = extract_created_task_id(result)
    if task_id is None:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "创建任务成功，但响应中未找到 taskId",
                "response": result.to_dict(),
            },
        )

    async with tasks_lock:
        state = new_registered_state(
            task_id,
            payload.deviceId,
            "server_start",
            api_token,
        )
        state.create_task_result = result.to_dict()
        tasks[task_id] = state
        data = state.to_dict()

    logger.info(
        "服务器创建并登记RPA taskId=%s deviceId=%s flowId=%s",
        task_id,
        payload.deviceId,
        payload.flowId,
    )
    return {
        "success": True,
        "taskId": task_id,
        "deviceId": payload.deviceId,
        "flowId": payload.flowId,
        "task": data,
        "response": result.to_dict(),
        "videoBlacklist": blacklist_record,
        "message": ("RPA 已创建，启动超时计时已立即开始；无需等待第一个用户名心跳"),
    }


@app.post("/api/tiktok/rpa/heartbeat")
@app.post("/api/rpa/heartbeat")
async def heartbeat(payload: HeartbeatIn, request: Request) -> dict[str, Any]:
    api_token = request.state.geelark_token
    raw_progress_id = get_progress_id(payload)
    normalized_progress = normalize_username(raw_progress_id)
    is_startup_signal = normalized_progress in {
        "__rpa_start__",
        "__start__",
    }
    now_utc = datetime.now(timezone.utc)
    now_mono = time.monotonic()

    is_new_progress = False
    cache_hit = False
    accepted = True
    cleanup_scheduled = False
    first_heartbeat = False

    async with tasks_lock:
        state = tasks.get(payload.taskId)

        # 向后兼容：没有预登记时，首个心跳仍可自动登记。
        # 但这种模式无法检测“首个请求之前就卡住”的情况。
        if state is None:
            state = new_registered_state(
                payload.taskId,
                payload.deviceId,
                "heartbeat_auto_register",
                api_token,
            )
            tasks[payload.taskId] = state

        if not owns_task(state, api_token):
            raise HTTPException(status_code=404, detail="任务不存在")

        if state.device_id != payload.deviceId:
            raise HTTPException(
                status_code=409,
                detail="相同 taskId 已绑定另一个 deviceId",
            )

        state.heartbeat_count += 1
        state.last_request_at = now_utc

        if state.status not in {"starting", "running"}:
            accepted = False
        else:
            if not state.first_heartbeat_received:
                state.first_heartbeat_received = True
                state.first_heartbeat_at = now_utc
                state.status = "running"
                state.last_new_progress_monotonic = now_mono
                state.last_new_progress_at = now_utc
                first_heartbeat = True

            state.last_progress_id = normalized_progress

            if is_startup_signal:
                # 启动信号只证明 RPA 已进入流程，不参与用户名缓存。
                state.last_new_progress_monotonic = now_mono
                state.last_new_progress_at = now_utc
            else:
                state.first_user_received = True
                cache_hit = normalized_progress in state.recent_users

                if cache_hit:
                    state.consecutive_cache_hits += 1
                    state.total_cache_hits += 1

                    if state.consecutive_cache_hits >= config.RECENT_USER_HIT_THRESHOLD:
                        state.status = "stopping"
                        state.cleanup_reason = "recent_user_cache_hit"
                        cleanup_scheduled = True
                else:
                    state.consecutive_cache_hits = 0
                    state.recent_users.append(normalized_progress)
                    if len(state.recent_users) > config.RECENT_USER_CACHE_SIZE:
                        del state.recent_users[
                            : len(state.recent_users) - config.RECENT_USER_CACHE_SIZE
                        ]

                    state.last_new_progress_monotonic = now_mono
                    state.last_new_progress_at = now_utc
                    state.new_progress_count += 1
                    is_new_progress = True

        response_status = state.status
        consecutive_cache_hits = state.consecutive_cache_hits
        recent_users = list(state.recent_users)
        startup_source = state.startup_source

    if cleanup_scheduled:
        logger.warning(
            "检测到评论列表循环 taskId=%s deviceId=%s username=%s consecutiveHits=%s cache=%s",
            payload.taskId,
            payload.deviceId,
            normalized_progress,
            consecutive_cache_hits,
            recent_users,
        )
        job = asyncio.create_task(cleanup_task(payload.taskId, "recent_user_cache_hit"))
        track_cleanup_job(job)

    if not accepted:
        return {
            "success": True,
            "accepted": False,
            "isNewProgress": False,
            "cacheHit": False,
            "cleanupScheduled": False,
            "taskId": payload.taskId,
            "deviceId": payload.deviceId,
            "progressId": normalized_progress,
            "status": response_status,
            "consecutiveCacheHits": consecutive_cache_hits,
            "recentUsers": recent_users,
            "message": "任务已进入清理或终态，心跳未刷新",
        }

    if is_startup_signal:
        log_label = "收到RPA启动心跳"
    elif cleanup_scheduled:
        log_label = "缓存命中并触发清理"
    elif cache_hit:
        log_label = "缓存命中"
    else:
        log_label = "新用户名"

    logger.info(
        "%s taskId=%s deviceId=%s progressId=%s "
        "firstHeartbeat=%s source=%s consecutiveCacheHits=%s",
        log_label,
        payload.taskId,
        payload.deviceId,
        normalized_progress,
        first_heartbeat,
        startup_source,
        consecutive_cache_hits,
    )

    if is_startup_signal:
        message = (
            "已收到 RPA 启动信号；如果之后一直没有用户名，"
            f"超过 {config.FIRST_USER_TIMEOUT_SECONDS} 秒将自动停止"
        )
    elif cleanup_scheduled:
        message = (
            f"连续 {config.RECENT_USER_HIT_THRESHOLD} 个用户名命中最近 "
            f"{config.RECENT_USER_CACHE_SIZE} 人缓存，已开始停止 RPA "
            "并关闭云手机"
        )
    elif cache_hit:
        message = "用户名命中缓存，等待下一次心跳判断是否已经循环"
    else:
        message = "新用户名，已加入最近用户缓存并刷新超时计时"

    return {
        "success": True,
        "accepted": True,
        "firstHeartbeat": first_heartbeat,
        "startupSignal": is_startup_signal,
        "isNewProgress": is_new_progress,
        "cacheHit": cache_hit,
        "cleanupScheduled": cleanup_scheduled,
        "taskId": payload.taskId,
        "deviceId": payload.deviceId,
        "progressId": normalized_progress,
        "status": response_status,
        "consecutiveCacheHits": consecutive_cache_hits,
        "recentUsers": recent_users,
        "message": message,
    }


@app.get("/api/tiktok/rpa/tasks")
@app.get("/api/rpa/tasks")
async def list_tasks(request: Request) -> dict[str, Any]:
    api_token = request.state.geelark_token
    async with tasks_lock:
        data = [state.to_dict() for state in tasks.values() if owns_task(state, api_token)]
    return {"success": True, "count": len(data), "tasks": data}


@app.get("/api/tiktok/rpa/tasks/{task_id}")
@app.get("/api/rpa/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    api_token = request.state.geelark_token
    async with tasks_lock:
        state = tasks.get(task_id)
        if state is None or not owns_task(state, api_token):
            raise HTTPException(status_code=404, detail="任务不存在")
        data = state.to_dict()
    return {"success": True, "task": data}


@app.post("/api/tiktok/rpa/tasks/{task_id}/stop")
@app.post("/api/rpa/tasks/{task_id}/stop")
async def manually_stop(task_id: str, request: Request) -> dict[str, Any]:
    api_token = request.state.geelark_token
    async with tasks_lock:
        state = tasks.get(task_id)
        if state is None or not owns_task(state, api_token):
            raise HTTPException(status_code=404, detail="任务不存在")
        if state.status in FINISHED_LOCAL_STATUSES:
            return {
                "success": True,
                "scheduled": False,
                "taskId": task_id,
                "status": state.status,
                "message": "任务已经清理结束",
            }
        if state.status == "stopping":
            return {
                "success": True,
                "scheduled": False,
                "taskId": task_id,
                "status": "stopping",
                "message": "任务正在清理",
            }
        state.status = "stopping"
        state.cleanup_reason = "manual_stop"

    job = asyncio.create_task(cleanup_task(task_id, "manual_stop"))
    track_cleanup_job(job)
    return {
        "success": True,
        "scheduled": True,
        "taskId": task_id,
        "status": "stopping",
    }


# 只读接口：用于先验证 /open/v1/task/query 的 Body 和解析
@app.get("/api/tiktok/rpa/geelark/task-status/{task_id}")
@app.get("/api/rpa/geelark/task-status/{task_id}")
async def remote_task_status(task_id: str, request: Request) -> dict[str, Any]:
    assert geelark_client is not None
    api_token = request.state.geelark_token
    try:
        result = await geelark_client.query_task(task_id, api_token)
    except (GeeLarkNetworkError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "success": result.ok,
        "parsedStatus": extract_task_status(result, task_id),
        "response": result.to_dict(),
    }


@app.delete("/api/tiktok/rpa/tasks/{task_id}")
@app.delete("/api/rpa/tasks/{task_id}")
async def delete_local_task(task_id: str, request: Request) -> dict[str, Any]:
    api_token = request.state.geelark_token
    async with tasks_lock:
        state = tasks.get(task_id)
        if state is not None and not owns_task(state, api_token):
            raise HTTPException(status_code=404, detail="任务不存在")
        if state is not None and state.status == "stopping":
            raise HTTPException(
                status_code=409,
                detail="任务正在清理，不能删除",
            )
        removed = tasks.pop(task_id, None)

    return {
        "success": True,
        "deleted": removed is not None,
        "taskId": task_id,
    }


if __name__ == "__main__":
    try:
        uvicorn.run(
            "main:app",
            host="127.0.0.1",
            port=8002,
            workers=1,
        )
    except KeyboardInterrupt:
        pass
