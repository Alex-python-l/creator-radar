'use client'

import React, { useEffect, useState } from 'react'
import { useLocale } from 'next-intl'
import { useStudioT } from '@/lib/studio/i18n'
import { useUrlSearchParams } from '@/lib/studio/use-url-search-params'
import {
  Check,
  MoreHorizontal,
  Trash2,
  UploadCloud,
  Users,
  X,
} from 'lucide-react'
import { GroupRail, ALL_ACCOUNTS, type GroupSummary } from '@/components/studio/GroupRail'
import GroupWizard from '@/components/studio/wizard/GroupWizard'
import { ReplaceProxiesModal } from '@/components/studio/ReplaceProxiesModal'
import { api, API_ORIGIN, PLATFORMS, type AccountPublication, type CaptionPrompt } from '@/lib/studio/api'
import { useStudioToast } from '@/components/studio/studio-providers'
import { parseProxyDebug } from '@/lib/studio/proxy'
import { countryFlag } from '@/lib/studio/countryFlag'
import { formatLocalDateTime, formatRelative, parseBackendIso } from '@/lib/studio/time'
import { PlatformIcon } from '@/components/studio/PlatformIcon'
import { PLATFORM_NAME } from '@/lib/studio/platformNames'
import { cn } from '@/lib/studio/cn'
import {
  Badge,
  Button,
  DataTableShell,
  Dot,
  Drawer,
  DRAWER_EXIT_MS,
  EmptyState,
  Modal,
  PageHeader,
  SectionTitle,
  TBody,
  THead,
  Td,
  TextField,
  Th,
  Tr,
} from '@/components/ui'

type Account = {
  id: number
  profile_name: string
  email: string | null
  tiktok_login_info: string | null
  group_name: string | null
  group_id: number | null
  proxy: string | null
  proxy_status: string | null
  proxy_checked_at: string | null
  proxy_exit_ip: string | null
  proxy_expires_at: string | null
  proxy_country: string | null
  proxy_region: string | null
  geelark_phone_id: string | null
  phone_busy_until: string | null
  tiktok_logged_in: number
  last_posted_at: string | null
  warmed_up: number
  warmup_count: number
  target_warmup_count: number
  warmup_mode: 'count' | 'daily'
  last_warm_result: string | null
  // multi-platform-publish: per-platform login state + login info
  youtube_logged_in: number
  youtube_login_info: string | null
  last_posted_at_youtube: string | null
  instagram_logged_in: number
  instagram_login_info: string | null
  last_posted_at_instagram: string | null
  facebook_logged_in: number
  facebook_login_info: string | null
  last_posted_at_facebook: string | null
}

type EditForm = {
  profile_name: string
  tiktok_login_info: string
  tiktok_logged_in: boolean
  proxy: string
  group_id: number | null
  youtube_logged_in: boolean
  youtube_login_info: string
  instagram_logged_in: boolean
  instagram_login_info: string
  facebook_logged_in: boolean
  facebook_login_info: string
}

const toForm = (a: Account): EditForm => ({
  profile_name: a.profile_name,
  tiktok_login_info: a.tiktok_login_info ?? '',
  tiktok_logged_in: !!a.tiktok_logged_in,
  proxy: a.proxy ?? '',
  group_id: a.group_id ?? null,
  youtube_logged_in: !!a.youtube_logged_in,
  youtube_login_info: a.youtube_login_info ?? '',
  instagram_logged_in: !!a.instagram_logged_in,
  instagram_login_info: a.instagram_login_info ?? '',
  facebook_logged_in: !!a.facebook_logged_in,
  facebook_login_info: a.facebook_login_info ?? '',
})

const formsEqual = (a: EditForm, b: EditForm) =>
  a.profile_name === b.profile_name &&
  a.tiktok_login_info === b.tiktok_login_info &&
  a.tiktok_logged_in === b.tiktok_logged_in &&
  a.proxy === b.proxy &&
  a.group_id === b.group_id &&
  a.youtube_logged_in === b.youtube_logged_in &&
  a.youtube_login_info === b.youtube_login_info &&
  a.instagram_logged_in === b.instagram_logged_in &&
  a.instagram_login_info === b.instagram_login_info &&
  a.facebook_logged_in === b.facebook_logged_in &&
  a.facebook_login_info === b.facebook_login_info

const errorMessage = (error: unknown, fallback: string) =>
  error instanceof Error && error.message ? error.message : fallback

const splitLoginInfoParts = (value: string) =>
  value.trim().split(/\s+/).filter(Boolean)

const fallbackCopyText = (value: string) => {
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '-1000px'
  textarea.style.left = '0'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  textarea.setSelectionRange(0, value.length)
  const copied = document.execCommand('copy')
  document.body.removeChild(textarea)
  if (!copied) throw new Error('copy failed')
}

const POST_RESULT_TONE: Record<
  AccountPublication['result'],
  'green' | 'red'
> = {
  SUCCESS: 'green',
  FAILURE: 'red',
}

type PostRecordsState = {
  loading: boolean
  records: AccountPublication[]
  error: string | null
}

// ── Group header helpers ──────────────────────────────────────────────────────

function SelectionToolbar({ count, onMarkWarmedUp,
                            onMove, onDelete, onCancel, markWarmedLabel,
                            onSwitchMode, switchToDailyLabel,
                            switchToCountLabel, onReplaceProxies }: {
  count: number
  onMarkWarmedUp: () => void
  onMove: () => void; onDelete: () => void; onCancel: () => void
  markWarmedLabel: string
  onSwitchMode: (next: 'count' | 'daily') => void
  switchToDailyLabel: string
  switchToCountLabel: string
  onReplaceProxies?: () => void
}) {
  const t = useStudioT()
  return (
    <div className="sticky top-3 z-20 flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-card)] border border-[#c9d7f0] bg-[var(--color-blue-bg)] px-3 py-2 shadow-[var(--shadow-card-sm)]">
      <div className="inline-flex items-center gap-2 text-[13px] font-semibold text-[var(--color-foreground)]">
        <Users size={14} />
        {t('accounts.selection.selected', { count })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={onMarkWarmedUp}>
          <Check size={13} />
          {markWarmedLabel}
        </Button>
        <Button size="sm" variant="secondary" onClick={() => onSwitchMode('daily')}>
          {switchToDailyLabel}
        </Button>
        <Button size="sm" variant="secondary" onClick={() => onSwitchMode('count')}>
          {switchToCountLabel}
        </Button>
        <Button size="sm" variant="secondary" onClick={onMove}>
          {t('accounts.selection.move')}
        </Button>
        {onReplaceProxies ? (
          <Button size="sm" variant="secondary" onClick={onReplaceProxies}>
            {t('replaceProxies.openButton')}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" className="text-[var(--color-destructive)]" onClick={onDelete}>
          <Trash2 size={13} />
          {t('common.delete')}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          <X size={13} />
          {t('common.cancel')}
        </Button>
      </div>
    </div>
  )
}

function MenuItem({ children, onClick, disabled }: {
  children: React.ReactNode; onClick: () => void; disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className="block w-full rounded-[6px] px-2.5 py-1.5 text-left text-[12px] text-[var(--color-foreground-2)] hover:bg-[var(--color-gray-bg)] disabled:cursor-not-allowed disabled:text-[var(--color-muted-foreground-3)]"
    >
      {children}
    </button>
  )
}

// ─────────────────────────────────────────────────────────────────────────────

// expires_at → {tone, daysRemaining} for the "Expires in Xd" badge.
// Returns null when the proxy has no expiry stamp (legacy rows) so the
// caller hides the badge entirely.
function proxyExpiryBadge(expiresAt: string | null): {
  tone: 'red' | 'orange' | 'yellow'
  days: number
} | null {
  if (!expiresAt) return null
  const d = parseBackendIso(expiresAt)
  if (!d) return null
  const deltaMs = d.getTime() - Date.now()
  const days = Math.floor(deltaMs / 86_400_000)
  // ≤0d red (already expired), ≤3d orange, ≤7d yellow, else hide.
  if (deltaMs <= 0) return { tone: 'red', days }
  if (deltaMs <= 3 * 86_400_000) return { tone: 'orange', days }
  if (deltaMs <= 7 * 86_400_000) return { tone: 'yellow', days }
  return null
}

function ProxyHealth({
  proxy,
  status,
  checkedAt,
  exitIp,
  expiresAt,
  country,
  region,
  onReplace,
}: {
  proxy: string | null
  status: string | null
  checkedAt: string | null
  exitIp: string | null
  expiresAt: string | null
  country: string | null
  region: string | null
  onReplace?: () => void
}) {
  const t = useStudioT()
  const hasProxy = !!(proxy && proxy.trim())
  if (!hasProxy) return null
  const tone: 'green' | 'red' | 'gray' =
    status === 'ok' ? 'green' : status === 'fail' ? 'red' : 'gray'
  const label =
    status === 'ok'
      ? t('accounts.drawer.proxyStatusOk')
      : status === 'fail'
      ? t('accounts.drawer.proxyStatusFail')
      : t('accounts.drawer.proxyStatusUnknown')
  const debug = parseProxyDebug(proxy)
  const rel = checkedAt ? formatRelative(checkedAt) : null
  const expiry = proxyExpiryBadge(expiresAt)
  const flag = countryFlag(country)
  const locationText = [country?.toUpperCase(), region].filter(Boolean).join(' · ')
  return (
    <div className="flex flex-col gap-1 text-[12.5px] text-[var(--color-muted-foreground)]">
      <div className="flex flex-wrap items-center gap-2">
        <Dot tone={tone} />
        <span>{label}</span>
        {rel ? <span className="text-[var(--color-muted-foreground-2)]">· {rel}</span> : null}
        {expiry ? (
          <Badge tone={expiry.tone}>
            {expiry.days <= 0
              ? t('accounts.drawer.proxyExpired')
              : t('accounts.drawer.proxyExpiresIn', { days: expiry.days })}
          </Badge>
        ) : null}
        {onReplace ? (
          <button
            type="button"
            onClick={onReplace}
            className="ml-auto rounded-[6px] border border-[var(--color-border)] px-2 py-0.5 text-[11.5px] text-[var(--color-foreground-2)] hover:bg-[var(--color-gray-bg)]"
          >
            {t('accounts.drawer.proxyReplace')}
          </button>
        ) : null}
      </div>
      {flag || locationText ? (
        <div className="text-[var(--color-muted-foreground-2)]">
          {flag ? <span className="mr-1">{flag}</span> : null}
          {locationText || t('accounts.drawer.proxyLocationUnknown')}
        </div>
      ) : null}
      {debug ? (
        <div className="font-mono text-[var(--color-muted-foreground-2)] truncate">{debug}</div>
      ) : null}
      {exitIp ? (
        <div className="text-[var(--color-muted-foreground-2)]">
          {t('accounts.drawer.proxyExitIp', { ip: exitIp })}
        </div>
      ) : null}
    </div>
  )
}

function lastPostedFor(account: Account, plat: string): string | null {
  if (plat === 'tiktok') return account.last_posted_at
  const key = `last_posted_at_${plat}` as keyof Account
  const v = account[key]
  return typeof v === 'string' ? v : null
}

function formatCooldown(
  lastIso: string | null,
  cooldownHours: number,
  zh: boolean,
): string {
  if (!lastIso) return zh ? '从未发布' : 'never posted'
  const d = parseBackendIso(lastIso)
  if (!d) return zh ? '从未发布' : 'never posted'
  const elapsedMs = Date.now() - d.getTime()
  const cooldownMs = cooldownHours * 3600 * 1000
  const remainingMs = cooldownMs - elapsedMs
  if (remainingMs <= 0) {
    const hours = elapsedMs / 3600000
    const last = hours < 1
      ? `${Math.round(elapsedMs / 60000)}${zh ? '分钟' : 'm'}`
      : `${hours.toFixed(1)}${zh ? '小时' : 'h'}`
    return zh ? `可发布（上次 ${last} 前）` : `ready (last ${last} ago)`
  }
  const h = Math.floor(remainingMs / 3600000)
  const m = Math.floor((remainingMs % 3600000) / 60000)
  const remaining = h > 0 ? `${h}h${m}m` : `${m}m`
  return zh ? `冷却中，还需 ${remaining}` : `cooling, ${remaining} left`
}

export default function Accounts() {
  const onToast = useStudioToast()
  const t = useStudioT()
  const locale = useLocale()
  const [rows, setRows] = useState<Account[]>([])
  const [cooldownHours, setCooldownHours] = useState<number>(2)
  const [newGroupOpen, setNewGroupOpen] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [moveDialogIds, setMoveDialogIds] = useState<number[] | null>(null)
  // 'unset' → user hasn't picked yet (default; Submit disabled)
  // 'ungrouped' → user explicitly chose "no group" (clears group_id)
  // number → group id
  type MoveTarget = 'unset' | 'ungrouped' | number
  const [moveTarget, setMoveTarget] = useState<MoveTarget>('unset')
  const [moveSubmitting, setMoveSubmitting] = useState(false)
  const [confirmState, setConfirmState] = useState<{
    title: string
    body: string
    submitLabel?: string
    submitTone?: 'primary' | 'secondary' | 'ghost' | 'danger'
    onConfirm: () => void | Promise<void>
  } | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)

  // Drawer state
  const [editing, setEditing] = useState<Account | null>(null)
  const [form, setForm] = useState<EditForm | null>(null)
  const [saving, setSaving] = useState(false)
  const [replaceTargets, setReplaceTargets] = useState<number[] | null>(null)
  const [expandedPostRows, setExpandedPostRows] = useState<Set<number>>(new Set())
  const [postRecordsByAccount, setPostRecordsByAccount] =
    useState<Record<number, PostRecordsState>>({})

  // Group settings modal state
  const [groupSettingsTarget, setGroupSettingsTarget] = useState<{
    id: number; name: string; caption_prompt_id: number | null
  } | null>(null)
  const [groupSettingsSaving, setGroupSettingsSaving] = useState(false)
  const [captionPrompts, setCaptionPrompts] = useState<CaptionPrompt[]>([])

  const loadCaptionPrompts = () => {
    api.captionPrompts()
      .then(setCaptionPrompts)
      .catch(() => {})
  }
  useEffect(() => { loadCaptionPrompts() }, [])

  const load = () =>
    api
      .accounts()
      .then(setRows)
      .catch(() => {})
  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    fetch(API_ORIGIN + '/api/settings')
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        const raw = s?.post_cooldown_hours
        const n = typeof raw === 'number' ? raw : parseFloat(raw)
        if (Number.isFinite(n) && n > 0) setCooldownHours(n)
      })
      .catch(() => {})
  }, [])

  const refreshAndSyncEditing = async (accountId: number) => {
    const next: Account[] = await api.accounts()
    setRows(next)
    setEditing((prev) => {
      if (prev?.id !== accountId) return prev
      return next.find((r) => r.id === accountId) ?? prev
    })
  }

  const openDrawer = (a: Account) => {
    setEditing(a)
    setForm(toForm(a))
  }

  const loadPostRecords = async (accountId: number) => {
    setPostRecordsByAccount((prev) => ({
      ...prev,
      [accountId]: { loading: true, records: [], error: null },
    }))
    try {
      const records = await api.accountPublications(accountId)
      setPostRecordsByAccount((prev) => ({
        ...prev,
        [accountId]: { loading: false, records, error: null },
      }))
    } catch (e) {
      setPostRecordsByAccount((prev) => ({
        ...prev,
        [accountId]: {
          loading: false,
          records: [],
          error: errorMessage(e, t('accounts.drawer.postRecordsError')),
        },
      }))
    }
  }

  const togglePostRecords = (accountId: number) => {
    const willExpand = !expandedPostRows.has(accountId)
    setExpandedPostRows((prev) => {
      const next = new Set(prev)
      if (next.has(accountId)) next.delete(accountId)
      else next.add(accountId)
      return next
    })
    const current = postRecordsByAccount[accountId]
    if (willExpand && (!current || current.error)) {
      void loadPostRecords(accountId)
    }
  }

  const renderPostRecords = (state: PostRecordsState | undefined) => {
    if (!state || state.loading) {
      return (
        <div className="text-[13px] text-[var(--color-muted-foreground-2)]">
          {t('accounts.drawer.postRecordsLoading')}
        </div>
      )
    }
    if (state.error) {
      return <div className="text-[13px] text-[var(--color-destructive)]">{state.error}</div>
    }
    if (state.records.length === 0) {
      return (
        <div className="text-[13px] text-[var(--color-muted-foreground-2)]">
          {t('accounts.drawer.postRecordsEmpty')}
        </div>
      )
    }
    return (
      <div className="grid gap-2 md:grid-cols-2">
        {state.records.map((record) => {
          const completedAt =
            record.posted_at ?? record.last_checked_at ?? record.created_at
          const failureDetail =
            record.result === 'FAILURE'
              ? `${record.notes ?? t('videos.historyNoNotes')}${
                  record.fail_code ? ` (${record.fail_code})` : ''
                }`
              : ''
          return (
            <div
              key={record.id}
              className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2.5"
            >
              <div className="flex items-center gap-2 min-w-0">
                <PlatformIcon platform={record.platform} size={15} />
                <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--color-foreground)]">
                  {record.original_filename ?? `#${record.video_id}`}
                </span>
                <Badge tone={POST_RESULT_TONE[record.result]}>
                  {t(`videos.result.${record.result}`, {
                    defaultValue: record.result,
                  })}
                </Badge>
              </div>
              <div className="mt-1 text-[12px] text-[var(--color-muted-foreground-2)] tabular-nums">
                <span
                  title={
                    completedAt ? formatLocalDateTime(completedAt) : undefined
                  }
                >
                  {formatRelative(
                    completedAt,
                    locale ?? locale,
                  )}
                </span>
              </div>
              {record.caption ? (
                <div
                  className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--color-muted-foreground)]"
                  style={{
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                  }}
                >
                  {record.caption}
                </div>
              ) : null}
              {failureDetail ? (
                <div className="mt-1.5 text-[12.5px] text-[var(--color-muted-foreground)]">
                  <span>{failureDetail}</span>
                  {record.fail_screenshot ? (
                    <a
                      href={`${API_ORIGIN}/api/videos/publications/${record.id}/screenshot`}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-2 text-[var(--color-blue)] underline"
                    >
                      {t('videos.viewFailureShot')}
                    </a>
                  ) : null}
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
    )
  }

  const isDirty = (): boolean => {
    if (!editing || !form) return false
    return !formsEqual(form, toForm(editing))
  }

  const discardAndClose = () => {
    // Only flip `editing` here — `form`/`saving` are cleared
    // by the effect below AFTER the Drawer's exit animation finishes,
    // so the body keeps rendering its last-known content while sliding out.
    setEditing(null)
  }

  useEffect(() => {
    if (editing) return
    const timer = setTimeout(() => {
      setForm(null)
      setSaving(false)
    }, DRAWER_EXIT_MS)
    return () => clearTimeout(timer)
  }, [editing])

  const requestClose = () => {
    if (!isDirty()) {
      discardAndClose()
      return
    }
    setConfirmState({
      title: t('accounts.confirm.unsavedTitle'),
      body: t('accounts.drawer.unsavedConfirm'),
      submitLabel: t('accounts.confirm.discard'),
      submitTone: 'primary',
      onConfirm: discardAndClose,
    })
  }

  const saveDrawer = async () => {
    if (!editing || !form) return
    const base = toForm(editing)
    if (!form.profile_name.trim()) {
      onToast({
        msg: t('accounts.toast.saveFailed', { field: 'profile_name' }),
        tone: 'red',
      })
      return
    }
    const diff: Record<string, string | number | null> = {}
    if (base.profile_name !== form.profile_name)
      diff.profile_name = form.profile_name.trim()
    if (base.proxy !== form.proxy) diff.proxy = form.proxy.trim()
    if (base.group_id !== form.group_id)
      diff.group_id = form.group_id
    for (const plat of PLATFORMS) {
      const loggedKey = `${plat}_logged_in` as const
      const loginInfoKey = `${plat}_login_info` as const
      if (base[loggedKey] !== form[loggedKey]) {
        diff[loggedKey] = form[loggedKey] ? 1 : 0
      }
      if (base[loginInfoKey] !== form[loginInfoKey]) {
        diff[loginInfoKey] = form[loginInfoKey].trim()
      }
    }
    if (Object.keys(diff).length === 0) {
      discardAndClose()
      return
    }
    setSaving(true)
    try {
      await api.updateAccount(editing.id, diff)
      onToast({ msg: t('accounts.toast.saved'), tone: 'green' })
      await load()
      discardAndClose()
    } catch {
      onToast({
        msg: t('accounts.toast.saveFailed', { field: 'account' }),
        tone: 'red',
      })
      setSaving(false)
    }
  }

  const copyLoginInfo = async (value: string) => {
    try {
      if (window.navigator?.clipboard?.writeText && window.isSecureContext) {
        await window.navigator.clipboard.writeText(value)
      } else {
        fallbackCopyText(value)
      }
      onToast({ msg: t('accounts.toast.copied'), tone: 'green' })
    } catch {
      onToast({ msg: t('accounts.toast.copyFailed'), tone: 'red' })
    }
  }

  const deleteFromDrawer = () => {
    if (!editing) return
    const target = editing
    setConfirmState({
      title: t('accounts.confirm.deleteAccountTitle'),
      body: t('accounts.deleteConfirm', { name: target.profile_name }),
      submitLabel: t('accounts.confirm.delete'),
      submitTone: 'danger',
      onConfirm: async () => {
        try {
          await api.deleteAccount(target.id)
          onToast({
            msg: t('accounts.toast.removed', { name: target.profile_name }),
            tone: 'orange',
          })
          await load()
          discardAndClose()
        } catch (e) {
          onToast({
            msg: errorMessage(e, t('accounts.toast.deleteFailed')),
            tone: 'red',
          })
        }
      },
    })
  }

  const [holdBusy, setHoldBusy] = useState(false)
  const holdPhoneForUse = async () => {
    if (!editing) return
    setHoldBusy(true)
    try {
      await api.holdPhone(editing.id)
      await refreshAndSyncEditing(editing.id)
      onToast({ msg: 'Phone held — sweeper will leave it alone', tone: 'green' })
    } catch (e) {
      onToast({ msg: errorMessage(e, 'Could not hold phone'), tone: 'red' })
    } finally {
      setHoldBusy(false)
    }
  }
  const releasePhoneFromHold = async () => {
    if (!editing) return
    setHoldBusy(true)
    try {
      await api.releasePhone(editing.id)
      await refreshAndSyncEditing(editing.id)
      onToast({ msg: 'Phone released', tone: 'green' })
    } catch (e) {
      onToast({ msg: errorMessage(e, 'Could not release phone'), tone: 'red' })
    } finally {
      setHoldBusy(false)
    }
  }

  const importGeelark = async () => {
    try {
      const r = await api.importGeelark()
      onToast({
        msg: t('accounts.toast.imported', { count: r.imported ?? 0 }),
        tone: 'green',
      })
      load()
    } catch {
      onToast({ msg: t('accounts.toast.importFailed'), tone: 'red' })
    }
  }

  // Group-filter — selection is URL-driven via ?group=N
  const [groups, setGroups] = useState<GroupSummary[]>([])
  useEffect(() => {
    fetch(API_ORIGIN + '/api/groups')
      .then((r) => (r.ok ? r.json() : []))
      .then(setGroups)
      .catch(() => {})
  }, [])
  const [searchParams, setSearchParams] = useUrlSearchParams()
  const groupParam = searchParams.get('group')
  const selectedGroup: number | 'all' =
    !groupParam || groupParam === 'all'
      ? ALL_ACCOUNTS
      : Number(groupParam)

  const setSelectedGroup = (g: number | 'all') => {
    const next = new URLSearchParams(searchParams)
    if (g === ALL_ACCOUNTS) next.delete('group')
    else next.set('group', String(g))
    setSelected(new Set())
    setSearchParams(next, { replace: false })
  }

  const wizardParam = searchParams.get('wizard')
  const wizardInitialGroupId =
    wizardParam === 'phones' &&
    selectedGroup !== ALL_ACCOUNTS &&
    Number.isFinite(selectedGroup)
      ? selectedGroup
      : null
  const wizardInitialGroup = wizardInitialGroupId === null
    ? null
    : groups.find((g) => g.id === wizardInitialGroupId)
  const isWizardActive =
    wizardParam === 'new' || wizardParam === '1' || wizardInitialGroupId !== null

  const closeWizard = async () => {
    const next = new URLSearchParams(searchParams)
    next.delete('wizard')
    setSearchParams(next)
    await Promise.all([reloadGroups(), reloadRows()])
  }

  const [menuOpen, setMenuOpen] = useState<number | null>(null)

  // ── Selection state ──────────────────────────────────────────────────────────
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const toggleRow = (id: number) =>
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  const toggleAll = () =>
    setSelected(prev =>
      prev.size === visibleRows.length ? new Set()
        : new Set(visibleRows.map((r) => r.id)))

  // ── Batch handlers ───────────────────────────────────────────────────────────
  const reloadRows = async () => {
    const r = await fetch(API_ORIGIN + '/api/accounts')
    if (r.ok) setRows(await r.json())
  }

  async function doSwitchMode(ids: number[], mode: 'count' | 'daily') {
    try {
      const result = await api.setWarmupMode(ids, mode)
      setSelected(new Set())
      await reloadRows()
      onToast({
        msg: t('accounts.mode.switched', {
          count: result.updated ?? ids.length,
          mode: t(`accounts.mode.${mode}`),
        }),
        tone: 'green',
      })
    } catch (e) {
      onToast({
        msg: errorMessage(e, t('accounts.mode.switchFailed')),
        tone: 'red',
      })
    }
  }
  function doMarkWarmedUp(ids: number[]) {
    setConfirmState({
      title: t('accounts.confirm.markWarmedTitle', { count: ids.length }),
      body: t('accounts.markWarmedConfirm', { count: ids.length }),
      submitLabel: t('accounts.confirm.markWarmed'),
      submitTone: 'primary',
      onConfirm: async () => {
        try {
          const result = await api.markAccountsWarmedUp(ids)
          setSelected(new Set())
          await reloadRows()
          onToast({
            msg: t('accounts.toast.markWarmed', {
              count: result.updated ?? ids.length,
            }),
            tone: 'green',
          })
        } catch (e) {
          onToast({
            msg: errorMessage(e, t('accounts.toast.markWarmedFailed')),
            tone: 'red',
          })
        }
      },
    })
  }
  function doBatchDelete(ids: number[]) {
    setConfirmState({
      title: t('accounts.confirm.batchDeleteTitle', { count: ids.length }),
      body: t('accounts.batchDeleteConfirm', { count: ids.length }),
      submitLabel: t('accounts.confirm.delete'),
      submitTone: 'danger',
      onConfirm: async () => {
        try {
          await api.batchDeleteAccounts(ids)
          setSelected(new Set())
          await reloadRows()
        } catch (e) {
          onToast({
            msg: errorMessage(e, t('accounts.toast.deleteFailed')),
            tone: 'red',
          })
        }
      },
    })
  }
  function openMoveDialog(ids: number[]) {
    setMoveDialogIds(ids)
    setMoveTarget('unset')
  }

  function closeMoveDialog() {
    if (moveSubmitting) return
    setMoveDialogIds(null)
    setMoveTarget('unset')
  }

  async function submitMove(e: React.FormEvent) {
    e.preventDefault()
    const ids = moveDialogIds
    if (!ids?.length || moveTarget === 'unset' || moveSubmitting) return
    const groupId = moveTarget === 'ungrouped' ? null : moveTarget
    setMoveSubmitting(true)
    try {
      await Promise.all(
        ids.map((id) => api.updateAccount(id, { group_id: groupId })),
      )
      setSelected(new Set())
      setMoveDialogIds(null)
      setMoveTarget('unset')
      await Promise.all([reloadRows(), reloadGroups()])
    } catch (e) {
      onToast({ msg: errorMessage(e, 'Move failed'), tone: 'red' })
    } finally {
      setMoveSubmitting(false)
    }
  }

  const reloadGroups = async () => {
    const r = await fetch(API_ORIGIN + '/api/groups')
    if (r.ok) setGroups(await r.json())
  }

  async function submitNewGroup(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = newGroupName.trim()
    if (!trimmed) {
      onToast({ msg: t('wizard.s1.no_name'), tone: 'red' })
      return
    }
    try {
      const created: { id: number } = await api.createGroup({ name: trimmed })
      await reloadGroups()
      setSelectedGroup(created.id)
      setNewGroupName('')
      setNewGroupOpen(false)
      onToast({ msg: `Group "${trimmed}" created`, tone: 'blue' })
    } catch (e) {
      onToast({ msg: errorMessage(e, 'Create group failed'), tone: 'red' })
    }
  }

  function openAddPhones(gid: number) {
    const group = groups.find((g) => g.id === gid)
    if (!group) {
      onToast({ msg: 'Group is unavailable', tone: 'red' })
      void reloadGroups()
      return
    }
    const next = new URLSearchParams(searchParams)
    next.set('group', String(gid))
    next.set('wizard', 'phones')
    setSelected(new Set())
    setMenuOpen(null)
    setSearchParams(next)
  }

  const groupRows =
    selectedGroup === ALL_ACCOUNTS
      ? rows
      : rows.filter((r) => r.group_id === selectedGroup)
  const visibleRows = groupRows
  const selectedGroupMeta =
    selectedGroup === ALL_ACCOUNTS
      ? null
      : groups.find((g) => g.id === selectedGroup) ?? null
  return (
    <div className="min-h-screen lg:flex">
      <GroupRail
        groups={groups}
        totalAccounts={rows.length}
        selected={selectedGroup}
        onSelect={setSelectedGroup}
        onNewGroup={() => setNewGroupOpen(true)}
      />
      <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
    <div className="flex flex-col gap-6">
      <PageHeader
        title={
          selectedGroupMeta
            ? selectedGroupMeta.name
            : t('accounts.title')
        }
        actions={
          <div className="relative flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={importGeelark}>
              <UploadCloud size={14} />
              {t('accounts.importFromGeelark')}
            </Button>
            {selectedGroupMeta ? (
              <>
                <Button variant="primary" onClick={() => openAddPhones(selectedGroupMeta.id)}>
                  + {t('accounts.addPhones')}
                </Button>
                <Button
                  variant="secondary"
                  aria-label={t('accounts.groupActions')}
                  onClick={() => setMenuOpen((m) => m === selectedGroupMeta.id ? null : selectedGroupMeta.id)}
                >
                  <MoreHorizontal size={15} />
                </Button>
              </>
            ) : null}
            {selectedGroupMeta && menuOpen === selectedGroupMeta.id ? (
              <div className="absolute right-0 top-10 z-30 min-w-[160px] rounded-[7px] border border-[var(--color-border)] bg-[var(--color-card)] p-1 shadow-[var(--shadow-card-lg)]">
                <MenuItem onClick={() => {
                  setGroupSettingsTarget({
                    id: selectedGroupMeta.id,
                    name: selectedGroupMeta.name,
                    caption_prompt_id: selectedGroupMeta.caption_prompt_id ?? null,
                  })
                  setMenuOpen(null)
                }}>
                  {t('accounts.groupSettings')}
                </MenuItem>
                <MenuItem
                  disabled={selectedGroupMeta.counts.phones > 0}
                  onClick={() => {
                    setMenuOpen(null)
                    setConfirmState({
                      title: t('accounts.confirm.deleteGroupTitle'),
                      body: t('accounts.confirm.deleteGroupBody', { name: selectedGroupMeta.name }),
                      submitLabel: t('accounts.confirm.delete'),
                      submitTone: 'danger',
                      onConfirm: async () => {
                        try {
                          await api.deleteGroup(selectedGroupMeta.id)
                          setSelectedGroup(ALL_ACCOUNTS)
                          await reloadGroups()
                        } catch (e) {
                          onToast({
                            msg: errorMessage(e, 'Delete failed'),
                            tone: 'red',
                          })
                        }
                      },
                    })
                  }}
                >
                  {t('common.delete')}
                </MenuItem>
              </div>
            ) : null}
          </div>
        }
      />

      {selected.size > 0 ? (
        <SelectionToolbar
          count={selected.size}
          onMarkWarmedUp={() => doMarkWarmedUp([...selected])}
          onMove={() => openMoveDialog([...selected])}
          onDelete={() => doBatchDelete([...selected])}
          onCancel={() => setSelected(new Set())}
          markWarmedLabel={t('accounts.markWarmedAction')}
          onSwitchMode={(next) => doSwitchMode([...selected], next)}
          switchToDailyLabel={t('accounts.mode.switchToDaily')}
          switchToCountLabel={t('accounts.mode.switchToCount')}
          onReplaceProxies={() => setReplaceTargets([...selected])}
        />
      ) : null}

      <section>
        <DataTableShell>
          <table className="w-full border-collapse">
          <THead>
            <Tr className="hover:bg-transparent">
              <Th className="w-[30px]">
                <input type="checkbox"
                       checked={selected.size > 0 && selected.size === visibleRows.length}
                       onChange={toggleAll} />
              </Th>
              <Th>{t('accounts.col.env')}</Th>
              <Th>{t('accounts.col.tiktokLoginInfo')}</Th>
              {selectedGroup === ALL_ACCOUNTS && <Th>{t('accounts.col.group')}</Th>}
              <Th align="center" className="w-[64px]">{t('accounts.col.proxy')}</Th>
              <Th align="center" className="w-[72px]">{t('accounts.col.platforms', 'Platforms')}</Th>
              <Th>{t('accounts.col.mode')}</Th>
              <Th>{t('accounts.col.warmProgress')}</Th>
              <Th>{t('accounts.col.warmResult')}</Th>
              <Th align="right" className="w-[24px]" />
            </Tr>
          </THead>
          <TBody>
            {visibleRows.length === 0 ? (
              <tr>
                <td colSpan={selectedGroup === ALL_ACCOUNTS ? 10 : 9}>
                  <EmptyState title={t('accounts.emptyTitle')}>
                    {t.rich('accounts.emptyBody', {
                      b: () => (
                        <span className="text-[var(--color-blue)] font-medium">
                          {t('accounts.importFromGeelark')}
                        </span>
                      ),
                    })}
                  </EmptyState>
                </td>
              </tr>
            ) : (
              visibleRows.map((r) => {
                const hasProxy = !!(r.proxy && r.proxy.trim())
                const proxyTone: 'green' | 'red' | 'gray' = !hasProxy
                  ? 'gray'
                  : r.proxy_status === 'ok'
                  ? 'green'
                  : r.proxy_status === 'fail'
                  ? 'red'
                  : 'gray'
                const postsExpanded = expandedPostRows.has(r.id)
                const postsState = postRecordsByAccount[r.id]
                const tableColSpan = selectedGroup === ALL_ACCOUNTS ? 10 : 9
                return (
                  <React.Fragment key={r.id}>
                    <Tr
                      className="cursor-pointer group"
                      onClick={() => openDrawer(r)}
                    >
                      <td className="px-4 py-3 align-middle w-[30px]"
                          onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox"
                               checked={selected.has(r.id)}
                               onChange={() => toggleRow(r.id)} />
                      </td>
                      <Td>
                        <div className="flex items-center gap-2.5">
                          <button
                            type="button"
                            aria-expanded={postsExpanded}
                            aria-label={t('accounts.drawer.postRecords')}
                            title={t('accounts.drawer.postRecords')}
                            onClick={(e) => {
                              e.stopPropagation()
                              togglePostRecords(r.id)
                            }}
                            className={cn(
                              'inline-flex h-6 w-6 shrink-0 items-center justify-center',
                              'rounded-[6px] border border-[var(--color-border)]',
                              'text-[13px] leading-none text-[var(--color-muted-foreground)]',
                              'hover:bg-[var(--color-gray-bg)] hover:text-[var(--color-foreground)]',
                            )}
                          >
                            {postsExpanded ? '▾' : '▸'}
                          </button>
                          <div className="min-w-0">
                            <div className="max-w-[260px] text-[var(--color-foreground)] font-medium truncate">
                              {r.profile_name}
                            </div>
                          </div>
                        </div>
                      </Td>
                      <Td>
                        {r.tiktok_login_info ? (
                          <Badge tone="blue">{t('accounts.loginInfoSet')}</Badge>
                        ) : (
                          <span className="text-[var(--color-muted-foreground-3)]">—</span>
                        )}
                      </Td>
                      {selectedGroup === ALL_ACCOUNTS && (
                        <Td>
                          <span className="text-[13px] text-[var(--color-muted-foreground)] truncate">
                            {r.group_name || '—'}
                          </span>
                        </Td>
                      )}
                      <Td align="center">
                        {hasProxy ? (
                          <span className="inline-flex justify-center w-full">
                            <Dot tone={proxyTone} size="md" />
                          </span>
                        ) : (
                          <span className="text-[var(--color-muted-foreground-3)]">—</span>
                        )}
                      </Td>
                      <Td align="center">
                        <div className="inline-grid grid-cols-2 gap-1.5 justify-items-center">
                          {PLATFORMS.map((plat) => {
                            const loggedIn =
                              plat === 'tiktok'
                                ? r.tiktok_logged_in
                                : r[`${plat}_logged_in` as const]
                            const loginInfo =
                              plat === 'tiktok'
                                ? r.tiktok_login_info
                                : r[`${plat}_login_info` as const]
                            const zh = (locale ?? locale ?? 'en').startsWith('zh')
                            const cooldown = loggedIn
                              ? formatCooldown(lastPostedFor(r, plat), cooldownHours, zh)
                              : null
                            const title = `${PLATFORM_NAME[plat]} · ${
                              loggedIn
                                ? loginInfo
                                  ? t('accounts.loginInfoSet')
                                  : t('accounts.loggedInYes')
                                : t('accounts.loggedInNo')
                            }${cooldown ? ` · ${cooldown}` : ''}`
                            return loggedIn ? (
                              <PlatformIcon
                                key={plat}
                                platform={plat}
                                size={18}
                                title={title}
                              />
                            ) : (
                              <PlatformIcon
                                key={plat}
                                platform={plat}
                                size={18}
                                mono
                                title={title}
                                className="text-[var(--color-muted-foreground-3)] opacity-50"
                              />
                            )
                          })}
                        </div>
                      </Td>
                      <Td>
                        {r.warmup_mode === 'daily' ? (
                          <Badge tone="blue">{t('accounts.mode.daily')}</Badge>
                        ) : (
                          <Badge tone="gray">{t('accounts.mode.count')}</Badge>
                        )}
                      </Td>
                      <Td>
                        <span className="text-[13px] text-[var(--color-muted-foreground)] tabular-nums">
                          {r.warmup_count}
                        </span>
                      </Td>
                      <Td align="center">
                        {r.last_warm_result === 'Success' ? (
                          <Badge tone="green">{t('accounts.warmOk')}</Badge>
                        ) : r.last_warm_result === 'Failed' ? (
                          <Badge tone="red">{t('accounts.warmFailed')}</Badge>
                        ) : (
                          <span className="text-[var(--color-muted-foreground-2)]">—</span>
                        )}
                      </Td>
                      <Td align="right">
                        <span className="text-[var(--color-muted-foreground-3)] opacity-0 group-hover:opacity-100 transition-opacity">
                          →
                        </span>
                      </Td>
                    </Tr>
                    {postsExpanded ? (
                      <tr>
                        <td
                          colSpan={tableColSpan}
                          className="px-4 py-3 bg-[var(--color-muted)]"
                        >
                          <div className="flex flex-col gap-2 pl-[68px]">
                            <div className="text-[12px] font-semibold uppercase tracking-[0.04em] text-[var(--color-muted-foreground-2)]">
                              {t('accounts.drawer.postRecords')}
                            </div>
                            {renderPostRecords(postsState)}
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </React.Fragment>
                )
              })
            )}
          </TBody>
          </table>
        </DataTableShell>
      </section>

      <Drawer
        open={!!editing}
        onClose={requestClose}
        title={t('accounts.drawer.title')}
        subtitle={editing?.profile_name}
        footer={
          editing && form ? (
            <div className="flex justify-between items-center">
              <Button
                variant="ghost"
                className="text-[var(--color-destructive)] hover:bg-[var(--color-destructive-bg)] hover:text-[var(--color-destructive)]"
                disabled={saving}
                onClick={deleteFromDrawer}
              >
                {t('accounts.drawer.deleteAccount')}
              </Button>
              <div className="flex gap-2 ml-auto">
                <Button variant="secondary" onClick={requestClose}>
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="primary"
                  disabled={saving || !isDirty()}
                  onClick={saveDrawer}
                >
                  {saving ? t('common.loading') : t('accounts.drawer.saveChanges')}
                </Button>
              </div>
            </div>
          ) : null
        }
      >
        {form ? (
          <div className="flex flex-col gap-6">
            <section>
              <SectionTitle>{t('accounts.drawer.identity')}</SectionTitle>
              <div className="flex flex-col gap-3">
                <TextField
                  label={t('accounts.drawer.identityName')}
                  value={form.profile_name}
                  onChange={(e) =>
                    setForm({ ...form, profile_name: e.target.value })
                  }
                />
                <div>
                  <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
                    {t('accounts.drawer.identityPhone')}
                  </span>
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-[13.5px] text-[var(--color-muted-foreground)] font-mono">
                      {editing?.geelark_phone_id ??
                        t('accounts.drawer.identityNoPhone')}
                    </span>
                    {editing?.geelark_phone_id ? (() => {
                      const busyDate = parseBackendIso(editing.phone_busy_until ?? '')
                      const heldActive = !!busyDate && busyDate.getTime() > Date.now()
                      if (heldActive && busyDate) {
                        return (
                          <>
                            <span className="text-[12.5px] text-[var(--color-muted-foreground)]">
                              Held until {busyDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </span>
                            <button
                              type="button"
                              onClick={releasePhoneFromHold}
                              disabled={holdBusy}
                              className="text-[12.5px] text-[var(--color-blue)] hover:underline disabled:opacity-50"
                            >
                              Release
                            </button>
                          </>
                        )
                      }
                      return (
                        <button
                          type="button"
                          onClick={holdPhoneForUse}
                          disabled={holdBusy}
                          className="text-[12.5px] text-[var(--color-blue)] hover:underline disabled:opacity-50"
                          title="Marks the phone as in use for 2h so the idle sweeper won't auto-stop it"
                        >
                          Hold for use (2h)
                        </button>
                      )
                    })() : null}
                  </div>
                </div>
              </div>
            </section>

            <section>
              <SectionTitle>{t('accounts.drawer.credentials')}</SectionTitle>
              <div className="flex flex-col gap-3">
                <label style={{ display: 'block' }}>
                  <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
                    Group
                  </span>
                  <select
                    value={form.group_id ?? ''}
                    onChange={e => setForm((f) =>
                      f
                        ? {
                            ...f,
                            group_id: e.target.value === ''
                              ? null
                              : Number(e.target.value),
                          }
                        : f,
                    )}
                    style={{ width: '100%', padding: '6px 8px',
                             border: '1px solid #d1d5db', borderRadius: 4,
                             fontSize: 13.5, background: '#fff' }}
                  >
                    <option value="">(none)</option>
                    {groups.map(g => (
                      <option key={g.id} value={g.id}>{g.name}</option>
                    ))}
                  </select>
                </label>
                <div>
                  <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
                    {t('accounts.drawer.platformsLoggedIn', 'Logged in to')}
                  </span>
                  <div className="grid grid-cols-4 gap-2">
                    {PLATFORMS.map((plat) => {
                      const loggedKey = `${plat}_logged_in` as const
                      const checked = form[loggedKey]
                      return (
                        <label
                          key={plat}
                          title={PLATFORM_NAME[plat]}
                          className={cn(
                            'flex items-center justify-center gap-2 px-2 py-2.5 rounded-md border cursor-pointer transition-colors',
                            checked
                              ? 'border-[var(--color-border-strong)] bg-[var(--color-muted)]'
                              : 'border-[var(--color-border)] hover:bg-[var(--color-muted)]',
                          )}
                        >
                          <PlatformIcon platform={plat} size={20} />
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) =>
                              setForm({ ...form, [loggedKey]: e.target.checked })
                            }
                            className="h-4 w-4 cursor-pointer"
                          />
                        </label>
                      )
                    })}
                  </div>
                </div>
                <div className="flex flex-col gap-3">
                  {PLATFORMS.map((plat) => {
                    const loginInfoKey = `${plat}_login_info` as const
                    const fieldId = `account-credentials-${plat}`
                    const parts = splitLoginInfoParts(form[loginInfoKey])
                    return (
                      <div key={plat}>
                        <label
                          htmlFor={fieldId}
                          className="flex items-center gap-1.5 mb-1.5 text-[12.5px] font-medium text-[var(--color-foreground-2)]"
                        >
                          <PlatformIcon platform={plat} size={14} />
                          <span>
                            {t('accounts.drawer.credentialsBlob', {
                              platform: PLATFORM_NAME[plat],
                              defaultValue: `${PLATFORM_NAME[plat]} login info`,
                            })}
                          </span>
                        </label>
                        <textarea
                          id={fieldId}
                          value={form[loginInfoKey]}
                          onChange={(e) =>
                            setForm({ ...form, [loginInfoKey]: e.target.value })
                          }
                          placeholder={t(
                            'accounts.drawer.credentialsBlobPlaceholder',
                            {
                              platform: PLATFORM_NAME[plat],
                              defaultValue: `Paste the raw login info for this ${PLATFORM_NAME[plat]} account.`,
                            },
                          )}
                          rows={3}
                          className={cn(
                            'w-full bg-[var(--color-card)] border border-[var(--color-border)]',
                            'rounded-[8px] px-3 py-2 text-[13px] leading-relaxed font-mono',
                            'text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)]',
                            'focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20',
                            'focus:outline-none transition-shadow resize-y min-h-[72px]',
                          )}
                        />
                        {form[loginInfoKey].trim() ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            <button
                              type="button"
                              onClick={() => copyLoginInfo(form[loginInfoKey].trim())}
                              className={cn(
                                'h-7 px-2.5 rounded-[6px] border border-[var(--color-border)]',
                                'bg-[var(--color-gray-bg)] text-[12px] font-medium',
                                'text-[var(--color-foreground-2)] hover:bg-[#e6e6ea]',
                              )}
                            >
                              {t('accounts.drawer.copyAll')}
                            </button>
                            {parts.map((part, index) => (
                              <button
                                key={`${part}-${index}`}
                                type="button"
                                title={t('accounts.drawer.copyPartTitle', {
                                  n: index + 1,
                                  value: part,
                                })}
                                onClick={() => copyLoginInfo(part)}
                                className={cn(
                                  'max-w-full h-7 px-2 rounded-[6px]',
                                  'border border-[var(--color-border)] bg-[var(--color-card)]',
                                  'text-[12px] leading-none font-mono text-[var(--color-foreground)]',
                                  'hover:border-[var(--color-muted-foreground-3)] hover:bg-[var(--color-muted)]',
                                  'truncate',
                                )}
                              >
                                {part}
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
                <TextField
                  label={t('accounts.drawer.proxy')}
                  value={form.proxy}
                  onChange={(e) => setForm({ ...form, proxy: e.target.value })}
                  placeholder={t('accounts.drawer.proxyPlaceholder')}
                  className="font-mono"
                />
                {editing ? (
                  <ProxyHealth
                    proxy={editing.proxy}
                    status={editing.proxy_status}
                    checkedAt={editing.proxy_checked_at}
                    exitIp={editing.proxy_exit_ip}
                    expiresAt={editing.proxy_expires_at}
                    country={editing.proxy_country}
                    region={editing.proxy_region}
                    onReplace={editing.geelark_phone_id
                      ? () => setReplaceTargets([editing.id])
                      : undefined}
                  />
                ) : null}
              </div>
            </section>

          </div>
        ) : null}
      </Drawer>

      {isWizardActive && (
        <GroupWizard
          mode="modal"
          initialGroupId={wizardInitialGroupId}
          initialGroupName={wizardInitialGroup?.name ?? (
            wizardInitialGroupId === null ? null : `#${wizardInitialGroupId}`
          )}
          onClose={closeWizard}
        />
      )}

      <ReplaceProxiesModal
        open={replaceTargets !== null}
        accounts={(replaceTargets || [])
          .map((id) => rows.find((r) => r.id === id))
          .filter((r): r is Account => !!r)
          .map((r) => ({
            id: r.id,
            profile_name: r.profile_name,
            group_name: r.group_name,
            proxy: r.proxy,
            proxy_expires_at: r.proxy_expires_at,
            geelark_phone_id: r.geelark_phone_id,
          }))}
        onClose={() => setReplaceTargets(null)}
        onSuccess={({ synced, failed, items }) => {
          setReplaceTargets(null)
          setSelected(new Set())
          if (failed === 0) {
            onToast({
              msg: t('replaceProxies.resultAllOk', { count: synced }),
              tone: 'green',
            })
          } else {
            const dbCount = items.filter((i) => i.db_updated).length
            onToast({
              msg: t('replaceProxies.resultPartial', {
                db: dbCount,
                failed,
              }),
              tone: 'orange',
            })
          }
          // Refresh the table so the new proxies + reset proxy_status show
          // up; the in-place drawer also re-reads from `rows`.
          api.accounts().then(setRows).catch(() => {})
        }}
      />

      <Modal
        open={newGroupOpen}
        onClose={() => {
          setNewGroupOpen(false)
          setNewGroupName('')
        }}
        title={t('accounts.newGroupModal.title')}
        submitLabel={t('accounts.newGroupModal.submit')}
        onSubmit={submitNewGroup}
      >
        <TextField
          label={t('accounts.newGroupModal.label')}
          value={newGroupName}
          autoFocus
          onChange={(e) => setNewGroupName(e.target.value)}
          placeholder={t('accounts.newGroupModal.placeholder')}
        />
      </Modal>

      <Modal
        open={confirmState !== null}
        onClose={() => {
          if (confirmBusy) return
          setConfirmState(null)
        }}
        title={confirmState?.title ?? ''}
        submitLabel={
          confirmBusy ? t('common.loading') : confirmState?.submitLabel
        }
        submitTone={confirmState?.submitTone ?? 'danger'}
        submitDisabled={confirmBusy}
        onSubmit={async (e) => {
          e.preventDefault()
          if (confirmBusy) return
          const action = confirmState?.onConfirm
          if (!action) return
          setConfirmBusy(true)
          try {
            await action()
          } finally {
            setConfirmBusy(false)
            setConfirmState(null)
          }
        }}
      >
        <p className="text-[13.5px] text-[var(--color-foreground-2)] leading-relaxed whitespace-pre-line">
          {confirmState?.body ?? ''}
        </p>
      </Modal>

      <Modal
        open={moveDialogIds !== null}
        onClose={closeMoveDialog}
        title={t('accounts.moveModal.title', {
          count: moveDialogIds?.length ?? 0,
        })}
        submitLabel={
          moveSubmitting
            ? t('common.loading')
            : t('accounts.moveModal.submit')
        }
        submitDisabled={moveTarget === 'unset' || moveSubmitting}
        onSubmit={submitMove}
      >
        <label className="block">
          <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
            {t('accounts.moveModal.label')}
          </span>
          <select
            value={moveTarget === 'unset' ? '' : moveTarget === 'ungrouped' ? '__ungrouped__' : String(moveTarget)}
            disabled={moveSubmitting}
            onChange={(e) => {
              const v = e.target.value
              if (v === '') setMoveTarget('unset')
              else if (v === '__ungrouped__') setMoveTarget('ungrouped')
              else setMoveTarget(Number(v))
            }}
            className="w-full bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[14px] text-[var(--color-foreground)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 focus:outline-none disabled:opacity-60"
          >
            <option value="" disabled>{t('accounts.moveModal.pick')}</option>
            <option value="__ungrouped__">{t('accounts.moveModal.ungrouped')}</option>
            {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
          </select>
        </label>
      </Modal>

      {groupSettingsTarget && (
        <Modal
          open={groupSettingsTarget != null}
          onClose={() => setGroupSettingsTarget(null)}
          title="Group settings"
        >
          <div className="flex flex-col gap-5 p-1">
            <TextField
              label="Group name"
              value={groupSettingsTarget.name}
              onChange={(e) =>
                setGroupSettingsTarget((prev) =>
                  prev ? { ...prev, name: e.target.value } : prev,
                )
              }
            />
            <label className="block">
              <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
                Caption prompt
              </span>
              <select
                value={groupSettingsTarget.caption_prompt_id ?? ''}
                onChange={(e) => {
                  const v = e.target.value
                  setGroupSettingsTarget((prev) =>
                    prev
                      ? { ...prev, caption_prompt_id: v ? Number(v) : null }
                      : prev,
                  )
                }}
                className="w-full bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[14px] text-[var(--color-foreground)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 transition-shadow"
              >
                <option value="">— none (publishing blocked) —</option>
                {captionPrompts.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <span className="block mt-1.5 text-[12px] text-[var(--color-muted-foreground)]">
                Manage the prompt library in Settings → Caption prompts.
                Groups without a prompt cannot publish.
              </span>
            </label>
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() => setGroupSettingsTarget(null)}
                disabled={groupSettingsSaving}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={groupSettingsSaving}
                onClick={async () => {
                  if (!groupSettingsTarget) return
                  setGroupSettingsSaving(true)
                  try {
                    await api.patchGroup(groupSettingsTarget.id, {
                      name: groupSettingsTarget.name.trim(),
                      caption_prompt_id: groupSettingsTarget.caption_prompt_id,
                    })
                    await reloadGroups()
                    setGroupSettingsTarget(null)
                  } catch (e) {
                    onToast({ msg: errorMessage(e, 'Save failed'), tone: 'red' })
                  } finally {
                    setGroupSettingsSaving(false)
                  }
                }}
              >
                {groupSettingsSaving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
      </main>
    </div>
  )
}
