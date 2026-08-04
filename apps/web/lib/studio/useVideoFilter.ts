'use client'

import { useCallback, useMemo } from 'react'

import { useUrlSearchParams } from './use-url-search-params'

export type GroupSel = number | 'all' | 'ungrouped'
export type VideoStatusFilter = 'all' | 'pending' | 'in_progress' | 'handled'
export type VideoFilter = { group_id: GroupSel; status: VideoStatusFilter }

function parseGroup(raw: string | null): GroupSel {
  if (raw == null || raw === 'all') return 'all'
  if (raw === 'ungrouped') return 'ungrouped'
  const n = parseInt(raw, 10)
  return Number.isFinite(n) ? n : 'all'
}

function parseStatus(raw: string | null): VideoStatusFilter {
  if (
    raw === 'pending' ||
    raw === 'in_progress' ||
    raw === 'handled'
  ) return raw
  return 'all'
}

export function useVideoFilter() {
  const [sp, setSp] = useUrlSearchParams()
  const filter: VideoFilter = useMemo(
    () => ({
      group_id: parseGroup(sp.get('group')),
      status: parseStatus(sp.get('status')),
    }),
    [sp],
  )
  const filterKey = `${filter.group_id}|${filter.status}`

  const setFilter = useCallback(
    (next: Partial<VideoFilter>) => {
      setSp(
        (prev) => {
          const out = new URLSearchParams(prev)
          const g = next.group_id ?? filter.group_id
          const s = next.status ?? filter.status
          if (g === 'all') out.delete('group')
          else out.set('group', String(g))
          if (s === 'all') out.delete('status')
          else out.set('status', s)
          return out
        },
        { replace: true },
      )
    },
    [setSp, filter.group_id, filter.status],
  )

  return { filter, filterKey, setFilter }
}
