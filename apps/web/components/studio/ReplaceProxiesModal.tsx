'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { api, type ReplaceProxyItem } from '@/lib/studio/api'
import { parseProxyDebug } from '@/lib/studio/proxy'
import { parseBackendIso } from '@/lib/studio/time'
import { Button } from '@/components/ui'

export type ReplaceProxyAccount = {
  id: number
  profile_name: string | null
  group_name: string | null
  proxy: string | null
  proxy_expires_at: string | null
  geelark_phone_id: string | null
}

// Lightweight proxy parser used for the preview only — the backend
// `proxy_intake.normalize_proxy` is authoritative and we re-validate
// there before applying anything. We just want to show the user how
// their pasted text will round-robin onto the selected accounts.
function previewNormalize(line: string): string | null {
  const s = line.trim()
  if (!s) return null
  // Accept scheme://... and bare host:port and user:pass@host:port. We
  // don't reach for the four-segment vendor format here; the backend
  // handles all the exotic cases.
  if (s.includes('://')) return s
  if (s.includes('@')) return `socks5://${s}`
  if (/^[\w.-]+:\d+$/.test(s)) return `socks5://${s}`
  return null
}

function daysRemaining(iso: string | null): number | null {
  if (!iso) return null
  const d = parseBackendIso(iso)
  if (!d) return null
  return Math.floor((d.getTime() - Date.now()) / 86_400_000)
}

export function ReplaceProxiesModal({
  open,
  accounts,
  onClose,
  onSuccess,
}: {
  open: boolean
  accounts: ReplaceProxyAccount[]
  onClose: () => void
  onSuccess: (result: {
    items: ReplaceProxyItem[]
    synced: number
    failed: number
  }) => void
}) {
  const t = useStudioT()
  const ref = useRef<HTMLDialogElement | null>(null)
  const [raw, setRaw] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 用 effect 同步原生 <dialog> 的开关状态(命令式 DOM API,effect 的本职工作)。
  useEffect(() => {
    const dlg = ref.current
    if (!dlg) return
    if (open && !dlg.open) dlg.showModal()
    if (!open && dlg.open) dlg.close()
  }, [open])

  // open 由 false→true 时表单重置为初始态,等价于原先塞在同一个 effect 里的
  // setState、以 `if (open)` 做门槛。这里改成 React 官方"渲染期调整状态"写法:
  // 用 wasOpen 记录上一次渲染看到的 open 值,值变化时在渲染期直接 setState——
  // 在这次 commit 前就把 raw/error/submitting 清空,不会有残留内容被提交后又被
  // effect 清掉的闪烁,行为等价且更早生效。
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setRaw('')
      setError(null)
      setSubmitting(false)
    }
  }

  const parsed = useMemo(() => {
    const lines = raw.split('\n')
    const valid: string[] = []
    let invalidCount = 0
    for (const line of lines) {
      if (!line.trim()) continue
      const n = previewNormalize(line)
      if (n) valid.push(n)
      else invalidCount += 1
    }
    return { valid, invalidCount }
  }, [raw])

  const assignments = useMemo(() => {
    if (parsed.valid.length === 0) return []
    return accounts.map((acc, i) => ({
      account: acc,
      proxy: parsed.valid[i % parsed.valid.length],
    }))
  }, [accounts, parsed.valid])

  const canSubmit =
    !submitting &&
    accounts.length > 0 &&
    parsed.valid.length > 0 &&
    parsed.invalidCount === 0

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await api.replaceProxies(
        accounts.map((a) => a.id),
        parsed.valid,
      )
      onSuccess({
        items: result.items,
        synced: result.synced,
        failed: result.failed,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
      className="bg-transparent p-0 m-auto"
    >
      <div className="w-[640px] max-w-[92vw] bg-[var(--color-card)] rounded-[var(--radius-card)] shadow-[0_24px_48px_rgba(0,0,0,0.18)] border border-[var(--color-border)] overflow-hidden">
        <div className="px-5 py-4 border-b border-[var(--color-border)] flex items-center justify-between">
          <div className="text-[15px] font-semibold text-[var(--color-foreground)]">
            {t('replaceProxies.title', { count: accounts.length })}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 rounded-full hover:bg-[var(--color-gray-bg)] text-[var(--color-muted-foreground)] flex items-center justify-center"
            aria-label={t('common.close')}
          >
            ✕
          </button>
        </div>

        <div className="p-5 flex flex-col gap-4">
          <div>
            <div className="text-[12.5px] text-[var(--color-muted-foreground)] mb-1.5">
              {t('replaceProxies.pasteLabel')}
            </div>
            <textarea
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              rows={5}
              placeholder={t('replaceProxies.pastePlaceholder')}
              className="w-full font-mono text-[12.5px] rounded-[6px] border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2 outline-none focus:border-[var(--color-muted-foreground)]"
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
            />
            <div className="mt-1 flex items-center gap-3 text-[12px] text-[var(--color-muted-foreground-2)]">
              <span>
                {t('replaceProxies.proxyCount', { count: parsed.valid.length })}
              </span>
              {parsed.invalidCount > 0 ? (
                <span className="text-[var(--color-destructive)]">
                  {t('replaceProxies.invalidLines', {
                    count: parsed.invalidCount,
                  })}
                </span>
              ) : null}
            </div>
          </div>

          <div>
            <div className="text-[12.5px] text-[var(--color-muted-foreground)] mb-1.5">
              {t('replaceProxies.assignmentPreview')}
            </div>
            <div className="rounded-[6px] border border-[var(--color-border)] divide-y divide-[var(--color-border)] max-h-[260px] overflow-y-auto">
              {assignments.length === 0 ? (
                <div className="px-3 py-4 text-[12px] text-[var(--color-muted-foreground-2)] text-center">
                  {t('replaceProxies.previewEmpty')}
                </div>
              ) : (
                assignments.map(({ account, proxy }) => {
                  const days = daysRemaining(account.proxy_expires_at)
                  const oldDebug = parseProxyDebug(account.proxy) || '—'
                  const newDebug = parseProxyDebug(proxy) || proxy
                  return (
                    <div
                      key={account.id}
                      className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-3 py-2 text-[12px]"
                    >
                      <div className="min-w-0">
                        <div className="font-medium text-[var(--color-foreground)] truncate">
                          {account.profile_name || `#${account.id}`}
                          {account.group_name ? (
                            <span className="ml-1.5 text-[var(--color-muted-foreground-2)]">
                              · {account.group_name}
                            </span>
                          ) : null}
                        </div>
                        <div className="font-mono text-[var(--color-muted-foreground-2)] truncate">
                          {oldDebug}
                          {days != null ? (
                            <span className="ml-1.5">
                              ({days <= 0
                                ? t('replaceProxies.expiredBadge')
                                : t('replaceProxies.daysLeftBadge', { days })})
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <div className="text-[var(--color-muted-foreground-2)]">→</div>
                      <div className="min-w-0 font-mono text-[var(--color-foreground-2)] truncate">
                        {newDebug}
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </div>

          {error ? (
            <div className="text-[12.5px] text-[var(--color-destructive)]">{error}</div>
          ) : null}
        </div>

        <div className="px-5 py-3.5 border-t border-[var(--color-border)] flex justify-end gap-2 bg-[var(--color-muted)]">
          <Button type="button" variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="button"
            variant="primary"
            onClick={submit}
            disabled={!canSubmit}
          >
            {submitting
              ? t('replaceProxies.submitting')
              : t('replaceProxies.submit')}
          </Button>
        </div>
      </div>
    </dialog>
  )
}
