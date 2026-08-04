'use client'

import { useLocale } from 'next-intl'
import { parseBackendIso } from '@/lib/studio/time'

type Props = { iso: string | null; count: number }

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear()
      && a.getMonth() === b.getMonth()
      && a.getDate() === b.getDate()
}

function label(iso: string | null, locale: string): string {
  const d = parseBackendIso(iso ?? '')
  if (!d) return '—'
  const today = new Date()
  const y = new Date(today); y.setDate(today.getDate() - 1)
  const zh = locale.startsWith('zh')
  if (sameDay(d, today)) return zh ? '今天' : 'Today'
  if (sameDay(d, y))     return zh ? '昨天' : 'Yesterday'
  return d.toLocaleDateString(locale, {
    year: 'numeric', month: 'short', day: 'numeric',
  })
}

export function DateSectionHead({ iso, count }: Props) {
  const locale = useLocale()
  return (
    <div
      className="flex items-center gap-2 px-4 py-2 bg-[var(--color-gray-bg)] border-b border-[var(--color-border)] text-[12px] font-semibold text-[var(--color-muted-foreground)]"
    >
      <span>{label(iso, locale)}</span>
      <span className="text-[var(--color-muted-foreground-3)] font-normal">· {count}</span>
    </div>
  )
}
