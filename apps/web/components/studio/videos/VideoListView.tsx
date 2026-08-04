'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { useVirtualizer } from '@tanstack/react-virtual'
import type { VideoListRow } from '@/lib/studio/useVideoPages'
import VideoMediaRow from '@/components/studio/videos/VideoMediaRow'
import { DateSectionHead } from '@/components/studio/videos/DateSectionHead'
import VideoDetailPanel from '@/components/studio/videos/VideoDetailPanel'
import { parseBackendIso } from '@/lib/studio/time'

type Row =
  | { kind: 'section'; iso: string | null; count: number }
  | { kind: 'video'; video: VideoListRow }
  | { kind: 'detail'; videoId: number }
  | { kind: 'sentinel' }

function dateKey(iso: string | null): string {
  const d = parseBackendIso(iso ?? '')
  if (!d) return 'unknown'
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

type Props = {
  items: VideoListRow[]
  hasMore: boolean
  loading: boolean
  expandedIds: Set<number>
  selectedIds: Set<number>
  groupsById: Map<number, string>
  onRowClick: (id: number, mods: { shift: boolean; meta: boolean }) => void
  onToggleExpanded: (id: number) => void
  onDelete: (id: number) => void
  onLoadMore: () => void
  onOpenFailureShot: (url: string, platform: string, detail: string) => void
}

export function VideoListView({
  items, hasMore, loading, expandedIds, selectedIds, groupsById,
  onRowClick, onToggleExpanded, onDelete, onLoadMore, onOpenFailureShot,
}: Props) {
  const t = useStudioT()
  const containerRef = useRef<HTMLDivElement | null>(null)

  const rows: Row[] = useMemo(() => {
    const out: Row[] = []
    const sectionCounts: number[] = []
    let prevKey: string | null = null
    let curSectionRef: { count: number } | null = null
    for (const v of items) {
      const k = dateKey(v.uploaded_at)
      if (k !== prevKey) {
        if (curSectionRef) sectionCounts.push(curSectionRef.count)
        curSectionRef = { count: 0 }
        out.push({ kind: 'section', iso: v.uploaded_at, count: 0 })
        prevKey = k
      }
      out.push({ kind: 'video', video: v })
      if (expandedIds.has(v.id)) out.push({ kind: 'detail', videoId: v.id })
      if (curSectionRef) curSectionRef.count += 1
    }
    // Fill the count back into each section placeholder
    let segIdx = 0
    if (curSectionRef) sectionCounts.push(curSectionRef.count)
    for (let i = 0; i < out.length; i++) {
      const r = out[i]
      if (r.kind === 'section') {
        out[i] = { kind: 'section', iso: r.iso, count: sectionCounts[segIdx] ?? 0 }
        segIdx += 1
      }
    }
    out.push({ kind: 'sentinel' })
    return out
  }, [items, expandedIds])

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => containerRef.current,
    estimateSize: (i) => {
      const r = rows[i]
      if (r.kind === 'section') return 32
      if (r.kind === 'detail') return 180
      if (r.kind === 'sentinel') return 40
      return 64
    },
    overscan: 6,
    measureElement: (el) => el.getBoundingClientRect().height,
  })

  const endIndex = virtualizer.range?.endIndex ?? null
  useEffect(() => {
    if (!hasMore || loading) return
    if (endIndex !== null && endIndex >= rows.length - 1) onLoadMore()
  }, [endIndex, rows.length, hasMore, loading, onLoadMore])

  return (
    <div
      ref={containerRef}
      role="table"
      className="border border-[var(--color-border)] rounded-[10px] bg-[var(--color-card)] shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-auto max-h-[calc(100vh-260px)]"
    >
      <div
        role="rowgroup"
        style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}
      >
        {virtualizer.getVirtualItems().map((vi) => {
          const row = rows[vi.index]
          return (
            <div
              key={vi.key}
              data-index={vi.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute', top: 0, left: 0, right: 0,
                transform: `translateY(${vi.start}px)`,
              }}
            >
              {row.kind === 'section' && (
                <DateSectionHead iso={row.iso} count={row.count} />
              )}
              {row.kind === 'video' && (
                <VideoMediaRow
                  row={row.video}
                  groupsById={groupsById}
                  isExpanded={expandedIds.has(row.video.id)}
                  isSelected={selectedIds.has(row.video.id)}
                  onRowClick={onRowClick}
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
                <div className="px-3 py-3 text-center text-[12px] text-[var(--color-muted-foreground-3)]">
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
