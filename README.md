# Creator Radar

面向个人与团队工作空间的多平台短视频监控与云手机运营系统。当前主线是 `main`，Web 版已经上线运行；旧 PyQt6 桌面端只保留在更早的 git 历史中。

## 这是什么

Creator Radar 用来监控 TikTok / Instagram / Facebook / YouTube 账号，定时抓取视频数据，按分组、平台和时间窗口查看账号表现与视频表现。2026-07 起是**面向公众的多用户产品**：radar 自持注册/登录（better-auth，`REGISTER_MODE` 控制开放节奏），个人与团队工作空间分别隔离数据和额度。公开注册时个人空间默认额度为 `0`；用户加入已配置额度的团队后共享团队额度。仓库同时包含 GeeLark Studio（云手机矩阵运营控制台，养号/发布/互动/计费），与 Radar 同域统一登录；团队成员可使用运营能力，只有 leader/admin 可管理工作空间密钥。生产入口为 `igotyoufrank.cn/studio`；详见 `apps/studio/README.md`。

## 当前架构

```
apps/
  web/        # Next.js 15 App Router：UI、Server Actions、Drizzle schema
  scraper/    # Python FastAPI：抓取 API、jobs worker、APScheduler 定时任务
  studio/     # GeeLark Studio 纯 API 后端：FastAPI + SQLModel + Procrastinate；前端已并入 apps/web
  rpa-watchdog/ # GeeLark RPA 独立心跳看门狗：启动/进度/回环超时清理
packages/
  shared/     # TS 侧平台枚举等共享常量
docs/
```

核心链路：

1. Web 端 Server Action 写入 Postgres `jobs` 表。
2. Scraper 常驻 worker 按平台 lane 轮询 `jobs`，同平台串行、跨平台并发。
3. Scraper 抓取后写 `videos` / `fetch_logs` / `influencers`。
4. Web 从共享 Postgres 读取看板、任务状态和图表。

Web 目前只通过 `SCRAPER_BASE_URL` 调 scraper 的内部 HTTP API 做 Facebook URL 解析；抓取任务写入 Postgres `jobs` 表，由 scraper worker 消费。

## 交接与运维入口

- [技术与运维交接手册（正式 Word 版）](documents/交接/Creator-Radar-技术与运维交接手册.docx)：代码、生产、账号资产、故障处置与签收入口。
- [生产部署 Runbook](docs/DEPLOY.md)：当前完整发布入口以 **H 节**为准；A–G 为历史迁移背景，不可直接重放。
- [Studio 生产部署补充](apps/studio/docs/production-deploy.md)：Studio API/Worker 的打包、Alembic、systemd 与 ingress 细节。

真实密码、token、私钥、代理凭据和恢复码不得写入仓库；通过公司密码库或当面安全渠道交付。

## 技术栈

| 层 | 当前使用 |
| --- | --- |
| Web | Next.js 15 App Router · TypeScript · Tailwind v4 · React 19 |
| 数据库 | PostgreSQL · Drizzle ORM（schema 单一来源） |
| 队列 | Postgres `jobs` 表 · Python worker · `FOR UPDATE SKIP LOCKED` |
| 定时任务 | APScheduler（scraper 进程内） |
| 抓取 | Python · yt-dlp · instagrapi · Bright Data API fallback |
| ORM 映射 | Web: Drizzle · Scraper: SQLAlchemy async（不负责迁移） |
| 认证 | better-auth（注册/登录/DB session · 个人/团队工作空间 · 工作空间数据隔离与配额）；Studio 转发工作空间会话，密钥管理限 leader/admin |
| 部署 | 自托管 Docker：阿里云 web+PG，美国机 scraper，autossh 隧道 |
| Studio | FastAPI · SQLModel · Procrastinate · Alembic；React 前端位于 apps/web |

## 本地开发

```bash
# 依赖
pnpm install

# 本地 Postgres
docker compose up -d postgres

# Web 环境变量
cp .env.example apps/web/.env.local
# 本地默认库可用：
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/short_video_monitor

# 初始化本地 schema（仅本地开发用）
pnpm db:push

# Web
pnpm dev

# Scraper（另开终端）
cd apps/scraper
uv sync
cp ../../.env.example .env
uv run uvicorn app.main:app --reload --port 8000
```

常用检查：

```bash
pnpm --filter web test
pnpm --filter web typecheck
cd apps/scraper && uv run pytest
cd apps/rpa-watchdog && uv run pytest
```

## 数据库与迁移

- Drizzle schema 在 `apps/web/lib/db/schema.ts`，这是唯一 schema 来源。
- Python 侧 SQLAlchemy model 只做读写映射，不写迁移。
- 本地可用 `pnpm db:push` 快速同步。
- 生产库已有数据，schema 变更走 `apps/web/drizzle/manual/*.sql` 的幂等 SQL，部署前阅读 `docs/DEPLOY.md`。

## 还需要做什么

历史 roadmap 文档已清理；当前主线工作见 `docs/superpowers/specs/` 与 `docs/superpowers/plans/` 下的最新文档。

## 分支规范

代码或文档改动走短分支，按任务只 stage 相关文件，合并 main 后即删分支。
