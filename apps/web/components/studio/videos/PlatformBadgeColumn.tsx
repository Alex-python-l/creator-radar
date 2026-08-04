'use client'

import { useStudioT } from '@/lib/studio/i18n'
import { PLATFORMS, type Platform } from '@/lib/studio/api'
import { PlatformIcon } from '@/components/studio/PlatformIcon'
import { cn } from '@/lib/studio/cn'

type PerPlat = {
  result: string
  has_phone: boolean
  phone_label?: string | null
}

type Props = {
  platforms: Partial<Record<Platform, PerPlat>>
  fallbackPhoneLabel: string | null
  layout: 'column' | 'inline'
  size?: 'sm' | 'md'
}

const CORNER_BG: Record<string, string> = {
  SUCCESS: 'bg-[var(--color-green)]',
  FAILURE: 'bg-[var(--color-destructive)]',
  INITIATED: 'bg-[var(--color-blue)]',
  QUEUED: 'bg-[var(--color-blue)]',
}

const CORNER_GLYPH: Record<string, string> = {
  SUCCESS: '✓',
  FAILURE: '!',
  INITIATED: '·',
  QUEUED: '·',
}

function Badge({
  plat, info, size,
}: { plat: Platform; info: PerPlat | undefined; size: 'sm' | 'md' }) {
  const muted = !info
  const result = info?.result ?? 'PENDING'
  const corner = CORNER_BG[result]
  const glyph = CORNER_GLYPH[result]
  const dim = size === 'sm' ? 22 : 26
  const iconDim = size === 'sm' ? 14 : 16
  return (
    <span
      className={cn(
        'relative inline-flex items-center justify-center rounded-[7px] bg-[var(--color-muted)]',
        muted && 'opacity-25 grayscale',
      )}
      style={{ width: dim, height: dim }}
      aria-label={plat}
      title={plat}
    >
      <PlatformIcon platform={plat} size={iconDim} />
      {corner && (
        <span
          className={cn(
            'absolute -right-[3px] -bottom-[3px] w-[11px] h-[11px] rounded-full',
            'border-[2px] border-white text-white text-[7px] font-black leading-none',
            'inline-flex items-center justify-center',
            corner,
          )}
          aria-hidden
        >
          {glyph}
        </span>
      )}
    </span>
  )
}

export function PlatformBadgeColumn({
  platforms, fallbackPhoneLabel, layout, size = 'md',
}: Props) {
  const t = useStudioT()
  if (layout === 'column') {
    return (
      <div className="flex gap-1.5">
        {PLATFORMS.map((plat: Platform) => {
          const info = platforms[plat]
          const phone = info?.phone_label ?? null
          return (
            <div key={plat} className="flex flex-col items-center gap-1 min-w-[32px]">
              <Badge plat={plat} info={info} size={size} />
              <span className="text-[9.5px] text-[var(--color-muted-foreground-2)] max-w-[50px] truncate">
                {phone ?? (info ? t('videos.phoneMissing', { defaultValue: '—' }) : '—')}
              </span>
            </div>
          )
        })}
      </div>
    )
  }
  // inline: all badges in a single row, fallback phone on the right
  return (
    <div className="flex items-center gap-1.5">
      {PLATFORMS.map((plat: Platform) => (
        <Badge key={plat} plat={plat} info={platforms[plat]} size={size} />
      ))}
      {fallbackPhoneLabel && (
        <span className="ml-2 text-[10px] text-[var(--color-muted-foreground-2)] truncate max-w-[80px]">
          {fallbackPhoneLabel}
        </span>
      )}
    </div>
  )
}
