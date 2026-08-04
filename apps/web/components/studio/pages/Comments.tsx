'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocale } from 'next-intl'
import { useStudioT } from '@/lib/studio/i18n'
import { MessageSquarePlus, RefreshCw } from 'lucide-react'
import {
  api,
  type EngagementRun,
  type EngagementRunResult,
  type EngagementRunsStats,
  type EngagementTaskStatus,
} from '@/lib/studio/api'
import { useStudioToast } from '@/components/studio/studio-providers'
import {
  Button,
  Card,
  EmptyState,
  FilterBar,
  PageHeader,
  SegmentedControl,
} from '@/components/ui'
import { CommentModal, type CommentSubmitPayload } from '@/components/studio/CommentModal'
import { cn } from '@/lib/studio/cn'
import { formatRelative, parseBackendIso } from '@/lib/studio/time'

type StatusKey = 'all' | 'success' | 'running' | 'queued' | 'failed'
type RecordSourceKey = 'owned' | 'manual'

const PAGE_SIZE = 50
function isInflight(record: EngagementRun) {
  const runInflight: EngagementRunResult[] = ['PENDING', 'QUEUED', 'INITIATED']
  const taskInflight: EngagementTaskStatus[] = ['PENDING', 'QUEUED', 'RUNNING']
  return (
    runInflight.includes(record.result) ||
    (record.task_status != null && taskInflight.includes(record.task_status))
  )
}

function classifyStatus(record: EngagementRun): {
  key: StatusKey
  accent: 'green' | 'blue' | 'orange' | 'red' | 'gray'
  labelKey: string
  spin: boolean
} {
  if (record.result === 'SUCCESS') return { key: 'success', accent: 'green', labelKey: 'comments.statusSuccess', spin: false }
  if (record.result === 'FAILURE') return { key: 'failed', accent: 'red', labelKey: 'comments.statusFailed', spin: false }
  if (record.result === 'INITIATED' || record.task_status === 'RUNNING')
    return { key: 'running', accent: 'blue', labelKey: 'comments.statusRunning', spin: true }
  if (record.result === 'QUEUED') return { key: 'queued', accent: 'orange', labelKey: 'comments.statusQueued', spin: false }
  if (record.result === 'PENDING') return { key: 'queued', accent: 'orange', labelKey: 'comments.statusPending', spin: false }
  return { key: 'queued', accent: 'gray', labelKey: 'comments.statusSkipped', spin: false }
}

function sourceOf(record: EngagementRun): RecordSourceKey {
  return record.task_source === 'publication_share_links' ? 'owned' : 'manual'
}

function matchesStatusFilter(record: EngagementRun, statusFilter: StatusKey) {
  return statusFilter === 'all' || classifyStatus(record).key === statusFilter
}

function activityKey(r: EngagementRun): number {
  // Mirrors the backend's COALESCE(finished_at, started_at, created_at)
  // so frontend dedupe-sort and backend pagination agree on row order.
  const raw = r.activity_at ?? r.finished_at ?? r.started_at ?? r.created_at
  const d = raw ? parseBackendIso(raw) : null
  return d ? d.getTime() : 0
}

function dedupeAndSortByActivityDesc(items: EngagementRun[]): EngagementRun[] {
  const seen = new Map<number, EngagementRun>()
  for (const item of items) seen.set(item.id, item)
  return Array.from(seen.values()).sort((a, b) => {
    const delta = activityKey(b) - activityKey(a)
    return delta !== 0 ? delta : b.id - a.id
  })
}

const ACCENT_BAR: Record<string, string> = {
  green: 'before:bg-[var(--color-green)]',
  blue: 'before:bg-[var(--color-blue)]',
  orange: 'before:bg-[var(--color-orange)]',
  red: 'before:bg-[var(--color-destructive)]',
  gray: 'before:bg-[var(--color-muted-foreground-3)]',
}
const STATUS_TEXT: Record<string, string> = {
  green: 'text-[var(--color-green)]',
  blue: 'text-[var(--color-blue)]',
  orange: 'text-[var(--color-orange)]',
  red: 'text-[var(--color-destructive)]',
  gray: 'text-[var(--color-muted-foreground-2)]',
}
const DOT_BG: Record<string, string> = {
  green: 'bg-[var(--color-green)]',
  blue: 'bg-[var(--color-blue)]',
  orange: 'bg-[var(--color-orange)]',
  red: 'bg-[var(--color-destructive)]',
  gray: 'bg-[var(--color-muted-foreground-3)]',
}

export default function Comments() {
  const onToast = useStudioToast()
  const t = useStudioT()
  const [records, setRecords] = useState<EngagementRun[]>([])
  const [stats, setStats] = useState<EngagementRunsStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusKey>('all')
  const [modalOpen, setModalOpen] = useState(false)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  const refreshStats = useCallback(() => {
    api
      .engagementRunsStats({ platform: 'tiktok' })
      .then(setStats)
      .catch(() => {})
  }, [])

  const loadFirstPage = useCallback(async () => {
    const [r] = await Promise.all([
      api.engagementRuns({
        limit: PAGE_SIZE,
        status: statusFilter,
        platform: 'tiktok',
      }),
      api
        .engagementRunsStats({ platform: 'tiktok' })
        .then(setStats)
        .catch(() => {}),
    ])
    const next = dedupeAndSortByActivityDesc(r.items)
    setRecords(next)
    setExhausted(r.items.length < PAGE_SIZE)
  }, [statusFilter])

  const loadMore = useCallback(async () => {
    if (loadingMore || exhausted || records.length === 0) return
    setLoadingMore(true)
    try {
      const tail = records[records.length - 1]
      // Compound (before_activity_at, before_id) cursor — must match the
      // backend's order_by (activity_at desc, id desc) so we don't skip
      // rows whose id > tail.id but activity_at < tail.activity_at.
      const beforeActivityAt =
        tail.activity_at ?? tail.finished_at ?? tail.started_at ?? tail.created_at
      const r = await api.engagementRuns({
        limit: PAGE_SIZE,
        beforeId: tail.id,
        beforeActivityAt: beforeActivityAt ?? undefined,
        status: statusFilter,
        platform: 'tiktok',
      })
      setRecords((prev) => dedupeAndSortByActivityDesc([...prev, ...r.items]))
      if (r.items.length < PAGE_SIZE) setExhausted(true)
    } finally {
      setLoadingMore(false)
    }
  }, [loadingMore, exhausted, records, statusFilter])

  // First fetch and refetch on filter change.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setLoading(true)
      setExhausted(false)
      loadFirstPage().finally(() => setLoading(false))
    }, 0)
    return () => window.clearTimeout(id)
  }, [loadFirstPage])

  // 5-second poll: refresh visible inflight rows by id, plus the latest page
  // for newly-created runs. Older loaded pages keep their live status.
  const inflightRunIds = useMemo(
    () => records.filter(isInflight).map((record) => record.id),
    [records],
  )
  const hasInflight = inflightRunIds.length > 0
  useEffect(() => {
    if (!hasInflight) return undefined
    let cancelled = false
    const timer = window.setInterval(() => {
      const idBatches: number[][] = []
      for (let i = 0; i < inflightRunIds.length; i += 500) {
        idBatches.push(inflightRunIds.slice(i, i + 500))
      }
      Promise.all([
        ...idBatches.map((ids) => api.engagementRuns({
          ids,
          limit: ids.length,
          platform: 'tiktok',
        })),
        api.engagementRuns({
          limit: PAGE_SIZE,
          platform: 'tiktok',
        }),
      ])
        .then((responses) => {
          if (cancelled) return
          const latest = responses[responses.length - 1] ?? { items: [] }
          const inflightItems = responses.slice(0, -1).flatMap((r) => r.items)
          setRecords((prev) =>
            dedupeAndSortByActivityDesc([...prev, ...latest.items, ...inflightItems]).filter(
              (record) => matchesStatusFilter(record, statusFilter),
            ),
          )
        })
        .catch(() => {})
      refreshStats()
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [hasInflight, inflightRunIds, statusFilter, refreshStats])

  // IntersectionObserver: load next page when sentinel comes into view.
  useEffect(() => {
    const node = sentinelRef.current
    if (!node) return undefined
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore()
      },
      { rootMargin: '400px' },
    )
    obs.observe(node)
    return () => obs.disconnect()
  }, [loadMore])

  const localCounts = useMemo(() => {
    const out = { total: records.length, success: 0, running: 0, queued: 0, failed: 0 }
    for (const record of records) {
      const key = classifyStatus(record).key
      if (key !== 'all') out[key] += 1
    }
    return out
  }, [records])

  const counts = stats ?? localCounts

  const refresh = () => {
    setLoading(true)
    setExhausted(false)
    loadFirstPage().finally(() => setLoading(false))
  }

  const onSubmit = async (payload: CommentSubmitPayload) => {
    let created: Awaited<ReturnType<typeof api.createEngagementComment>>
    try {
      created = await api.createEngagementComment(payload)
    } catch (e) {
      // Only a genuine create failure reaches here.
      const msg = e instanceof Error && e.message ? e.message : t('comments.failed')
      onToast({ msg, tone: 'red' })
      return
    }
    onToast({
      msg: t('comments.created', { count: created.created_runs ?? 0 }),
      tone: 'green',
    })
    setModalOpen(false)
    // The runs ARE created; a reload hiccup must not be reported as a
    // create failure, and must not strand the list in a Loading card.
    setLoading(true)
    setExhausted(false)
    loadFirstPage().finally(() => setLoading(false))
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow={t('comments.eyebrow')}
        title={t('comments.title')}
        actions={
          <>
            <Button variant="secondary" onClick={refresh}>
              <RefreshCw size={14} />
              {t('common.refresh')}
            </Button>
            <Button variant="primary" onClick={() => setModalOpen(true)}>
              <MessageSquarePlus size={14} />
              {t('comments.newComment')}
            </Button>
          </>
        }
      />

      <FilterBar>
        <SegmentedControl
          value={statusFilter}
          ariaLabel={t('comments.filterStatus')}
          onChange={(v) => setStatusFilter(v)}
          options={[
            { value: 'all', label: t('comments.filterAll'), count: counts.total },
            { value: 'success', label: t('comments.filterSuccess'), count: counts.success },
            { value: 'running', label: t('comments.filterRunning'), count: counts.running },
            { value: 'queued', label: t('comments.filterQueued'), count: counts.queued },
            { value: 'failed', label: t('comments.filterFailed'), count: counts.failed },
          ]}
        />
      </FilterBar>

      {loading ? (
        <Card><EmptyState title={t('common.loading')} /></Card>
      ) : records.length === 0 ? (
        <Card><EmptyState title={t('comments.recordsEmpty')} /></Card>
      ) : (
        <div className="flex flex-col gap-2.5">
          {records.map((r) => (
            <RecordCard key={r.id} record={r} />
          ))}
          <div ref={sentinelRef} className="h-px" />
          {loadingMore ? (
            <div className="py-3 text-center text-[12.5px] text-[var(--color-muted-foreground-2)]">
              {t('comments.loadingMore')}
            </div>
          ) : exhausted ? (
            <div className="py-3 text-center text-[12.5px] text-[var(--color-muted-foreground-3)]">
              {t('comments.noMore')}
            </div>
          ) : null}
        </div>
      )}

      <CommentModal open={modalOpen} onClose={() => setModalOpen(false)} onSubmit={onSubmit} />
    </div>
  )
}

function RecordCard({ record }: { record: EngagementRun }) {
  const t = useStudioT()
  const locale = useLocale()
  const stat = classifyStatus(record)
  const isFail = stat.key === 'failed'
  const isManual = sourceOf(record) === 'manual'
  return (
    <article
      className={cn(
        'relative overflow-hidden bg-[var(--color-card)] border border-[var(--color-border)] rounded-[var(--radius-card)]',
        'shadow-[var(--shadow-card-sm)] px-4 py-3',
        'before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[3px]',
        ACCENT_BAR[stat.accent],
      )}
    >
      {!isFail && record.comment_text ? (
        <div className="text-[13.5px] leading-[1.5] text-[var(--color-foreground)] max-w-[92ch]">
          <span className="text-[var(--color-muted-foreground-3)]">{'"'}</span>
          {record.comment_text}
          <span className="text-[var(--color-muted-foreground-3)]">{'"'}</span>
        </div>
      ) : isFail ? (
        <div className="italic text-[14px] text-[var(--color-muted-foreground-2)]">
          {record.notes ?? t('comments.statusFailed')}
        </div>
      ) : (
        <div className="italic text-[14px] text-[var(--color-muted-foreground-2)]">
          {t('comments.statusPending')}…
        </div>
      )}

      <div className="h-px bg-[var(--color-border)] mt-3 mb-2.5" />

      <div className="flex items-center gap-2 text-[12.5px] text-[var(--color-muted-foreground)]">
        <span className={cn('inline-flex items-center gap-1.5 font-medium', STATUS_TEXT[stat.accent])}>
          <span className={cn('w-[7px] h-[7px] rounded-full', DOT_BG[stat.accent], stat.spin && 'animate-pulse')} />
          {t(stat.labelKey)}
        </span>
        <span className="text-[var(--color-muted-foreground-3)]">·</span>
        <span className="font-medium text-[var(--color-foreground-2)]">
          {record.account_profile_name ?? `#${record.account_id ?? '—'}`}
        </span>
        <span className="text-[var(--color-muted-foreground-3)]">·</span>
        <span className="text-[var(--color-muted-foreground-2)]">
          {formatRelative(
            record.activity_at ??
              record.finished_at ??
              record.started_at ??
              record.created_at ??
              record.task_created_at ??
              null,
            locale,
          )}
        </span>
      </div>

      <div className="mt-2.5 flex items-baseline gap-2.5 text-[13px] min-w-0">
        {!isManual && record.target_title ? (
          <span className="text-[var(--color-foreground-2)] font-medium truncate max-w-[55%]">{record.target_title}</span>
        ) : null}
        <a
          href={record.target_link}
          target="_blank"
          rel="noreferrer"
          className="flex-1 min-w-0 text-[12.5px] text-[var(--color-blue)] truncate hover:underline"
        >
          {record.target_link}
        </a>
        <a
          href={record.target_link}
          target="_blank"
          rel="noreferrer"
          className="flex-shrink-0 text-[var(--color-muted-foreground-2)] hover:text-[var(--color-blue)] hover:bg-[var(--color-blue-bg)] rounded-[6px] px-1.5 py-0.5 text-[13px]"
          aria-label="open"
        >
          ↗
        </a>
      </div>
    </article>
  )
}
