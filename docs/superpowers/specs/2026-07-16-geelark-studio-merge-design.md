# geelark-studio 并入 creator-radar——第一阶段·门户统一(设计)

- 日期:2026-07-16
- 状态:已与用户对齐,待实施
- 源仓库:https://github.com/yuyuyuyu52/geelark-studio(并入后归档只读)

## 背景与动机

creator-radar(监控/读)与 geelark-studio(GeeLark 云手机运营/写)是同一套业务的两半,生产环境已共享同一台阿里云服务器、同一个 nginx、同一个 `svm-postgres` PG 实例(不同 database)。当前两个门户(`igotyoufrank.cn` 与 `mkt.igotyoufrank.cn`)各自登录、各自 UI,使用割裂。

第一阶段目标:**一个仓库、一个域名、一次登录、左上角产品切换器**。深度融合(合库、统一框架、功能联动)留到后续阶段。

## 已确认的决策

| 议题 | 决定 |
|---|---|
| 合并动机 | 先统一域名 + 统一 UI 壳(左上角切换两个产品),后续再深度融合 |
| 域名路径 | radar 占根,studio 挂 `/studio`;`mkt.igotyoufrank.cn` 301;GeeLark 裸 IP `:8080` 回调入口不动 |
| 登录与权限 | 统一到 studio 多用户体系,一套账号全员通行(不加角色);radar 旧口令门退役 |
| 代码搬迁 | `git subtree` 保留完整历史并入 `apps/studio`;旧仓库归档 |
| 上线节奏 | 一次性合并上线(一个分支全部改完,一个部署窗口切换,备好回滚) |
| UI 风格 | 以 radar 为主:studio 整体换肤对齐 radar(白底、近黑主色、系统字体栈);布局骨架不动 |

## 非目标(明确不做)

- 不合数据库:studio 继续 `mkt` 库 + Alembic,radar 继续 `short_video_monitor` 库 + Drizzle。
- 不统一前端框架(Next.js 与 Vite SPA 并存)、不统一任务队列(自建 jobs 表与 Procrastinate 并存)。
- 不做监控↔运营功能联动、不做角色权限。
- 运行时进程模型不动:studio systemd 双 unit,radar docker,美西 probe 不动。

## 1. 仓库结构

```
apps/
  web/       # radar Next.js(不变)
  scraper/   # radar Python scraper(不变)
  studio/    # ← geelark-studio 全量:app/ frontend/ alembic/ tests/ deploy/ probe/ scripts/
packages/
  shared/    # + 产品清单常量(名称/路径),供两边切换器用
```

- `git subtree add --prefix=apps/studio <repo> main`,全历史并入;主线历史用 `git log --first-parent` 过滤。
- `apps/studio/frontend` 加入 pnpm workspace(`pnpm-workspace.yaml` 增加该路径,删 `package-lock.json`);Python 侧保持独立 uv 项目(与 `apps/scraper` 同模式)。
- studio 的 GitHub workflows 并入主仓,按 `apps/studio/**` 路径过滤触发;knip / 根级 lint 范围适配,不误伤 studio。
- studio 自己的 `CLAUDE.md` / `AGENTS.md` 留在 `apps/studio/` 下继续生效;根 `README.md` 更新架构说明;`docs/production-deploy.md` 并入本仓 `docs/` 并更新所有路径引用。

## 2. 域名与路由

| 入口 | 合并后 |
|---|---|
| `igotyoufrank.cn/` | Radar(不变,反代 :3000) |
| `igotyoufrank.cn/studio/**` | nginx 剥前缀反代 → uvicorn :18080 |
| `mkt.igotyoufrank.cn` | 301 → `igotyoufrank.cn/studio$request_uri`(证书保留续期,https 跳转不告警) |
| `139.196.52.92:8080` | **原样不动**(GeeLark 回调注册地址,硬约束) |

核心手法 **nginx strip prefix**:studio 后端路由完全不改(`/api/*` 原样),只有前端感知基路径:

- Vite `base: '/studio/'`;React Router `basename: '/studio'`;`api.ts` 基地址随 `import.meta.env.BASE_URL` 推导。
- nginx `location /studio/ { proxy_pass http://mkt_backend/; }`(尾斜杠剥前缀);SSE 免缓冲端点(`publish-stream`、`phones/create`)与 2GB 上传限制、超时参数随迁——正则 location 内 `proxy_pass` 不能带 URI,用 `rewrite ^/studio(/.*)$ $1 break;`。
- 检查 studio 登录 `set_cookie`:`path=/`、`secure`,保证根路径的 radar 能收到同一 cookie。
- 已知副作用:浏览器直接开 `:8080` 时静态资源 404(index.html 引用 `/studio/assets/*`)。该入口只服务 GeeLark API 回调,不影响;在部署文档注明。

## 3. 统一登录

- studio 新增 `GET /api/auth/verify`:验 `session` cookie 签名与时效 + 查 `users.session_ver` 比对(沿用 `require_auth` 逻辑),返回 200(附用户信息)/401。无副作用、不写库。
- radar 的 Next.js middleware 删除旧口令门,改为:读同域 `session` cookie → 转发到 verify 端点校验(生产同机内网 `http://127.0.0.1:18080/api/auth/verify`,经 env `STUDIO_AUTH_VERIFY_URL` 配置)→ 未登录/校验失败(401)302 `/studio/login?next=<原地址>`。首版不做校验结果缓存(同机内网开销可忽略);若日后观测到延迟,再加 30s TTL 进程内缓存。
- studio 登录页支持 `next` 参数跳回任意同源相对路径(校验必须以 `/` 开头且非 `//`,防 open redirect);登录成功后可直达 radar 根路径。
- radar 侧 `AUTH_SECRET`、`auth-token.ts`、`login-guard.ts`、登录页全部删除;`docs/DEPLOY.md` 同步更新 env 清单。
- 本地开发:radar dev 通过 env 指向本地 studio(`:8080`);`NODE_ENV=development` 下允许 `AUTH_DEV_BYPASS=1` 跳过校验。Vite dev server 在 `/studio/` base 下代理 `/studio/api` → `:8080/api`,与生产路径行为一致。
- radar 数据无 per-user 概念,全员看同一份监控数据(已确认接受)。

## 4. 产品切换器

- `packages/shared` 导出产品清单常量:`[{ id, name, path }]`(Radar → `/`,Studio → `/studio/`)。
- 两边各自实现视觉一致的左上角切换器组件,整页跳转互达。共享常量、不共享 UI 组件代码(两边构建体系不同);视觉规格以 radar 风格为准(见 §5)。studio 端文案保持英文,radar 端产品名同样用英文 Radar / Studio。

## 5. studio UI 换肤:对齐 radar 风格

**依据**:radar 是 shadcn 式语义 token(白底、近黑 primary、灰蓝 muted、`rounded-xl` 卡片、系统中文字体栈);studio 现为自成一套的"dense ops console"(灰 canvas、五级 ink 灰阶、蓝主按钮、橙红 accent `#f54e00`、SF Pro 栈)。用户判定 studio 风格不对,整体向 radar 对齐。

**利好**:studio 全站样式集中在 `index.css` 的 `@theme` 变量 + `components/ui.tsx`(27 个组件),页面散落的引用均为 `var(--color-*)` 形式,几乎无硬编码色值;Shell 本就是左侧栏布局,与 radar 骨架相同。换肤 = 改 token + 调组件,布局不动。

**Token 语义迁移**(统一到 radar 命名,studio 特有状态色保留为扩展):

| studio 旧 token | → 新(radar 语义) | 值 |
|---|---|---|
| `canvas` `#f3f4f6` | `background` | 白 `hsl(0 0% 100%)` |
| `surface` | `card` | 白 |
| `surface-hover` / `blue-bg`(悬停/选中态) | `muted` | `hsl(210 40% 96.1%)` |
| `panel` | `sidebar` | `hsl(0 0% 98%)` |
| `line` / `line-strong` | `border` / `input` | `hsl(214.3 31.8% 91.4%)` |
| `ink` 五级灰阶 | `foreground` + `muted-foreground` 派生灰阶 | radar 的 222/215 色相系 |
| `blue`(主行动色场景) | `primary` | 近黑 `hsl(222.2 47.4% 11.2%)` |
| `blue`(信息状态场景,如 Badge) | 保留蓝,值不变 | `#2563eb` |
| `red` | `destructive` | `hsl(0 84.2% 60.2%)` |
| `green` / `orange` | 对齐 radar tier 色 | `tier-growing` / `tier-mid` |
| `yellow` / `purple` | 保留(radar 无对应) | 沿用 |
| `accent` `#f54e00` | **删除**,主行动一律 `primary` | — |
| 字体 SF Pro 栈 | radar 系统中文栈 | `system-ui, PingFang SC, …` |
| radius 6/8 | 对齐 radar 用法 | 卡片 `rounded-xl`、控件 `rounded-md` |

**组件层**(`ui.tsx`):Button 主按钮改 primary 近黑(hover:opacity-90)、secondary 白底+border;focus ring 从蓝 outline 改为 radar 的近黑 `ring/20`;Card 对齐 `rounded-xl border shadow-sm`;表头对齐 uppercase tracking-wide 灰;Modal/Drawer/Toast/SegmentedControl 等 studio 特有组件保留 API 与动效,仅调色与圆角。

**Shell 与登录页**:侧栏配色对齐 radar sidebar(98% 灰底 + 右 border),logo 区并入产品切换器(§4);登录页是合并后全门户的入口,视觉完全对齐 radar,branding 文案不变。

**方法与范围**:`index.css` 重写 `@theme`;`ui.tsx` 逐组件调整;页面散落的 `var(--color-*)` 引用按上表映射全局替换(ink/line/canvas/surface 系可机械替换,`blue` 需逐处判断行动色/信息色语义,约几十处);涉及 `frontend/src` 全部约 20 个 tsx。纯视觉改动,不碰组件 API、路由与后端。

## 6. 数据库与部署形态

全部维持现状:同一 `svm-postgres` 实例、两个独立 database、各自迁移工具;studio systemd `mkt` + `mkt-worker`(`/opt/mkt`),radar docker `svm-web`。变化仅两点:

1. studio 部署 tarball 改从 monorepo `apps/studio` 打包(runbook 更新路径)。
2. radar 生产 env:删 `AUTH_SECRET`,加 `STUDIO_AUTH_VERIFY_URL=http://127.0.0.1:18080/api/auth/verify`。

## 7. 上线序列与回滚

代码在一个分支完成,部署窗口(选无人使用时段)按序:

1. **备份**:nginx 旧 conf、studio 旧 tarball、radar 旧镜像各留一份。
2. **切 studio + nginx**:部署新 studio 到 `/opt/mkt`,同窗口放新 nginx conf(`/studio` location + mkt 301)并 reload。两步之间存在分钟级静态资源 404 窗口,可接受。
3. **切 radar**:部署新镜像(新 middleware)重启 docker。
4. **冒烟**:登录 → radar 看板 → 切换器互跳 → studio 视频列表 / SSE → `curl http://139.196.52.92:8080/healthz`(回调通路)→ 登出重登。
5. **回滚**:恢复 nginx conf、旧 tarball 重启、旧镜像重启——三者相互独立,可单独回退。

## 8. 测试

- studio:现有 pytest 基线(≥385)在 `apps/studio` 新路径全绿;新增 `/api/auth/verify` 用例(有效/过期/篡改/`session_ver` 失配)。
- radar:`pnpm --filter web test` / `typecheck` 保持绿;middleware 新鉴权逻辑单测(mock verify 端点:200/401/网络错)。
- 前端:`/studio` base 下 build 产物资源路径抽查;登录 `next` 跳转的 open-redirect 校验用例。
- UI 换肤点检:9 个页面 + 登录页 + Modal/Drawer/Toast/wizard 逐一过目(重点:残留橙红 accent、灰 canvas、蓝主按钮;对比 radar 同类组件);`rg 'f54e00|color-canvas|color-ink'` 确认旧 token 无残留。
- CI:studio 测试 job 带 postgres service(测试库名须含 `test`),按路径过滤触发。

## 风险与对策

| 风险 | 对策 |
|---|---|
| GeeLark 回调断(最高危,烧钱业务) | 后端路由零改动 + `:8080` 入口不动;冒烟含回调通路检查 |
| base path 漏改致 SPA 白屏 | 集中在 Vite `base` / Router `basename` / `api.ts` 三处;build 后本地以 strip-prefix 反代预演 |
| cookie 收不到(域/路径不对) | 上线前检查 `set_cookie` 参数;冒烟含跨产品免登验证 |
| 一次性上线爆整站 | 分钟级窗口 + 三路独立回滚预案(§7) |
| UI 换肤引入视觉回归(如状态色语义错乱) | token 按映射表机械迁移 + `blue` 语义逐处判断;§8 逐页点检清单;不碰组件 API |
| verify 端点故障导致 radar 全站 302 | middleware 区分 401(→ 302 登录页)与网络错误/5xx(→ 直接返回 503,绝不重定向,避免循环;也不放行);同机内网可用性高 |

## 后续(深度融合 backlog,本期不做)

统一登录态下的功能联动(监控爆款 → 一键发布/互动)、合库、统一队列、统一前端框架、角色权限。届时另立 spec。
