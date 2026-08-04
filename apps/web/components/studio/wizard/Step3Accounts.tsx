'use client'

import { useState } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { API_ORIGIN } from '@/lib/studio/api'
import type { WizardState } from '@/components/studio/wizard/GroupWizard'

export default function Step3Accounts({
  state, onNext, onBack,
}: {
  state: WizardState
  onNext: () => void
  onBack: () => void
}) {
  const t = useStudioT()
  const slotCount = state.createdPhones.length
  const [rawLoginInfo, setRawLoginInfo] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const lineCount = rawLoginInfo
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .length

  async function commit() {
    setErr(null)
    setSubmitting(true)
    try {
      const r = await fetch(
        `${API_ORIGIN}/api/groups/${state.groupId}/accounts/assign`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raw_login_info: rawLoginInfo }) },
      )
      if (!r.ok) { setErr(await r.text()); return }
      onNext()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="text-sm text-[var(--color-foreground-2)]">
        {t('wizard.s3.intro', {
          n: slotCount,
          defaultValue:
            `Paste login info for up to ${slotCount} phone(s). ` +
            'Each non-empty line is assigned to the next empty phone.',
        })}
      </div>

      <label className="flex flex-col gap-1.5">
        <span className="text-[12px] font-medium text-[var(--color-foreground-2)]">
          {t('wizard.s3.rawLoginInfo', {
            defaultValue: 'TikTok login info',
          })}
        </span>
        <textarea
          rows={14}
          value={rawLoginInfo}
          onChange={(e) => setRawLoginInfo(e.target.value)}
          placeholder={t('wizard.s3.rawLoginInfoPlaceholder', {
            defaultValue:
              'Paste one account per line, for example: username password email email-password',
          })}
          className="w-full border border-[var(--color-border)] rounded-[6px] px-2 py-1.5 text-[12px] font-mono resize-y min-h-[260px]"
        />
      </label>

      {err && <div className="text-red-600 text-sm">{err}</div>}
      <div className="flex justify-between items-center">
        <button
          onClick={onBack}
          className="px-3 py-1.5"
          disabled={submitting}
        >
          ← {t('common.back')}
        </button>
        <div className="text-[12px] text-[var(--color-muted-foreground)]">
          {t('wizard.s3.summary', {
            filled: lineCount,
            total: slotCount,
            defaultValue: `${lineCount} / ${slotCount} lines`,
          })}
        </div>
        <button
          onClick={commit}
          disabled={submitting}
          className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-[var(--color-primary-foreground)] disabled:opacity-50"
        >
          {submitting ? t('common.loading') : t('wizard.s3.confirm')}
        </button>
      </div>
    </div>
  )
}
