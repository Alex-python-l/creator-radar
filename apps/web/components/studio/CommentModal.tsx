'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useStudioT } from '@/lib/studio/i18n'
import { useStudioHref } from '@/lib/studio/nav'
import { api, type CommentTarget, type EligiblePhone, type DiscoverTarget, type CommentPrompt } from '@/lib/studio/api'
import { Button } from '@/components/ui'
import { cn } from '@/lib/studio/cn'

type Mode = 'owned' | 'discover' | 'manual'
type Step = 1 | 2 | 3
type CommentMode = 'generate' | 'provided'

export type CommentSubmitPayload = {
  account_ids: number[]
  target_links: string[]
  target_publication_ids: number[]
  comments_per_video: number
  comment_prompt_id: number | null
  comment_mode?: 'studio_ai' | 'provided'
  provided_comments?: string[] | null
}

const UNGROUPED = '__ungrouped__'

export function CommentModal({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (payload: CommentSubmitPayload) => Promise<void>
}) {
  const t = useStudioT()
  const cardRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLElement | null>(null)

  const [step, setStep] = useState<Step>(1)
  const [phones, setPhones] = useState<EligiblePhone[]>([])
  const [targets, setTargets] = useState<CommentTarget[]>([])
  const [selectedPhones, setSelectedPhones] = useState<Set<number>>(new Set())
  const [selectedTargets, setSelectedTargets] = useState<Set<number>>(new Set())
  const [discoverTargets, setDiscoverTargets] = useState<DiscoverTarget[]>([])
  const [selectedDiscover, setSelectedDiscover] = useState<Set<string>>(new Set())
  const [discoverLoadFailed, setDiscoverLoadFailed] = useState(false)
  const [mode, setMode] = useState<Mode>('owned')
  const [manualLinks, setManualLinks] = useState('')
  const [perVideo, setPerVideo] = useState(3)
  const [prompts, setPrompts] = useState<CommentPrompt[]>([])
  const [commentPromptId, setCommentPromptId] = useState<number | null>(null)
  const [commentMode, setCommentMode] = useState<CommentMode>('generate')
  const [providedComments, setProvidedComments] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [phoneSearch, setPhoneSearch] = useState('')

  useEffect(() => {
    if (!open) return
    let cancelled = false
    triggerRef.current = (document.activeElement as HTMLElement) ?? null
    const stepTimer = window.setTimeout(() => setStep(1), 0)
    Promise.all([
      api.eligiblePhones(),
      api.engagementTargets(),
      api.discoverTargets().catch(() => null),
      api.commentPrompts().catch(() => [] as CommentPrompt[]),
    ])
      .then(([p, t, d, pr]) => {
        if (cancelled) return
        setPhones(p.items)
        setTargets(t.items)
        if (d) {
          setDiscoverTargets(d.items)
          setDiscoverLoadFailed(false)
        } else {
          setDiscoverTargets([])
          setDiscoverLoadFailed(true)
        }
        setSelectedPhones(new Set(p.items.filter((x) => !x.busy).map((x) => x.id)))
        setSelectedTargets(new Set())
        setSelectedDiscover(new Set())
        setPrompts(pr)
        setCommentPromptId(pr.length === 1 ? pr[0].id : null)
      })
      .catch(() => {})
    return () => {
      cancelled = true
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

  // Block every close affordance while a submit is in flight. Closing
  // mid-submit left the create request running in the background; the
  // operator, seeing the modal gone, would reopen it (fresh state) and
  // submit again — two engagement tasks, double the runs. The submit
  // button's own !submitting guard didn't cover Esc / scrim / ✕.
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

  const providedCommentList = useMemo(
    () =>
      providedComments
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    [providedComments],
  )

  const targetCount =
    mode === 'owned'    ? selectedTargets.size :
    mode === 'discover' ? selectedDiscover.size :
                          manualLinkList.length
  const phoneCount = selectedPhones.size
  const totalRuns = targetCount * perVideo
  const reuseHint = perVideo > phoneCount && phoneCount > 0
  const providedShortHint =
    commentMode === 'provided' && providedCommentList.length > 0 && providedCommentList.length < totalRuns

  const canStep1Next = targetCount > 0
  const canStep2Next = phoneCount > 0
  const canSubmit =
    !submitting &&
    phoneCount > 0 &&
    targetCount > 0 &&
    perVideo >= 1 &&
    perVideo <= 20 &&
    (
      (commentMode === 'generate' && commentPromptId != null) ||
      (commentMode === 'provided' && providedCommentList.length > 0)
    )

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
      await onSubmit({
        account_ids: [...selectedPhones],
        target_links:
          mode === 'manual'   ? manualLinkList :
          mode === 'discover' ? [...selectedDiscover] :
                                [],
        target_publication_ids:
          mode === 'owned' ? [...selectedTargets] : [],
        comments_per_video: perVideo,
        comment_prompt_id: commentMode === 'generate' ? commentPromptId : null,
        comment_mode: commentMode === 'provided' ? 'provided' : 'studio_ai',
        provided_comments: commentMode === 'provided' ? providedCommentList : null,
      })
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
        aria-label={t('comments.modalTitle')}
        className="w-[720px] max-w-[92vw] max-h-[86vh] flex flex-col bg-[var(--color-card)] border border-[var(--color-border)] rounded-[var(--radius-card)] shadow-[0_24px_48px_rgba(0,0,0,0.18)] overflow-hidden modal-card-in"
        onClick={(e) => e.stopPropagation()}
      >
        <Header onClose={guardedClose} title={t('comments.modalTitle')} />
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
              discoverTargets={discoverTargets}
              selectedDiscover={selectedDiscover}
              onToggleDiscover={(url) => {
                setSelectedDiscover((prev) => {
                  const next = new Set(prev)
                  if (next.has(url)) next.delete(url)
                  else next.add(url)
                  return next
                })
              }}
              onSelectAllDiscover={() =>
                setSelectedDiscover(new Set(discoverTargets.map((it) => it.url)))
              }
              onClearAllDiscover={() => setSelectedDiscover(new Set())}
              discoverLoadFailed={discoverLoadFailed}
            />
          )}
          {step === 2 && (
            <PhonesByGroupStep
              phones={phones}
              selected={selectedPhones}
              onToggle={togglePhone}
              onSetSelected={setSelectedPhones}
              search={phoneSearch}
              onSearch={setPhoneSearch}
            />
          )}
          {step === 3 && (
            <ConfigureStep
              perVideo={perVideo}
              onPerVideo={setPerVideo}
              reuseHint={reuseHint}
              prompts={prompts}
              commentPromptId={commentPromptId}
              onCommentPromptId={setCommentPromptId}
              totalRuns={totalRuns}
              targetCount={targetCount}
              commentMode={commentMode}
              onCommentMode={setCommentMode}
              providedComments={providedComments}
              onProvidedComments={setProvidedComments}
              providedCount={providedCommentList.length}
              providedShortHint={providedShortHint}
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
          totalRuns={totalRuns}
          perVideo={perVideo}
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
    { n: 1 as Step, label: t('comments.step1Title') },
    { n: 2 as Step, label: t('comments.step2Title') },
    { n: 3 as Step, label: t('comments.step3Title') },
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
  discoverTargets,
  selectedDiscover,
  onToggleDiscover,
  onSelectAllDiscover,
  onClearAllDiscover,
  discoverLoadFailed,
}: {
  mode: Mode
  onMode: (m: Mode) => void
  targets: CommentTarget[]
  selected: Set<number>
  onToggle: (id: number) => void
  manualLinks: string
  onManualLinks: (v: string) => void
  discoverTargets: DiscoverTarget[]
  selectedDiscover: Set<string>
  onToggleDiscover: (url: string) => void
  onSelectAllDiscover: () => void
  onClearAllDiscover: () => void
  discoverLoadFailed: boolean
}) {
  const t = useStudioT()
  return (
    <>
      <StepHeading title={t('comments.step1Heading')} sub={t('comments.step1Sub')} />
      <Segmented
        value={mode}
        options={[
          { value: 'owned',    label: t('comments.ownedMode') },
          { value: 'discover', label: t('comments.discoverMode') },
          { value: 'manual',   label: t('comments.manualMode') },
        ]}
        onChange={(v) => onMode(v as Mode)}
      />
      <div className="mt-3">
        {mode === 'manual' ? (
          <textarea
            rows={6}
            value={manualLinks}
            onChange={(e) => onManualLinks(e.target.value)}
            placeholder={t('comments.linksPlaceholder')}
            className="w-full min-h-[140px] resize-y bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
          />
        ) : mode === 'discover' ? (
          <DiscoverList
            items={discoverTargets}
            selected={selectedDiscover}
            onToggle={onToggleDiscover}
            onSelectAll={onSelectAllDiscover}
            onClearAll={onClearAllDiscover}
            loadFailed={discoverLoadFailed}
          />
        ) : targets.length === 0 ? (
          <div className="text-center py-8 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
            <div className="font-medium text-[var(--color-foreground-2)] mb-1">{t('comments.ownedEmptyTitle')}</div>
            <div>{t('comments.ownedEmptyBody')}</div>
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
                  <div className="font-medium text-[var(--color-foreground)] truncate">{tg.video_filename ?? `#${tg.video_id}`}</div>
                  <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] truncate">{tg.account_profile_name ?? '—'}</div>
                </div>
              </label>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

function PhonesByGroupStep({
  phones,
  selected,
  onToggle,
  onSetSelected,
  search,
  onSearch,
}: {
  phones: EligiblePhone[]
  selected: Set<number>
  onToggle: (id: number) => void
  onSetSelected: (next: Set<number>) => void
  search: string
  onSearch: (v: string) => void
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
        name: key === UNGROUPED ? t('comments.groupUngrouped') : key,
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

  return (
    <>
      <StepHeading title={t('comments.step2Heading')} sub={t('comments.step2Sub')} />
      <div className="relative mb-3">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-muted-foreground-2)] text-[12px] pointer-events-none">⌕</span>
        <input
          type="text"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={t('comments.searchPhones')}
          className="w-full h-[34px] bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] pl-9 pr-3 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
        />
      </div>

      {phones.length === 0 ? (
        <div className="text-center py-10 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
          {t('comments.noPhones')}
        </div>
      ) : visibleCount === 0 ? (
        <div className="text-center py-10 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
          {t('comments.searchEmpty')}
        </div>
      ) : (
        <div className="border border-[var(--color-border)] rounded-[12px] overflow-hidden bg-[var(--color-card)] max-h-[380px] overflow-y-auto">
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
                      {t('comments.selectAll')}
                    </button>
                    <span className="text-[var(--color-muted-foreground-3)]">·</span>
                    <button
                      type="button"
                      className="text-[var(--color-blue)] hover:underline"
                      onClick={() => clearGroup(g.items)}
                    >
                      {t('comments.clearSelection')}
                    </button>
                  </div>
                </div>
                <div className="px-3 py-3 grid gap-1.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}>
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
                            ? t('comments.dailyLimitQueueTitle', {
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
                            {t('comments.busy')}
                          </span>
                        ) : isCapped ? (
                          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-[var(--color-amber,#d97706)] text-white leading-none">
                            {t('comments.queueTomorrow')}
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
          {t('comments.selectedCount', { selected: selected.size, total: phones.length })}
        </span>
        <span className="flex items-center gap-1.5">
          <button type="button" className="text-[var(--color-blue)] hover:underline" onClick={selectAllAvailable}>
            {t('comments.selectAllAvailable')}
          </button>
          <span className="text-[var(--color-muted-foreground-3)]">·</span>
          <button type="button" className="text-[var(--color-blue)] hover:underline" onClick={clearAll}>
            {t('comments.clearSelection')}
          </button>
        </span>
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
  perVideo,
  onPerVideo,
  reuseHint,
  prompts,
  commentPromptId,
  onCommentPromptId,
  totalRuns,
  targetCount,
  commentMode,
  onCommentMode,
  providedComments,
  onProvidedComments,
  providedCount,
  providedShortHint,
}: {
  perVideo: number
  onPerVideo: (v: number) => void
  reuseHint: boolean
  prompts: CommentPrompt[]
  commentPromptId: number | null
  onCommentPromptId: (v: number | null) => void
  totalRuns: number
  targetCount: number
  commentMode: CommentMode
  onCommentMode: (m: CommentMode) => void
  providedComments: string
  onProvidedComments: (v: string) => void
  providedCount: number
  providedShortHint: boolean
}) {
  const t = useStudioT()
  const studioHref = useStudioHref()
  return (
    <>
      <StepHeading title={t('comments.step3Heading')} sub={t('comments.step3Sub')} />
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-[var(--color-muted)] rounded-[10px] p-3">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-muted-foreground)] mb-2">
            {t('comments.commentsPerVideo')}
          </div>
          <div className="inline-flex items-center border border-[var(--color-border)] rounded-[8px] overflow-hidden bg-white">
            <button
              type="button"
              onClick={() => onPerVideo(Math.max(1, perVideo - 1))}
              className="w-7 h-[30px] text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]"
            >
              −
            </button>
            <input
              type="number"
              min={1}
              max={20}
              value={perVideo}
              onChange={(e) => {
                const n = Number(e.target.value)
                onPerVideo(Number.isFinite(n) ? Math.max(1, Math.min(20, n)) : 1)
              }}
              className="w-[44px] h-[30px] text-center text-[14px] font-medium border-x border-[var(--color-border)] bg-white focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
            <button
              type="button"
              onClick={() => onPerVideo(Math.min(20, perVideo + 1))}
              className="w-7 h-[30px] text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]"
            >
              +
            </button>
          </div>
          <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] mt-1.5">
            {t('comments.commentsPerVideoHelp')}
          </div>
          {reuseHint ? (
            <div className="text-[11.5px] text-[var(--color-orange)] mt-1">
              {t('comments.phoneReuseHint')}
            </div>
          ) : null}
        </div>
        <div className="bg-[var(--color-muted)] rounded-[10px] p-3">
          <div className="text-[11.5px] uppercase tracking-wider text-[var(--color-muted-foreground)] mb-2">
            {t('comments.estimateTitle')}
          </div>
          <div className="text-[26px] font-bold tracking-tight leading-none text-[var(--color-foreground)]">
            {totalRuns}
            <span className="text-[13px] font-medium text-[var(--color-muted-foreground)] ml-1.5">
              {t('comments.estimateUnit')}
            </span>
          </div>
          <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] mt-1.5">
            {t('comments.estimateBreakdown', { videos: targetCount, perVideo })}
          </div>
        </div>
      </div>

      <div className="mb-3 flex items-center justify-between gap-3">
        <label className="text-[12px] font-medium text-[var(--color-foreground-2)]">
          {t('comments.commentSourceLabel')}
        </label>
        <Segmented
          value={commentMode}
          options={[
            { value: 'generate', label: t('comments.commentSourceGenerate') },
            { value: 'provided', label: t('comments.commentSourceProvided') },
          ]}
          onChange={(v) => onCommentMode(v as CommentMode)}
        />
      </div>

      {commentMode === 'generate' ? (
        <div>
          <div className="flex items-end justify-between mb-1.5 gap-3">
            <label className="block text-[12px] font-medium text-[var(--color-foreground-2)]">
              {t('comments.commentPromptLabel')}
            </label>
            <a
              href={studioHref('/settings#comment-prompts')}
              target="_blank"
              rel="noreferrer"
              className="text-[11.5px] text-[var(--color-blue)] hover:underline"
            >
              {t('comments.managePrompts')}
            </a>
          </div>
          {prompts.length === 0 ? (
            <div className="text-[12.5px] text-[var(--color-muted-foreground-2)] bg-[var(--color-muted)] rounded-[10px] px-3 py-3 border border-dashed border-[var(--color-border)]">
              {t('comments.noPromptsHint')}
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
                <option value="">{t('comments.commentPromptPlaceholder')}</option>
                {prompts.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
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
              {t('comments.providedListLabel')}
            </label>
            <span className="text-[11.5px] text-[var(--color-muted-foreground-2)] tabular-nums">
              {t('comments.providedCountFmt', { count: providedCount, need: totalRuns })}
            </span>
          </div>
          <textarea
            rows={8}
            value={providedComments}
            onChange={(e) => onProvidedComments(e.target.value)}
            placeholder={t('comments.providedPlaceholder')}
            className="w-full min-h-[160px] resize-y bg-[var(--color-card)] border border-[var(--color-border)] rounded-[10px] px-3 py-2 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
          />
          <div className="mt-1.5 text-[11.5px] text-[var(--color-muted-foreground-2)]">
            {t('comments.providedHelp')}
          </div>
          {providedShortHint ? (
            <div className="mt-1 text-[11.5px] text-[var(--color-orange)]">
              {t('comments.providedShortHint', { count: providedCount, need: totalRuns })}
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
  totalRuns,
  perVideo,
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
  totalRuns: number
  perVideo: number
}) {
  const t = useStudioT()
  const leftLabel: ReactNode =
    step === 1
      ? t('comments.selectedTargets', { count: targetCount })
      : step === 2
        ? t('comments.selectedPhones', { count: phoneCount })
        : t('comments.estimate', { count: totalRuns, videos: targetCount, perVideo })

  return (
    <div className="px-[22px] py-3.5 border-t border-[var(--color-border)] bg-[var(--color-muted)] flex justify-between items-center gap-2.5">
      <div className="text-[12px] text-[var(--color-muted-foreground)] min-w-0 truncate">{leftLabel}</div>
      <div className="flex gap-2 flex-shrink-0">
        {step > 1 ? (
          <Button type="button" variant="secondary" onClick={onBack}>
            ← {t('comments.stepBack')}
          </Button>
        ) : null}
        <Button type="button" variant="secondary" onClick={onCancel}>
          {t('comments.cancel')}
        </Button>
        {step < 3 ? (
          <Button
            type="button"
            variant="primary"
            onClick={onNext}
            disabled={step === 1 ? !canStep1Next : !canStep2Next}
          >
            {t('comments.stepNext')} →
          </Button>
        ) : (
          <Button type="button" variant="primary" onClick={onSubmit} disabled={!canSubmit}>
            {submitting ? t('comments.starting') : t('comments.start')}
          </Button>
        )}
      </div>
    </div>
  )
}

type DiscoverSort = 'plays' | 'newest' | 'keyword'

function DiscoverList({
  items, selected, onToggle, onClearAll, loadFailed,
}: {
  items: DiscoverTarget[]
  selected: Set<string>
  onToggle: (url: string) => void
  onSelectAll: () => void
  onClearAll: () => void
  loadFailed: boolean
}) {
  const t = useStudioT()
  const [query, setQuery] = useState('')
  const [sortBy, setSortBy] = useState<DiscoverSort>('plays')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    let list = items
    if (q) {
      list = items.filter((it) => {
        const title = (it.title ?? '').toLowerCase()
        const author = (it.author_nickname ?? '').toLowerCase()
        const kw = (it.search_keyword ?? '').toLowerCase()
        return title.includes(q) || author.includes(q) || kw.includes(q)
      })
    }
    const sorted = list.slice()
    if (sortBy === 'plays') {
      sorted.sort((a, b) => (b.play_count ?? 0) - (a.play_count ?? 0))
    } else if (sortBy === 'newest') {
      sorted.sort((a, b) => {
        const ta = a.create_time ? Date.parse(a.create_time) : 0
        const tb = b.create_time ? Date.parse(b.create_time) : 0
        return tb - ta
      })
    } else {
      sorted.sort((a, b) => {
        const ka = (a.search_keyword ?? '').localeCompare(b.search_keyword ?? '')
        if (ka !== 0) return ka
        return (b.play_count ?? 0) - (a.play_count ?? 0)
      })
    }
    return sorted
  }, [items, query, sortBy])

  if (loadFailed) {
    return (
      <div className="text-center py-8 text-[13px] text-[var(--color-destructive)] border border-dashed border-[var(--color-border)] rounded-[10px]">
        {t('comments.loadFailed')}
      </div>
    )
  }
  if (items.length === 0) {
    return (
      <div className="text-center py-8 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
        <div className="font-medium text-[var(--color-foreground-2)] mb-1">
          {t('comments.discoverEmptyTitle')}
        </div>
        <div>{t('comments.discoverEmptyBody')}</div>
      </div>
    )
  }

  const onSelectAllFiltered = () => {
    for (const it of filtered) {
      if (!selected.has(it.url)) onToggle(it.url)
    }
  }

  return (
    <>
      <div className="flex items-center gap-2 mb-2">
        <div className="relative flex-1">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-muted-foreground-2)] text-[12px] pointer-events-none">⌕</span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('comments.discoverSearchPlaceholder')}
            className="w-full h-[32px] bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] pl-8 pr-3 text-[13px] text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none"
          />
        </div>
        <label className="inline-flex items-center gap-1.5 h-8 px-2.5 border border-[var(--color-border)] rounded-[8px] bg-[var(--color-card)] text-[12px] text-[var(--color-foreground-2)]">
          <span className="text-[var(--color-muted-foreground-2)]">{t('comments.discoverSortLabel')}</span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as DiscoverSort)}
            className="bg-transparent text-[var(--color-foreground)] font-medium focus:outline-none"
          >
            <option value="plays">{t('comments.discoverSortPlays')}</option>
            <option value="newest">{t('comments.discoverSortNewest')}</option>
            <option value="keyword">{t('comments.discoverSortKeyword')}</option>
          </select>
        </label>
      </div>
      <div className="flex items-center justify-between gap-1.5 text-[11.5px] mb-1.5">
        <span className="text-[var(--color-muted-foreground-2)] tabular-nums">
          {t('comments.discoverSelectedCount', { selected: selected.size, total: filtered.length })}
        </span>
        <div className="flex items-center gap-1.5">
          <button type="button" className="text-[var(--color-blue)] hover:underline" onClick={onSelectAllFiltered}>
            {t('comments.selectAll')}
          </button>
          <span className="text-[var(--color-muted-foreground-3)]">·</span>
          <button type="button" className="text-[var(--color-blue)] hover:underline" onClick={onClearAll}>
            {t('comments.clearSelection')}
          </button>
        </div>
      </div>
      {filtered.length === 0 ? (
        <div className="text-center py-8 text-[13px] text-[var(--color-muted-foreground)] border border-dashed border-[var(--color-border)] rounded-[10px]">
          {t('comments.discoverFilterEmpty')}
        </div>
      ) : (
        <div className="border border-[var(--color-border)] rounded-[10px] max-h-[440px] overflow-y-auto">
          {filtered.map((it) => (
            <DiscoverRow
              key={it.url}
              item={it}
              checked={selected.has(it.url)}
              onToggle={() => onToggle(it.url)}
            />
          ))}
        </div>
      )}
    </>
  )
}

function DiscoverRow({
  item, checked, onToggle,
}: {
  item: DiscoverTarget
  checked: boolean
  onToggle: () => void
}) {
  const t = useStudioT()
  const plays = formatCount(item.play_count)
  const author = item.author_nickname ?? '—'
  const days = relativeDays(item.create_time)
  return (
    <label className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 px-3 py-2.5 border-b border-[var(--color-border)] last:border-b-0 text-[13px] cursor-pointer hover:bg-[var(--color-muted)]">
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="accent-[var(--color-primary)] w-[14px] h-[14px]"
      />
      <div className="min-w-0">
        <div className="font-medium text-[var(--color-foreground)] truncate">
          {item.title ?? item.url}
        </div>
        <div className="text-[11.5px] text-[var(--color-muted-foreground-2)] truncate flex items-center gap-1.5">
          {item.search_keyword ? (
            <span className="inline-flex items-center px-1.5 py-0 rounded-[4px] bg-[var(--color-blue-bg)] text-[var(--color-blue)] text-[10.5px] font-medium leading-[16px] flex-shrink-0">
              #{item.search_keyword}
            </span>
          ) : null}
          <span className="truncate">
            @{author} · {t('comments.viewsCount', { n: plays })} · {days ?? '—'}
          </span>
        </div>
      </div>
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer"
        onClick={(e) => e.stopPropagation()}
        className="text-[var(--color-muted-foreground-2)] hover:text-[var(--color-blue)] text-[13px] px-1.5 py-0.5"
      >
        ↗
      </a>
    </label>
  )
}

function formatCount(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function relativeDays(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return null
  const diffMs = Date.now() - d.getTime()
  const days = Math.max(0, Math.round(diffMs / 86_400_000))
  if (days === 0) return 'today'
  return `${days}d ago`
}
