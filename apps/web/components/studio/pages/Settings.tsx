'use client'

import { useCallback, useEffect, useState } from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { api, type CaptionPrompt, type CommentPrompt } from '@/lib/studio/api'
import { KeyRound, Save, Settings2, Sparkles } from 'lucide-react'
import { useStudioToast, type ToastState } from '@/components/studio/studio-providers'
import { Button, Card, Modal, PageHeader, Panel, SectionTitle, TextField, Toggle } from '@/components/ui'

type SettingsMap = Record<string, string>
type SecretStatus = { set: boolean; masked: string }
type SecretsMap = Record<string, SecretStatus>
type GeminiModel = { id: string; display_name: string; status: 'GA' | 'Preview' }

const GEMINI_MODEL_KEY = 'gemini_model'
const GEMINI_HIGH_QUALITY_KEY = 'gemini_high_quality'

const SECRET_KEYS = [
  'GEELARK_TOKEN',
  'GEMINI_API_KEY',
] as const

export default function Settings() {
  const onToast = useStudioToast()
  const t = useStudioT()
  const [s, setS] = useState<SettingsMap>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  const [secrets, setSecrets] = useState<SecretsMap>({})
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({})
  const [savingSecrets, setSavingSecrets] = useState(false)
  const [secretsForbidden, setSecretsForbidden] = useState(false)

  const loadSecrets = () => {
    api
      .secrets()
      .then(setSecrets)
      .catch(() => setSecretsForbidden(true))
  }

  useEffect(() => {
    api
      .settings()
      .then(setS)
      .catch(() => {})
    loadSecrets()
  }, [])

  const secretsDirty = SECRET_KEYS.some((k) => (secretInputs[k] ?? '').trim() !== '')

  const saveSecrets = async () => {
    const payload: Record<string, string> = {}
    for (const k of SECRET_KEYS) {
      const v = (secretInputs[k] ?? '').trim()
      if (v) payload[k] = v
    }
    if (Object.keys(payload).length === 0) return
    setSavingSecrets(true)
    try {
      const res = await api.updateSecrets(payload)
      const updated: string[] = res?.updated ?? []
      setSecretInputs({})
      loadSecrets()
      onToast({
        msg: t('settings.toast.secretsUpdated', { count: updated.length }),
        tone: 'green',
      })
    } catch {
      onToast({ msg: t('settings.toast.secretsFailed'), tone: 'red' })
    } finally {
      setSavingSecrets(false)
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      const next = await api.updateSettings(s)
      setS(next)
      setDirty(false)
      onToast({ msg: t('settings.toast.saved'), tone: 'green' })
    } catch {
      onToast({ msg: t('settings.toast.saveFailed'), tone: 'red' })
    } finally {
      setSaving(false)
    }
  }

  const update = (k: string, v: string) => {
    setS((prev) => ({ ...prev, [k]: v }))
    setDirty(true)
  }

  const keys = Object.keys(s).filter(
    (k) => k !== GEMINI_MODEL_KEY && k !== GEMINI_HIGH_QUALITY_KEY,
  )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow={t('settings.eyebrow')}
        title={t('settings.title')}
        actions={
          <Button variant="primary" onClick={save} disabled={!dirty || saving}>
            <Save size={14} />
            {saving ? t('settings.saving') : dirty ? t('settings.save') : t('settings.noChanges')}
          </Button>
        }
      />

      <Panel
        title={t('settings.schedulerHeading')}
        description={t('settings.schedulerSubtitle')}
        right={<Settings2 size={15} className="text-[var(--color-muted-foreground-2)]" />}
        bodyClassName="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-5"
      >
          {keys.length === 0 ? (
            <p className="text-[13px] text-[var(--color-muted-foreground)] col-span-2">
              {t('common.loading')}
            </p>
          ) : (
            <>
              {keys.map((k) => {
                const label = t(`settings.meta.${k}.label`, { defaultValue: k })
                const hint = t(`settings.meta.${k}.hint`, { defaultValue: '' })
                return (
                  <TextField
                    key={k}
                    label={label}
                    hint={hint || undefined}
                    value={s[k] ?? ''}
                    onChange={(e) => update(k, e.target.value)}
                  />
                )
              })}
              <GeminiModelPicker
                value={s[GEMINI_MODEL_KEY] ?? ''}
                onChange={(v) => update(GEMINI_MODEL_KEY, v)}
              />
            </>
          )}
      </Panel>

      <Panel
        title="Gemini API"
        description="Generation quality controls shared by captions and comments."
        right={<Sparkles size={15} className="text-[var(--color-orange)]" />}
        bodyClassName="flex flex-col gap-5"
      >
          <label className="block">
            <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
              High-quality mode
            </span>
            <Toggle
              checked={s[GEMINI_HIGH_QUALITY_KEY] === '1'}
              onChange={() =>
                update(
                  GEMINI_HIGH_QUALITY_KEY,
                  s[GEMINI_HIGH_QUALITY_KEY] === '1' ? '0' : '1',
                )
              }
              ariaLabel="Gemini high-quality mode"
            />
            <span className="block mt-1.5 text-[12px] text-[var(--color-muted-foreground)]">
              Enables Gemini thinking for every AI generation in the project —
              captions and comments — slower and costlier. Try it when the
              output quality is unstable.
            </span>
          </label>
      </Panel>

      <CaptionPromptsSection onToast={onToast} />
      <CommentPromptsSection onToast={onToast} />


      {!secretsForbidden ? <Panel
        title={t('settings.secretsHeading')}
        description={t('settings.secretsSubtitle')}
        right={<KeyRound size={15} className="text-[var(--color-muted-foreground-2)]" />}
        bodyClassName="flex flex-col gap-5"
      >
          <p className="text-[12.5px] text-[var(--color-muted-foreground)] leading-relaxed">
            工作空间凭据使用服务端主密钥加密保存，接口只返回掩码。
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-5">
            {SECRET_KEYS.map((k) => {
              const status = secrets[k]
              const current = status?.set
                ? t('settings.currentMasked', { masked: status.masked })
                : t('settings.notSet')
              const label = t(`settings.secret.${k}.label`)
              const hint = t(`settings.secret.${k}.hint`)
              return (
                <TextField
                  key={k}
                  type="password"
                  autoComplete="new-password"
                  label={label}
                  hint={`${current} · ${hint}`}
                  placeholder={status?.set ? status.masked : t('settings.enterValue')}
                  value={secretInputs[k] ?? ''}
                  onChange={(e) =>
                    setSecretInputs((prev) => ({
                      ...prev,
                      [k]: e.target.value,
                    }))
                  }
                />
              )
            })}
          </div>

          <div className="flex justify-end gap-2">
            {secretsDirty ? (
              <Button
                variant="ghost"
                onClick={() => setSecretInputs({})}
                disabled={savingSecrets}
              >
                {t('settings.discard')}
              </Button>
            ) : null}
            <Button
              variant="primary"
              onClick={saveSecrets}
              disabled={!secretsDirty || savingSecrets}
            >
              {savingSecrets ? t('settings.saving') : t('settings.saveSecrets')}
            </Button>
          </div>
      </Panel> : null}

    </div>
  )
}

function CaptionPromptsSection({ onToast }: { onToast: (t: ToastState) => void }) {
  const [prompts, setPrompts] = useState<CaptionPrompt[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<
    { id: number | null; name: string; body: string } | null
  >(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.captionPrompts()
      .then(setPrompts)
      .catch(() => onToast({ msg: 'Failed to load prompts', tone: 'red' }))
      .finally(() => setLoading(false))
  }, [onToast])
  useEffect(() => {
    const id = window.setTimeout(() => {
      load()
    }, 0)
    return () => window.clearTimeout(id)
  }, [load])

  const save = async () => {
    if (!editing) return
    const name = editing.name.trim()
    const body = editing.body.trim()
    if (!name || !body) {
      onToast({ msg: 'Name and body are required', tone: 'red' })
      return
    }
    setSaving(true)
    try {
      if (editing.id == null) {
        await api.createCaptionPrompt({ name, body })
      } else {
        await api.updateCaptionPrompt(editing.id, { name, body })
      }
      setEditing(null)
      load()
      onToast({ msg: 'Prompt saved', tone: 'green' })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      onToast({ msg, tone: 'red' })
    } finally {
      setSaving(false)
    }
  }

  const remove = async (p: CaptionPrompt) => {
    if (!confirm(`Delete prompt "${p.name}"? Groups using it must be re-bound first.`)) return
    try {
      await api.deleteCaptionPrompt(p.id)
      load()
      onToast({ msg: 'Prompt deleted', tone: 'green' })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Delete failed'
      onToast({ msg, tone: 'red' })
    }
  }

  return (
    <section>
      <SectionTitle>Caption prompts</SectionTitle>
      <Card className="p-6 flex flex-col gap-4">
        <p className="text-[12.5px] text-[var(--color-muted-foreground)] leading-relaxed">
          Each group must pick one prompt. Without a prompt, the group cannot
          publish videos. Editing a prompt body takes effect on the next
          caption generation for every group bound to it.
        </p>
        {loading ? (
          <p className="text-[13px] text-[var(--color-muted-foreground)]">Loading…</p>
        ) : prompts.length === 0 ? (
          <p className="text-[13px] text-[var(--color-muted-foreground)]">No prompts yet.</p>
        ) : (
          <div className="flex flex-col divide-y divide-[var(--color-border)]">
            {prompts.map((p) => (
              <div key={p.id} className="py-3 flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-medium text-[var(--color-foreground)]">{p.name}</div>
                  <div className="text-[12px] text-[var(--color-muted-foreground)] mt-0.5 line-clamp-2 font-mono leading-relaxed">
                    {p.body.slice(0, 200)}{p.body.length > 200 ? '…' : ''}
                  </div>
                </div>
                <Button variant="ghost" size="sm"
                        onClick={() => setEditing({ id: p.id, name: p.name, body: p.body })}>
                  Edit
                </Button>
                <Button variant="ghost" size="sm" onClick={() => remove(p)}>
                  Delete
                </Button>
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-end">
          <Button variant="primary"
                  onClick={() => setEditing({ id: null, name: '', body: '' })}>
            + New prompt
          </Button>
        </div>
      </Card>

      {editing && (
        <Modal open onClose={() => setEditing(null)}
               title={editing.id == null ? 'New caption prompt' : 'Edit caption prompt'}>
          <div className="flex flex-col gap-5 p-1">
            <TextField label="Name"
                       value={editing.name}
                       onChange={(e) =>
                         setEditing((prev) => prev ? { ...prev, name: e.target.value } : prev)
                       } />
            <label className="block">
              <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
                Body
              </span>
              <textarea rows={14}
                        value={editing.body}
                        onChange={(e) =>
                          setEditing((prev) => prev ? { ...prev, body: e.target.value } : prev)
                        }
                        className="w-full bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[13px] text-[var(--color-foreground)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 transition-shadow font-mono leading-relaxed" />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setEditing(null)} disabled={saving}>
                Cancel
              </Button>
              <Button variant="primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  )
}


function GeminiModelPicker({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const t = useStudioT()
  const [models, setModels] = useState<GeminiModel[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchOnce = () => {
    if (models || loading) return
    setLoading(true)
    setError(null)
    api
      .geminiModels()
      .then((rows: unknown) => {
        // The project's fetch wrapper doesn't reject on non-2xx — it just
        // returns the parsed body. On 502 that's {detail: "..."}, not a list.
        if (!Array.isArray(rows)) throw new Error('not an array')
        setModels(rows as GeminiModel[])
      })
      .catch(() => setError(t('settings.captionModel.loadFailed')))
      .finally(() => setLoading(false))
  }

  const ga = models?.filter((m) => m.status === 'GA') ?? []
  const preview = models?.filter((m) => m.status === 'Preview') ?? []
  const known = models ? new Set(models.map((m) => m.id)) : null
  // Keep the saved value selectable even if it's not in the latest list
  // (e.g. user picked a model that's since been deprecated).
  const showCurrentFallback = value && (!known || !known.has(value))

  const label = t('settings.meta.gemini_model.label')
  const hint = loading
    ? t('settings.captionModel.loading')
    : error
      ? error
      : t('settings.meta.gemini_model.hint')

  return (
    <label className="block">
      <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onMouseDown={fetchOnce}
        onFocus={fetchOnce}
        className="w-full bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[14px] text-[var(--color-foreground)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 transition-shadow"
      >
        {!value ? (
          <option value="">{t('settings.captionModel.useDefault')}</option>
        ) : null}
        {showCurrentFallback ? <option value={value}>{value}</option> : null}
        {ga.length > 0 ? (
          <optgroup label={t('settings.captionModel.groupGA')}>
            {ga.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </optgroup>
        ) : null}
        {preview.length > 0 ? (
          <optgroup label={t('settings.captionModel.groupPreview')}>
            {preview.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </optgroup>
        ) : null}
      </select>
      <span className="block mt-1.5 text-[12px] text-[var(--color-muted-foreground)]">{hint}</span>
    </label>
  )
}

function CommentPromptsSection({ onToast }: { onToast: (t: ToastState) => void }) {
  const [prompts, setPrompts] = useState<CommentPrompt[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<
    { id: number | null; name: string; body: string } | null
  >(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.commentPrompts()
      .then(setPrompts)
      .catch(() => onToast({ msg: 'Failed to load comment prompts', tone: 'red' }))
      .finally(() => setLoading(false))
  }, [onToast])
  useEffect(() => {
    const id = window.setTimeout(() => {
      load()
    }, 0)
    return () => window.clearTimeout(id)
  }, [load])

  const save = async () => {
    if (!editing) return
    const name = editing.name.trim()
    const body = editing.body.trim()
    if (!name || !body) {
      onToast({ msg: 'Name and body are required', tone: 'red' })
      return
    }
    setSaving(true)
    try {
      if (editing.id == null) {
        await api.createCommentPrompt({ name, body })
      } else {
        await api.updateCommentPrompt(editing.id, { name, body })
      }
      setEditing(null)
      load()
      onToast({ msg: 'Comment prompt saved', tone: 'green' })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      onToast({ msg, tone: 'red' })
    } finally {
      setSaving(false)
    }
  }

  const remove = async (p: CommentPrompt) => {
    if (!confirm(`Delete comment prompt "${p.name}"? Inflight comment runs using it must finish first.`)) return
    try {
      await api.deleteCommentPrompt(p.id)
      load()
      onToast({ msg: 'Comment prompt deleted', tone: 'green' })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Delete failed'
      onToast({ msg, tone: 'red' })
    }
  }

  return (
    <section id="comment-prompts">
      <SectionTitle>Comment prompts</SectionTitle>
      <Card className="p-6 flex flex-col gap-4">
        <p className="text-[12.5px] text-[var(--color-muted-foreground)] leading-relaxed">
          The body is sent verbatim to Gemini for each AI-generated comment.
          The target TikTok video&apos;s title is appended to the end of the
          body as <span className="font-mono text-[12px]">Video title: …</span>
          {' '}automatically — you don&apos;t write any placeholder for it.
          Edits take effect on the next worker pickup for queued runs that
          already reference the prompt.
        </p>
        {loading ? (
          <p className="text-[13px] text-[var(--color-muted-foreground)]">Loading…</p>
        ) : prompts.length === 0 ? (
          <p className="text-[13px] text-[var(--color-muted-foreground)]">No comment prompts yet.</p>
        ) : (
          <div className="flex flex-col divide-y divide-[var(--color-border)]">
            {prompts.map((p) => (
              <div key={p.id} className="py-3 flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-medium text-[var(--color-foreground)]">{p.name}</div>
                  <div className="text-[12px] text-[var(--color-muted-foreground)] mt-0.5 line-clamp-2 font-mono leading-relaxed">
                    {p.body.slice(0, 200)}{p.body.length > 200 ? '…' : ''}
                  </div>
                </div>
                <Button variant="ghost" size="sm"
                        onClick={() => setEditing({ id: p.id, name: p.name, body: p.body })}>
                  Edit
                </Button>
                <Button variant="ghost" size="sm" onClick={() => remove(p)}>
                  Delete
                </Button>
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-end">
          <Button variant="primary"
                  onClick={() => setEditing({ id: null, name: '', body: '' })}>
            + New prompt
          </Button>
        </div>
      </Card>

      {editing && (
        <Modal open onClose={() => setEditing(null)}
               title={editing.id == null ? 'New comment prompt' : 'Edit comment prompt'}>
          <div className="flex flex-col gap-5 p-1">
            <TextField label="Name"
                       value={editing.name}
                       onChange={(e) =>
                         setEditing((prev) => prev ? { ...prev, name: e.target.value } : prev)
                       } />
            <label className="block">
              <span className="block text-[12.5px] font-medium text-[var(--color-foreground-2)] mb-1.5">
                Body
              </span>
              <textarea rows={14}
                        value={editing.body}
                        onChange={(e) =>
                          setEditing((prev) => prev ? { ...prev, body: e.target.value } : prev)
                        }
                        className="w-full bg-[var(--color-card)] border border-[var(--color-border)] rounded-[8px] px-3 py-2 text-[13px] text-[var(--color-foreground)] focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20 transition-shadow font-mono leading-relaxed" />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setEditing(null)} disabled={saving}>
                Cancel
              </Button>
              <Button variant="primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  )
}
