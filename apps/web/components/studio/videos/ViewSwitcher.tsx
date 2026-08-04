'use client'

import type { ReactNode } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { cn } from '@/lib/studio/cn'
import type { VideoView } from '@/lib/studio/useVideoView'

type Props = {
  value: VideoView
  onChange: (v: VideoView) => void
}

export function ViewSwitcher({ value, onChange }: Props) {
  const t = useStudioT()
  const btn = (k: VideoView, label: string, icon: ReactNode) => (
    <button
      type="button"
      onClick={() => onChange(k)}
      className={cn(
        'inline-flex items-center gap-1.5 h-7 px-2.5 rounded-[6px] text-[12.5px] font-medium',
        'transition-colors',
        value === k
          ? 'bg-white text-[var(--color-foreground)] shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
          : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
      )}
      aria-pressed={value === k}
    >
      {icon}
      {label}
    </button>
  )
  return (
    <div className="inline-flex p-[3px] bg-[var(--color-gray-bg)] rounded-[8px]">
      {btn('list', t('videos.view.list', { defaultValue: '列表' }),
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
          <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>)}
      {btn('grid', t('videos.view.grid', { defaultValue: '网格' }),
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
          <rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.4" />
        </svg>)}
    </div>
  )
}
