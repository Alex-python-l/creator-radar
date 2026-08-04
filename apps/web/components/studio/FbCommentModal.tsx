'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useStudioT } from '@/lib/studio/i18n'
import { useStudioHref } from '@/lib/studio/nav'
import {
  api,
  type CommentPrompt,
  type CommentTarget,
  type EligiblePhone,
  type FbCommentSubmitPayload,
} from '@/lib/studio/api'
import { Button } from '@/components/ui'
import { cn } from '@/lib/studio/cn'

type Mode = 'owned' | 'manual'
type Step = 1 | 2 | 3
type CommentMode = 'generate' | 'provided'

const UNGROUPED = '__ungrouped__'

// FB post URL light validation — authoritative check stays server-side.
const FB_URL_RE = /^https:\/\/(?:(?:www|m|web|mbasic)\.)?facebook\.com\/.+/i
const FB_SHORT_RE = /^https:\/\/fb\.(?:watch|com)\/[A-Za-z0-9_-]+/i

function isLikelyFbUrl(u: string): boolean {
  const v = u.trim()
  return FB_URL_RE.test(v) || FB_SHORT_RE.test(v)
}

export function FbCommentModal({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (payload: FbCommentSubmitPayload) => Promise<void>
}) {
  const t = useStudioT()
  const cardRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLElement | null>(null)

  const [step, setStep] = useState<Step>(1)
  const [phones, setPhones] = useState<EligiblePhone[]>([])
  const [targets, setTargets] = useState<CommentTarget[]>([])
  const [selectedPhones, setSelectedPhones] = useState<Set<number>>(new Set())
  const [selectedTargets, setSelectedTargets] = useState<Set<number>>(new Set())
  const [mode, setMode] = useState<Mode>('owned')
  const [manualLinks, setManualLinks] = useState('')
  const [keywordsText, setKeywordsText] = useState('')
  const [candidatesPerPost, setCandidatesPerPost] = useState(3)
  const [prompts, setPrompts] = useState<CommentPrompt[]>([])
  const [commentPromptId, setCommentPromptId] = useState<number | null>(null)
  const [commentMode, setCommentMode] = useState<CommentMode>('generate')
  const [providedComments, setProvidedComments] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [phoneSearch, setPhoneSearch] = useState('')

  useEffect(() => {
    if (!open) return
    triggerRef.current = (document.activeElement as HTMLElement) ?? null
    const stepTimer = window.setTimeout(() => {
      setStep(1)
      Promise.all([
        api.eligiblePhones({ platform: 'facebook' }),
        api.engagementTargets({ platform: 'facebook' }),
        api.commentPrompts().catch(() => [] as CommentPrompt[]),
      ])
        .then(([p, ts, pr]) => {
          setPhones(p.items)
          setTargets(ts.items)
          setSelectedPhones(new Set(p.items.filter((x) => !x.busy).map((x) => x.id)))
          setSelectedTargets(new Set())
          setPrompts(pr)
          setCommentPromptId(pr.length === 1 ? pr[0].id : null)
        })
        .catch(() => {})
    }, 0)
    return () => {
      window.clearTimeout(stepTimer)
      const trigger = triggerRef.current
      if (trigger && typeof trigger.focus === 'function') trigger.focus()
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const original = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = original
    }
  }, [open])

  // Block every close affordance while submitting — closing mid-submit
  // and reopening (fresh state) leads to a double submit / duplicate
  // runs. The submit button's !submitting guard didn't cover Esc /
  // scrim / ✕.
  const guardedClose = useCallback(() => {
    if (submitting) return
    onClose()
  }, [submitting, onClose])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        guardedClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, guardedClose])

  const manualLinkList = useMemo(
    () =>
      manualLinks
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    [manualLinks],
  )

  const keywordList = useMemo(
    () =>
      keywordsText
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    [keywordsText],
  )

  const providedList = useMemo(
    () =>
      providedComments
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    [providedComments],
  )

  const targetCount = mode === 'owned' ? selectedTargets.size : manualLinkList.length
  const phoneCount = selectedPhones.size
  const allLinksLookOK = mode !== 'manual' || (manualLinkList.length > 0 && manualLinkList.every(isLikelyFbUrl))
  const keywordsOk =
    keywordList.length >= 1 &&
    keywordList.length <= 10 &&
    keywordList.every((k) => k.length <= 100)
  const candidateCountOk = candidatesPerPost >= 1 && candidatesPerPost <= 10
  const providedOk =
    commentMode !== 'provided' ||
    (providedList.length > 0 && providedList.length <= 10 && providedList.every((c) => c.length <= 8000))
  const promptOk = commentMode === 'generate' ? commentPromptId != null : true

  const canStep1Next = targetCount > 0 && (mode !== 'manual' || allLinksLookOK)
  const canStep2Next = phoneCount > 0 && keywordsOk
  const canSubmit =
    !submitting &&
    phoneCount > 0 &&
    targetCount > 0 &&
    allLinksLookOK &&
    keywordsOk &&
    candidateCountOk &&
    providedOk &&
    promptOk

  const togglePhone = (id: number) =>
    setSelectedPhones((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const toggleTarget = (id: number) =>
    setSelectedTargets((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      const payload: FbCommentSubmitPayload = {
        account_ids: [...selectedPhones],
        target_links: mode === 'manual' ? manualLinkList : [],
        target_publication_ids: mode === 'owned' ? [...selectedTargets] : [],
        candidates_per_post: candidatesPerPost,
        keywords: keywordList,
        comment_mode: commentMode === 'generate' ? 'studio_ai' : 'provided',
        comment_prompt_id: commentMode === 'generate' ? commentPromptId : null,
        provided_comments: commentMode === 'provided' ? providedList : null,
      }
      await onSubmit(payload)
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-[rgba(0,0,0,0.34)] backdrop-blur-[4px] modal-scrim-in"
      onClick={guardedClose}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-label={t('fbComments.modalTitle')}
        className="w-[720px] max-w-[92vw] max-h-[86vh] flex flex-col bg-[var(--color-card)] border border-[var(--color-border)] rounded-[var(--radius-card)] shadow-[0_24px_48px_rgba(0,0,0,0.18)] overflow-hidden modal-card-in"
        onClick={(e) => e.stopPropagation()}
      >
        <Header onClose={guardedClose} title={t('fbComments.modalTitle')} />
        <Stepper current={step} />
        <div className="flex-1 overflow-y-auto px-[22px] py-[18px]">
          {step === 1 && (
            <TargetsStep
              mode={mode}
              onMode={setMode}
              targets={targets}
              selected={selectedTargets}
              onToggle={toggleTarget}
              manualLinks={manualLinks}
              onManualLinks={setManualLinks}
              manualLinkList={manualLinkList}
              allLinksLookOK={allLinksLookOK}
            />
          )}
          {step === 2 && (
            <PhonesAndKeywordsStep
              phones={phones}
              selected={selectedPhones}
              onToggle={togglePhone}
              onSetSelected={setSelectedPhones}
              search={phoneSearch}
              onSearch={setPhoneSearch}
              keywordsText={keywordsText}
              onKeywordsText={setKeywordsText}
              keywordList={keywordList}
              keywordsOk={keywordsOk}
            />
          )}
          {step === 3 && (
            <ConfigureStep
              candidatesPerPost={candidatesPerPost}
              onCandidatesPerPost={setCandidatesPerPost}
              targetCount={targetCount}
              prompts={prompts}
              commentPromptId={commentPromptId}
              onCommentPromptId={setCommentPromptId}
              commentMode={commentMode}
              onCommentMode={setCommentMode}
              providedComments={providedComments}
              onProvidedComments={setProvidedComments}
              providedCount={providedList.length}
              providedOk={providedOk}
            />
          )}
        </div>
        <Footer
          step={step}
          onBack={() => setStep((s) => (s > 1 ? ((s - 1) as Step) : s))}
          onNext={() => setStep((s) => (s < 3 ? ((s + 1) as Step) : s))}
          onCancel={guardedClose}
          onSubmit={submit}
          submitting={submitting}
          canStep1Next={canStep1Next}
          canStep2Next={canStep2Next}
          canSubmit={canSubmit}
          targetCount={targetCount}
          phoneCount={phoneCount}
          candidatesPerPost={candidatesPerPost}
        />
      </div>
    </div>,
    document.body,
  )
}

function Header({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <div className="px-[22px] py-[16px] border-b border-[var(--color-border)] flex items-start justify-between gap-3">
      <div className="text-[15.5px] font-semibold text-[var(--color-foreground)]">{title}</div>
      <button
        type="button"
        onClick={onClose}
        className="w-7 h-7 rounded-full text-[var(--color-muted-foreground)] hover:bg-[var(--color-gray-bg)] flex items-center justify-center"
        aria-label="close"
      >
        ✕
      </button>
    </div>
  )
}

function Stepper({ current }: { current: Step }) {
  const t = useStudioT()
  const steps = [
    { n: 1 as Step, label: t('fbComments.step1Title') },
    { n: 2 as Step, label: t('fbComments.step2Title') },
    { n: 3 as Step, label: t('fbComments.step3Title') },
  ]
  return (
    <div className="px-[22px] pt-[16px] pb-[4px] flex items-center">
      {steps.map((s, i) => {
        const isDone = current > s.n
        const isOn = current === s.n
        return (
          <div key={s.n} className="flex items-center flex-1 last:flex-none">
            <div
              className={cn(
                'flex items-center gap-2 text-[12.5px]',
                isOn && 'text-[var(--color-foreground)] font-semibold',
                isDone && 'text-[var(--color-muted-foreground)]',
                !isOn && !isDone && 'text-[var(--color-muted-foreground-2)]',
              )}
            >
              <div
                className={cn(
                  'w-[22px] h-[22px] rounded-full flex items-center justify-center text-[11.5px] font-semibold',
                  isOn && 'bg-[var(--color-primary)] text-[var(--color-primary-foreground)]',
                  isDone && 'bg-[var(--color-green)] text-white',
                  !isOn && !isDone && 'bg-[var(--color-gray-bg)] text-[var(--color-muted-foreground)]',
                )}
              >
                {isDone ? '✓' : s.n}
              </div>
              {s.label}
            </div>
            {i < steps.length - 1 ? (
              <div
                className={cn(
                  'flex-1 h-px mx-[10px]',
                  current > s.n ? 'bg-[var(--color-green)]' : 'bg-[var(--color-border)]',
                )}
              />
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

function StepHeading({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mb-3">
      <div className="text-[14.5px] font-semibold text-[var(--color-foreground)]">{title}</div>
      <div className="text-[12px] text-[var(--color-muted-foreground)] mt-0.5">{sub}</div>
    </div>
  )
}

function TargetsStep({
  mode,
  onMode,
  targets,
  selected,
  onToggle,
  manualLinks,
  onManualLinks,
  manualLinkList,
  allLinksLookOK,
}: {
  mode: Mode
  onMode: (m: Mode) => void
  targets: CommentTarget[]
  selected: Set<number>
  onToggle: (id: number) => void
  manualLinks: string
  onManualLinks: (v: string) => void
  manualLinkList: string[]
  allLinksLookOK: boolean
}) {
  const t = useStudioT()
  return (
    <>
      <StepHeading title={t('fbComments.step1Heading')} sub={t('fbComments.step1Sub')} />
      <Segmented
        value={mode}
        options={[
          { value: 'owned', label: t('fbComments.ownedMode') },
          { value: 'manual', label: t('fbComments.manualMode') },
        ]}
        onChange={(v) => onMode(v as Mode)}
      />
      <div className="mt-3">
        {mode === 'manual' ? (
          <>
            <textarea
              rows={6}
              value={manualLinks}
              onChange={(e) => onManualLinks(e.target.value)}
              placeholder={t('fbComments.linksPlaceholder')}
              className="w-full min-h-[140px] resize-y bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
            />
            <div className="mt-1.5 flex items-center justify-between text-[11.5px]">
              <span className="text-[var(--color-muted-foreground-2)]">{t('fbComments.manualUrl')}</span>
              <span
                className={cn(
                  'tabular-nums',
                  manualLinkList.length === 0
                    ? 'text-[var(--color-muted-foreground-2)]'
                    : allLinksLookOK
                      ? 'text-[var(--color-green)]'
                      : 'text-[var(--color-orange)]',
                )}
              >
                {t('fbComments.linksCount', { count: manualLinkList.length })}
              </span>
            </div>
            {!allLinksLookOK && manualLinkList.length > 0 ? (
              <div className="mt-1 text-[11.5px] text-[var(--color-orange)]">
                {t('fbComments.linksInvalidHint')}
              </div>
            ) : null}
          </>
        ) : targets.length === 0 ? (
          <div className="text-center py-8 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
            <div className="font-medium text-[var(--color-foreground-2)] mb-1">
              {t('fbComments.ownedEmptyTitle')}
            </div>
            <div>{t('fbComments.ownedEmptyBody')}</div>
          </div>
        ) : (
          <div className="border border-[var(--color-border)] rounded-[10px] max-h-[300px] overflow-y-auto">
            {targets.map((tg) => (
              <label
                key={tg.publication_id}
                className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 px-3 py-2.5 border-b border-[var(--color-border)] last:border-b-0 text-[13px] cursor-pointer hover:bg-[var(--color-muted)]"
              >
                <input
                  type="checkbox"
                  checked={selected.has(tg.publication_id)}
                  onChange={() => onToggle(tg.publication_id)}
                  className="accent-[var(--color-primary)] w-[14px] h-[14px]"
                />
                <div className="min-w-0">
                  <div className="font-medium text-[var(--color-foreground)] truncate">
                    {tg.video_filename ?? `#${tg.video_id}`}
                  </div>
                  <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] truncate">
                    {tg.account_profile_name ?? '—'}
                  </div>
                </div>
              </label>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

function PhonesAndKeywordsStep({
  phones,
  selected,
  onToggle,
  onSetSelected,
  search,
  onSearch,
  keywordsText,
  onKeywordsText,
  keywordList,
  keywordsOk,
}: {
  phones: EligiblePhone[]
  selected: Set<number>
  onToggle: (id: number) => void
  onSetSelected: (next: Set<number>) => void
  search: string
  onSearch: (v: string) => void
  keywordsText: string
  onKeywordsText: (v: string) => void
  keywordList: string[]
  keywordsOk: boolean
}) {
  const t = useStudioT()
  const groups = useMemo(() => {
    const q = search.trim().toLowerCase()
    const groupMap = new Map<string, EligiblePhone[]>()
    for (const p of phones) {
      if (q) {
        const matchName = (p.profile_name ?? '').toLowerCase().includes(q)
        const matchGroup = (p.group_name ?? '').toLowerCase().includes(q)
        if (!matchName && !matchGroup) continue
      }
      const key = p.group_name ?? UNGROUPED
      if (!groupMap.has(key)) groupMap.set(key, [])
      groupMap.get(key)!.push(p)
    }
    return Array.from(groupMap.entries())
      .map(([key, items]) => ({
        key,
        name: key === UNGROUPED ? t('fbComments.groupUngrouped') : key,
        items: items.slice().sort((a, b) =>
          (a.profile_name ?? '').localeCompare(b.profile_name ?? ''),
        ),
      }))
      .sort((a, b) => {
        if (a.key === UNGROUPED) return 1
        if (b.key === UNGROUPED) return -1
        return a.name.localeCompare(b.name)
      })
  }, [phones, search, t])

  const selectGroupAll = (items: EligiblePhone[]) => {
    const next = new Set(selected)
    for (const p of items) if (!p.busy) next.add(p.id)
    onSetSelected(next)
  }
  const clearGroup = (items: EligiblePhone[]) => {
    const next = new Set(selected)
    for (const p of items) next.delete(p.id)
    onSetSelected(next)
  }
  const toggleGroup = (items: EligiblePhone[], allSelected: boolean) => {
    if (allSelected) clearGroup(items)
    else selectGroupAll(items)
  }
  const selectAllAvailable = () => {
    onSetSelected(new Set(phones.filter((p) => !p.busy).map((p) => p.id)))
  }
  const clearAll = () => onSetSelected(new Set())

  const visibleCount = groups.reduce((n, g) => n + g.items.length, 0)
  const keywordTooMany = keywordList.length > 10
  const keywordTooLong = keywordList.some((k) => k.length > 100)

  return (
    <>
      <StepHeading title={t('fbComments.step2Heading')} sub={t('fbComments.step2Sub')} />
      <div className="relative mb-3">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-muted-foreground-2)] text-[12px] pointer-events-none">
          ⌕
        </span>
        <input
          type="text"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={t('fbComments.searchPhones')}
          className="w-full h-[34px] bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] pl-9 pr-3 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
        />
      </div>

      {phones.length === 0 ? (
        <div className="text-center py-10 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
          {t('fbComments.noPhones')}
        </div>
      ) : visibleCount === 0 ? (
        <div className="text-center py-10 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
          {t('fbComments.searchEmpty')}
        </div>
      ) : (
        <div className="border border-[var(--color-border)] rounded-[12px] overflow-hidden bg-[var(--color-card)] max-h-[300px] overflow-y-auto">
          {groups.map((g, idx) => {
            const total = g.items.length
            const selectedCount = g.items.filter((p) => selected.has(p.id)).length
            const allSelected = selectedCount === total && total > 0
            const someSelected = selectedCount > 0 && selectedCount < total
            return (
              <div key={g.key} className={cn(idx > 0 && 'border-t border-[var(--color-border)]')}>
                <div className="sticky top-0 z-10 bg-[var(--color-muted)] border-b border-[var(--color-border)] px-3.5 py-2.5 flex items-center gap-2.5">
                  <TriCheckbox
                    state={allSelected ? 'on' : someSelected ? 'indet' : 'off'}
                    onClick={() => toggleGroup(g.items, allSelected)}
                  />
                  <div className="text-[13px] font-semibold text-[var(--color-foreground)]">{g.name}</div>
                  <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] tabular-nums">
                    {selectedCount} / {total}
                  </div>
                  <div className="ml-auto flex items-center gap-1.5 text-[11.5px]">
                    <button
                      type="button"
                      className="text-[var(--color-blue)] hover:underline"
                      onClick={() => selectGroupAll(g.items)}
                    >
                      {t('fbComments.selectAll')}
                    </button>
                    <span className="text-[var(--color-muted-foreground-3)]">·</span>
                    <button
                      type="button"
                      className="text-[var(--color-blue)] hover:underline"
                      onClick={() => clearGroup(g.items)}
                    >
                      {t('fbComments.clearSelection')}
                    </button>
                  </div>
                </div>
                <div
                  className="px-3 py-3 grid gap-1.5"
                  style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}
                >
                  {g.items.map((p) => {
                    const isOn = selected.has(p.id)
                    const isBusy = p.busy
                    const isCapped = !isBusy && p.daily_limit_reached
                    return (
                      <button
                        key={p.id}
                        type="button"
                        disabled={isBusy}
                        onClick={() => onToggle(p.id)}
                        title={
                          isCapped
                            ? t('fbComments.dailyLimitQueueTitle', {
                                used: p.daily_used,
                                limit: p.daily_limit,
                              })
                            : undefined
                        }
                        className={cn(
                          'h-[32px] px-2.5 rounded-[8px] border-[1.5px] text-[13px] flex items-center gap-1.5 text-left select-none transition-colors',
                          isOn && !isBusy && !isCapped && 'border-[var(--color-border-strong)] bg-[var(--color-muted)] text-[var(--color-foreground)] font-medium',
                          isOn && isCapped && 'border-[var(--color-amber,#d97706)] bg-[var(--color-amber-bg,#fef3c7)] text-[var(--color-amber,#92400e)] font-medium',
                          !isOn && !isBusy && !isCapped && 'border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-foreground-2)] hover:border-[var(--color-muted-foreground-3)]',
                          !isOn && isCapped && 'border-[var(--color-amber,#d97706)]/40 bg-[var(--color-card)] text-[var(--color-foreground-2)] hover:border-[var(--color-amber,#d97706)]',
                          isBusy && 'border-[var(--color-border)] bg-[var(--color-muted)] text-[var(--color-muted-foreground-2)] cursor-not-allowed opacity-70',
                        )}
                      >
                        <span className="truncate flex-1 min-w-0">{p.profile_name ?? `#${p.id}`}</span>
                        {isBusy ? (
                          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-[var(--color-orange)] text-white leading-none">
                            {t('fbComments.busy')}
                          </span>
                        ) : isCapped ? (
                          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-[var(--color-amber,#d97706)] text-white leading-none">
                            {t('fbComments.queueTomorrow')}
                          </span>
                        ) : null}
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
      <div className="mt-2.5 flex justify-between items-center text-[11.5px] text-[var(--color-muted-foreground)]">
        <span>
          {t('fbComments.selectedCount', { selected: selected.size, total: phones.length })}
        </span>
        <span className="flex items-center gap-1.5">
          <button
            type="button"
            className="text-[var(--color-blue)] hover:underline"
            onClick={selectAllAvailable}
          >
            {t('fbComments.selectAllAvailable')}
          </button>
          <span className="text-[var(--color-muted-foreground-3)]">·</span>
          <button
            type="button"
            className="text-[var(--color-blue)] hover:underline"
            onClick={clearAll}
          >
            {t('fbComments.clearSelection')}
          </button>
        </span>
      </div>

      <div className="mt-5">
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-[12px] font-medium text-[var(--color-foreground-2)]">
            {t('fbComments.keywords')}
          </label>
          <span
            className={cn(
              'text-[11.5px] tabular-nums',
              keywordList.length === 0
                ? 'text-[var(--color-muted-foreground-2)]'
                : keywordsOk
                  ? 'text-[var(--color-muted-foreground)]'
                  : 'text-[var(--color-orange)]',
            )}
          >
            {keywordList.length} / 10
          </span>
        </div>
        <textarea
          rows={4}
          value={keywordsText}
          onChange={(e) => onKeywordsText(e.target.value)}
          placeholder={t('fbComments.keywordsPlaceholder')}
          className="w-full min-h-[96px] resize-y bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
        />
        <div className="mt-1 text-[11.5px] text-[var(--color-muted-foreground-2)]">
          {t('fbComments.keywordsHelp')}
        </div>
        {keywordTooMany ? (
          <div className="mt-1 text-[11.5px] text-[var(--color-orange)]">
            {t('fbComments.keywordsTooMany')}
          </div>
        ) : null}
        {keywordTooLong ? (
          <div className="mt-1 text-[11.5px] text-[var(--color-orange)]">
            {t('fbComments.keywordsTooLong')}
          </div>
        ) : null}
      </div>
    </>
  )
}

function TriCheckbox({
  state,
  onClick,
}: {
  state: 'off' | 'on' | 'indet'
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-checked={state === 'on'}
      role="checkbox"
      className={cn(
        'w-[16px] h-[16px] rounded-[3px] border-[1.5px] flex items-center justify-center relative transition-colors',
        state === 'off' && 'border-[var(--color-muted-foreground-3)] bg-[var(--color-card)]',
        state === 'on' && 'border-[var(--color-primary)] bg-[var(--color-primary)]',
        state === 'indet' && 'border-[var(--color-primary)] bg-[var(--color-primary)]',
      )}
    >
      {state === 'on' ? (
        <span className="block w-[5px] h-[9px] border-r-[1.5px] border-b-[1.5px] border-white rotate-45 -mt-0.5" />
      ) : state === 'indet' ? (
        <span className="block w-[8px] h-[2px] bg-white rounded-[1px]" />
      ) : null}
    </button>
  )
}

function ConfigureStep({
  candidatesPerPost,
  onCandidatesPerPost,
  targetCount,
  prompts,
  commentPromptId,
  onCommentPromptId,
  commentMode,
  onCommentMode,
  providedComments,
  onProvidedComments,
  providedCount,
  providedOk,
}: {
  candidatesPerPost: number
  onCandidatesPerPost: (v: number) => void
  targetCount: number
  prompts: CommentPrompt[]
  commentPromptId: number | null
  onCommentPromptId: (v: number | null) => void
  commentMode: CommentMode
  onCommentMode: (m: CommentMode) => void
  providedComments: string
  onProvidedComments: (v: string) => void
  providedCount: number
  providedOk: boolean
}) {
  const t = useStudioT()
  const studioHref = useStudioHref()
  return (
    <>
      <StepHeading title={t('fbComments.step3Heading')} sub={t('fbComments.step3Sub')} />
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-[var(--color-muted)] rounded-[10px] p-3">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-muted-foreground)] mb-2">
            {t('fbComments.candidatesPerPost')}
          </div>
          <div className="inline-flex items-center border border-[var(--color-border)] rounded-[8px] overflow-hidden bg-white">
            <button
              type="button"
              onClick={() => onCandidatesPerPost(Math.max(1, candidatesPerPost - 1))}
              className="w-7 h-[30px] text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]"
            >
              −
            </button>
            <input
              type="number"
              min={1}
              max={10}
              value={candidatesPerPost}
              onChange={(e) => {
                const n = Number(e.target.value)
                onCandidatesPerPost(Number.isFinite(n) ? Math.max(1, Math.min(10, n)) : 1)
              }}
              className="w-[44px] h-[30px] text-center text-[14px] font-medium border-x border-[var(--color-border)] bg-white focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
            <button
              type="button"
              onClick={() => onCandidatesPerPost(Math.min(10, candidatesPerPost + 1))}
              className="w-7 h-[30px] text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]"
            >
              +
            </button>
          </div>
          <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] mt-1.5">
            {t('fbComments.candidatesHelp')}
          </div>
        </div>
        <div className="bg-[var(--color-muted)] rounded-[10px] p-3">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-muted-foreground)] mb-2">
            {t('fbComments.targetsTitle')}
          </div>
          <div className="text-[26px] font-bold tracking-tight leading-none text-[var(--color-foreground)]">
            {targetCount}
            <span className="text-[13px] font-medium text-[var(--color-muted-foreground)] ml-1.5">
              {t('fbComments.targetIsPost')}
            </span>
          </div>
          <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] mt-1.5">
            {t('fbComments.targetsBreakdown', {
              targets: targetCount,
              candidates: candidatesPerPost,
            })}
          </div>
        </div>
      </div>

      <div className="mb-3 flex items-center justify-between gap-3">
        <label className="text-[12px] font-medium text-[var(--color-foreground-2)]">
          {t('fbComments.commentSourceLabel')}
        </label>
        <Segmented
          value={commentMode}
          options={[
            { value: 'generate', label: t('fbComments.commentSourceGenerate') },
            { value: 'provided', label: t('fbComments.commentSourceProvided') },
          ]}
          onChange={(v) => onCommentMode(v as CommentMode)}
        />
      </div>

      {commentMode === 'generate' ? (
        <div>
          <div className="flex items-end justify-between mb-1.5 gap-3">
            <label className="block text-[12px] font-medium text-[var(--color-foreground-2)]">
              {t('fbComments.commentPromptLabel')}
            </label>
            <a
              href={studioHref('/settings#comment-prompts')}
              target="_blank"
              rel="noreferrer"
              className="text-[11.5px] text-[var(--color-blue)] hover:underline"
            >
              {t('fbComments.managePrompts')}
            </a>
          </div>
          {prompts.length === 0 ? (
            <div className="text-[12.5px] text-[var(--color-muted-foreground-2)] bg-[var(--color-muted)] rounded-[10px] px-3 py-3 border border-dashed border-[var(--color-border)]">
              {t('fbComments.noPromptsHint')}
            </div>
          ) : (
            <>
              <select
                value={commentPromptId ?? ''}
                onChange={(e) => {
                  const v = e.target.value
                  onCommentPromptId(v === '' ? null : Number(v))
                }}
                className="w-full bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-foreground)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
              >
                <option value="">{t('fbComments.commentPromptPlaceholder')}</option>
                {prompts.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {commentPromptId != null && (
                <pre className="mt-2 text-[11.5px] text-[var(--color-muted-foreground)] bg-[var(--color-muted)] rounded-[10px] px-3 py-2 max-h-[120px] overflow-y-auto whitespace-pre-wrap font-mono leading-relaxed">
                  {prompts.find((p) => p.id === commentPromptId)?.body ?? ''}
                </pre>
              )}
            </>
          )}
        </div>
      ) : (
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-[12px] font-medium text-[var(--color-foreground-2)]">
              {t('fbComments.providedListLabel')}
            </label>
            <span
              className={cn(
                'text-[11.5px] tabular-nums',
                providedCount === 0
                  ? 'text-[var(--color-muted-foreground-2)]'
                  : providedOk
                    ? 'text-[var(--color-muted-foreground)]'
                    : 'text-[var(--color-orange)]',
              )}
            >
              {providedCount} / 10
            </span>
          </div>
          <textarea
            rows={8}
            value={providedComments}
            onChange={(e) => onProvidedComments(e.target.value)}
            placeholder={t('fbComments.providedPlaceholder')}
            className="w-full min-h-[160px] resize-y bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
          />
          <div className="mt-1.5 text-[11.5px] text-[var(--color-muted-foreground-2)]">
            {t('fbComments.providedHelp')}
          </div>
          {!providedOk && providedCount > 0 ? (
            <div className="mt-1 text-[11.5px] text-[var(--color-orange)]">
              {t('fbComments.providedInvalidHint')}
            </div>
          ) : null}
        </div>
      )}
    </>
  )
}

function Segmented({
  value,
  options,
  onChange,
}: {
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (v: string) => void
}) {
  return (
    <div className="inline-flex bg-[var(--color-gray-bg)] p-0.5 rounded-[8px] gap-0">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            'px-3 py-1 text-[12.5px] font-medium rounded-[6px]',
            value === opt.value
              ? 'bg-[var(--color-card)] text-[var(--color-foreground)] shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
              : 'text-[var(--color-muted-foreground)]',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

function Footer({
  step,
  onBack,
  onNext,
  onCancel,
  onSubmit,
  submitting,
  canStep1Next,
  canStep2Next,
  canSubmit,
  targetCount,
  phoneCount,
  candidatesPerPost,
}: {
  step: Step
  onBack: () => void
  onNext: () => void
  onCancel: () => void
  onSubmit: () => void
  submitting: boolean
  canStep1Next: boolean
  canStep2Next: boolean
  canSubmit: boolean
  targetCount: number
  phoneCount: number
  candidatesPerPost: number
}) {
  const t = useStudioT()
  const leftLabel: ReactNode =
    step === 1
      ? t('fbComments.selectedTargets', { count: targetCount })
      : step === 2
        ? t('fbComments.selectedPhones', { count: phoneCount })
        : t('fbComments.estimate', {
            targets: targetCount,
            candidates: candidatesPerPost,
          })

  return (
    <div className="px-[22px] py-3.5 border-t border-[var(--color-border)] bg-[var(--color-muted)] flex justify-between items-center gap-2.5">
      <div className="text-[12px] text-[var(--color-muted-foreground)] min-w-0 truncate">{leftLabel}</div>
      <div className="flex gap-2 flex-shrink-0">
        {step > 1 ? (
          <Button type="button" variant="secondary" onClick={onBack}>
            ← {t('common.back')}
          </Button>
        ) : null}
        <Button type="button" variant="secondary" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        {step < 3 ? (
          <Button
            type="button"
            variant="primary"
            onClick={onNext}
            disabled={step === 1 ? !canStep1Next : !canStep2Next}
          >
            {t('common.next')} →
          </Button>
        ) : (
          <Button type="button" variant="primary" onClick={onSubmit} disabled={!canSubmit}>
            {submitting ? t('fbComments.starting') : t('fbComments.submit')}
          </Button>
        )}
      </div>
    </div>
  )
}
