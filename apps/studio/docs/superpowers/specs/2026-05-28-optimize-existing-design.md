# Optimize Existing Project — Design Spec

**Date**: 2026-05-28
**Status**: Draft for user review
**Reference**: `docs/rewrite-reference.md`（"从 0 设计"版本）

## 背景

`docs/rewrite-reference.md` 描述了"如果今天从 0 写"的理想栈。本文档是**基于该参考、针对现有代码的增量优化方案**——不是推倒重写。

经过对照分析，现有项目和参考栈之间**值得改的差距**集中在后端协调机制（APScheduler + `threading.Lock` + 5 分钟轮询对账）和数据库（SQLite → PG）。前端 React 整套不改——4180 行已写好、在跑、营销在用，重写无业务收益。

## 范围

### 改

1. **APScheduler + `threading.Lock` + 5min 轮询 → Procrastinate**（核心收益）
2. **SQLite → PostgreSQL**（配合 Procrastinate，捆绑改）
3. **systemd × 1 → systemd × 2**（web + worker，崩溃域隔离）
4. **手写 SQL + 手写 `_migrate()` → SQLModel + Alembic**（顺势重写 repo）
5. **pip + requirements.txt → uv + pyproject.toml**（开发体验）
6. **`requests` → `httpx`**（仅新代码用，旧 `geelark_client.py` 保留）
7. **加 `/healthz` + healthchecks.io 心跳告警**

### 不改

- React 前端整套（包括所有页面、wizard、i18next、构建链）
- 鉴权（HMAC cookie）
- 日志（stdlib logging）
- 业务逻辑模块（`warmup_service.py`、`posting_service.py`、`geelark_client.py`、`generate_caption.py`）

## 实施路径

**方案 B：按依赖分 4 个阶段，每阶段独立可上线**

```
阶段 0 (0.5d) ─→ 上线 ─→ [暂停点 1] uv 体验如何
阶段 1 (1.5d) ─→ 上线 ─→ [暂停点 2] PG 稳吗 数据对吗
阶段 2 (2.0d) ─→ 上线 ─→ [暂停点 3] worker + lock 符合预期吗
阶段 3 (0.5d) ─→ 完成

总计 4.5 天，任何暂停点都可终止
```

**关键设计原则**：每个暂停点都是有意义的终点。如果阶段 2 觉得 Procrastinate 复杂度不划算，可以停在阶段 1（业务已在 PG 上，调度仍是 APScheduler），这就是参考文档的"中改造"档位。

## 架构对比

### 变更前

```
单进程 uvicorn (--workers 1)
├── FastAPI 路由 (handler 直接调 services)
├── BackgroundScheduler (in-memory jobstore)
│   ├── warmup (cron)
│   ├── status_check (interval 1min, 全表扫 INITIATED)
│   ├── pic_check (interval 10min)
│   └── proxy_check (cron 04:00)
├── threading.Lock OPERATION_LOCK  ← 进程内同步
└── sqlite3 connection (WAL)

数据: app.db (单文件)
部署: systemd × 1
```

### 变更后

```
mkt-web.service                          mkt-worker.service
├── FastAPI 路由                          ├── Procrastinate worker
├── handler 调 service 或 defer() 任务    ├── 4 个 periodic task
└── SQLModel session (PG)                 ├── publish task (handler defer)
                                          ├── follow-up check task
                                          ├── lock="phones" (跨进程锁)
                                          └── SQLModel session (PG)

数据: PostgreSQL 16
├── 业务表 (accounts/videos/settings/job_runs/groups)
└── procrastinate_* (jobs/locks/periodic 自带)

监控: /healthz endpoint + healthchecks.io ping
部署: systemd × 2 + nginx + PG
```

**关键变化**

1. **协调机制下沉到 PG**：`threading.Lock` → Procrastinate `lock`（基于 procrastinate_jobs 表 + `SELECT FOR UPDATE`），跨进程互斥
2. **调度从内存搬到 PG**：APScheduler in-memory → Procrastinate periodic（PG-backed）。重启不丢、跨进程不重复触发
3. **崩溃域隔离**：worker 崩了不影响页面访问；web 崩了 worker 照常跑

## PG 实例

**决策**：用生产服务器已有的 PG 实例，**新建独占 database `mkt`**。不另起 Docker 容器（单 VPS 内部工具，多一个容器多一份运维负担）。

```sql
CREATE DATABASE mkt;
CREATE USER mkt WITH PASSWORD '<生成的强密码>';
GRANT ALL PRIVILEGES ON DATABASE mkt TO mkt;
\c mkt
GRANT ALL ON SCHEMA public TO mkt;
```

连接串：`DATABASE_URL=postgresql+psycopg://mkt:<pwd>@127.0.0.1:5432/mkt` 写入 `.env`。

**待阶段 1 启动前确认**：生产 PG 实际监听地址（假设 127.0.0.1:5432，若实际不同需调整）、PG 版本是否 >= 14（Alembic + SQLModel 测试覆盖最稳的版本范围）。

## 阶段 0：工具链（0.5 天）

| 项 | 动作 |
|---|---|
| `requirements.txt` → `pyproject.toml` | 依赖搬过去，加 `[tool.uv]` 配置 |
| 安装方式 | `pip install ...` → `uv sync`；锁文件 `uv.lock` 入 git |
| 启动脚本 | `./.venv/bin/uvicorn ...` → `uv run uvicorn ...`，改 `run.sh` |
| ruff | `[tool.ruff]` 在 pyproject.toml，规则集 default + `I` (isort)；**只 lint 不一次性全量 format**（避免阶段 0 commit 塞几千行格式 diff）|
| README / CLAUDE.md | 安装命令同步 |

**验收**：`uv sync && uv run pytest -q` 仍 38 passed。

## 阶段 1：DB + ORM 切换（1.5 天）

### 依赖变更

加：`sqlmodel`, `psycopg[binary]>=3`, `alembic`
留：`apscheduler`（阶段 2 才删）、`requests`（阶段 3 才动）

### 模型层（新建 `app/models.py`）

5 个表全部翻译成 SQLModel 类：`Account` / `Video` / `Setting` / `JobRun` / `Group`。`datetime('now')` 类的 SQL 表达式改成 `default_factory=datetime.utcnow` + `sa_column_kwargs={"server_default": text("NOW()")}`。

### `db.py` 重写

```python
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10)

def get_session():
    with Session(engine) as s:
        yield s
```

删掉：`sqlite3.Connection`、`PRAGMA`、`check_same_thread`、手写 `_migrate()`、`row_factory`、`main.py` 的 thread-local `db_conn()`。

### Repository 层重写

300 行手写 SQL → SQLModel `session.exec()`。**决策：保留 repository 层**（不让 SQLModel 漏到 routes/services），函数签名换成接收 `Session` 而不是 `sqlite3.Connection`。复杂查询掉到 `text()` + 命名参数。

### Alembic baseline

```bash
uv run alembic init alembic
# env.py: target_metadata = SQLModel.metadata
uv run alembic revision --autogenerate -m "baseline"
```

后续 schema 改动都走 `alembic revision --autogenerate`。

### APScheduler 暂时不动

阶段 1 **不切 jobstore**，APScheduler 仍用 MemoryJobStore，`threading.Lock` 仍在。理由：阶段 2 马上整个删掉，没必要中间切一次再删。

### 数据迁移脚本

`scripts/migrate_sqlite_to_pg.py`：
- 表顺序：`groups → accounts → videos → settings → job_runs`（FK 顺序）
- 保留 id（videos.account_id 是 FK）
- 跑完校验：每表 `SELECT count(*)` SQLite vs PG 必须一致
- 旧 `data/app.db` 改名 `data/app.db.bak.<date>` 保留 7 天

### 测试

- 本地起独立 `mkt_test` PG database
- pytest `conftest.py`：fixture 每测试前 TRUNCATE 所有表
- **不引入 testcontainers / Docker**——依赖太重
- 38 测试全部移植；预估 2-3 个用 SQLite 特有写法的要小改

### 上线步骤

```
1. 生产 PG: CREATE DATABASE mkt; CREATE USER mkt; ...
2. systemctl stop mkt
3. cp data/app.db data/app.db.bak.<date>
4. git pull && uv sync
5. uv run alembic upgrade head        # 在 PG 建表
6. uv run python scripts/migrate_sqlite_to_pg.py
7. 校验：行数对得上、随机查询 OK
8. systemctl start mkt
9. 浏览器登录冒烟测试
```

## 阶段 2：Procrastinate + worker 拆分（2 天）

### Procrastinate App 配置

```python
# app/tasks/__init__.py
from procrastinate import App, PsycopgConnector
from app.config import DATABASE_URL

procrastinate_app = App(
    connector=PsycopgConnector(conninfo=DATABASE_URL),
    import_paths=[
        "app.tasks.warmup",
        "app.tasks.publish",
        "app.tasks.status_poll",
        "app.tasks.pic_check",
        "app.tasks.proxy_check",
    ],
)
```

Procrastinate 自己的表由 `procrastinate -a app.tasks:procrastinate_app schema --apply` 创建。

**并入 Alembic 的方式**：在阶段 2 的第一个 Alembic revision 里用 `op.execute()` 直接执行 Procrastinate 的 schema SQL（从 `procrastinate.schema_get_sql()` 拿到）。**不**在 Alembic 里 shell out 调 `procrastinate` CLI（避免 migration 依赖外部进程，离线/CI 不稳）。Procrastinate 之后的 schema 升级（升级 procrastinate 版本时）也走这种方式生成新 revision。

### Periodic tasks

```python
# warmup
@procrastinate_app.periodic(cron="0 9,14,20 * * *")
@procrastinate_app.task(lock="phones", queue="phones")
async def warmup_task(timestamp: int):
    with Session(engine) as s:
        warmup_service.run_warmup(s, make_client())

# pic_check
@procrastinate_app.periodic(cron="*/10 * * * *")
@procrastinate_app.task(lock="phones", queue="phones")
async def pic_check_task(timestamp): ...

# proxy_check (不持 lock, 不动手机)
@procrastinate_app.periodic(cron="0 4 * * *")
@procrastinate_app.task(queue="default")
async def proxy_check_task(timestamp): ...
```

### publish 流程重设计（关键变化）

**当前**：API 同步调 GeeLark submit → 立即返回 INITIATED → 5min 后定时 `status_check` 全表扫所有 INITIATED 对账。

**改后**：

```
用户点 Publish
  → API: publish_video_task.defer(video_id, account_id)
     立即返回 {"status": "QUEUED", "job_id": ...}
  → worker 拉起 publish_video_task (持 lock="phones"):
       - 调 GeeLark submit → 拿 geelark_task_id
       - 更新 DB: result=INITIATED
       - 释放 phones lock
       - defer check_publish_status(video_id, retry=0) schedule_in=30s
  → worker 拉起 check_publish_status (不持 lock, 只读):
       - 查 GeeLark 状态
       - 完成 → 写 SUCCESS / FAILURE
       - 仍 running 且 retry<40 → re-defer 自己 schedule_in=30s
       - retry=40 (20分钟) → 标记超时 FAILURE
```

**收益**：
- 对账延迟 5min → 30s
- 不再全表扫，每个 video 独立追踪轨道
- "启动手机"路径全部走 `lock="phones"`，跨进程互斥

**代价**：videos.result 多一个状态 `QUEUED`（PENDING 和 INITIATED 之间）。**前端 Videos 表格加一行状态映射**——阶段 2 唯一前端改动。

### lock="phones" 替换 OPERATION_LOCK

所有"启动手机"路径都用 Procrastinate `lock="phones"`：
- `warmup_task`、`pic_check_task`、`publish_video_task` → 加 `lock="phones"`
- `proxy_check_task`、`check_publish_status` → **不加**（只读）

**前置验证**（阶段 2 第一步）：**grep 全代码**，列出所有调 GeeLark 启动手机的代码位置，逐一确认走 lock。预估只有 publish 这一处 API 路径 + scheduler 的 3 个 job，但必须先验证。

### worker 入口 + systemd × 2

```ini
# /etc/systemd/system/mkt-worker.service
[Unit]
Description=mkt worker
After=postgresql.service
Requires=postgresql.service

[Service]
WorkingDirectory=/opt/mkt
EnvironmentFile=/opt/mkt/.env
ExecStart=/opt/mkt/.venv/bin/procrastinate -a app.tasks:procrastinate_app worker
Restart=always
RestartSec=5
User=mkt

[Install]
WantedBy=multi-user.target
```

`mkt-web.service` 原样保留，两个 unit 独立崩溃域。

**待阶段 2 启动前确认**：生产服务器是否已有 OS 用户 `mkt`（当前 `mkt-web.service` 的 `User=`），没有则 `useradd -r -s /usr/sbin/nologin mkt` 创建后再上线。

### 删除旧代码

阶段 2 末尾一并删除：
- `app/scheduler.py`（整个文件）
- `app/locks.py`（`OPERATION_LOCK`）
- `app/main.py` 启动 scheduler 的代码
- `apscheduler` 依赖

业务逻辑模块（`warmup_service.py` / `posting_service.py` 等）**全部保留**，只是从 scheduler 调用改成被 task 包装调用。

### 开发模式

本地起两个进程，**不引入 honcho/Procfile**（多一个依赖）。`run.sh` 仍只起 web。开发 worker 时另开终端：

```bash
uv run procrastinate -a app.tasks:procrastinate_app worker
```

README 写明这一点。

### 测试

- 单元测试用 Procrastinate 自带 `InMemoryConnector`
- 关键集成测试：
  - `lock="phones"` 真互斥（起两个 worker，并发 defer warmup + publish，验证执行顺序串行）
  - publish 全链路（QUEUED → INITIATED → SUCCESS）
  - `check_publish_status` 重试边界（retry=40 超时）

### 上线步骤

```
1. systemctl stop mkt
2. git pull && uv sync
3. uv run alembic upgrade head  # procrastinate schema 已并入
4. systemctl start mkt
5. systemctl enable --now mkt-worker
6. journalctl -u mkt-worker -f
7. 手动触发一个 publish，验证全流程
```

## 阶段 3：附带（0.5 天）

### `/healthz` endpoint

`GET /healthz` 返回 `{ok: true, db: "ok", worker_last_seen: <ts>}`：
- `db` 是 `SELECT 1`
- `worker_last_seen` 查 `procrastinate_workers` 表最近 heartbeat

### healthchecks.io 心跳

- 已注册 URL：写入 `.env` 的 `HEALTHCHECK_URL=https://hc-ping.com/b65f6e53-71de-4736-9a5b-a3c55c36f592`
- 配置：Period 5min，Grace 10min（worker 静默 15min 才告警）
- 实现：在 `status_poll` periodic task（每 5min 跑）任务体最后 `httpx.get(HEALTHCHECK_URL)`，没设 env 则跳过

**心跳的含义**：worker 在跑 + task 在执行 + DB 能连——三件事打包。

### `httpx`

新建 `app/clients/geelark_async.py`，仅 Procrastinate task 内部使用。旧 `geelark_client.py` 保留同步 requests（API 路由还在调）。两套并存，不强求统一。

### `.env.example` 收尾清理

- 加新增变量：`DATABASE_URL`、`HEALTHCHECK_URL`
- 移除废弃变量：`SHEET_URL`、`SERVICE_ACCOUNT_PATH`、`DRIVE_FOLDER_ID`（Google Sheets/Drive 已移除）
- 保留并标注：`DASHBOARD_PASSWORD=changeme` 必须改

## 测试策略（汇总）

| 阶段 | 测试 |
|---|---|
| 0 | `uv sync && uv run pytest -q` → 38 passed，零业务测试改动 |
| 1 | conftest 改 PG 版（`mkt_test` database，每测试 TRUNCATE）。38 测试全部移植；预估 2-3 个 SQLite 特有写法要小改 |
| 2 | 新增 task 测试用 `InMemoryConnector`；两个关键集成测试（lock 互斥 + publish 全链路）；retry 边界测试 |
| 3 | `/healthz` 单元测试；healthchecks ping 在无 `HEALTHCHECK_URL` env 时跳过 |

## 回滚策略

| 阶段 | 回滚动作 | 数据风险 |
|---|---|---|
| 0 | `git checkout` 旧分支 + 恢复 venv | 无 |
| 1 | `git checkout` + 重启 web（代码读 sqlite app.db） | **阶段 1 上线后新写入 PG 的数据会丢**。**24h 内回滚可接受丢一天数据；超 24h 不支持回滚** |
| 2 | `systemctl stop mkt-worker` + `git checkout` web 到阶段 1 状态 | 回滚期间无法 publish；要等 in-flight procrastinate jobs 跑完再切 |
| 3 | 删 endpoint / 删 env var | 无 |

## 风险清单

| 风险 | 阶段 | 严重度 | 缓解 |
|---|---|---|---|
| 数据迁移丢数据 | 1 | **高** | 迁移脚本行数校验；旧 `app.db.bak.<date>` 保留 7 天 |
| `lock="phones"` 遗漏某启动手机路径 → 重复扣费 | 2 | **高** | 阶段 2 启动前 grep 全代码，列出所有调 GeeLark 启动手机位置 |
| PG 运维不熟（备份、磁盘） | 1 | 中 | 阶段 1 上线前确认 `pg_dump` 定时任务、监控磁盘 |
| Procrastinate 学习曲线 | 2 | 中 | 阶段 2 第一步先做最小 POC：trivial task `defer → execute` 跑通 |
| in-flight job 部署时丢失 | 2 | 低 | Procrastinate 任务持久化在 PG，worker SIGTERM 默认等当前任务完成 |
| 前端遗漏 `QUEUED` 状态映射 | 2 | 低 | 前端只改一处，QA 验收时点一次 publish 即发现 |
| 开发者本地没 PG | 全部 | 低 | README 加"本地装 PG" 三行 |

## 不在本次范围内的事

记录下来供未来参考，但**本次不做**：

- 前端 React → HTMX 重写（参考文档建议但无业务收益，4180 行已稳定）
- 鉴权升级（HMAC cookie → Argon2 session）
- 日志框架升级（stdlib logging → structlog）
- 监控扩展（OpenTelemetry / Prometheus / Grafana）
- Docker 化（单 VPS 内部工具不需要）
- CI/CD（systemd 部署 = `git pull && systemctl restart`）
- i18n 清理（如果项目实际不用 i18next 可后续单独 PR 删）

## 后续步骤

1. 用户审阅本 spec
2. 通过后，调用 `superpowers:writing-plans` skill 基于本 spec 生成详细实施 plan（按阶段拆任务）
3. 按 plan 进入实施
