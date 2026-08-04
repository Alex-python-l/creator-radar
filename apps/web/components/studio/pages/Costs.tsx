'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type CostAttribution,
  type CostKpis,
  type CostSummaryGroup,
  type CostTransaction,
} from '@/lib/studio/api'
import {
  Badge,
  Card,
  EmptyState,
  PageTitle,
  StatCard,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from '@/components/ui'
import { cn } from '@/lib/studio/cn'

type KindFilter = 'all' | CostAttribution['kind']

const KIND_LABEL: Record<CostAttribution['kind'], string> = {
  publish: 'Publish',
  warmup: 'Warmup',
  engagement: 'Engagement',
  idle: 'Unattributed',
}

const KIND_BADGE_TONE: Record<CostAttribution['kind'],
  'blue' | 'green' | 'orange' | 'red' | 'gray'> = {
  publish: 'blue',
  warmup: 'green',
  engagement: 'orange',
  idle: 'red',
}

const KIND_CHART_COLOR: Record<CostAttribution['kind'], string> = {
  publish: '#2563eb',
  warmup: '#16a34a',
  engagement: '#f59e0b',
  idle: '#dc2626',
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function defaultDates(): { from: string; to: string } {
  const now = new Date()
  const to = new Date(now)
  to.setUTCDate(to.getUTCDate() + 1)
  const from = new Date(now)
  from.setUTCDate(from.getUTCDate() - 6)
  return { from: isoDate(from), to: isoDate(to) }
}

function fmtUsd(n: number): string {
  // costs are negative; show as positive in the cell, drop the minus.
  const abs = Math.abs(n)
  return `$${abs.toFixed(abs < 1 ? 4 : 2)}`
}

// GeeLark reports a chargeType=8 row whenever a phone consumes "free
// tier" minutes (gift balance / promotional allowance); these rows
// have amount=0 but still record usedTime. They aren't real spend.
function isFreeMinutes(amount: number): boolean {
  return amount === 0
}

function fmtTime(iso: string): string {
  // Server emits `<utc>Z`. Show local time in compact form.
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  if (!Number.isFinite(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const PAGE_SIZE = 100

function isCostKind(value: string): value is CostAttribution['kind'] {
  return value in KIND_LABEL
}

function costMix(groups: CostSummaryGroup[]) {
  return groups
    .filter((g) => isCostKind(g.key))
    .map((g) => ({
      kind: g.key as CostAttribution['kind'],
      amount: Math.abs(g.total),
      count: g.count,
    }))
    .filter((g) => g.amount > 0)
    .sort((a, b) => b.amount - a.amount)
}

function CostMixCard({
  groups,
  loading,
}: {
  groups: CostSummaryGroup[]
  loading: boolean
}) {
  const slices = costMix(groups)
  const total = slices.reduce((sum, s) => sum + s.amount, 0)
  const visibleSlices = total > 0 ? slices : []
  const radius = 42
  const circumference = 2 * Math.PI * radius
  let offset = 0

  return (
    <Card className="p-4 min-h-[182px]">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-[12.5px] text-[var(--color-muted-foreground)] font-medium">
            Spend mix
          </div>
          <div className="mt-2 text-[24px] leading-none font-semibold tabular-nums">
            {total > 0 ? fmtUsd(total) : '—'}
          </div>
          <div className="mt-2 text-[12.5px] text-[var(--color-muted-foreground)]">
            Selected date range
          </div>
        </div>
        <div className="relative h-[132px] w-[132px] shrink-0">
          <svg viewBox="0 0 120 120" className="h-full w-full">
            <circle
              cx="60"
              cy="60"
              r={radius}
              fill="none"
              stroke="var(--color-gray-bg)"
              strokeWidth="18"
            />
            {total > 0 ? slices.map((slice) => {
              const length = (slice.amount / total) * circumference
              const dash = `${length} ${circumference - length}`
              const segment = (
                <circle
                  key={slice.kind}
                  cx="60"
                  cy="60"
                  r={radius}
                  fill="none"
                  stroke={KIND_CHART_COLOR[slice.kind]}
                  strokeWidth="18"
                  strokeDasharray={dash}
                  strokeDashoffset={-offset}
                  transform="rotate(-90 60 60)"
                />
              )
              offset += length
              return segment
            }) : null}
          </svg>
          <div className="absolute inset-0 flex items-center justify-center text-[12px] font-medium text-[var(--color-muted-foreground)]">
            {loading ? '…' : total > 0 ? '100%' : 'No spend'}
          </div>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2">
        {visibleSlices.map((slice) => {
          const pct = total > 0 ? (slice.amount / total) * 100 : 0
          return (
            <div key={slice.kind} className="flex items-center gap-2 min-w-0">
              <span
                className="h-2.5 w-2.5 rounded-full shrink-0"
                style={{ background: KIND_CHART_COLOR[slice.kind] }}
              />
              <div className="min-w-0 flex-1 text-[12.5px] text-[var(--color-foreground-2)] truncate">
                {KIND_LABEL[slice.kind]}
              </div>
              <div className="text-[12.5px] tabular-nums text-[var(--color-muted-foreground)]">
                {pct.toFixed(1)}%
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

export default function Costs() {
  const [{ from, to }, setRange] = useState(defaultDates)
  const [kind, setKind] = useState<KindFilter>('all')

  const [kpis, setKpis] = useState<CostKpis | null>(null)
  const [summaryGroups, setSummaryGroups] = useState<CostSummaryGroup[]>([])
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [items, setItems] = useState<CostTransaction[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Infinite-scroll state. `nextPage` is the page to request next;
  // `done` flips true once the server returns fewer rows than asked,
  // stopping the observer from firing again.
  const [nextPage, setNextPage] = useState(1)
  const [done, setDone] = useState(false)
  const inFlight = useRef(false)
  // Monotonic request id: a filter change supersedes any in-flight load,
  // and only the newest request may write state. Without it, switching
  // Kind while "load more" was running dropped the new filter's page-1
  // request (inFlight early-return) and appended the old filter's late
  // page onto the cleared list.
  const requestSeq = useRef(0)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    api.costKpis().then(setKpis).catch(() => setKpis(null))
  }, [])

  useEffect(() => {
    const id = window.setTimeout(() => {
      setSummaryLoading(true)
      api.costSummary({
        group_by: 'kind',
        date_from: from || null,
        date_to: to || null,
      }).then((r) => {
        setSummaryGroups(r.groups)
      }).catch(() => {
        setSummaryGroups([])
      }).finally(() => {
        setSummaryLoading(false)
      })
    }, 0)
    return () => window.clearTimeout(id)
  }, [from, to])

  const loadPage = useCallback(
    async (pageToLoad: number, replace: boolean) => {
      // A "load more" (append) de-dupes against an in-flight request, but
      // a filter/first-page load must always fire and supersede whatever
      // is running — otherwise the new filter's request gets dropped.
      if (!replace && inFlight.current) return
      const reqId = ++requestSeq.current
      inFlight.current = true
      setLoading(true)
      setError(null)
      try {
        const r = await api.costTransactions({
          page: pageToLoad,
          page_size: PAGE_SIZE,
          kind: kind === 'all' ? null : kind,
          date_from: from || null,
          date_to: to || null,
        })
        if (reqId !== requestSeq.current) return  // superseded — drop
        setTotal(r.total)
        setItems((prev) => (replace ? r.items : [...prev, ...r.items]))
        if (r.items.length < PAGE_SIZE) setDone(true)
        else setNextPage(pageToLoad + 1)
      } catch (e) {
        if (reqId !== requestSeq.current) return
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        // Only the newest request owns the shared loading/inFlight flags;
        // a late stale response must not clear them from under it.
        if (reqId === requestSeq.current) {
          setLoading(false)
          inFlight.current = false
        }
      }
    },
    [kind, from, to],
  )

  // Filter change → reset and refetch from page 1.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setItems([])
      setTotal(0)
      setDone(false)
      setNextPage(1)
      loadPage(1, true)
    }, 0)
    return () => window.clearTimeout(id)
  }, [kind, from, to, loadPage])

  // Scroll-to-load-more. We listen to the Shell's <main> (the actual
  // overflow-auto element) and fire when the user is within 400px of
  // the bottom. Plain scroll listener is more reliable than
  // IntersectionObserver across nested scroll contexts.
  useEffect(() => {
    const node = sentinelRef.current
    if (!node || done) return
    const root = node.closest('main') as HTMLElement | null
    if (!root) return

    const maybeLoad = () => {
      if (inFlight.current || done) return
      const distFromBottom =
        root.scrollHeight - root.scrollTop - root.clientHeight
      if (distFromBottom < 400) loadPage(nextPage, false)
    }
    // Fire once on mount in case the list is already short enough to
    // sit fully in view.
    maybeLoad()
    root.addEventListener('scroll', maybeLoad, { passive: true })
    return () => root.removeEventListener('scroll', maybeLoad)
    // items.length is in deps so the listener re-attaches the moment
    // the sentinel first mounts (initial render has items=[] and no
    // sentinel DOM node yet).
  }, [nextPage, done, loadPage, items.length])

  const exportHref = api.costExportCsvUrl({
    date_from: from || null,
    date_to: to || null,
  })

  return (
    <div className="space-y-6">
      <PageTitle
        title="Cost audit"
      />

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_360px] gap-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            label="Today"
            value={kpis ? fmtUsd(kpis.today_spent) : '—'}
            caption="UTC day"
          />
          <StatCard
            label="Month-to-date"
            value={kpis ? fmtUsd(kpis.mtd_spent) : '—'}
          />
          <StatCard
            label="Avg / day (30d)"
            value={kpis ? fmtUsd(kpis.day_avg_30d) : '—'}
          />
          <StatCard
            label="Unattributed (30d)"
            value={kpis ? `${kpis.idle_30d_pct.toFixed(1)}%` : '—'}
            tone={kpis && kpis.idle_30d_pct > 10 ? 'red' : 'gray'}
            caption="Phone-on time with no matching task"
          />
        </div>
        <CostMixCard groups={summaryGroups} loading={summaryLoading} />
      </div>

      <Card className="p-4 space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-[12px] text-[var(--color-muted-foreground)] mb-1">
              From
            </label>
            <input
              type="date"
              value={from}
              onChange={(e) => {
                setRange((r) => ({ ...r, from: e.target.value }))
              }}
              className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[13px]"
            />
          </div>
          <div>
            <label className="block text-[12px] text-[var(--color-muted-foreground)] mb-1">
              To
            </label>
            <input
              type="date"
              value={to}
              onChange={(e) => {
                setRange((r) => ({ ...r, to: e.target.value }))
              }}
              className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[13px]"
            />
          </div>
          <div>
            <label className="block text-[12px] text-[var(--color-muted-foreground)] mb-1">
              Kind
            </label>
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as KindFilter)
              }}
              className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[13px]"
            >
              <option value="all">All</option>
              <option value="publish">Publish</option>
              <option value="warmup">Warmup</option>
              <option value="engagement">Engagement</option>
              <option value="idle">Unattributed</option>
            </select>
          </div>
          <div className="ml-auto">
            <a
              href={exportHref}
              className="inline-flex items-center h-[34px] px-3.5 text-[13px] rounded-[8px] border border-[var(--color-border)] bg-[var(--color-card)] hover:bg-[var(--color-muted)]"
            >
              Export CSV
            </a>
          </div>
        </div>

        {error ? (
          <div className="text-[13px] text-[var(--color-destructive)] py-3">
            {error}
          </div>
        ) : loading && items.length === 0 ? (
          <div className="text-[13px] text-[var(--color-muted-foreground)] py-6">
            Loading…
          </div>
        ) : items.length === 0 ? (
          <EmptyState title="No transactions">
            No GeeLark charges fall within the selected range. The sync runs
            every 15 minutes.
          </EmptyState>
        ) : (
          <>
            <Table>
              <THead>
                <Tr>
                  <Th>Time</Th>
                  <Th>Account</Th>
                  <Th>Group</Th>
                  <Th>Attribution</Th>
                  <Th align="right">Used</Th>
                  <Th align="right">Amount</Th>
                </Tr>
              </THead>
              <TBody>
                {items.map((it) => {
                  const k = it.attribution.kind
                  const isIdle = k === 'idle'
                  const isFree = isFreeMinutes(it.amount_usd)
                  return (
                    <Tr key={it.id}>
                      <Td className="text-[var(--color-foreground-2)] whitespace-nowrap">
                        {fmtTime(it.time)}
                      </Td>
                      <Td className="text-[var(--color-foreground-2)]">
                        {it.account?.profile_name ? (
                          <div
                            className="max-w-[260px] truncate"
                            title={it.account.profile_name}
                          >
                            {it.account.profile_name}
                          </div>
                        ) : (
                          <span className="block max-w-[180px] truncate text-[var(--color-muted-foreground-2)]">
                            (env {it.env_id.slice(-6)})
                          </span>
                        )}
                      </Td>
                      <Td className="text-[var(--color-muted-foreground)]">
                        {it.group?.name ? (
                          <div
                            className="max-w-[220px] truncate"
                            title={it.group.name}
                          >
                            {it.group.name}
                          </div>
                        ) : '—'}
                      </Td>
                      <Td>
                        <div
                          className="flex items-center gap-2"
                          title={it.attribution.reason ?? undefined}
                        >
                          <Badge tone={KIND_BADGE_TONE[k]}>{KIND_LABEL[k]}</Badge>
                          {!isIdle && it.attribution.label && (
                            <span
                              className="max-w-[260px] truncate text-[12.5px] text-[var(--color-muted-foreground)]"
                              title={it.attribution.label}
                            >
                              {it.attribution.label}
                            </span>
                          )}
                          {it.attribution.confidence === 'medium' && !isIdle && (
                            <span className="text-[12px] text-[var(--color-muted-foreground-2)] cursor-help">
                              ~
                            </span>
                          )}
                        </div>
                      </Td>
                      <Td
                        align="right"
                        className="tabular-nums text-[var(--color-muted-foreground)]"
                      >
                        {it.used_min}m
                      </Td>
                      <Td
                        align="right"
                        className={cn(
                          'tabular-nums',
                          isFree
                            ? 'text-[var(--color-muted-foreground-2)]'
                            : isIdle
                              ? 'text-[var(--color-destructive)]'
                              : 'text-[var(--color-foreground)]',
                        )}
                      >
                        <span title={isFree ? 'Free-tier minutes (no charge)' : undefined}>
                          {isFree ? 'Free' : fmtUsd(it.amount_usd)}
                        </span>
                      </Td>
                    </Tr>
                  )
                })}
              </TBody>
            </Table>

            <div
              ref={sentinelRef}
              className="flex items-center justify-between text-[12.5px] text-[var(--color-muted-foreground)] pt-3"
            >
              <div>
                {items.length} / {total} loaded ·{' '}
                {fmtUsd(items.reduce((s, x) => s + x.amount_usd, 0))} total
              </div>
              <div>
                {loading
                  ? 'Loading…'
                  : done
                    ? 'All loaded'
                    : 'Scroll to load more'}
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
