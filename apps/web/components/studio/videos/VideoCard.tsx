'use client'

import { useLocale } from 'next-intl'
import { useStudioT } from '@/lib/studio/i18n'
import type { VideoListRow } from '@/lib/studio/useVideoPages'
import { VideoThumbnail } from '@/components/studio/videos/VideoThumbnail'
import { PlatformBadgeColumn } from '@/components/studio/videos/PlatformBadgeColumn'
import { StatusPill } from '@/components/studio/videos/StatusPill'
import { deriveStatusBucket } from '@/lib/studio/videoStatusBucket'
import { cn } from '@/lib/studio/cn'
import { parseBackendIso } from '@/lib/studio/time'

function formatDateTime(iso: string | null, locale: string): string {
  const d = parseBackendIso(iso ?? '')
  if (!d) return '—'
  const time = d.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
  const date = d.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
  return `${date} ${time}`
}

type Props = {
  row: VideoListRow
  groupsById: Map<number, string>
  isSelected: boolean
  onClick: (id: number, mods: { shift: boolean; meta: boolean }) => void
  onDelete: (id: number) => void
  onOpenDetail: (id: number) => void
}

export function VideoCard({
  row, groupsById, isSelected, onClick, onDelete, onOpenDetail,
}: Props) {
  const t = useStudioT()
  const locale = useLocale()
  const isPending = row.summary === 'PENDING'
  const groupName = row.group_id == null
    ? t('videos.ungrouped')
    : groupsById.get(row.group_id) ?? `#${row.group_id}`

  return (
    <div
      role="gridcell"
      onClick={(e) => onClick(row.id, {
        shift: e.shiftKey, meta: e.metaKey || e.ctrlKey,
      })}
      className={cn(
        'group relative flex flex-col rounded-[10px] border',
        'cursor-pointer transition-colors overflow-hidden',
        isSelected
          ? 'border-[var(--color-primary)] ring-[3px] ring-[var(--color-primary)] bg-[var(--color-muted)] shadow-[0_2px_8px_rgba(0,0,0,0.15)]'
          : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-muted-foreground-3)]',
      )}
    >
      <div className="relative">
        <VideoThumbnail
          url={row.thumbnail_url}
          duration={row.duration_seconds}
          variant="grid"
          videoId={row.id}
        />
        <div className="absolute left-2 top-2">
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
        </div>
        {isPending && (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => {}}
            aria-label={`select ${row.original_filename ?? row.id}`}
            onClick={(e) => e.stopPropagation()}
            className={cn(
              'absolute left-2 bottom-2 w-[18px] h-[18px] accent-[var(--color-primary)] bg-white rounded-[4px] shadow-[0_1px_2px_rgba(0,0,0,0.15)]',
              'opacity-0 group-hover:opacity-100',
              isSelected && 'opacity-100',
            )}
          />
        )}
        <div className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onOpenDetail(row.id) }}
            className="h-7 px-2 bg-black/55 hover:bg-black/70 text-white text-[11px] rounded-[5px]"
          >
            {t('videos.openDetail', { defaultValue: '详情' })}
          </button>
          {isPending && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onDelete(row.id) }}
              className="ml-1 h-7 px-2 bg-black/55 hover:bg-[var(--color-destructive)] text-white text-[11px] rounded-[5px]"
            >
              {t('common.delete')}
            </button>
          )}
        </div>
      </div>
      <div className="px-3 py-2.5">
        <div className="text-[13px] font-medium text-[var(--color-foreground)] truncate">
          {row.original_filename}
        </div>
        <div className="mt-0.5 text-[11px] text-[var(--color-muted-foreground-2)] truncate">
          {formatDateTime(row.uploaded_at, locale)} · {groupName}
        </div>
        <div className="mt-2">
          <PlatformBadgeColumn
            platforms={row.platforms}
            fallbackPhoneLabel={row.phone_label}
            layout="inline"
            size="sm"
          />
        </div>
      </div>
    </div>
  )
}
