'use client'

import { useCallback, useEffect, useState } from 'react'
import { Play, RefreshCw, ShieldCheck, Square, Trash2 } from 'lucide-react'

import {
  Badge,
  Button,
  EmptyState,
  Panel,
  Table,
  TBody,
  Td,
  TextField,
  Th,
  THead,
  Tr,
} from '@/components/ui'
import {
  api,
  type RpaWatchdogHealth,
  type RpaWatchdogTask,
} from '@/lib/studio/api'

const DEFAULT_PARAMS = JSON.stringify(
  {
    视频链接: '',
    评论翻页次数: '500',
    是否打开主页: true,
  },
  null,
  2,
)

function statusTone(status: string): 'blue' | 'green' | 'orange' | 'gray' | 'red' {
  if (status === 'running') return 'green'
  if (status === 'starting') return 'blue'
  if (status === 'stopping') return 'orange'
  if (status === 'stopped') return 'gray'
  return 'red'
}

function seconds(value: number | null): string {
  if (value == null) return '—'
  if (value < 60) return `${Math.round(value)}s`
  const minutes = Math.floor(value / 60)
  const rest = Math.round(value % 60)
  return `${minutes}m ${rest}s`
}

export function RpaWatchdogPanel() {
  const [health, setHealth] = useState<RpaWatchdogHealth | null>(null)
  const [tasks, setTasks] = useState<RpaWatchdogTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [deviceId, setDeviceId] = useState('')
  const [flowId, setFlowId] = useState('')
  const [paramMap, setParamMap] = useState(DEFAULT_PARAMS)
  const [starting, setStarting] = useState(false)
  const [actingTask, setActingTask] = useState<string | null>(null)

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const [nextHealth, nextTasks] = await Promise.all([
        api.opsRpaWatchdogHealth(),
        api.opsRpaWatchdogTasks(),
      ])
      setHealth(nextHealth)
      setTasks(nextTasks.tasks)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(true), 5_000)
    return () => window.clearInterval(timer)
  }, [load])

  const start = async () => {
    setNotice(null)
    setError(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(paramMap)
    } catch {
      setError('Param map must be valid JSON.')
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('Param map must be a JSON object.')
      return
    }
    if (!deviceId.trim() || !flowId.trim()) {
      setError('Device ID and flow ID are required.')
      return
    }

    setStarting(true)
    try {
      const result = await api.opsStartRpaWatchdogTask({
        deviceId: deviceId.trim(),
        flowId: flowId.trim(),
        paramMap: parsed as Record<string, unknown>,
      })
      setNotice(`RPA ${result.taskId} started and registered with the watchdog.`)
      await load(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }

  const stop = async (task: RpaWatchdogTask) => {
    if (!window.confirm(`Stop RPA ${task.taskId} and its cloud phone?`)) return
    setActingTask(task.taskId)
    setError(null)
    try {
      await api.opsStopRpaWatchdogTask(task.taskId)
      setNotice(`Cleanup scheduled for ${task.taskId}.`)
      await load(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActingTask(null)
    }
  }

  const remove = async (task: RpaWatchdogTask) => {
    if (!window.confirm(`Delete local watchdog state for ${task.taskId}?`)) return
    setActingTask(task.taskId)
    setError(null)
    try {
      await api.opsDeleteRpaWatchdogTask(task.taskId)
      setNotice(`Local state deleted for ${task.taskId}.`)
      await load(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActingTask(null)
    }
  }

  return (
    <div className="space-y-6">
      {error ? (
        <div className="rounded-md border border-[var(--color-destructive)]/25 bg-[var(--color-destructive-bg)] px-4 py-3 text-[13px] text-[var(--color-destructive)]">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-md border border-[var(--color-green)]/25 bg-[var(--color-green-bg)] px-4 py-3 text-[13px] text-[var(--color-green)]">
          {notice}
        </div>
      ) : null}

      <Panel
        title="Watchdog connection"
        description="Studio uses the active workspace's GeeLark token server-side; it never reaches the browser."
        right={
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </Button>
        }
      >
        {health ? (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-[13px]">
            <Badge tone={health.tokenMode === 'per-request-geelark-token' ? 'green' : 'red'}>
              <ShieldCheck size={12} />
              Connected · workspace GeeLark token
            </Badge>
            <span>Startup timeout <strong>{health.startupTimeoutSeconds}s</strong></span>
            <span>First user <strong>{health.firstUserTimeoutSeconds}s</strong></span>
            <span>Progress timeout <strong>{health.watchdogTimeoutSeconds}s</strong></span>
            <span>
              Loop rule <strong>{health.recentUserHitThreshold} hits / {health.recentUserCacheSize} users</strong>
            </span>
            <span>Blacklisted videos <strong>{health.videoBlacklistCount}</strong></span>
          </div>
        ) : (
          <div className="text-[13px] text-[var(--color-muted-foreground)]">
            {loading ? 'Connecting…' : 'No health response.'}
          </div>
        )}
      </Panel>

      <Panel
        title="Start guarded RPA"
        description="The watchdog registers the real GeeLark task ID immediately, before the first heartbeat."
        bodyClassName="space-y-4"
      >
        <div className="grid gap-4 md:grid-cols-2">
          <TextField
            label="GeeLark device ID"
            value={deviceId}
            onChange={(event) => setDeviceId(event.target.value)}
            placeholder="Cloud phone ID"
          />
          <TextField
            label="RPA flow ID"
            value={flowId}
            onChange={(event) => setFlowId(event.target.value)}
            placeholder="GeeLark flow ID"
          />
        </div>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-[var(--color-foreground-2)]">
            Param map (JSON)
          </span>
          <textarea
            value={paramMap}
            onChange={(event) => setParamMap(event.target.value)}
            rows={7}
            spellCheck={false}
            className="w-full rounded-md border border-[var(--color-input)] bg-[var(--color-card)] px-3 py-2 font-mono text-[13px] text-[var(--color-foreground)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20"
          />
        </label>
        <div className="flex justify-end">
          <Button onClick={start} disabled={starting}>
            <Play size={14} />
            {starting ? 'Starting…' : 'Start RPA'}
          </Button>
        </div>
      </Panel>

      <Panel
        title={`Tracked tasks (${tasks.length})`}
        description="Auto-refreshes every 5 seconds. Stop runs screenshot → cancel → terminal wait → phone stop."
        bodyClassName="p-0"
      >
        {loading && tasks.length === 0 ? (
          <div className="py-12 text-center text-[13px] text-[var(--color-muted-foreground)]">
            Loading…
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState title="No watchdog tasks">
            Start a guarded RPA above or wait for an RPA heartbeat.
          </EmptyState>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>Task / phone</Th>
                <Th>Status</Th>
                <Th>Progress</Th>
                <Th>Recent users</Th>
                <Th>Idle</Th>
                <Th>Cleanup</Th>
                <Th align="right">Actions</Th>
              </Tr>
            </THead>
            <TBody>
              {tasks.map((task) => {
                const active = ['starting', 'running'].includes(task.status)
                return (
                  <Tr key={task.taskId}>
                    <Td>
                      <div className="font-mono text-[12px]" title={task.taskId}>
                        {task.taskId}
                      </div>
                      <div className="font-mono text-[11px] text-[var(--color-muted-foreground-2)]">
                        {task.deviceId}
                      </div>
                    </Td>
                    <Td>
                      <Badge tone={statusTone(task.status)}>{task.status}</Badge>
                    </Td>
                    <Td className="text-[12px]">
                      <div>{task.lastProgressId || 'waiting'}</div>
                      <div className="text-[var(--color-muted-foreground-2)]">
                        {task.heartbeatCount} heartbeats · {task.newProgressCount} new
                      </div>
                    </Td>
                    <Td className="tabular-nums">
                      {task.recentUserCount}
                      {task.consecutiveCacheHits > 0 ? (
                        <span className="ml-1 text-[var(--color-orange)]">
                          ({task.consecutiveCacheHits} hits)
                        </span>
                      ) : null}
                    </Td>
                    <Td className="tabular-nums">{seconds(task.secondsSinceNewProgress)}</Td>
                    <Td className="max-w-[240px] text-[12px]">
                      {task.cleanupError ? (
                        <span className="text-[var(--color-destructive)]">{task.cleanupError}</span>
                      ) : (
                        task.cleanupReason || '—'
                      )}
                    </Td>
                    <Td align="right">
                      <div className="flex justify-end gap-1">
                        {active ? (
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => void stop(task)}
                            disabled={actingTask === task.taskId}
                          >
                            <Square size={12} />
                            Stop
                          </Button>
                        ) : null}
                        {!active && task.status !== 'stopping' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => void remove(task)}
                            disabled={actingTask === task.taskId}
                          >
                            <Trash2 size={12} />
                            Delete
                          </Button>
                        ) : null}
                      </div>
                    </Td>
                  </Tr>
                )
              })}
            </TBody>
          </Table>
        )}
      </Panel>
    </div>
  )
}
