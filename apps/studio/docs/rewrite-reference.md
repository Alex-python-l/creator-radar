# Rewrite Reference — 从 0 设计的技术栈参考

**状态**: 参考文档，非执行计划。仅记录"如果今天从零开始会怎么设计"，供未来决策时对照。
**日期**: 2026-05-28
**适用前提**: 单台 VPS 内部工具，单用户/小团队，IO 密集，无 SaaS 化诉求。前提变了，结论会变。

---

## 业务本质

剥到最干净一句话：**一个外部 API 的任务编排器**——账号有状态、视频有状态、手机有状态，所有操作都是触发状态机迁移；调用 GeeLark + Gemini 是 IO；UI 是状态机的观察窗 + 人工触发器。

特征：

- 单用户/小团队，内部工具
- IO 密集，零 CPU 压力
- 数据小（账号 ~10³、视频 ~10⁴、任务历史 ~10⁵）
- **"启动手机 = 花钱"**——重复执行是真损失，幂等是核心约束
- "发视频 → 等结果"是异步流程，不是请求-响应
- 单 VPS 部署可接受

## 技术栈选型

```
语言:    Python 3.12+
API:     FastAPI + Pydantic v2
ORM:     SQLModel (薄, 够用) + Alembic (migrations)
数据库:  PostgreSQL 16
队列:    Procrastinate (PG-native, cron + queue + advisory lock 全包)
HTTP:    httpx (async)
UI:      HTMX + Alpine.js + Tailwind (standalone CLI) + Jinja2
鉴权:    FastAPI session middleware + Argon2 密码
日志:    structlog → systemd journal
工具链:  uv (依赖) + ruff (lint/format) + pytest
部署:    单 VPS + systemd × 2 unit + nginx 或 Caddy
监控:    healthchecks.io ping + Procrastinate 自带 UI
```

## 关键决策的"为什么"

### 1. Python（不是 TS / Go / Rust）

业务是「调外部 API + 状态机 + 写脚本」。Python 在这三件事上的开发速度无人能敌。Go 的单二进制部署诱人但 verbose；TS 让前后端同语言但失去 Python 的脚本性；Rust 是噪音。

### 2. Procrastinate（最关键的一个决策）

PG 上的 Python 任务队列。一个组件解决四件事：

- **cron 触发器** → 暖号定时器
- **任务队列 + 重试** → 替掉「fire-and-forget + 5min 轮询对账」
- **`queueing_lock`** → PG advisory lock，跨进程防重复启动手机
- **持久化任务历史** → 自带 jobs 表 + 管理 UI

不需要 Redis、Celery、RabbitMQ、Beat、APScheduler。一个 PG = 调度 + 队列 + 业务数据 + 锁。

**不选 Temporal 的理由**：能力更强、状态机表达更优雅，但要么自托管（运维重）要么 Temporal Cloud（跨境延迟 + 依赖外部）。对单 VPS 内部工具过度。如果哪天业务流程超过 5 个状态机 × 10 条迁移路径，再迁。

### 3. PostgreSQL（不是 SQLite）

不是为性能。是为：

- Procrastinate 要它
- advisory lock（跨进程锁）
- JSONB（存 GeeLark API 原始回执，未来加字段不动 schema）
- 备份/恢复生态（`pg_dump` + 定时丢 OSS）
- 远程 `psql` 做临时查询

### 4. HTMX + Alpine + Tailwind（不是 React）

4 个页面 + wizard 的内部工具，没有任何场景需要 SPA：

- 表格 + 表单 + SSE → HTMX 的 sweet spot
- 上传进度、抽屉展开 → Alpine 10 行搞定
- 服务端渲染 = 类型从 DB → Pydantic → Jinja 直通，零 API contract 维护

**砍掉的东西**：npm、node、vite、tsc、eslint、tailwind 插件、React、i18next、react-router、`@types/*`、整个 `frontend/` 目录、frontend build 步骤。

不选 React 的另一个理由：内部工具不需要"产品级 UX"。HTMX 风格糙但能用，营销人员关心的是"批量勾选 + 一键发布"能不能跑，不是过渡动画。

### 5. SQLModel + Alembic（不是手写 SQL）

SQLModel 是 FastAPI 作者的 ORM，Pydantic 模型 = DB 模型 = API schema，**一份定义三处用**。复杂查询掉到原生 SQL（SQLModel 不挡你）。Alembic 管 migrations，告别手写迁移函数。

### 6. uv（不是 pip + venv + requirements.txt）

`uv sync` 创建环境 + 装依赖一条命令，`uv.lock` 跨机一致，CI/CD 速度比 pip 快约 10×。

### 7. 单 VPS + systemd（不是 Docker / K8s）

两个 unit：`mkt-web.service`（uvicorn `--workers 1`）+ `mkt-worker.service`（`procrastinate worker`）。web 和 worker 用 advisory lock 协调跨进程，不再用 `threading.Lock`。崩溃域隔离。

### 8. 鉴权：多用户 + cookie（2026-07-03 起）

内部工具不上 OAuth/SSO/RBAC。users 表（用户名 + scrypt 哈希）+ HMAC timed cookie（30 天），邀请码 `REGISTER_CODE` 自助注册，人人平权。此前是单一 `DASHBOARD_PASSWORD`。

## 不做的事

| 不做 | 原因 |
|---|---|
| Docker / Compose | 单机内部工具，引入比收益多 |
| K8s / 微服务 | 业务边界不存在，拆什么 |
| Redis | Procrastinate 用 PG 干完 Redis 的事 |
| Celery / RQ | 同上 |
| GraphQL / tRPC | REST + Pydantic 够 |
| OpenTelemetry / Prometheus / Grafana | systemd journal + healthchecks.io ping 够 |
| Vault / Secrets Manager | `.env` 文件够 |
| 任何 LLM agent framework | 业务里只有「Gemini 生成 caption」一处单次调用 |
| i18n | 内部工具单语 |
| 前端状态管理库 / Redux | 服务端渲染没这个问题 |
| Storybook / 组件库 | 4 个页面用不上 |
| GitHub Actions 全套 CI | systemd 部署 = `git pull && systemctl restart`，一行 |
| TypeScript | 删掉本身就是 feature |

## 项目结构（参考）

```
mkt/
  app/
    main.py              FastAPI factory, mount static, routes
    db.py                SQLModel engine + session dep
    models.py            Account / Video / JobAudit / Settings
    auth.py              cookie + password
    deps.py              FastAPI dependencies
    routes/              路由（返回 Jinja 模板，不是 JSON）
      accounts.py
      videos.py
      dashboard.py
      settings.py
    tasks/               Procrastinate tasks（替掉 services + scheduler）
      warmup.py          @app.periodic(cron="0 9,14,20 * * *")
      publish.py         @app.task(queueing_lock="phones")
      status_poll.py     @app.periodic(cron="*/1 * * * *")
      proxy_check.py
    clients/
      geelark.py         httpx async client
      gemini.py          caption 生成
    templates/           Jinja2（HTMX 友好的 partial）
      base.html
      accounts/
      videos/
      ...
    static/              tailwind output + alpine + htmx
  alembic/
  tests/
  pyproject.toml         uv 管
  systemd/
    mkt-web.service
    mkt-worker.service
  Caddyfile              或 nginx.conf
  .env.example
```

预估总代码量 **1500-2000 行**（相比现状约为 1/3）。

## 会动摇这套设计的条件

如果出现下列情况，需要重新评估：

1. **SaaS 化 / 多租户** → HTMX + 单 VPS + 单密码鉴权全部失效，需要重新设计
2. **业务流程状态机超过 5 个状态 × 10 条迁移路径** → 考虑迁 Temporal
3. **数据量上 10⁷ 级** → 需要分库 / 读写分离，超出单 PG 单机范畴
4. **多机房 / 多区域部署** → systemd 单机模型失效
5. **强合规要求**（审计 / 加密 / 隔离）→ 需要正经 secrets management 和审计基础设施

只要这些前提不出现，**保持简单**就是正确的设计。

## 与现状的关系

本文档是「如果今天从零开始」的参考。**当前代码不必按这份设计重写**——重写成本是 2-3 周，能否换回价值取决于未来还要在这套代码上加多少功能。如果功能基本定型，**别动**；如果还要持续加新页面、新调度、新外部对接，重做一次比每次都和现有架构搏斗划算。
