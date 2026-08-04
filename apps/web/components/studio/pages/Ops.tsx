'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, type OpsSession } from '@/lib/studio/api'
import {
  Badge,
  Card,
  EmptyState,
  PageTitle,
  SegmentedControl,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from '@/components/ui'
import { cn } from '@/lib/studio/cn'
import { parseBackendIso } from '@/lib/studio/time'
import { RpaWatchdogPanel } from '@/components/studio/ops/RpaWatchdogPanel'

type StatusFilter = 'active' | 'overdue'
type OpsView = 'sessions' | 'rpa-watchdog'

const SOURCE_TONE: Record<string,
  'blue' | 'green' | 'orange' | 'gray' | 'red'> = {
  publish: 'blue',
  warmup: 'green',
  engagement: 'orange',
  manual: 'gray',
  external_detected: 'red',
}

function fmtTime(iso: string): string {
  const d = parseBackendIso(iso)
  if (!d) return iso
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit',
                                    second: '2-digit' })
}

function fmtRelative(iso: string): string {
  const d = parseBackendIso(iso)
  if (!d) return iso
  const ms = Date.now() - d.getTime()
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  return `${Math.floor(hr / 24)}d ago`
}

function fmtRemaining(iso: string): string {
  const d = parseBackendIso(iso)
  if (!d) return iso
  const ms = d.getTime() - Date.now()
  if (ms < 0) {
    const overSec = Math.floor(-ms / 1000)
    if (overSec < 60) return `−${overSec}s`
    const overMin = Math.floor(overSec / 60)
    if (overMin < 60) return `−${overMin}m`
    return `−${Math.floor(overMin / 60)}h`
  }
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h`
  return `${Math.floor(hr / 24)}d`
}

export default function Ops() {
  const [view, setView] = useState<OpsView>('sessions')
  const [items, setItems] = useState<OpsSession[]>([])
  const [filter, setFilter] = useState<StatusFilter>('active')
  const [loading, setLoading] = useState(false)
  const [releasing, setReleasing] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (view !== 'sessions') return
    setLoading(true)
    setError(null)
    try {
      const r = await api.opsSessions(filter)
      setItems(r.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter, view])

  useEffect(() => {
    const id = window.setTimeout(() => {
      void load()
    }, 0)
    return () => window.clearTimeout(id)
  }, [load])

  // Poll every 15s so the operator sees the watchdog/sweeper output
  // without manually refreshing. Cheap query — single LEFT JOIN.
  useEffect(() => {
    if (view !== 'sessions') return
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load, view])

  const release = async (sid: number) => {
    setReleasing(sid)
    try {
      await api.opsReleaseSession(sid)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setReleasing(null)
    }
  }

  const overdueCount = items.filter((s) => s.overdue).length
  const total = items.length

  return (
    <div className="space-y-6">
      <PageTitle
        title="Operations"
        subtitle="Inspect phone leases and control the real-time RPA progress watchdog."
      />

      <SegmentedControl
        value={view}
        onChange={setView}
        ariaLabel="Operations view"
        options={[
          { value: 'sessions', label: 'Phone sessions', count: total },
          { value: 'rpa-watchdog', label: 'RPA Watchdog' },
        ]}
      />

      {view === 'rpa-watchdog' ? (
        <RpaWatchdogPanel />
      ) : (
        <>

      <div className="flex items-center gap-2">
        {(['active', 'overdue'] as StatusFilter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={cn(
              'px-3 py-1.5 rounded text-[13px] transition-colors',
              filter === f
                ? 'bg-[var(--color-foreground)] text-white'
                : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
            )}
          >
            {f === 'active'
              ? `Active (${total})`
              : `Overdue (${overdueCount})`}
          </button>
        ))}
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
          <EmptyState title="No sessions">
            {filter === 'overdue'
              ? 'No sessions past their budget — the watchdog is keeping up.'
              : 'No active phone sessions right now.'}
          </EmptyState>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>Account</Th>
                <Th>Source</Th>
                <Th>Owner</Th>
                <Th>Started</Th>
                <Th>Budget left</Th>
                <Th>GeeLark task</Th>
                <Th align="right">Actions</Th>
              </Tr>
            </THead>
            <TBody>
              {items.map((s) => (
                <Tr key={s.id}>
                  <Td>
                    <div
                      className="max-w-[260px] truncate text-[var(--color-foreground)]"
                      title={s.profile_name || `(account ${s.account_id})`}
                    >
                      {s.profile_name || `(account ${s.account_id})`}
                    </div>
                    <div className="text-[12px] text-[var(--color-muted-foreground-2)]">
                      env {s.env_id.slice(-6)}
                    </div>
                  </Td>
                  <Td>
                    <Badge tone={SOURCE_TONE[s.source] || 'gray'}>
                      {s.source}
                    </Badge>
                  </Td>
                  <Td className="text-[12.5px] text-[var(--color-muted-foreground)]">
                    {s.owner_type ? `${s.owner_type} #${s.owner_id}` : '—'}
                  </Td>
                  <Td className="whitespace-nowrap text-[12.5px] text-[var(--color-muted-foreground)]">
                    <span title={s.started_at}>{fmtTime(s.started_at)}</span>
                    <span className="text-[var(--color-muted-foreground-2)]">
                      {' · '}{fmtRelative(s.started_at)}
                    </span>
                  </Td>
                  <Td
                    className={cn(
                      'whitespace-nowrap tabular-nums',
                      s.overdue
                        ? 'text-[var(--color-destructive)]'
                        : 'text-[var(--color-foreground-2)]',
                    )}
                  >
                    {fmtRemaining(s.budget_deadline_at)}
                  </Td>
                  <Td className="font-mono text-[12px] text-[var(--color-muted-foreground-2)]">
                    {s.geelark_task_id
                      ? s.geelark_task_id.slice(-10)
                      : '—'}
                  </Td>
                  <Td align="right">
                    <button
                      onClick={() => release(s.id)}
                      disabled={releasing === s.id}
                      className="text-[12.5px] text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] disabled:opacity-50"
                      title="Finalize this session as STOPPED (operator override). Does not stop the cloud phone — the idle sweeper handles that."
                    >
                      {releasing === s.id ? 'Releasing…' : 'Release'}
                    </button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </Card>

      <div className="text-[12px] text-[var(--color-muted-foreground-2)]">
        Auto-refreshes every 15s. Release finalizes the session in our ledger
        only; the cloud phone is left running for the idle sweeper to catch on
        the next tick.
      </div>
        </>
      )}
    </div>
  )
}
