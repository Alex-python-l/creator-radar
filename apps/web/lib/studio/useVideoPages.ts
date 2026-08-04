'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Platform } from './api'

export type VideoListRow = {
  id: number
  original_filename: string | null
  uploaded_at: string | null
  group_id: number | null
  phone_label: string | null
  thumbnail_url: string | null
  duration_seconds: number | null
  platforms: Partial<Record<Platform, {
    result: string
    has_phone: boolean
    phone_label?: string | null
  }>>
  caption_preview: string | null
  summary: 'PENDING' | 'TERMINAL'
}

type Page = {
  items: VideoListRow[]
  next_cursor: number | string | null
  total: number | null
  summary_counts: {
    total: number
    pending: number
    in_progress: number
    handled: number
  } | null
}

import type { VideoFilter } from './useVideoFilter'
export type { VideoFilter }

const PAGE_SIZE = 50

export function useVideoPages(filter: VideoFilter) {
  const [pages, setPages] = useState<Page[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const lastRefreshAtRef = useRef<number>(0)
  const filterKey = `${filter.group_id}|${filter.status}`

  const fetchPage = useCallback(
    async (cursor: number | string | null) => {
      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac
      setLoading(true)
      setError(null)
      try {
        const params: Parameters<typeof api.videos>[0] = {
          group_id: filter.group_id,
          status: filter.status,
          limit: PAGE_SIZE,
        }
        if (cursor != null) params.cursor = cursor
        const body = await api.videos(params)
        if (ac.signal.aborted) return null
        return body as Page
      } catch (e) {
        if (!ac.signal.aborted) {
          setError(e instanceof Error ? e.message : String(e))
        }
        return null
      } finally {
        if (!ac.signal.aborted) setLoading(false)
      }
    },
    [filter.group_id, filter.status],
  )

  // Initial / on-filter-change fetch — replace all pages.
  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(() => {
      fetchPage(null).then((p) => {
        if (cancelled || p == null) return
        setPages([p])
        lastRefreshAtRef.current = Date.now()
      })
    }, 0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      abortRef.current?.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey])

  const loadMore = useCallback(async () => {
    const last = pages[pages.length - 1]
    if (!last || last.next_cursor == null || loading) return
    const p = await fetchPage(last.next_cursor)
    if (p) setPages((prev) => [...prev, p])
  }, [pages, loading, fetchPage])

  const refresh = useCallback(async () => {
    const p = await fetchPage(null)
    if (p) {
      setPages((prev) => (prev.length === 0 ? [p] : [p, ...prev.slice(1)]))
      lastRefreshAtRef.current = Date.now()
    }
  }, [fetchPage])

  // Optimistic mutation: remove a row across all loaded pages, decrement total.
  const removeLocally = useCallback((id: number) => {
    setPages((prev) =>
      prev.map((page, idx) => ({
        ...page,
        items: page.items.filter((r) => r.id !== id),
        total: idx === 0 && page.total != null
          ? Math.max(0, page.total - 1) : page.total,
      })),
    )
  }, [])

  // Patch a row in-place if currently loaded (e.g. after publish).
  const patchLocally = useCallback(
    (id: number, patch: Partial<VideoListRow>) => {
      setPages((prev) =>
        prev.map((page) => ({
          ...page,
          items: page.items.map((r) =>
            r.id === id ? { ...r, ...patch } : r),
        })),
      )
    },
    [],
  )

  // Visibility tickle: refresh on tab focus if > 30s since last refresh.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState !== 'visible') return
      if (Date.now() - lastRefreshAtRef.current < 30_000) return
      refresh()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [refresh])

  const items: VideoListRow[] = pages.flatMap((p) => p.items)
  const total = pages[0]?.total ?? null
  const hasMore = (pages[pages.length - 1]?.next_cursor ?? null) != null

  return { items, total, hasMore, loading, error, loadMore, refresh,
           removeLocally, patchLocally,
           summaryCounts: pages[0]?.summary_counts ?? null }
}
