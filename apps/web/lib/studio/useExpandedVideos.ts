'use client'

import { useCallback, useEffect, useState } from 'react'

export function useExpandedVideos(filterKey: string) {
  const [ids, setIds] = useState<Set<number>>(() => new Set())

  useEffect(() => {
    const id = window.setTimeout(() => setIds(new Set()), 0)
    return () => window.clearTimeout(id)
  }, [filterKey])

  const toggle = useCallback((id: number) => {
    setIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const collapseAll = useCallback(() => {
    setIds(new Set())
  }, [])

  return { expandedIds: ids, toggle, collapseAll }
}
