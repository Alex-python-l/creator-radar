# 视频状态分类 3 桶重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/videos` 列表状态徽章和过滤 chip 从 5 桶（success/in_progress/partial/failure/pending）压缩到 3 桶（pending/in_progress/handled），徽章右侧附 `X/N` 比例显示进度。

**Architecture:** 后端 SQL 重写 `_status_having_sql` 和 `summary_counts_for` 输出 3 桶；前端 `deriveStatusBucket` 改为返回 `{bucket, x, n}`，`StatusPill` 渲染加 X/N 文字，FilterChip / Videos.tsx 削减到 4 个 chip（全部 + 3 桶）。所有改动单一 source of truth：后端只算桶分类和总数，前端只 derive 单条视频的桶 + X/N。

**Tech Stack:** PostgreSQL 16 + SQLModel + FastAPI；React 19 + Vite + TypeScript + i18next。

**Spec:** [docs/superpowers/specs/2026-06-09-video-status-3-buckets-design.md](docs/superpowers/specs/2026-06-09-video-status-3-buckets-design.md)

---

## Task 1: 后端测试改写（RED 阶段）

把现有桶相关测试改成断言新 3 桶语义，确认它们在旧代码下失败。

**Files:**
- Modify: `tests/test_api.py`

- [ ] **Step 1.1: 改写 `test_summary_counts_buckets_are_exhaustive`**

替换为新的 3 桶断言，多加一个 FAILURE+PENDING 案例确保它落 handled。

```python
def test_summary_counts_buckets_are_exhaustive(client, db):
    """Seed one video per bucket, then assert every bucket count is
    correct and that the three buckets sum to total. Mutually exclusive
    + exhaustive is the whole point of the new shape."""
    # handled: all SUCCESS
    _seed_video_with_pubs(db, "all-ok.mp4",
                          [("tiktok", "SUCCESS"), ("youtube", "SUCCESS")])
    # in_progress: any INITIATED/QUEUED
    _seed_video_with_pubs(db, "running.mp4",
                          [("tiktok", "INITIATED"), ("youtube", "SUCCESS")])
    # handled: SUCCESS + FAILURE mix
    _seed_video_with_pubs(db, "mixed.mp4",
                          [("tiktok", "SUCCESS"), ("youtube", "FAILURE")])
    # handled: all FAILURE
    _seed_video_with_pubs(db, "all-fail.mp4",
                          [("tiktok", "FAILURE"), ("youtube", "FAILURE")])
    # handled: FAILURE + PENDING (user's screenshot case)
    _seed_video_with_pubs(db, "fail-plus-pending.mp4",
                          [("tiktok", "FAILURE"), ("youtube", "PENDING")])
    # pending: only PENDING
    _seed_video_with_pubs(db, "pending.mp4",
                          [("tiktok", "PENDING")])
    # pending: no publications at all
    from app.models import Video
    v_empty = Video(original_filename="no-pubs.mp4",
                    stored_path="/tmp/no-pubs.mp4")
    db.add(v_empty)
    db.commit()

    client.post("/api/login", json={"password": "pw"})
    body = client.get("/api/videos?group_id=all&status=all").json()
    sc = body["summary_counts"]
    assert sc["total"] == 7
    assert sc["pending"] == 2
    assert sc["in_progress"] == 1
    assert sc["handled"] == 4  # all-ok + mixed + all-fail + fail-plus-pending
    assert sc["pending"] + sc["in_progress"] + sc["handled"] == sc["total"]
```

- [ ] **Step 1.2: 改写 `test_videos_list_returns_summary_counts`**

期望的 keys 改成 3 个。

```python
def test_videos_list_returns_summary_counts(client, sample_video):
    client.post("/api/login", json={"password": "pw"})
    r = client.get("/api/videos?group=all&status=all")
    body = r.json()
    assert "summary_counts" in body
    sc = body["summary_counts"]
    assert sc.keys() >= {"total", "pending", "in_progress", "handled"}
    assert all(isinstance(v, int) for v in sc.values())
```

- [ ] **Step 1.3: 改写 `test_filter_by_in_progress_bucket`**

进行中桶语义不变（仍是真在跑 GeeLark）。微调文案。

```python
def test_filter_by_in_progress_bucket(client, db):
    """The in_progress filter returns videos with ≥1 INITIATED or
    QUEUED publication — i.e., a phone is actively running a GeeLark
    task right now. PENDING is NOT in_progress; it's waiting to be
    picked up by the scheduler."""
    v_run = _seed_video_with_pubs(db, "running.mp4",
                                  [("tiktok", "INITIATED"),
                                   ("youtube", "SUCCESS")])
    v_qd = _seed_video_with_pubs(db, "queued.mp4",
                                 [("tiktok", "QUEUED")])
    # Not in_progress under 3-bucket model:
    _seed_video_with_pubs(db, "done.mp4", [("tiktok", "SUCCESS")])
    _seed_video_with_pubs(db, "failed.mp4", [("tiktok", "FAILURE")])
    _seed_video_with_pubs(db, "pending.mp4", [("tiktok", "PENDING")])
    _seed_video_with_pubs(db, "fail-plus-pending.mp4",
                          [("tiktok", "FAILURE"), ("youtube", "PENDING")])

    client.post("/api/login", json={"password": "pw"})
    body = client.get("/api/videos?group_id=all&status=in_progress").json()
    ids = {it["id"] for it in body["items"]}
    assert ids == {v_run.id, v_qd.id}
    for it in body["items"]:
        results = {p["result"] for p in it["platforms"].values()}
        assert results & {"INITIATED", "QUEUED"}, it
```

- [ ] **Step 1.4: 替换 `test_filter_by_partial_bucket` → `test_filter_by_handled_bucket`**

合并掉原 `test_partial_bucket_covers_success_plus_pending` 和 `test_failure_plus_pending_is_in_progress`。删原两个，新建这一个。

```python
def test_filter_by_handled_bucket(client, db):
    """The handled filter returns videos with no in-flight publications
    AND at least one SUCCESS or FAILURE. This covers:
      - all SUCCESS
      - all FAILURE
      - SUCCESS + FAILURE mix
      - SUCCESS + PENDING mix
      - FAILURE + PENDING mix (the user's "2 fail + 2 待发布" case)
    Anything with ≥1 INITIATED/QUEUED belongs to in_progress, not handled."""
    v_all_ok = _seed_video_with_pubs(db, "all-ok.mp4",
                                     [("tiktok", "SUCCESS")])
    v_all_fail = _seed_video_with_pubs(db, "all-fail.mp4",
                                       [("tiktok", "FAILURE")])
    v_mix = _seed_video_with_pubs(db, "mixed.mp4",
                                  [("tiktok", "SUCCESS"),
                                   ("youtube", "FAILURE")])
    v_succ_pend = _seed_video_with_pubs(db, "succ-plus-pend.mp4",
                                        [("tiktok", "SUCCESS"),
                                         ("youtube", "PENDING")])
    v_fail_pend = _seed_video_with_pubs(db, "fail-plus-pend.mp4",
                                        [("tiktok", "FAILURE"),
                                         ("youtube", "PENDING")])
    # Should NOT appear in handled:
    _seed_video_with_pubs(db, "running.mp4",
                          [("tiktok", "INITIATED"), ("youtube", "SUCCESS")])
    _seed_video_with_pubs(db, "all-pending.mp4",
                          [("tiktok", "PENDING")])

    client.post("/api/login", json={"password": "pw"})
    body = client.get("/api/videos?group_id=all&status=handled").json()
    ids = {it["id"] for it in body["items"]}
    assert ids == {v_all_ok.id, v_all_fail.id, v_mix.id,
                   v_succ_pend.id, v_fail_pend.id}
```

- [ ] **Step 1.5: 删除已并入的两个测试**

删 `test_partial_bucket_covers_success_plus_pending` 和 `test_failure_plus_pending_is_in_progress`。它们的覆盖被 `test_filter_by_handled_bucket` 完全包含。

- [ ] **Step 1.6: 跑测试确认 RED**

```bash
uv run pytest tests/test_api.py -q -k "bucket or summary_counts" 2>&1 | tail -20
```

Expected: 几个 FAIL（因为 SQL 还没改）。

- [ ] **Step 1.7: 不要提交**

测试改完后保持 unstaged，等 Task 2 SQL 改完一起提交。

---

## Task 2: 后端 SQL + API validation 重写（GREEN 阶段）

**Files:**
- Modify: `app/repositories/videos.py` (`_status_having_sql`, `summary_counts_for`)
- Modify: `app/api/videos.py` (`_STATUS_FILTER_VALUES`)

- [ ] **Step 2.1: 重写 `_status_having_sql`**

替换整个函数：

```python
def _status_having_sql(status: str) -> str:
    """Returns the HAVING fragment (or 'TRUE') for the 3-bucket model.

    Supported buckets (mutually exclusive, exhaustive over all videos):

    - ``all``         — no filter
    - ``pending``     — no publications OR every publication is PENDING
    - ``in_progress`` — ≥1 publication is INITIATED or QUEUED
    - ``handled``     — no in-flight AND ≥1 publication is SUCCESS or FAILURE
    """
    if status == "all":
        return "TRUE"
    if status == "pending":
        return ("COUNT(p.id) = 0 OR ("
                "SUM(CASE WHEN p.result IN ('INITIATED','QUEUED') "
                "THEN 1 ELSE 0 END) = 0 AND "
                "SUM(CASE WHEN p.result = 'SUCCESS' THEN 1 ELSE 0 END) = 0 AND "
                "SUM(CASE WHEN p.result = 'FAILURE' THEN 1 ELSE 0 END) = 0)")
    if status == "in_progress":
        return ("COUNT(p.id) > 0 AND "
                "SUM(CASE WHEN p.result IN ('INITIATED','QUEUED') "
                "THEN 1 ELSE 0 END) > 0")
    if status == "handled":
        return ("COUNT(p.id) > 0 AND "
                "SUM(CASE WHEN p.result IN ('INITIATED','QUEUED') "
                "THEN 1 ELSE 0 END) = 0 AND "
                "(SUM(CASE WHEN p.result = 'SUCCESS' THEN 1 ELSE 0 END) > 0 "
                " OR SUM(CASE WHEN p.result = 'FAILURE' THEN 1 ELSE 0 END) > 0)")
    raise ValueError(f"unknown status: {status!r}")
```

- [ ] **Step 2.2: 重写 `summary_counts_for`**

替换整个函数：

```python
def summary_counts_for(
    session: Session, *, group_id: int | str,
) -> dict:
    """Counts for the page-level summary line — three mutually exclusive
    buckets that always sum to ``total``.

    total       – number of videos in the current group
    pending     – no publications OR every publication is PENDING
    in_progress – ≥1 publication is INITIATED or QUEUED
    handled     – no in-flight AND ≥1 SUCCESS or FAILURE
    """
    gfrag, gparams = _group_filter_sql(group_id)
    sql = f"""
        WITH v_pub AS (
          SELECT v.id,
                 COUNT(p.id) AS n,
                 SUM(CASE WHEN p.result = 'SUCCESS'
                          THEN 1 ELSE 0 END) AS ok,
                 SUM(CASE WHEN p.result = 'FAILURE'
                          THEN 1 ELSE 0 END) AS fail,
                 SUM(CASE WHEN p.result IN ('INITIATED','QUEUED')
                          THEN 1 ELSE 0 END) AS in_flight
            FROM videos v
            LEFT JOIN publications p ON p.video_id = v.id
            WHERE {gfrag}
            GROUP BY v.id
        )
        SELECT
          COUNT(*)                                                              AS total,
          SUM(CASE WHEN n = 0 OR (in_flight = 0 AND ok = 0 AND fail = 0)
                   THEN 1 ELSE 0 END)                                           AS pending,
          SUM(CASE WHEN n > 0 AND in_flight > 0
                   THEN 1 ELSE 0 END)                                           AS in_progress,
          SUM(CASE WHEN n > 0 AND in_flight = 0 AND (ok > 0 OR fail > 0)
                   THEN 1 ELSE 0 END)                                           AS handled
        FROM v_pub
    """
    row = session.execute(sa.text(sql), gparams).first()
    if row is None:
        return {"total": 0, "pending": 0, "in_progress": 0, "handled": 0}
    return {
        "total":       int(row[0] or 0),
        "pending":     int(row[1] or 0),
        "in_progress": int(row[2] or 0),
        "handled":     int(row[3] or 0),
    }
```

- [ ] **Step 2.3: 更新 API status filter 允许值**

修改 `app/api/videos.py` 中 `_STATUS_FILTER_VALUES`：

```python
_STATUS_FILTER_VALUES = (
    "all", "pending", "in_progress", "handled",
)
```

- [ ] **Step 2.4: 跑桶相关测试确认 GREEN**

```bash
uv run pytest tests/test_api.py -q -k "bucket or summary_counts" 2>&1 | tail -10
```

Expected: 全部 PASS。

- [ ] **Step 2.5: 跑全套测试确认无回归**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: `583 passed`（本次净删 2 个测试：partial_covers_success_plus_pending、failure_plus_pending_is_in_progress；合并入 `test_filter_by_handled_bucket`）。

- [ ] **Step 2.6: 提交 Task 1+2**

```bash
git add tests/test_api.py app/repositories/videos.py app/api/videos.py
git commit -m "$(cat <<'EOF'
refactor(videos): collapse status to 3 buckets (pending/in_progress/handled)

Replaces the 5-bucket model (success/in_progress/partial/failure/pending)
with 3 buckets:
  - pending: no publications or all PENDING
  - in_progress: ≥1 INITIATED/QUEUED (GeeLark actually running)
  - handled: no in-flight AND ≥1 SUCCESS or FAILURE

The user's screenshot case (2 FAILURE + 2 PENDING) now correctly lands
in handled instead of being mislabeled "部分完成". Frontend wiring
follows in the next commit.
EOF
)"
```

---

## Task 3: 前端类型 + deriveStatusBucket + StatusPill 渲染

**Files:**
- Modify: `frontend/src/components/videos/StatusPill.tsx`
- Modify: `frontend/src/components/videos/VideoCard.tsx`
- Modify: `frontend/src/components/videos/VideoMediaRow.tsx`
- Modify: `frontend/src/lib/useVideoFilter.ts`
- Modify: `frontend/src/lib/useVideoPages.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 3.1: 重写 `StatusPill.tsx`**

整个文件替换：

```tsx
import { useTranslation } from 'react-i18next'
import type { VideoListRow } from '../../lib/useVideoPages'
import { PLATFORMS } from '../../api'
import { cn } from '../../lib/cn'

export type StatusBucket = 'pending' | 'in_progress' | 'handled'

export type BucketWithCount = {
  bucket: StatusBucket
  x: number  // count of pubs contributing to this bucket (inflight or terminal)
  n: number  // total publications on the video
}

const BUCKET_BG: Record<StatusBucket, string> = {
  pending:     'bg-[var(--color-line-strong)]',
  in_progress: 'bg-[var(--color-blue)]',
  handled:     'bg-[var(--color-ink-3)]',
}

const BUCKET_DOT: Record<StatusBucket, string> = {
  pending:     'bg-[var(--color-ink-5)]',
  in_progress: 'bg-[var(--color-blue)]',
  handled:     'bg-[var(--color-ink-3)]',
}

const BUCKET_GLYPH: Record<StatusBucket, string> = {
  pending:     '○',
  in_progress: '⟳',
  handled:     '✓',
}

const BUCKET_LABEL_ZH: Record<StatusBucket, string> = {
  pending:     '待处理',
  in_progress: '进行中',
  handled:     '已处理',
}

export function deriveStatusBucket(
  platforms: VideoListRow['platforms'],
): BucketWithCount {
  let n = 0
  let ok = 0
  let fail = 0
  let inFlight = 0
  for (const plat of PLATFORMS) {
    const r = platforms[plat]?.result
    if (!r) continue
    n += 1
    if (r === 'SUCCESS') ok += 1
    else if (r === 'FAILURE') fail += 1
    else if (r === 'INITIATED' || r === 'QUEUED') inFlight += 1
  }
  if (inFlight > 0) return { bucket: 'in_progress', x: inFlight, n }
  if (ok + fail > 0) return { bucket: 'handled', x: ok + fail, n }
  return { bucket: 'pending', x: 0, n }
}

type Variant = 'row' | 'overlay'

type Props = {
  bucket: StatusBucket
  x: number
  n: number
  variant: Variant
}

export function StatusPill({ bucket, x, n, variant }: Props) {
  const { t, i18n } = useTranslation()
  const zh = (i18n.resolvedLanguage ?? i18n.language ?? 'en').startsWith('zh')
  const label = zh
    ? BUCKET_LABEL_ZH[bucket]
    : t(`videos.bucket.${bucket}`, { defaultValue: BUCKET_LABEL_ZH[bucket] })
  const glyph = BUCKET_GLYPH[bucket]
  const bg = BUCKET_BG[bucket]
  // Pending shows no ratio (would always be 0/N which adds noise).
  const showRatio = bucket !== 'pending' && n > 0

  if (variant === 'overlay') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 h-[22px] px-2 rounded-[6px]',
          'text-[11px] font-semibold text-white tabular-nums',
          'shadow-[0_2px_6px_rgba(0,0,0,0.25)]',
          bg,
        )}
        aria-label={label}
      >
        <span className="text-[12px] leading-none">{glyph}</span>
        <span>{label}</span>
        {showRatio && <span className="opacity-90">{x}/{n}</span>}
      </span>
    )
  }
  return (
    <span
      className={cn(
        'inline-flex items-center justify-center gap-1.5 h-7 w-[100px]',
        'rounded-[6px] text-[11.5px] font-semibold text-white tabular-nums',
        bg,
      )}
      aria-label={label}
    >
      <span className="text-[12px] leading-none">{glyph}</span>
      <span>{label}</span>
      {showRatio && <span className="opacity-90">{x}/{n}</span>}
    </span>
  )
}

export { BUCKET_DOT, BUCKET_LABEL_ZH }
```

注意：row 变体的宽度从 `w-[88px]` 改成 `w-[100px]` 容纳额外的 X/N 文字。

- [ ] **Step 3.2: 更新 VideoCard.tsx 调用**

把：

```tsx
<StatusPill
  bucket={deriveStatusBucket(row.platforms)}
  variant="overlay"
/>
```

改成：

```tsx
{(() => {
  const d = deriveStatusBucket(row.platforms)
  return (
    <StatusPill
      bucket={d.bucket}
      x={d.x}
      n={d.n}
      variant="overlay"
    />
  )
})()}
```

- [ ] **Step 3.3: 更新 VideoMediaRow.tsx 调用**

把：

```tsx
<StatusPill bucket={deriveStatusBucket(row.platforms)} variant="row" />
```

改成：

```tsx
{(() => {
  const d = deriveStatusBucket(row.platforms)
  return <StatusPill bucket={d.bucket} x={d.x} n={d.n} variant="row" />
})()}
```

- [ ] **Step 3.4: 更新 `useVideoFilter.ts` 类型**

```ts
export type VideoStatusFilter = 'all' | 'pending' | 'in_progress' | 'handled'
```

更新 `parseStatus` 也只接 3 个值：

```ts
function parseStatus(raw: string | null): VideoStatusFilter {
  if (
    raw === 'pending' ||
    raw === 'in_progress' ||
    raw === 'handled'
  ) return raw
  return 'all'
}
```

- [ ] **Step 3.5: 更新 `useVideoPages.ts` `summary_counts` 类型**

```ts
summary_counts: {
  total: number
  pending: number
  in_progress: number
  handled: number
} | null
```

- [ ] **Step 3.6: 更新 `api.ts` videos 参数的 status 联合类型**

在 `frontend/src/api.ts` 的 `videos:` 方法签名里，把：

```ts
status?:
  | 'all' | 'success' | 'in_progress'
  | 'partial' | 'failure' | 'pending'
```

改成：

```ts
status?: 'all' | 'pending' | 'in_progress' | 'handled'
```

- [ ] **Step 3.7: 跑 tsc 验证类型一致**

```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npx tsc -b 2>&1 | tail -20
```

Expected: 无错误。如果有错误，多半是 Videos.tsx 还在用 success/partial/failure，下个 Task 处理。

- [ ] **Step 3.8: 不要提交**

留待 Task 4 一起提交。

---

## Task 4: 前端 FilterChip + Videos.tsx 过滤区 + i18n

**Files:**
- Modify: `frontend/src/components/videos/FilterChip.tsx`
- Modify: `frontend/src/pages/Videos.tsx`
- Modify: `frontend/src/i18n/locales/zh.json`
- Modify: `frontend/src/i18n/locales/en.json`

- [ ] **Step 4.1: 简化 `FilterChip.tsx` 的 tone 类型**

```ts
export type ChipTone =
  | 'neutral' | 'pending' | 'in_progress' | 'handled'

const TONE_INACTIVE: Record<ChipTone, string> = {
  neutral:     'bg-[var(--color-gray-bg)] text-[var(--color-ink-2)] hover:bg-[#e6e6ea]',
  pending:     'bg-[var(--color-gray-bg)] text-[var(--color-ink-3)] hover:bg-[#e6e6ea]',
  in_progress: 'bg-[#e7f1ff] text-[var(--color-blue)] hover:bg-[#d6e6ff]',
  handled:     'bg-[#ececef] text-[var(--color-ink-2)] hover:bg-[#dfdfe3]',
}

const TONE_ACTIVE: Record<ChipTone, string> = {
  neutral:     'bg-[var(--color-blue)] text-white',
  pending:     'bg-[var(--color-ink-4)] text-white',
  in_progress: 'bg-[var(--color-blue)] text-white',
  handled:     'bg-[var(--color-ink-3)] text-white',
}
```

`partial` / `failure` / `success` 行全删。

- [ ] **Step 4.2: 重写 Videos.tsx 过滤 chip 区**

在 `frontend/src/pages/Videos.tsx` 找到 `filter.status === 'all'` 那个 chip 起始位置，把整段 chip 列表替换成 4 个：

```tsx
<FilterChip
  active={filter.status === 'all'}
  tone="neutral"
  onClick={() => setFilter({ status: 'all' })}
  label={t('videos.bucket.all', { defaultValue: '全部' })}
  count={summaryCounts?.total}
/>
<FilterChip
  active={filter.status === 'pending'}
  tone="pending"
  onClick={() => setFilter({ status: 'pending' })}
  glyph="○"
  label={t('videos.bucket.pending', { defaultValue: '待处理' })}
  count={summaryCounts?.pending}
/>
<FilterChip
  active={filter.status === 'in_progress'}
  tone="in_progress"
  onClick={() => setFilter({ status: 'in_progress' })}
  glyph="⟳"
  label={t('videos.bucket.in_progress', { defaultValue: '进行中' })}
  count={summaryCounts?.in_progress}
/>
<FilterChip
  active={filter.status === 'handled'}
  tone="handled"
  onClick={() => setFilter({ status: 'handled' })}
  glyph="✓"
  label={t('videos.bucket.handled', { defaultValue: '已处理' })}
  count={summaryCounts?.handled}
/>
```

- [ ] **Step 4.3: 更新 i18n zh.json bucket 部分**

```json
"bucket": {
  "all": "全部",
  "pending": "待处理",
  "in_progress": "进行中",
  "handled": "已处理"
},
```

- [ ] **Step 4.4: 更新 i18n en.json bucket 部分**

```json
"bucket": {
  "all": "All",
  "pending": "Pending",
  "in_progress": "In progress",
  "handled": "Handled"
},
```

- [ ] **Step 4.5: 检查 summaryLine 是否引用了已删 key**

在 `Videos.tsx` 和 i18n 文件搜索 `success` / `partial` / `failure` 引用，确认 `summaryLine` 用的字段在 summary_counts 里仍存在。

```bash
grep -n "success\|partial\|failure" frontend/src/pages/Videos.tsx frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
```

如果 zh.json/en.json 的 `summaryLine` 还有 `{{success}}` 占位符（旧 5 桶时代），改成：

```json
"summaryLine": "共 {{total}} 个 · 已处理 {{handled}} · 待处理 {{pending}}"
```

en.json：

```json
"summaryLine": "{{total}} total · {{handled}} handled · {{pending}} pending"
```

如果 Videos.tsx 调用 `t('videos.summaryLine', {...})` 时传了 success 字段，改成传 handled。

- [ ] **Step 4.6: 跑 tsc + vite build**

```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build 2>&1 | tail -10
```

Expected: `built in <ms>` 无错。

- [ ] **Step 4.7: 提交 Task 3+4**

```bash
# frontend/dist is gitignored — not committed; rebuilt at deploy time
git add frontend/src/components/videos/StatusPill.tsx \
        frontend/src/components/videos/FilterChip.tsx \
        frontend/src/components/videos/VideoCard.tsx \
        frontend/src/components/videos/VideoMediaRow.tsx \
        frontend/src/lib/useVideoFilter.ts \
        frontend/src/lib/useVideoPages.ts \
        frontend/src/api.ts \
        frontend/src/pages/Videos.tsx \
        frontend/src/i18n/locales/zh.json \
        frontend/src/i18n/locales/en.json
git commit -m "$(cat <<'EOF'
feat(videos): render 3-bucket status with X/N counter

Frontend wiring for the 3-bucket model. StatusPill now shows e.g.
"⟳ 进行中 1/4" or "✓ 已处理 2/4"; pending stays glyph + label only.
Filter chips drop from 6 (incl. success/partial/failure) to 4 (all +
pending/in_progress/handled). i18n labels updated.
EOF
)"
```

---

## Task 5: 本地端到端校验 + 部署

**Files:** none (verification + deploy)

- [ ] **Step 5.1: 跑全套 pytest**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: 全 passed。

- [ ] **Step 5.2: 重新 build 前端**

```bash
export PATH="/opt/homebrew/bin:$PATH" && cd frontend && npm run build 2>&1 | tail -5
```

Expected: `built in <ms>`，dist 文件生成。

- [ ] **Step 5.3: 打包**

```bash
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-deploy.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini geelark_client.py generate_caption.py generate_comment.py \
  pyproject.toml uv.lock frontend/dist
ls -lh /tmp/mkt-deploy.tar.gz
```

Expected: tarball 约 1.7M。

- [ ] **Step 5.4: 上传 + 解压 + 重启**

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-deploy.tar.gz root@139.196.52.92:/tmp/mkt-deploy.tar.gz

ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/root/.local/bin:$PATH"
  cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini frontend/dist geelark_client.py generate_caption.py generate_comment.py pyproject.toml uv.lock
  uv sync --no-dev
  uv run alembic upgrade head
  systemctl restart mkt mkt-worker
  sleep 5
  systemctl is-active mkt mkt-worker
  rm -f /tmp/mkt-deploy.tar.gz
'
```

Expected: 两行 `active`。无 alembic 迁移要跑（本次无 schema 变更）。

- [ ] **Step 5.5: 烟测**

```bash
curl -sS -o /dev/null -w "/ -> %{http_code}\n" http://139.196.52.92:8080/
curl -sS -o /dev/null -w "/api/accounts -> %{http_code}\n" http://139.196.52.92:8080/api/accounts
```

Expected: `/` 200, `/api/accounts` 401。

- [ ] **Step 5.6: 用户去刷新 dashboard 确认**

打开 `http://139.196.52.92:8080/videos`，确认：
- 过滤区只剩 4 个 chip（全部 / 待处理 / 进行中 / 已处理）
- 之前截图那条 `03_19_final_two_minutes_transparent10s.mp4` 的徽章变成 `✓ 已处理 2/4`（蓝灰色，不再是橙色"部分完成"）
- 切换 `?status=handled` 过滤能看到那条
- 切换 `?status=in_progress` 看不到那条

---

## 完成判定

- 所有 5 个 Task 的 checkbox 全部勾上
- pytest 全套 PASS
- 前端 tsc + vite 无错
- prod `/` 200，`/api/accounts` 401
- dashboard 实际看到 3 桶 + X/N 比例
