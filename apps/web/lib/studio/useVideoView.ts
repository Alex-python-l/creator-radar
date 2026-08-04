'use client'

import { useEffect, useState } from 'react'

export type VideoView = 'list' | 'grid'

const KEY = 'videos:view'

// localStorage 只能在 effect 里读:SSR/水合期服务端与客户端必须渲染同一
// 初始值('list'),否则存过 'grid' 的用户会 hydration mismatch。
export function useVideoView(): [VideoView, (v: VideoView) => void] {
  const [view, setView] = useState<VideoView>('list')
  useEffect(() => {
    try {
      if (localStorage.getItem(KEY) === 'grid') setView('grid')
    } catch {
      /* private mode — keep default */
    }
  }, [])
  const update = (v: VideoView) => {
    try {
      localStorage.setItem(KEY, v)
    } catch {
      /* private mode — keep in-memory only */
    }
    setView(v)
  }
  return [view, update]
}
