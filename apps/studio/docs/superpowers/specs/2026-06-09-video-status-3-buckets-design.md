# 视频状态分类压缩到 3 桶 — Design

**日期**: 2026-06-09
**作者**: will (with Claude)
**状态**: Draft — pending user review

## 背景

当前 `/videos` 列表的状态徽章和过滤 chip 用 5 个桶：`success` / `in_progress` / `partial` / `failure` / `pending`。计算逻辑分布在 [_status_having_sql](app/repositories/videos.py:23)、[summary_counts_for](app/repositories/videos.py:260) 和前端 [deriveStatusBucket](frontend/src/components/videos/StatusPill.tsx:41) 三处。

5 桶设计存在两个问题：

1. **"部分完成"语义混乱。** 历史上为了让 `3 SUCCESS + 1 PENDING` 也算 partial，把判定条件放宽成"有终态、未全成全失"，结果把 `2 FAILURE + 2 PENDING`（零成功、Gemini API 拒绝授权）也卷进了"部分完成"——徽章显示橙色 ! 但实际什么也没完成。用户截图反馈"这能叫部分完成？"
2. **"进行中"定义模糊。** 当前 in_progress = 有 INITIATED/QUEUED；但当 `2 FAILURE + 2 PENDING` 应该归到哪儿没有自然落点，往 in_progress 塞又混淆了"进行中"的语义——用户明确："只有某个平台真的在跑 GeeLark 才能叫进行中"。

## 目标

1. 把状态桶压缩到 **3 个**：待处理 / 进行中 / 已处理
2. "进行中"严格指 GeeLark 上真在跑（INITIATED/QUEUED）
3. "已处理"涵盖一切"被系统操作过、有终态结果"的情况（含全成功 / 全失败 / 终态混合 / 终态 + PENDING 混合）
4. 徽章在桶名右侧附 `X/N` 比例，区分"4/4 已完结"和"2/4 还在跑"

## 非目标

- **不细分成败**：已处理桶里全成功 / 全失败 / 混合用同一徽章（同色同图标），靠点开详情看每平台明细
- **不改 publications 的 result 枚举**：仍然是 `PENDING / QUEUED / INITIATED / SUCCESS / FAILURE`
- **不改 Procrastinate 调度逻辑**：自动 `auto_publish_scan` / `check_publication_task` 不动

## 桶定义 & 优先级

按优先级从高到低判定（命中即停）：

1. **⟳ 进行中**（蓝）—— 至少一个 publication 是 `INITIATED` 或 `QUEUED`
2. **✓ 已处理**（蓝灰中性色）—— 不命中 1，且至少一个 publication 是 `SUCCESS` 或 `FAILURE`
3. **○ 待处理**（灰）—— 以上都不命中，即所有 publication 都是 `PENDING`，或视频没有 publication

桶之间互斥、穷尽。任意视频归且仅归一个桶。

## 徽章 UI

| 桶 | 徽章格式 | X / N 含义 |
|----|----------|------------|
| 进行中 | `⟳ 进行中 X/N` | X = INITIATED/QUEUED 数；N = 该视频 publication 总数 |
| 已处理 | `✓ 已处理 X/N` | X = SUCCESS+FAILURE 数；N = publication 总数 |
| 待处理 | `○ 待处理` | 不显示比例（X=0 没意义） |

N 取该视频的 publication 总数（一般是 4：TikTok / YouTube / Instagram / Facebook）。不到 4 的情况（例如平台被禁用、账号没绑定该平台）也按实际 publication 数算。

颜色继续沿用现有 CSS 变量：
- 进行中 → `--color-blue`
- 已处理 → 新增"中性蓝灰"，建议复用 `--color-ink-3` 或类似的中性深灰，避免暗示成败
- 待处理 → `--color-line-strong` / `--color-ink-5`（沿用当前 pending 配色）

## 决策矩阵（4 平台示例）

| s | f | inflight | pending | 桶 | 徽章 |
|----|----|----------|---------|-----|------|
| 0 | 0 | 0 | 4 | 待处理 | ○ 待处理 |
| 0 | 0 | 1 | 3 | 进行中 | ⟳ 进行中 1/4 |
| 0 | 0 | 4 | 0 | 进行中 | ⟳ 进行中 4/4 |
| 4 | 0 | 0 | 0 | 已处理 | ✓ 已处理 4/4 |
| 0 | 4 | 0 | 0 | 已处理 | ✓ 已处理 4/4 |
| 2 | 2 | 0 | 0 | 已处理 | ✓ 已处理 4/4 |
| 3 | 0 | 0 | 1 | 已处理 | ✓ 已处理 3/4 |
| **0** | **2** | **0** | **2** | **已处理** | **✓ 已处理 2/4** ← 用户截图 |
| 1 | 0 | 1 | 2 | 进行中 | ⟳ 进行中 1/4 |
| 1 | 1 | 1 | 1 | 进行中 | ⟳ 进行中 1/4 |

## 改动范围

### 后端

- [_status_having_sql](app/repositories/videos.py:23) — 缩减为 3 个分支（`pending` / `in_progress` / `handled`）。删掉 `success` / `partial` / `failure` 三个分支
- [summary_counts_for](app/repositories/videos.py:260) — SELECT 改成 3 列：`pending` / `in_progress` / `handled`，同步修正返回字典
- [app/api/videos.py:120](app/api/videos.py:120) 校验 status 参数 —— 旧的 5 个值改成新的 3 个
- 列表接口 **不增加新字段**：前端继续从 row 自带的 `platforms` dict 算 X/N（沿用今日 `deriveStatusBucket` 的模式，单源真相，避免后端字段和前端计算分叉）
- 老 status 值（success/partial/failure）的 URL 兼容性 —— 直接 400，不做 redirect（内部工具，用户没书签）

### 前端

- [StatusBucket](frontend/src/components/videos/StatusPill.tsx:6) 类型 —— 3 个值：`'pending' | 'in_progress' | 'handled'`
- [deriveStatusBucket](frontend/src/components/videos/StatusPill.tsx:41) —— 重写。返回 `{bucket, x, n}`，其中 X 按桶语义取（in_progress 取 inflight 数；handled 取 terminal 数；pending 不用）
- [StatusPill](frontend/src/components/videos/StatusPill.tsx:71) —— 渲染逻辑加 `X/N` 文字。`待处理` 隐藏比例
- [FilterChip](frontend/src/components/videos/FilterChip.tsx) —— 3 个 tone，去掉 partial/failure/success 的活跃/非活跃样式
- [useVideoFilter](frontend/src/lib/useVideoFilter.ts:5) `VideoStatusFilter` —— 3 个值
- [useVideoPages](frontend/src/lib/useVideoPages.ts:26) `summary_counts` 类型 —— 3 个字段
- [Videos.tsx](frontend/src/pages/Videos.tsx:411) 过滤 chip —— 从 6 个（含"全部"）减到 4 个
- i18n [zh.json](frontend/src/i18n/locales/zh.json) / [en.json](frontend/src/i18n/locales/en.json) — `videos.bucket.*` 改 key 名 + 中英文

### 测试

- 删 / 改：
  - `test_filter_by_partial_bucket` → 改名 `test_filter_by_handled_bucket`，断言对所有"已处理"组合都返回
  - `test_failure_plus_pending_is_in_progress` → 改名 `test_failure_plus_pending_is_handled`，断言用户截图场景落 handled
  - `test_partial_bucket_covers_success_plus_pending` → 改名 `test_handled_bucket_covers_success_plus_pending`
  - `test_summary_counts_buckets_are_exhaustive` → 5 桶改 3 桶断言
  - `test_filter_by_in_progress_bucket` → 继续测，文案微调
  - `test_videos_list_returns_summary_counts` → 期望的 keys 改成 3 个
- 新增：
  - `test_handled_bucket_excludes_inflight`：1 SUCCESS + 1 INITIATED → in_progress, NOT handled
  - `test_list_row_returns_bucket_x_n`：响应每行带 `bucket`, `bucket_x`, `bucket_n`

## 兼容性 & 迁移

- **DB schema 不变**：publications.result 枚举不动，无 Alembic 迁移
- **API 兼容性**：`?status=success|partial|failure` 直接 400，不向后兼容。这是内部工具，用户群仅 1 人
- **前端缓存**：本地 SPA 通过 vite 构建，URL hash 自动失效

## 实施顺序（writing-plans 阶段细化）

1. 后端 SQL + summary_counts + 列表接口加 bucket_x/n 字段 + status 参数校验
2. 后端测试改 + 跑通
3. 前端类型 + StatusPill + FilterChip + Videos.tsx + i18n
4. 前端 build + tsc
5. 整体提交 + 部署
