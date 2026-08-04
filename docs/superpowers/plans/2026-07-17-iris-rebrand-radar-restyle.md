# Iris 品牌统一 + studio 全面 radar 风格重写 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把整个项目品牌统一为 Iris(名称 + radar 图标全局唯一 + 切换器改监控/运营),并把 studio 前端从"密排运营控制台"全面重写成 radar 的"清爽留白"视觉语言(功能/结构/后端不变)。

**Architecture:** 方案 A 设计体系先行——先固化 radar 视觉语言(spec §1 表),先改 studio `ui.tsx` 的 27 个组件(杠杆,页面都是组件拼的),再扫 9 页 + Shell + Login 的页面级密度;品牌文案 + 图标资产按 grep 清单精确替换。纯 className / 文案 / 图标改动,不碰组件 props/API、路由、后端、动效。

**Tech Stack:** studio 前端 Vite + React 19 + Tailwind v4;radar Next.js 15;两侧 i18n(studio react-i18next `src/i18n/locales/{zh,en}.json`,radar next-intl `messages/{zh,en}.json`)。

**Spec:** `docs/superpowers/specs/2026-07-17-iris-rebrand-radar-restyle-design.md`(§1 视觉语言对照表是重写的唯一标尺,每个 UI 任务都以它为目标)

**关键既有事实**(执行者无需重新侦察):

- **品牌名 vs 厂商名的区分(最重要)**:要改成 Iris 的是**本应用品牌名**——`Creator Radar`、`GeeLark Studio`。要**保留**的是**被运营的外部厂商 GeeLark 云手机服务**的引用:`VideoDetailPanel.tsx:29` 的 `'GeeLark'`(评论来源=GeeLark AI)、`Sagas.tsx:197`/`Ops.tsx:167` 的 `GeeLark task`(GeeLark RPA 任务)、`Costs.tsx:67,417` 的 `GeeLark charges/reports`(GeeLark 计费)。这些名的是外部服务,不能改。以及内部标识 `geelark_client`、`GEELARK_TOKEN`、`geelark-studio-frontend`(包名)、api source `'geelark'` 全部保留。
- 品牌字符串精确触点:radar `app/[locale]/layout.tsx:55` `Creator Radar`、`app/manifest.ts:5-7` `Creator Radar`×3;studio `src/Login.tsx:95` `GeeLark Studio` + 页脚 `GeeLark Studio · internal ops console`、`index.html:11,14,16` ×3、`public/manifest.webmanifest:2-3`、`components/Shell.tsx` 顶部品牌区。
- radar 图标源:`apps/web/app/icon.png`(1.4MB PNG)+ `apps/web/app/apple-icon.png`。studio 要替换的:`frontend/public/{app-icon.png,favicon.png,apple-touch-icon.png}`、`frontend/src/assets/app-logo.png`(Shell/Login 的 bundled logo,当前是猫头鹰)。
- studio 图标断言测试:`apps/studio/tests/test_frontend_brand_icon_assets.py` 断言 `app-logo.png` 尺寸=128×128、字节<80000,且 Login/Shell 用 `appLogoUrl` 而非 `/app-icon.png`——换图标后按新图标尺寸/字节同步改断言。
- `packages/shared/src/products.ts` 现为 `{ id, name, path }[]`,radar `components/product-switcher.tsx` 和 studio `src/components/ProductSwitcher.tsx` 都渲染 `p.name`。
- radar 视觉语言取自 `apps/web/components/ui.tsx`(Card=`rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-card)] shadow-sm`;Button primary=`bg-primary text-primary-foreground hover:opacity-90` `h-9 px-4 text-sm` `rounded-md font-semibold`;Th=`px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground`;Td=`px-4 py-3 text-sm`)+ `page.tsx`(容器 `mx-auto max-w-[1760px] px-6 py-10`;标题 `text-2xl font-bold tracking-tight` + `mb-6`;块 `space-y-6`;列 `gap-4`)。
- studio `ui.tsx` 27 组件(1016 行):Button/Badge/Card/PageTitle/SectionTitle/PageHeader/Panel/Toolbar/FilterBar/SegmentedControl/MetricCard/DataTableShell/StatCard/TextField/Toggle/ProgressBar/Table/THead/Th/TBody/Tr/Td/Modal/EmptyState/Toast/Dot/Drawer。
- studio 现网已上线;本期上线走 `docs/DEPLOY.md` §E(tar 不删文件的坑已知,但本期无文件删除,低风险)。

---

## 前置

- [ ] 工作区干净(`git status --porcelain` 空);当前在 main。新建分支:`git checkout -b iris-rebrand-restyle`

---

### Task 1: 产品常量瘦身 + 切换器改「监控/运营」(两侧 i18n)

**Files:**
- Modify: `packages/shared/src/products.ts`
- Modify: `apps/web/components/product-switcher.tsx`、`apps/web/messages/zh.json`、`apps/web/messages/en.json`
- Modify: `apps/studio/frontend/src/components/ProductSwitcher.tsx`、`apps/studio/frontend/src/i18n/locales/zh.json`、`apps/studio/frontend/src/i18n/locales/en.json`

- [ ] **Step 1: products.ts 去掉 name(避免无 i18n 的常量写死语言)**

```ts
export type ProductId = 'radar' | 'studio';

export type Product = { id: ProductId; path: string };

// 门户统一后的两个产品入口。展示标签由各侧 i18n 出(见各自 ProductSwitcher),
// 这里只保留内部 id 与站内路径。
export const PRODUCTS: readonly Product[] = [
  { id: 'radar', path: '/' },
  { id: 'studio', path: '/studio/' },
] as const;
```

- [ ] **Step 2: radar 侧切换器本地化**

`apps/web/messages/zh.json` 与 `en.json` 各在 `layout`(或新建 `switcher`)命名空间加两个 key。zh:`"monitor": "监控", "ops": "运营"`;en:`"monitor": "Monitor", "ops": "Ops"`。放进现有 `layout` 命名空间即可(layout.tsx 已 `getTranslations({namespace:'layout'})`)。

`apps/web/components/product-switcher.tsx` 改为按 id 取本地化标签(server component 用 `getTranslations`):

```tsx
import { getTranslations } from 'next-intl/server';
import { PRODUCTS, type ProductId } from '@svm/shared/products';

export async function ProductSwitcher({ current }: { current: ProductId }) {
  const t = await getTranslations('layout');
  const label: Record<ProductId, string> = { radar: t('monitor'), studio: t('ops') };
  return (
    <div className="mb-4 flex rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-0.5">
      {PRODUCTS.map((p) =>
        p.id === current ? (
          <span key={p.id} className="flex-1 rounded-md bg-[color:var(--color-primary)] px-2 py-1 text-center text-xs font-semibold text-[color:var(--color-primary-foreground)]">
            {label[p.id]}
          </span>
        ) : (
          <a key={p.id} href={p.path} className="flex-1 rounded-md px-2 py-1 text-center text-xs font-semibold text-[color:var(--color-muted-foreground)] hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)]">
            {label[p.id]}
          </a>
        ),
      )}
    </div>
  );
}
```

注意:`ProductSwitcher` 现在是 async。`layout.tsx` 里 `<ProductSwitcher current="radar" />` 在 async server component 内,直接 `await` 渲染即可——若 tsx 报 async 组件不能直接用,改成 `{await ProductSwitcher({current:'radar'})}` 或保持 `<ProductSwitcher .../>`(Next 15 支持 async 组件)。build 会验证。

- [ ] **Step 3: studio 侧切换器本地化**

`apps/studio/frontend/src/i18n/locales/zh.json` 加 `"switcher": { "monitor": "监控", "ops": "运营" }`;`en.json` 加 `"switcher": { "monitor": "Monitor", "ops": "Ops" }`(键路径按该文件现有嵌套风格放置)。

`apps/studio/frontend/src/components/ProductSwitcher.tsx` 用 `useTranslation` 出标签:

```tsx
import { useTranslation } from 'react-i18next'
import { PRODUCTS, type ProductId } from '@svm/shared/products'

export function ProductSwitcher({ current }: { current: ProductId }) {
  const { t } = useTranslation()
  const label: Record<ProductId, string> = { radar: t('switcher.monitor'), studio: t('switcher.ops') }
  return (
    <div className="mb-4 flex rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-0.5">
      {PRODUCTS.map((p) =>
        p.id === current ? (
          <span key={p.id} className="flex-1 rounded-md bg-[var(--color-primary)] px-2 py-1 text-center text-xs font-semibold text-[var(--color-primary-foreground)]">
            {label[p.id]}
          </span>
        ) : (
          <a key={p.id} href={p.path} className="flex-1 rounded-md px-2 py-1 text-center text-xs font-semibold text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)]">
            {label[p.id]}
          </a>
        ),
      )}
    </div>
  )
}
```

- [ ] **Step 4: build 两侧**

```bash
pnpm --filter web typecheck && pnpm --filter web build 2>&1 | tail -3
pnpm --filter @svm/studio-frontend build 2>&1 | tail -3
```
Expected: 都成功;切换器显示监控/运营(radar 双语、studio 双语)。

- [ ] **Step 5: Commit**

```bash
git add packages/shared apps/web/components/product-switcher.tsx apps/web/messages apps/studio/frontend/src/components/ProductSwitcher.tsx apps/studio/frontend/src/i18n
git commit -m "feat: 切换器改「监控/运营」,标签走两侧 i18n"
```

---

### Task 2: Iris 品牌文案替换(保留 GeeLark 厂商引用)

**Files:**
- Modify: `apps/web/app/[locale]/layout.tsx`、`apps/web/app/manifest.ts`
- Modify: `apps/studio/frontend/src/Login.tsx`、`apps/studio/frontend/index.html`、`apps/studio/frontend/public/manifest.webmanifest`、`apps/studio/frontend/src/components/Shell.tsx`

- [ ] **Step 1: radar 品牌名**

- `apps/web/app/[locale]/layout.tsx:55`:`Creator Radar` → `Iris`。
- `apps/web/app/manifest.ts`:`name`/`short_name` → `Iris`;`description` → `Iris dashboard`。

- [ ] **Step 2: studio 品牌名(只改品牌,保留 GeeLark 厂商词)**

- `apps/studio/frontend/src/Login.tsx:95`:`GeeLark Studio` → `Iris`。
- Login 页脚 `GeeLark Studio · internal ops console`(grep `internal ops console` 定位)→ `Iris · ops console`。
- `apps/studio/frontend/index.html`:`application-name` content、description content、`<title>` 三处 `GeeLark Studio` → `Iris`(description 可写 `Iris dashboard for cloud-phone account operations.`)。
- `apps/studio/frontend/public/manifest.webmanifest`:`"name": "GeeLark Studio"` → `"Iris"`;`"short_name": "GeeLark"` → `"Iris"`。
- `apps/studio/frontend/src/components/Shell.tsx`:顶部品牌区的 `GeeLark Studio` 文字 → `Iris`(grep Shell.tsx 里 `GeeLark`,只改品牌文字标题,不碰导航项/厂商词)。

- [ ] **Step 2.5: 确认没误伤厂商引用**

```bash
grep -rn 'GeeLark' apps/studio/frontend/src apps/web/app apps/web/components --include='*.tsx' --include='*.ts'
```
剩余命中**必须只是厂商引用**:`VideoDetailPanel.tsx`(source label)、`Sagas.tsx`/`Ops.tsx`(`GeeLark task`)、`Costs.tsx`(注释 + `GeeLark charges`)。这些保留。若还有品牌义 `GeeLark Studio` 残留,继续改。

- [ ] **Step 3: build + 品牌 grep**

```bash
pnpm --filter web build 2>&1 | tail -2
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
grep -rn 'GeeLark Studio\|Creator Radar' apps/web apps/studio/frontend/src apps/studio/frontend/index.html apps/studio/frontend/public --include='*.tsx' --include='*.ts' --include='*.html' --include='*.webmanifest' && echo 残留 || echo OK
```
Expected: build 成功;第二个 grep 归零(OK)。

- [ ] **Step 4: Commit**

```bash
git add apps/web/app apps/studio/frontend/src apps/studio/frontend/index.html apps/studio/frontend/public/manifest.webmanifest
git commit -m "feat: 应用品牌名统一为 Iris(保留 GeeLark 厂商引用)"
```

---

### Task 3: 图标全局统一为 radar 图标

**Files:**
- Replace: `apps/studio/frontend/public/{app-icon.png,favicon.png,apple-touch-icon.png}`、`apps/studio/frontend/src/assets/app-logo.png`
- Modify: `apps/studio/tests/test_frontend_brand_icon_assets.py`

- [ ] **Step 1: 生成/替换图标**

radar 源图标 `apps/web/app/icon.png`(大图)。studio 各资产按用途替换:
- `app-logo.png`(Shell/Login 的 bundled logo,当前断言 128×128 < 80KB):从 radar `icon.png` 生成 128×128 PNG。用 sips(macOS 自带):
  ```bash
  sips -s format png -z 128 128 apps/web/app/icon.png --out apps/studio/frontend/src/assets/app-logo.png
  ```
- `favicon.png`:32×32 或 48×48:`sips -z 48 48 apps/web/app/icon.png --out apps/studio/frontend/public/favicon.png`
- `apple-touch-icon.png`:180×180:`sips -z 180 180 apps/web/app/icon.png --out apps/studio/frontend/public/apple-touch-icon.png`
- `app-icon.png`(公开大图,manifest 用):512×512:`sips -z 512 512 apps/web/app/icon.png --out apps/studio/frontend/public/app-icon.png`
- `icons.svg`:若 grep 确认 studio 代码引用它做品牌 logo 则一并处理;若只是平台图标集(TikTok/IG 等)则不动。先 `grep -rn 'icons.svg' apps/studio/frontend/src` 判断。

- [ ] **Step 2: 更新图标断言测试**

`apps/studio/tests/test_frontend_brand_icon_assets.py`:该测试断言 `app-logo.png` 尺寸 `(128,128)` 和 `st_size < 80_000`。生成后 `ls -l apps/studio/frontend/src/assets/app-logo.png` 看实际字节;若新图 128×128 但字节超 80000,把阈值调到实际值上方(如 `< 120_000`);尺寸断言保持 128×128(生成时已固定)。Login/Shell 用 `appLogoUrl` 的断言不受影响(未改引用方式)。

- [ ] **Step 3: 验证**

```bash
cd apps/studio && uv run pytest tests/test_frontend_brand_icon_assets.py -v 2>&1 | tail -5; cd ../..
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
```
Expected: 测试 pass;build 成功。

- [ ] **Step 4: 目视确认(dev)**

`pnpm --filter @svm/studio-frontend dev`,浏览器开 `localhost:5173/studio/login`,截图确认 logo 是 radar 图标(不再是猫头鹰),tab favicon 也换了。停 dev。

- [ ] **Step 5: Commit**

```bash
git add apps/studio/frontend/public apps/studio/frontend/src/assets/app-logo.png apps/studio/tests/test_frontend_brand_icon_assets.py
git commit -m "feat: studio 图标全局替换为 Iris(radar)图标"
```

---

### Task 4: ui.tsx 重写(一)——卡片族 + 标题族

**Files:**
- Modify: `apps/studio/frontend/src/components/ui.tsx`(Card/Panel/MetricCard/StatCard/DataTableShell/PageTitle/PageHeader/SectionTitle)

目标 = spec §1 表 + radar `apps/web/components/ui.tsx`。

- [ ] **Step 1: 卡片族对齐 radar**

Card/Panel/MetricCard/StatCard/DataTableShell 统一:外框 `rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-[var(--shadow-sm)]`;内距从紧(现 p-3 类)抬到 `p-4`~`p-5`;内部堆叠 `space-y-3`。去掉 studio 旧的 `rounded-[8px]`/杂阴影。示例(Card):

```tsx
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn(
      'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-[var(--shadow-sm)]',
      className,
    )}>
      {children}
    </div>
  )
}
```
MetricCard/StatCard/DataTableShell/Panel 内部内距与圆角同步抬到 `rounded-xl` + `p-4`/`p-5`。

- [ ] **Step 2: 标题族对齐 radar**

- PageTitle → `text-2xl font-bold tracking-tight text-[var(--color-foreground)]`(radar 页面标题标准)。
- PageHeader → 容器 `mb-6 flex items-end justify-between`,主标题走 PageTitle 样式。
- SectionTitle → `text-sm font-semibold text-[var(--color-foreground)]`;其副文/描述 `text-xs text-[var(--color-muted-foreground)]`。

- [ ] **Step 3: build + 抽查**

```bash
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
```
不动 props/API/动效——diff 只在 className 字符串。

- [ ] **Step 4: Commit**

```bash
git add apps/studio/frontend/src/components/ui.tsx
git commit -m "restyle(studio): ui.tsx 卡片族+标题族对齐 radar 尺度"
```

---

### Task 5: ui.tsx 重写(二)——控件族

**Files:**
- Modify: `apps/studio/frontend/src/components/ui.tsx`(Button/TextField/Toggle/SegmentedControl/ProgressBar)

- [ ] **Step 1: 控件尺度对齐 radar**

- Button:高度 `h-8`(sm)/`h-9`(md),圆角 `rounded-md`,字号 `text-xs`(sm)/`text-sm`(md),`font-semibold`;variant 配色已在换肤定过(primary 近黑 hover:opacity-90、secondary 白底 border hover:bg-muted、danger 红字浅底),本任务只调尺度/圆角/字重,不改配色语义。
- TextField(input/select):高度 `h-9`,圆角 `rounded-md`,`text-sm`,`border-[var(--color-input)]`;focus `ring-2 ring-[var(--color-ring)]/20 border-[var(--color-ring)]`(换肤已定,复查)。label `text-xs`~`text-sm`。
- Toggle/SegmentedControl:触区抬松(padding/高度),圆角 `rounded-md`;选中态用 `--color-primary`(换肤已定)。
- ProgressBar:轨道/进度圆角与高度对齐 radar 观感(细而不促),主色 `--color-primary`。

- [ ] **Step 2: build**

```bash
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
```

- [ ] **Step 3: Commit**

```bash
git add apps/studio/frontend/src/components/ui.tsx
git commit -m "restyle(studio): ui.tsx 控件族抬松尺度对齐 radar"
```

---

### Task 6: ui.tsx 重写(三)——表格 + 徽章 + 弹层

**Files:**
- Modify: `apps/studio/frontend/src/components/ui.tsx`(Table/THead/Th/TBody/Tr/Td/Badge/Dot/Toast/Modal/Drawer/EmptyState/Toolbar/FilterBar)

- [ ] **Step 1: 表格对齐 radar**

- Th → `px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)] text-left`(radar Th 标准)。
- Td → `px-4 py-3 text-sm align-middle text-[var(--color-foreground)]`(抬松行高)。
- Tr hover 保持 `hover:bg-[var(--color-muted)]` 类的轻反馈。

- [ ] **Step 2: 徽章/状态**

Badge/Dot:字号 11.5px → 12px（text-xs);圆角/内距对齐;**信息色 tone 结构保留**(blue/green/orange/yellow/purple 信息色语义,换肤已定,不动 tone)。

- [ ] **Step 3: 弹层留白**

Modal/Drawer/Toast/EmptyState/Toolbar/FilterBar:内距留白加大、外框圆角 `rounded-xl`、标题字重 bold(对齐 radar 标题惯例);**@keyframes 动效 class 引用不动**(modal-scrim-in/drawer-panel-in 等)。

- [ ] **Step 4: build + grep 密排残留**

```bash
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
grep -n 'text-\[11\.5px\]\|text-\[11px\]\|rounded-\[8px\]\|rounded-\[6px\]\|rounded-\[7px\]' apps/studio/frontend/src/components/ui.tsx | head
```
第二个 grep 应大幅减少或归零(ui.tsx 内的密排残留已抬松);剩余若有,判断是否有意保留(如极小 chip)。

- [ ] **Step 5: Commit**

```bash
git add apps/studio/frontend/src/components/ui.tsx
git commit -m "restyle(studio): ui.tsx 表格/徽章/弹层对齐 radar 留白"
```

---

### Task 7: 页面级重写(一)——Shell + Login

**Files:**
- Modify: `apps/studio/frontend/src/components/Shell.tsx`、`apps/studio/frontend/src/Login.tsx`

- [ ] **Step 1: Shell 侧栏对齐 radar**

对照 radar `apps/web/app/[locale]/layout.tsx:44` 的 aside(`sticky top-0 h-screen w-48 flex-col border-r border-[var(--color-sidebar-border)] bg-[var(--color-sidebar)] px-3 py-6`)。Shell 的 `<aside>` 宽度/间距/背景对齐(bg-sidebar 换肤已定);顶部品牌区:radar 图标(`appLogoUrl`,Task 3 已换)`h-8 w-8 rounded-lg` + `Iris` 文字 `text-base font-bold`(Task 2 已改文字)+ `<ProductSwitcher current="studio" />`(已在);NavLink 项 `rounded-md px-3 py-2 text-sm font-semibold hover:bg-[var(--color-muted)]`,激活态 radar 式(bg-muted 或 primary 指示条,取现状最小改)。

- [ ] **Step 2: Login 对齐 radar 留白**

Login 页外层容器 `bg-[var(--color-background)]`(换肤已定);品牌区 logo + `Iris`(已改);tab/输入/按钮走 ui.tsx 新尺度(Task 4-6 已改);整体留白抬松到 radar 观感(卡片/间距)。

- [ ] **Step 3: build + dev 目检**

```bash
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
```
`pnpm --filter @svm/studio-frontend dev`,开 `localhost:5173/studio/login` 截图确认登录页 radar 化 + Iris + radar 图标;停 dev。

- [ ] **Step 4: Commit**

```bash
git add apps/studio/frontend/src/components/Shell.tsx apps/studio/frontend/src/Login.tsx
git commit -m "restyle(studio): Shell 侧栏 + Login 页对齐 radar 留白与品牌"
```

---

### Task 8: 页面级重写(二)——高频页 Dashboard/Accounts/Videos

**Files:**
- Modify: `apps/studio/frontend/src/pages/{Dashboard,Accounts,Videos}.tsx`

- [ ] **Step 1: 三页套 radar 页面骨架**

每页:外层容器套 radar 式 `mx-auto max-w-[1760px] px-6 py-10`(或该页合理等价大留白;若页面本就在 Shell 的 `max-w-[1440px] px-4..8 py-6..8` 容器内,则调该容器为更大留白,或页面内部块间距抬松);页面标题区 `mb-6` + `text-2xl font-bold tracking-tight`;块间 `space-y-6`、列间 `gap-4`;清掉密排残留(11.5px、紧 padding)。组件已在 Task 4-6 抬松,页面主要调**布局间距/标题/容器**。

- [ ] **Step 2: build + grep 密排残留(这三页)**

```bash
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
grep -n 'text-\[11\.5px\]\|text-\[11px\]' apps/studio/frontend/src/pages/{Dashboard,Accounts,Videos}.tsx | head
```
剩余密排逐处判断(数据密集处可保留合理紧凑,不教条——spec §风险表)。

- [ ] **Step 3: Commit**

```bash
git add apps/studio/frontend/src/pages/Dashboard.tsx apps/studio/frontend/src/pages/Accounts.tsx apps/studio/frontend/src/pages/Videos.tsx
git commit -m "restyle(studio): Dashboard/Accounts/Videos 页面级对齐 radar 留白"
```

---

### Task 9: 页面级重写(三)——长尾页 Comments/FbComments/Costs/Ops/Sagas/Settings

**Files:**
- Modify: `apps/studio/frontend/src/pages/{Comments,FbComments,Costs,Ops,Sagas,Settings}.tsx`

- [ ] **Step 1: 六页套 radar 页面骨架**

同 Task 8 Step 1 的做法逐页应用:容器大留白、标题 `text-2xl font-bold`、块 `space-y-6`。**Ops/Sagas 是极密的运维表页**——radar 风格优先,但表格信息密度可保留合理紧凑(不为留白牺牲一屏可读的行数);Costs/Settings/Comments 正常抬松。保留 `GeeLark task`/`GeeLark charges` 厂商词(Task 2.5 已确认)。

- [ ] **Step 2: build**

```bash
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
```

- [ ] **Step 3: Commit**

```bash
git add apps/studio/frontend/src/pages/Comments.tsx apps/studio/frontend/src/pages/FbComments.tsx apps/studio/frontend/src/pages/Costs.tsx apps/studio/frontend/src/pages/Ops.tsx apps/studio/frontend/src/pages/Sagas.tsx apps/studio/frontend/src/pages/Settings.tsx
git commit -m "restyle(studio): 长尾页对齐 radar 留白(Ops/Sagas 保留合理密度)"
```

---

### Task 10: 集成验收(全量 + 逐页目检)

- [ ] **Step 1: 全量回归**

```bash
cd apps/studio && uv run pytest -q 2>&1 | tail -3; cd ../..
pnpm --filter web build 2>&1 | tail -2 && pnpm --filter web typecheck 2>&1 | tail -2 && pnpm --filter web test 2>&1 | grep -iE 'pass|fail'
pnpm --filter @svm/studio-frontend build 2>&1 | tail -2
```
Expected: pytest 全绿(图标测试已在 Task 3 更新);radar build/typecheck/test 绿;studio build 绿。

- [ ] **Step 2: 品牌 + 密排残留扫描**

```bash
grep -rn 'GeeLark Studio\|Creator Radar' apps/web apps/studio/frontend/src apps/studio/frontend/index.html apps/studio/frontend/public --include='*.tsx' --include='*.ts' --include='*.html' --include='*.webmanifest' && echo 品牌残留 || echo 品牌OK
grep -rn 'GeeLark' apps/studio/frontend/src --include='*.tsx' | grep -v 'VideoDetailPanel\|Sagas\|Ops\|Costs' && echo 查厂商词误留 || echo 厂商词仅在预期文件
```
Expected: 品牌 OK;GeeLark 仅剩 VideoDetailPanel/Sagas/Ops/Costs 的厂商引用。

- [ ] **Step 3: 逐页视觉目检(本期核心)**

`pnpm --filter @svm/studio-frontend dev`。因数据页需 studio 后端,若本地无后端:至少目检 Login(截图)+ 用 dev 起前端后逐页看**布局骨架/密度/卡片形态/标题字重**(空数据态也能判断留白与结构)。逐项对照 spec §1 表 + radar 页面:
- Login / Shell 侧栏 / Dashboard / Accounts / Videos / Comments / FbComments / Costs / Ops / Sagas / Settings + Modal/Drawer/Toast/wizard
- 核对:rounded-xl 卡片、12–14px 字号(无 11.5px 密排残留)、大留白、bold 标题、切换器显示监控/运营、Iris 图标。与 radar 页面并排对比一次。
停 dev。

- [ ] **Step 4: 目检修补 Commit(若有)**

```bash
git add -A apps/studio/frontend
git commit -m "restyle(studio): 逐页目检修补"
```

---

### Task 11: 上线(需用户在场/授权,不自动执行)

> 纯前端品牌 + 视觉,无 schema、无鉴权、nginx 不动。按 `docs/DEPLOY.md` §E 同套流程,风险低于门户统一那次。本期无文件删除,不涉及"tar 不删文件"的坑,但仍建议部署后 grep 服务器确认无旧品牌残留。

- [ ] merge `iris-rebrand-restyle` 到 main(全绿后)
- [ ] 备份(nginx 无需动;studio 旧 tarball + radar 旧镜像各留一份)
- [ ] 部署 studio(tarball → /opt/mkt,重启 mkt+mkt-worker)
- [ ] 部署 radar(tar-over-ssh → /opt/svm,docker build + up -d web)
- [ ] 冒烟:登录页 Iris + radar 图标、切换器监控/运营、`:8080/healthz` 200(不受影响)、登录后逐页看新视觉
- [ ] 回滚:旧 tarball/旧镜像各自重启(独立)

---

## 任务顺序

```
前置 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11
```
1-3 品牌/图标(独立,可先做);4-6 组件(地基,必须先于页面);7-9 页面(依赖 4-6);10 验收;11 上线。

## Self-review 已做

- spec 逐节对照:§1 视觉语言→贯穿 T4-9 的目标;§2 UI 范围→T4-6(组件)+T7-9(页面);§3 品牌→T1(切换器)+T2(文案);§4 图标→T3;§5 测试验收→T10;§6 上线→T11。无缺口。
- 品牌 vs 厂商区分在"关键事实" + T2.5 + T10.2 三处钉死,避免误改 GeeLark 厂商引用。
- 无 TBD;组件/页面重写以 spec §1 表为具体标尺(表内是精确 class);枚举型(品牌串/图标文件/切换器 key)给了精确路径与命令。
- 命名一致:ProductSwitcher(两侧)、products.ts `{id,path}`、i18n key `monitor`/`ops`(radar layout 命名空间)与 `switcher.monitor`/`switcher.ops`(studio)全文一致。
