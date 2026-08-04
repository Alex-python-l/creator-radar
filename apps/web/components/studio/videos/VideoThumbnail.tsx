'use client'

import { useState } from 'react'
import { API_ORIGIN } from '@/lib/studio/api'
import { cn } from '@/lib/studio/cn'

type Props = {
  url: string | null
  duration: number | null
  variant: 'list' | 'grid'
  videoId: number
}

const GRADIENTS = [
  'from-[#4a5b8a] to-[#8b6a8c]',
  'from-[#c7a16f] to-[#a86b3a]',
  'from-[#3d8c6b] to-[#2a5a40]',
  'from-[#8a4a7a] to-[#3a2a5a]',
  'from-[#6a8a4a] to-[#3a5a2a]',
  'from-[#4a6a8a] to-[#2a3a5a]',
  'from-[#8a6a4a] to-[#5a3a2a]',
  'from-[#5a4a8a] to-[#3a2a6a]',
]

function formatDuration(s: number | null): string | null {
  if (s == null || !Number.isFinite(s) || s <= 0) return null
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${r.toString().padStart(2, '0')}`
}

export function VideoThumbnail({ url, duration, variant, videoId }: Props) {
  const [errored, setErrored] = useState(false)
  const showImage = url && !errored
  const grad = GRADIENTS[Math.abs(videoId) % GRADIENTS.length]
  const dur = formatDuration(duration)

  const box = variant === 'grid'
    ? 'w-full aspect-[16/10] rounded-[8px]'
    : 'w-[60px] h-[40px] rounded-[6px]'

  return (
    <div className={cn('relative overflow-hidden shrink-0', box)}>
      {showImage ? (
        <img
          // `url` is the backend's `/static/thumbnails/…` path (see
          // apps/studio/app/api/videos.py) — root-relative, so it needs the
          // same /studio origin prefix as every other backend call.
          src={url ? API_ORIGIN + url : url}
          alt=""
          loading="lazy"
          onError={() => setErrored(true)}
          className="absolute inset-0 w-full h-full object-cover"
        />
      ) : (
        <div
          className={cn('absolute inset-0 bg-gradient-to-br', grad)}
          aria-hidden
        >
          <svg
            className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-white/70"
            width={variant === 'grid' ? 32 : 18}
            height={variant === 'grid' ? 32 : 18}
            viewBox="0 0 24 24"
            fill="currentColor"
          >
            <path d="M8 5v14l11-7z" />
          </svg>
        </div>
      )}
      {dur && (
        <span
          className={cn(
            'absolute bg-black/60 text-white font-semibold tabular-nums rounded-[3px]',
            variant === 'grid'
              ? 'right-1.5 top-1.5 text-[10px] px-1.5 py-0.5'
              : 'right-1 bottom-1 text-[9px] px-1',
          )}
        >
          {dur}
        </span>
      )}
    </div>
  )
}
