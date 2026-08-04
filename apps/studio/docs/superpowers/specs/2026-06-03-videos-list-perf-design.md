# Videos 列表性能优化 — Design Spec

**Date**: 2026-06-03
**Status**: Draft for user review
**Background ticket**: 视频库累积到 1346 条，列表打开慢；预期未来还会持续增长

## 背景

`/api/videos` 当前一次性返回所有视频 + 全部 publications（≈1346 行 × 4 平台 ≈ 5400 publication 对象），前端按日期分组渲染全部 1346 行，每 15 秒整体轮询一次。三个症状叠加：

1. **首屏白屏久**（主要痛点）：JSON payload 大 + 序列化慢 + 网络传输慢
2. **滚动/操作卡顿**：1346 行 × 多列 DOM 节点上万
3. **轮询时 UI 顿**：每 15 秒重复全量

用户的库会持续增长（"以后都会变成历史"），不能靠"只看 pending"的 UI 切割回避。本方案做服务端分页 + 按需详情 + 虚拟滚动 + 事件驱动刷新，从源头治理。

## 范围

### 改

1. `GET /api/videos` 改为游标分页 + 服务端过滤，payload 瘦身
2. 新增 `GET /api/videos/:id/detail`，行展开时按需 fetch
3. `app/repositories/videos.py` 新增 `list_page` / `count_filtered` / `get_detail`，删 `list_all`
4. 前端 `Videos.tsx` 改造：游标分页 + 虚拟滚动 + 事件驱动刷新
5. 前端拆分子组件 + 新增 `useVideoPages` / `useVideoDetail` hooks
6. 新增日期独立列，删现有最右"uploaded"列
7. 加索引 `ix_videos_group_id`（如未有）、`ix_publications_video_id`（如未有），走 Alembic migration

### 不改

- `POST /api/videos/upload`、`DELETE /api/videos/:id`、`POST /api/videos/assign`、`POST /api/videos/:id/publish` 等其他端点
- `vid_repo.count_for_group`（Groups 页面用）、`bulk_set_group`
- 后端 publication / GeeLark / 调度逻辑
- 鉴权
- 不引入新的数据库/状态库（不用 React Query / SWR）
- 不做搜索框（用户明确不要）
- 不做 SSE 实时推送（这次不做，留作以后扩展）

## API 设计

### 列表（改造现有）

```
GET /api/videos?
    group_id=<int|"all"|"ungrouped">    # 默认 "all"
    status=<"pending"|"terminal"|"all"> # 默认 "all"
    cursor=<int>                         # 上一页最后一行的 video.id，可选
    limit=<int>                          # 默认 50，上限 200
```

返回：

```json
{
  "items": [
    {
      "id": 1234,
      "original_filename": "xxx.mp4",
      "uploaded_at": "2026-06-03T12:34:56Z",
      "group_id": 7,
      "summary": "PENDING",
      "platforms": {
        "tiktok":    {"result": "SUCCESS",  "has_phone": true},
        "youtube":   {"result": "PENDING",  "has_phone": false},
        "instagram": {"result": "FAILURE",  "has_phone": true},
        "facebook":  {"result": "PENDING",  "has_phone": false}
      },
      "caption_preview": "First 80 chars…",
      "phone_label": "phone-007"
    }
  ],
  "next_cursor": 1185,
  "total": 1346
}
```

**约定**：
- 游标按 `id desc` 排序：`WHERE id < cursor ORDER BY id DESC LIMIT limit + 1`
- 多取的第 N+1 行用于判断 `next_cursor`，不返回给客户端
- `total` 仅在 `cursor` 为空（首屏）时返回；翻页时省略，前端复用首屏 total
- `platforms` 字典只包含实际存在 publication 的平台键，缺失平台不出现
- payload 不含完整 publications 数组、caption 全文、fail_screenshot、notes

### 详情（新增）

```
GET /api/videos/:id/detail
```

返回与现有 `vid_repo.list_all` 单行同结构：完整 publications 数组（含 caption 全文、fail_code、fail_screenshot、notes、posted_at、account_profile_name）。404 当视频不存在。

### 未变接口

`/upload`、`/:id` DELETE/PATCH、`/assign`、`/:id/publish`、`/publications/:id/screenshot`、`/import*` 全保留原行为。

## 数据层设计

`app/repositories/videos.py`：

- 删 `list_all`（只在 `/api/videos` 用，无外部复用）
- 新增 `list_page(session, *, group_id, status, cursor, limit)` → `(items, next_cursor)`
- 新增 `count_filtered(session, *, group_id, status)` → `int`
- 新增 `get_detail(session, vid)` → `dict | None`
- 保留 `create`、`bulk_set_group`、`existing_drive_file_ids`、`get`、`set_account`、`clear_stored_path`、`delete`、`count_for_group`

### 列表 SQL

一条聚合查询完成。语义见下：

```sql
SELECT
  v.id, v.original_filename, v.uploaded_at, v.group_id,
  owner.profile_name AS phone_label,
  jsonb_object_agg(p.platform,
                   jsonb_build_object('result', p.result,
                                      'has_phone', p.account_id IS NOT NULL))
    FILTER (WHERE p.platform IS NOT NULL) AS platforms,
  LEFT(MAX(NULLIF(p.caption, '')), 80) AS caption_preview,
  CASE
    WHEN COUNT(p.id) = 0 THEN 'PENDING'
    WHEN BOOL_OR(p.result NOT IN ('SUCCESS','FAILURE')) THEN 'PENDING'
    ELSE 'TERMINAL'
  END AS summary
FROM videos v
LEFT JOIN accounts owner ON owner.id = v.account_id
LEFT JOIN publications p ON p.video_id = v.id
WHERE
  (:cursor IS NULL OR v.id < :cursor)
  AND <group_filter>
GROUP BY v.id, owner.profile_name
HAVING <status_filter>
ORDER BY v.id DESC
LIMIT :limit + 1
```

`group_filter`：
- `"all"` → `TRUE`
- `"ungrouped"` → `v.group_id IS NULL`
- 具体 id → `v.group_id = :gid`

`status_filter`（聚合依赖，走 HAVING）：
- `"all"` → `TRUE`
- `"pending"` → `COUNT(p.id) = 0 OR BOOL_OR(p.result NOT IN ('SUCCESS','FAILURE'))`
- `"terminal"` → `COUNT(p.id) > 0 AND NOT BOOL_OR(p.result NOT IN ('SUCCESS','FAILURE'))`

### Count SQL

```sql
SELECT COUNT(*) FROM (
  SELECT v.id FROM videos v
  LEFT JOIN publications p ON p.video_id = v.id
  WHERE <group_filter>
  GROUP BY v.id HAVING <status_filter>
) sub
```

### 详情 SQL

复用现有 `list_all` 两步 join 模式但 `WHERE v.id = :vid` —— 单行场景，N+1 风险不存在。

### 索引

新 Alembic migration `alembic/versions/<rev>_videos_list_indexes.py`：

```python
op.create_index('ix_videos_group_id', 'videos', ['group_id'],
                if_not_exists=True)
op.create_index('ix_publications_video_id', 'publications', ['video_id'],
                if_not_exists=True)
```

注意：**PostgreSQL 不会为外键自动建索引**（与 MySQL 不同），所以即使 `publications.video_id` 是 FK 也可能没有索引，需显式创建。`if_not_exists=True` 在 Alembic 1.10+ 支持；实施前先 `\d videos` / `\d publications` 看现状，若已有同名索引则去掉该 op。

## 前端架构

### 状态

```ts
type Filter = {
  group_id: number | 'all' | 'ungrouped'
  status: 'all' | 'pending' | 'terminal'
}

type Page = {
  items: VideoRow[]
  next_cursor: number | null
  total: number | null   // 仅首屏有
}
```

容器组件持有 `filter`、`pages`、`loading`、`detailCache` (`Map<number, VideoDetail>`)。

### 不引第三方数据库

只引一个分页 hook + 一个详情 hook：

```
frontend/src/lib/useVideoPages.ts   // { items, total, hasMore, loadMore, refresh, loading }
frontend/src/lib/useVideoDetail.ts  // { detail, loading, error, retry } —— 按 videoId 缓存
```

### 加载触发

| 触发 | 行为 |
|---|---|
| 首屏 / `filter` 变化 | 重置 pages，拉第一页；用 AbortController abort 旧请求 |
| 滚到底 sentinel 入视口 | `loadMore()` 拉下一页 |
| 上传完成 | `refresh()`（只刷第一页，pages[1..] 不动） |
| 删除 / 分组分配 / 单视频 publish | 乐观更新（直接改 pages 状态） |
| `visibilitychange: visible` | 距上次刷新 > 30s 才 `refresh()` |
| 顶部"刷新"按钮 | 主动 `refresh()` |

**不做定时轮询**。后台 worker 推进的状态变化在用户切走再切回来时才同步。

### 虚拟滚动

库：`@tanstack/react-virtual`（React 19 兼容，社区主流，动态行高支持好）。

行模型平铺：

```ts
type Row =
  | { kind: 'video';    video: VideoRow }
  | { kind: 'detail';   videoId: number }    // 展开时插入紧跟在 video 后
  | { kind: 'sentinel' }                     // 永远是最后一行
```

`useVirtualizer({ count: rows.length, estimateSize: () => 56, ...measureElement })`，动态行高用 `measureElement`。

外层 `<div role="table">`，行用 `role="row"` —— 不再用 `<Table>/<THead>/<TBody>` 包装（虚拟化不兼容 `<table>` 的 reflow）。原 `<Table>` 组件保留，仅在不需虚拟化的位置继续用（Modal 等）。

### 日期列

加在第一列（checkbox 前），宽 ~92px：

- 跟上一行同一日期 → 显示浅色 │ 竖线
- 不同 → 显示日期 `Jun 03` / `Today` / `Yesterday`
- 跨页拼接：前端拿到第 N 页时跟 pages[N-1] 最后一行对比决定首行是否省略
- hover title 显示 `YYYY-MM-DD HH:MM`

删除现有最右"uploaded"列，时间信息收敛到这一列。

### 展开详情

```ts
const toggleExpanded = async (id: number) => {
  if (expandedVideos.has(id)) { setExpandedVideos(prev => del(prev, id)); return }
  setExpandedVideos(prev => add(prev, id))
  if (!detailCache.has(id)) {
    const d = await api.videoDetail(id)
    setDetailCache(prev => new Map(prev).set(id, d))
  }
}
```

展开行在 `rows` 中以 `detail` kind 插入紧跟 video 行后；占位 skeleton "加载中…"；fetch 完成后 measureElement 重算高度。缓存按 id 永不失效（同会话），refresh 不清缓存。

### Group filter

当前是客户端筛选 → 改为 `setFilter({ ...filter, group_id })` 触发服务端 reload。`selected`（批量分组复选框）在 filter 切换时清空，避免跨视图错乱。

### 子文件拆分

`Videos.tsx` 当前 1083 行，借这次重构拆：

```
pages/Videos.tsx                       # 容器：filter、刷新、Modal 协调
components/videos/VideoList.tsx        # 虚拟列表，纯渲染
components/videos/VideoRow.tsx         # 单行（普通行）
components/videos/VideoDetailPanel.tsx # 展开详情 UI：调用 useVideoDetail，渲染 skeleton / 详情 / 错误
components/videos/VideoDateCell.tsx    # 日期列
lib/useVideoPages.ts                   # 列表分页数据
lib/useVideoDetail.ts                  # 详情 fetch + 缓存
```

`useVideoDetail` 负责数据（fetch + 缓存 + 错误状态），`VideoDetailPanel` 负责呈现，分工清晰。

Modal（导入 / 截图预览）、Upload 进度、批量分配工具栏留在 `Videos.tsx`。

### Loading UX

- 首屏：5 行 skeleton row
- 翻页：底部 sentinel 上方 "加载中…"，不挡已渲染
- 切 filter：保留旧内容 300ms（debounce），新数据来了再 swap
- 详情：展开行内 skeleton "加载中…"

## 错误处理

| 场景 | 行为 |
|---|---|
| 列表请求失败（首屏） | 整页 "加载失败，[重试]" |
| 翻页请求失败 | 底部 sentinel 行 "加载失败，[重试]"，已加载行不动 |
| 详情请求失败 | 展开行内 "详情加载失败，[重试]"，行本身不受影响 |
| `filter` 切换时旧请求未完成 | AbortController abort，安静丢弃 |
| 上传/删除后 refresh 失败 | toast，列表保留旧数据 |
| 详情缓存命中已删除视频 | 行已不在 pages 里，不会重新展开 |

`useVideoPages` 内部统一管理 abort，对调用者透明。

## 测试

### 后端

`tests/test_repositories_videos.py`（新建或扩展）：

- `list_page` 单元测试：
  - 空库
  - 各 `group_id`（all / ungrouped / 具体 id）
  - 各 `status`（all / pending / terminal）
  - 游标翻页：插 N > limit 行，两页拼接 = 全集，无重复无遗漏
  - 同日多行按 `id desc` 排
  - `platforms` 字典只含存在的平台键
- `count_filtered` 单元测试：跟 `list_page` 同样的过滤组合，count = 翻完全部页的 .items 总数
- `get_detail` 单元测试：返回完整 publications 数组；不存在 id 返 `None`

`tests/test_api.py`（扩展）：

- `GET /api/videos` 缺省参数返回首页 + total
- 带 `cursor` 翻页
- 删一个视频后再请求，total 减 1
- `GET /api/videos/:id/detail` 200 / 404

回归：保留所有现有 `vid_repo` 用例（`count_for_group`、`bulk_set_group` 等），baseline 385 测试不下降。

### 前端

不强制单元测试。建议覆盖：

- `lib/useVideoPages.ts` hook 测试：filter 切换 / loadMore / abort

Manual smoke：

- 上传 100 个 dummy 视频，验证滚动流畅 + 翻页 + 日期列正确
- group filter 切换 → 数据正确
- 删除一个视频 → total 减 1，列表立即反映
- tab 切走 60s 再切回 → 首页静默刷新

### 性能验证

实施前在生产快照（restore 到本地的 1346 视频副本）上跑 baseline：

| 指标 | baseline | 目标 |
|---|---|---|
| 首屏 `/api/videos` 响应时间 | ?ms | < 200ms |
| 首屏 JSON 体积 | ?KB | < 50KB |
| TTI（"加载中"消失） | ?ms | < 500ms |
| 滚动 fps（Chrome DevTools） | ? | ≥ 55 |

数字写进 commit message。

## 部署

### 数据库

新 Alembic migration 加索引。**非破坏性**——仅 `CREATE INDEX IF NOT EXISTS`，无需 `pg_dump` 备份。但仍按 prod 流程：

```
docker exec svm-postgres psql -U mkt -d mkt -c '\d videos'
docker exec svm-postgres psql -U mkt -d mkt -c '\d publications'
# 确认索引名不冲突后
ssh aliyun "cd /opt/mkt && uv run alembic upgrade head"
```

### 应用

按 `docs/production-deploy.md` 标准流程：tarball 构建（含 `frontend/dist`） → 上传 → 解包到 `/opt/mkt` → 重启 `mkt` 和 `mkt-worker`（虽然 worker 不直接受影响，但 app/ 共享代码已变）。

### 回滚

应用层：保留旧 tarball，回滚解包。
索引：保留无害（旧版列表 SQL 不用，新索引不影响）。

## 不在本次范围

- 真正的"实时同步"（SSE 推送状态变化）——以后用 `app/sse.py` 加
- 视频搜索框
- 跳页（页码分页器）
- Groups / Accounts 页面的类似优化——独立工作

## 关键 trade-off

| 取舍 | 选择 | 理由 |
|---|---|---|
| 游标 vs 页码 | 游标 | 新视频持续插入，页码 OFFSET 会漏/重；用户也无跳页需求 |
| 真虚拟滚动 vs 普通分页 DOM | 真虚拟滚动 | 用户预期库会持续增长，避免 1000+ 行 DOM 累积 |
| sticky 日期头 vs 日期列 | 日期列 | sticky 头跟虚拟滚动难配；日期列更适合表格语义且实现简单 |
| 定时轮询 vs 事件驱动 | 事件驱动 + 可见性兜底 | 视频管理页不是仪表盘，无强实时需求 |
| 第三方数据库（React Query 等）vs 自写 hook | 自写 | 项目当前无类似依赖，单页用例不值得引 |
| caption_preview 取"第一条非空" vs "按 platform 顺序" | SQL 任取 | 4 平台 caption 实际一致，差异对预览无意义 |
