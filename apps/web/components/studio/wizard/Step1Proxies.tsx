'use client'

import { useState } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { API_ORIGIN } from '@/lib/studio/api'
import type { WizardState } from '@/components/studio/wizard/GroupWizard'

type ProbeRow = { proxy: string; ok: boolean; exit_ip?: string | null;
                  latency_ms?: number | null; error?: string | null }

export default function Step1Proxies({
  state, patch, existingGroupMode, onDraftGroupCreated, onNext, onCancel,
}: {
  state: WizardState
  patch: (p: Partial<WizardState>) => void
  existingGroupMode?: boolean
  onDraftGroupCreated?: (groupId: number) => void
  onNext: () => void
  onCancel: () => void
}) {
  const t = useStudioT()
  const [text, setText] = useState('')
  const [checking, setChecking] = useState(false)
  const [results, setResults] = useState<ProbeRow[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function runCheck() {
    setErr(null)
    const proxies = text.split('\n').map(l => l.trim()).filter(Boolean)
    if (!proxies.length) { setErr(t('wizard.s1.no_proxies')); return }
    if (!existingGroupMode && !state.name.trim()) {
      setErr(t('wizard.s1.no_name'))
      return
    }
    setChecking(true)
    let gid = state.groupId
    if (gid === null) {
      const r = await fetch(API_ORIGIN + '/api/groups', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: state.name.trim(),
                                notes: state.notes || null }),
      })
      if (!r.ok) { setErr(await r.text()); setChecking(false); return }
      const created: { id: number } = await r.json()
      gid = created.id
      patch({ groupId: gid })
      onDraftGroupCreated?.(gid)
    }
    const r2 = await fetch(`${API_ORIGIN}/api/groups/${gid}/proxies/check`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxies }),
    })
    if (!r2.ok) { setErr(await r2.text()); setChecking(false); return }
    const data: ProbeRow[] = await r2.json()
    setResults(data)
    setSelected(new Set(data.filter(r => r.ok).map(r => r.proxy)))
    if (!state.count.trim()) {
      patch({ count: String(data.filter(r => r.ok).length) })
    }
    setChecking(false)
  }

  function toggle(proxy: string) {
    const next = new Set(selected)
    if (next.has(proxy)) next.delete(proxy)
    else next.add(proxy)
    setSelected(next)
  }

  function proceed() {
    const chosen = (results || []).filter(r => selected.has(r.proxy))
                                    .map(r => r.proxy)
    if (!chosen.length) { setErr(t('wizard.s1.select_at_least_one')); return }
    const n = parseInt(state.count, 10)
    if (!Number.isFinite(n) || n < 1) {
      setErr(t('wizard.s1.count_invalid')); return
    }
    setSubmitting(true)
    patch({ proxies: chosen })
    onNext()
  }

  return (
    <div className="space-y-4">
      {existingGroupMode ? (
        <div className="rounded border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          {t('wizard.s1.existing_group', { name: state.name || `#${state.groupId}` })}
        </div>
      ) : (
        <>
          <div>
            <label className="block text-sm font-medium mb-1">
              {t('wizard.s1.name')}
            </label>
            <input
              className="border rounded w-full px-2 py-1"
              value={state.name}
              onChange={e => patch({ name: e.target.value })}
              placeholder="project-jp"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">
              {t('wizard.s1.notes')}
            </label>
            <input
              className="border rounded w-full px-2 py-1"
              value={state.notes}
              onChange={e => patch({ notes: e.target.value })}
            />
          </div>
        </>
      )}
      <div>
        <label className="block text-sm font-medium mb-1">
          {t('wizard.s1.proxies')}
        </label>
        <textarea
          className="border rounded w-full px-2 py-1 font-mono text-xs h-40"
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="socks5://user:pass@host:port"
        />
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">
          {t('wizard.s1.count')}
        </label>
        <input
          type="number"
          min={1}
          className="border rounded w-32 px-2 py-1"
          value={state.count}
          onChange={e => patch({ count: e.target.value })}
          placeholder=""
        />
        <div className="text-xs text-gray-500 mt-1">
          {t('wizard.s1.count_hint')}
        </div>
      </div>
      <div className="flex gap-2">
        <button onClick={runCheck} disabled={checking}
                className="px-3 py-1.5 rounded bg-gray-200">
          {checking ? t('common.loading') : t('wizard.s1.check')}
        </button>
      </div>
      {results && (
        <div className="border rounded p-2 text-xs max-h-48 overflow-auto">
          {results.map((r, i) => (
            <label key={i} className="flex items-center gap-2 py-0.5">
              <input type="checkbox" checked={selected.has(r.proxy)}
                     onChange={() => toggle(r.proxy)} />
              <span className={r.ok ? 'text-green-600' : 'text-red-600'}>
                {r.ok ? '🟢' : '🔴'}
              </span>
              <code className="flex-1 truncate">{r.proxy}</code>
              {r.ok
                ? <span className="text-gray-500">
                    {r.exit_ip} · {r.latency_ms}ms
                  </span>
                : <span className="text-red-600">{r.error}</span>}
            </label>
          ))}
        </div>
      )}
      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="px-3 py-1.5">
          {t('common.cancel')}
        </button>
        <button onClick={proceed}
                disabled={!results || submitting
                          || !(parseInt(state.count, 10) >= 1)}
                className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-[var(--color-primary-foreground)] disabled:opacity-50">
          {t('common.next')} →
        </button>
      </div>
    </div>
  )
}
