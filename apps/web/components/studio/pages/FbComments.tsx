'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocale } from 'next-intl'
import { useStudioT } from '@/lib/studio/i18n'
import {
  api,
  type EngagementRun,
  type EngagementRunResult,
  type EngagementTaskStatus,
  type FbCommentSubmitPayload,
} from '@/lib/studio/api'
import { useStudioToast } from '@/components/studio/studio-providers'
import { Button, Card, EmptyState, PageTitle } from '@/components/ui'
import { FbCommentModal } from '@/components/studio/FbCommentModal'
import { cn } from '@/lib/studio/cn'
import { formatRelative, parseBackendIso } from '@/lib/studio/time'

type StatusKey = 'all' | 'success' | 'running' | 'queued' | 'failed'

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
  if (record.result === 'SUCCESS') return { key: 'success', accent: 'green', labelKey: 'fbComments.statusSuccess', spin: false }
  if (record.result === 'FAILURE') return { key: 'failed', accent: 'red', labelKey: 'fbComments.statusFailed', spin: false }
  if (record.result === 'INITIATED' || record.task_status === 'RUNNING')
    return { key: 'running', accent: 'blue', labelKey: 'fbComments.statusRunning', spin: true }
  if (record.result === 'QUEUED') return { key: 'queued', accent: 'orange', labelKey: 'fbComments.statusQueued', spin: false }
  if (record.result === 'PENDING') return { key: 'queued', accent: 'orange', labelKey: 'fbComments.statusPending', spin: false }
  return { key: 'queued', accent: 'gray', labelKey: 'fbComments.statusSkipped', spin: false }
}

function matchesStatus(record: EngagementRun, statusFilter: StatusKey) {
  return statusFilter === 'all' || classifyStatus(record).key === statusFilter
}

function activityKey(r: EngagementRun): number {
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

export default function FbComments() {
  const onToast = useStudioToast()
  const t = useStudioT()
  const [records, setRecords] = useState<EngagementRun[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusKey>('all')
  const [modalOpen, setModalOpen] = useState(false)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  const loadFirstPage = useCallback(async () => {
    const r = await api.engagementRuns({
      limit: PAGE_SIZE,
      status: statusFilter,
      platform: 'facebook',
    })
    const next = dedupeAndSortByActivityDesc(r.items)
    setRecords(next)
    setExhausted(r.items.length < PAGE_SIZE)
  }, [statusFilter])

  const loadMore = useCallback(async () => {
    if (loadingMore || exhausted || records.length === 0) return
    setLoadingMore(true)
    try {
      const tail = records[records.length - 1]
      const beforeActivityAt =
        tail.activity_at ?? tail.finished_at ?? tail.started_at ?? tail.created_at
      const r = await api.engagementRuns({
        limit: PAGE_SIZE,
        beforeId: tail.id,
        beforeActivityAt: beforeActivityAt ?? undefined,
        status: statusFilter,
        platform: 'facebook',
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
          platform: 'facebook',
        })),
        api.engagementRuns({
          limit: PAGE_SIZE,
          platform: 'facebook',
        }),
      ])
        .then((responses) => {
          if (cancelled) return
          const latest = responses[responses.length - 1] ?? { items: [] }
          const inflightItems = responses.slice(0, -1).flatMap((r) => r.items)
          setRecords((prev) =>
            dedupeAndSortByActivityDesc([...prev, ...latest.items, ...inflightItems]).filter(
              (record) => matchesStatus(record, statusFilter),
            ),
          )
        })
        .catch(() => {})
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [hasInflight, inflightRunIds, statusFilter])

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

  const onSubmit = async (payload: FbCommentSubmitPayload) => {
    let created: Awaited<ReturnType<typeof api.createFbEngagementComment>>
    try {
      created = await api.createFbEngagementComment(payload)
    } catch (e) {
      const msg = e instanceof Error && e.message ? e.message : t('fbComments.failed')
      onToast({ msg, tone: 'red' })
      return
    }
    onToast({
      msg: t('fbComments.created', { count: created.created_runs ?? 0 }),
      tone: 'green',
    })
    setModalOpen(false)
    // Runs created; a reload hiccup must not read as a create failure or
    // strand the list in a Loading card.
    setLoading(true)
    setExhausted(false)
    loadFirstPage().finally(() => setLoading(false))
  }

  return (
    <div className="flex flex-col gap-6">
      <PageTitle
        title={t('fbComments.title')}
        right={
          <Button variant="primary" onClick={() => setModalOpen(true)}>
            + {t('fbComments.newComment')}
          </Button>
        }
      />

      <div className="flex items-center gap-2.5">
        <FilterMenu
          label={t('fbComments.filterStatus')}
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as StatusKey)}
          options={[
            { value: 'all', label: t('fbComments.filterAll') },
            { value: 'success', label: t('fbComments.filterSuccess') },
            { value: 'running', label: t('fbComments.filterRunning') },
            { value: 'queued', label: t('fbComments.filterQueued') },
            { value: 'failed', label: t('fbComments.filterFailed') },
          ]}
        />
        <div className="flex-1" />
        <span className="text-[12px] text-[var(--color-muted-foreground)] inline-flex items-center gap-1.5">
          <span
            className={cn(
              'w-[7px] h-[7px] rounded-full',
              hasInflight ? 'bg-[var(--color-green)] animate-pulse' : 'bg-[var(--color-muted-foreground-3)]',
            )}
          />
          {t('fbComments.autoRefresh')}
        </span>
      </div>

      {loading ? (
        <Card><EmptyState title={t('common.loading')} /></Card>
      ) : records.length === 0 ? (
        <Card><EmptyState title={t('fbComments.recordsEmpty')} /></Card>
      ) : (
        <div className="flex flex-col gap-2.5">
          {records.map((r) => (
            <FbRecordCard key={r.id} record={r} />
          ))}
          <div ref={sentinelRef} className="h-px" />
          {loadingMore ? (
            <div className="py-3 text-center text-[12.5px] text-[var(--color-muted-foreground-2)]">
              {t('fbComments.loadingMore')}
            </div>
          ) : exhausted ? (
            <div className="py-3 text-center text-[12.5px] text-[var(--color-muted-foreground-3)]">
              {t('fbComments.noMore')}
            </div>
          ) : null}
        </div>
      )}

      <FbCommentModal open={modalOpen} onClose={() => setModalOpen(false)} onSubmit={onSubmit} />
    </div>
  )
}

function FilterMenu({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (v: string) => void
}) {
  return (
    <label className="inline-flex items-center gap-1.5 h-8 px-3 border border-[var(--color-border)] rounded-[8px] bg-[var(--color-card)] text-[13px] text-[var(--color-foreground-2)]">
      <span className="text-[var(--color-muted-foreground-2)]">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-[var(--color-foreground)] font-medium focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  )
}

function FbRecordCard({ record }: { record: EngagementRun }) {
  const t = useStudioT()
  const locale = useLocale()
  const stat = classifyStatus(record)
  const isFail = stat.key === 'failed'
  // `comment_candidates` lives on the backend model but is not yet typed on
  // the EngagementRun TS surface — we read it defensively to surface
  // alternative drafts under the chosen comment.
  const candidates = (record as unknown as { comment_candidates?: string[] | null }).comment_candidates
  const extraCount = Array.isArray(candidates) && candidates.length >= 2 ? candidates.length - 1 : 0
  return (
    <article
      className={cn(
        'relative overflow-hidden bg-[var(--color-card)] border border-[var(--color-border)] rounded-[var(--radius-card)]',
        'shadow-[0_1px_2px_rgba(0,0,0,0.03)] px-[26px] pt-[22px] pb-[18px]',
        'before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[3px]',
        ACCENT_BAR[stat.accent],
      )}
    >
      {!isFail && record.comment_text ? (
        <div className="font-[family-name:var(--font-serif)] text-[18px] leading-[1.45] tracking-[-0.005em] text-[var(--color-foreground)] max-w-[65ch]">
          <span className="text-[var(--color-muted-foreground-3)]">{'"'}</span>
          {record.comment_text}
          <span className="text-[var(--color-muted-foreground-3)]">{'"'}</span>
        </div>
      ) : isFail ? (
        <div className="italic text-[14px] text-[var(--color-muted-foreground-2)]">
          {record.notes ?? t('fbComments.statusFailed')}
        </div>
      ) : (
        <div className="italic text-[14px] text-[var(--color-muted-foreground-2)]">
          {t('fbComments.statusPending')}…
        </div>
      )}

      {extraCount > 0 && !isFail ? (
        <div className="mt-1.5 text-[12px] text-[var(--color-muted-foreground-2)]">
          +{extraCount} {t('fbComments.candidatesExtra')}
        </div>
      ) : null}

      <div className="h-px bg-[var(--color-border)] mt-4 mb-3" />

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
        {record.target_title ? (
          <span className="text-[var(--color-foreground-2)] font-medium truncate max-w-[55%]">{record.target_title}</span>
        ) : (
          <span className="text-[var(--color-muted-foreground-2)] text-[12.5px]">{t('fbComments.targetIsPost')}</span>
        )}
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
