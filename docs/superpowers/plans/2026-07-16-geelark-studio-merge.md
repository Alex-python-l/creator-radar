# geelark-studio 并入 creator-radar(门户统一 + UI 换肤)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 geelark-studio 以 subtree 并入本仓库 `apps/studio`,实现同域名(`igotyoufrank.cn/studio`)、统一登录(studio 多用户体系)、左上角产品切换器,并将 studio UI 整体换肤为 radar 风格;一次性上线。

**Architecture:** nginx strip-prefix 让 studio 后端路由零改动(GeeLark 裸 IP `:8080` 回调因此天然兼容);radar middleware 转发 cookie 到 studio 新增的 `/api/auth/verify` 实现统一登录;换肤通过 `index.css` token 语义迁移 + `ui.tsx` 组件对齐完成,布局骨架不动。

**Tech Stack:** pnpm monorepo · Next.js 15 (radar) · Vite + React 19 (studio 前端) · FastAPI + SQLModel (studio 后端) · nginx · systemd · GitHub Actions

**Spec:** `docs/superpowers/specs/2026-07-16-geelark-studio-merge-design.md`

**关键既有事实**(执行者无需重新侦察):

- studio cookie:名 `session`,itsdangerous `URLSafeTimedSerializer(SESSION_SECRET, salt="auth")`,30 天,`httponly, samesite=lax`,**无 `secure`、path 默认 `/`**——同域下 radar 可直接收到,无需改 cookie。
- studio `require_auth`(`app/auth.py:47`)已实现 cookie→User→`session_ver` 校验,verify 端点直接复用。
- studio 前端 API 调用:`api.ts:221` 的 `req()`(`fetch('/api'+path)`)、`api.ts:274`(`/healthz`)、以及 **7 处裸 fetch/XHR**:`components/wizard/Step1Proxies.tsx:37`、`pages/Videos.tsx:135,247(XHR upload)`、`pages/Accounts.tsx:407,724,782,884`。
- radar 生产容器 `svm-web` 跑 **host 网络**,容器内 `127.0.0.1:18080` 可直达 studio uvicorn。
- radar 测试:`node --experimental-strip-types --test test/*.test.mjs`(看 `apps/web/test/` 现有文件的 import 惯例)。
- studio CI(`.github/workflows/ci-cd.yml`)含 backend/frontend/deploy 三个 job,deploy 打 tarball scp 到 `/opt/mkt` 解包(包内相对结构必须保持:`app alembic alembic.ini geelark_client.py … frontend/dist`)。
- shared 包名 `@svm/shared`,`exports` 直接指 TS 源(`./src/index.ts`)。
- studio 侧栏 Shell 本就是左侧栏布局;radar 侧栏在 `apps/web/app/[locale]/layout.tsx:43-65`。

---

## 前置条件(开工前确认)

- [ ] 工作区必须干净:当前有大量用户未提交的 docs 删改,**请用户自行提交或 stash**(subtree add 需要 clean tree;也避免误把用户改动卷进合并提交)。执行 `git status --porcelain`,非空则停下来找用户。
- [ ] 在 main 上新建分支:`git checkout -b merge-geelark-studio`

---

### Task 1: subtree 收编 geelark-studio

**Files:**
- Create: `apps/studio/**`(整个 geelark-studio 树,subtree 自动生成)

- [ ] **Step 1: subtree add(保留全历史)**

```bash
git subtree add --prefix=apps/studio https://github.com/yuyuyuyu52/geelark-studio.git main
```

Expected: 自动产生一个 merge commit,`apps/studio/` 出现完整项目(`app/ frontend/ alembic/ tests/ deploy/ probe/ scripts/ docs/ pyproject.toml uv.lock` 等)。

- [ ] **Step 2: 验证 studio 测试基线在新路径全绿**

```bash
cd apps/studio && uv sync && uv run alembic upgrade head 2>/dev/null; uv run pytest -q
```

Expected: `385 passed`(±,以当时基线为准)。若本地无 `mkt_test` 库:`createdb mkt_test` 后重跑(conftest 会自动改用含 test 的库名)。

- [ ] **Step 3: 清理 studio 仓库级杂项**

subtree 带进来的仓库级文件在 monorepo 里语义重复,删除:

```bash
git rm -r apps/studio/.github apps/studio/.claude
git commit -m "chore(studio): 移除随 subtree 带入的仓库级配置(CI 在 Task 3 重建)"
```

注意:`apps/studio/CLAUDE.md`、`AGENTS.md`、`.env.example`、`.gitignore` **保留**(目录级约定继续生效)。

---

### Task 2: frontend 纳入 pnpm workspace

**Files:**
- Modify: `pnpm-workspace.yaml`
- Modify: `apps/studio/frontend/package.json`
- Delete: `apps/studio/frontend/package-lock.json`
- Modify: `apps/studio/run.sh`

- [ ] **Step 1: workspace 声明 + 包名规范化**

`pnpm-workspace.yaml` 改为:

```yaml
packages:
  - 'apps/*'
  - 'apps/studio/frontend'
  - 'packages/*'
```

`apps/studio/frontend/package.json` 的 `"name"` 从 `geelark-studio-frontend` 改为 `"@svm/studio-frontend"`。

- [ ] **Step 2: 切换到 pnpm**

```bash
git rm apps/studio/frontend/package-lock.json
pnpm install
```

Expected: `pnpm-lock.yaml` 更新,`node_modules` 链接就绪,无 peer 报错。

- [ ] **Step 3: 验证 build + 更新 run.sh**

```bash
pnpm --filter @svm/studio-frontend build
```

Expected: `apps/studio/frontend/dist/index.html` 生成。

`apps/studio/run.sh` 中 `(cd frontend && npm run build)` 改为 `(cd frontend && pnpm build)`。

- [ ] **Step 4: Commit**

```bash
git add pnpm-workspace.yaml pnpm-lock.yaml apps/studio/frontend/package.json apps/studio/run.sh
git commit -m "chore(studio): frontend 纳入 pnpm workspace"
```

---

### Task 3: CI 迁移到 monorepo

**Files:**
- Create: `.github/workflows/studio.yml`(以删除前的 `apps/studio/.github/workflows/ci-cd.yml` 为底稿,`git show` 可取回)

- [ ] **Step 1: 生成适配后的 workflow**

以原 ci-cd.yml 为基础,做以下确定性修改(其余原样保留):

1. 触发加路径过滤:

```yaml
on:
  pull_request:
    branches: [main]
    paths: ['apps/studio/**', '.github/workflows/studio.yml']
  push:
    branches: [main]
    paths: ['apps/studio/**', '.github/workflows/studio.yml']
```

2. backend/frontend job:所有步骤加 `defaults.run.working-directory: apps/studio`(uv sync / ruff / alembic / pytest);Node 步骤换 pnpm:

```yaml
      - uses: pnpm/action-setup@v4
      - name: Set up Node
        uses: actions/setup-node@v6
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: pnpm
      - name: Build frontend
        run: pnpm install --frozen-lockfile && pnpm --filter @svm/studio-frontend build
        working-directory: .
```

3. deploy job:
   - `Detect migration changes` 里两处 `alembic/versions` → `apps/studio/alembic/versions`;
   - `Build deploy package` 改为 `tar -C apps/studio -czf "$DEPLOY_PACKAGE" …`(文件清单不变,**包内结构保持与 /opt/mkt 现状一致**);`test -f apps/studio/frontend/dist/index.html`;
   - `scp deploy/mkt-worker.service` → `apps/studio/deploy/mkt-worker.service`;
   - artifact 上传/下载路径 → `apps/studio/frontend/dist`。

- [ ] **Step 2: 语法校验并提交**

```bash
gh workflow list 2>/dev/null || true   # 仅确认 gh 可用,推送后在 PR 里看 Actions 是否解析
git add .github/workflows/studio.yml
git commit -m "ci(studio): CI/CD 迁入 monorepo,按 apps/studio/** 路径触发"
```

注意:**deploy job 需要的 `PROD_SSH_PRIVATE_KEY`、`PROD_SSH_KNOWN_HOSTS` secrets 及 `PROD_*` vars 在 creator-radar 仓库尚未配置**——首次上线走 Task 16 的手动窗口;secrets 配置列在 Task 17,配好前 push main 的 deploy job 会失败,属预期(可先在 workflow 加 `if: false` 到 deploy job,Task 17 时移除;二选一,推荐加 `if: false` 保持 Actions 绿)。

---

### Task 4: 根文档与工具链适配

**Files:**
- Modify: `README.md`、`CLAUDE.md`、`AGENTS.md`、`knip.json`

- [ ] **Step 1: README 架构节更新**

`README.md` 的目录树加一行 `apps/studio`,技术栈表加一行 Studio(FastAPI + SQLModel + Procrastinate + Vite SPA),"这是什么"段落补一句:Studio 是 GeeLark 云手机运营控制台,与 Radar 共用登录,入口 `igotyoufrank.cn/studio`。

- [ ] **Step 2: 工具链适配**

```bash
pnpm lint && pnpm typecheck
npx knip 2>&1 | head -30
```

`pnpm -r lint/typecheck` 会带起 `@svm/studio-frontend` 自己的 scripts,应通过;knip 若对 `apps/studio/frontend` 报误报,在 `knip.json` 加:

```json
{
  "ignoreDependencies": ["eslint-config-next"],
  "ignoreWorkspaces": ["apps/studio/frontend"]
}
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md AGENTS.md knip.json
git commit -m "docs: 根文档纳入 studio;工具链适配"
```

---

### Task 5: studio 前端 /studio 基路径

**Files:**
- Modify: `apps/studio/frontend/vite.config.ts`
- Modify: `apps/studio/frontend/src/main.tsx`
- Modify: `apps/studio/frontend/src/api.ts`
- Modify: `apps/studio/frontend/src/components/wizard/Step1Proxies.tsx`、`src/pages/Videos.tsx`、`src/pages/Accounts.tsx`

- [ ] **Step 1: vite base + dev proxy**

```ts
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/studio/',
  build: { outDir: 'dist' },
  server: {
    proxy: {
      '/studio/api': {
        target: 'http://localhost:8080',
        rewrite: (path) => path.replace(/^\/studio/, ''),
      },
      '/studio/healthz': {
        target: 'http://localhost:8080',
        rewrite: (path) => path.replace(/^\/studio/, ''),
      },
    },
  },
})
```

- [ ] **Step 2: Router basename**

`main.tsx`:

```tsx
<BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '')}>
```

- [ ] **Step 3: API 基地址集中化**

`api.ts` 顶部(`AUTH_EXPIRED_EVENT` 定义旁)加:

```ts
// nginx 在生产剥掉 /studio 前缀;dev 由 vite proxy rewrite。二者一致。
export const API_ORIGIN = import.meta.env.BASE_URL.replace(/\/$/, '')
```

`req()` 改 `fetch(API_ORIGIN + '/api' + path, …)`;`/healthz` 处改 `fetch(API_ORIGIN + '/healthz', …)`。

- [ ] **Step 4: 7 处裸 fetch/XHR 改造**

每处 `import { API_ORIGIN } from '../api'`(相对层级按文件调整),把
`fetch('/api/…')` → `fetch(API_ORIGIN + '/api/…')`;
`Videos.tsx:247` 的 `xhr.open('POST', '/api/videos/upload')` → `xhr.open('POST', API_ORIGIN + '/api/videos/upload')`。

逐处清单:`Step1Proxies.tsx:37`、`Videos.tsx:135`、`Videos.tsx:247`、`Accounts.tsx:407`、`Accounts.tsx:724`、`Accounts.tsx:782`、`Accounts.tsx:884`。
完成后断言无残留:

```bash
grep -rn "fetch('/api\|fetch(\"/api\|open('POST', '/api" apps/studio/frontend/src && echo "有残留,继续改" || echo OK
```

- [ ] **Step 5: build 断言**

```bash
pnpm --filter @svm/studio-frontend build
grep -o 'src="/studio/assets/[^"]*"' apps/studio/frontend/dist/index.html | head -3
```

Expected: 资源引用带 `/studio/` 前缀。

- [ ] **Step 6: 后端静态相关 pytest 回归**

```bash
cd apps/studio && uv run pytest -q
```

Expected: 全绿(SPA 挂载逻辑未动,`test_frontend_brand_icon_assets` 等不受影响)。

- [ ] **Step 7: Commit**

```bash
git add apps/studio/frontend
git commit -m "feat(studio): 前端适配 /studio 基路径(vite base + basename + API_ORIGIN)"
```

---

### Task 6: studio `/api/auth/verify` 端点(TDD)

**Files:**
- Modify: `apps/studio/app/api/users.py`
- Test: `apps/studio/tests/test_auth_verify.py`

- [ ] **Step 1: 写失败测试**

`tests/test_auth_verify.py`(conftest 已预置用户 `tester`/`pw` 并提供 `client` fixture,见 `tests/test_auth_users.py` 文件头注释):

```python
"""GET /api/auth/verify — Radar 统一登录的会话校验端点。"""


def _login(client):
    r = client.post("/api/login", json={"username": "tester", "password": "pw"})
    assert r.status_code == 200


def test_verify_ok_with_valid_session(client):
    _login(client)
    r = client.get("/api/auth/verify")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "tester"
    assert isinstance(body["id"], int)


def test_verify_401_without_cookie(client):
    assert client.get("/api/auth/verify").status_code == 401


def test_verify_401_with_tampered_cookie(client):
    client.cookies.set("session", "garbage.token.value")
    assert client.get("/api/auth/verify").status_code == 401


def test_verify_401_after_session_ver_bump(client):
    _login(client)
    # 改密码会 bump session_ver 并 revoke 旧 cookie(与 test_auth_users 的
    # password-change 用例同机制);verify 必须跟着失效。
    r = client.post("/api/me/password",
                    json={"current_password": "pw", "new_password": "longenough9"})
    if r.status_code == 404:  # 若端点路径不同,退化为直接改库
        from sqlmodel import select
        from app.models import User
        from tests.conftest import TestSession  # 按 conftest 实际导出名校正
        with TestSession() as s:
            u = s.exec(select(User).where(User.username == "tester")).one()
            u.session_ver += 1
            s.add(u); s.commit()
    assert client.get("/api/auth/verify").status_code == 401
```

(第 4 个用例的改密路径动手时对照 `test_auth_users.py` 的 password-change 用例取实际端点,二选一删掉另一分支。)

- [ ] **Step 2: 运行确认失败**

```bash
cd apps/studio && uv run pytest tests/test_auth_verify.py -v
```

Expected: FAIL,404(路由不存在)。

- [ ] **Step 3: 实现端点**

`app/api/users.py`(router 内、login 端点旁):

```python
@router.get("/auth/verify")
def auth_verify(user: User = Depends(require_auth)):
    """轻量会话校验,供同域 Radar 的 middleware 转发调用。"""
    return {"id": user.id, "username": user.username}
```

(`require_auth`、`User` 已在该文件 import;路由前缀按该 router 现状对齐,最终路径必须是 `/api/auth/verify`。)

- [ ] **Step 4: 测试通过 + 全量回归**

```bash
uv run pytest tests/test_auth_verify.py -v && uv run pytest -q
```

Expected: 新用例 4 passed,全量绿。

- [ ] **Step 5: Commit**

```bash
git add apps/studio/app/api/users.py apps/studio/tests/test_auth_verify.py
git commit -m "feat(studio): /api/auth/verify 会话校验端点,供 Radar 统一登录"
```

---

### Task 7: studio 登录页支持 `next` 跳转

**Files:**
- Modify: `apps/studio/frontend/src/Login.tsx`

- [ ] **Step 1: 加 safeNext 纯函数与跳转逻辑**

`Login.tsx` 组件外定义:

```ts
// 仅允许站内相对路径,拦 open redirect(//evil.com、/\evil、绝对 URL)
function safeNext(raw: string | null): string | null {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return null
  return raw
}
```

`submit()` 成功分支(`onOk(me)` 处)改为:

```ts
const next = safeNext(new URLSearchParams(window.location.search).get('next'))
if (next) {
  window.location.assign(next)   // 跨产品整页跳转(radar 不在本 SPA 内)
  return
}
if (window.location.pathname.endsWith('/login')) {
  window.history.replaceState(null, '', import.meta.env.BASE_URL)  // 避免 SPA 停在无路由的 /login
}
onOk(me)
```

- [ ] **Step 2: 手测(dev)**

```bash
# 终端 A:cd apps/studio && ./run.sh  或 uv run uvicorn app.main:app --port 8080
# 终端 B:pnpm --filter @svm/studio-frontend dev
```

浏览器验证三条:`localhost:5173/studio/login?next=/` 登录后整页跳根路径;`?next=//evil.com` 登录后**不**跳外域(落 SPA 首页);无 next 时正常进 Dashboard。

- [ ] **Step 3: Commit**

```bash
git add apps/studio/frontend/src/Login.tsx
git commit -m "feat(studio): 登录支持 next 回跳(仅站内路径,防 open redirect)"
```

---

### Task 8: radar middleware 换用 studio 会话(TDD)

**Files:**
- Create: `apps/web/lib/studio-session.ts`
- Test: `apps/web/test/studio-session.test.mjs`
- Modify: `apps/web/middleware.ts`、`apps/web/lib/env.ts`(若 env 走 t3-env 校验)、`.env.example`

- [ ] **Step 1: 写失败测试**

`test/studio-session.test.mjs`(import 方式照该目录现有 `*.test.mjs` 惯例):

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyStudioSession } from '../lib/studio-session.ts';

const URL_ = 'http://127.0.0.1:18080/api/auth/verify';
const mockFetch = (status) => async () => new Response(null, { status });

test('devBypass 直接 ok', async () => {
  assert.equal(await verifyStudioSession('tok', URL_, true, mockFetch(500)), 'ok');
});
test('无 cookie 即 unauthorized(不发请求)', async () => {
  assert.equal(await verifyStudioSession(undefined, URL_, false, async () => { throw new Error('不应调用'); }), 'unauthorized');
});
test('200 → ok / 401 → unauthorized / 5xx → unavailable', async () => {
  assert.equal(await verifyStudioSession('tok', URL_, false, mockFetch(200)), 'ok');
  assert.equal(await verifyStudioSession('tok', URL_, false, mockFetch(401)), 'unauthorized');
  assert.equal(await verifyStudioSession('tok', URL_, false, mockFetch(503)), 'unavailable');
});
test('网络异常 → unavailable;未配 URL → unavailable', async () => {
  assert.equal(await verifyStudioSession('tok', URL_, false, async () => { throw new Error('down'); }), 'unavailable');
  assert.equal(await verifyStudioSession('tok', undefined, false, mockFetch(200)), 'unavailable');
});
```

- [ ] **Step 2: 运行确认失败**

```bash
pnpm --filter web test
```

Expected: FAIL(模块不存在)。

- [ ] **Step 3: 实现 `lib/studio-session.ts`**

```ts
// Radar 不再自持登录:同域 cookie 转发给 studio 的 verify 端点校验。
export type VerifyResult = 'ok' | 'unauthorized' | 'unavailable';

export const STUDIO_SESSION_COOKIE = 'session';

export async function verifyStudioSession(
  cookie: string | undefined,
  verifyUrl: string | undefined,
  devBypass = false,
  fetchImpl: typeof fetch = fetch,
): Promise<VerifyResult> {
  if (devBypass) return 'ok';
  if (!cookie) return 'unauthorized';
  if (!verifyUrl) return 'unavailable';
  try {
    const r = await fetchImpl(verifyUrl, {
      headers: { cookie: `${STUDIO_SESSION_COOKIE}=${cookie}` },
      cache: 'no-store',
    });
    if (r.status === 200) return 'ok';
    if (r.status === 401) return 'unauthorized';
    return 'unavailable';
  } catch {
    return 'unavailable';
  }
}
```

- [ ] **Step 4: 测试通过**

```bash
pnpm --filter web test
```

Expected: PASS。

- [ ] **Step 5: 改写 middleware.ts**

```ts
import createMiddleware from 'next-intl/middleware';
import { NextResponse, type NextRequest } from 'next/server';

import { routing } from '@/i18n/routing';
import { STUDIO_SESSION_COOKIE, verifyStudioSession } from '@/lib/studio-session';

const intlMiddleware = createMiddleware(routing);

export default async function middleware(req: NextRequest) {
  const result = await verifyStudioSession(
    req.cookies.get(STUDIO_SESSION_COOKIE)?.value,
    process.env.STUDIO_AUTH_VERIFY_URL,
    process.env.NODE_ENV !== 'production' && process.env.AUTH_DEV_BYPASS === '1',
  );

  if (result === 'ok') return intlMiddleware(req);
  if (result === 'unauthorized') {
    const next = encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search);
    return NextResponse.redirect(new URL(`/studio/login?next=${next}`, req.url));
  }
  // verify 不可达/未配置:显式 503,绝不重定向(避免循环),也不放行。
  return new NextResponse('auth backend unavailable', { status: 503 });
}

export const config = {
  matcher: '/((?!api|trpc|_next|_vercel|.*\\..*).*)',
};
```

- [ ] **Step 6: env 样例与校验**

`.env.example` 删 `AUTH_SECRET`,加:

```
# Radar 统一登录:studio verify 端点(生产 host 网络直达 uvicorn)
STUDIO_AUTH_VERIFY_URL=http://127.0.0.1:18080/api/auth/verify
# 本地开发不想起 studio 时:AUTH_DEV_BYPASS=1
```

若 `apps/web/lib/env.ts` 用 t3-env 声明了 `AUTH_SECRET`,同步替换为 `STUDIO_AUTH_VERIFY_URL`(optional string)。

- [ ] **Step 7: typecheck + 全量测试 + Commit**

```bash
pnpm --filter web typecheck && pnpm --filter web test
git add apps/web/lib/studio-session.ts apps/web/test/studio-session.test.mjs apps/web/middleware.ts apps/web/lib/env.ts .env.example
git commit -m "feat(web): middleware 改用 studio 会话统一登录"
```

---

### Task 9: radar 旧口令门退役

**Files:**
- Delete: `apps/web/lib/auth-token.ts`、`apps/web/lib/login-guard.ts`、`apps/web/app/[locale]/login/page.tsx`
- Modify: 引用它们的一切(login server action、i18n 文案、可能的 logout 入口)

- [ ] **Step 1: 找全引用**

```bash
grep -rn "auth-token\|login-guard\|AUTH_COOKIE\|AUTH_SECRET\|svm_auth" apps/web --include="*.ts*" -l
```

- [ ] **Step 2: 删除与清理**

`git rm` 三个文件;逐个清理引用文件(登录 server action、layout 里可能的登出按钮换成指向 studio 的登出或直接移除——studio 登出后刷新 radar 自然 302 到登录页);删 `messages/*.json` 里 login 命名空间文案(若存在)。

- [ ] **Step 3: 验证无残留 + 全绿**

```bash
grep -rn "AUTH_SECRET\|svm_auth\|auth-token\|login-guard" apps/web --include="*.ts*" && echo 残留 || echo OK
pnpm --filter web typecheck && pnpm --filter web test && pnpm --filter web build
```

Expected: OK + 全绿 + build 成功。

- [ ] **Step 4: Commit**

```bash
git add -A apps/web
git commit -m "feat(web): 退役单用户口令门(登录统一到 studio)"
```

---

### Task 10: shared 产品常量 + radar 切换器

**Files:**
- Create: `packages/shared/src/products.ts`
- Modify: `packages/shared/package.json`(exports)、`packages/shared/src/index.ts`
- Create: `apps/web/components/product-switcher.tsx`
- Modify: `apps/web/app/[locale]/layout.tsx`

- [ ] **Step 1: products 常量**

`packages/shared/src/products.ts`:

```ts
export type ProductId = 'radar' | 'studio';

export type Product = { id: ProductId; name: string; path: string };

// 门户统一后的两个产品入口。path 是站内绝对路径(整页跳转)。
export const PRODUCTS: readonly Product[] = [
  { id: 'radar', name: 'Radar', path: '/' },
  { id: 'studio', name: 'Studio', path: '/studio/' },
] as const;
```

`package.json` 的 `exports` 加 `"./products": "./src/products.ts"`;`src/index.ts` 加 `export * from './products';`。

- [ ] **Step 2: radar 切换器组件**

`apps/web/components/product-switcher.tsx`(无交互,纯 `<a>`,server component 即可):

```tsx
import { PRODUCTS, type ProductId } from '@svm/shared/products';

export function ProductSwitcher({ current }: { current: ProductId }) {
  return (
    <div className="mb-4 flex rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-0.5">
      {PRODUCTS.map((p) =>
        p.id === current ? (
          <span
            key={p.id}
            className="flex-1 rounded-md bg-[color:var(--color-primary)] px-2 py-1 text-center text-xs font-semibold text-[color:var(--color-primary-foreground)]"
          >
            {p.name}
          </span>
        ) : (
          <a
            key={p.id}
            href={p.path}
            className="flex-1 rounded-md px-2 py-1 text-center text-xs font-semibold text-[color:var(--color-muted-foreground)] hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)]"
          >
            {p.name}
          </a>
        ),
      )}
    </div>
  );
}
```

- [ ] **Step 3: 挂进 radar 侧栏**

`layout.tsx` 的 logo `<Link>`(`:44-56`)之后、`<nav>` 之前插入:

```tsx
<ProductSwitcher current="radar" />
```

(import 加到文件头。)

- [ ] **Step 4: 验证 + Commit**

```bash
pnpm --filter web typecheck && pnpm --filter web build
git add packages/shared apps/web/components/product-switcher.tsx apps/web/app/\[locale\]/layout.tsx
git commit -m "feat: 产品切换器(shared 常量 + radar 侧)"
```

---

### Task 11: studio 切换器

**Files:**
- Modify: `apps/studio/frontend/package.json`(加依赖 `"@svm/shared": "workspace:*"`)
- Create: `apps/studio/frontend/src/components/ProductSwitcher.tsx`
- Modify: `apps/studio/frontend/src/components/Shell.tsx`

- [ ] **Step 1: 依赖 + 组件**

`pnpm --filter @svm/studio-frontend add '@svm/shared@workspace:*'`

`ProductSwitcher.tsx`(与 radar 版视觉同构;token 名换肤后一致,直接用同一套类名):

```tsx
import { PRODUCTS, type ProductId } from '@svm/shared/products'

export function ProductSwitcher({ current }: { current: ProductId }) {
  return (
    <div className="mb-4 flex rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-0.5">
      {PRODUCTS.map((p) =>
        p.id === current ? (
          <span key={p.id} className="flex-1 rounded-md bg-[var(--color-primary)] px-2 py-1 text-center text-xs font-semibold text-[var(--color-primary-foreground)]">
            {p.name}
          </span>
        ) : (
          <a key={p.id} href={p.path} className="flex-1 rounded-md px-2 py-1 text-center text-xs font-semibold text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)]">
            {p.name}
          </a>
        ),
      )}
    </div>
  )
}
```

注意:本 Task 在换肤(Task 12)**之后**执行才有这些 token;若顺序执行到此,直接按计划顺序 Task 12→13→14 先做,再回来。**执行顺序按:12 → 13 → 14 → 10 → 11 → 15。**(Task 10 radar 侧不依赖换肤,可提前;studio 侧依赖。)

- [ ] **Step 2: 挂进 Shell logo 区**

`Shell.tsx` 的 logo/标题区(`:132-150` 附近,`src={appLogoUrl}` 所在块)之后插 `<ProductSwitcher current="studio" />`;若 tsc 对 workspace TS 源解析报错,在 `frontend/tsconfig.app.json` 加:

```json
"paths": { "@svm/shared/*": ["../../../packages/shared/src/*"] }
```

- [ ] **Step 3: 验证 + Commit**

```bash
pnpm --filter @svm/studio-frontend build
git add apps/studio/frontend
git commit -m "feat(studio): 左上角产品切换器"
```

---

### Task 12: 换肤 A——token 语义迁移

**Files:**
- Modify: `apps/studio/frontend/src/index.css`(@theme 整体重写)
- Modify: `apps/studio/frontend/src/**/*.tsx`(var 引用机械替换)

- [ ] **Step 1: 重写 @theme**

`index.css` 的 `@theme` 块替换为(radar 语义 + studio 扩展;**动效/滚动条/focus 等块保留**,其中引用的旧变量按下方映射同步改名):

```css
@theme {
  /* — radar 语义(与 apps/web/app/globals.css 同值) — */
  --color-background: hsl(0 0% 100%);
  --color-foreground: hsl(222.2 84% 4.9%);
  --color-card: hsl(0 0% 100%);
  --color-muted: hsl(210 40% 96.1%);
  --color-muted-foreground: hsl(215.4 16.3% 46.9%);
  --color-primary: hsl(222.2 47.4% 11.2%);
  --color-primary-foreground: hsl(210 40% 98%);
  --color-destructive: hsl(0 84.2% 60.2%);
  --color-border: hsl(214.3 31.8% 91.4%);
  --color-input: hsl(214.3 31.8% 91.4%);
  --color-ring: hsl(222.2 84% 4.9%);
  --color-sidebar: hsl(0 0% 98%);
  --color-sidebar-foreground: hsl(240 5.3% 26.1%);
  --color-sidebar-border: hsl(220 13% 91%);

  /* — studio 扩展灰阶(radar 色相系派生,保层次) — */
  --color-foreground-2: hsl(222 30% 22%);
  --color-muted-foreground-2: hsl(215 16% 58%);
  --color-muted-foreground-3: hsl(215 16% 70%);
  --color-border-strong: hsl(215 20% 80%);

  /* — 状态色(green/orange 对齐 radar tier;蓝紫黄为 studio 信息色) — */
  --color-blue: #2563eb;
  --color-blue-bg: #eaf1ff;
  --color-destructive-bg: hsl(0 84% 97%);
  --color-green: hsl(142 71% 45%);
  --color-green-bg: #ecfdf3;
  --color-orange: hsl(38 92% 50%);
  --color-orange-bg: #fff7ed;
  --color-yellow: #a16207;
  --color-yellow-bg: #fef3c7;
  --color-purple: #7c3aed;
  --color-purple-bg: #f3e8ff;
  --color-gray-bg: #eef1f6;

  /* — radius:卡片对齐 radar rounded-xl,控件 rounded-md — */
  --radius-sm: 6px;
  --radius: 8px;
  --radius-lg: 12px;

  /* — shadows(保留,浅) — */
  --shadow-sm: 0 1px 1px rgba(15, 23, 42, 0.04);
  --shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 8px 24px rgba(15, 23, 42, 0.04);
  --shadow-lg: 0 16px 48px rgba(15, 23, 42, 0.12);

  /* — type:radar 系统中文栈 — */
  --font-sans: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei UI', sans-serif;
  --font-mono: ui-monospace, 'SF Mono', 'Menlo', 'Monaco', monospace;
}
```

同文件其余部分:`html,body,#root` 的 `background: var(--color-canvas)` → `var(--color-background)`、`color: var(--color-ink)` → `var(--color-foreground)`;滚动条 `--color-line-strong` → `--color-border-strong`、`--color-ink-5` → `--color-muted-foreground-3`;focus-visible 的 `--color-blue` 改 `--color-ring`(70% mix 改为 `color-mix(in srgb, var(--color-ring) 30%, white)`);`::selection` 保留 blue-bg;`--font-serif` 删除(无引用则删,有引用保留)。

- [ ] **Step 2: 全局机械替换(顺序敏感:长名在前)**

```bash
cd apps/studio/frontend/src
# ink 系(必须先 5/4/3/2 后裸 ink)
grep -rl -- '--color-ink-5' . | xargs sed -i '' 's/--color-ink-5/--color-muted-foreground-3/g'
grep -rl -- '--color-ink-4' . | xargs sed -i '' 's/--color-ink-4/--color-muted-foreground-2/g'
grep -rl -- '--color-ink-3' . | xargs sed -i '' 's/--color-ink-3/--color-muted-foreground/g'
grep -rl -- '--color-ink-2' . | xargs sed -i '' 's/--color-ink-2/--color-foreground-2/g'
grep -rl -- '--color-ink'   . | xargs sed -i '' 's/--color-ink/--color-foreground/g'
# 面板/线条(line-strong 先于 line;surface-hover 先于 surface)
grep -rl -- '--color-line-strong'   . | xargs sed -i '' 's/--color-line-strong/--color-border-strong/g'
grep -rl -- '--color-line'          . | xargs sed -i '' 's/--color-line/--color-border/g'
grep -rl -- '--color-surface-hover' . | xargs sed -i '' 's/--color-surface-hover/--color-muted/g'
grep -rl -- '--color-surface'       . | xargs sed -i '' 's/--color-surface/--color-card/g'
grep -rl -- '--color-canvas'        . | xargs sed -i '' 's/--color-canvas/--color-background/g'
grep -rl -- '--color-panel'         . | xargs sed -i '' 's/--color-panel/--color-sidebar/g'
# 红系(hover 变体并入主色;-bg 改名)
grep -rl -- '--color-red-hover' . | xargs sed -i '' 's/--color-red-hover/--color-destructive/g'
grep -rl -- '--color-red-bg'    . | xargs sed -i '' 's/--color-red-bg/--color-destructive-bg/g'
grep -rl -- '--color-red'       . | xargs sed -i '' 's/--color-red/--color-destructive/g'
```

**不 sed**:`--color-blue*`(行动/信息语义要逐处判断,Task 14)、`--color-accent*`(Task 14 清除)、green/orange/yellow/purple/gray-bg(名字不变,只换了值)。

- [ ] **Step 3: build + 残留断言**

```bash
pnpm --filter @svm/studio-frontend build
grep -rn -- '--color-ink\|--color-line\|--color-canvas\|--color-surface\|--color-panel\|--color-red' apps/studio/frontend/src && echo 残留 || echo OK
```

- [ ] **Step 4: Commit**

```bash
git add apps/studio/frontend/src
git commit -m "restyle(studio): token 语义迁移到 radar 体系(机械替换)"
```

---

### Task 13: 换肤 B——ui.tsx 组件形态对齐

**Files:**
- Modify: `apps/studio/frontend/src/components/ui.tsx`

对齐规格(radar `apps/web/components/ui.tsx` 为准):

| 组件 | 改动 |
|---|---|
| `Button` | primary variant:`bg-[var(--color-blue)]` 系 → `bg-[var(--color-primary)] text-[var(--color-primary-foreground)] hover:opacity-90`;secondary:白底 `border border-[var(--color-border)] bg-[var(--color-card)] hover:bg-[var(--color-muted)]`;danger:红字红边浅底(`text-[var(--color-destructive)] border-[hsl(0_84%_85%)] hover:bg-[var(--color-destructive-bg)]`);圆角统一 `rounded-md`;字重 `font-semibold` |
| `Toggle`/`SegmentedControl` | 选中态蓝 → `--color-primary` |
| `ProgressBar` | 进度条主色蓝 → `--color-primary` |
| `Card`/`Panel`/`MetricCard`/`StatCard`/`DataTableShell` | 圆角 `rounded-[var(--radius-lg)]`(现 12px)+ `border-[var(--color-border)]` + `shadow-[var(--shadow-sm)]`(≈radar `rounded-xl border shadow-sm`) |
| `Th` | 对齐 radar:`text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]` |
| `TextField`(input/select) | `rounded-md border-[var(--color-input)]`,focus:`ring-2 ring-[var(--color-ring)]/20 border-[var(--color-ring)]`(替换原蓝色 focus) |
| `Badge`/`Dot`/`Toast` | **信息色保留**(blue/green/red→destructive/orange/yellow/gray 已由 token 迁移调值),只把圆角/字号对齐,不动 tone 结构 |
| `Modal`/`Drawer`/`EmptyState`/`Toolbar`/`FilterBar`/`PageHeader`/`PageTitle`/`SectionTitle` | 仅颜色类随 token 已迁移,复查圆角 `radius-lg` 与标题字重(`font-bold` 对齐 radar 标题) |

- [ ] **Step 1: 按上表逐组件修改**(27 个组件顺文件过一遍;`var(--color-blue)` 在此文件中凡属"交互激活/主行动"一律改 `--color-primary`,属"信息展示"保留)
- [ ] **Step 2: build + 目检**

```bash
pnpm --filter @svm/studio-frontend build
grep -n -- 'var(--color-blue)' apps/studio/frontend/src/components/ui.tsx   # 剩余应全部是信息色语义,逐行确认
```

- [ ] **Step 3: Commit**

```bash
git add apps/studio/frontend/src/components/ui.tsx
git commit -m "restyle(studio): ui.tsx 组件形态对齐 radar(primary/圆角/focus ring)"
```

---

### Task 14: 换肤 C——Shell、登录页与 blue/accent 清点

**Files:**
- Modify: `apps/studio/frontend/src/components/Shell.tsx`、`src/Login.tsx`、`src/components/GroupRail.tsx`、各 pages 残余

- [ ] **Step 1: accent 清除**

```bash
grep -rn -- '--color-accent' apps/studio/frontend/src
```

逐处改 `--color-primary`(已知:`Login.tsx` tabClass 激活下划线 `border-[var(--color-accent)]`;其余以 grep 为准)。完成后:

```bash
grep -rn -- '--color-accent\|f54e00' apps/studio/frontend/src && echo 残留 || echo OK
```

- [ ] **Step 2: blue 语义清点(ui.tsx 之外)**

```bash
grep -rn -- 'color-blue\|bg-blue\|text-blue\|border-blue' apps/studio/frontend/src --include='*.tsx' | grep -v components/ui.tsx
```

判定规则:**按钮/选中态/激活 tab/焦点 = `--color-primary`;链接/状态徽章/信息图标 = 保留 blue**。逐处处理(基线约 6 处 `bg-blue` + 若干 var 引用)。

- [ ] **Step 3: Shell 侧栏对齐 radar**

`Shell.tsx` `<aside>`:`bg-[var(--color-card)]` → `bg-[var(--color-sidebar)]`,边框已是 `--color-border`;NavLink 激活态若为蓝底,改 radar 式(`bg-[var(--color-muted)] text-[var(--color-foreground)]` 或 primary 左指示条,取现状最小改);语言/登出等次要按钮跟随 ui.tsx 新样式。

- [ ] **Step 4: 登录页对齐**

`Login.tsx`:页面容器 `bg-[var(--color-card)]` 改 `bg-[var(--color-background)]`;主按钮已随 ui.tsx `Button`;tab 激活下划线已改 primary(Step 1);检查输入框 focus 已是 ring 风格。

- [ ] **Step 5: build + Commit**

```bash
pnpm --filter @svm/studio-frontend build
git add apps/studio/frontend/src
git commit -m "restyle(studio): Shell/登录页对齐 radar;accent 清除、blue 语义清点"
```

---

### Task 15: 换肤与集成验收

- [ ] **Step 1: 全量回归**

```bash
cd apps/studio && uv run pytest -q && cd ../..
pnpm --filter web test && pnpm --filter web typecheck
pnpm --filter @svm/studio-frontend build && pnpm --filter web build
```

Expected: 全绿。

- [ ] **Step 2: 残留扫描(spec §8 断言)**

```bash
grep -rn -- 'f54e00\|--color-canvas\|--color-ink\|--color-line\|--color-accent' apps/studio/frontend/src && echo FAIL || echo OK
```

- [ ] **Step 3: 逐页目检**

起 dev(studio 后端 :8080 + `pnpm --filter @svm/studio-frontend dev`),浏览器过:Dashboard / Accounts / Videos / Comments / FbComments / Costs / Ops / Sagas / Settings / 登录页 / 任一 Modal + Drawer + Toast + wizard。核对:无灰 canvas 底、无橙红、主按钮近黑、focus ring 近黑、切换器两端显示正确。与 radar 页面并排目视对比一次。

- [ ] **Step 4: Commit(如目检有修补)**

```bash
git add -A apps/studio/frontend && git commit -m "restyle(studio): 目检修补"
```

---

### Task 16: 部署物料(nginx / runbook / DEPLOY.md)

**Files:**
- Create: `apps/studio/deploy/nginx-igotyoufrank-studio.conf`(radar.conf 内追加段,版本化存档)
- Modify: `apps/studio/deploy/nginx-mkt-domain.conf`(改为 301)
- Modify: `docs/DEPLOY.md`、`apps/studio/docs/production-deploy.md`

- [ ] **Step 1: /studio 反代段(新文件,内容将并入服务器 radar.conf 的 443 server)**

```nginx
    # ---- GeeLark Studio(igotyoufrank.cn/studio → uvicorn :18080)----
    # upstream mkt_backend 定义在 nginx-mkt.conf,同 nginx 实例内共享。
    location = /studio { return 301 /studio/; }

    # SSE:免缓冲(正则 location 不能带 URI 的 proxy_pass,用 rewrite 剥前缀)
    location = /studio/api/videos/publish-stream {
        rewrite ^/studio(/.*)$ $1 break;
        proxy_pass http://mkt_backend;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location ~ ^/studio/api/groups/[0-9]+/phones/create$ {
        rewrite ^/studio(/.*)$ $1 break;
        proxy_pass http://mkt_backend;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /studio/ {
        proxy_pass http://mkt_backend/;   # 尾斜杠:剥掉 /studio 前缀
        client_max_body_size 2048m;
        client_body_timeout 600s;
        proxy_http_version 1.1;
        proxy_connect_timeout 10s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
    }
```

- [ ] **Step 2: mkt 子域改 301**

`nginx-mkt-domain.conf` 的 443 server 内容替换为(80 段与证书段保留):

```nginx
    # 门户统一后:全部 301 到主域 /studio。证书保留续期,GeeLark 回调走 :8080 不受影响。
    location / {
        return 301 https://igotyoufrank.cn/studio$request_uri;
    }
```

- [ ] **Step 3: 文档更新**

- `docs/DEPLOY.md`:D 节追加"studio 并入 /studio"小节(nginx 段引用上述文件、radar env 增删、上线序列与回滚——照 spec §7 五步扩写成命令级 checklist:备份 conf/tarball/镜像 → 部署 studio → 放 conf + `nginx -t && systemctl reload nginx` → 部署 radar → 冒烟四连 → 回滚三路命令)。
- `apps/studio/docs/production-deploy.md`:tarball 打包命令改 `tar -C apps/studio …`(与 Task 3 的 CI 一致);访问地址改 `https://igotyoufrank.cn/studio/`;注明裸 IP `:8080` 仅服务 GeeLark 回调、浏览器打开静态资源 404 属预期。
- 冒烟清单(写进 DEPLOY.md):

```bash
curl -fsS https://igotyoufrank.cn/healthz || true            # radar 可用(如无此端点,curl / 看 200)
curl -fsS https://igotyoufrank.cn/studio/healthz              # 经 nginx strip 到 studio
curl -fsS http://139.196.52.92:8080/healthz                   # GeeLark 回调通路
curl -s -o /dev/null -w '%{http_code}\n' https://igotyoufrank.cn/studio/api/accounts   # 401(未登录)
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: mkt.igotyoufrank.cn' https://139.196.52.92/ -k  # 301
# 浏览器:登录 → radar 看板 → 切换器互跳 → studio Videos/SSE → 登出重登
```

- [ ] **Step 4: Commit**

```bash
git add apps/studio/deploy docs/DEPLOY.md apps/studio/docs/production-deploy.md
git commit -m "deploy: /studio nginx 段、mkt 301、上线与回滚 runbook"
```

---

### Task 17: 上线窗口(需用户在场/授权,不自动执行)

> 生产操作,按 `docs/DEPLOY.md` 新增 checklist 逐条执行;此处只列骨架。

- [ ] merge 分支到 main(本地全绿后)
- [ ] `gh secret set PROD_SSH_PRIVATE_KEY / PROD_SSH_KNOWN_HOSTS` + `gh variable set PROD_*`(值同旧仓库;或首次上线纯手动、CI 后补),移除 Task 3 的 `if: false`(若加过)
- [ ] 服务器:备份 nginx conf + `/opt/mkt` 旧 tarball + radar 旧镜像
- [ ] 部署新 studio(手动 tarball 或触发 CI)→ 放 nginx 新 conf → `nginx -t && systemctl reload nginx`
- [ ] 部署新 radar 镜像(env 加 `STUDIO_AUTH_VERIFY_URL`、删 `AUTH_SECRET`)并重启
- [ ] 跑冒烟清单(Task 16 Step 3)
- [ ] `gh repo archive yuyuyuyu52/geelark-studio`(确认新家稳定后)
- [ ] 回滚预案:恢复 conf + reload;旧 tarball 解包 + `systemctl restart mkt mkt-worker`;旧镜像重启——三路独立

---

## 任务顺序

```
前置 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 12 → 13 → 14 → 10 → 11 → 15 → 16 → 17
```

(10 可提前到 9 后任意点;11 依赖 12-14 的 token。)

## Self-review 已做

- spec 逐节对照:§1→T1-4,§2→T5+T16,§3→T6-9,§4→T10-11,§5→T12-14,§6→T16,§7→T16-17,§8→T15。无缺口。
- 无 TBD/占位符;测试代码给出(fixture 细节指向既有同构文件 `test_auth_users.py` 属可执行指引)。
- 类型/命名一致性:`verifyStudioSession`/`STUDIO_SESSION_COOKIE`/`API_ORIGIN`/`ProductSwitcher`/`@svm/studio-frontend` 全文一致;token 新名(`--color-foreground-2` 等)在 T12 定义、T13/14 使用一致。
