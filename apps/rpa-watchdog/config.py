from __future__ import annotations

import os

GEELARK_BASE_URL = os.getenv("GEELARK_BASE_URL", "https://openapi.geelark.cn").rstrip("/")

WATCHDOG_TIMEOUT_SECONDS = float(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "30"))
WATCHDOG_CHECK_INTERVAL_SECONDS = float(os.getenv("WATCHDOG_CHECK_INTERVAL_SECONDS", "2"))

# 启动阶段超时：任务已由服务器创建/预登记，但一个心跳都没有收到。
RPA_STARTUP_TIMEOUT_SECONDS = float(os.getenv("RPA_STARTUP_TIMEOUT_SECONDS", "45"))

# 已收到 __RPA_START__，但迟迟没有抓到第一个用户名。
FIRST_USER_TIMEOUT_SECONDS = float(os.getenv("FIRST_USER_TIMEOUT_SECONDS", "90"))
GEELARK_HTTP_TIMEOUT_SECONDS = float(os.getenv("GEELARK_HTTP_TIMEOUT_SECONDS", "8"))

# 每个 RPA 任务独立保存最近 N 个用户名。
# 连续命中缓存达到阈值后，服务器立即取消 RPA 并关闭云手机。
RECENT_USER_CACHE_SIZE = max(1, int(os.getenv("RECENT_USER_CACHE_SIZE", "15")))
RECENT_USER_HIT_THRESHOLD = max(1, int(os.getenv("RECENT_USER_HIT_THRESHOLD", "3")))

TASK_TERMINAL_TIMEOUT_SECONDS = float(os.getenv("TASK_TERMINAL_TIMEOUT_SECONDS", "45"))
TASK_QUERY_INTERVAL_SECONDS = float(os.getenv("TASK_QUERY_INTERVAL_SECONDS", "3"))

PHONE_STOP_MAX_ATTEMPTS = int(os.getenv("PHONE_STOP_MAX_ATTEMPTS", "4"))
PHONE_STOP_RETRY_DELAY_SECONDS = float(os.getenv("PHONE_STOP_RETRY_DELAY_SECONDS", "4"))

CLEANUP_ON_SHUTDOWN = os.getenv("CLEANUP_ON_SHUTDOWN", "true").lower() in {"1", "true", "yes", "on"}
SHUTDOWN_CLEANUP_TIMEOUT_SECONDS = float(os.getenv("SHUTDOWN_CLEANUP_TIMEOUT_SECONDS", "60"))

# 看门狗清理前调用 GeeLark 截图接口，保存当前云手机错误现场。
ERROR_SCREENSHOT_ENABLED = os.getenv("ERROR_SCREENSHOT_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ERROR_SCREENSHOT_DIR = os.getenv("ERROR_SCREENSHOT_DIR", "rpa_error_screenshots")
ERROR_SCREENSHOT_TIMEOUT_SECONDS = float(os.getenv("ERROR_SCREENSHOT_TIMEOUT_SECONDS", "20"))
ERROR_SCREENSHOT_POLL_INTERVAL_SECONDS = float(
    os.getenv("ERROR_SCREENSHOT_POLL_INTERVAL_SECONDS", "1")
)
ERROR_SCREENSHOT_DOWNLOAD_TIMEOUT_SECONDS = float(
    os.getenv("ERROR_SCREENSHOT_DOWNLOAD_TIMEOUT_SECONDS", "20")
)

# 由管理员手动维护；RPA 启动接口只读取并检查，不会自动添加。
VIDEO_BLACKLIST_FILE = os.getenv("VIDEO_BLACKLIST_FILE", "ignored_video_links.jsonl")

# 你没有提供 /open/v1/task/query 的精确 Body。
# 默认使用 {"ids":[task_id]}；实测若文档要求其他格式，可改为 taskId 或 id。
TASK_QUERY_BODY_STYLE = os.getenv("GEELARK_TASK_QUERY_BODY_STYLE", "ids")
