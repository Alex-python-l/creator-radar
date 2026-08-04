'use client'

import { cn } from '@/lib/studio/cn'

type ChipTone =
  | 'neutral' | 'pending' | 'in_progress' | 'handled'

const TONE_INACTIVE: Record<ChipTone, string> = {
  neutral:     'bg-[var(--color-gray-bg)] text-[var(--color-foreground-2)] hover:bg-[#e6e6ea]',
  pending:     'bg-[var(--color-gray-bg)] text-[var(--color-muted-foreground)] hover:bg-[#e6e6ea]',
  in_progress: 'bg-[#e7f1ff] text-[var(--color-blue)] hover:bg-[#d6e6ff]',
  handled:     'bg-[#ececef] text-[var(--color-foreground-2)] hover:bg-[#dfdfe3]',
}

const TONE_ACTIVE: Record<ChipTone, string> = {
  neutral:     'bg-[var(--color-primary)] text-[var(--color-primary-foreground)]',
  pending:     'bg-[var(--color-muted-foreground-2)] text-white',
  in_progress: 'bg-[var(--color-blue)] text-white',
  handled:     'bg-[var(--color-muted-foreground)] text-white',
}

type Props = {
  active: boolean
  tone: ChipTone
  onClick: () => void
  glyph?: string
  label: string
  count?: number
}

export function FilterChip({ active, tone, onClick, glyph, label, count }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={label}
      className={cn(
        'inline-flex min-w-0 max-w-[220px] sm:max-w-[320px] items-center gap-1.5 h-7 px-3 rounded-full',
        'text-[12.5px] font-medium transition-colors whitespace-nowrap',
        active ? TONE_ACTIVE[tone] : TONE_INACTIVE[tone],
      )}
    >
      {glyph && <span className="text-[12px] leading-none">{glyph}</span>}
      <span className="min-w-0 truncate">{label}</span>
      {count !== undefined && (
        <span className={cn(
          'text-[11.5px] font-bold tabular-nums ml-0.5',
          active ? 'text-white/85' : 'opacity-70',
        )}>
          {count}
        </span>
      )}
    </button>
  )
}
