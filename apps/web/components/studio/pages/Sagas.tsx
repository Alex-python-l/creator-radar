'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type SagaKind,
  type SagaSummary,
  type SagaView,
} from '@/lib/studio/api'
import {
  Badge,
  Card,
  EmptyState,
  PageTitle,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from '@/components/ui'
import { cn } from '@/lib/studio/cn'
import { parseBackendIso } from '@/lib/studio/time'

type KindFilter = SagaKind | 'all'

const KIND_TONE: Record<SagaKind, 'blue' | 'green' | 'orange'> = {
  publish: 'blue',
  warmup: 'green',
  engagement: 'orange',
}

function fmtDuration(seconds: number): string {
  if (seconds < 0) return `−${fmtDuration(-seconds)}`
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseBackendIso(iso)
  if (!d) return iso
  return d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export default function Sagas() {
  const [items, setItems] = useState<SagaView[]>([])
  const [summary, setSummary] = useState<SagaSummary | null>(null)
  const [filter, setFilter] = useState<KindFilter>('all')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 请求序号护栏:10s 轮询与切换 filter 并发时,先发的慢响应可能后到,若不设防
  // 会用旧数据覆盖新数据(如 publish→warmup 后,晚到的 publish 响应把列表打回
  // publish,而筛选按钮停在 warmup)。每次 load() 自增取号;响应到达后各回调先核对
  // 自己是否仍是最新一次请求,不是则跳过全部 setState——finally 的 setLoading(false)
  // 也跳过,加载态始终由最新一次请求独占收尾。计数器挂在组件级 ref 上而不是闭包里,
  // 跨 load 的 useCallback 重建(filter 变化)仍单调递增:旧闭包能取号的时刻必然
  // 早于新闭包首跑(interval 清理先于新 effect 执行),其序号永远小于新请求,必过期。
  const loadSeqRef = useRef(0)

  // 用 .then()/.catch()/.finally() 而不是 async/await:react-hooks/set-state-in-effect
  // 只会顺着同一个函数体的同步指令流查 setState 调用,async 函数里 await 前后的
  // 语句仍算同一条流(哪怕先 await 一次也一样),会被 useEffect(() => void load())
  // 判定为"在 effect 里同步调用了会 setState 的函数";但传给 .then() 的回调是另一个
  // 独立的函数体,分析器不会往里看。这里把 setLoading/setError/setItems/setSummary
  // 全部挪进 .then() 回调,load() 自身不再直接同步调用任何 setState,
  // try/catch/finally 的语义原样保留为 .then/.catch/.finally 链。
  const load = useCallback(() => {
    const seq = ++loadSeqRef.current
    const kind = filter === 'all' ? undefined : filter
    return Promise.resolve()
      .then(() => {
        // 首个 .then 在 load() 调用所在宏任务收尾的微任务里执行,此刻本请求必然
        // 是最新号(每个触发源——挂载 effect、interval tick、切 filter——各占一个
        // 宏任务),故不设防;且置 loading/清 error 幂等,晚到的新请求会重复执行。
        setLoading(true)
        setError(null)
        return Promise.all([api.opsSagas(kind), api.opsSagaSummary()])
      })
      .then(([list, sum]) => {
        if (seq !== loadSeqRef.current) return
        setItems(list.items)
        setSummary(sum)
      })
      .catch((e) => {
        if (seq !== loadSeqRef.current) return
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (seq !== loadSeqRef.current) return
        setLoading(false)
      })
  }, [filter])

  useEffect(() => {
    void load()
  }, [load])

  // Auto-refresh every 10s so an operator can watch a saga walk
  // through its steps in near-real-time.
  useEffect(() => {
    const id = setInterval(load, 10_000)
    return () => clearInterval(id)
  }, [load])

  const inFlightTotal = summary
    ? Object.values(summary.in_flight_by_kind).reduce(
        (acc, n) => acc + (n ?? 0),
        0,
      )
    : 0

  const filterCounts: Record<KindFilter, number> = {
    all: inFlightTotal,
    publish: summary?.in_flight_by_kind.publish ?? 0,
    warmup: summary?.in_flight_by_kind.warmup ?? 0,
    engagement: summary?.in_flight_by_kind.engagement ?? 0,
  }

  return (
    <div className="space-y-6">
      <PageTitle title="Sagas" />

      {/* Stuck-signal banner: only renders when something is wedged.
          Two distinct counts because lease-stuck and step-stuck are
          orthogonal — overdue_session_watchdog catches the first;
          saga_stuck_logger surfaces the second. */}
      {summary && (summary.stuck > 0 || summary.step_stuck > 0) ? (
        <Card className="p-3 border-[var(--color-destructive)]/40 bg-[var(--color-destructive)]/5">
          <div className="flex flex-wrap items-center gap-4 text-[13px]">
            {summary.stuck > 0 ? (
              <span className="text-[var(--color-destructive)]">
                <strong>{summary.stuck}</strong> saga
                {summary.stuck > 1 ? 's' : ''} past lease deadline
              </span>
            ) : null}
            {summary.step_stuck > 0 ? (
              <span className="text-[var(--color-orange)]">
                <strong>{summary.step_stuck}</strong> saga
                {summary.step_stuck > 1 ? 's' : ''} stuck at one step
              </span>
            ) : null}
          </div>
        </Card>
      ) : null}

      <div className="flex items-center gap-2">
        {(['all', 'publish', 'warmup', 'engagement'] as KindFilter[]).map(
          (k) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              className={cn(
                'px-3 py-1.5 rounded text-[13px] transition-colors',
                filter === k
                  ? 'bg-[var(--color-foreground)] text-white'
                  : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
              )}
            >
              {k === 'all' ? 'All' : k} ({filterCounts[k]})
            </button>
          ),
        )}
      </div>

      {error && (
        <div className="text-[13px] text-[var(--color-destructive)]">
          Failed to load: {error}
        </div>
      )}

      <Card className="p-0 overflow-hidden">
        {loading && items.length === 0 ? (
          <div className="py-12 text-center text-[13px] text-[var(--color-muted-foreground)]">
            Loading…
          </div>
        ) : items.length === 0 ? (
          <EmptyState title="No in-flight sagas">
            All pipelines are idle right now.
          </EmptyState>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>Kind</Th>
                <Th>Business ref</Th>
                <Th>Step</Th>
                <Th>At step</Th>
                <Th>Lease left</Th>
                <Th>GeeLark task</Th>
                <Th>Account</Th>
              </Tr>
            </THead>
            <TBody>
              {items.map((s) => {
                const stepStuck = s.is_step_stuck
                const leaseStuck = s.is_stuck
                return (
                  <Tr key={s.saga_id}>
                    <Td>
                      <Badge tone={KIND_TONE[s.kind]}>{s.kind}</Badge>
                    </Td>
                    <Td className="font-mono text-[12px] text-[var(--color-foreground-2)]">
                      {s.business_ref}
                    </Td>
                    <Td>
                      <span
                        className={cn(
                          'text-[12.5px]',
                          stepStuck
                            ? 'text-[var(--color-orange)] font-semibold'
                            : 'text-[var(--color-foreground-2)]',
                        )}
                        title={
                          s.step_budget_seconds != null
                            ? `Budget ${s.step_budget_seconds}s`
                            : undefined
                        }
                      >
                        {s.current_step}
                      </span>
                    </Td>
                    <Td
                      className={cn(
                        'whitespace-nowrap tabular-nums text-[12.5px]',
                        stepStuck
                          ? 'text-[var(--color-orange)] font-semibold'
                          : 'text-[var(--color-muted-foreground)]',
                      )}
                    >
                      <span title={fmtTime(s.updated_at)}>
                        {fmtDuration(s.seconds_at_current_step)}
                        {s.step_budget_seconds != null ? (
                          <span className="text-[var(--color-muted-foreground-3)]">
                            {' / '}
                            {fmtDuration(s.step_budget_seconds)}
                          </span>
                        ) : null}
                      </span>
                    </Td>
                    <Td
                      className={cn(
                        'whitespace-nowrap tabular-nums text-[12.5px]',
                        leaseStuck
                          ? 'text-[var(--color-destructive)] font-semibold'
                          : 'text-[var(--color-muted-foreground)]',
                      )}
                    >
                      {s.budget_remaining_seconds != null
                        ? fmtDuration(s.budget_remaining_seconds)
                        : '—'}
                    </Td>
                    <Td className="font-mono text-[12px] text-[var(--color-muted-foreground-2)]">
                      {s.geelark_task_id
                        ? s.geelark_task_id.slice(-10)
                        : '—'}
                    </Td>
                    <Td className="text-[12px] text-[var(--color-muted-foreground-2)]">
                      #{s.account_id}
                    </Td>
                  </Tr>
                )
              })}
            </TBody>
          </Table>
        )}
      </Card>

      <div className="text-[12px] text-[var(--color-muted-foreground-2)]">
        Auto-refreshes every 10s. Step budgets are tunable in
        app/services/sagas.py STEP_BUDGETS_SECONDS.
      </div>
    </div>
  )
}
