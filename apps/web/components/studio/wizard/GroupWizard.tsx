'use client'

import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useStudioT } from '@/lib/studio/i18n'
import { API_ORIGIN } from '@/lib/studio/api'
import Step1Proxies from '@/components/studio/wizard/Step1Proxies'
import Step2Phones from '@/components/studio/wizard/Step2Phones'
import Step3Accounts from '@/components/studio/wizard/Step3Accounts'
import { shouldDeleteDraftGroupOnClose, wizardTitleKey } from '@/lib/studio/groupSetupFlow'

export type WizardState = {
  name: string
  notes: string
  proxies: string[]
  count: string            // kept as string so empty/typing states are first-class
  groupId: number | null
  createdPhones: { id: number; name: string }[]
}

type Props = {
  onClose: () => void
  mode?: 'modal' | 'fullpane'
  initialGroupId?: number | null
  initialGroupName?: string | null
}

export default function GroupWizard({
  onClose,
  mode = 'modal',
  initialGroupId = null,
  initialGroupName = null,
}: Props) {
  const t = useStudioT()
  const [step, setStep] = useState(1)
  const [createdGroupId, setCreatedGroupId] = useState<number | null>(null)
  const [state, setState] = useState<WizardState>({
    name: initialGroupName ?? '', notes: '', proxies: [], count: '',
    groupId: initialGroupId, createdPhones: [],
  })

  function patch(p: Partial<WizardState>) {
    setState(s => ({ ...s, ...p }))
  }

  async function handleClose() {
    if (shouldDeleteDraftGroupOnClose({
      initialGroupId,
      createdGroupId,
      currentGroupId: state.groupId,
      step,
    })) {
      try {
        await fetch(`${API_ORIGIN}/api/groups/${state.groupId}`, { method: 'DELETE' })
      } catch {
        // Best-effort cleanup for a draft group created in step 1.
      }
    }
    onClose()
  }

  const body = (
    <>
      {mode === 'fullpane' && <StepBar step={step} />}
      {mode === 'modal' && (
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold">
            {t(wizardTitleKey(initialGroupId))} — {t(`wizard.step.${step}`)}
          </h2>
          <button onClick={handleClose}
                  className="text-gray-500 hover:text-black">✕</button>
        </div>
      )}
      {step === 1 && (
        <Step1Proxies state={state} patch={patch}
                      existingGroupMode={initialGroupId !== null}
                      onDraftGroupCreated={setCreatedGroupId}
                      onNext={() => setStep(2)} onCancel={handleClose} />
      )}
      {step === 2 && (
        <Step2Phones state={state} patch={patch}
                     onNext={() => setStep(3)} onBack={() => setStep(1)} />
      )}
      {step === 3 && (
        <Step3Accounts state={state}
                        onNext={onClose} onBack={() => setStep(2)} />
      )}
    </>
  )

  if (mode === 'fullpane') {
    return <div className="p-2">{body}</div>
  }
  // Render the modal via a portal so it escapes any ancestor with a non-`none`
  // `transform` (e.g. Shell's `.fade-up` route-transition wrapper). Otherwise
  // `position: fixed` + `inset-0` is anchored to that ancestor's box instead
  // of the viewport, leaving the backdrop offset and the card visually
  // misplaced.
  return createPortal(
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-card w-[800px] max-h-[90vh] overflow-auto p-6">
        {body}
      </div>
    </div>,
    document.body,
  )
}

function StepBar({ step }: { step: number }) {
  const labels = ['Proxies', 'Create phones', 'Paste login info']
  return (
    <div className="flex gap-2 mb-4">
      {labels.map((label, i) => {
        const n = i + 1
        const state: 'done' | 'active' | 'pending' =
          n < step ? 'done' : n === step ? 'active' : 'pending'
        const bg = state === 'active' ? 'bg-[var(--color-primary)]'
                 : state === 'done' ? 'bg-green-100' : 'bg-gray-100'
        const fg = state === 'active' ? 'text-white'
                 : state === 'done' ? 'text-green-800' : 'text-gray-500'
        return (
          <div key={n}
               className={`flex-1 px-3 py-2 rounded text-xs font-semibold ${bg} ${fg}`}>
            {state === 'done' ? '✓ ' : `${n}. `}{label}
          </div>
        )
      })}
    </div>
  )
}
