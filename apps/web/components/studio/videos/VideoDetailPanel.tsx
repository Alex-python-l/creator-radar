'use client'

import { useEffect, useState } from 'react'
import { useLocale } from 'next-intl'
import { useStudioT } from '@/lib/studio/i18n'
import { api, API_ORIGIN, PLATFORMS, type Platform, type PublicationAttempt } from '@/lib/studio/api'
import { PlatformIcon } from '@/components/studio/PlatformIcon'
import { Badge, Button } from '@/components/ui'
import { useVideoDetail } from '@/lib/studio/useVideoDetail'
import { formatLocalDateTime, formatRelative } from '@/lib/studio/time'
import { PLATFORM_NAME } from '@/lib/studio/platformNames'

const RESULT_TONE: Record<
  string, 'green' | 'red' | 'blue' | 'orange' | 'gray'
> = {
  SUCCESS: 'green',
  FAILURE: 'red',
  INITIATED: 'blue',
  QUEUED: 'orange',
  PENDING: 'gray',
}

const SOURCE_TONE: Record<string, 'blue' | 'green' | 'red' | 'orange' | 'gray'> = {
  gemini: 'orange',
  geelark: 'red',
  server: 'blue',
  account_config: 'gray',
}

const sourceLabel = (source: string) => {
  if (source === 'gemini') return 'Gemini API'
  if (source === 'geelark') return 'GeeLark'
  if (source === 'server') return 'Server'
  if (source === 'account_config') return 'Account setup'
  return source
}

type Props = {
  videoId: number
  onOpenFailureShot: (
    url: string, platform: string, detail: string) => void
}

export default function VideoDetailPanel({
  videoId, onOpenFailureShot,
}: Props) {
  const t = useStudioT()
  const locale = useLocale()
  const { detail, loading, error, retry } = useVideoDetail(videoId)
  const [attemptsByPub, setAttemptsByPub] =
    useState<Record<number, PublicationAttempt[]>>({})
  const [retryingPubId, setRetryingPubId] = useState<number | null>(null)
  const resultLabel = (r: string) =>
    t(`videos.result.${r}`, { defaultValue: r })

  useEffect(() => {
    if (!detail) return
    const idsToFetch = detail.publications
      .filter((p) => p.result === 'FAILURE' || p.retry_count > 0)
      .filter((p) => !attemptsByPub[p.id])
      .map((p) => p.id)
    if (!idsToFetch.length) return
    let cancelled = false
    Promise.all(idsToFetch.map(async (id) => {
      try {
        return { id, attempts: await api.publicationAttempts(id) }
      } catch {
        return null
      }
    })).then((rows) => {
      if (cancelled) return
      setAttemptsByPub((prev) => {
        const next = { ...prev }
        for (const row of rows) {
          if (row) next[row.id] = row.attempts
        }
        return next
      })
    })
    return () => {
      cancelled = true
    }
  }, [detail, attemptsByPub])

  const retryPublication = async (pubId: number) => {
    if (retryingPubId != null) return
    setRetryingPubId(pubId)
    try {
      await api.retryPublication(pubId)
      setAttemptsByPub((prev) => {
        const next = { ...prev }
        delete next[pubId]
        return next
      })
      retry()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Retry failed')
    } finally {
      setRetryingPubId(null)
    }
  }

  if (loading || (!detail && !error)) {
    return (
      <div className="px-4 py-3 bg-[var(--color-muted)] text-[12.5px] text-[var(--color-muted-foreground)]">
        {t('videos.detail.loading', { defaultValue: '加载中…' })}
      </div>
    )
  }
  if (error) {
    return (
      <div className="px-4 py-3 bg-[var(--color-muted)] text-[12.5px] text-[var(--color-destructive)] flex items-center gap-2">
        <span>
          {t('videos.detail.error', { defaultValue: '详情加载失败' })}
        </span>
        <button
          type="button"
          onClick={retry}
          className="underline text-[var(--color-blue)]"
        >
          {t('common.retry', { defaultValue: '重试' })}
        </button>
      </div>
    )
  }
  if (!detail) return null

  const pubs = detail.publications
  const byPlat = new Map(pubs.map((p) => [p.platform, p]))

  return (
    <div className="px-4 py-3 bg-[var(--color-muted)]">
      <div className="flex flex-col gap-2 min-w-[720px]">
        {PLATFORMS.map((plat: Platform) => {
          const p = byPlat.get(plat) ?? null
          const muted = !p || (p.result === 'PENDING' && p.account_id == null)
          const displayResult = p?.result ?? 'PENDING'
          const attempts = p ? attemptsByPub[p.id] ?? [] : []
          const latestAttempt = attempts.length ? attempts[attempts.length - 1] : null
          const diagnosticDetail =
            p && (p.result === 'FAILURE' || p.retry_count > 0)
            ? `${p.notes ?? ''}${p.fail_code ? ` (${p.fail_code})` : ''}`
            : ''
          const nextRetryAt =
            p?.result === 'PENDING' && p.retry_count > 0
              ? p.next_retry_at
              : null
          const phoneLabel = p?.account_profile_name
            ?? (p?.account_id != null
              ? `#${p.account_id}`
              : t('videos.auto') as string)
          return (
            <div
              key={plat}
              className="grid grid-cols-[150px_104px_120px_130px_minmax(0,1fr)] gap-x-4 gap-y-1 items-center text-[12.5px]"
            >
              <div className="inline-flex items-center gap-2 text-[var(--color-foreground-2)]">
                <PlatformIcon
                  platform={plat} size={15} mono={muted}
                  className={
                    muted ? 'text-[var(--color-muted-foreground-2)]' : undefined}
                />
                {PLATFORM_NAME[plat]}
              </div>
              <div>
                <Badge tone={RESULT_TONE[displayResult] ?? 'gray'}>
                  {resultLabel(displayResult)}
                </Badge>
              </div>
              <div className="text-[var(--color-muted-foreground)] tabular-nums whitespace-nowrap">
                <span
                  title={p?.posted_at
                    ? formatLocalDateTime(p.posted_at) : undefined}
                >
                  {formatRelative(p?.posted_at ?? null, locale)}
                </span>
              </div>
              <div className="text-[var(--color-muted-foreground)] truncate">
                {phoneLabel}
              </div>
              <div className="min-w-0 text-[var(--color-muted-foreground)] truncate">
                {latestAttempt ? (
                  <Badge
                    tone={SOURCE_TONE[latestAttempt.source] ?? 'gray'}
                    className="mr-2"
                  >
                    {sourceLabel(latestAttempt.source)}
                  </Badge>
                ) : null}
                {diagnosticDetail ? <span>{diagnosticDetail}</span> : null}
                {p?.fail_hint && (
                  <div className="text-[11.5px] text-[var(--color-orange)] mt-0.5 italic">
                    {p.fail_hint}
                  </div>
                )}
                {nextRetryAt ? (
                  <span className="ml-2 text-[var(--color-muted-foreground-2)]">
                    {t('videos.retryScheduled', {
                      defaultValue: 'Retry scheduled {{time}}',
                      time: formatRelative(nextRetryAt, locale),
                    })}
                  </span>
                ) : null}
                {p?.fail_screenshot ? (
                  <button
                    type="button"
                    onClick={() => onOpenFailureShot(
                      `${API_ORIGIN}/api/videos/publications/${p.id}/screenshot`,
                      PLATFORM_NAME[plat],
                      diagnosticDetail,
                    )}
                    className="text-[var(--color-blue)] underline ml-2"
                  >
                    {t('videos.viewFailureShot')}
                  </button>
                ) : null}
                {p?.result === 'FAILURE' ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-2 h-6 px-2"
                    disabled={retryingPubId === p.id}
                    onClick={() => retryPublication(p.id)}
                  >
                    {retryingPubId === p.id
                      ? t('videos.retryingPublication', { defaultValue: 'Retrying' })
                      : t('videos.retryPublication', { defaultValue: 'Retry' })}
                  </Button>
                ) : null}
                {attempts.length ? (
                  <div className="mt-1 truncate text-[11.5px] text-[var(--color-muted-foreground-2)]">
                    {attempts.map((a) =>
                      `#${a.attempt_no} ${sourceLabel(a.source)} ${a.stage}: ${a.message}`,
                    ).join(' | ')}
                  </div>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
