'use client'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useStudioT } from '@/lib/studio/i18n'
import { RefreshCw, UploadCloud } from 'lucide-react'
import { api, API_ORIGIN, AUTH_EXPIRED_EVENT } from '@/lib/studio/api'
import { PLATFORMS, type Platform } from '@/lib/studio/api'
import { PlatformIcon } from '@/components/studio/PlatformIcon'
import { PLATFORM_NAME } from '@/lib/studio/platformNames'
import { useStudioToast } from '@/components/studio/studio-providers'
import {
  Button,
  Card,
  EmptyState,
  FilterBar,
  Modal,
  PageHeader,
  ProgressBar,
  SectionTitle,
  TextField,
} from '@/components/ui'
import { cn } from '@/lib/studio/cn'
import {
  createFailureScreenshotPreview,
  type FailureScreenshotPreview,
} from '@/lib/studio/failureScreenshotPreview'
import { VideoListView } from '@/components/studio/videos/VideoListView'
import { VideoGridView } from '@/components/studio/videos/VideoGridView'
import { ViewSwitcher } from '@/components/studio/videos/ViewSwitcher'
import { FilterChip } from '@/components/studio/videos/FilterChip'
import { useVideoView } from '@/lib/studio/useVideoView'
import VideoDetailPanel from '@/components/studio/videos/VideoDetailPanel'
import { useVideoPages } from '@/lib/studio/useVideoPages'
import { useVideoFilter } from '@/lib/studio/useVideoFilter'
import { useExpandedVideos } from '@/lib/studio/useExpandedVideos'
import { useScrollRestore } from '@/lib/studio/useScrollRestore'
import { computeNextSelection, type SelectionModifiers } from '@/lib/studio/videoSelection'

type ImportState = {
  state: string
  total: number
  done: number
  skipped: number
  failed: number
  current: string | null
  errors: string[]
}

type UploadProgressState = {
  loaded: number
  total: number
  percent: number
  count: number
}

type DriveFile = { id: string; name: string; already_imported: boolean }

const formatUploadBytes = (bytes: number) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1
  return `${value.toFixed(digits)} ${units[unit]}`
}

export default function Videos() {
  const onToast = useStudioToast()
  const t = useStudioT()
  const { filter, filterKey, setFilter } = useVideoFilter()
  const {
    items, hasMore, loading, error,
    loadMore, refresh, removeLocally, summaryCounts,
  } = useVideoPages(filter)
  const [view, setView] = useVideoView()
  const [detailVideoId, setDetailVideoId] = useState<number | null>(null)
  const { expandedIds: expandedVideos, toggle: toggleExpanded, collapseAll } =
    useExpandedVideos(filterKey)
  useScrollRestore(filterKey, items.length > 0)

  const [groups, setGroups] = useState<{ id: number; name: string }[]>([])
  const groupsById = useMemo(() => {
    const m = new Map<number, string>()
    for (const g of groups) m.set(g.id, g.name)
    return m
  }, [groups])

  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] =
    useState<UploadProgressState | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importUrl, setImportUrl] = useState('')
  const [importStep, setImportStep] =
    useState<'choose' | 'url' | 'pick'>('choose')
  const [importFiles, setImportFiles] = useState<DriveFile[]>([])
  const [importSel, setImportSel] = useState<Set<string>>(new Set())
  const [importBusy, setImportBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importProg, setImportProg] = useState<ImportState | null>(null)
  const [importGroup, setImportGroup] = useState<number | ''>('')
  const [importPlatforms, setImportPlatforms] = useState<Set<string>>(
    () => new Set(PLATFORMS),
  )
  const toggleImportPlatform = (plat: string) =>
    setImportPlatforms((prev) => {
      const next = new Set(prev)
      if (next.has(plat)) next.delete(plat)
      else next.add(plat)
      return next
    })
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const anchorIdRef = useRef<number | null>(null)
  const [assignTarget, setAssignTarget] = useState<number | ''>('')
  const [assignBusy, setAssignBusy] = useState(false)
  const [failureShot, setFailureShot] =
    useState<FailureScreenshotPreview | null>(null)

  useEffect(() => {
    const id = window.setTimeout(() => {
      setSelected(new Set())
      anchorIdRef.current = null
    }, 0)
    return () => window.clearTimeout(id)
  }, [filter.group_id, filter.status])

  useEffect(() => {
    fetch(API_ORIGIN + '/api/groups')
      .then((r) => (r.ok ? r.json() : []))
      .then(setGroups)
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!importing) return
    const tick = () =>
      api.importStatus()
        .then((s: ImportState) => {
          setImportProg(s)
          if (s.state === 'done' || s.state === 'error') {
            setImporting(false)
            onToast(s.state === 'done'
              ? {
                  msg: t('videos.toast.driveImportedSummary', {
                    done: s.done, skipped: s.skipped, failed: s.failed,
                  }),
                  tone: s.failed ? 'orange' : 'green',
                }
              : { msg: t('videos.toast.driveImportFailed'), tone: 'red' })
            refresh()
          }
        })
        .catch(() => {})
    tick()
    const timer = setInterval(tick, 2000)
    return () => clearInterval(timer)
  }, [importing, refresh, onToast, t])

  const openImport = () => {
    setImportStep('choose')
    setImportUrl('')
    setImportFiles([])
    setImportSel(new Set())
    setImportGroup('')
    setImportPlatforms(new Set(PLATFORMS))
    setImportOpen(true)
  }

  const importSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (importBusy) return
    if (importStep === 'url') {
      const url = importUrl.trim()
      if (!url) return
      setImportBusy(true)
      try {
        const r = await api.importList(url)
        if (Array.isArray(r?.files)) {
          setImportFiles(r.files)
          setImportSel(new Set(
            r.files.filter((f: DriveFile) => !f.already_imported)
              .map((f: DriveFile) => f.id)))
          setImportStep('pick')
        } else {
          onToast({
            msg: r?.detail || t('videos.toast.couldNotList'), tone: 'red' })
        }
      } catch {
        onToast({ msg: t('videos.toast.couldNotList'), tone: 'red' })
      } finally {
        setImportBusy(false)
      }
      return
    }
    const chosen = importFiles.filter((f) => importSel.has(f.id))
    if (chosen.length === 0) return
    setImportBusy(true)
    try {
      const r = await api.importDrive(
        chosen.map((f) => ({ id: f.id, name: f.name })),
        importGroup === '' ? null : importGroup,
        Array.from(importPlatforms))
      if (r?.started) {
        setImportOpen(false)
        setImportProg(null)
        setImporting(true)
      } else {
        onToast({
          msg: r?.detail || t('videos.toast.couldNotStart'), tone: 'red' })
      }
    } catch {
      onToast({ msg: t('videos.toast.couldNotStart'), tone: 'red' })
    } finally {
      setImportBusy(false)
    }
  }

  const toggleImportFile = (id: string) =>
    setImportSel((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })

  const upload = async (files: FileList | File[]) => {
    if (!files || (files as FileList).length === 0) return
    const fileArray = Array.from(files as ArrayLike<File>)
    setUploading(true)
    const totalBytes = fileArray.reduce((sum, f) => sum + f.size, 0)
    setUploadProgress({
      loaded: 0, total: totalBytes, percent: 0, count: fileArray.length,
    })
    const fd = new FormData()
    fileArray.forEach((f) => fd.append('files', f))
    if (importGroup !== '') fd.append('group_id', String(importGroup))
    fd.append('platforms', Array.from(importPlatforms).join(','))
    try {
      const j = await new Promise<{ uploaded?: number }>((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('POST', API_ORIGIN + '/api/videos/upload')
        xhr.withCredentials = true
        xhr.upload.onprogress = (event) => {
          const tot = event.lengthComputable ? event.total : totalBytes
          const loaded = event.lengthComputable
            ? event.loaded
            : Math.min(event.loaded || 0, totalBytes)
          const percent = tot > 0
            ? Math.min(100, Math.round((loaded / tot) * 100)) : 0
          setUploadProgress({
            loaded, total: tot, percent, count: fileArray.length,
          })
        }
        xhr.upload.onload = () => {
          setUploadProgress((prev) => prev
            ? { ...prev, loaded: prev.total,
                percent: prev.total > 0 ? 100 : prev.percent }
            : prev)
        }
        xhr.onload = () => {
          if (xhr.status === 401) {
            window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
            reject(new Error('unauthorized')); return
          }
          if (xhr.status < 200 || xhr.status >= 300) {
            reject(new Error(xhr.responseText || `HTTP ${xhr.status}`))
            return
          }
          setUploadProgress((prev) => prev
            ? { ...prev, loaded: prev.total, percent: 100 } : prev)
          try { resolve(JSON.parse(xhr.responseText || '{}')) }
          catch (e) { reject(e) }
        }
        xhr.onerror = () => reject(new Error('network error'))
        xhr.onabort = () => reject(new Error('upload aborted'))
        xhr.send(fd)
      })
      onToast({
        msg: t('videos.toast.uploaded', { count: j.uploaded ?? 0 }),
        tone: 'green',
      })
      refresh()
    } catch {
      onToast({ msg: t('videos.toast.uploadFailed'), tone: 'red' })
    } finally {
      setUploading(false)
      setUploadProgress(null)
    }
  }

  const handleRowClick = (id: number, mods: SelectionModifiers) => {
    setSelected((prev) => {
      const { selected: next, nextAnchorId } = computeNextSelection(
        items,
        id,
        anchorIdRef.current,
        mods,
        prev,
        (v) => v.summary === 'PENDING',
      )
      anchorIdRef.current = nextAnchorId
      return next
    })
  }

  const confirmAssign = async () => {
    if (assignTarget === '' || selected.size === 0) return
    setAssignBusy(true)
    try {
      const r = await api.assignVideos(
        Array.from(selected), assignTarget as number)
      onToast({
        msg: t('videos.toast.assigned', { count: r?.assigned ?? 0 }),
        tone: 'green',
      })
      setSelected(new Set())
      anchorIdRef.current = null
      setAssignTarget('')
      refresh()
    } catch {
      onToast({ msg: t('videos.toast.assignFailed'), tone: 'red' })
    } finally {
      setAssignBusy(false)
    }
  }

  const removeVideo = async (id: number) => {
    try {
      await api.deleteVideo(id)
      removeLocally(id)
      onToast({ msg: t('videos.toast.removed'), tone: 'orange' })
    } catch {
      onToast({ msg: t('videos.toast.deleteFailed'), tone: 'red' })
    }
  }

  const openFailureShot = useCallback(
    (url: string, platform: string, detail: string) => {
      const preview = createFailureScreenshotPreview({
        url, platform, detail })
      if (preview) setFailureShot(preview)
    }, [])

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow={t('videos.eyebrow')}
        title={t('videos.title')}
        actions={
          <>
            <ViewSwitcher value={view} onChange={setView} />
            <Button
              variant="primary"
              onClick={openImport}
              disabled={uploading}
            >
              <UploadCloud size={14} />
              {uploading
                ? t('videos.upload.uploading', 'Uploading…')
                : t('videos.import.button', 'Import videos')}
            </Button>
            <Button variant="secondary" onClick={refresh}>
              <RefreshCw size={14} />
              {t('common.refresh', { defaultValue: '刷新' })}
            </Button>
          </>
        }
      />
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="video/*"
        className="hidden"
        onChange={(e) => {
          if (e.target.files) upload(e.target.files)
          e.target.value = ''
        }}
      />

      {groups.length > 0 && (
        <div className="flex flex-col rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-card)] shadow-[var(--shadow-card-sm)] divide-y divide-[var(--color-border)]">
          <FilterBar className="rounded-none border-0 bg-transparent shadow-none">
            <span className="text-[12px] font-medium uppercase tracking-[0.06em] text-[var(--color-muted-foreground-2)] mr-1">
              {t('videos.filterGroupLabel')}
            </span>
            <FilterChip
              active={filter.group_id === 'all'}
              tone="neutral"
              onClick={() => setFilter({ group_id: 'all' })}
              label={t('common.all')}
            />
            <FilterChip
              active={filter.group_id === 'ungrouped'}
              tone="neutral"
              onClick={() => setFilter({ group_id: 'ungrouped' })}
              label={t('videos.ungrouped')}
            />
            {groups.map((g) => (
              <FilterChip
                key={g.id}
                active={filter.group_id === g.id}
                tone="neutral"
                onClick={() => setFilter({ group_id: g.id })}
                label={g.name}
              />
            ))}
          </FilterBar>
          <FilterBar className="rounded-none border-0 bg-transparent shadow-none">
            <span className="text-[12px] font-medium uppercase tracking-[0.06em] text-[var(--color-muted-foreground-2)] mr-1">
              {t('videos.filterStatusLabel')}
            </span>
            <FilterChip
              active={filter.status === 'all'}
              tone="neutral"
              onClick={() => setFilter({ status: 'all' })}
              label={t('videos.bucket.all', { defaultValue: '全部' })}
              count={summaryCounts?.total}
            />
            <FilterChip
              active={filter.status === 'pending'}
              tone="pending"
              onClick={() => setFilter({ status: 'pending' })}
              glyph="○"
              label={t('videos.bucket.pending', { defaultValue: '待处理' })}
              count={summaryCounts?.pending}
            />
            <FilterChip
              active={filter.status === 'in_progress'}
              tone="in_progress"
              onClick={() => setFilter({ status: 'in_progress' })}
              glyph="⟳"
              label={t('videos.bucket.in_progress', { defaultValue: '进行中' })}
              count={summaryCounts?.in_progress}
            />
            <FilterChip
              active={filter.status === 'handled'}
              tone="handled"
              onClick={() => setFilter({ status: 'handled' })}
              glyph="✓"
              label={t('videos.bucket.handled', { defaultValue: '已处理' })}
              count={summaryCounts?.handled}
            />
          </FilterBar>
        </div>
      )}

      {expandedVideos.size > 0 && (
        <div className="flex">
          <button
            type="button"
            onClick={collapseAll}
            className="h-7 px-3 rounded-full text-[12.5px] font-medium bg-[var(--color-gray-bg)] text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:bg-[#e6e6ea] transition-colors"
          >
            {t('videos.collapseAll', { defaultValue: '全部收起 ({{n}})', n: expandedVideos.size })}
          </button>
        </div>
      )}

      {importProg && (importing || importProg.state !== 'idle') && (
        <section>
          <SectionTitle>{t('videos.driveImport')}</SectionTitle>
          <Card className="p-4 flex flex-col gap-3">
            <ProgressBar value={importProg.done} total={importProg.total} />
            <div className="text-[12.5px] text-[var(--color-muted-foreground)]">
              {importing
                ? (importProg.current
                    ? t('videos.downloadingCurrent', {
                        name: importProg.current })
                    : t('videos.listingFolder'))
                : t('videos.driveDone', {
                    done: importProg.done,
                    skipped: importProg.skipped,
                    failed: importProg.failed,
                  })}
            </div>
            {importProg.errors.length > 0 && (
              <div className="text-[12px] text-[var(--color-destructive)] flex flex-col gap-0.5">
                {importProg.errors.slice(0, 5).map((er, i) => (
                  <div key={i}>{er}</div>
                ))}
              </div>
            )}
          </Card>
        </section>
      )}

      {uploadProgress && (
        <section>
          <SectionTitle>{t('videos.uploadProgress.title')}</SectionTitle>
          <Card className="p-4 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[12.5px] text-[var(--color-foreground-2)]">
                {t('videos.uploadProgress.detail', {
                  count: uploadProgress.count })}
              </span>
              <span className="text-[12.5px] tabular-nums text-[var(--color-muted-foreground)]">
                {uploadProgress.percent}%
              </span>
            </div>
            <div className="relative h-1.5 rounded-full bg-[var(--color-gray-bg)] overflow-hidden">
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-[var(--color-primary)] transition-[width]"
                style={{ width: `${uploadProgress.percent}%` }}
              />
            </div>
            <div className="text-[12px] text-[var(--color-muted-foreground)] tabular-nums">
              {t('videos.uploadProgress.bytes', {
                loaded: formatUploadBytes(uploadProgress.loaded),
                total: formatUploadBytes(uploadProgress.total),
              })}
            </div>
          </Card>
        </section>
      )}

      <section>
        {selected.size > 0 && (
          <div className="sticky top-0 z-20 px-3.5 py-2.5 flex items-center gap-3 flex-wrap mb-3 bg-[var(--color-blue-bg)] border border-[#d2e0f2] rounded-[10px] shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
            <span className="text-[12.5px] text-[var(--color-foreground-2)]">
              {t('videos.assignSelected', { count: selected.size })}
            </span>
            <select
              value={assignTarget}
              onChange={(e) => setAssignTarget(
                e.target.value ? parseInt(e.target.value) : '')}
              className="border border-[var(--color-border)] rounded-[6px] px-2 py-1 text-sm bg-[var(--color-card)]"
            >
              <option value="">{t('videos.assignPickGroup')}</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
            <Button
              variant="primary"
              size="sm"
              onClick={confirmAssign}
              disabled={assignBusy || assignTarget === ''}
            >
              {assignBusy
                ? t('videos.assigning')
                : t('videos.confirmAssign')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSelected(new Set())
                anchorIdRef.current = null
              }}
            >
              {t('videos.clearSelection', { defaultValue: '取消选择' })}
            </Button>
            <span className="text-[11.5px] text-[var(--color-muted-foreground)] ml-auto">
              {t('videos.autoPublishHint')}
            </span>
          </div>
        )}

        {error && items.length === 0 && (
          <Card className="p-6 text-center">
            <div className="text-[var(--color-destructive)] mb-2">
              {t('videos.loadError', {
                defaultValue: '加载失败' })}
            </div>
            <Button variant="secondary" onClick={refresh}>
              {t('common.retry', { defaultValue: '重试' })}
            </Button>
          </Card>
        )}

        {!error && items.length === 0 && !loading ? (
          <Card>
            <EmptyState title={t('videos.emptyVideosTitle')}>
              {t('videos.emptyVideosBody')}
            </EmptyState>
          </Card>
        ) : items.length > 0 ? (
          view === 'list' ? (
            <VideoListView
              items={items}
              hasMore={hasMore}
              loading={loading}
              expandedIds={expandedVideos}
              selectedIds={selected}
              groupsById={groupsById}
              onRowClick={handleRowClick}
              onToggleExpanded={toggleExpanded}
              onDelete={removeVideo}
              onLoadMore={loadMore}
              onOpenFailureShot={openFailureShot}
            />
          ) : (
            <VideoGridView
              items={items}
              hasMore={hasMore}
              loading={loading}
              selectedIds={selected}
              groupsById={groupsById}
              onCardClick={handleRowClick}
              onDelete={removeVideo}
              onOpenDetail={(id) => setDetailVideoId(id)}
              onLoadMore={loadMore}
            />
          )
        ) : null}
      </section>

      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title={importStep === 'choose'
          ? t('videos.import.button', '导入视频')
          : t('videos.modal.title')}
        onSubmit={importStep === 'choose' ? undefined : importSubmit}
        submitDisabled={importStep === 'pick' && importSel.size === 0}
        submitLabel={importBusy
          ? t('videos.modal.working')
          : importStep === 'url'
            ? t('videos.modal.listVideos')
            : t('videos.modal.importN', {
                count: importFiles.filter((f) =>
                  importSel.has(f.id)).length,
              })}
      >
        {importStep === 'choose' ? (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <label className="text-[13px] text-[var(--color-foreground-2)] font-medium shrink-0">
                {t('videos.upload.pick_group')}
              </label>
              <select
                value={importGroup}
                onChange={(e) => setImportGroup(
                  e.target.value ? parseInt(e.target.value) : '')}
                className="flex-1 border border-[var(--color-border)] rounded-[6px] px-2 py-1 text-sm bg-[var(--color-card)]"
              >
                <option value="">{t('videos.upload.pick_group')}</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <label className="text-[13px] text-[var(--color-foreground-2)] font-medium shrink-0">
                {t('videos.import.platformsLabel', { defaultValue: '发布平台' })}
              </label>
              {PLATFORMS.map((plat: Platform) => {
                const active = importPlatforms.has(plat)
                return (
                  <button
                    key={plat}
                    type="button"
                    onClick={() => toggleImportPlatform(plat)}
                    className={
                      'inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[12.5px] font-medium transition-colors ' +
                      (active
                        ? 'bg-[var(--color-muted)] text-[var(--color-foreground)]'
                        : 'bg-[var(--color-gray-bg)] text-[var(--color-muted-foreground-2)] hover:bg-[#e6e6ea]')
                    }
                  >
                    <span className={active ? '' : 'opacity-40 grayscale'}>
                      <PlatformIcon platform={plat} size={14} />
                    </span>
                    {PLATFORM_NAME[plat]}
                  </button>
                )
              })}
            </div>
            {importPlatforms.size === 0 && (
              <div className="text-[12px] text-[var(--color-destructive)]">
                {t('videos.import.platformsEmpty', {
                  defaultValue: '至少选择一个平台' })}
              </div>
            )}
            <button
              type="button"
              disabled={importPlatforms.size === 0}
              onClick={() => {
                setImportOpen(false)
                inputRef.current?.click()
              }}
              className="flex items-center gap-3 p-4 rounded-[10px] border border-[var(--color-border)] hover:border-[var(--color-blue)] hover:bg-[var(--color-blue-bg)] text-left transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className="w-9 h-9 rounded-full bg-[var(--color-blue-bg)] text-[var(--color-blue)] flex items-center justify-center shrink-0">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M12 16V4M12 4l-4 4M12 4l4 4M5 20h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="text-[14px] font-medium text-[var(--color-foreground)]">
                  {t('videos.import.localTitle', '本地上传')}
                </div>
                <div className="text-[12.5px] text-[var(--color-muted-foreground)]">
                  {t('videos.import.localHint', '从这台电脑选择视频文件')}
                </div>
              </div>
            </button>
            <button
              type="button"
              disabled={importPlatforms.size === 0}
              onClick={() => setImportStep('url')}
              className="flex items-center gap-3 p-4 rounded-[10px] border border-[var(--color-border)] hover:border-[var(--color-blue)] hover:bg-[var(--color-blue-bg)] text-left transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className="w-9 h-9 rounded-full bg-[var(--color-blue-bg)] text-[var(--color-blue)] flex items-center justify-center shrink-0">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M4 4h10l6 6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                  <path d="M14 4v6h6" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="text-[14px] font-medium text-[var(--color-foreground)]">
                  {t('videos.import.driveTitle', '从 Google Drive 导入')}
                </div>
                <div className="text-[12.5px] text-[var(--color-muted-foreground)]">
                  {t('videos.import.driveHint', '粘贴 Drive 文件夹链接')}
                </div>
              </div>
            </button>
          </div>
        ) : importStep === 'url' ? (
          <TextField
            label={t('videos.modal.folderLink')}
            placeholder={t('videos.modal.folderPlaceholder')}
            hint={t('videos.modal.folderHint')}
            value={importUrl}
            onChange={(e) => setImportUrl(e.target.value)}
            autoFocus
          />
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <label className="text-[13px] text-[var(--color-foreground-2)] font-medium shrink-0">
                {t('videos.upload.pick_group')}
              </label>
              <select
                value={importGroup}
                onChange={(e) => setImportGroup(
                  e.target.value ? parseInt(e.target.value) : '')}
                className="border border-[var(--color-border)] rounded-[6px] px-2 py-1 text-sm bg-[var(--color-card)]"
              >
                <option value="">{t('videos.upload.pick_group')}</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5 max-h-[280px] overflow-auto">
              {importFiles.length === 0 ? (
                <div className="text-[13px] text-[var(--color-muted-foreground)]">
                  {t('videos.modal.noVideos')}
                </div>
              ) : (
                importFiles.map((f) => (
                  <label
                    key={f.id}
                    className={cn(
                      'flex items-center gap-2.5 px-2 py-1.5 rounded-[6px] text-[13.5px]',
                      f.already_imported
                        ? 'text-[var(--color-muted-foreground-2)]'
                        : 'text-[var(--color-foreground)] hover:bg-[var(--color-gray-bg)] cursor-pointer',
                    )}
                  >
                    <input
                      type="checkbox"
                      disabled={f.already_imported}
                      checked={importSel.has(f.id)}
                      onChange={() => toggleImportFile(f.id)}
                    />
                    <span className="truncate">{f.name}</span>
                    {f.already_imported ? (
                      <span className="text-[11.5px] text-[var(--color-muted-foreground-2)] ml-auto">
                        {t('videos.modal.alreadyImported')}
                      </span>
                    ) : null}
                  </label>
                ))
              )}
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={failureShot !== null}
        onClose={() => setFailureShot(null)}
        title={failureShot
          ? t('videos.failureShotTitle', { platform: failureShot.platform })
          : t('videos.viewFailureShot')}
      >
        {failureShot ? (
          <div className="flex flex-col gap-3">
            {failureShot.detail ? (
              <div className="text-[12.5px] text-[var(--color-muted-foreground)] break-words">
                {failureShot.detail}
              </div>
            ) : null}
            <div className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-muted)] p-2">
              <img
                src={failureShot.url}
                alt={t('videos.failureShotAlt', {
                  platform: failureShot.platform })}
                className="max-h-[72vh] w-full object-contain rounded-[6px]"
              />
            </div>
            <a
              href={failureShot.url}
              target="_blank"
              rel="noreferrer"
              className="self-start text-[12.5px] text-[var(--color-blue)] underline"
            >
              {t('videos.openOriginalShot')}
            </a>
          </div>
        ) : null}
      </Modal>

      <Modal
        open={detailVideoId !== null}
        onClose={() => setDetailVideoId(null)}
        title={t('videos.detailTitle', { defaultValue: '视频详情' })}
      >
        {detailVideoId !== null && (
          <VideoDetailPanel
            videoId={detailVideoId}
            onOpenFailureShot={openFailureShot}
          />
        )}
      </Modal>
    </div>
  )
}
