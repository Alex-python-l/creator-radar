# Videos 双视图重做 — Design

**日期**: 2026-06-08
**作者**: will (with Claude)
**状态**: Draft — pending user review

## 背景

`/videos?group=…` 当前是一个 9 列虚拟化表格（[Videos.tsx](frontend/src/pages/Videos.tsx)、[VideoList.tsx](frontend/src/components/videos/VideoList.tsx)、[VideoRow.tsx](frontend/src/components/videos/VideoRow.tsx)）：

- 行高 44px，9 列全是 11–13px 灰色小字（日期 / 复选框 / 文件名 / 分组 / 5 个平台图标 / 状态文字 / 手机 / 标题预览 / 删除）
- 没有任何视频缩略图，完全是文字行
- "这条视频 × 5 个平台 × 当前状态 × 用哪个手机" 被切碎到 3 个独立列里，眼睛要扫多个字段才能拼出语义
- 总状态文字（已完成/失败/进行中）和 5 个平台图标的红点是同一信息的两次表达

用户反馈："信息的展现不太好"。

## 目标

1. 引入视频缩略图，让页面有"视频"的视觉感
2. 把"5 个平台 × 状态 × 手机" 合成**一组视觉单元**（平台徽章柱）
3. 提供**列表 / 网格双视图**，按场景切换：批量操作 → 列表；浏览今日产出 → 网格
4. 保留所有现有功能（虚拟化滚动、批量勾选、详情面板、删除、失败截图）

## 非目标

- **不改业务逻辑**：assignment 算法、warmup、publish、caption 生成保持原样
- **不引入新 npm 依赖**：徽章、缩略图占位都用现有的 React + Tailwind
- **不改 Procrastinate 队列结构**，只新增一个一次性 backfill 任务
- **不做视频预览/播放**：缩略图就是静态 JPG，点击照常进详情面板

## 后端

### 数据契约

`GET /api/videos` 列表响应的每个 item 新增两个字段：

```python
class VideoListRow(BaseModel):
    # ...existing fields
    thumbnail_url: str | None      # "/static/thumbnails/{id}.jpg" 或 None
    duration_seconds: int | None   # 整数秒，None 表示未探测/失败
```

`videos` 表新增两列（Alembic 迁移）：

```sql
ALTER TABLE videos ADD COLUMN thumbnail_path VARCHAR(255);   -- 相对路径，如 "thumbnails/12.jpg"
ALTER TABLE videos ADD COLUMN duration_seconds INTEGER;
```

`thumbnail_url` 由 API 层拼接：`/static/thumbnails/{id}.jpg` 当 `thumbnail_path` 不为 NULL，否则 `None`。**前端永远不直接读 `thumbnail_path`。**

### 缩略图生成

新增模块 [`app/services/thumbnail_service.py`](app/services/thumbnail_service.py)：

```python
def generate_thumbnail(video_path: Path, video_id: int) -> tuple[str | None, int | None]:
    """同步用 ffmpeg 抽第 1 秒一帧 + 探测时长。

    Returns:
        (thumbnail_path, duration_seconds)
        任何失败都返回 (None, None) 而不抛错。
    """
```

实现细节：

- 缩略图：`ffmpeg -ss 00:00:01 -i {video} -vframes 1 -vf scale=480:-2 -q:v 4 -y {out}`（宽 480，高自适应到偶数）
- 时长：`ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 {video}`
- 输出路径：`data/thumbnails/{video_id}.jpg`（绝对路径用 `config.DATA_DIR / "thumbnails"`）
- ffmpeg / ffprobe 不在 PATH 或返回非零 → `subprocess.run` 不抛错，函数返回 `(None, None)`
- 视频时长 < 1 秒：用 `-ss 00:00:00` 抓首帧
- 整个函数有 60 秒超时；超时返回 `(None, None)`

调用点：

- [`app/api/videos.py:upload`](app/api/videos.py) 在 `vid_repo.create` 成功后**同步**调用，把结果 update 回 video 行
- [`app/services/drive_import.py`](app/services/drive_import.py) — Drive 导入完一个文件后同样同步调用
- 同步调用是因为缩略图小（10–30 KB）+ 抓首帧只需 0.5–1 秒；不值得引入异步任务复杂度

### StaticFiles 挂载

[`app/main.py:create_app`](app/main.py) 在 SPA fallback **之前**挂载：

```python
thumbnails_dir = Path(config.DATA_DIR) / "thumbnails"
thumbnails_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/thumbnails", StaticFiles(directory=thumbnails_dir),
          name="thumbnails")
```

复用现有 `_IMMUTABLE` cache header 不需要，因为缩略图按 video_id 不变，但浏览器自带 ETag 协商已足够。

### 历史数据迁移

新增任务 [`app/tasks/backfill_thumbnails.py`](app/tasks/backfill_thumbnails.py)：

```python
@app.task(name="backfill_thumbnails", queue="default")
async def backfill_thumbnails_task():
    """扫所有 thumbnail_path is NULL 的 video，逐个生成。

    每条之间 sleep 0.2s 避免 ffmpeg 把 worker CPU 打满。
    """
```

部署后手动通过 `python -m procrastinate -a app.tasks.procrastinate_app defer backfill_thumbnails` 触发一次。

### Alembic 迁移

新增 `alembic/versions/XXXX_video_thumbnail_columns.py`：

```python
def upgrade():
    op.add_column('videos', sa.Column('thumbnail_path', sa.String(255), nullable=True))
    op.add_column('videos', sa.Column('duration_seconds', sa.Integer, nullable=True))

def downgrade():
    op.drop_column('videos', 'duration_seconds')
    op.drop_column('videos', 'thumbnail_path')
```

非破坏性，无需 PG 备份。

## 前端

### 文件改动

新增：

```
frontend/src/
├── lib/useVideoView.ts                     ← localStorage hook
├── components/videos/
│   ├── ViewSwitcher.tsx                    ← segmented control
│   ├── DateSectionHead.tsx                 ← "今天 · 3 个" sticky header（两视图共用）
│   ├── PlatformBadgeColumn.tsx             ← 平台徽章组件（两视图共用）
│   ├── VideoThumbnail.tsx                  ← 缩略图组件（两视图共用，带 fallback）
│   ├── VideoListView.tsx                   ← 列表视图（B）
│   ├── VideoMediaRow.tsx                   ← 列表单行
│   ├── VideoGridView.tsx                   ← 网格视图（C）
│   └── VideoCard.tsx                       ← 网格单卡
```

修改：

- [`Videos.tsx`](frontend/src/pages/Videos.tsx) — PageHeader 加 ViewSwitcher，根据 `useVideoView()` 切换渲染 ListView / GridView
- [`useVideoPages.ts`](frontend/src/lib/useVideoPages.ts) — `VideoListRow` 类型增加 `thumbnail_url`、`duration_seconds`

删除：

- `VideoList.tsx`, `VideoRow.tsx` — 被 `VideoListView` + `VideoMediaRow` 替代

保留不动：

- `VideoDetailPanel.tsx` — 列表视图 inline 展开使用；网格视图改用 Modal 包装
- `useVideoFilter.ts`、`useExpandedVideos.ts`、`useScrollRestore.ts`
- `videoSelection.ts`、`failureScreenshotPreview.ts`

### `useVideoView` hook

```typescript
// frontend/src/lib/useVideoView.ts
type Mode = 'list' | 'grid'

export function useVideoView(): [Mode, (m: Mode) => void] {
  const [mode, setMode] = useState<Mode>(() => {
    try {
      const v = localStorage.getItem('videos:view')
      return v === 'grid' ? 'grid' : 'list'
    } catch {
      return 'list'   // 隐私模式 fallback
    }
  })
  const update = (m: Mode) => {
    try { localStorage.setItem('videos:view', m) } catch {}
    setMode(m)
  }
  return [mode, update]
}
```

### `PlatformBadgeColumn` 组件

```typescript
type Props = {
  platforms: Partial<Record<Platform, {
    result: string
    has_phone: boolean
    phone_label?: string | null      // ← 后端新增字段，per-platform 手机
  }>>
  fallbackPhoneLabel: string | null   // video 级总手机标签（兼容旧字段），inline 布局显示
  layout: 'column' | 'inline'         // column = 列表 / inline = 网格
  size?: 'sm' | 'md'                  // sm=22px (网格) / md=26px (列表)，默认 md
}
```

- `layout='column'`（列表）：5 列徽章，每个徽章下方显示 `platforms[plat].phone_label`（如果未启用或无手机则显示 `—`）
- `layout='inline'`（网格）：所有徽章一行，右侧显示 `fallbackPhoneLabel`（如果所有 ENABLED 平台共享同一手机），否则显示"多个手机"

徽章语义：

- 背景：平台品牌色（复用 [PlatformIcon.tsx](frontend/src/components/PlatformIcon.tsx) 的颜色定义）
- 未启用（`platforms[plat]` 为 undefined）→ `opacity .22 + grayscale .6`
- 右下角角标（10–12px 圆）：
  - `result='SUCCESS'` → 绿底 ✓
  - `result='FAILURE'` → 红底 !
  - `result='INITIATED'` 或 `'QUEUED'` → 蓝底 ·
  - `result='PENDING'` 或未启用 → 无角标
- `layout='column'`：徽章下方一行 9.5px 灰字 = phone_label（截断到 50px max-width）；未启用平台显示 `—`
- `layout='inline'`：所有徽章一行，右边一个共享 phone_label（如果当前 video 的所有 ENABLED 平台共享同一台手机时）

### `VideoThumbnail` 组件

```typescript
type Props = {
  url: string | null
  duration: number | null     // 秒
  variant: 'list' | 'grid'    // list = 60×40 / grid = 16:10 aspect ratio
  videoId: number             // 用于生成稳定的占位渐变色 hash
}
```

- `url` 存在 → `<img src={url} loading="lazy" />`；加 `onError` 回退到占位
- `url=null` 或加载失败 → 用 `videoId` 算 hash → 选 8 套预设渐变色之一 → 居中显示半透明白色 ▶ icon
- `duration` 不为 null → 右下角 badge 显示 `mm:ss`
- 视频 icon 用 SVG 内联（不引入 lucide-react 等）

### 视图行为

#### `VideoListView`（B）

- 沿用 `@tanstack/react-virtual`，`estimateSize` 改为：
  - 媒体行 64px（从 44px 提升）
  - 详情面板 180px（不变）
  - 日期段标题 32px
  - sentinel 40px
- 行结构：

  ```
  [60×40 缩略图]  [文件名 14px / 时间·分组 11.5px]  [平台徽章柱 5×（26px 徽章 + 手机小字）]
  ```

- 日期分段：扫到下一行 `dateOnly(uploaded_at)` 变化时插入一个 `DateSectionHead`（"今天 · 3 个"），代替现有 `isContinuation` 省略日期的方案
- 左侧 4px 总状态色条仍然保留（绿/橙/红/蓝/灰），紧贴行最左
- 复选框 → 移到 hover 时左上角浮出（不再单独占一列），节省空间；批量选中时全部露出
- 文件名旁的 `+` 展开按钮 — 保留
- 删除按钮 — 行 hover 时右侧露出

#### `VideoGridView`（C）

- 使用同一个 `@tanstack/react-virtual` 的 lanes 模式（`useVirtualizer({ lanes: cols })`）
- 列数：通过 `ResizeObserver` 监听容器宽度，按 `Math.max(2, Math.floor(width / 240))` 算 lanes（最少 2 列，目标卡片宽 220–280px）
- 卡片结构：

  ```
  ┌──────────────────────┐
  │   16:10 缩略图        │
  │   [时长 badge]       │
  │   ▶ 半透明播放图标    │
  │   ════ 底部 3px 总状态色条 ════ │
  ├──────────────────────┤
  │ 文件名 13px           │
  │ 时间·分组 11px        │
  │ [5 个平台徽章] phone  │
  └──────────────────────┘
  ```

- 卡片宽度对应的高度 ≈ 16:10 缩略图 + 12px padding × 2 + 文件名行 + meta 行 + 平台行 ≈ 175–195px
- 点击卡片：PENDING → 选中切换；TERMINAL → 打开 Modal 版 `VideoDetailPanel`
- hover 卡片右上角浮出 `⋯` 菜单按钮，下拉里 "查看详情" / "删除"（PENDING）
- 日期分段：插入 `grid-column: 1 / -1` 占满一行的 sticky header

### 工具栏区域改造

[`Videos.tsx`](frontend/src/pages/Videos.tsx) `PageTitle` 部分调整：

```
┌──────────────────────────────────────────────────────────────┐
│ 视频                       [▤ 列表 | ▦ 网格] [刷新] [+ 导入]│
│ 共 142 个 · 已发 89 · 待发 53                                 │
└──────────────────────────────────────────────────────────────┘
[分组] 全部 雅思口语✓ 商务英语 未分组
[状态] 全部✓ 待发布 已结束
```

- ViewSwitcher 是一个 segmented control（两个按钮，背景灰底，激活态白底 + 阴影），不是两个独立按钮
- subtitle 由 `t('videos.libraryHeading')` 改为更具信息量的 `共 {{total}} 个 · 已发 {{success}} · 待发 {{pending}}`
- 后端在 `/api/videos` 响应里需要带上 `summary_counts: { total, success, pending }`（基于当前 filter 统计）

## 错误处理 / 边界

| 情况 | 行为 |
|---|---|
| ffmpeg 不在 PATH | `generate_thumbnail` 静默返回 `(None, None)`；日志 `logger.warning("ffmpeg unavailable")` 一次 |
| ffmpeg 调用超时（>60s） | 同上 |
| video 文件不可读 | 同上 |
| ffprobe 返回非数字 | duration 为 None；缩略图正常 |
| StaticFiles 请求不存在的图 | 404 → `<img onError>` 切到占位渐变 |
| duration_seconds 为 null | 缩略图右下角不显示时长 badge |
| localStorage 不可用 | `useVideoView` 永远返回 `'list'`，setMode 静默吞错 |
| 平台 platforms[plat] === undefined | 徽章 grayscale + 无角标，列表手机标签 `—` |
| 网格容器宽度 < 480px | 自动降为 2 列，卡片最窄 ~220px |

## 测试

### 后端

新增 `tests/test_thumbnail_service.py`：

- 用 `tests/fixtures/short.mp4`（5 秒小视频）跑一次 `generate_thumbnail`，断言返回的 `thumbnail_path` 存在且文件 > 1KB，`duration_seconds` 是 5
- monkeypatch `subprocess.run` 抛 `FileNotFoundError` 模拟 ffmpeg 不存在 → 断言返回 `(None, None)`
- monkeypatch `subprocess.run` 模拟 ffmpeg 返回 nonzero → 断言返回 `(None, None)`

扩展 `tests/test_api_videos.py`：

- 上传一个 mp4 → 响应里 `thumbnail_url` 是 `/static/thumbnails/{id}.jpg` 或 None
- GET `/api/videos` 列表里每个 item 都有 `thumbnail_url` 字段（即使是 None）
- GET `/api/videos` 响应里有 `summary_counts: {total, success, pending}`

不增加 fixture mp4 的话，可以用 `pytest.importorskip("imageio_ffmpeg")` 生成一个，但更简单：fixture 用一个 5 KB 的提前 commit 进 tests/fixtures 的真实 mp4 文件。

### 前端

新增 `frontend/src/lib/__tests__/useVideoView.test.ts`：

- 写入 / 读取 localStorage
- localStorage throws → fallback 到 'list'

新增 `frontend/src/components/videos/__tests__/PlatformBadgeColumn.test.tsx`：

- 5 种 result 状态的角标渲染（SUCCESS / FAILURE / INITIATED / PENDING / 未启用）
- `layout='column'` vs `layout='inline'` 的 DOM 差异

视觉验证（preview_* 工具）：

- 切到 grid 后刷新，仍在 grid（localStorage 持久化）
- thumbnail_url=null 时占位渐变 + ▶ icon 正常
- 1280 / 768 / 375 三种宽度下列表 + 网格列数变化
- 同一 video 在两个视图下信息一致

## 部署

1. **预检 ffmpeg**：`ssh aliyun "which ffmpeg && ffmpeg -version | head -1"`；如果不存在 → `ssh aliyun "sudo apt-get install -y ffmpeg"`
2. 走标准 `docs/production-deploy.md` 流程打包 + 上传 tarball + 重启 `mkt` 和 `mkt-worker`
3. SSH 上服务器跑 `cd /opt/mkt && uv run alembic upgrade head`
4. SSH 跑 `uv run python -m procrastinate -a app.tasks.procrastinate_app defer backfill_thumbnails` 给历史数据补图
5. 浏览器访问 `http://139.196.52.92:8080/videos` 验证：
   - 切换视图能记住
   - 缩略图加载（新上传 + 回填后的旧视频）
   - 平台徽章 + 角标显示正确

非破坏性迁移，无需 PG 备份；但 deploy 前仍按惯例 `docker exec svm-postgres pg_dump` 一次留底，根据 [`commit-every-change`](mkt-deployment) 和 [`deploy-after-every-change`](deploy-after-every-change) 习惯一气呵成部署。

## 滚动条与开放问题

- **手机标签同步**：列表视图下每个平台徽章下方都显示该平台的 phone_label。当前 [`VideoListRow`](frontend/src/lib/useVideoPages.ts) 的 `platforms[plat]` 只有 `{ result, has_phone }`，**需要后端补 `phone_label`**。`api/videos.py` 的列表 SQL 已经 join publications + accounts，加一个 column 即可
- **是否做卡片 grid 的 `useVirtualizer` lanes 模式**：这个模式社区支持但文档少；如果实现成本高，可以先用普通 CSS grid（不虚拟化），假设视频总数 < 1000 不会卡 — 接受作为 v1 简化
- **网格卡片宽度** 220–280px 是凭感觉定的，落地后跑一遍真实数据调整
