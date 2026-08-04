import Link from 'next/link';
import type { Route } from 'next';
import { Suspense, cache } from 'react';
import { connection } from 'next/server';
import { getTranslations } from 'next-intl/server';

import { AccountsPanel } from '@/components/accounts-panel';
import { AccountsRangeFilter } from '@/components/accounts-range-filter';
import { AutoRefresh } from '@/components/auto-refresh';
import { BotanicalAccent } from '@/components/botanical-accent';
import { ChartDropdownFilter } from '@/components/chart-dropdown-filter';
import { PlatformMark, formatCompact, formatRelative } from '@/components/format';
import { GroupViewsChart } from '@/components/group-views-chart';
import { ImportDialog } from '@/components/import-dialog';
import { TaskQueueCard } from '@/components/task-queue-card';
import { Card, Td, Th } from '@/components/ui';
import { formatAccountListLabel, formatAccountListTitle } from '@/lib/account-display';
import { requireViewer, type Viewer } from '@/lib/auth/viewer';
import { getGroupViewsTrend } from '@/lib/queries/group-views';
import type { GroupViewsPoint } from '@/lib/queries/group-views';
import {
  countWorkspaceInfluencers,
  getFetchTaskStats,
  listCompetitorOverview,
} from '@/lib/queries/influencers';
import { listVideos } from '@/lib/queries/videos';
import {
  CHART_PERIODS,
  CHART_RANGES,
  RANGES,
  buildAccountsQuery,
  parseAccountsSearchParams,
  rangeToSince,
  type ChartPeriod,
  type ChartRange,
} from '@/lib/url-params';
import { FetchEnabledPlatforms, PlatformLabels, type Platform } from '@svm/shared/platforms';

export const dynamic = 'force-dynamic';

/** React cache():多个 Suspense 子段各自 await 时同一请求只击中 PG 一次。 */
const getCompetitorTaskStatsCached = cache((viewer: Viewer) =>
  getFetchTaskStats(viewer, 'competitor'),
);
const countCompetitorsCached = cache((viewer: Viewer) =>
  countWorkspaceInfluencers(viewer, 'competitor'),
);

/**
 * 竞品分析页。与 monitor 页(自己的账号)同构但取舍不同:
 * - 无分组轴(竞品订阅行 group_id 恒 NULL) → 无 GroupsSidebar,单列布局
 * - 无 AI 分析卡(本期不做竞品 AI 对比)、无批量重试
 * - 多出竞品概览(粉丝规模对比)与爆款内容速览
 * root 不 await 数据查询,全部下放 Suspense 子段(同 monitor 页的流式骨架策略)。
 */
export default async function CompetitorsPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();

  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'competitorsPage' });
  const common = await getTranslations({ locale, namespace: 'common' });
  const sp = await searchParams;
  const parsed = parseAccountsSearchParams(sp);
  const basePath = `/${locale}/competitors`;

  const rangeLabels = Object.fromEntries(
    RANGES.map((r) => [r, common(`range.${r}`)]),
  ) as Record<(typeof RANGES)[number], string>;

  const periodLabels = {
    day: t('viewsPeriodDay'),
    week: t('viewsPeriodWeek'),
    month: t('viewsPeriodMonth'),
  } satisfies Record<ChartPeriod, string>;
  const chartRangeLabels = {
    '30d': t('viewsRange30d'),
    '90d': t('viewsRange90d'),
    '365d': t('viewsRange365d'),
    all: t('viewsRangeAll'),
  } satisfies Record<ChartRange, string>;
  const chartPlatformOptions = ['all', ...FetchEnabledPlatforms.filter((p) => p !== 'douyin')] as Array<
    Platform | 'all'
  >;
  const chartPlatformLabels: Record<Platform | 'all', string> = {
    all: t('viewsPlatformAll'),
    tiktok: PlatformLabels.tiktok,
    instagram: PlatformLabels.instagram,
    facebook: PlatformLabels.facebook,
    youtube: PlatformLabels.youtube,
    douyin: PlatformLabels.douyin,
  };

  const viewer = await requireViewer();

  return (
    <div className="relative mx-auto max-w-[1600px] px-5 py-7 lg:px-8 lg:py-8">
      <BotanicalAccent
        variant="sprig"
        sizes="360px"
        className="absolute -left-28 -top-20 w-[360px] -rotate-[8deg] opacity-[0.07]"
      />
      <BotanicalAccent
        variant="petals"
        sizes="240px"
        motion="drift"
        className="absolute right-[8%] -top-16 w-[240px] opacity-[0.05]"
      />
      <div className="relative z-10 mb-7 flex items-center justify-between gap-5">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="text-[30px] font-semibold text-[color:var(--color-foreground)] [font-family:var(--font-serif)]">
            {t('title')}
          </h1>
          <Suspense fallback={null}>
            <CompetitorCountBadge viewer={viewer} label={(count) => t('accountCount', { count })} />
          </Suspense>
        </div>
        {viewer.monitorQuota > 0 ? (
          <ImportDialog relation="competitor" />
        ) : (
          <Link
            href={`/${locale}/apply?plan=trial` as Route}
            className="inline-flex h-9 items-center rounded-md bg-[color:var(--color-primary)] px-4 text-sm font-semibold text-white transition hover:opacity-90"
          >
            {t('applyTrial')}
          </Link>
        )}
      </div>

      {viewer.monitorQuota === 0 ? (
        <Card className="relative mb-6 overflow-hidden border-[color:var(--color-primary)]/20 bg-[color:var(--color-muted)]/55 p-5">
          <BotanicalAccent
            variant="petals"
            sizes="150px"
            className="absolute -right-8 -top-12 w-[150px] opacity-[0.09]"
          />
          <div className="relative">
            <h2 className="text-base font-semibold text-[color:var(--color-foreground)]">
              {t('quotaPendingTitle')}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[color:var(--color-muted-foreground)]">
              {t('quotaPendingDescription')}
            </p>
          </div>
        </Card>
      ) : null}

      <div className="space-y-6">
        <Card className="space-y-3 p-5">
          <h2 className="text-sm font-semibold text-[color:var(--color-foreground)]">
            {t('overviewTitle')}
          </h2>
          <Suspense fallback={<OverviewSkeleton />}>
            <CompetitorOverviewSection viewer={viewer} locale={locale} />
          </Suspense>
        </Card>

        <Card className="space-y-3 p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-[color:var(--color-foreground)]">
                {t('trendTitle')}
              </h2>
              <p className="mt-1 text-xs text-[color:var(--color-muted-foreground)]">
                {t('trendLegend')}
              </p>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <ChartDropdownFilter
                key={`period-${parsed.period}`}
                label={t('viewsPeriodLabel')}
                currentLabel={periodLabels[parsed.period]}
                options={CHART_PERIODS.map((period) => ({
                  key: period,
                  label: periodLabels[period],
                  href: (basePath + buildAccountsQuery(parsed, { period })) as Route,
                  active: parsed.period === period,
                }))}
              />
              <ChartDropdownFilter
                key={`range-${parsed.chartRange}`}
                label={t('viewsRangeLabel')}
                currentLabel={chartRangeLabels[parsed.chartRange]}
                options={CHART_RANGES.map((chartRange) => ({
                  key: chartRange,
                  label: chartRangeLabels[chartRange],
                  href: (basePath + buildAccountsQuery(parsed, { chartRange })) as Route,
                  active: parsed.chartRange === chartRange,
                }))}
              />
              <ChartDropdownFilter
                key={`chart-platform-${parsed.chartPlatform}`}
                label={t('viewsPlatformLabel')}
                currentLabel={chartPlatformLabels[parsed.chartPlatform]}
                options={chartPlatformOptions.map((chartPlatform) => ({
                  key: chartPlatform,
                  label: chartPlatformLabels[chartPlatform],
                  href: (basePath + buildAccountsQuery(parsed, { chartPlatform })) as Route,
                  active: parsed.chartPlatform === chartPlatform,
                }))}
              />
            </div>
          </div>
          <Suspense
            key={`chart-${parsed.period}-${parsed.chartRange}-${parsed.chartPlatform}`}
            fallback={<ChartSkeleton />}
          >
            <CompetitorViewsChartAsync
              viewer={viewer}
              period={parsed.period}
              chartRange={parsed.chartRange}
              platform={parsed.chartPlatform}
              locale={locale}
              emptyLabel={t('trendEmpty')}
            />
          </Suspense>
        </Card>

        <Suspense fallback={<TaskQueueSkeleton />}>
          <TaskQueueSection viewer={viewer} />
        </Suspense>
        {/* 焦点感知刷新 — 抓取进行中时竞品数据陆续可见 */}
        <AutoRefresh />

        <Card className="space-y-3 p-5">
          <h2 className="text-sm font-semibold text-[color:var(--color-foreground)]">
            {t('topVideosTitle')}
          </h2>
          <Suspense fallback={<TopVideosSkeleton />}>
            <TopVideosSection viewer={viewer} range={parsed.range} locale={locale} />
          </Suspense>
        </Card>

        <Card className="space-y-3 px-5 py-3">
          <AccountsRangeFilter
            current={parsed}
            basePath={basePath}
            label={common('publishedAt')}
            rangeLabels={rangeLabels}
          />
        </Card>

        <AccountsPanel
          parsed={parsed}
          locale={locale}
          platformLabel={common('platform')}
          rangeLabel={common(`range.${parsed.range}`)}
          rangeIsAll={parsed.range === 'all'}
          groups={[]}
          basePath={basePath}
          relation="competitor"
        />
      </div>
    </div>
  );
}

/* ────────────────── Section: header 计数 ────────────────── */

async function CompetitorCountBadge({
  viewer,
  label,
}: {
  viewer: Viewer;
  label: (count: number) => string;
}) {
  const count = await countCompetitorsCached(viewer);
  return (
    <span className="text-sm tabular-nums text-[color:var(--color-muted-foreground)]">
      {label(count)}
    </span>
  );
}

/* ────────────────── Section: 竞品概览(粉丝规模对比) ────────────────── */

async function CompetitorOverviewSection({ viewer, locale }: { viewer: Viewer; locale: string }) {
  const [t, rows] = await Promise.all([
    getTranslations({ locale, namespace: 'competitorsPage' }),
    listCompetitorOverview(viewer),
  ]);
  if (rows.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-[color:var(--color-muted-foreground)]">
        {t('overviewEmpty')}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="border-b border-[color:var(--color-border)]">
          <tr>
            <Th>{t('overviewAccount')}</Th>
            <Th className="text-right">{t('followers')}</Th>
            <Th className="text-right">{t('totalVideos')}</Th>
            <Th className="text-right">{t('lastFetched')}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              className="border-b border-[color:var(--color-border)] last:border-b-0 hover:bg-[color:var(--color-muted)]/40"
            >
              <Td>
                <Link
                  href={`/${locale}/accounts/${row.id}` as Route}
                  className="flex min-w-0 items-center gap-2 hover:underline"
                  title={formatAccountListTitle(row)}
                >
                  <PlatformMark platform={row.platform} />
                  <span className="truncate font-semibold">{formatAccountListLabel(row)}</span>
                </Link>
              </Td>
              <Td className="text-right tabular-nums font-semibold">
                {formatCompact(row.followerCount, locale)}
              </Td>
              <Td className="text-right tabular-nums">
                {formatCompact(row.videoCount, locale)}
              </Td>
              <Td className="text-right text-xs text-[color:var(--color-muted-foreground)]">
                {row.lastFetchedAt ? formatRelative(row.lastFetchedAt, locale) : t('neverFetched')}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div className="space-y-1.5">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-9 w-full animate-pulse rounded-md bg-[color:var(--color-muted)]/40" />
      ))}
    </div>
  );
}

/* ────────────────── Section: 发布表现图表 ────────────────── */

async function CompetitorViewsChartAsync({
  viewer,
  period,
  chartRange,
  platform,
  locale,
  emptyLabel,
}: {
  viewer: Viewer;
  period: ChartPeriod;
  chartRange: ChartRange;
  platform: Platform | 'all';
  locale: string;
  emptyLabel: string;
}) {
  const data: GroupViewsPoint[] = await getGroupViewsTrend({
    workspaceId: viewer.workspaceId,
    period,
    chartRange,
    platform,
    relation: 'competitor',
  });
  return <GroupViewsChart data={data} locale={locale} emptyLabel={emptyLabel} />;
}

function ChartSkeleton() {
  return (
    <div
      className="h-[260px] w-full animate-pulse rounded-md bg-[color:var(--color-muted)]/40"
      aria-hidden
    />
  );
}

/* ────────────────── Section: 抓取任务卡 ────────────────── */

async function TaskQueueSection({ viewer }: { viewer: Viewer }) {
  const [stats, totalAccounts] = await Promise.all([
    getCompetitorTaskStatsCached(viewer),
    countCompetitorsCached(viewer),
  ]);
  return <TaskQueueCard stats={stats} totalAccounts={totalAccounts} />;
}

function TaskQueueSkeleton() {
  return (
    <div className="h-[52px] w-full animate-pulse rounded-md bg-[color:var(--color-muted)]/40" />
  );
}

/* ────────────────── Section: 爆款内容速览 ────────────────── */

/**
 * 固定按播放量降序 Top 20,无表头排序交互 —— 不复用 VideosTable:
 * 其 SortableTh 链接走 videos 页参数体系(platform 默认 tiktok / sort / order),
 * 会与本页 accounts 参数体系(platform=all / range / expand)在同一 URL 上互相覆盖。
 */
async function TopVideosSection({
  viewer,
  range,
  locale,
}: {
  viewer: Viewer;
  range: (typeof RANGES)[number];
  locale: string;
}) {
  const [t, contentType, videos] = await Promise.all([
    getTranslations({ locale, namespace: 'competitorsPage' }),
    getTranslations({ locale, namespace: 'contentType' }),
    listVideos(viewer, {
      relation: 'competitor',
      since: rangeToSince(range),
      sortBy: 'play_count',
      order: 'desc',
      limit: 20,
    }),
  ]);
  if (videos.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-[color:var(--color-muted-foreground)]">
        {t('topVideosEmpty')}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="border-b border-[color:var(--color-border)]">
          <tr>
            <Th>{t('overviewAccount')}</Th>
            <Th className="w-[40%]">{t('videoCol')}</Th>
            <Th className="text-right">{t('playCount')}</Th>
            <Th className="text-right">{t('likeCount')}</Th>
            <Th className="text-right">{t('publishedAt')}</Th>
          </tr>
        </thead>
        <tbody>
          {videos.map((v) => {
            const typeKey = ['video', 'reel', 'carousel', 'post'].includes(v.contentType)
              ? (v.contentType as 'video' | 'reel' | 'carousel' | 'post')
              : 'video';
            return (
              <tr
                key={v.id}
                className="border-b border-[color:var(--color-border)] last:border-b-0 hover:bg-[color:var(--color-muted)]/40"
              >
                <Td>
                  <Link
                    href={`/${locale}/accounts/${v.influencerId}` as Route}
                    className="flex min-w-0 items-center gap-2 hover:underline"
                  >
                    <PlatformMark platform={v.influencerPlatform} />
                    <span className="truncate text-xs font-semibold">
                      {formatAccountListLabel({
                        alias: v.influencerAlias,
                        displayName: v.influencerDisplayName,
                        username: v.influencerUsername,
                        platform: v.influencerPlatform,
                      })}
                    </span>
                  </Link>
                </Td>
                <Td>
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      data-content-type={v.contentType}
                      className="inline-flex shrink-0 items-center rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[color:var(--color-muted-foreground)]"
                    >
                      {contentType(typeKey)}
                    </span>
                    {v.videoUrl ? (
                      <a
                        href={v.videoUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate text-xs hover:underline"
                        title={v.description ?? v.videoId}
                      >
                        {v.description || v.videoId}
                      </a>
                    ) : (
                      <span className="truncate text-xs" title={v.description ?? v.videoId}>
                        {v.description || v.videoId}
                      </span>
                    )}
                  </div>
                </Td>
                <Td className="text-right tabular-nums font-semibold">
                  {formatCompact(v.playCount, locale)}
                </Td>
                <Td className="text-right tabular-nums">
                  {formatCompact(v.likeCount, locale)}
                </Td>
                <Td className="text-right text-xs text-[color:var(--color-muted-foreground)]">
                  {v.publishedAt ? formatRelative(v.publishedAt, locale) : '—'}
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TopVideosSkeleton() {
  return (
    <div className="space-y-1.5">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-9 w-full animate-pulse rounded-md bg-[color:var(--color-muted)]/40" />
      ))}
    </div>
  );
}
