'use client'

import { useState } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { API_ORIGIN } from '@/lib/studio/api'
import type { WizardState } from '@/components/studio/wizard/GroupWizard'

type GroupAccount = {
  id: number
  profile_name: string
  group_id: number | null
}

export default function Step2Phones({
  state, patch, onNext, onBack,
}: {
  state: WizardState
  patch: (p: Partial<WizardState>) => void
  onNext: () => void
  onBack: () => void
}) {
  const t = useStudioT()
  const [log, setLog] = useState('')
  const [running, setRunning] = useState(false)
  const [done, setDone] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function start() {
    setRunning(true); setErr(null); setLog('')
    const r = await fetch(`${API_ORIGIN}/api/groups/${state.groupId}/phones/create`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxies: state.proxies,
                              count: parseInt(state.count, 10) }),
    })
    if (!r.ok) { setErr(await r.text()); setRunning(false); return }
    const reader = r.body!.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { value, done: streamDone } = await reader.read()
      if (streamDone) break
      buf += dec.decode(value, { stream: true })
      setLog(buf)
    }
    setRunning(false); setDone(true)
    const r2 = await fetch(`${API_ORIGIN}/api/accounts`)
    if (r2.ok) {
      const all: GroupAccount[] = await r2.json()
      const own = all.filter((a) => a.group_id === state.groupId)
      patch({ createdPhones: own.map((a) =>
                ({ id: a.id, name: a.profile_name })) })
    }
  }

  return (
    <div className="space-y-4">
      <div className="text-sm">
        {t('wizard.s2.summary', { count: parseInt(state.count, 10) || state.proxies.length,
                                    proxies: state.proxies.length,
                                    name: state.name })}
      </div>
      <button onClick={start} disabled={running || done}
              className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-[var(--color-primary-foreground)] disabled:opacity-50">
        {running ? t('wizard.s2.running')
                  : done ? t('wizard.s2.done')
                          : t('wizard.s2.start')}
      </button>
      {log && (
        <pre className="border rounded p-2 text-xs whitespace-pre-wrap
                       max-h-64 overflow-auto bg-gray-50">{log}</pre>
      )}
      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex justify-between">
        <button onClick={onBack} disabled={running}
                className="px-3 py-1.5 disabled:opacity-50">
          ← {t('common.back')}
        </button>
        <button onClick={onNext} disabled={!done}
                className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-[var(--color-primary-foreground)] disabled:opacity-50">
          {t('common.next')} →
        </button>
      </div>
    </div>
  )
}
