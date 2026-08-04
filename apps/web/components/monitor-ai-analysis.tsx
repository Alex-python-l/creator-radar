'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { ExternalLink, Sparkles } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { Badge, Button, Card } from '@/components/ui';
import type {
  AnalysisAccountRef,
  MonitorAnalysisPlatform,
  MonitorAnalysisRange,
  MonitorAnalysisSnapshot,
  PerformanceVideo,
} from '@/lib/monitor-analysis';
import { PlatformLabels } from '@svm/shared/platforms';

type AnalysisResponse = MonitorAnalysisSnapshot & {
  ai: {
    status: 'ready' | 'missing_key' | 'failed';
    narrative: null | {
      healthSummary: string;
      publishingSummary: string;
      performanceSummary: string;
    };
    recommendations: string[];
  };
};

function AccountLink({ account, locale }: { account: AnalysisAccountRef; locale: string }) {
  return (
    <Link className="font-medium text-[color:var(--color-foreground)] hover:underline" href={`/${locale}/accounts/${account.id}`}>
      {account.name}
    </Link>
  );
}

function VideoLink({ video }: { video: PerformanceVideo }) {
  const label = video.description?.trim() || `#${video.id}`;
  return video.url ? (
    <a className="inline-flex items-center gap-1 hover:underline" href={video.url} target="_blank" rel="noreferrer">
      <span className="max-w-[420px] truncate">{label}</span><ExternalLink className="h-3 w-3" />
    </a>
  ) : <span>{label}</span>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-[color:var(--color-muted)]/50 px-3 py-2">
      <div className="text-[11px] text-[color:var(--color-muted-foreground)]">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums text-[color:var(--color-foreground)]">{value}</div>
    </div>
  );
}

export function MonitorAiAnalysis({
  groupId,
  groupName,
  platform,
  locale,
  canManageSecrets,
}: {
  groupId?: number;
  groupName: string;
  platform: MonitorAnalysisPlatform;
  locale: string;
  canManageSecrets: boolean;
}) {
  const t = useTranslations('accountsPage.aiAnalysis');
  const [range, setRange] = useState<MonitorAnalysisRange>('7d');
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setResult(null);
    setError(null);
  }, [groupId, platform, locale]);

  async function generate() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/studio/api/monitor/analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ groupId, platform, range, locale: locale === 'en' ? 'en' : 'zh' }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setResult(await response.json() as AnalysisResponse);
    } catch {
      setResult(null);
      setError(t('requestFailed'));
    } finally {
      setLoading(false);
    }
  }

  const platformLabel = platform === 'all' ? t('allPlatforms') : PlatformLabels[platform];
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-[color:var(--color-primary)]" />
            <h2 className="text-sm font-semibold text-[color:var(--color-foreground)]">{t('title')}</h2>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge tone="blue">{groupName}</Badge>
            <Badge>{platformLabel}</Badge>
            <span className="text-xs leading-6 text-[color:var(--color-muted-foreground)]">{t('scopeHint')}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="sr-only" htmlFor="monitor-ai-range">{t('rangeLabel')}</label>
          <select
            id="monitor-ai-range"
            value={range}
            onChange={(event) => {
              setRange(event.target.value as MonitorAnalysisRange);
              setResult(null);
              setError(null);
            }}
            className="h-9 rounded-md border border-[color:var(--color-border)] bg-white px-3 text-sm"
          >
            <option value="today">{t('rangeToday')}</option>
            <option value="7d">{t('range7d')}</option>
            <option value="30d">{t('range30d')}</option>
          </select>
          <Button onClick={generate} disabled={loading}>
            <Sparkles className="h-4 w-4" />{loading ? t('generating') : t('generate')}
          </Button>
        </div>
      </div>

      {error ? <div className="border-t border-[color:var(--color-border)] px-5 py-4 text-sm text-[color:var(--color-destructive)]">{error}</div> : null}
      {result ? (
        <div className="space-y-6 border-t border-[color:var(--color-border)] p-5">
          {result.ai.status !== 'ready' ? (
            <div className="rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-muted)]/40 px-4 py-3 text-sm text-[color:var(--color-muted-foreground)]">
              {result.ai.status === 'missing_key' ? (
                canManageSecrets ? <>{t('missingKeyManager')} <Link className="font-semibold text-[color:var(--color-primary)] hover:underline" href={`/${locale}/studio/settings`}>{t('openSettings')}</Link></> : t('missingKeyMember')
              ) : t('aiFailed')}
            </div>
          ) : null}

          <AnalysisSection title={t('healthTitle')} narrative={result.ai.narrative?.healthSummary}>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
              <Metric label={t('healthStatus')} value={t(`healthState.${result.health.status}`)} />
              <Metric label={t('accounts')} value={result.health.totalAccounts} />
              <Metric label={t('successAttempts')} value={result.health.successAttempts} />
              <Metric label={t('failedAttempts')} value={result.health.failedAttempts} />
              <Metric label={t('successRate')} value={result.health.successRate == null ? '—' : `${Math.round(result.health.successRate * 100)}%`} />
              <Metric label={t('currentIssues')} value={result.health.currentIssues.length} />
            </div>
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              <Detail title={t('issueDetails', { count: result.health.currentIssues.length })}>
                {result.health.currentIssues.length ? result.health.currentIssues.map((item) => (
                  <div key={item.id} className="border-t border-[color:var(--color-border)]/60 py-2 first:border-0">
                    <AccountLink account={item} locale={locale} /> · {t(`reason.${item.reason}`)}
                    {item.errorCode || item.errorMessage ? <div className="mt-1 break-words text-xs text-[color:var(--color-muted-foreground)]">{item.errorCode || t('unknownError')}{item.errorMessage ? ` · ${item.errorMessage}` : ''}</div> : null}
                  </div>
                )) : <Empty text={t('none')} />}
              </Detail>
              <Detail title={t('processingRecovered', { processing: result.health.processing.length, recovered: result.health.recovered.length })}>
                {result.health.processing.map((item) => <div key={`p-${item.id}`} className="py-1"><AccountLink account={item} locale={locale} /> · {t('processing')}</div>)}
                {result.health.recovered.map((item) => <div key={`r-${item.id}`} className="py-1"><AccountLink account={item} locale={locale} /> · {t('recovered')}</div>)}
                {!result.health.processing.length && !result.health.recovered.length ? <Empty text={t('none')} /> : null}
              </Detail>
              <Detail title={t('errorBreakdown')}>
                {result.health.errorBreakdown.length ? result.health.errorBreakdown.map((item) => <div key={item.code} className="flex justify-between py-1"><span>{item.code}</span><span className="tabular-nums">{item.count}</span></div>) : <Empty text={t('none')} />}
              </Detail>
            </div>
          </AnalysisSection>

          <AnalysisSection title={t('publishingTitle')} narrative={result.ai.narrative?.publishingSummary}>
            <div className="grid grid-cols-3 gap-2">
              <Metric label={t('posted')} value={result.publishing.postedCount} />
              <Metric label={t('silent')} value={result.publishing.silentCount} />
              <Metric label={t('uncertain')} value={result.publishing.uncertainCount} />
            </div>
            <div className="mt-3 grid gap-2 lg:grid-cols-3">
              <Detail title={t('postedDetails')}>
                {result.publishing.postedAccounts.length ? result.publishing.postedAccounts.map((item) => (
                  <div key={item.id} className="border-t border-[color:var(--color-border)]/60 py-2 first:border-0">
                    <AccountLink account={item} locale={locale} /> · {t('contentCount', { count: item.count })}
                    {item.samples.map((sample) => <div key={sample.id} className="mt-1 pl-3 text-xs text-[color:var(--color-muted-foreground)]"><VideoLink video={sample} /></div>)}
                  </div>
                )) : <Empty text={t('none')} />}
              </Detail>
              <Detail title={t('silentDetails')}>
                {result.publishing.silentAccounts.length ? result.publishing.silentAccounts.map((item) => <div key={item.id} className="py-1"><AccountLink account={item} locale={locale} /></div>) : <Empty text={t('none')} />}
              </Detail>
              <Detail title={t('uncertainDetails')}>
                {result.publishing.uncertainAccounts.length ? result.publishing.uncertainAccounts.map((item) => <div key={item.id} className="py-1"><AccountLink account={item} locale={locale} /> · {t('unavailable')}</div>) : <Empty text={t('none')} />}
              </Detail>
            </div>
          </AnalysisSection>

          <AnalysisSection title={t('performanceTitle')} narrative={result.ai.narrative?.performanceSummary}>
            {result.performance.cohorts.length ? <div className="space-y-2">
              {result.performance.cohorts.map((cohort) => (
                <Detail key={`${cohort.platform}-${cohort.contentType}`} title={`${PlatformLabels[cohort.platform]} · ${cohort.contentType} · ${t(cohort.metric)}`}>
                  <p className="mb-2 text-xs text-[color:var(--color-muted-foreground)]">
                    {t('sampleCounts', { videos: cohort.videoCount, accounts: cohort.accountCount })}
                    {!cohort.enoughVideoSample || !cohort.enoughAccountSample ? ` · ${t('sampleInsufficient')}` : ''}
                  </p>
                  <Ranking title={t(cohort.enoughVideoSample ? 'topVideos' : 'rawRanking')} videos={cohort.topVideos} locale={locale} metric={cohort.metric} />
                  {cohort.enoughVideoSample ? <Ranking title={t('bottomVideos')} videos={cohort.bottomVideos} locale={locale} metric={cohort.metric} /> : null}
                  <div className="mt-3 grid grid-cols-2 gap-4">
                    <AccountRanking title={t(cohort.enoughAccountSample ? 'topAccounts' : 'rawAccountRanking')} accounts={cohort.topAccounts} locale={locale} />
                    {cohort.enoughAccountSample ? <AccountRanking title={t('bottomAccounts')} accounts={cohort.bottomAccounts} locale={locale} /> : null}
                  </div>
                </Detail>
              ))}
            </div> : <Empty text={t('noPerformanceData')} />}
          </AnalysisSection>

          {result.ai.recommendations.length ? (
            <div className="rounded-lg bg-[color:var(--color-primary)]/5 px-4 py-3">
              <h3 className="text-sm font-semibold">{t('recommendations')}</h3>
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-[color:var(--color-muted-foreground)]">
                {result.ai.recommendations.map((item) => <li key={item}>{item}</li>)}
              </ol>
            </div>
          ) : null}
          <p className="text-right text-[11px] text-[color:var(--color-muted-foreground)]">{t('generatedAt', { time: new Date(result.generatedAt).toLocaleString(locale) })}</p>
        </div>
      ) : null}
    </Card>
  );
}

function AnalysisSection({ title, narrative, children }: { title: string; narrative?: string; children: React.ReactNode }) {
  return <section><h3 className="mb-3 text-base font-semibold text-[color:var(--color-foreground)]">{title}</h3>{children}{narrative ? <div className="mt-3 rounded-lg border-l-2 border-[color:var(--color-primary)] bg-[color:var(--color-primary)]/5 px-4 py-3 text-sm leading-6">{narrative}</div> : null}</section>;
}

function Detail({ title, children }: { title: string; children: React.ReactNode }) {
  return <details className="rounded-lg border border-[color:var(--color-border)] px-3 py-2"><summary className="cursor-pointer text-sm font-medium">{title}</summary><div className="mt-2 text-sm">{children}</div></details>;
}

function Empty({ text }: { text: string }) {
  return <p className="py-1 text-xs text-[color:var(--color-muted-foreground)]">{text}</p>;
}

function Ranking({ title, videos, locale, metric }: { title: string; videos: PerformanceVideo[]; locale: string; metric: string }) {
  return <div className="mb-3"><h4 className="text-xs font-semibold text-[color:var(--color-muted-foreground)]">{title}</h4>{videos.map((video) => <div key={video.id} className="mt-1 flex items-center justify-between gap-4 text-xs"><span className="min-w-0 truncate"><AccountLink account={video.account} locale={locale} /> · <VideoLink video={video} /></span><span className="shrink-0 tabular-nums">{formatMetric(video.primaryMetric, metric)}</span></div>)}</div>;
}

function AccountRanking({ title, accounts, locale }: { title: string; accounts: Array<AnalysisAccountRef & { contentCount: number; medianMetric: number }>; locale: string }) {
  return <div><h4 className="text-xs font-semibold text-[color:var(--color-muted-foreground)]">{title}</h4>{accounts.map((account) => <div key={account.id} className="mt-1 flex justify-between gap-2 text-xs"><AccountLink account={account} locale={locale} /><span className="tabular-nums">{Math.round(account.medianMetric).toLocaleString()}</span></div>)}</div>;
}

function formatMetric(value: number, metric: string): string {
  return metric === 'daily_plays' ? `${Math.round(value).toLocaleString()}/d` : `${value.toFixed(1)}/d`;
}
