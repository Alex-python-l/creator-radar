# 视频列表批量选择 Shift/Cmd 多选 — Design

## 背景

`/videos` 页面（含 `?group=15` 这类过滤视图）每行有一个小 checkbox（仅在视频未发布、即 `summary === 'PENDING'` 时显示）。用户用它把视频批量分配到某个分组。当前只能逐个 checkbox 点选，过滤出几十条待发布视频时操作非常慢。

需求：引入 Shift / Cmd 快捷多选，让一次性框选一段成为可能。

## 范围

- 仅影响视频列表行的"选择"语义。
- 不改变现有 checkbox 的视觉、不改变顶部 sticky 操作栏、不改变 assign 后端契约。
- 不改账号页（`/accounts`），尽管动作栏写的是"账号"——本次只在视频页面做。

## 交互规则

行点击触发选择（整行任意空白处）。点 + 展开按钮、点删除按钮保持原行为，不触发选择。

| 输入 | 行为 |
|---|---|
| 普通点击行 | toggle 该行；设当前行为 anchor |
| Shift+点击行 | 从 anchor 行到当前行（含两端），范围内可选行**全部加入**当前选择；不重置已选；anchor 不变 |
| Cmd+点击行（macOS） | toggle 该行；设当前行为 anchor |
| Ctrl+点击行（Win/Linux） | 同 Cmd |
| 点击 + 按钮 / 删除按钮 | 保持原行为，事件不冒泡为选择 |
| 切换分组 chip / 状态 chip | 清空选择，清空 anchor（现状已清选择，新增清 anchor） |

要点：

- **anchor** = 最近一次"非 Shift"点击的行 id。Shift 不更新 anchor。无 anchor 时 Shift+click 退化为普通 toggle。
- Shift 范围用 `items[]` 的索引计算（min..max），逐项处理。已发布、无 checkbox 的行**无声跳过**（不报错、不提示）。
- Shift 行为统一为"加入"——不做"清除范围外其他选中"。理由：批量分配场景里用户通常是累加选择，避免误操作。
- 锚点行本身是否可选不影响 anchor 记录；只是它若不可选就不会被加入选中。
- Cmd 与普通点击同义（toggle），保留是为了符合 macOS 用户的肌肉记忆。

## 模型 / 状态

`Videos.tsx` 已有 `selected: Set<number>`。新增：

```ts
const anchorIdRef = useRef<number | null>(null)
```

- 用 `useRef` 而非 state：anchor 变化不需要触发重渲染，写入即用。
- `useEffect` 清空 selection 的同一处增加 `anchorIdRef.current = null`。

新增顶层回调 `handleRowClick(id: number, modifiers: { shift: boolean; meta: boolean })`：

- shift 且有 anchor：计算 `items` 中 anchor → id 索引区间，遍历范围内每条 `v`，若 `v.summary === 'PENDING'` 则 `next.add(v.id)`。
- 其他情况：toggle `id`；写 `anchorIdRef.current = id`（即使 id 当前是已发布行，也作为 anchor）。

## 组件改动

### `frontend/src/pages/Videos.tsx`

- 新增 `anchorIdRef`。
- 替换 `toggleSelected` 为 `handleRowClick(id, mods)`（保留 `toggleSelected` 内部作为 `mods=空` 路径，或直接 inline）。
- 把 `handleRowClick` 透传给 `VideoList`，再透传给 `VideoRow`。命名仍以 `onToggleSelect` 暴露给 `VideoList` 也可——选择"`onRowClick`"更准确，本次 spec 采用 `onRowClick`。
- `useEffect` 清空 selection 处一并清 anchor。

### `frontend/src/components/videos/VideoList.tsx`

- 把 prop `onToggleSelect: (id) => void` 改名 / 新增 `onRowClick: (id, mods) => void`，透传给 `VideoRow`。

### `frontend/src/components/videos/VideoRow.tsx`

- 整个外层 `<div role="row">` 加 `onClick` 与 `onMouseDown`：
  - `onMouseDown`：当 `event.shiftKey` 时 `event.preventDefault()`，避免 Shift+click 触发浏览器文本选区高亮。
  - `onClick`：调 `onRowClick(row.id, { shift: e.shiftKey, meta: e.metaKey || e.ctrlKey })`。
- + 按钮与删除按钮的 onClick 内加 `e.stopPropagation()`，避免冒泡到行。
- checkbox 本身：行为与"点击行"等价。让 checkbox 也走 `onRowClick`（而非自己的 `onChange`）——把 `<input type="checkbox" onChange={...} />` 改成 `checked` 受控但 `onChange={() => {}}`，由父行的 onClick 接管；或保留 onChange 但保证不重复触发（推荐前者，避免两个事件源）。
- 鼠标命中"光标"提示：行 `cursor-pointer`（非已发布行）/ 普通（已发布行可点选作锚点，但视觉上没必要强提示）。本次先一致 `cursor-default`，不改光标。

## 边界与回归

- **虚拟化下的正确性**：`useVirtualizer` 不影响 anchor / 范围语义，因为我们用 `items[]` 而非 DOM 计算范围。Shift 触达没渲染的行也能正确选中。
- **filter 变化重置**：`useEffect` 已经清 `selected`；本次补清 anchor。
- **跨页加载**：用户 scroll 触发 `onLoadMore`，新加载的视频不会自动加入选择；anchor 仍是最近一次单击的 id，下一次 Shift 范围仍正确。若 anchor 因 filter 变化被清，则下一次 Shift 退化为 toggle。
- **已发布行作为 anchor**：允许。用户在已发布行单击 → anchor 记下 → 下移几行 Shift+click 未发布行 → 范围内"自上而下"的未发布行都被选中，已发布行被跳过。这是符合直觉的"我从这条开始往下选"。
- **文本选区**：Shift+click 在标准浏览器里会扩展文本选区。`onMouseDown` 里在 Shift 时 `preventDefault` 即可消除。
- **a11y**：checkbox 仍保留，aria 仍有效；行 click 是增量交互，键盘用户走原 checkbox 即可。

## 测试

非自动化（前端无 Vitest 体系）；人工冒烟：

1. 在 `/videos?group=15`，普通点 3 个 PENDING 行 → 都选中。
2. 单击第 1 行 → Shift+点击第 10 行 → 1~10 行内 PENDING 全选，已发布行未选。
3. anchor 持久性：单击第 5 行 → Shift+点击第 3 行 → 3~5 全选；再 Shift+点击第 8 行 → 5~8 加入选择（anchor 仍是 5）。
4. 切换 `状态: 已结束` chip → 选择 + anchor 清空。
5. 点 + 按钮 → 展开详情面板，不选中；点删除 → 删除该视频，不选中。
6. Cmd+点击 → 等同于普通点击 toggle。
7. 顶部 sticky 操作栏在 selection > 0 时出现；选 group + 确认分配走原 `api.assignVideos` 流程，无回归。

## 不做的事

- 不支持"取消整范围"（Shift 仅加入，不做"如果范围全选则反向取消"的 GitHub 风格逻辑）。简化心智模型。
- 不支持"全选"按钮 / `Cmd+A`。如有需求另立 spec。
- 不引入新 hook `useVideoSelection`。状态保留在 `Videos.tsx`，因为它本就管 assign 流程。
