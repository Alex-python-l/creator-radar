'use client'

import { useStudioT } from '@/lib/studio/i18n'
import type { VideoListRow } from '@/lib/studio/useVideoPages'
import { VideoThumbnail } from '@/components/studio/videos/VideoThumbnail'
import { PlatformBadgeColumn } from '@/components/studio/videos/PlatformBadgeColumn'
import { StatusPill } from '@/components/studio/videos/StatusPill'
import { deriveStatusBucket } from '@/lib/studio/videoStatusBucket'
import { cn } from '@/lib/studio/cn'
import { parseBackendIso } from '@/lib/studio/time'

function formatTime(iso: string | null): string {
  const d = parseBackendIso(iso ?? '')
  if (!d) return ''
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

type Props = {
  row: VideoListRow
  groupsById: Map<number, string>
  isExpanded: boolean
  isSelected: boolean
  onRowClick: (id: number, mods: { shift: boolean; meta: boolean }) => void
  onToggleExpanded: (id: number) => void
  onDelete: (id: number) => void
}

export default function VideoMediaRow({
  row, groupsById, isExpanded, isSelected,
  onRowClick, onToggleExpanded, onDelete,
}: Props) {
  const t = useStudioT()
  const isPending = row.summary === 'PENDING'
  const groupName = row.group_id == null
    ? t('videos.ungrouped')
    : groupsById.get(row.group_id) ?? `#${row.group_id}`

  return (
    <div
      role="row"
      onMouseDown={(e) => { if (e.shiftKey) e.preventDefault() }}
      onClick={(e) => onRowClick(row.id, {
        shift: e.shiftKey, meta: e.metaKey || e.ctrlKey,
      })}
      className={cn(
        'group grid grid-cols-[118px_60px_minmax(0,1fr)_auto] gap-x-3 items-center',
        'pr-3 h-16 border-b border-[var(--color-border)] cursor-pointer transition-colors',
        isSelected
          ? 'bg-[var(--color-muted)] hover:bg-[var(--color-gray-bg)]'
          : 'hover:bg-[var(--color-muted)]',
      )}
    >
      {(() => {
        const d = deriveStatusBucket(row.platforms)
        return <StatusPill bucket={d.bucket} x={d.x} n={d.n} variant="row" />
      })()}

      <div className="relative">
        <VideoThumbnail
          url={row.thumbnail_url}
          duration={row.duration_seconds}
          variant="list"
          videoId={row.id}
        />
        {isPending && (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => {}}
            aria-label={`select ${row.original_filename ?? row.id}`}
            className={cn(
              'absolute left-1 top-1 w-3.5 h-3.5 accent-[var(--color-primary)] bg-white rounded-[2px]',
              'opacity-0 group-hover:opacity-100',
              isSelected && 'opacity-100',
            )}
            onClick={(e) => e.stopPropagation()}
          />
        )}
      </div>

      <div className="min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onToggleExpanded(row.id) }}
            aria-expanded={isExpanded}
            aria-label={isExpanded
              ? (t('videos.collapseHistoryDetails') as string)
              : (t('videos.expandHistoryDetails') as string)}
            className="inline-flex h-5 w-5 items-center justify-center rounded-[4px] border border-[var(--color-border)] bg-white text-[12px] leading-none text-[var(--color-muted-foreground-2)] hover:text-[var(--color-foreground)] hover:border-[var(--color-muted-foreground-3)] shrink-0"
          >
            {isExpanded ? '−' : '+'}
          </button>
          <span className="text-[14px] font-medium text-[var(--color-foreground)] truncate min-w-0">
            {row.original_filename}
          </span>
        </div>
        <div className="mt-0.5 text-[11.5px] text-[var(--color-muted-foreground-2)] flex items-center gap-2">
          <span className="tabular-nums">{formatTime(row.uploaded_at)}</span>
          <span className="w-[3px] h-[3px] rounded-full bg-[var(--color-border-strong)]" />
          <span className="truncate">{groupName}</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <PlatformBadgeColumn
          platforms={row.platforms}
          fallbackPhoneLabel={row.phone_label}
          layout="column"
          size="md"
        />
        {isPending && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(row.id) }}
            className="text-[12.5px] text-[var(--color-muted-foreground-2)] hover:text-[var(--color-destructive)] opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-1 ml-2"
          >
            {t('common.delete')}
          </button>
        )}
      </div>
    </div>
  )
}
