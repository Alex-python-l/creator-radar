'use client'

import { useEffect, useRef } from 'react'

const KEY = (fk: string) => `videos:scrollY:${fk}`

function loadScroll(filterKey: string): number | null {
  try {
    const raw = sessionStorage.getItem(KEY(filterKey))
    if (raw == null) return null
    const n = parseFloat(raw)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

function saveScroll(filterKey: string, y: number) {
  try {
    sessionStorage.setItem(KEY(filterKey), String(y))
  } catch {
    // ignored
  }
}

/**
 * Restore window.scrollY for the current filterKey once the first page is
 * ready. Save on scroll / filterKey switch / unmount / beforeunload.
 */
export function useScrollRestore(filterKey: string, pageReady: boolean) {
  const filterKeyRef = useRef(filterKey)
  const restoredRef = useRef(false)

  useEffect(() => {
    if (filterKeyRef.current === filterKey) return
    saveScroll(filterKeyRef.current, window.scrollY)
    filterKeyRef.current = filterKey
    restoredRef.current = false
  }, [filterKey])

  useEffect(() => {
    if (!pageReady || restoredRef.current) return
    const y = loadScroll(filterKey)
    if (y != null) window.scrollTo(0, y)
    restoredRef.current = true
  }, [filterKey, pageReady])

  useEffect(() => {
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        saveScroll(filterKeyRef.current, window.scrollY)
      })
    }
    const onBeforeUnload = () =>
      saveScroll(filterKeyRef.current, window.scrollY)
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      if (raf) cancelAnimationFrame(raf)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('beforeunload', onBeforeUnload)
      saveScroll(filterKeyRef.current, window.scrollY)
    }
  }, [])
}
