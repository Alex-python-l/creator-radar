'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Platform } from './api'

type DetailPublication = {
  id: number
  platform: Platform
  account_id: number | null
  account_profile_name: string | null
  result: string
  caption: string | null
  notes: string | null
  fail_code: number | null
  fail_hint?: string | null
  fail_screenshot: string | null
  retry_count: number
  next_retry_at: string | null
  geelark_task_id: string | null
  posted_at: string | null
}

export type VideoDetail = {
  id: number
  original_filename: string | null
  uploaded_at: string | null
  group_id: number | null
  account_id: number | null
  account_profile_name: string | null
  publications: DetailPublication[]
}

// Session-lifetime cache — details for the same id don't change in a way
// the list view cares about between refresh ticks.
const cache = new Map<number, VideoDetail>()

export function useVideoDetail(id: number | null) {
  const [detail, setDetail] = useState<VideoDetail | null>(
    id != null ? cache.get(id) ?? null : null,
  )
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reqIdRef = useRef(0)

  const fetchOnce = useCallback(async (vid: number) => {
    const myReq = ++reqIdRef.current
    setLoading(true)
    setError(null)
    try {
      const body = (await api.videoDetail(vid)) as VideoDetail
      if (reqIdRef.current !== myReq) return
      cache.set(vid, body)
      setDetail(body)
    } catch (e) {
      if (reqIdRef.current !== myReq) return
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (reqIdRef.current === myReq) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (id == null) {
        setDetail(null)
        return
      }
      const cached = cache.get(id)
      if (cached) {
        setDetail(cached)
        return
      }
      setDetail(null)
      void fetchOnce(id)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [id, fetchOnce])

  const retry = useCallback(() => {
    if (id != null) fetchOnce(id)
  }, [id, fetchOnce])

  return { detail, loading, error, retry }
}
