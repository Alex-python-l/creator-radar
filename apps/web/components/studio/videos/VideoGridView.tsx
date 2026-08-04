'use client'

import { useEffect, useMemo, useRef } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import type { VideoListRow } from '@/lib/studio/useVideoPages'
import { VideoCard } from '@/components/studio/videos/VideoCard'
import { DateSectionHead } from '@/components/studio/videos/DateSectionHead'
import { parseBackendIso } from '@/lib/studio/time'

function dateKey(iso: string | null): string {
  const d = parseBackendIso(iso ?? '')
  if (!d) return 'unknown'
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

type Props = {
  items: VideoListRow[]
  hasMore: boolean
  loading: boolean
  selectedIds: Set<number>
  groupsById: Map<number, string>
  onCardClick: (id: number, mods: { shift: boolean; meta: boolean }) => void
  onDelete: (id: number) => void
  onOpenDetail: (id: number) => void
  onLoadMore: () => void
}

export function VideoGridView({
  items, hasMore, loading, selectedIds, groupsById,
  onCardClick, onDelete, onOpenDetail, onLoadMore,
}: Props) {
  const t = useStudioT()
  const sections = useMemo(() => {
    const map = new Map<string, { iso: string | null; rows: VideoListRow[] }>()
    for (const v of items) {
      const k = dateKey(v.uploaded_at)
      const cur = map.get(k)
      if (cur) cur.rows.push(v)
      else map.set(k, { iso: v.uploaded_at, rows: [v] })
    }
    return Array.from(map.values())
  }, [items])

  // Infinite-scroll sentinel
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!sentinelRef.current) return
    const obs = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasMore && !loading) onLoadMore()
    }, { rootMargin: '200px' })
    obs.observe(sentinelRef.current)
    return () => obs.disconnect()
  }, [hasMore, loading, onLoadMore])

  return (
    <div className="rounded-[10px] bg-[var(--color-card)] border border-[var(--color-border)] shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden">
      {sections.map((sec, idx) => (
        <div key={idx}>
          <DateSectionHead iso={sec.iso} count={sec.rows.length} />
          <div
            className="grid gap-3 p-3"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}
          >
            {sec.rows.map((row) => (
              <VideoCard
                key={row.id}
                row={row}
                groupsById={groupsById}
                isSelected={selectedIds.has(row.id)}
                onClick={onCardClick}
                onDelete={onDelete}
                onOpenDetail={onOpenDetail}
              />
            ))}
          </div>
        </div>
      ))}
      <div ref={sentinelRef} className="h-10 flex items-center justify-center text-[12px] text-[var(--color-muted-foreground-3)]">
        {hasMore
          ? (loading
              ? t('videos.loadingMore', { defaultValue: '加载中…' })
              : t('videos.scrollForMore', { defaultValue: '滚动加载更多' }))
          : t('videos.endOfList', { defaultValue: '— 已到底部 —' })}
      </div>
    </div>
  )
}
