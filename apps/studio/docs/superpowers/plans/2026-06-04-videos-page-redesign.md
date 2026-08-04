# Videos 页表格重做 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/videos` 页表格按 [spec](../specs/2026-06-04-videos-page-redesign-design.md) 重做：补表头、去死高、视觉密集化（状态色条 + Dot + 平台褪色/红点 + 同日 continuation），并把 filter/展开/滚动持久化到 URL + sessionStorage。

**Architecture:** 三个新 hook 隔离持久化（`useVideoFilter` / `useExpandedVideos` / `useScrollRestore`），UI 改在 `VideoList` + `VideoRow` + `Videos.tsx` 三个文件，`useVideoPages` 与后端 API 不动。

**Tech Stack:** React 19、TypeScript、Tailwind v4（CSS 变量在 [index.css](../../../frontend/src/index.css)）、`@tanstack/react-virtual`、`react-router-dom` v7（`useSearchParams`）。

**Test 策略：** Frontend 无 unit test 框架。每个 task 收尾：
1. `cd frontend && npm run build`（tsc -b 含类型检查 + vite build）
2. 用 preview 工具在浏览器里验证视觉/行为
3. （可选）每 task `git commit`，由执行阶段决定（用户可能要求"全部做完再一次性 commit + 部署"）

---

## File Structure

**新增**

| 文件 | 责任 |
|------|------|
| `frontend/src/lib/useVideoFilter.ts` | URL searchParams ↔ `{group, status}` |
| `frontend/src/lib/useExpandedVideos.ts` | sessionStorage 分桶存展开 id |
| `frontend/src/lib/useScrollRestore.ts` | sessionStorage 分桶存 window.scrollY |

**修改**

| 文件 | 修改 |
|------|------|
| `frontend/src/pages/Videos.tsx` | 接入 3 个新 hook、加 status chips、批量栏改 sticky、删 useState filter |
| `frontend/src/components/videos/VideoList.tsx` | sticky thead、去 70vh、10 列 grid、行高 44 |
| `frontend/src/components/videos/VideoRow.tsx` | 状态色条、Dot 状态、hover 操作、内联日期 continuation、平台 wrapper |

**删除**

| 文件 | 原因 |
|------|------|
| `frontend/src/components/videos/VideoDateCell.tsx` | 逻辑挪 VideoRow 内联，减少一层 |

---

### Task 1: useVideoFilter hook

**Files:**
- Create: `frontend/src/lib/useVideoFilter.ts`

- [ ] **Step 1: 写 hook 代码**

把 `VideoFilter` 类型从 [useVideoPages.ts:22-25](../../../frontend/src/lib/useVideoPages.ts) 移过来共享。新文件：

```typescript
// frontend/src/lib/useVideoFilter.ts
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

export type GroupSel = number | 'all' | 'ungrouped'
export type StatusSel = 'all' | 'pending' | 'terminal'
export type VideoFilter = { group_id: GroupSel; status: StatusSel }

function parseGroup(raw: string | null): GroupSel {
  if (raw == null || raw === 'all') return 'all'
  if (raw === 'ungrouped') return 'ungrouped'
  const n = parseInt(raw, 10)
  return Number.isFinite(n) ? n : 'all'
}

function parseStatus(raw: string | null): StatusSel {
  if (raw === 'pending' || raw === 'terminal') return raw
  return 'all'
}

export function useVideoFilter() {
  const [sp, setSp] = useSearchParams()
  const filter: VideoFilter = useMemo(
    () => ({
      group_id: parseGroup(sp.get('group')),
      status: parseStatus(sp.get('status')),
    }),
    [sp],
  )
  const filterKey = `${filter.group_id}|${filter.status}`

  const setFilter = useCallback(
    (next: Partial<VideoFilter>) => {
      setSp(
        (prev) => {
          const out = new URLSearchParams(prev)
          const g = next.group_id ?? filter.group_id
          const s = next.status ?? filter.status
          if (g === 'all') out.delete('group')
          else out.set('group', String(g))
          if (s === 'all') out.delete('status')
          else out.set('status', s)
          return out
        },
        { replace: true },
      )
    },
    [setSp, filter.group_id, filter.status],
  )

  return { filter, filterKey, setFilter }
}
```

- [ ] **Step 2: 把 VideoFilter 类型从 useVideoPages.ts 改成从这里 re-export**

Modify: `frontend/src/lib/useVideoPages.ts:22-25`

Replace:

```typescript
export type VideoFilter = {
  group_id: number | 'all' | 'ungrouped'
  status: 'all' | 'pending' | 'terminal'
}
```

With:

```typescript
export type { VideoFilter } from './useVideoFilter'
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npm run build`
Expected: 通过，无 type error。

- [ ] **Step 4 (可选): commit**

```bash
git add frontend/src/lib/useVideoFilter.ts frontend/src/lib/useVideoPages.ts
git commit -m "feat(videos): add useVideoFilter hook backed by URL searchParams"
```

---

### Task 2: useExpandedVideos hook

**Files:**
- Create: `frontend/src/lib/useExpandedVideos.ts`

- [ ] **Step 1: 写 hook 代码**

```typescript
// frontend/src/lib/useExpandedVideos.ts
import { useCallback, useEffect, useRef, useState } from 'react'

const KEY = (fk: string) => `videos:expanded:${fk}`

function loadFromStorage(filterKey: string): Set<number> {
  try {
    const raw = sessionStorage.getItem(KEY(filterKey))
    if (!raw) return new Set()
    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) return new Set()
    return new Set(arr.filter((n): n is number => typeof n === 'number'))
  } catch {
    return new Set()
  }
}

function saveToStorage(filterKey: string, ids: Set<number>) {
  try {
    sessionStorage.setItem(KEY(filterKey), JSON.stringify(Array.from(ids)))
  } catch {
    // ignored — quota / privacy mode
  }
}

export function useExpandedVideos(filterKey: string) {
  const [ids, setIds] = useState<Set<number>>(() => loadFromStorage(filterKey))
  const filterKeyRef = useRef(filterKey)

  // 切换 filterKey: 保存当前桶，加载新桶
  useEffect(() => {
    if (filterKeyRef.current === filterKey) return
    saveToStorage(filterKeyRef.current, ids)
    filterKeyRef.current = filterKey
    setIds(loadFromStorage(filterKey))
    // 依赖只看 filterKey；ids 写桶用 ref 里的旧 key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey])

  // 卸载 / 刷新前持久化
  useEffect(() => {
    const onBeforeUnload = () => saveToStorage(filterKeyRef.current, ids)
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      saveToStorage(filterKeyRef.current, ids)
    }
  }, [ids])

  const toggle = useCallback((id: number) => {
    setIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  return { expandedIds: ids, toggle }
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npm run build`
Expected: 通过。

- [ ] **Step 3 (可选): commit**

```bash
git add frontend/src/lib/useExpandedVideos.ts
git commit -m "feat(videos): add useExpandedVideos hook with sessionStorage per filterKey"
```

---

### Task 3: useScrollRestore hook

**Files:**
- Create: `frontend/src/lib/useScrollRestore.ts`

- [ ] **Step 1: 写 hook 代码**

```typescript
// frontend/src/lib/useScrollRestore.ts
import { useEffect, useRef } from 'react'

const KEY = (fk: string) => `videos:scrollY:${fk}`

function loadScroll(filterKey: string): number | null {
  try {
    const raw = sessionStorage.getItem(KEY(filterKey))
    if (raw == null) return null
    const n = parseFloat(raw)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

function saveScroll(filterKey: string, y: number) {
  try {
    sessionStorage.setItem(KEY(filterKey), String(y))
  } catch {
    // ignored
  }
}

/**
 * 在第一页数据 ready 后恢复 scrollY；切 filterKey 时保存旧 key 并清掉本次恢复 latch。
 * pageReady: 第一页 items 长度 > 0 转 true（caller 传），用于决定恢复时机。
 */
export function useScrollRestore(filterKey: string, pageReady: boolean) {
  const filterKeyRef = useRef(filterKey)
  const restoredRef = useRef(false)

  // filterKey 切换：保存旧、重置 latch
  useEffect(() => {
    if (filterKeyRef.current === filterKey) return
    saveScroll(filterKeyRef.current, window.scrollY)
    filterKeyRef.current = filterKey
    restoredRef.current = false
  }, [filterKey])

  // 数据 ready 一次性恢复
  useEffect(() => {
    if (!pageReady || restoredRef.current) return
    const y = loadScroll(filterKey)
    if (y != null) window.scrollTo(0, y)
    restoredRef.current = true
  }, [filterKey, pageReady])

  // 滚动节流写盘 + unmount/unload 写盘
  useEffect(() => {
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        saveScroll(filterKeyRef.current, window.scrollY)
      })
    }
    const onBeforeUnload = () => saveScroll(filterKeyRef.current, window.scrollY)
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      if (raf) cancelAnimationFrame(raf)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('beforeunload', onBeforeUnload)
      saveScroll(filterKeyRef.current, window.scrollY)
    }
  }, [])
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npm run build`
Expected: 通过。

- [ ] **Step 3 (可选): commit**

```bash
git add frontend/src/lib/useScrollRestore.ts
git commit -m "feat(videos): add useScrollRestore hook for sessionStorage scrollY recovery"
```

---

### Task 4: VideoRow 重做

**Files:**
- Modify: `frontend/src/components/videos/VideoRow.tsx`（完全重写）

- [ ] **Step 1: 写新版 VideoRow**

完整替换文件内容：

```tsx
// frontend/src/components/videos/VideoRow.tsx
import { useTranslation } from 'react-i18next'
import type { VideoListRow } from '../../lib/useVideoPages'
import { PLATFORMS, type Platform } from '../../api'
import { PlatformIcon } from '../PlatformIcon'
import { PLATFORM_NAME } from '../../lib/platformNames'
import { cn } from '../../lib/cn'

const STATUS_BAR: Record<string, string> = {
  SUCCESS: 'bg-[var(--color-green)]',
  FAILURE: 'bg-[var(--color-red)]',
  INITIATED: 'bg-[var(--color-blue)]',
  QUEUED: 'bg-[var(--color-orange)]',
  PENDING: 'bg-[var(--color-line-strong)]',
}

const STATUS_DOT: Record<string, string> = {
  SUCCESS: 'bg-[var(--color-green)]',
  FAILURE: 'bg-[var(--color-red)]',
  INITIATED: 'bg-[var(--color-blue)]',
  QUEUED: 'bg-[var(--color-orange)]',
  PENDING: 'bg-[var(--color-ink-5)]',
}

function formatDateLabel(iso: string | null, locale: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const today = new Date()
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  const yest = new Date(today)
  yest.setDate(today.getDate() - 1)
  if (sameDay(d, today)) return locale.startsWith('zh') ? '今天' : 'Today'
  if (sameDay(d, yest)) return locale.startsWith('zh') ? '昨天' : 'Yesterday'
  return d.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

type Props = {
  row: VideoListRow
  groupsById: Map<number, string>
  isContinuation: boolean
  isExpanded: boolean
  isSelected: boolean
  onToggleSelect: (id: number) => void
  onToggleExpanded: (id: number) => void
  onDelete: (id: number) => void
}

export default function VideoRow({
  row, groupsById, isContinuation, isExpanded, isSelected,
  onToggleSelect, onToggleExpanded, onDelete,
}: Props) {
  const { t, i18n } = useTranslation()
  const locale = i18n.resolvedLanguage ?? i18n.language ?? 'en'
  const isPending = row.summary === 'PENDING'
  const resultKey = row.summary === 'PENDING' ? 'PENDING' : row.summary
  const resultLabel = t(`videos.result.${resultKey}`, { defaultValue: resultKey })

  const phoneLabel = row.phone_label ??
    (isPending && row.group_id == null
      ? (t('videos.pickGroupFirst') as string)
      : (t('videos.autoByPlatform') as string))

  const barClass =
    isPending && !row.phone_label
      ? 'bg-transparent'
      : STATUS_BAR[row.summary] ?? 'bg-transparent'

  return (
    <div
      role="row"
      className={cn(
        'group grid items-center gap-x-3 pr-3',
        // 10 columns: bar / date / check / name / group / platforms / status / phone / caption / actions
        'grid-cols-[3px_88px_28px_minmax(0,1fr)_120px_108px_88px_140px_minmax(0,1.1fr)_56px]',
        'border-b border-[var(--color-line)]',
        !isContinuation && 'border-t border-[var(--color-line)]',
        'h-11',
        isSelected
          ? 'bg-[var(--color-blue-bg)] hover:bg-[#e0ecfa]'
          : 'hover:bg-[var(--color-surface-hover)]',
        'transition-colors',
      )}
    >
      {/* 1: status bar */}
      <div className={cn('h-full w-[3px]', barClass)} />

      {/* 2: date */}
      <div className="text-right tabular-nums">
        {!isContinuation ? (
          <>
            <div className="text-[12.5px] font-medium text-[var(--color-ink-2)] leading-tight">
              {formatDateLabel(row.uploaded_at, locale)}
            </div>
            <div className="text-[11px] text-[var(--color-ink-4)] leading-tight mt-0.5">
              {formatTime(row.uploaded_at)}
            </div>
          </>
        ) : (
          <div className="text-[11px] text-[var(--color-ink-4)] leading-tight">
            {formatTime(row.uploaded_at)}
          </div>
        )}
      </div>

      {/* 3: checkbox */}
      <div className="flex justify-center">
        {isPending ? (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect(row.id)}
            aria-label={`select ${row.original_filename ?? row.id}`}
            className="w-3.5 h-3.5 accent-[var(--color-blue)]"
          />
        ) : (
          <span className="text-[var(--color-ink-5)]">—</span>
        )}
      </div>

      {/* 4: filename + expand */}
      <div className="flex items-center gap-2 min-w-0">
        <button
          type="button"
          onClick={() => onToggleExpanded(row.id)}
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
          {isExpanded ? '−' : '+'}
        </button>
        <span className="text-[13.5px] font-medium text-[var(--color-ink)] truncate min-w-0">
          {row.original_filename}
        </span>
      </div>

      {/* 5: group */}
      <div className="text-[12.5px] text-[var(--color-ink-2)] truncate">
        {row.group_id == null ? (
          <span className="text-[var(--color-ink-5)]">
            {t('videos.ungrouped')}
          </span>
        ) : (
          groupsById.get(row.group_id) ?? `#${row.group_id}`
        )}
      </div>

      {/* 6: platforms */}
      <div className="flex items-center justify-center gap-1.5">
        {PLATFORMS.map((plat: Platform) => {
          const p = row.platforms[plat]
          const muted = !p || (p.result === 'PENDING' && !p.has_phone)
          const failed = p?.result === 'FAILURE'
          const displayResult = p?.result ?? 'PENDING'
          return (
            <span
              key={plat}
              className={cn(
                'relative inline-flex w-[18px] h-[18px] items-center justify-center',
                muted && 'opacity-40 grayscale',
              )}
              title={`${PLATFORM_NAME[plat]} · ${t(`videos.result.${displayResult}`, {
                defaultValue: displayResult,
              })}`}
            >
              <PlatformIcon platform={plat} size={16} />
              {failed && (
                <span
                  className={cn(
                    'absolute -bottom-0.5 -right-0.5 w-[7px] h-[7px] rounded-full',
                    'bg-[var(--color-red)] ring-[1.5px] ring-white',
                  )}
                  aria-hidden
                />
              )}
            </span>
          )
        })}
      </div>

      {/* 7: status */}
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            'inline-block w-[7px] h-[7px] rounded-full',
            STATUS_DOT[resultKey] ?? STATUS_DOT.PENDING,
          )}
          aria-hidden
        />
        <span className="text-[12px] font-medium text-[var(--color-ink-2)]">
          {resultLabel}
        </span>
      </div>

      {/* 8: phone */}
      <div className="text-[12.5px] text-[var(--color-ink-2)] truncate">
        {phoneLabel}
      </div>

      {/* 9: caption */}
      <div className="text-[12.5px] text-[var(--color-ink-3)] truncate">
        {row.caption_preview ?? (
          <span className="text-[var(--color-ink-5)]">—</span>
        )}
      </div>

      {/* 10: actions */}
      <div className="flex justify-end">
        {isPending ? (
          <button
            onClick={() => onDelete(row.id)}
            className={cn(
              'text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)]',
              'opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-1',
            )}
          >
            {t('common.delete')}
          </button>
        ) : null}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npm run build`
Expected: 通过。可能因为 VideoDateCell 仍被引用而失败 — 暂时保留，Task 7 删除。

- [ ] **Step 3 (可选): commit**

```bash
git add frontend/src/components/videos/VideoRow.tsx
git commit -m "feat(videos): redesign VideoRow with status bar, dot status, hover delete"
```

---

### Task 5: VideoList 重做

**Files:**
- Modify: `frontend/src/components/videos/VideoList.tsx`（完全重写）

- [ ] **Step 1: 写新版 VideoList — sticky thead + 去 70vh + 自适应**

完整替换文件内容：

```tsx
// frontend/src/components/videos/VideoList.tsx
import { useEffect, useMemo, useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useTranslation } from 'react-i18next'
import type { VideoListRow } from '../../lib/useVideoPages'
import VideoRow from './VideoRow'
import VideoDetailPanel from './VideoDetailPanel'

type Row =
  | { kind: 'video'; video: VideoListRow; isContinuation: boolean }
  | { kind: 'detail'; videoId: number }
  | { kind: 'sentinel' }

type Props = {
  items: VideoListRow[]
  hasMore: boolean
  loading: boolean
  expandedIds: Set<number>
  selectedIds: Set<number>
  groupsById: Map<number, string>
  onToggleSelect: (id: number) => void
  onToggleExpanded: (id: number) => void
  onDelete: (id: number) => void
  onLoadMore: () => void
  onOpenFailureShot: (
    url: string, platform: string, detail: string) => void
}

function dateOnly(iso: string | null): string {
  return iso ? iso.slice(0, 10) : 'unknown'
}

// Grid template shared between header and rows
const GRID_COLS =
  'grid-cols-[3px_88px_28px_minmax(0,1fr)_120px_108px_88px_140px_minmax(0,1.1fr)_56px]'

export default function VideoList({
  items, hasMore, loading, expandedIds, selectedIds, groupsById,
  onToggleSelect, onToggleExpanded, onDelete, onLoadMore,
  onOpenFailureShot,
}: Props) {
  const { t } = useTranslation()
  const parentRef = useRef<HTMLDivElement | null>(null)

  const rows: Row[] = useMemo(() => {
    const out: Row[] = []
    let prevDate: string | null = null
    for (const v of items) {
      const d = dateOnly(v.uploaded_at)
      out.push({
        kind: 'video',
        video: v,
        isContinuation: prevDate === d,
      })
      prevDate = d
      if (expandedIds.has(v.id)) {
        out.push({ kind: 'detail', videoId: v.id })
      }
    }
    out.push({ kind: 'sentinel' })
    return out
  }, [items, expandedIds])

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => {
      const r = rows[i]
      if (r.kind === 'detail') return 180
      if (r.kind === 'sentinel') return 40
      return 44
    },
    overscan: 6,
    measureElement: (el) => el.getBoundingClientRect().height,
  })

  const endIndex = virtualizer.range?.endIndex ?? null
  useEffect(() => {
    if (!hasMore || loading) return
    if (endIndex !== null && endIndex >= rows.length - 1) {
      onLoadMore()
    }
  }, [endIndex, rows.length, hasMore, loading, onLoadMore])

  const headerLabel = (key: string, fallback: string) =>
    t(`videos.col.${key}`, { defaultValue: fallback })

  return (
    <div
      ref={parentRef}
      role="table"
      className="border border-[var(--color-line)] rounded-[10px] bg-[var(--color-surface)] overflow-visible min-h-[320px] shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
    >
      {/* sticky thead */}
      <div
        role="rowgroup"
        className="sticky top-0 z-10 bg-[var(--color-surface)] border-b border-[var(--color-line)] rounded-t-[10px]"
      >
        <div
          role="row"
          className={`grid items-center gap-x-3 pr-3 h-9 text-[11.5px] font-medium text-[var(--color-ink-3)] uppercase tracking-[0.06em] ${GRID_COLS}`}
        >
          <div />
          <div className="text-right">{headerLabel('date', '日期')}</div>
          <div />
          <div>{headerLabel('name', '文件名')}</div>
          <div>{headerLabel('group', '分组')}</div>
          <div className="text-center">{headerLabel('platforms', '平台')}</div>
          <div>{headerLabel('status', '状态')}</div>
          <div>{headerLabel('phone', '手机')}</div>
          <div>{headerLabel('caption', '标题')}</div>
          <div />
        </div>
      </div>

      {/* rows (virtual) */}
      <div
        role="rowgroup"
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((vi) => {
          const row = rows[vi.index]
          return (
            <div
              key={vi.key}
              data-index={vi.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${vi.start}px)`,
              }}
            >
              {row.kind === 'video' && (
                <VideoRow
                  row={row.video}
                  groupsById={groupsById}
                  isContinuation={row.isContinuation}
                  isExpanded={expandedIds.has(row.video.id)}
                  isSelected={selectedIds.has(row.video.id)}
                  onToggleSelect={onToggleSelect}
                  onToggleExpanded={onToggleExpanded}
                  onDelete={onDelete}
                />
              )}
              {row.kind === 'detail' && (
                <VideoDetailPanel
                  videoId={row.videoId}
                  onOpenFailureShot={onOpenFailureShot}
                />
              )}
              {row.kind === 'sentinel' && (
                <div className="px-3 py-3 text-center text-[12px] text-[var(--color-ink-5)]">
                  {hasMore
                    ? (loading
                        ? t('videos.loadingMore', { defaultValue: '加载中…' })
                        : t('videos.scrollForMore',
                            { defaultValue: '滚动加载更多' }))
                    : t('videos.endOfList',
                        { defaultValue: '— 已到底部 —' })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

> **关键变化点：** 原来 `style={{ height: '70vh' }}` 让虚拟滚动用 div 内部滚动；现在去掉，外层 `overflow-visible`，`useVirtualizer` 的 `getScrollElement` 仍是 `parentRef.current` 但因为外层没 overflow，虚拟滚动会回退到 window 滚动测量？— 不，`useVirtualizer` 需要一个 scroll container。
>
> **修正：** `useVirtualizer` 用 `getScrollElement: () => document.documentElement` 即用 window 作为滚动容器。配合 sticky thead，整页一个滚动条。

修正 `useVirtualizer` 配置（把上面代码中 `getScrollElement` 那一行替换）：

```typescript
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    observeElementRect: (instance, cb) => {
      // 用 parentRef 元素的位置做偏移计算；scroll source = window
      const el = instance.scrollElement
      if (!el) return
      const measure = () => {
        const rect = (parentRef.current ?? el).getBoundingClientRect()
        cb({ width: rect.width, height: window.innerHeight })
      }
      measure()
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    },
    estimateSize: (i) => {
      const r = rows[i]
      if (r.kind === 'detail') return 180
      if (r.kind === 'sentinel') return 40
      return 44
    },
    overscan: 6,
    measureElement: (el) => el.getBoundingClientRect().height,
  })
```

> **更简单的修正方案 — 推荐：** 用 `useWindowVirtualizer` 替代 `useVirtualizer`：
>
> ```typescript
> import { useWindowVirtualizer } from '@tanstack/react-virtual'
>
> const virtualizer = useWindowVirtualizer({
>   count: rows.length,
>   estimateSize: (i) => {
>     const r = rows[i]
>     if (r.kind === 'detail') return 180
>     if (r.kind === 'sentinel') return 40
>     return 44
>   },
>   overscan: 6,
>   measureElement: (el) => el.getBoundingClientRect().height,
> })
> ```
>
> `useWindowVirtualizer` 直接用 window 作滚动源，不需要 scroll container。删掉 `parentRef`。但要在外层 div 上加 `scrollMargin`：用 `virtualizer.scrollMargin` 替代 `parentRef.current` 的 offsetTop。
>
> 实际改法：在 `parentRef` 容器外层 div 上加 `data-virtual-window` 标识，把 `parentRef` 容器的 `offsetTop` 传给 virtualizer 的 `scrollMargin`：
>
> ```typescript
> const [marginTop, setMarginTop] = useState(0)
> useEffect(() => {
>   const update = () => setMarginTop(parentRef.current?.offsetTop ?? 0)
>   update()
>   window.addEventListener('resize', update)
>   return () => window.removeEventListener('resize', update)
> }, [])
> const virtualizer = useWindowVirtualizer({
>   count: rows.length,
>   estimateSize: ...,
>   overscan: 6,
>   scrollMargin: marginTop,
> })
> ```
>
> 然后 row 的 transform 用 `vi.start - marginTop`：
>
> ```typescript
> transform: `translateY(${vi.start - marginTop}px)`,
> ```

**执行选择：** 在 Step 1 实施时直接使用 `useWindowVirtualizer` 路线（推荐）。完整代码以此为准；上面"修正方案"是文档化的决策过程。

实施代码（最终版）：

```tsx
// frontend/src/components/videos/VideoList.tsx
import { useEffect, useMemo, useRef, useState } from 'react'
import { useWindowVirtualizer } from '@tanstack/react-virtual'
import { useTranslation } from 'react-i18next'
import type { VideoListRow } from '../../lib/useVideoPages'
import VideoRow from './VideoRow'
import VideoDetailPanel from './VideoDetailPanel'

type Row =
  | { kind: 'video'; video: VideoListRow; isContinuation: boolean }
  | { kind: 'detail'; videoId: number }
  | { kind: 'sentinel' }

type Props = {
  items: VideoListRow[]
  hasMore: boolean
  loading: boolean
  expandedIds: Set<number>
  selectedIds: Set<number>
  groupsById: Map<number, string>
  onToggleSelect: (id: number) => void
  onToggleExpanded: (id: number) => void
  onDelete: (id: number) => void
  onLoadMore: () => void
  onOpenFailureShot: (url: string, platform: string, detail: string) => void
}

function dateOnly(iso: string | null): string {
  return iso ? iso.slice(0, 10) : 'unknown'
}

const GRID_COLS =
  'grid-cols-[3px_88px_28px_minmax(0,1fr)_120px_108px_88px_140px_minmax(0,1.1fr)_56px]'

export default function VideoList({
  items, hasMore, loading, expandedIds, selectedIds, groupsById,
  onToggleSelect, onToggleExpanded, onDelete, onLoadMore,
  onOpenFailureShot,
}: Props) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [scrollMargin, setScrollMargin] = useState(0)

  useEffect(() => {
    const update = () => setScrollMargin(containerRef.current?.offsetTop ?? 0)
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  const rows: Row[] = useMemo(() => {
    const out: Row[] = []
    let prevDate: string | null = null
    for (const v of items) {
      const d = dateOnly(v.uploaded_at)
      out.push({
        kind: 'video',
        video: v,
        isContinuation: prevDate === d,
      })
      prevDate = d
      if (expandedIds.has(v.id)) {
        out.push({ kind: 'detail', videoId: v.id })
      }
    }
    out.push({ kind: 'sentinel' })
    return out
  }, [items, expandedIds])

  const virtualizer = useWindowVirtualizer({
    count: rows.length,
    estimateSize: (i) => {
      const r = rows[i]
      if (r.kind === 'detail') return 180
      if (r.kind === 'sentinel') return 40
      return 44
    },
    overscan: 6,
    scrollMargin,
    measureElement: (el) => el.getBoundingClientRect().height,
  })

  const endIndex = virtualizer.range?.endIndex ?? null
  useEffect(() => {
    if (!hasMore || loading) return
    if (endIndex !== null && endIndex >= rows.length - 1) {
      onLoadMore()
    }
  }, [endIndex, rows.length, hasMore, loading, onLoadMore])

  const headerLabel = (key: string, fallback: string) =>
    t(`videos.col.${key}`, { defaultValue: fallback })

  return (
    <div
      ref={containerRef}
      role="table"
      className="border border-[var(--color-line)] rounded-[10px] bg-[var(--color-surface)] min-h-[320px] shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden"
    >
      {/* sticky thead — sticky 相对窗口顶 */}
      <div
        role="rowgroup"
        className="sticky top-0 z-10 bg-[var(--color-surface)] border-b border-[var(--color-line)]"
      >
        <div
          role="row"
          className={`grid items-center gap-x-3 pr-3 h-9 text-[11.5px] font-medium text-[var(--color-ink-3)] uppercase tracking-[0.06em] ${GRID_COLS}`}
        >
          <div />
          <div className="text-right">{headerLabel('date', '日期')}</div>
          <div />
          <div>{headerLabel('name', '文件名')}</div>
          <div>{headerLabel('group', '分组')}</div>
          <div className="text-center">{headerLabel('platforms', '平台')}</div>
          <div>{headerLabel('status', '状态')}</div>
          <div>{headerLabel('phone', '手机')}</div>
          <div>{headerLabel('caption', '标题')}</div>
          <div />
        </div>
      </div>

      {/* virtual rows */}
      <div
        role="rowgroup"
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((vi) => {
          const row = rows[vi.index]
          return (
            <div
              key={vi.key}
              data-index={vi.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${vi.start - scrollMargin}px)`,
              }}
            >
              {row.kind === 'video' && (
                <VideoRow
                  row={row.video}
                  groupsById={groupsById}
                  isContinuation={row.isContinuation}
                  isExpanded={expandedIds.has(row.video.id)}
                  isSelected={selectedIds.has(row.video.id)}
                  onToggleSelect={onToggleSelect}
                  onToggleExpanded={onToggleExpanded}
                  onDelete={onDelete}
                />
              )}
              {row.kind === 'detail' && (
                <VideoDetailPanel
                  videoId={row.videoId}
                  onOpenFailureShot={onOpenFailureShot}
                />
              )}
              {row.kind === 'sentinel' && (
                <div className="px-3 py-3 text-center text-[12px] text-[var(--color-ink-5)]">
                  {hasMore
                    ? (loading
                        ? t('videos.loadingMore', { defaultValue: '加载中…' })
                        : t('videos.scrollForMore', { defaultValue: '滚动加载更多' }))
                    : t('videos.endOfList', { defaultValue: '— 已到底部 —' })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npm run build`
Expected: 通过。`useWindowVirtualizer` 在 `@tanstack/react-virtual` 中已导出。

- [ ] **Step 3 (可选): commit**

```bash
git add frontend/src/components/videos/VideoList.tsx
git commit -m "feat(videos): use window virtualizer + sticky thead, drop fixed 70vh"
```

---

### Task 6: Videos.tsx 改造

**Files:**
- Modify: `frontend/src/pages/Videos.tsx`

- [ ] **Step 1: 把 filter state 换成 useVideoFilter，加 useExpandedVideos / useScrollRestore**

替换 [Videos.tsx:68-101](../../../frontend/src/pages/Videos.tsx) 附近的 `useState` 块：

将：

```typescript
const [filter, setFilter] = useState<VideoFilter>({
  group_id: 'all', status: 'all',
})
```

替换为：

```typescript
const { filter, filterKey, setFilter } = useVideoFilter()
```

将：

```typescript
const [expandedVideos, setExpandedVideos] = useState<Set<number>>(new Set())
```

替换为：

```typescript
const { expandedIds: expandedVideos, toggle: toggleExpanded } =
  useExpandedVideos(filterKey)
```

并删除原本的 `toggleExpanded` useCallback 定义（已被 hook 提供）。

在 hook 列表中（`useVideoPages` 调用之后）加：

```typescript
useScrollRestore(filterKey, items.length > 0)
```

顶部 imports 改：

```typescript
import { useVideoFilter } from '../lib/useVideoFilter'
import { useExpandedVideos } from '../lib/useExpandedVideos'
import { useScrollRestore } from '../lib/useScrollRestore'
import {
  useVideoPages,
} from '../lib/useVideoPages'
import type { VideoFilter } from '../lib/useVideoFilter'
```

- [ ] **Step 2: filter chip 行的 setFilter 调用语义改一下**

原来：

```typescript
onClick={() => setFilter((f) => ({ ...f, group_id: ALL_GROUPS }))}
```

新 hook 的 `setFilter` 接 `Partial<VideoFilter>`，可直接：

```typescript
onClick={() => setFilter({ group_id: 'all' })}
```

把整段 chips 的 onClick 都改成 `setFilter({ group_id: ... })` / `setFilter({ status: ... })`。

- [ ] **Step 3: 新增 status chips 行**

在群组 chips 行下方插入：

```tsx
<div className="flex gap-2 flex-wrap text-xs items-center">
  <span className="text-[11.5px] font-medium uppercase tracking-[0.06em] text-[var(--color-ink-4)] mr-1">
    {t('videos.filterStatusLabel', { defaultValue: '状态' })}
  </span>
  {(['all', 'pending', 'terminal'] as const).map((s) => (
    <button
      key={s}
      onClick={() => setFilter({ status: s })}
      className={
        'h-7 px-3 rounded-full text-[12.5px] font-medium transition-colors ' +
        (filter.status === s
          ? 'bg-[var(--color-blue-bg)] text-[var(--color-blue)]'
          : 'bg-[var(--color-gray-bg)] text-[var(--color-ink-2)] hover:bg-[#e6e6ea]')
      }
    >
      {t(`videos.statusFilter.${s}`, {
        defaultValue: s === 'all' ? '全部' : s === 'pending' ? '待发布' : '已结束',
      })}
    </button>
  ))}
</div>
```

同时把上方的群组 chip 行样式统一调成 `h-7 rounded-full bg-blue-bg` 风格（不再是 `bg-blue-600 text-white`）。

- [ ] **Step 4: 把批量栏改 sticky**

原来 [Videos.tsx:449-478](../../../frontend/src/pages/Videos.tsx) 是 `Card mb-3`。改为顶部 sticky 横栏：

```tsx
{selected.size > 0 && (
  <div className="sticky top-0 z-20 bg-[var(--color-blue-bg)] border border-[#d2e0f2] rounded-[10px] px-3.5 py-2.5 flex items-center gap-3 flex-wrap mb-3 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
    {/* …原内容沿用… */}
  </div>
)}
```

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npm run build`
Expected: 通过。

- [ ] **Step 6 (可选): commit**

```bash
git add frontend/src/pages/Videos.tsx
git commit -m "feat(videos): persist filter/expanded/scroll, add status chips, sticky batch bar"
```

---

### Task 7: 删除 VideoDateCell

**Files:**
- Delete: `frontend/src/components/videos/VideoDateCell.tsx`

- [ ] **Step 1: 确认无引用残留**

Run: `grep -r "VideoDateCell" frontend/src`
Expected: 无输出（VideoRow 已不引用，因为 Task 4 把日期逻辑内联了）。

- [ ] **Step 2: 删除文件**

Run: `rm frontend/src/components/videos/VideoDateCell.tsx`

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npm run build`
Expected: 通过。

- [ ] **Step 4 (可选): commit**

```bash
git add -A frontend/src/components/videos/
git commit -m "refactor(videos): remove VideoDateCell (logic inlined into VideoRow)"
```

---

### Task 8: 验证 + 部署准备

- [ ] **Step 1: Frontend build clean**

Run: `cd frontend && npm run build`
Expected: PASS。

- [ ] **Step 2: Backend tests still ≥385**

Run: `uv run pytest -q`
Expected: ≥385 passed。

- [ ] **Step 3: dev server 本地验证**

Run: `./run.sh`
打开 http://localhost:8080/videos，按 spec "测试策略" 清单逐项确认：

- [ ] 刷新页面 filter 状态保留（URL 带 `?group=X&status=Y`）
- [ ] 切 filter 互不污染 sessionStorage 桶
- [ ] 展开多行后刷新，原展开行仍展开
- [ ] 刷新后滚动位置恢复
- [ ] hover 行时删除按钮淡入
- [ ] 同日多行不重复日期，跨日有上边线
- [ ] FAILURE 行对应平台图标有红点
- [ ] 表头 sticky 跟随视口
- [ ] 选中时批量栏 sticky 在表头之上（≥10 行视频时）
- [ ] 长文件名/长 caption 截断不溢出

- [ ] **Step 4: 部署到 aliyun**

按 [docs/production-deploy.md](../../production-deploy.md) 流程打包 + 部署（无 schema 改动）。重启 mkt + mkt-worker（参考 [memory:deploy-restart-worker]）。

- [ ] **Step 5: 生产页面冒烟**

打开 http://139.196.52.92:8080/videos，确认上述清单仍通过。

---

## Self-Review (writing-plans）

**Spec coverage:**
- Spec §1 整体布局变化 → Task 5 (VideoList) ✅
- Spec §2 表格结构 (10 列 grid) → Task 4 + 5 ✅
- Spec §3 状态色条 → Task 4 (STATUS_BAR map) ✅
- Spec §4 状态列 Dot → Task 4 (STATUS_DOT map) ✅
- Spec §5 平台图标褪色 + FAILURE 红点 → Task 4 ✅
- Spec §6 同日 continuation 视觉 → Task 4 (formatDateLabel 内联) ✅
- Spec §7 文件名列 → Task 4 ✅
- Spec §8 操作列 hover 淡入 → Task 4 ✅
- Spec §9 筛选 chips（group + status 双行） → Task 6 ✅
- Spec §10 批量工具栏 sticky → Task 6 Step 4 ✅
- Spec §11 持久化（filter URL / expanded sessionStorage / scrollY sessionStorage） → Task 1 + 2 + 3 + 6 ✅
- Spec §12 错误/空状态 → 保留现有，无需改 ✅

**Placeholder scan:** 无 TBD / TODO 文本；每步都有完整代码或具体命令；自我审查未发现"add appropriate error handling"这类含糊指令。

**Type consistency:**
- `VideoFilter` 在 Task 1 定义为 `{ group_id: GroupSel; status: StatusSel }`，Task 6 使用一致 ✅
- `useExpandedVideos` 返回 `{ expandedIds, toggle }`，Task 6 解构使用 `expandedIds` + `toggle: toggleExpanded` ✅
- `useScrollRestore(filterKey, pageReady)` 签名一致 ✅
- VideoRow Props 与 VideoList 传参一致（10 项） ✅

无修正需要。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-04-videos-page-redesign.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 我每 task 派一个新 subagent 实施 + review，迭代快
2. **Inline Execution** — 我在当前会话里按 plan 顺序执行，每 task 后停一下让你检查

Which approach?
