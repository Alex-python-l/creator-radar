# apps/web

Next.js 15 App Router · TypeScript · Tailwind v4 · Drizzle · Postgres。

Web 端负责 UI、Server Actions、登录、Drizzle schema 和读模型查询。抓取任务通过写入共享 Postgres `jobs` 表交给 `apps/scraper` 的常驻 worker 消费。

## 本地启动

```bash
# 在仓库根目录
pnpm install

# 起本地 Postgres
docker compose up -d postgres

# 准备环境变量
cp ../../.env.example .env.local
# 本地默认库：
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/short_video_monitor
# AUTH_SECRET=任意长随机字符串

# 推送 schema 到本地库（不要对生产库执行）
pnpm db:push

# 跑开发服务器
pnpm dev          # http://localhost:3000
```

如果要测试 Facebook URL 解析，另起 scraper：

```bash
cd ../scraper
uv sync
cp ../../.env.example .env
uv run uvicorn app.main:app --reload --port 8000
```

## 目录

```
app/
  [locale]/              # 看板、视频页、账号详情、登录/退出
  api/
    accounts/route.ts    # 账号列表 JSON API
    login/route.ts       # 登录表单处理
  globals.css            # Tailwind v4 + 应用设计 token
components/              # 账号表、分组、图表、任务队列等 UI
lib/
  actions/               # Server Actions：账号增删、分组、抓取入队
  db/
    schema.ts            # Drizzle schema — schema 单一来源
    index.ts             # drizzle client
  queries/               # 看板 / 视频 / 图表查询
  scraper-client.ts      # 内部 HTTP 调 scraper：当前只用于 FB URL resolve
drizzle/
  manual/                # 生产手工迁移 SQL
drizzle.config.ts
```

## 队列契约

Web 不直接执行抓取。当前契约是共享 Postgres：

- 入队：`lib/actions/accounts.ts` 向 `jobs` 插入 `kind='account.fetch'`。
- 去重：同账号已有 `queued` / `running` job 时不重复入队。
- 消费：scraper worker 按 `payload.platform` 抢占任务。
- 状态展示：Web 读取 `jobs` 与 `fetch_logs`，在任务卡和账号行展示 `queued` / `running` / `dead` 等状态。

`SCRAPER_BASE_URL` 只用于内部 HTTP API，例如批量导入 Facebook URL 时调用 `/resolve-fb-url` 生成稳定 identifier。

## Drizzle 工作流

```bash
pnpm db:generate   # 生成迁移 SQL
pnpm db:push       # 本地开发直接 push schema
pnpm db:studio     # Drizzle Studio
```

生产库已有数据，不跑 `db:push`。schema 变更按 `docs/DEPLOY.md`：先写幂等 SQL 到 `apps/web/drizzle/manual/`，先迁移生产库，再部署读写新字段的服务。

## 检查

```bash
pnpm --filter web test
pnpm --filter web typecheck
pnpm --filter web build
```

`next lint` 依赖 Next 旧命令，当前重点检查是测试、类型和 build。
