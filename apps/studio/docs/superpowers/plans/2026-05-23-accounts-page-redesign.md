# Accounts Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inline-editable Accounts table with a read-only status table + right-side editing drawer. Long fields (email, proxy, picture URL) finally fit; the avatar becomes a real thumbnail instead of a URL string.

**Architecture:** Frontend-only. Main `<Table>` renders status; clicking any row (except the Login toggle) opens a new `<Drawer>` component with a transactional form. Two new helpers ship along: `<Avatar>` (image + monogram fallback) and `parseProxyHost` (extracts `host:port` for the compact proxy cell). No backend, schema, or scheduler changes; existing `api.updateAccount` PATCH accepts any subset of fields, so save sends only the diff.

**Tech Stack:** React 19 · TypeScript · Tailwind v4 · react-i18next · native `<dialog>` element. No new dependencies. No frontend test runner exists in the repo today — verification is `tsc -b && vite build` plus a manual QA pass against the spec checklist.

**Spec:** [docs/superpowers/specs/2026-05-23-accounts-page-redesign-design.md](../specs/2026-05-23-accounts-page-redesign-design.md)

---

## Conventions for this plan

- Each task ends with a green build (`cd frontend && npm run build`) and a commit. The build is the type-check gate — TypeScript errors must be zero.
- Source paths are relative to the repo root `/Users/Zhuanz/Documents/mkt`.
- Use the npm binary on PATH. If `npm` is not on PATH, prefix with `export PATH="/opt/homebrew/bin:$PATH"`.
- Existing components (`Modal`, `Toggle`, `Badge`, `ProgressBar`, `TextField`, `Button`, `Td/Th/Tr/Table`) keep their public APIs. New components live in `frontend/src/components/ui.tsx` alongside them.
- Commit messages follow the repo's existing pattern (`feat:`, `fix:`, `refactor:`).

---

## Task 1: Add `parseProxyHost` helper

**Files:**
- Create: `frontend/src/lib/proxy.ts`

A pure function used by the main table's Proxy cell. Given `socks5://user:pass@host.example.com:1080` it returns `host.example.com:1080`. Hides credentials. Returns the input trimmed if parsing fails so the cell never shows something completely wrong.

- [ ] **Step 1: Create `frontend/src/lib/proxy.ts`**

```ts
// Parse a proxy URL (e.g. socks5://user:pass@host.example.com:1080) and
// return the `host:port` slice — strips scheme, username, password.
//
// Falls back to the trimmed input if the value isn't a valid URL so the
// caller can still show *something* and rely on a tooltip carrying the
// full original for debugging.
export function parseProxyHost(proxy: string | null | undefined): string | null {
  if (!proxy) return null
  const trimmed = proxy.trim()
  if (!trimmed) return null
  try {
    // The URL constructor rejects unknown schemes in some browsers (Safari
    // historically rejects socks5://). Normalize anything that looks like a
    // scheme to http:// before parsing; if there's no scheme, prepend one.
    const hasScheme = /^[a-z][a-z0-9+\-.]*:\/\//i.test(trimmed)
    const normalized = hasScheme
      ? trimmed.replace(/^[a-z][a-z0-9+\-.]*:\/\//i, 'http://')
      : `http://${trimmed}`
    const u = new URL(normalized)
    if (!u.hostname) return trimmed
    return u.port ? `${u.hostname}:${u.port}` : u.hostname
  } catch {
    return trimmed
  }
}
```

- [ ] **Step 2: Type-check builds**

Run:
```bash
cd /Users/Zhuanz/Documents/mkt/frontend && npm run build
```

Expected: build succeeds with no TypeScript errors. (The function is dead code until Task 5 imports it, but `tsc -b` should still pass.)

- [ ] **Step 3: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add frontend/src/lib/proxy.ts
git commit -m "feat: parseProxyHost helper for masked proxy display"
```

---

## Task 2: Add `<Avatar>` component

**Files:**
- Modify: `frontend/src/components/ui.tsx` (add a new export at the end of the file, before the closing of the module; add `useState` to the imports)

Renders a 32px (or any-size) round avatar. If a URL is provided and the image loads, shows the image. Otherwise (no URL, or `onError`) falls back to a monogram (first letter of `name`) on a deterministically colored circle so each account has its own consistent color.

- [ ] **Step 1: Add `useState` to the imports in `frontend/src/components/ui.tsx`**

Find the imports block at the top of the file. It currently reads:

```ts
import {
  type ReactNode,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type LabelHTMLAttributes,
  useEffect,
  useRef,
  type FormEvent,
} from 'react'
```

Change to:

```ts
import {
  type ReactNode,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type LabelHTMLAttributes,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react'
```

- [ ] **Step 2: Append the `<Avatar>` component to `frontend/src/components/ui.tsx`**

Add this block at the very end of the file (after the existing `Dot` component):

```tsx
/* ─────────────────────── Avatar ─────────────────────── */
// Soft, low-saturation palette — meant to be a quiet identity hint, not loud.
const AVATAR_PALETTE: ReadonlyArray<{ bg: string; fg: string }> = [
  { bg: '#dbeafe', fg: '#1d4ed8' }, // blue
  { bg: '#dcfce7', fg: '#15803d' }, // green
  { bg: '#fee2e2', fg: '#b91c1c' }, // red
  { bg: '#ffedd5', fg: '#c2410c' }, // orange
  { bg: '#ede9fe', fg: '#6d28d9' }, // violet
  { bg: '#fce7f3', fg: '#be185d' }, // pink
  { bg: '#fef3c7', fg: '#a16207' }, // amber
  { bg: '#cffafe', fg: '#0e7490' }, // cyan
]

function hashStringToIndex(s: string, mod: number): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h) % mod
}

export function Avatar({
  url,
  name,
  size = 32,
}: {
  url?: string | null
  name: string
  size?: number
}) {
  const [errored, setErrored] = useState(false)
  // Reset error state whenever the URL itself changes (important for the
  // live preview thumbnail inside the drawer).
  useEffect(() => {
    setErrored(false)
  }, [url])
  const showImage = !!url && !errored
  const initial = (name?.trim()?.charAt(0) || '?').toUpperCase()
  const tone = AVATAR_PALETTE[hashStringToIndex(name || '?', AVATAR_PALETTE.length)]
  const px = `${size}px`
  return (
    <span
      className="inline-flex items-center justify-center rounded-full overflow-hidden flex-shrink-0"
      style={{ width: px, height: px, background: tone.bg }}
      aria-label={name}
    >
      {showImage ? (
        <img
          src={url!}
          alt=""
          onError={() => setErrored(true)}
          className="w-full h-full object-cover"
        />
      ) : (
        <span
          className="font-semibold leading-none"
          style={{ color: tone.fg, fontSize: `${Math.round(size * 0.42)}px` }}
        >
          {initial}
        </span>
      )}
    </span>
  )
}
```

- [ ] **Step 3: Build to confirm types compile**

Run:
```bash
cd /Users/Zhuanz/Documents/mkt/frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add frontend/src/components/ui.tsx
git commit -m "feat: Avatar component with image + monogram fallback"
```

---

## Task 3: Add `<Drawer>` component

**Files:**
- Modify: `frontend/src/components/ui.tsx` (append `<Drawer>` after `<Avatar>`)

A right-side slide-in panel built on the native `<dialog>` element. 480px wide, full viewport height, with header + scrollable body + footer slot. Sliding animation: the inner panel translates from `translate-x-full` to `translate-x-0` on the first animation frame after `showModal()`.

- [ ] **Step 1: Append the `<Drawer>` component to `frontend/src/components/ui.tsx`**

Add this block immediately after the `Avatar` block from Task 2:

```tsx
/* ─────────────────────── Drawer (right-side slide-in) ─────────────────────── */
export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
}: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
}) {
  const { t } = useTranslation()
  const ref = useRef<HTMLDialogElement | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const dlg = ref.current
    if (!dlg) return
    if (open) {
      if (!dlg.open) dlg.showModal()
      // Defer the visible-flag flip by a frame so the inner panel transitions
      // from translate-x-full → translate-x-0 instead of appearing instantly.
      const raf = requestAnimationFrame(() => setVisible(true))
      return () => cancelAnimationFrame(raf)
    } else {
      setVisible(false)
      // Wait for the slide-out animation before actually closing the dialog,
      // so the panel doesn't snap away.
      const timer = setTimeout(() => {
        if (dlg.open) dlg.close()
      }, 200)
      return () => clearTimeout(timer)
    }
  }, [open])

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
      className={cn(
        'bg-transparent p-0',
        'fixed top-0 right-0 bottom-0 left-auto',
        'h-screen max-h-none w-screen max-w-none m-0',
      )}
    >
      {/* Backdrop is the dialog itself (transparent); the panel sits inside
          the right edge. Backdrop color comes from the ::backdrop pseudo via
          the dialog's UA stylesheet. */}
      <div
        className={cn(
          'ml-auto h-full w-[480px] max-w-[100vw]',
          'flex flex-col',
          'bg-[var(--color-surface)] border-l border-[var(--color-line)]',
          'shadow-[-12px_0_32px_rgba(0,0,0,0.12)]',
          'transition-transform duration-200 ease-out',
          visible ? 'translate-x-0' : 'translate-x-full',
        )}
      >
        <div className="px-5 py-4 border-b border-[var(--color-line)] flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[15px] font-semibold text-[var(--color-ink)] truncate">
              {title}
            </div>
            {subtitle ? (
              <div className="mt-0.5 text-[12.5px] text-[var(--color-ink-3)] truncate">
                {subtitle}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 rounded-full hover:bg-[var(--color-gray-bg)] text-[var(--color-ink-3)] flex items-center justify-center flex-shrink-0"
            aria-label={t('common.close')}
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer ? (
          <div className="px-5 py-3.5 border-t border-[var(--color-line)] bg-[var(--color-surface-hover)]">
            {footer}
          </div>
        ) : null}
      </div>
    </dialog>
  )
}
```

- [ ] **Step 2: Build to confirm types compile**

Run:
```bash
cd /Users/Zhuanz/Documents/mkt/frontend && npm run build
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add frontend/src/components/ui.tsx
git commit -m "feat: Drawer component (right-side slide-in via <dialog>)"
```

---

## Task 4: Update i18n locales

**Files:**
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

Add the new `accounts.drawer.*` group, add `accounts.col.account`, remove `accounts.col.pictureUrl`, update `accounts.inventoryHeading`, update `accounts.modal.body` and `accounts.toast.added` (both still reference the old "edit cells inline" flow), add `accounts.toast.saved`.

- [ ] **Step 1: Replace the `accounts.col` block in `en.json`**

In `frontend/src/i18n/locales/en.json`, find:

```json
    "col": {
      "profile": "Profile",
      "email": "Email",
      "proxy": "Proxy",
      "pictureUrl": "Picture URL",
      "maxWarm": "Max warm",
      "loggedIn": "Logged in",
      "warmProgress": "Warm progress",
      "lastWarm": "Last warm"
    },
```

Replace with:

```json
    "col": {
      "account": "Account",
      "email": "Email",
      "proxy": "Proxy",
      "maxWarm": "Max warm",
      "loggedIn": "Logged in",
      "warmProgress": "Warm progress",
      "lastWarm": "Last warm"
    },
```

(`profile` → `account`; `pictureUrl` removed.)

- [ ] **Step 2: Update outdated strings in `en.json`**

Find:
```json
    "inventoryHeading": "Inventory · click any cell to edit, saves on blur",
```
Replace with:
```json
    "inventoryHeading": "Inventory · click a row to edit",
```

Find:
```json
      "body": "Pick a unique profile name. After creating, fill in email, proxy, picture URL and max warm time directly in the table row — cells save on blur.",
```
Replace with:
```json
      "body": "Pick a unique profile name. After creating, click the new row to open the editor and fill in the rest.",
```

Find:
```json
      "added": "{{name}} added — fill the row's cells",
```
Replace with:
```json
      "added": "{{name}} added — click the row to edit",
```

- [ ] **Step 3: Add `saved` toast and `drawer` group to `en.json`**

In the `accounts.toast` object (find the line `"deleteFailed": "Delete failed"`), add a comma after `"Delete failed"` and insert before the closing `}`:

The block to find:
```json
      "removed": "{{name}} removed",
      "deleteFailed": "Delete failed"
    }
  },
```

Replace with:
```json
      "removed": "{{name}} removed",
      "deleteFailed": "Delete failed",
      "saved": "Changes saved"
    },
    "drawer": {
      "title": "Edit account",
      "identity": "Identity",
      "identityName": "Profile name",
      "identityPhone": "Phone",
      "identityNoPhone": "No phone linked",
      "avatar": "Avatar",
      "avatarUrl": "Picture URL",
      "avatarPreviewEmpty": "Paste a URL to preview the image.",
      "credentials": "Credentials",
      "email": "Email",
      "proxy": "Proxy",
      "proxyPlaceholder": "socks5://user:pass@host:port",
      "warmup": "Warm-up",
      "maxWarm": "Max warm time (seconds)",
      "deleteAccount": "Delete account",
      "saveChanges": "Save changes",
      "unsavedConfirm": "Discard unsaved changes?"
    }
  },
```

Confirm the closing `},` matches the original (the `accounts` object closes with `},` before `"videos": {` starts).

- [ ] **Step 4: Mirror all four edits to `zh.json`**

In `frontend/src/i18n/locales/zh.json`:

Find:
```json
    "col": {
      "profile": "账号",
      "email": "邮箱",
      "proxy": "代理",
      "pictureUrl": "头像 URL",
      "maxWarm": "最大养号时长",
      "loggedIn": "已登录",
      "warmProgress": "养号进度",
      "lastWarm": "上次养号"
    },
```
Replace with:
```json
    "col": {
      "account": "账号",
      "email": "邮箱",
      "proxy": "代理",
      "maxWarm": "最大养号时长",
      "loggedIn": "已登录",
      "warmProgress": "养号进度",
      "lastWarm": "上次养号"
    },
```

Find:
```json
    "inventoryHeading": "清单 · 点击单元格编辑，失焦保存",
```
Replace with:
```json
    "inventoryHeading": "清单 · 点击行打开编辑",
```

Find:
```json
      "body": "选一个唯一的账号名。创建后，在表格行内直接填写邮箱、代理、头像 URL 和最大养号时长——单元格失焦自动保存。",
```
Replace with:
```json
      "body": "选一个唯一的账号名。创建后点击新行打开编辑面板，填写其余字段。",
```

Find:
```json
      "added": "{{name}} 已添加——请补全该行",
```
Replace with:
```json
      "added": "{{name}} 已添加——点击该行进行编辑",
```

Find:
```json
      "removed": "{{name}} 已删除",
      "deleteFailed": "删除失败"
    }
  },
```
Replace with:
```json
      "removed": "{{name}} 已删除",
      "deleteFailed": "删除失败",
      "saved": "修改已保存"
    },
    "drawer": {
      "title": "编辑账号",
      "identity": "基础信息",
      "identityName": "账号名",
      "identityPhone": "手机",
      "identityNoPhone": "未关联手机",
      "avatar": "头像",
      "avatarUrl": "头像 URL",
      "avatarPreviewEmpty": "粘贴 URL 后将显示预览。",
      "credentials": "凭据",
      "email": "邮箱",
      "proxy": "代理",
      "proxyPlaceholder": "socks5://user:pass@host:port",
      "warmup": "养号",
      "maxWarm": "最大养号时长（秒）",
      "deleteAccount": "删除账号",
      "saveChanges": "保存修改",
      "unsavedConfirm": "放弃未保存的修改？"
    }
  },
```

- [ ] **Step 5: Validate JSON & build**

Run:
```bash
cd /Users/Zhuanz/Documents/mkt
python3 -c "import json; json.load(open('frontend/src/i18n/locales/en.json')); json.load(open('frontend/src/i18n/locales/zh.json')); print('OK')"
cd frontend && npm run build
```

Expected: prints `OK`, then build succeeds. (`pictureUrl` is no longer referenced anywhere yet — Task 5 will stop reading it.)

- [ ] **Step 6: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add frontend/src/i18n/locales/en.json frontend/src/i18n/locales/zh.json
git commit -m "i18n: accounts drawer keys + drop pictureUrl + refresh outdated copy"
```

---

## Task 5: Rewrite `Accounts.tsx`

**Files:**
- Replace: `frontend/src/pages/Accounts.tsx`

The big change. Main row becomes read-only display (with Login toggle as the one exception). Clicking anywhere else on a row opens the drawer. Drawer holds local form state, computes diff on Save, calls existing `api.updateAccount`. Delete moves into the drawer footer.

- [ ] **Step 1: Replace the entire contents of `frontend/src/pages/Accounts.tsx`**

Overwrite the file with:

```tsx
import { useEffect, useState, type FormEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { api, streamPost } from '../api'
import type { ToastState } from '../App'
import { parseProxyHost } from '../lib/proxy'
import {
  Avatar,
  Badge,
  Button,
  Drawer,
  EmptyState,
  LogPanel,
  Modal,
  PageTitle,
  ProgressBar,
  SectionTitle,
  TBody,
  THead,
  Table,
  Td,
  TextField,
  Th,
  Toggle,
  Tr,
} from '../components/ui'

type Account = {
  id: number
  profile_name: string
  email: string | null
  proxy: string | null
  profile_pic_url: string | null
  geelark_phone_id: string | null
  tiktok_logged_in: number
  warmed_up: number
  time_spent_warming: number
  max_warm_time: number
  pic_status: string
  last_warm_result: string | null
}

type EditForm = {
  profile_name: string
  email: string
  proxy: string
  profile_pic_url: string
  max_warm_time: string
}

const toForm = (a: Account): EditForm => ({
  profile_name: a.profile_name,
  email: a.email ?? '',
  proxy: a.proxy ?? '',
  profile_pic_url: a.profile_pic_url ?? '',
  max_warm_time: String(a.max_warm_time ?? 500),
})

const formsEqual = (a: EditForm, b: EditForm) =>
  a.profile_name === b.profile_name &&
  a.email === b.email &&
  a.proxy === b.proxy &&
  a.profile_pic_url === b.profile_pic_url &&
  a.max_warm_time === b.max_warm_time

export default function Accounts({ onToast }: { onToast: (t: ToastState) => void }) {
  const { t } = useTranslation()
  const [rows, setRows] = useState<Account[]>([])
  const [log, setLog] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [newName, setNewName] = useState('')

  // Drawer state
  const [editing, setEditing] = useState<Account | null>(null)
  const [form, setForm] = useState<EditForm | null>(null)
  const [saving, setSaving] = useState(false)
  const [previewUrl, setPreviewUrl] = useState('')

  const load = () =>
    api
      .accounts()
      .then(setRows)
      .catch(() => {})
  useEffect(() => {
    load()
  }, [])

  // Debounce the avatar preview URL so we don't reload the image on every keystroke.
  useEffect(() => {
    if (!form) return
    const url = form.profile_pic_url
    const handle = setTimeout(() => setPreviewUrl(url), 300)
    return () => clearTimeout(handle)
  }, [form?.profile_pic_url])

  const openDrawer = (a: Account) => {
    setEditing(a)
    setForm(toForm(a))
    setPreviewUrl(a.profile_pic_url ?? '')
  }

  const isDirty = (): boolean => {
    if (!editing || !form) return false
    return !formsEqual(form, toForm(editing))
  }

  const discardAndClose = () => {
    setEditing(null)
    setForm(null)
    setPreviewUrl('')
    setSaving(false)
  }

  const requestClose = () => {
    if (isDirty() && !confirm(t('accounts.drawer.unsavedConfirm'))) return
    discardAndClose()
  }

  const saveDrawer = async () => {
    if (!editing || !form) return
    const base = toForm(editing)
    const diff: Record<string, string | number> = {}
    if (base.profile_name !== form.profile_name)
      diff.profile_name = form.profile_name.trim()
    if (base.email !== form.email) diff.email = form.email.trim()
    if (base.proxy !== form.proxy) diff.proxy = form.proxy.trim()
    if (base.profile_pic_url !== form.profile_pic_url)
      diff.profile_pic_url = form.profile_pic_url.trim()
    if (base.max_warm_time !== form.max_warm_time) {
      const n = Number(form.max_warm_time)
      if (!Number.isFinite(n) || n < 0) {
        onToast({
          msg: t('accounts.toast.saveFailed', { field: 'max_warm_time' }),
          tone: 'red',
        })
        return
      }
      diff.max_warm_time = n
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

  const deleteFromDrawer = async () => {
    if (!editing) return
    if (!confirm(t('accounts.deleteConfirm', { name: editing.profile_name }))) return
    try {
      await api.deleteAccount(editing.id)
      onToast({
        msg: t('accounts.toast.removed', { name: editing.profile_name }),
        tone: 'orange',
      })
      discardAndClose()
      load()
    } catch {
      onToast({ msg: t('accounts.toast.deleteFailed'), tone: 'red' })
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

  const setPics = async () => {
    try {
      await api.setPics()
      onToast({ msg: t('accounts.toast.picsDispatched'), tone: 'blue' })
      load()
    } catch {
      onToast({ msg: t('accounts.toast.picsFailed'), tone: 'red' })
    }
  }

  const createProfiles = () => {
    setLog([])
    setRunning(true)
    streamPost('/accounts/create-profiles-stream', (l) => setLog((p) => [...p, l]))
      .then(() => {
        onToast({ msg: t('accounts.toast.createProfilesDone'), tone: 'green' })
        load()
      })
      .catch(() => onToast({ msg: t('accounts.toast.streamFailed'), tone: 'red' }))
      .finally(() => setRunning(false))
  }

  const addAccount = async (e: FormEvent) => {
    e.preventDefault()
    const name = newName.trim()
    if (!name) return
    try {
      await api.createAccount({ profile_name: name })
      setNewName('')
      setAddOpen(false)
      onToast({ msg: t('accounts.toast.added', { name }), tone: 'blue' })
      load()
    } catch {
      onToast({ msg: t('accounts.toast.nameUnique'), tone: 'red' })
    }
  }

  const loggedIn = rows.filter((r) => r.tiktok_logged_in).length
  const warmed = rows.filter((r) => r.warmed_up).length

  return (
    <div className="flex flex-col gap-7">
      <PageTitle
        title={t('accounts.title')}
        subtitle={t('accounts.subtitle', {
          total: rows.length,
          loggedIn,
          warmed,
        })}
        right={
          <>
            <Button variant="ghost" onClick={importGeelark}>
              {t('accounts.importFromGeelark')}
            </Button>
            <Button variant="ghost" onClick={setPics}>
              {t('accounts.setPictures')}
            </Button>
            <Button variant="secondary" onClick={createProfiles} disabled={running}>
              {running ? t('accounts.creating') : t('accounts.createProfiles')}
            </Button>
            <Button variant="primary" onClick={() => setAddOpen(true)}>
              {t('accounts.addAccount')}
            </Button>
          </>
        }
      />

      {(running || log.length > 0) && (
        <section>
          <SectionTitle>{t('accounts.createProfilesLive')}</SectionTitle>
          <LogPanel lines={log} active={running} />
        </section>
      )}

      <section>
        <SectionTitle>{t('accounts.inventoryHeading')}</SectionTitle>
        <Table>
          <THead>
            <Tr className="hover:bg-transparent">
              <Th>{t('accounts.col.account')}</Th>
              <Th>{t('accounts.col.email')}</Th>
              <Th>{t('accounts.col.proxy')}</Th>
              <Th align="right">{t('accounts.col.maxWarm')}</Th>
              <Th align="center">{t('accounts.col.loggedIn')}</Th>
              <Th>{t('accounts.col.warmProgress')}</Th>
              <Th align="center">{t('accounts.col.lastWarm')}</Th>
              <Th align="right" className="w-[24px]" />
            </Tr>
          </THead>
          <TBody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={8}>
                  <EmptyState title={t('accounts.emptyTitle')}>
                    <Trans
                      i18nKey="accounts.emptyBody"
                      components={{
                        a: (
                          <span className="text-[var(--color-blue)] font-medium">
                            {t('accounts.addAccount')}
                          </span>
                        ),
                        b: (
                          <span className="text-[var(--color-blue)] font-medium">
                            {t('accounts.importFromGeelark')}
                          </span>
                        ),
                      }}
                    />
                  </EmptyState>
                </td>
              </tr>
            ) : (
              rows.map((r) => {
                const proxyShort = parseProxyHost(r.proxy)
                return (
                  <Tr
                    key={r.id}
                    className="cursor-pointer group"
                    // Open the drawer when the user clicks anywhere on the row.
                    // The Login toggle stops propagation on its wrapper.
                    onClick={() => openDrawer(r)}
                  >
                    <Td>
                      <div className="flex items-center gap-3">
                        <Avatar
                          url={r.profile_pic_url}
                          name={r.profile_name}
                          size={32}
                        />
                        <div className="min-w-0">
                          <div className="text-[var(--color-ink)] font-medium truncate">
                            {r.profile_name}
                          </div>
                          <div className="text-[11.5px] text-[var(--color-ink-4)] truncate">
                            {r.geelark_phone_id
                              ? t('accounts.phoneLabel', { id: r.geelark_phone_id })
                              : t('accounts.noPhone')}
                          </div>
                        </div>
                      </div>
                    </Td>
                    <Td>
                      {r.email ? (
                        <span
                          title={r.email}
                          className="block truncate max-w-[220px]"
                        >
                          {r.email}
                        </span>
                      ) : (
                        <span className="text-[var(--color-ink-5)]">—</span>
                      )}
                    </Td>
                    <Td>
                      {proxyShort ? (
                        <span
                          title={r.proxy ?? ''}
                          className="block truncate max-w-[180px] font-mono text-[12.5px] text-[var(--color-ink-2)]"
                        >
                          {proxyShort}
                        </span>
                      ) : (
                        <span className="text-[var(--color-ink-5)]">—</span>
                      )}
                    </Td>
                    <Td align="right" className="tabular-nums">
                      {r.max_warm_time ?? 500}
                    </Td>
                    <Td align="center">
                      <div
                        className="inline-flex justify-center w-full"
                        // Toggle is the one inline-editable element. Stop the
                        // row click handler so toggling never opens the drawer.
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Toggle
                          checked={!!r.tiktok_logged_in}
                          ariaLabel={`${t('accounts.col.loggedIn')} ${r.profile_name}`}
                          onChange={() => api.loginToggle(r.id).then(load)}
                        />
                      </div>
                    </Td>
                    <Td>
                      {r.warmed_up ? (
                        <Badge tone="green">{t('accounts.warmedBadge')}</Badge>
                      ) : (
                        <ProgressBar
                          value={r.time_spent_warming}
                          total={r.max_warm_time || 500}
                        />
                      )}
                    </Td>
                    <Td align="center">
                      {r.last_warm_result === 'Success' ? (
                        <Badge tone="green">{t('accounts.warmOk')}</Badge>
                      ) : r.last_warm_result === 'Failed' ? (
                        <Badge tone="red">{t('accounts.warmFailed')}</Badge>
                      ) : (
                        <span className="text-[var(--color-ink-4)]">—</span>
                      )}
                    </Td>
                    <Td align="right">
                      <span className="text-[var(--color-ink-5)] opacity-0 group-hover:opacity-100 transition-opacity">
                        →
                      </span>
                    </Td>
                  </Tr>
                )
              })
            )}
          </TBody>
        </Table>
      </section>

      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title={t('accounts.modal.title')}
        submitLabel={t('accounts.modal.submit')}
        onSubmit={addAccount}
      >
        <p className="text-[13.5px] text-[var(--color-ink-2)] mb-4 leading-relaxed">
          {t('accounts.modal.body')}
        </p>
        <TextField
          label={t('accounts.modal.profileName')}
          value={newName}
          autoFocus
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t('accounts.modal.namePlaceholder')}
        />
      </Modal>

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
                className="text-[var(--color-red)] hover:bg-[var(--color-red-bg)] hover:text-[var(--color-red)]"
                onClick={deleteFromDrawer}
              >
                {t('accounts.drawer.deleteAccount')}
              </Button>
              <div className="flex gap-2">
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
                  <span className="block text-[12.5px] font-medium text-[var(--color-ink-2)] mb-1.5">
                    {t('accounts.drawer.identityPhone')}
                  </span>
                  <span className="text-[13.5px] text-[var(--color-ink-3)] font-mono">
                    {editing?.geelark_phone_id ??
                      t('accounts.drawer.identityNoPhone')}
                  </span>
                </div>
              </div>
            </section>

            <section>
              <SectionTitle>{t('accounts.drawer.avatar')}</SectionTitle>
              <TextField
                label={t('accounts.drawer.avatarUrl')}
                value={form.profile_pic_url}
                onChange={(e) =>
                  setForm({ ...form, profile_pic_url: e.target.value })
                }
                placeholder="https://…"
              />
              <div className="mt-3 flex items-center gap-3">
                <Avatar
                  url={previewUrl || null}
                  name={form.profile_name || '?'}
                  size={80}
                />
                {!previewUrl ? (
                  <span className="text-[12.5px] text-[var(--color-ink-4)]">
                    {t('accounts.drawer.avatarPreviewEmpty')}
                  </span>
                ) : null}
              </div>
            </section>

            <section>
              <SectionTitle>{t('accounts.drawer.credentials')}</SectionTitle>
              <div className="flex flex-col gap-3">
                <TextField
                  label={t('accounts.drawer.email')}
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="user@example.com"
                />
                <TextField
                  label={t('accounts.drawer.proxy')}
                  value={form.proxy}
                  onChange={(e) => setForm({ ...form, proxy: e.target.value })}
                  placeholder={t('accounts.drawer.proxyPlaceholder')}
                  className="font-mono"
                />
              </div>
            </section>

            <section>
              <SectionTitle>{t('accounts.drawer.warmup')}</SectionTitle>
              <TextField
                label={t('accounts.drawer.maxWarm')}
                type="number"
                value={form.max_warm_time}
                onChange={(e) =>
                  setForm({ ...form, max_warm_time: e.target.value })
                }
              />
            </section>
          </div>
        ) : null}
      </Drawer>
    </div>
  )
}
```

- [ ] **Step 2: Build to confirm the rewrite compiles**

Run:
```bash
cd /Users/Zhuanz/Documents/mkt/frontend && npm run build
```

Expected: build succeeds, no TypeScript errors. (The unused `InlineInput` import is gone; the `parseProxyHost`, `Avatar`, and `Drawer` symbols are now used.)

- [ ] **Step 3: Sanity-check that pytest still passes**

The change is frontend-only, but the project's invariant is "38 passed" (per CLAUDE.md). Confirm:

```bash
cd /Users/Zhuanz/Documents/mkt && ./.venv/bin/pytest -q
```

Expected: `38 passed`.

- [ ] **Step 4: Commit**

```bash
cd /Users/Zhuanz/Documents/mkt
git add frontend/src/pages/Accounts.tsx
git commit -m "feat: Accounts page — read-only table + right-side edit drawer"
```

---

## Task 6: Manual QA pass

**Files:** none modified. This task verifies the spec's manual QA checklist against the running app and records what was observed.

- [ ] **Step 1: Start the app**

```bash
cd /Users/Zhuanz/Documents/mkt && ./run.sh
```

Wait for the line indicating uvicorn is listening on `:8080`. Open `http://localhost:8080` in a browser and sign in.

- [ ] **Step 2: Walk through every item in the spec's manual QA checklist**

The checklist (from [the spec](../specs/2026-05-23-accounts-page-redesign-design.md)):

1. Accounts page renders. Accounts with `profile_pic_url` show their image; accounts without show a monogram with a stable color per name.
2. Long emails truncate in the cell; full email visible on hover via `title` tooltip.
3. Proxy cell shows only `host:port`; password is not visible in the table; full URL appears on hover.
4. Clicking anywhere on a row except the Login toggle opens the drawer from the right.
5. Clicking the Login toggle flips its state; the drawer does not open.
6. Inside the drawer, change one field, click **Save changes** — a green toast appears, the drawer closes, and the row reflects the new value.
7. Inside the drawer, change a field, then click **Cancel** / press **Esc** / click outside the panel — `confirm()` fires; choosing Cancel keeps the change in the drawer, choosing OK discards.
8. Inside the drawer, change nothing, click **Cancel** — closes immediately, no confirm.
9. In the drawer's Avatar URL field, paste a working image URL — the 80×80 preview appears ~300ms later. Paste obviously invalid text — Avatar shows the monogram fallback (no broken-image icon) and the "Paste a URL to preview" caption disappears.
10. In the drawer, click **Delete account** — existing delete confirmation runs; on confirm the drawer closes and the row disappears.
11. Toggle the sidebar language to 中文 — every label inside the drawer translates.
12. `npm run build` succeeds with no new TypeScript errors (already done in Task 5).

Record each result. Any failure → fix in a follow-up task before final commit.

- [ ] **Step 3: Final commit (only if Step 2 found small visual touch-ups worth applying)**

If the manual pass surfaces minor adjustments (e.g. a Tailwind class tweak), apply them and commit:

```bash
cd /Users/Zhuanz/Documents/mkt
git add -A
git commit -m "fix: Accounts redesign — visual QA adjustments"
```

If the manual pass surfaces no issues, skip this step; the implementation is complete at the end of Task 5.

---

## Self-review notes

**Spec coverage:**

- Main table 8 columns, Picture URL gone, avatar in Account column → Task 5.
- Login toggle stays inline as the one exception → Task 5 (Toggle wrapper has `stopPropagation`).
- Drawer 480px, slide-in animation, ESC/backdrop close with unsaved-confirm, transactional Save → Task 3 + Task 5.
- Live avatar preview, 300ms debounce, error fallback → Task 5 useEffect + Task 2 Avatar `onError`.
- Proxy parsing → Task 1, consumed in Task 5.
- Monogram fallback with deterministic color → Task 2 `hashStringToIndex`.
- Delete moves into drawer footer → Task 5 `deleteFromDrawer`.
- All required new i18n keys + outdated copy refreshed in both locales → Task 4.
- Manual QA checklist run as the verification gate → Task 6.

**Type consistency:**

- `Avatar({ url, name, size })` signature matches between Task 2 definition and the two call sites in Task 5 (main row size=32, drawer preview size=80).
- `Drawer({ open, onClose, title, subtitle, children, footer })` matches between Task 3 definition and Task 5 usage.
- `parseProxyHost` signature `(proxy: string | null | undefined) => string | null` matches between Task 1 definition and Task 5 usage on `r.proxy: string | null`.
- `EditForm`, `Account`, `toForm`, `formsEqual` all defined and consumed within the same file (Task 5).

**Placeholder scan:** No `TBD`, no `TODO`, no "implement later", no "similar to Task N" — every step lists the literal code to apply.
