# 视频列表批量选择 Shift/Cmd 多选 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `/videos` 列表支持 Shift+click 范围选、Cmd/Ctrl+click 单选，并支持整行任意空白处点击触发，替代逐个点 checkbox 的低效操作。

**Architecture:** 选择逻辑被抽到纯函数 `frontend/src/lib/videoSelection.ts`，由 `Videos.tsx` 顶层持有 selected/anchor 状态，VideoRow 仅做事件转发。anchor 用 `useRef` 持有，避免不必要重渲染。Shift 范围在 `items[]` 索引层计算，已发布行无声跳过，不依赖 DOM。

**Tech Stack:** React 19 + TypeScript + Vite + `@tanstack/react-virtual`。前端测试用 `node --test --experimental-strip-types`（仓库现有约定）。

**Spec:** [`docs/superpowers/specs/2026-06-04-video-batch-select-design.md`](../specs/2026-06-04-video-batch-select-design.md)

---

## File Structure

**Create:**
- `frontend/src/lib/videoSelection.ts` — 纯函数 `computeNextSelection(items, clickedId, anchorId, modifiers, prevSelected, isSelectable)`，返回 `{ selected, nextAnchorId }`。
- `frontend/tests/videoSelection.test.ts` — 8 个用例覆盖普通 toggle / shift 范围 / 已发布跳过 / 反向 shift / anchor 不变 / clicked 不可选时 anchor 仍更新等。

**Modify:**
- `frontend/src/pages/Videos.tsx` — 新增 `anchorIdRef`，把 `toggleSelected` 替换成 `handleRowClick(id, mods)`，filter useEffect 同时清 anchor。
- `frontend/src/components/videos/VideoList.tsx` — prop `onToggleSelect` 改名为 `onRowClick`，签名变 `(id, mods) => void`。
- `frontend/src/components/videos/VideoRow.tsx` — 外层 div 接入 `onClick` / `onMouseDown`，+ 按钮和删除按钮 `e.stopPropagation()`，checkbox 改 `onChange={() => {}}` 占位，避免重复触发。
- prop 类型 `Props` 同步。

**No backend changes.** Spec 已明确。

---

## Task 1: 抽出选择计算纯函数 + 单元测试

**Files:**
- Create: `frontend/src/lib/videoSelection.ts`
- Test: `frontend/tests/videoSelection.test.ts`

- [ ] **Step 1: 写失败的测试**

`frontend/tests/videoSelection.test.ts`:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'

import { computeNextSelection } from '../src/lib/videoSelection.ts'

type Item = { id: number; selectable: boolean }
const sel = (item: Item) => item.selectable

const ITEMS: Item[] = [
  { id: 1, selectable: true },
  { id: 2, selectable: true },
  { id: 3, selectable: false }, // 已发布
  { id: 4, selectable: true },
  { id: 5, selectable: true },
  { id: 6, selectable: false },
  { id: 7, selectable: true },
]

const M = { shift: false, meta: false }
const SHIFT = { shift: true, meta: false }

test('toggle selects an unselected selectable row and sets anchor', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 2, null, M, new Set(), sel,
  )
  assert.deepEqual([...selected], [2])
  assert.equal(nextAnchorId, 2)
})

test('toggle removes an already-selected row', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 2, null, M, new Set([2, 4]), sel,
  )
  assert.deepEqual([...selected].sort(), [4])
  assert.equal(nextAnchorId, 2)
})

test('shift+click with no anchor degrades to toggle', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 5, null, SHIFT, new Set(), sel,
  )
  assert.deepEqual([...selected], [5])
  assert.equal(nextAnchorId, 5)
})

test('shift+click adds inclusive range of selectable rows', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 5, 1, SHIFT, new Set(), sel,
  )
  // 1..5: ids 1,2,4,5 selectable; id 3 skipped
  assert.deepEqual([...selected].sort((a, b) => a - b), [1, 2, 4, 5])
  // anchor unchanged
  assert.equal(nextAnchorId, 1)
})

test('shift+click skips non-selectable rows in range', () => {
  const { selected } = computeNextSelection(
    ITEMS, 7, 3, SHIFT, new Set(), sel,
  )
  // 3..7: ids 3(skip),4,5,6(skip),7
  assert.deepEqual([...selected].sort((a, b) => a - b), [4, 5, 7])
})

test('shift+click works when clicked is above anchor', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 1, 5, SHIFT, new Set(), sel,
  )
  assert.deepEqual([...selected].sort((a, b) => a - b), [1, 2, 4, 5])
  assert.equal(nextAnchorId, 5)
})

test('shift+click is additive: keeps prior selection outside range', () => {
  const { selected } = computeNextSelection(
    ITEMS, 5, 4, SHIFT, new Set([1, 7]), sel,
  )
  // prior 1,7 kept; range 4..5 adds 4,5
  assert.deepEqual([...selected].sort((a, b) => a - b), [1, 4, 5, 7])
})

test('toggle on non-selectable row leaves selection but updates anchor', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 3, null, M, new Set([2]), sel,
  )
  assert.deepEqual([...selected].sort((a, b) => a - b), [2])
  assert.equal(nextAnchorId, 3)
})

test('shift degrades to toggle when anchor not in items', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 4, 999, SHIFT, new Set(), sel,
  )
  assert.deepEqual([...selected], [4])
  assert.equal(nextAnchorId, 4)
})
```

- [ ] **Step 2: 跑测试验证全部 FAIL**

Run: `cd frontend && node --test --experimental-strip-types tests/videoSelection.test.ts`

Expected: 全部 FAIL，错误提示 `Cannot find module '.../videoSelection.ts'` 或 import error。

- [ ] **Step 3: 写最小实现**

`frontend/src/lib/videoSelection.ts`:

```ts
export type SelectionModifiers = {
  shift: boolean
  meta: boolean
}

export type SelectionResult = {
  selected: Set<number>
  nextAnchorId: number | null
}

export function computeNextSelection<T extends { id: number }>(
  items: readonly T[],
  clickedId: number,
  anchorId: number | null,
  modifiers: SelectionModifiers,
  prevSelected: ReadonlySet<number>,
  isSelectable: (item: T) => boolean,
): SelectionResult {
  if (modifiers.shift && anchorId !== null) {
    const a = items.findIndex((v) => v.id === anchorId)
    const b = items.findIndex((v) => v.id === clickedId)
    if (a >= 0 && b >= 0) {
      const lo = Math.min(a, b)
      const hi = Math.max(a, b)
      const next = new Set(prevSelected)
      for (let i = lo; i <= hi; i++) {
        const v = items[i]
        if (isSelectable(v)) next.add(v.id)
      }
      return { selected: next, nextAnchorId: anchorId }
    }
    // anchor or clicked missing — fall through to toggle
  }
  const next = new Set(prevSelected)
  const clicked = items.find((v) => v.id === clickedId)
  if (clicked && isSelectable(clicked)) {
    if (next.has(clickedId)) next.delete(clickedId)
    else next.add(clickedId)
  }
  return { selected: next, nextAnchorId: clickedId }
}
```

- [ ] **Step 4: 跑测试验证 PASS**

Run: `cd frontend && node --test --experimental-strip-types tests/videoSelection.test.ts`

Expected: `pass 9, fail 0`.

- [ ] **Step 5: 提交**

```bash
git add frontend/src/lib/videoSelection.ts frontend/tests/videoSelection.test.ts
git commit -m "feat(videos): add computeNextSelection helper for shift/cmd multi-select"
```

---

## Task 2: 在 Videos.tsx 接线 anchor 与 handleRowClick

**Files:**
- Modify: `frontend/src/pages/Videos.tsx`

- [ ] **Step 1: 在文件顶部加入 import**

打开 `frontend/src/pages/Videos.tsx`，在已有的 `import { useExpandedVideos } ...` 附近加入：

```ts
import { computeNextSelection, type SelectionModifiers } from '../lib/videoSelection'
```

- [ ] **Step 2: 在状态声明处加 anchorIdRef**

定位到 `const [selected, setSelected] = useState<Set<number>>(new Set())`（约 110 行），在其后加入：

```ts
const anchorIdRef = useRef<number | null>(null)
```

`useRef` 已在文件顶部 import（已存在），无需新增。

- [ ] **Step 3: 修改清空 selection 的 useEffect 同时清 anchor**

定位到：

```ts
useEffect(() => {
  setSelected(new Set())
}, [filter.group_id, filter.status])
```

改为：

```ts
useEffect(() => {
  setSelected(new Set())
  anchorIdRef.current = null
}, [filter.group_id, filter.status])
```

- [ ] **Step 4: 替换 toggleSelected 为 handleRowClick**

定位到：

```ts
const toggleSelected = (id: number) =>
  setSelected((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })
```

替换为：

```ts
const handleRowClick = (id: number, mods: SelectionModifiers) => {
  setSelected((prev) => {
    const { selected: next, nextAnchorId } = computeNextSelection(
      items,
      id,
      anchorIdRef.current,
      mods,
      prev,
      (v) => v.summary === 'PENDING',
    )
    anchorIdRef.current = nextAnchorId
    return next
  })
}
```

- [ ] **Step 5: 把"取消选择"按钮顺便清 anchor**

定位 sticky 工具栏里的 "清除选择" 按钮，附近的代码：

```ts
<Button
  variant="ghost"
  size="sm"
  onClick={() => setSelected(new Set())}
>
```

改为：

```ts
<Button
  variant="ghost"
  size="sm"
  onClick={() => {
    setSelected(new Set())
    anchorIdRef.current = null
  }}
>
```

同时找到 `confirmAssign` 函数里的 `setSelected(new Set())`，下面一行加 `anchorIdRef.current = null`：

```ts
setSelected(new Set())
anchorIdRef.current = null
setAssignTarget('')
```

- [ ] **Step 6: 把 VideoList 的 prop 从 onToggleSelect 改成 onRowClick**

定位到 `<VideoList ... onToggleSelect={toggleSelected} ...>`（约 541 行），改为：

```ts
<VideoList
  items={items}
  hasMore={hasMore}
  loading={loading}
  expandedIds={expandedVideos}
  selectedIds={selected}
  groupsById={groupsById}
  onRowClick={handleRowClick}
  onToggleExpanded={toggleExpanded}
  onDelete={removeVideo}
  onLoadMore={loadMore}
  onOpenFailureShot={openFailureShot}
/>
```

- [ ] **Step 7: 跑 build 验证 TS 类型通过**

Run: `cd frontend && npm run build`

Expected: build 成功；若 VideoList 的 prop 还没改，会报类型错（这是预期，下一个 Task 修）。

如果只想避免本步骤红字，可以暂时把 `onRowClick={handleRowClick as never}` 让步，但推荐直接进 Task 3 修。

- [ ] **Step 8: 不提交（与 Task 3、4 一起提交以保持每次 commit 可构建）**

skip

---

## Task 3: VideoList 透传 onRowClick

**Files:**
- Modify: `frontend/src/components/videos/VideoList.tsx`

- [ ] **Step 1: 修改 Props 类型**

定位到 `type Props = { ... onToggleSelect: (id: number) => void ... }`，把这一行改为：

```ts
onRowClick: (
  id: number,
  modifiers: { shift: boolean; meta: boolean },
) => void
```

- [ ] **Step 2: 修改解构与透传**

定位到：

```ts
export default function VideoList({
  items, hasMore, loading, expandedIds, selectedIds, groupsById,
  onToggleSelect, onToggleExpanded, onDelete, onLoadMore,
  onOpenFailureShot,
}: Props) {
```

把 `onToggleSelect` 改为 `onRowClick`。

定位到 `<VideoRow ... onToggleSelect={onToggleSelect} ...>`（约 145 行），改为：

```ts
<VideoRow
  row={row.video}
  groupsById={groupsById}
  isContinuation={row.isContinuation}
  isExpanded={expandedIds.has(row.video.id)}
  isSelected={selectedIds.has(row.video.id)}
  onRowClick={onRowClick}
  onToggleExpanded={onToggleExpanded}
  onDelete={onDelete}
/>
```

- [ ] **Step 3: 不跑 build（等 Task 4 完成再跑，VideoRow 还没改）**

skip

- [ ] **Step 4: 不提交**

skip

---

## Task 4: VideoRow 接入整行点击、按钮 stopPropagation、checkbox 占位

**Files:**
- Modify: `frontend/src/components/videos/VideoRow.tsx`

- [ ] **Step 1: 修改 Props 类型**

定位到 `type Props = { ... onToggleSelect: (id: number) => void ... }`，把这一行改为：

```ts
onRowClick: (
  id: number,
  modifiers: { shift: boolean; meta: boolean },
) => void
```

- [ ] **Step 2: 修改解构**

定位到：

```ts
export default function VideoRow({
  row, groupsById, isContinuation, isExpanded, isSelected,
  onToggleSelect, onToggleExpanded, onDelete,
}: Props) {
```

把 `onToggleSelect` 改成 `onRowClick`。

- [ ] **Step 3: 给外层 div 加 onClick / onMouseDown**

定位到 `<div role="row" className={cn(...)}>`（约 132 行），改为：

```tsx
<div
  role="row"
  onMouseDown={(e) => {
    if (e.shiftKey) e.preventDefault()
  }}
  onClick={(e) => {
    onRowClick(row.id, {
      shift: e.shiftKey,
      meta: e.metaKey || e.ctrlKey,
    })
  }}
  className={cn(
    'group grid items-center gap-x-3 pr-3',
    'grid-cols-[3px_76px_24px_minmax(0,1fr)_100px_96px_80px_120px_44px]',
    'xl:grid-cols-[3px_88px_28px_minmax(0,1fr)_120px_108px_88px_140px_minmax(0,1.1fr)_56px]',
    'border-b border-[var(--color-line)]',
    !isContinuation && 'border-t border-[var(--color-line)]',
    'h-11',
    isSelected
      ? 'bg-[var(--color-blue-bg)] hover:bg-[#e0ecfa]'
      : 'hover:bg-[var(--color-surface-hover)]',
    'cursor-pointer transition-colors',
  )}
>
```

新加：`onMouseDown`、`onClick`、`cursor-pointer`。

- [ ] **Step 4: + 展开按钮加 stopPropagation**

定位到 `<button ... onClick={() => onToggleExpanded(row.id)} ...>`（约 183 行），改为：

```tsx
<button
  type="button"
  onClick={(e) => {
    e.stopPropagation()
    onToggleExpanded(row.id)
  }}
  aria-label={isExpanded
    ? (t('videos.collapseHistoryDetails') as string)
    : (t('videos.expandHistoryDetails') as string)}
  aria-expanded={isExpanded}
  className={cn(
    'inline-flex h-5 w-5 items-center justify-center rounded-[4px]',
    'border border-[var(--color-line)] bg-white',
    'text-[12px] leading-none text-[var(--color-ink-4)]',
    'hover:text-[var(--color-ink)] hover:border-[var(--color-ink-5)]',
    'shrink-0 transition-colors',
  )}
>
```

- [ ] **Step 5: checkbox 改为 onChange 占位**

定位到：

```tsx
<input
  type="checkbox"
  checked={isSelected}
  onChange={() => onToggleSelect(row.id)}
  aria-label={`select ${row.original_filename ?? row.id}`}
  className="w-3.5 h-3.5 accent-[var(--color-blue)]"
/>
```

改为：

```tsx
<input
  type="checkbox"
  checked={isSelected}
  onChange={() => {}}
  aria-label={`select ${row.original_filename ?? row.id}`}
  className="w-3.5 h-3.5 accent-[var(--color-blue)]"
/>
```

行的 onClick 会冒泡到外层 div 触发 `onRowClick`，无需 checkbox 自身处理。`onChange` 占位避免 React 警告"受控组件缺 onChange"。

- [ ] **Step 6: 删除按钮加 stopPropagation**

定位到：

```tsx
<button
  onClick={() => onDelete(row.id)}
  className={cn(
    'text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)]',
    'opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-1',
  )}
>
  {t('common.delete')}
</button>
```

改为：

```tsx
<button
  onClick={(e) => {
    e.stopPropagation()
    onDelete(row.id)
  }}
  className={cn(
    'text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)]',
    'opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-1',
  )}
>
  {t('common.delete')}
</button>
```

- [ ] **Step 7: 跑 build 验证 TS / TSX 编译通过**

Run: `cd frontend && npm run build`

Expected: build 成功，无类型错误。

- [ ] **Step 8: 跑前端纯函数测试再验一遍**

Run: `cd frontend && node --test --experimental-strip-types tests/videoSelection.test.ts`

Expected: 全部 PASS。

- [ ] **Step 9: 提交 Task 2~4 的合并改动**

```bash
git add frontend/src/pages/Videos.tsx \
        frontend/src/components/videos/VideoList.tsx \
        frontend/src/components/videos/VideoRow.tsx
git commit -m "feat(videos): shift/cmd row-click multi-select on list page"
```

---

## Task 5: 浏览器人工冒烟 + 后端测试不回归 + 部署

**Files:**
- 无代码改动。

- [ ] **Step 1: 启动本地开发服务器**

Run: `./run.sh`

Expected: 监听 `127.0.0.1:8080`，无错误。

- [ ] **Step 2: 用浏览器打开过滤页面**

访问 `http://127.0.0.1:8080/videos?group=15`（或任一非空分组）。

- [ ] **Step 3: 冒烟 1 — 单击 toggle**

在 3 个不同的 PENDING 行任意空白处单击。每次行高亮变蓝，checkbox 勾上。

Expected: 顶部 sticky 操作栏显示 "已选 3 项"。

- [ ] **Step 4: 冒烟 2 — Shift 范围正向**

清空选择。单击第 1 个 PENDING 行 → 按住 Shift 单击第 10 个可见行。

Expected: 第 1~10 行间的所有 PENDING 行都选中；已发布行未选中；选区不报错。

- [ ] **Step 5: 冒烟 3 — Shift 锚点持久 + 反向 + 累加**

单击第 5 行 → Shift 单击第 3 行 → 3~5 范围选中。
再 Shift 单击第 8 行 → 5~8 加入；anchor 仍是 5；3~5 保留。

Expected: 选中是 {3, 4, 5, 6, 7, 8}（跳过已发布行）。

- [ ] **Step 6: 冒烟 4 — filter 切换清空**

点 "状态: 已结束" chip。

Expected: 选择被清空，sticky 操作栏消失。再点 "全部"，回到带选择前的状态（无残留）。

- [ ] **Step 7: 冒烟 5 — + 按钮和删除按钮不触发选择**

清空。点击某行的 + 按钮：行展开详情面板，行未被选中。
hover 出现的删除按钮上单击：弹出删除流程（或直接删除），行未被选中。

Expected: 两个按钮按原行为工作；选择数仍为 0。

- [ ] **Step 8: 冒烟 6 — Cmd 等同于普通点击**

按住 Cmd（macOS）/ Ctrl（Windows）逐个点 PENDING 行。

Expected: 行为与无 modifier 相同，toggle。

- [ ] **Step 9: 冒烟 7 — 整套 assign 流程**

选 5 个 PENDING 行 → 在 sticky 栏选目标 group → 点 "确认分配"。

Expected: 弹 toast `assigned 5`，列表刷新；选择清空；anchor 重置（filter 没变，但 confirmAssign 里已写）。

- [ ] **Step 10: 后端无改动验证**

Run: `uv run pytest -q`

Expected: ≥ 385 passed，无新增 fail。

- [ ] **Step 11: 按 docs/production-deploy.md 部署**

按运行书的 tarball 流程打包 + scp + 解压到 aliyun `/opt/mkt`，重启 `mkt` 服务。本次无 schema 改动、无 worker 代码改动，可只重启 `mkt`。

Run（参考 runbook 实际命令）：

```bash
# 打包
git ls-files | tar -czf /tmp/mkt-deploy.tar.gz -T -
# scp /tmp/mkt-deploy.tar.gz root@139.196.52.92:/tmp/
# ssh root@139.196.52.92 'cd /opt/mkt && tar -xzf /tmp/mkt-deploy.tar.gz && systemctl restart mkt'
```

冒烟：访问 `http://139.196.52.92:8080/videos?group=15`，单击 + shift 多选可用。

- [ ] **Step 12: 已无需额外提交**

Task 2~4 已提交一个 commit。

---

## Self-Review

**Spec 覆盖检查：**

| Spec 要求 | 实现位置 |
|---|---|
| 普通点击 toggle | Task 1 测试 `toggle selects...`；Task 4 行 onClick |
| Shift 范围加入 | Task 1 测试 `shift+click adds...`；纯函数实现 |
| Cmd/Ctrl toggle 同义 | Task 4 行 onClick 里 `meta: e.metaKey \|\| e.ctrlKey`；纯函数中 meta 不读，等同 toggle |
| + 按钮 / 删除按钮不触发选择 | Task 4 Step 4、Step 6 加 stopPropagation |
| filter 切换清 selection + anchor | Task 2 Step 3 |
| anchor 用 useRef | Task 2 Step 2 |
| Shift 范围用 items 索引计算 | Task 1 `computeNextSelection` 内部 findIndex |
| 已发布行无声跳过 | Task 1 测试 `shift+click skips...` |
| 已发布行可作为 anchor | Task 1 测试 `toggle on non-selectable row...` |
| Shift 不改 anchor | Task 1 测试 `shift+click adds inclusive range...` 断言 `nextAnchorId, 1` |
| onMouseDown 阻止 Shift 文本选区 | Task 4 Step 3 |
| checkbox onChange 占位 | Task 4 Step 5 |
| 顶部 sticky 操作栏与 confirmAssign 不变 | Task 2 Step 5 同步清 anchor，无其他改动 |
| 不做全选 / Cmd+A | 无任务（spec 已说不做） |

**Placeholder 扫描：** 无 TBD/TODO；每个代码 step 都有完整代码块；测试代码完整可粘贴。

**类型一致性：** `SelectionModifiers { shift, meta }` 在 Task 1 定义；Task 2 import 并使用；Task 3 / 4 行内 `{ shift, meta }` 字面量与之同形。`onRowClick: (id, modifiers) => void` 签名三处一致（Videos / VideoList / VideoRow）。
