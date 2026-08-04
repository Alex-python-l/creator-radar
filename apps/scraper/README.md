# apps/scraper

Python FastAPI + Postgres jobs worker + APScheduler。负责平台抓取、Facebook URL 解析、定时刷新入队和图表物化表重算。

## 本地启动

```bash
# 1. 同步依赖（uv 自动建 .venv）
uv sync

# 2. 准备环境变量（从仓库根的 .env.example 复制）
cp ../../.env.example .env
# 本地默认库：
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/short_video_monitor

# 3. 启动服务（FastAPI 监听 8000；启动时自动拉起 worker + scheduler）
uv run uvicorn app.main:app --reload --port 8000
```

健康检查：

```bash
curl http://localhost:8000/health
```

## Worker 行为

- 启动时为 `tiktok` / `instagram` / `facebook` / `youtube` 各开一条 worker lane。
- 每条 lane 只认领自己平台的 `jobs.payload.platform`，同平台串行、跨平台并发。
- 认领使用 `FOR UPDATE SKIP LOCKED`，重复启动 worker 时也尽量避免重复处理。
- transient 错误按指数退避重试；超过 `max_retries` 后进 `dead`。
- 非 transient 错误标记 `failed`，同时写 `fetch_logs.error_code`。

任务格式：

```json
{
  "kind": "account.fetch",
  "payload": {
    "influencerId": 123,
    "platform": "tiktok",
    "username": "example",
    "mode": "full"
  }
}
```

`mode='recent'` 用于每日定时刷新，只拉最近一批；`mode='full'` 用于手动抓取和新账号导入。

## Scheduler

`app/main.py` 启动 APScheduler：

- 每天北京时间 05:00：`enqueue_daily_refresh`，为所有 active 账号入队 `mode='recent'`。

## HTTP API

| 路径 | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `POST /resolve-fb-url` | Facebook share / pfbid URL 转稳定 identifier，供 Web 批量导入使用 |

抓取任务不通过 HTTP API 触发；Web 直接写 `jobs` 表。

## 目录

```
app/
  main.py                    # FastAPI app + worker/scheduler 生命周期
  config.py                  # pydantic-settings 读 .env
  worker.py                  # Postgres jobs worker loop
  db/
    models.py                # SQLAlchemy 映射，跟 Drizzle schema 对齐
    session.py               # async engine/session
  handlers/
    scrape.py                # account.fetch 执行逻辑
    schedule.py              # 每日刷新入队
    resolve_fb.py            # FB URL 解析
  scrapers/                  # 各平台抓取器与 Bright Data fallback
scripts/                     # 回填 / smoke / 去重脚本
tests/
```

## 配置重点

- `DATABASE_URL`：与 web 共用 Postgres；裸 `postgresql://` 会在 scraper 内自动转成 asyncpg driver URL。
- `INSTAGRAM_PROXY_URL`：Instagram 建议 sticky 单出口。
- `TIKTOK_PROXY_POOL` / `FACEBOOK_PROXY_POOL` / `YOUTUBE_PROXY_POOL` / `COMMON_PROXY_POOL`：池化代理。
- `BRIGHT_DATA_API_KEY`：平台原生抓取撞墙时的付费 fallback。
- `INSTAGRAM_USERNAME` / `INSTAGRAM_PASSWORD` / `INSTAGRAM_SESSIONID` / `INSTAGRAM_TOTP_SEED`：Instagram 登录态。

## 检查

```bash
uv run pytest
uv run ruff check .
```

如需真实抓取 smoke，优先使用 `scripts/` 下的脚本，并确认代理和账号登录态已经配置。
