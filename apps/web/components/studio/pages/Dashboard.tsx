'use client'

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { useRouter } from 'next/navigation'

import { useStudioHref } from '@/lib/studio/nav'
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
  RefreshCw,
  Server,
  Sparkles,
  UploadCloud,
  Users,
} from 'lucide-react'
import { api, type DashboardData, type DashboardException, type HealthData } from '@/lib/studio/api'
import {
  Badge,
  Button,
  EmptyState,
  MetricCard,
  PageHeader,
  Panel,
  ProgressBar,
  Toolbar,
} from '@/components/ui'
import { cn } from '@/lib/studio/cn'

const SEVERITY_DOT: Record<DashboardException['severity'], string> = {
  red: 'bg-[var(--color-destructive)]',
  orange: 'bg-[var(--color-orange)]',
  yellow: 'bg-[var(--color-yellow)]',
}

const KIND_LABEL_KEY: Record<DashboardException['kind'], string> = {
  publication_failed: 'dashboard.exception.kind.publicationFailed',
  proxy_down: 'dashboard.exception.kind.proxyDown',
  not_logged_in: 'dashboard.exception.kind.notLoggedIn',
  warmup_stuck: 'dashboard.exception.kind.warmupStuck',
  proxy_expiring: 'dashboard.exception.kind.proxyExpiring',
}

const KIND_TARGET: Record<DashboardException['kind'], string> = {
  publication_failed: '/videos?status=handled',
  proxy_down: '/accounts',
  not_logged_in: '/accounts',
  warmup_stuck: '/accounts',
  proxy_expiring: '/accounts?proxy_expiring=1',
}

const EMPTY_DATABASE_DETAIL: DashboardData['database'] = {
  query_ms: null,
  size_bytes: null,
  connections: null,
  active_queries: null,
  locks: null,
  tables: null,
}

function workerAge(health: HealthData | null): number | null {
  if (!health?.worker_last_seen) return null
  return Math.max(0, Math.floor(Date.now() / 1000 - health.worker_last_seen))
}

function isFiniteMetric(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function formatMetricCount(value: number | null | undefined): string {
  return isFiniteMetric(value) ? String(value) : '—'
}

function formatLatencyValue(value: number | null | undefined): string {
  if (!isFiniteMetric(value)) return '—'
  return value < 10 ? value.toFixed(1) : String(Math.round(value))
}

function formatBytes(value: number | null | undefined): string {
  if (!isFiniteMetric(value) || value <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  const precision = unitIndex === 0 || size >= 10 ? 0 : 1
  return `${size.toFixed(precision)} ${units[unitIndex]}`
}

function databaseBadge(database: DashboardData['database']): {
  tone: 'green' | 'orange' | 'gray'
  labelKey: string
} {
  if (!isFiniteMetric(database.query_ms)) {
    return { tone: 'gray', labelKey: 'dashboard.database.unknown' }
  }
  if (
    database.query_ms > 250 ||
    (isFiniteMetric(database.active_queries) && database.active_queries > 5) ||
    (isFiniteMetric(database.locks) && database.locks > 50)
  ) {
    return { tone: 'orange', labelKey: 'dashboard.database.watch' }
  }
  return { tone: 'green', labelKey: 'dashboard.database.healthy' }
}

export default function Dashboard() {
  const t = useStudioT()
  const router = useRouter()
  const studioHref = useStudioHref()
  const navigate = (path: string) => router.push(studioHref(path))
  const [d, setD] = useState<DashboardData | null>(null)
  const [health, setHealth] = useState<HealthData | null>(null)
  const [databaseSamples, setDatabaseSamples] = useState<number[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [summarizing, setSummarizing] = useState(false)
  const [summaryError, setSummaryError] = useState(false)
  const [exceptionSummary, setExceptionSummary] = useState<{
    text: string
    signature: string
  } | null>(null)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const [dashboard, healthz] = await Promise.all([
        api.dashboard().catch(() => null),
        api.healthz(),
      ])
      if (dashboard) {
        setD(dashboard)
        const queryMs = dashboard.database?.query_ms
        if (isFiniteMetric(queryMs)) {
          setDatabaseSamples((prev) => [...prev, queryMs].slice(-24))
        }
      }
      setHealth(healthz)
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    const immediate = window.setTimeout(() => void load(), 0)
    const id = window.setInterval(() => void load(), 15000)
    return () => {
      window.clearTimeout(immediate)
      window.clearInterval(id)
    }
  }, [load])

  const summarizeExceptions = async () => {
    if (!d || d.exceptions_total === 0) return
    const signature = JSON.stringify([d.exceptions_total, d.exceptions])
    setSummarizing(true)
    setSummaryError(false)
    setExceptionSummary(null)
    try {
      const result = await api.dashboardExceptionSummary()
      setExceptionSummary({ text: result.summary, signature })
    } catch {
      setSummaryError(true)
    } finally {
      setSummarizing(false)
    }
  }

  if (!d) {
    return <p className="text-[13px] text-[var(--color-muted-foreground)]">{t('common.loading')}</p>
  }

  const todayCaption =
    d.today.failed === 0 && d.today.in_progress === 0
      ? t('dashboard.stat.todayCaptionEmpty')
      : t('dashboard.stat.todayCaption', {
          failed: d.today.failed,
          in_progress: d.today.in_progress,
        })
  const readyPct = d.accounts.total > 0
    ? Math.round((d.accounts.ready / d.accounts.total) * 100)
    : 0
  const workerSeconds = workerAge(health)
  const workerOk = health?.ok === true && workerSeconds != null && workerSeconds < 15 * 60
  const healthTone = health?.ok === false
    ? 'red'
    : workerOk
      ? 'green'
      : health?.ok === true
        ? 'orange'
        : 'gray'
  const database = d.database ?? EMPTY_DATABASE_DETAIL
  const exceptionSignature = JSON.stringify([d.exceptions_total, d.exceptions])
  const visibleExceptionSummary = exceptionSummary?.signature === exceptionSignature
    ? exceptionSummary.text
    : null

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={t('dashboard.eyebrow')}
        title={t('dashboard.title')}
        meta={
          <>
            <Badge tone="gray">{t('dashboard.range.today')}</Badge>
            <Badge tone={d.exceptions_total > 0 ? 'orange' : 'green'}>
              {t('dashboard.exceptions.count', { count: d.exceptions_total })}
            </Badge>
          </>
        }
        actions={
          <Button variant="secondary" onClick={load} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : undefined} />
            {t('common.refresh', { defaultValue: 'Refresh' })}
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label={t('dashboard.stat.todayDone')}
          value={d.today.done}
          description={todayCaption}
          tone={d.today.failed > 0 ? 'red' : 'green'}
          icon={<CheckCircle2 size={16} />}
          onClick={() => navigate('/videos?status=handled')}
        />
        <MetricCard
          label={t('dashboard.stat.inProgress')}
          value={d.today.in_progress}
          description={t('dashboard.stat.inProgressCaption')}
          tone={d.today.in_progress > 0 ? 'orange' : 'gray'}
          icon={<Clock3 size={16} />}
          onClick={() => navigate('/videos?status=in_progress')}
        />
        <MetricCard
          label={t('dashboard.stat.accountsReady')}
          value={t('dashboard.stat.accountsValue', {
            ready: d.accounts.ready,
            total: d.accounts.total,
          })}
          description={t('dashboard.stat.accountsCaption', {
            warming: d.accounts.warming,
            not_started: d.accounts.not_started,
          })}
          tone="blue"
          icon={<Users size={16} />}
          onClick={() => navigate('/accounts')}
        />
        <MetricCard
          label={t('dashboard.stat.videosUnscheduled')}
          value={d.videos.unscheduled}
          description={t('dashboard.stat.videosCaption', { total: d.videos.total })}
          tone={d.videos.unscheduled > 0 ? 'orange' : 'gray'}
          icon={<UploadCloud size={16} />}
          onClick={() => navigate('/videos?status=pending')}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.35fr_0.9fr]">
        <Panel
          title={t('dashboard.exceptions.title')}
          description={t('dashboard.exceptions.subtitle')}
          right={(
            <div className="flex items-center gap-2">
              {d.exceptions_total > 0 ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void summarizeExceptions()}
                  disabled={summarizing}
                >
                  <Sparkles size={14} className={summarizing ? 'animate-pulse' : undefined} />
                  {summarizing
                    ? t('dashboard.exceptions.summarizing')
                    : t('dashboard.exceptions.summarize')}
                </Button>
              ) : null}
              <Badge tone={d.exceptions_total > 0 ? 'orange' : 'green'}>{d.exceptions_total}</Badge>
            </div>
          )}
          accent={d.exceptions_total > 0}
          bodyClassName="p-0"
        >
          <div aria-live="polite">
            {visibleExceptionSummary ? (
              <div className="border-b border-[var(--color-border)] bg-[var(--color-blue-bg)] px-4 py-3">
                <div className="flex items-center gap-1.5 text-[12px] font-semibold text-[var(--color-blue)]">
                  <Sparkles size={13} />
                  {t('dashboard.exceptions.summaryLabel')}
                </div>
                <p className="mt-1.5 whitespace-pre-line text-[13px] leading-5 text-[var(--color-foreground)]">
                  {visibleExceptionSummary}
                </p>
              </div>
            ) : null}
            {summaryError && d.exceptions_total > 0 ? (
              <div role="alert" className="border-b border-[var(--color-border)] px-4 py-3 text-[13px] text-[var(--color-destructive)]">
                {t('dashboard.exceptions.summaryError')}
              </div>
            ) : null}
            {d.exceptions.length === 0 ? (
              <EmptyState title={t('dashboard.exceptions.allQuietTitle')}>
                {t('dashboard.exceptions.allQuietBody')}
              </EmptyState>
            ) : (
              <ul className="divide-y divide-[var(--color-border)]">
                {d.exceptions.map((e, i) => (
                  <ExceptionRow key={i} e={e} onGo={() => navigate(KIND_TARGET[e.kind])} />
                ))}
              </ul>
            )}
          </div>
        </Panel>

        <div className="grid grid-cols-1 gap-4">
          <Panel
            title={t('dashboard.health.title')}
            description={t('dashboard.health.subtitle')}
            right={<Badge tone={healthTone}>{t(`dashboard.health.${healthTone}`)}</Badge>}
          >
            <div className="grid grid-cols-2 gap-3">
              <HealthItem
                icon={<Database size={15} />}
                label={t('dashboard.health.db')}
                value={health?.db ?? t('dashboard.health.unknown')}
                tone={health?.db === 'ok' ? 'green' : health?.ok === false ? 'red' : 'gray'}
              />
              <HealthItem
                icon={<Server size={15} />}
                label={t('dashboard.health.worker')}
                value={
                  workerSeconds == null
                    ? t('dashboard.health.unknown')
                    : t('dashboard.health.lastSeen', {
                        seconds: workerSeconds,
                      })
                }
                tone={workerOk ? 'green' : health?.ok === true ? 'orange' : 'gray'}
              />
            </div>
          </Panel>

          <DatabaseDetailPanel database={database} samples={databaseSamples} />

          <Panel
            title={t('dashboard.accounts.title')}
            description={t('dashboard.accounts.subtitle')}
          >
            <div className="flex items-center justify-between gap-4">
              <ProgressBar value={d.accounts.ready} total={d.accounts.total} tone="green" />
              <div className="text-right">
                <div className="text-[22px] font-semibold tabular-nums text-[var(--color-foreground)]">
                  {readyPct}%
                </div>
                <div className="text-[12px] text-[var(--color-muted-foreground)]">
                  {t('dashboard.accounts.ready')}
                </div>
              </div>
            </div>
            <Toolbar className="mt-4 shadow-none">
              <Badge tone="green">{t('dashboard.accounts.readyCount', { count: d.accounts.ready })}</Badge>
              <Badge tone="orange">{t('dashboard.accounts.warmingCount', { count: d.accounts.warming })}</Badge>
              <Badge tone="gray">{t('dashboard.accounts.notStartedCount', { count: d.accounts.not_started })}</Badge>
            </Toolbar>
          </Panel>
        </div>
      </div>
    </div>
  )
}

function DatabaseDetailPanel({
  database,
  samples,
}: {
  database: DashboardData['database']
  samples: number[]
}) {
  const t = useStudioT()
  const badge = databaseBadge(database)
  return (
    <Panel
      title={t('dashboard.database.title')}
      description={t('dashboard.database.subtitle')}
      right={<Badge tone={badge.tone}>{t(badge.labelKey)}</Badge>}
    >
      <div className="space-y-4">
        <DatabaseLatencyChart current={database.query_ms} samples={samples} />
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          <DatabaseMetric label={t('dashboard.database.size')} value={formatBytes(database.size_bytes)} />
          <DatabaseMetric label={t('dashboard.database.connections')} value={formatMetricCount(database.connections)} />
          <DatabaseMetric label={t('dashboard.database.activeQueries')} value={formatMetricCount(database.active_queries)} />
          <DatabaseMetric label={t('dashboard.database.locks')} value={formatMetricCount(database.locks)} />
          <DatabaseMetric label={t('dashboard.database.tables')} value={formatMetricCount(database.tables)} />
        </div>
      </div>
    </Panel>
  )
}

function DatabaseLatencyChart({
  current,
  samples,
}: {
  current: number | null
  samples: number[]
}) {
  const t = useStudioT()
  const series = samples.length > 0
    ? samples
    : isFiniteMetric(current)
      ? [current]
      : []
  const max = Math.max(1, ...series)

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-[12px] font-medium text-[var(--color-foreground)]">
          {t('dashboard.database.latencyTrend')}
        </div>
        <div className="text-[12px] tabular-nums text-[var(--color-muted-foreground)]">
          {isFiniteMetric(current)
            ? t('dashboard.database.currentLatency', { value: formatLatencyValue(current) })
            : t('dashboard.database.noSamples')}
        </div>
      </div>
      <div
        className="flex h-16 items-end gap-1 rounded-[7px] border border-[var(--color-border)] bg-[var(--color-sidebar)] px-2 py-2"
        aria-label={t('dashboard.database.latencyTrend')}
      >
        {series.length > 0 ? (
          series.map((sample, index) => {
            const height = Math.max(8, Math.round((sample / max) * 100))
            const isLatest = index === series.length - 1
            return (
              <div
                key={`${index}-${sample}`}
                className={cn(
                  'min-w-1 flex-1 rounded-t-[3px] transition-all',
                  isLatest ? 'bg-[var(--color-primary)]' : 'bg-[var(--color-blue-bg)]',
                )}
                style={{ height: `${height}%` }}
                title={`${formatLatencyValue(sample)} ms`}
              />
            )
          })
        ) : (
          <div className="flex h-full w-full items-center justify-center text-[12px] text-[var(--color-muted-foreground-2)]">
            {t('dashboard.database.noSamples')}
          </div>
        )}
      </div>
    </div>
  )
}

function DatabaseMetric({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="min-w-0 border-t border-[var(--color-border)] pt-2">
      <div className="truncate text-[12px] text-[var(--color-muted-foreground)]">{label}</div>
      <div className="mt-1 truncate text-[13px] font-semibold tabular-nums text-[var(--color-foreground)]">
        {value}
      </div>
    </div>
  )
}

function HealthItem({
  icon,
  label,
  value,
  tone,
}: {
  icon: ReactNode
  label: string
  value: string
  tone: 'green' | 'orange' | 'red' | 'gray'
}) {
  const dot = {
    green: 'bg-[var(--color-green)]',
    orange: 'bg-[var(--color-orange)]',
    red: 'bg-[var(--color-destructive)]',
    gray: 'bg-[var(--color-muted-foreground-3)]',
  }[tone]
  return (
    <div className="rounded-[7px] border border-[var(--color-border)] bg-[var(--color-sidebar)] p-3">
      <div className="flex items-center gap-2 text-[12px] text-[var(--color-muted-foreground)]">
        <span className="text-[var(--color-muted-foreground-2)]">{icon}</span>
        {label}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[13px] font-semibold text-[var(--color-foreground)]">
        <span className={cn('h-2 w-2 rounded-full', dot)} />
        <span className="truncate">{value}</span>
      </div>
    </div>
  )
}

function ExceptionRow({
  e,
  onGo,
}: {
  e: DashboardException
  onGo: () => void
}) {
  const t = useStudioT()
  const kindLabel = t(KIND_LABEL_KEY[e.kind])

  let metaTail: string | null = null
  if (e.kind === 'publication_failed') {
    metaTail = `${e.video} · ${e.platform}`
  } else if (e.kind === 'proxy_down') {
    metaTail = t('dashboard.exception.proxyDownContext', { hours: e.hours_since })
  } else if (e.kind === 'not_logged_in') {
    metaTail = e.platform
  } else if (e.kind === 'warmup_stuck') {
    metaTail = t('dashboard.exception.warmupStuckContext', { hours: e.hours_since })
  } else if (e.kind === 'proxy_expiring') {
    metaTail = e.days_remaining <= 0
      ? t('dashboard.exception.proxyExpiredContext')
      : t('dashboard.exception.proxyExpiringContext', { days: e.days_remaining })
    if (e.group_name) {
      metaTail = `${e.group_name} · ${metaTail}`
    }
  }

  const reason = e.kind === 'publication_failed' ? e.reason : null

  return (
    <li className="grid grid-cols-[auto_1fr_auto] items-start gap-3 px-4 py-3 hover:bg-[var(--color-muted)] transition-colors">
      <span
        className={cn('mt-1.5 inline-block h-2 w-2 rounded-full shrink-0', SEVERITY_DOT[e.severity])}
      />
      <div className="min-w-0">
        <div className="text-[13px] text-[var(--color-foreground)] truncate">
          <span className="font-semibold">{kindLabel}</span>
          <span className="text-[var(--color-muted-foreground)]"> · {e.account}</span>
          {metaTail ? (
            <span className="text-[var(--color-muted-foreground)]"> · {metaTail}</span>
          ) : null}
        </div>
        {reason ? (
          <div className="mt-1 text-[12px] text-[var(--color-foreground-2)] truncate">
            <AlertTriangle size={12} className="mr-1 inline-block text-[var(--color-orange)]" />
            {reason}
          </div>
        ) : null}
      </div>
      <Button type="button" variant="ghost" size="sm" onClick={onGo}>
        {t('dashboard.exception.action.go')}
      </Button>
    </li>
  )
}
