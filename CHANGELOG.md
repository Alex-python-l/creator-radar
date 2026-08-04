# Changelog

本文件记录项目所有重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

## [Unreleased] · 2026-07-17 多用户面向公众

- **radar 自持多用户认证（better-auth）**：注册（`REGISTER_MODE=open|invite|closed` 三态）/登录（用户名或邮箱）/登出/改密，DB session，登录注册限流；`admin`/`user` 角色；登录页与侧栏用户菜单（zh/en）。旧 studio 账号经 `apps/web/scripts/migrate-users-to-radar.mjs` 迁移，scrypt 旧哈希兼容分流、免改密。
- **订阅制数据隔离**：`influencers`/`videos` 全局一份，普通用户经 `user_influencers` 订阅可见（加号即见历史数据），组/别名为订阅者私有视图；admin 保留全局视图（团队工作流不变，存量数据零迁移）；普通用户 `monitor_quota` 限额（默认 20）。scraper 零改动。
- **studio 收编为 admin-only**：`require_auth` 反向转发 radar `get-session` 校验（30s 缓存、fail-closed），自有 users API/登录页退役，非 admin 显示提示页；GeeLark `:8080` 回调链路不动。
- 迁移 SQL `apps/web/drizzle/manual/0009_multi_user.sql`；上线序列见 `docs/DEPLOY.md` F 节。

## [Current] · 2026-05-29

当前主线是 `main`，Web 版已经上线自用。运行态已从早期规划的 Inngest 事件链路切到更轻量的共享 Postgres `jobs` 队列：

- Web Server Action 写入 `jobs` 表。
- Scraper FastAPI 进程启动平台 worker lane 和 APScheduler。
- 生产部署为阿里云 web+PG、美国机 scraper，通过 autossh 隧道互通。
- AI/R2/Auth.js 仍是预留方向；当前认证为自定义 `AUTH_SECRET` + HMAC cookie。

历史路线图文档已清理，当前主线工作见 `docs/superpowers/specs/` 与 `docs/superpowers/plans/` 下的最新文档。下面早期条目保留为历史记录，不代表当前运行态。

## [Historical] · `web` 分支重构初期

从零搭建 Web 全栈版本（Next.js + FastAPI + Postgres + Inngest）。旧的 PyQt6 桌面端代码完整保留在 `main` 分支历史里。

### Removed

- 桌面端运行时代码：`main.py` / `config.py` / `core/` / `ui/` / `data/` / `skills/` / `tiktok/` / `db/`
- 桌面端打包/启动脚本：`requirements.txt` / `run_app.command`
- 已过期文档：`AB_COMPARISON_MIGRATION.md`（描述的重构在 64dcaf0 已完成）
- 桌面端配置教程：`docs/SETUP_GUIDE.md` / `docs/GEMINI_SETUP.md` / `docs/CONFIG_GUIDE.md` / `docs/CONFIG_CHANGES.md`

合计删除 58 个文件、约 2 万行代码。

### Changed

- `README.md` — 重写为 Web 版项目说明，列出技术栈、目录规划、部署目标
- `CLAUDE.md` — 重写为 Web 架构指南，固化关键设计决策（抓取留 Python、Inngest 跨语言事件、Drizzle 单一 schema 源、Scraper 不能上 Vercel）
- `.env.example` — 替换为新栈环境变量（Postgres、Inngest、Gemini、Scraper、R2、Auth.js）
- `.gitignore` — 增加 Node/Next/Vite/uv/Python venv 相关条目

### Kept（明确保留作为参考）

- `Design/` — Lovable 生成的 React 原型，已含完整 shadcn HSL 设计 token（平台徽章、tier 配色、sidebar）。新 `apps/web` 的 Tailwind 配置和组件骨架以它为蓝本。
- `docs/GITHUB_FLOW.md` — 分支规范（新架构沿用）
- `docs/TikTok 等短视频爆款 AI 分析工具深度调研与设计报告.md` — 产品背景资料
- `.github/` — PR 模板

### Added — Web 骨架

- Monorepo 基础：`package.json` + `pnpm-workspace.yaml` + `docker-compose.yml`（Postgres 16）+ `.nvmrc`
- `packages/shared`：跨语言事件名（`@svm/shared/events`）与平台枚举（`@svm/shared/platforms`）
- `apps/web`：
  - Next.js 15.5 · App Router · TypeScript · Turbopack
  - Tailwind v4（CSS-first `@theme`，沿用 `Design/` shadcn HSL token）
  - Drizzle ORM + postgres-js + drizzle-kit
  - Drizzle schema 第一版：`users` / `influencers` / `videos` / `fetch_logs` / `ai_reports`
  - Inngest 类型化客户端（`EventSchemas`），编排函数占位
  - `@t3-oss/env-nextjs` + Zod 环境变量校验
  - Vercel AI SDK / TanStack Table+Query（依赖装好，未接入）
- Verification：`pnpm install` 通过（543 包，23.6 s）；`pnpm dev` 启动 864 ms；http://localhost:3000 HTTP 200

### Added — Scraper 骨架 + 端到端事件链路（C1）

- `apps/scraper`：Python 3.12 · FastAPI 0.136 · uv 管理
  - `app/main.py` — FastAPI app + `inngest.fast_api.serve` 挂载
  - `app/inngest_client.py` — Inngest Python 客户端（dev 模式默认开）
  - `app/handlers/scrape.py` — `on_account_fetch_requested` 订阅事件，先 console.log 验证链路
  - `app/events.py` — 与 `packages/shared/src/events.ts` 一字不差的事件名常量
  - `app/config.py` — pydantic-settings 读取共享 `.env`
- 根 `package.json` 的 `inngest:dev` 显式注入两个 SDK URL（web 3000 + scraper 8000）
- Verification：
  - `uv sync` 装 29 个包通过
  - `uv run uvicorn` 启动 scraper 监听 :8000
  - `pnpm inngest:dev` 起 dev 面板 :8288，自动同步两个 SDK，各注册 1 个 function
  - `POST /e/dev {"name":"account.fetch_requested",...}` 触发 Python handler 打印日志，HTTP 200

### Changed — SQLite 临时替代 Postgres（MVP demo 路径）

本机没装 Docker / brew / Postgres，按用户拍板用 SQLite 走通 MVP，等部署时切回 Postgres。

- `apps/web/lib/db/schema.ts` — `pgTable` → `sqliteTable`，`serial` → `integer().primaryKey({autoIncrement:true})`，`bigint` → `integer`，`jsonb` → `text({mode:'json'})`，`timestamp` → `integer({mode:'timestamp'})`，`boolean` → `integer({mode:'boolean'})`
- `apps/web/lib/db/index.ts` — `postgres-js` → `better-sqlite3` + WAL + foreign_keys=ON
- `apps/web/drizzle.config.ts` — `dialect: 'postgresql'` → `'sqlite'`，加 dotenv 加载 `.env.local`
- `apps/web/package.json` — 删 `postgres`，加 `better-sqlite3` / `@types/better-sqlite3` / `dotenv`
- `apps/web/lib/env.ts` — `DATABASE_URL` 不再要求 URL 格式，改 `min(1)` 兼容 SQLite 文件路径
- `apps/web/.env.local`、根 `.env.example` — `DATABASE_URL=./data.db`
- `db:push` 加 `--force` 跳过 dev 交互确认

Verification：`pnpm db:push` 推 5 张表到 `apps/web/data.db`，`[✓] Changes applied`。

### Added — 真实 TikTok 抓取 + 写库（C2）

- `apps/scraper`：
  - 依赖加 `aiosqlite` / `yt-dlp`，删 `asyncpg`
  - `app/db/{session,models}.py` — SQLAlchemy async engine + `Influencer`/`Video`/`FetchLog` declarative 映射，对齐 Drizzle 表名字段（Drizzle 仍是 schema 唯一来源）
  - `app/scrapers/tiktok.py` — yt-dlp 拉 TikTok 用户视频列表（`extract_flat`、不下载），归一化成 `ScrapedVideo` dataclass，支持代理
  - `app/handlers/scrape.py` 重写：查 influencer → 抓 → upsert videos（`on_conflict_do_update`）→ 更新 `last_fetched_at` → 写 `fetch_logs` → 发出 `account.fetch_completed` 或 `failed`
- `app/config.py` 默认 DATABASE_URL `sqlite+aiosqlite:///../web/data.db`

Verification（@tiktok 官方账号端到端）：
- yt-dlp 4.4 秒抓 20 条视频
- SQLite videos 表落 20 行（含播放量 3300 万 / 2320 万爆款）
- fetch_logs 写 1 条 success
- influencers.last_fetched_at / video_count 更新
- 抓完触发 `account.fetch_completed` 事件，TS 端 `onAccountFetchCompleted` 收到

### Added — Web UI：账号管理 + 视频列表（C3）

不引入 shadcn 安装链，直接用 `globals.css` 里的 HSL token + 手写最小组件（`Button` / `Card` / `Input` / `Select` / `Td` / `Th`），保持客户端浅色 + slate 风格。

- `app/layout.tsx` — 加左侧 sidebar 导航
- `app/page.tsx` — 账号管理首页（添加表单 + 表格 + 平台徽章 + 立即抓取 + 删除）
- `app/accounts/[id]/page.tsx` — 账号详情：用户信息 + 最近抓取记录 + 视频列表（按播放量倒序，前 100 条）
- `components/ui.tsx` — 最小组件库（无 Radix）
- `components/format.tsx` — `formatCompact` / `formatRelative` / `PlatformBadge`
- `lib/actions/accounts.ts` — Server Actions：`addAccount`（zod 校验 + onConflictDoNothing）/ `deleteAccount` / `triggerFetch`（发 `account.fetch_requested` 事件 + redirect 到详情页）
- `lib/queries/influencers.ts` — `listInfluencers` / `getInfluencer` / `listVideosByInfluencer` / `listFetchLogsByInfluencer`

Verification：
- `GET /` HTTP 200，HTML 含"账号管理"/"@tiktok"/"立即抓取"
- `GET /accounts/1` HTTP 200，1.5s 首次编译，HTML 含"视频列表"
- 完整浏览器交互（form 提交）暂未截图验证，待下回合 chrome MCP 跑

### 下一步

C4：Vercel AI SDK + Gemini，单视频 AI 分析（按钮触发 → 流式生成 → 写 `ai_reports` 表 → 详情页展示）。
