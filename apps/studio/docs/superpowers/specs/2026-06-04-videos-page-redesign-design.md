# Videos 页表格重做 — 设计稿

日期：2026-06-04

## 背景

当前 `/videos` 页存在三个明确缺陷，用户在使用中反馈：

1. **空白尾部**：[VideoList.tsx:89](../../../frontend/src/components/videos/VideoList.tsx) 把容器写死 `height: '70vh'`，少视频时下面留大段空白。
2. **无表头**：虚拟滚动只渲染了 9 列 grid 的行体，没有列名表头。
3. **状态丢失**：分组筛选、状态筛选、展开列表、滚动位置全部存于 React `useState`，刷新即清空。

且整张表的视觉密度、状态可读性、操作可发现性都有提升空间。本次决定按"精致密集表"方向（mockup 已对齐）整体重做。

## 目标

- 解决上述三个缺陷
- 把视觉密度往 Apple 安静风格靠拢，提升状态可读性
- 不引入新依赖（继续用 `@tanstack/react-virtual`、tailwind、现有 ui 原语）
- 不破坏现有 hooks 数据流（`useVideoPages` / `useVideoDetail` 不动）

## 非目标

- 不做键盘导航（上下键选行）— 下个迭代
- 不改 `useVideoPages` cursor 分页逻辑
- 不引入 React Router 之外的路由抽象
- 不做删除二次确认（保持现有行为）
- 不做实时 SSE 刷新（保留现有 visibilitychange 30s 检查）

## 方案概览

整体布局保持"PageTitle → 筛选 chips → 批量栏 → 表格"。表格本身做以下结构变化：

```
┌────────────────────────────────────────── (sticky thead) ──┐
│  日期   选择   文件名      分组    平台   状态   手机   标题   │
├──────────────────────────────────────────────────────────────┤
│ ┃ 今天 14:32  ☑  london_dorm... 英国留学  T·Y·I·F  ● 成功 ... │ ← 状态色条
│ ┃    12:08   ☑  ielts_speak.. 雅思口语   T·y·i·f  ● 进行中.. │ ← 同日 continuation
│ ┃    10:45   ☑  campus_walk.. 英国留学   T·Y!·I·f ● 部分失败│ ← 失败叠红点
│ ┌─ [详情面板] ────────────────────────────────────────────┐ │
│ │  TikTok    [成功]   2 小时前   UK-07                    │ │
│ │  YouTube   [失败]   2 小时前   UK-07   登录态失效 [截图]│ │
│ │  ...                                                    │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┃ 昨天 21:14  —  study_tips... 备考干货   T·Y·I·F  ● 已完成 │ ← 跨日 hairline
└──────────────────────────────────────────────────────────────┘
```

## 详细设计

### 1. 表格结构

- 容器去掉 `height: '70vh'`，改为 `min-h-[320px]`，自然撑开
- 整页一个滚动条（移除表内独立滚动）；**表头 sticky 在视口顶**
- 行高 36 → 44px（增加呼吸感）
- 9 列改 10 列 grid：

  ```
  grid-template-columns:
    3px            (1) 状态色条
    88px           (2) 日期
    28px           (3) 选择
    minmax(0,1fr)  (4) 文件名
    120px          (5) 分组
    108px          (6) 平台图标
    88px           (7) 状态
    140px          (8) 手机
    minmax(0,1.1fr)(9) 标题预览
    56px           (10) 操作
  ```

### 2. 状态色条（左侧 3px）

- `SUCCESS` → `var(--color-green)`
- `FAILURE` → `var(--color-red)`
- `INITIATED` → `var(--color-blue)`
- `QUEUED` → `var(--color-orange)`
- `PENDING+has_phone` → `var(--color-line-strong)`
- `PENDING+no_phone` → transparent（最弱）

色条贴行高、零间距。

### 3. 状态列（取代 Badge）

用 7px `Dot` + `font-medium text-ink-2` 文字标签。颜色映射同上。Badge 仍用于详情面板的"每平台单独状态"——保留 ui.tsx 现有原语，不删。

### 4. 平台图标

- [PlatformIcon.tsx](../../../frontend/src/components/PlatformIcon.tsx) 已支持彩色品牌色（默认 `mono={false}`，含 Instagram 渐变）— 沿用即可，无需改 PlatformIcon 组件
- 未发布 / 该平台无 publication：从现有 `mono={true}` 改为 wrapper 上加 `opacity: 0.42 + filter: grayscale(1)`，保留彩色基色但整体褪色，比 mono 更易看出"该平台未参与"
- 该平台 FAILURE：图标外层 wrapper `position: relative`，右下角绝对定位叠 7px 红 Dot，1.5px 白色描边
- title 工具提示："平台名 · 状态文本"

### 5. 同日 continuation 视觉

- 新日期首行：日期列显示 `日期文本 + HH:MM`，row 加 `border-top: 1px solid var(--color-line)`
- 同日后续行：日期列只显示 `HH:MM`，无上边框
- 行间统一 1px hairline 分隔

[VideoDateCell.tsx](../../../frontend/src/components/videos/VideoDateCell.tsx) 可以删除或简化 — 逻辑挪到 VideoRow 内联，避免额外组件。

### 6. 文件名列

- 展开/收起按钮 20×20 圆角小方块（边框 + 灰色字），点击切换详情面板
- 文件名 13.5px font-medium，单行截断

### 7. 操作列

- 默认 `opacity: 0`
- `:hover` 行时 fade in
- 仅 PENDING 行显示（保持现有逻辑）

### 8. 筛选 chips

两行，分别 `group` 和 `status`：

- `group`：全部 / 未分组 / 各群组名
- `status`：全部 / 待发布 / 已结束

样式：`rounded-full px-3 h-7 text-[12.5px]`，选中态 `bg-blue-bg + text-blue`（非实蓝）。带数量徽章的 chip 用嵌套圆角 pill 显示数量。

### 9. 批量工具栏

选中后顶部出现 sticky 一栏（不是 mb-3 卡片）：

```
[N 已选] [分组到 ▾] [分配到分组] [取消选择]    未分配手机的视频会自动按平台分配 →
```

`position: sticky; top: 0; z-index: 20`，在 thead 之上。

### 10. 持久化

| 状态 | 存储 | 实现 |
|------|------|------|
| `group`, `status` filter | URL searchParams | 新 hook `useVideoFilter` 包 `useSearchParams` |
| 展开的 video id 集合 | sessionStorage 按 filterKey 分桶 | 新 hook `useExpandedVideos(filterKey)` |
| 滚动位置 | sessionStorage 按 filterKey 分桶 | 新 hook `useScrollRestore(filterKey)` |

URL schema：

```
/videos                            (默认 group=all, status=all)
/videos?group=2                    (具体群组 id)
/videos?group=ungrouped            (未分组)
/videos?status=pending             (只看待发)
/videos?group=2&status=pending     (组合)
```

sessionStorage key：

```
videos:expanded:<filterKey>    → JSON [id, id, ...]
videos:scrollY:<filterKey>     → number
```

`filterKey = "${group}|${status}"`。

切 filter 时：保留另一 filter 的 sessionStorage（按 key 自然隔离）。

挂载时：从 URL 读 filter；从 sessionStorage 按当时 filterKey 读 expanded + scrollY；滚动到 scrollY 在 useVideoPages 第一页加载完成后。

卸载时（含路由切走 + 刷新前的 `beforeunload`）：把当前 expanded set + window.scrollY 写入。

### 11. 错误 / 空状态

- 加载失败：保留现有错误卡片，改用 `border-dashed`
- 空状态：保留 EmptyState 卡

## 影响的文件

**新增**

- `frontend/src/lib/useVideoFilter.ts`
- `frontend/src/lib/useExpandedVideos.ts`
- `frontend/src/lib/useScrollRestore.ts`

**修改**

- `frontend/src/pages/Videos.tsx` — 接入新 hooks，加 status chips，批量栏改 sticky
- `frontend/src/components/videos/VideoList.tsx` — sticky thead、去 70vh、行高调 44、grid 改 10 列
- `frontend/src/components/videos/VideoRow.tsx` — 状态色条、Dot 状态、hover 操作、内联同日 continuation、平台图标 wrapper（褪色 + 红点）
- `frontend/src/components/videos/VideoDateCell.tsx` — 简化或删除（逻辑挪 VideoRow）

**不动**

- `frontend/src/components/PlatformIcon.tsx`（已支持彩色 + 渐变，直接复用）

- `useVideoPages`、`useVideoDetail`、`VideoDetailPanel`
- 后端 API、Alembic
- 其他页（Accounts / Dashboard / Settings）

## 测试策略

- 现有 frontend 没有视觉回归测试。手动验证清单：
  - [ ] 刷新页面 filter 状态保留
  - [ ] 切换 filter，sessionStorage 按 key 分桶（互不污染）
  - [ ] 展开多行后刷新，原展开行仍展开
  - [ ] 刷新后滚动位置恢复
  - [ ] hover 行时操作按钮淡入
  - [ ] 同日多行不重复日期，跨日有 hairline
  - [ ] FAILURE 行的对应平台图标有红点
  - [ ] 表头 sticky 跟随视口
  - [ ] 选中时批量栏 sticky 在表头之上
  - [ ] 长文件名 / 长 caption 截断不溢出
  - [ ] 移动端 / 窄屏下 grid 不破坏（预期 ≥1024px 桌面，<1024px 不保证）
- 后端测试套件 `uv run pytest -q` ≥385 不变（本次无后端改动）

## 部署

按 `docs/production-deploy.md` 流程：本地 `cd frontend && npm run build` → 打包 → 部署。无 schema 改动，无需 alembic / 备份。

## 风险

- **sessionStorage 持久化**：用户清 cookie / 隐私模式失败时降级到内存 `useState`（`try/catch` 包 storage 访问）。
- **滚动恢复时机**：第一页数据未到时 `setScroll` 无效。在 `useVideoPages` 第一页 ready 的 effect 里执行恢复，只执行一次（用 `useRef` 保 `restored = true`）。
- **彩色平台图标的视觉密度**：当前页面 mono 图标在 muted 时也只是改颜色，新版改成"褪色彩色"后整行视觉色块多了一些。如果实际感觉太花，回退方案是把 muted 改回 `mono={true}`。
