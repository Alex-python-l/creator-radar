# Iris 品牌统一 + studio 全面 radar 风格重写(设计)

- 日期:2026-07-17
- 状态:已与用户对齐,待实施
- 前置:门户统一(2026-07-16)已上线;本期是其上的品牌 + 视觉深化

## 背景与动机

门户统一已把 geelark-studio 并入 creator-radar(一域名 `/studio`、统一登录、切换器),并做过一次换肤(仅迁移配色 token 到 radar 体系)。但 studio 的**结构与密度**仍是"dense ops console"那套(11.5px 紧排、组件重、信息密),与 radar 的"清爽留白"体感不一致——用户明确不满意,要求用 radar 风格**深度重写**,并借此把整个项目**重新品牌化为 Iris**、图标全局统一。

## 已确认决策

| 议题 | 决定 |
|---|---|
| 项目新名 | **Iris**(虹膜=观察/监控;Iris 希腊传信女神=运营/触达,一词双关双半) |
| 两半命名 | 一个品牌 Iris;左上角切换器从 Radar/Studio 改为 **监控 / 运营**(radar 侧双语 zh 监控/运营、en Monitor/Ops;studio 侧英文 Monitor/Ops) |
| UI 重写深度 | **全面视觉改造(保留功能结构)**——所有 studio 页面 + 27 组件系统性调成 radar 视觉语言;功能不变,只换"形+密度+字号+留白" |
| 重写方法 | 方案 A 设计体系先行:先固化 radar 视觉语言 → 改 ui.tsx 27 组件(杠杆) → 扫页面级密度 |
| 图标 | radar 的 `apps/web/app/icon.png` 做全局唯一品牌图标,替换 studio 的 GeeLark 猫头鹰 |
| 边界 | 品牌/用户可见层全改 Iris;**内部代码标识不动**(见非目标) |

## 非目标(明确不做)

- **不改内部标识**:包名 `@svm/shared` `web` `@svm/studio-frontend`、部署路径 `/opt/svm` `/opt/mkt`、docker 容器 `svm-web` `svm-postgres`、systemd `mkt` `mkt-worker`、数据库名 `short_video_monitor` `mkt`、GitHub 仓库名 `creator-radar` —— 全部保持,避免纯风险无收益的大churn(刚上完线)。
- **不改功能/交互/路由/后端**:纯品牌 + 视觉。组件 props/API 不变。
- **不动 radar 侧现有视觉**:radar 已是目标风格,只加品牌名/切换器文案改动,不重写 radar 组件。
- **不碰 GeeLark 回调 :8080 通路、不动鉴权链路**。

## 1. Radar 视觉语言(重写目标,取自 radar 现网代码)

以 `apps/web/components/ui.tsx` + `apps/web/app/[locale]/layout.tsx` + `page.tsx` 为准,固化为下表。studio 重写以此为唯一目标:

| 维度 | radar 规格 | studio 现状(要改掉) |
|---|---|---|
| 页面容器 | `mx-auto max-w-[1760px] px-6 py-10`(宽居中、大留白) | 各页自定、偏窄偏紧 |
| 页面标题 | `text-2xl font-bold tracking-tight` + `mb-6 flex items-end justify-between` | PageTitle 偏小 |
| 卡片 | `rounded-xl border border-border shadow-sm` + 内 `p-4 space-y-3` | rounded-[8/12px]、阴影杂、内距紧 |
| 卡内小标题 | `text-sm font-semibold`;副文 `text-xs text-muted-foreground` | 11.5px 系 |
| 段落间距 | `space-y-6`(块间)、`gap-4`(列间) | 偏密 |
| 按钮/控件 | `h-8`~`h-9` `rounded-md` `text-xs`~`text-sm` `font-semibold`;次要 `border-border bg-card hover:bg-muted` | 高度/圆角/字号偏小紧 |
| 表头 Th | `text-xs font-semibold uppercase tracking-wide text-muted-foreground`,`px-4 py-2.5` | (T13 已部分对齐,复查) |
| 表格 Td | `px-4 py-3 text-sm align-middle` | 密 |
| 侧栏 | `w-48 sticky bg-sidebar border-r px-3 py-6`;NavLink `rounded-md px-3 py-2 text-sm font-semibold hover:bg-muted` | Shell 自成一套 |
| 基础字号 | 12px(text-xs)/14px(text-sm)为主 | 大量 11.5px |
| 密度总纲 | **airy**:宁可少一行密度、多一分留白 | **dense**:信息密、行紧 |

**核心差异一句话**:把 studio 从"11.5px 紧排的运营控制台"抬到"12–14px、大留白、rounded-xl 卡片、bold 标题"的 radar 清爽体感。颜色 token 已在上次换肤对齐,本期改的是**尺度(spacing/size)、圆角、字重、密度**。

## 2. UI 重写范围(方案 A:设计体系先行)

**阶段 1 — ui.tsx 27 组件重写(杠杆,大部分视觉变化来自这里)**
`apps/studio/frontend/src/components/ui.tsx`。逐组件把尺度对齐 §1:
- 卡片族(Card/Panel/MetricCard/StatCard/DataTableShell):`rounded-xl` + `p-4`~`p-5` + `space-y-3` + `shadow-sm`,去掉紧内距。
- 标题族(PageTitle/PageHeader/SectionTitle):PageTitle→`text-2xl font-bold tracking-tight`;SectionTitle→`text-sm font-semibold`;副文 `text-xs muted`。
- 按钮/输入(Button/TextField/Toggle/SegmentedControl):高度 `h-8/h-9`,圆角 `rounded-md`,字号 `text-xs/sm`,`font-semibold`;放大触区。
- 表格(Table/THead/Th/TBody/Tr/Td):Th `text-xs uppercase tracking-wide muted`,Td `px-4 py-3 text-sm`,行高抬松。
- 徽章/状态(Badge/Dot/ProgressBar):字号 11.5→12px、圆角/内距对齐;信息色语义保留(上次已定)。
- 弹层(Modal/Drawer/Toast/EmptyState/Toolbar/FilterBar):内距留白加大、圆角 `rounded-xl`、标题字重 bold;动效 class 不动。

**阶段 2 — 页面级密度扫(9 页 + Shell + Login)**
`pages/{Dashboard,Accounts,Videos,Comments,FbComments,Costs,Ops,Sagas,Settings}.tsx` + `components/Shell.tsx` + `Login.tsx`:
- 页面容器套 radar 的 `mx-auto max-w-[1760px] px-6 py-10`(或各页合理的等价大留白)。
- 页面标题区对齐 `text-2xl font-bold` + `mb-6`。
- 块间距 `space-y-6`、列间 `gap-4`;清掉密排残留(11.5px、紧 padding)。
- Shell 侧栏对齐 radar `w-48 sticky bg-sidebar border-r`,NavLink `hover:bg-muted`;顶部品牌区换 Iris 名 + radar 图标 + 切换器。
- Login 页整体对齐 radar 登录观感(留白、卡片、字号),品牌换 Iris + radar 图标。

**顺序**:阶段 1 全部改完 build 通过后再进阶段 2(组件是页面的地基)。

## 3. Iris 品牌落地(用户可见层)

逐处替换文案 + 元数据(grep 驱动,内部标识不碰):

| 位置 | 现状 | 改为 |
|---|---|---|
| radar 侧栏标题 | "Creator Radar" | "Iris"(可加副签 · 监控) |
| radar 切换器 | Radar / Studio | 监控 / 运营(zh);Monitor / Ops(en) |
| radar metadata/manifest | Creator Radar 相关 | Iris |
| studio 登录页品牌 | "GeeLark Studio" | "Iris"(副签 · 运营 / internal ops console → Iris ops console) |
| studio Shell 顶部 | GeeLark Studio + 猫头鹰 | Iris + radar 图标 + 切换器 |
| studio 切换器 | Radar / Studio | 同上 监控/运营 |
| studio index.html title / manifest name | GeeLark Studio | Iris |
| `packages/shared/products.ts` | `{ id, name, path }` | **移除 name**,只留 `{ id, path }`;id 保留 radar/studio、path 保留 `/`·`/studio/`(内部标识不动) |
| 页脚等散落文案 | GeeLark Studio · internal ops console | Iris · ops console |

**切换器标签本地化**:`products.ts` 只出 `id`/`path`,不出 name(避免无 i18n 的中文常量在双语 radar 上写死)。标签由各侧自己的 i18n 提供 key `monitor`/`ops` → radar 用 next-intl(zh 监控/运营、en Monitor/Ops),studio 用 react-i18next(同样 zh/en)。两侧 ProductSwitcher 组件按 `id` 取本地化文案渲染,视觉仍一致。

## 4. 图标全局统一

源:`apps/web/app/icon.png`(radar 现有,1.4MB)+ `apps/web/app/apple-icon.png`。
- studio `frontend/public/`:`app-icon.png`、`favicon.png`、`apple-touch-icon.png`、`icons.svg`(若引用)全部替换为 radar 图标(按各自尺寸从 icon.png 生成或直接替换)。
- studio `src/assets/app-logo.png`(Shell/Login 的 bundled logo)替换为 radar 图标。
- `frontend/public/manifest.webmanifest`:name/icons 指向新图标 + Iris 名(路径 `/studio/` 前缀保持,上次已改)。
- `tests/test_frontend_brand_icon_assets.py`:该测试断言 app-logo.png 尺寸/来源,替换图标后同步更新断言(尺寸/字节阈值以新图标为准)。
- radar 侧图标本就是目标,不动。

## 5. 测试与验收

- studio `pnpm --filter @svm/studio-frontend build` + `lint` 绿;radar `pnpm --filter web build`/`typecheck`/`test` 绿(radar 只改品牌文案,风险低)。
- studio `uv run pytest -q` 绿(改图标要同步 `test_frontend_brand_icon_assets.py`)。
- 无功能回归:组件 props/API、路由、后端、动效未变(diff 只落在 className/文案/图标资产)。
- **逐页视觉目检**(关键,本期核心):9 页 + Shell + Login + Modal/Drawer/Toast/wizard 逐一过目,与 radar 页面并排比,确认"清爽留白、rounded-xl 卡片、bold 标题、12–14px、无密排残留";切换器显示 监控/运营 + Iris 图标。
- 品牌残留扫:`grep -rn 'GeeLark\|Creator Radar\|geelark' apps/studio/frontend/src apps/web/app apps/web/components` 归零(内部标识如包名除外,单列确认)。

## 6. 上线

品牌 + 视觉纯前端改动,无 schema、无鉴权变化。按 `docs/DEPLOY.md` §E 同套流程(studio tarball + radar tar-over-ssh + docker build;**注意 tar 不删文件的坑**已知);nginx 不动。可作为一次常规部署,风险低于门户统一那次。

## 风险与对策

| 风险 | 对策 |
|---|---|
| "全面改造"体量大、易改出视觉回归 | 方案 A 先改组件(DRY),§5 逐页目检兜底;分阶段 commit |
| 组件尺度改动误伤布局(卡片变大撑破栅格) | 阶段 1 改完先 build + 抽查几页;阶段 2 页面级修正 |
| 图标替换漏改某处仍露猫头鹰 | §4 清单 + §5 品牌残留 grep |
| 密度抬松导致长表格/密集页信息一屏放不下 | 目检时权衡:radar 风格优先,但极密页(Ops/Sagas)可保留合理紧凑,不教条 |

## 后续(不在本期)

内部标识彻底 Iris 化(仓库/包/容器/库名)、radar 与 studio 组件库真正合一——若将来要做,另立 spec。
