'use client'

import { useLocale } from 'next-intl'
import { useStudioT } from '@/lib/studio/i18n'
import { cn } from '@/lib/studio/cn'
import {
  BUCKET_LABEL_ZH,
  type StatusBucket,
} from '@/lib/studio/videoStatusBucket'

const BUCKET_BG: Record<StatusBucket, string> = {
  pending:     'bg-[var(--color-border-strong)]',
  in_progress: 'bg-[var(--color-blue)]',
  handled:     'bg-[var(--color-muted-foreground)]',
}

const BUCKET_GLYPH: Record<StatusBucket, string> = {
  pending:     '○',
  in_progress: '⟳',
  handled:     '✓',
}

type Variant = 'row' | 'overlay'

type Props = {
  bucket: StatusBucket
  x: number
  n: number
  variant: Variant
}

export function StatusPill({ bucket, x, n, variant }: Props) {
  const t = useStudioT()
  const locale = useLocale()
  const zh = (locale ?? locale ?? 'en').startsWith('zh')
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
        'inline-flex items-center justify-center gap-1.5 h-7 w-[118px]',
        'rounded-[6px] text-[11.5px] font-semibold text-white tabular-nums',
        'whitespace-nowrap',
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
