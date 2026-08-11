# GeeLark RPA Watchdog

GeeLark RPA 的独立单进程看门狗服务。它接收 RPA 心跳，在评论流程卡住或用户名列表
开始回环时，依次保存现场截图、取消 RPA、等待任务终态并关闭云手机。

这个服务与 `apps/studio` 的 `overdue_session_watchdog` 互补：

- Studio watchdog 按数据库中的 phone session 总预算兜底；
- 本服务按单个 RPA 的实时进度做秒级保护；
- 本服务状态保存在进程内存，必须保持 `--workers 1`，也不能做多实例负载均衡。

## 保护阶段

| 原因 | 默认阈值 | 含义 |
| --- | ---: | --- |
| `startup_timeout` | 45 秒 | 已创建/登记任务，但没有任何心跳 |
| `first_user_timeout` | 90 秒 | 已收到 `__RPA_START__`，但没有首个用户名 |
| `heartbeat_timeout` | 30 秒 | 已收到用户名，但一直没有新用户名 |
| `recent_user_cache_hit` | 连续 3 次 | 用户名连续命中最近 15 人缓存，判定列表回环 |

所有自动和手动清理都走同一条链路：

```text
保存异常截图 → 取消 RPA → 等待状态 3/4/7 → 关闭云手机
```

如果无法确认任务进入终态，服务不会贸然关闭云手机，并把本地状态标为
`stop_failed`。

## 本地运行

```bash
cd apps/rpa-watchdog
uv sync
cp .env.example .env
set -a && source .env && set +a
uv run uvicorn main:app --host 127.0.0.1 --port 8002 --workers 1
```

检查：

```bash
curl http://127.0.0.1:8002/health
uv run pytest
uv run ruff check .
```

## RPA 接入

推荐由服务创建任务，这样从 GeeLark 返回真实 `taskId` 的瞬间就开始启动超时计时：

```http
POST /api/rpa/start
Content-Type: application/json
Authorization: Bearer <当前workspace的GEELARK_TOKEN>

{
  "deviceId": "真实云手机ID",
  "flowId": "真实flowId",
  "paramMap": {
    "视频链接": "https://www.tiktok.com/@owner/video/123",
    "评论翻页次数": "500",
    "是否打开主页": true
  }
}
```

RPA 的第一个节点应发送：

```http
POST /api/tiktok/rpa/heartbeat
Content-Type: application/json
Authorization: Bearer <当前workspace的GEELARK_TOKEN>

{
  "taskId": "${_TASK_ID_}",
  "deviceId": "${_ENV_ID_}",
  "progressId": "__RPA_START__"
}
```

每进入一个评论用户，再向同一端点发送真实用户名：

```json
{
  "taskId": "${_TASK_ID_}",
  "deviceId": "${_ENV_ID_}",
  "progressId": "${commentUserName}"
}
```

用户名比较忽略开头 `@`、大小写和首尾空格。`__RPA_START__` 只表示流程已启动，
不会进入用户名缓存。

如果任务由其他入口创建，但调用方已知道真实 ID，可先调用：

```http
POST /api/rpa/register
Authorization: Bearer <当前workspace的GEELARK_TOKEN>

{
  "taskId": "真实任务ID",
  "deviceId": "真实云手机ID",
  "source": "manual_register"
}
```

没有预登记时，首个心跳仍会自动创建本地状态，但无法保护“首个请求之前卡住”的阶段。

## 任务接口

兼容 `/api/rpa/**` 和 `/api/tiktok/rpa/**` 两组路径：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/rpa/start` | 创建 GeeLark RPA 并立即登记 |
| `POST` | `/api/rpa/register` | 登记外部创建的真实任务 |
| `POST` | `/api/rpa/heartbeat` | 上传启动信号或用户名进度 |
| `GET` | `/api/rpa/tasks` | 查看当前进程内的全部任务 |
| `GET` | `/api/rpa/tasks/{taskId}` | 查看任务缓存和清理结果 |
| `POST` | `/api/rpa/tasks/{taskId}/stop` | 手动触发统一清理 |
| `DELETE` | `/api/rpa/tasks/{taskId}` | 删除非清理中的本地状态 |
| `GET` | `/api/rpa/geelark/task-status/{taskId}` | 只读验证 GeeLark 状态解析 |

除 `/health` 外的所有端点都要求 `Authorization: Bearer <GEELARK_TOKEN>`。
watchdog 不保存全局 GeeLark token：每个请求使用当前 workspace 自己的 token，
任务状态也按 token 隔离；同一进程可以同时服务多个 workspace。

现有产品通过 Studio Ops 的 `RPA Watchdog` 页签调用这些接口。调用链是浏览器 →
已登录的 Studio API → 从当前 workspace 加密凭据中读取 `GEELARK_TOKEN` → 本服务。
Token 不会返回给浏览器。RPA 的 HTTP 心跳节点也必须携带所属 workspace 的同一个
GeeLark Bearer token。

服务只监听 `127.0.0.1`。若需要让 GeeLark RPA 从公网回调，只应通过受控的反向代理
暴露必需路径；不要直接把 8002 端口开放到公网。

## 视频黑名单

`/api/rpa/start` 会原子占用 `paramMap["视频链接"]`：GeeLark 明确拒绝创建或网络请求
失败时撤销占用；任务创建成功后保留黑名单记录。若 GeeLark 返回成功但缺少 `taskId`，
因为任务可能已经创建，也会保留记录，避免重复执行。后续相同视频返回 HTTP 409，
且不会再次调用 GeeLark。

管理员仍可手动维护黑名单：

```bash
cd apps/rpa-watchdog
uv run python video_blacklist.py add 'TikTok视频或图文链接' --note '原因'
uv run python video_blacklist.py check 'TikTok视频或图文链接'
uv run python video_blacklist.py list
uv run python video_blacklist.py remove 'TikTok视频或图文链接'
```

默认记录在 `ignored_video_links.jsonl`。每次启动任务都会重新读取，修改后无需重启。

## 部署

systemd 模板位于 `deploy/geelark-watchdog.service`，默认安装目录是
`/opt/geelark-watchdog`。仓库统一发布步骤和回滚要求见根目录
`docs/DEPLOY.md` 的 RPA Watchdog 小节。

关键约束：

- `.env` 不打包、不提交；其中只放超时和路径配置，不放 workspace 凭据；
- 生产必须单进程；
- 重启服务会丢失进程内任务缓存，关闭前会尽力清理仍活跃的任务；
- `ERROR_SCREENSHOT_DIR`、日志、用户名和黑名单是运行数据，需要单独持久化和备份；
- 不要与 Studio 的 Web/worker 进程合并生命周期。
