import Link from 'next/link';
import type { Route } from 'next';
import { Suspense, cache } from 'react';
import { connection } from 'next/server';
import { getTranslations } from 'next-intl/server';

import { AccountsPanel } from '@/components/accounts-panel';
import { AccountsRangeFilter } from '@/components/accounts-range-filter';
import { BotanicalAccent } from '@/components/botanical-accent';
import { ChartDropdownFilter } from '@/components/chart-dropdown-filter';
import { GroupsSidebar } from '@/components/groups-sidebar';

import { AutoRefresh } from '@/components/auto-refresh';
import { ConfirmSubmitButton } from '@/components/confirm-submit-button';
import { Card } from '@/components/ui';
import { ImportDialog } from '@/components/import-dialog';
import { TaskQueueCard } from '@/components/task-queue-card';
import { GroupViewsChart } from '@/components/group-views-chart';
import { MonitorAiAnalysis } from '@/components/monitor-ai-analysis';
import { retryFailedFetchesForm } from '@/lib/actions/accounts';
import { requireViewer, type Viewer } from '@/lib/auth/viewer';
import {
  getFetchTaskStats,
  listGroupsWithCount,
} from '@/lib/queries/influencers';
import { getGroupViewsTrend } from '@/lib/queries/group-views';
import type { GroupViewsPoint } from '@/lib/queries/group-views';
import {
  CHART_PERIODS,
  CHART_RANGES,
  RANGES,
  buildAccountsQuery,
  parseAccountsSearchParams,
  type AccountsSearchParams,
  type ChartPeriod,
  type ChartRange,
} from '@/lib/url-params';
import { FetchEnabledPlatforms, PlatformLabels, type Platform } from '@svm/shared/platforms';

export const dynamic = 'force-dynamic';

/**
 * React `cache()` 是 per-request 去重 —— 同一个 RSC 请求里多个 server component
 * 各自 await 这两个查询时, 实际只击中 PG 一次。让我们可以在 RetryFailedSection、
 * TaskQueueSection 各自独立 Suspense 而不重复打数据库。
 */
const getTaskStatsCached = cache(getFetchTaskStats);
const listGroupsCached = cache(listGroupsWithCount);

/**
 * Root 不 await 任何数据查询 —— 只做 i18n / parse / 渲染骨架结构 + N 个 Suspense 段。
 * Next 15 client navigation 在 dynamic 页面要等 root segment payload 第一字节才切 URL,
 * 所以 root 必须立刻能开始 stream;数据查询全部下放到子段。
 *
 * 这是切组"点击立刻有响应"的关键改动 —— 之前 root 自己在 await Promise.all,
 * 浏览器看到"卡 1-2 秒 → 页面一下子换"。现在看到"骨架立刻出 → 各块逐个填充"。
 */
export default async function AccountsPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();

  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'accountsPage' });
  const common = await getTranslations({ locale, namespace: 'common' });
  const groups = await getTranslations({ locale, namespace: 'groups' });
  const sp = await searchParams;
  const parsed = parseAccountsSearchParams(sp);

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

  // 图表的平台过滤选项 —— 跟 PlatformFilterChips 保持一致(隐藏 douyin)。
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

  // viewer 携带服务端解析的活动工作空间；平台 admin 也不能绕过隔离。
  // (app)/layout 已做登录门，这里的 requireViewer 是 cache 命中。
  const viewer = await requireViewer();

  // ImportDialog 是 client 组件,需要 server-side fetch groups 提前 await 一次。
  // listGroupsCached 是 React cache(),后续 sidebar/tables section 调时走 cache hit,
  // 不重复打 DB。
  const groupRows = await listGroupsCached(viewer);
  const groupsForImport = groupRows.map((g) => ({ id: g.id, name: g.name }));
  const monitoredAccountCount = groupRows.reduce((sum, group) => sum + group.accountCount, 0);
  const defaultImportGroupId = parsed.group ?? groupsForImport[0]?.id ?? 1;
  const currentGroupName = parsed.group === undefined
    ? groups('allMixed')
    : groupRows.find((group) => group.id === parsed.group)?.name ?? groups('allMixed');

  return (
    <div className="relative mx-auto max-w-[1600px] px-5 py-7 lg:px-8 lg:py-8">
      <BotanicalAccent
        variant="sprig"
        sizes="360px"
        className="absolute -left-28 -top-20 w-[360px] -rotate-[8deg] opacity-[0.14]"
      />
      <BotanicalAccent
        variant="petals"
        sizes="240px"
        motion="drift"
        className="absolute right-[8%] -top-16 w-[240px] opacity-[0.12]"
      />
      <div className="relative z-10 mb-7 flex flex-col items-start justify-between gap-5 sm:flex-row sm:items-center">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="text-[30px] font-semibold text-[color:var(--color-foreground)] [font-family:var(--font-serif)]">
            {t('title')}
          </h1>
          <span className="text-sm tabular-nums text-[color:var(--color-muted-foreground)]">
            {t('accountCount', {count: monitoredAccountCount})}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Suspense fallback={null}>
            <RetryFailedSection locale={locale} viewer={viewer} />
          </Suspense>
          {viewer.monitorQuota > 0 ? (
            <ImportDialog groups={groupsForImport} defaultGroupId={defaultImportGroupId} />
          ) : (
            <Link
              href={`/${locale}/apply?plan=trial` as Route}
              className="inline-flex h-9 items-center rounded-md bg-[color:var(--color-primary)] px-4 text-sm font-semibold text-white transition hover:opacity-90"
            >
              {t('applyTrial')}
            </Link>
          )}
        </div>
      </div>

      {viewer.monitorQuota === 0 ? (
        <Card className="relative mb-6 overflow-hidden border-[color:var(--color-primary)]/20 bg-[color:var(--color-muted)]/55 p-5">
          <BotanicalAccent
            variant="petals"
            sizes="150px"
            className="absolute -right-8 -top-12 w-[150px] opacity-[0.15]"
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

      <div className="flex flex-col gap-5 lg:flex-row">
        <Suspense fallback={<GroupsSidebarSkeleton />}>
          <GroupsSidebarSection current={parsed} locale={locale} viewer={viewer} />
        </Suspense>
        <div className="flex-1 min-w-0 space-y-6">
          <MonitorAiAnalysis
            key={`monitor-analysis-${parsed.group ?? 'all'}-${parsed.platform}`}
            groupId={parsed.group}
            groupName={currentGroupName}
            platform={parsed.platform}
            locale={locale}
            canManageSecrets={viewer.canManageSecrets}
          />
          <Card className="space-y-3 p-5">
            <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
              <div>
                <h2 className="text-sm font-semibold text-[color:var(--color-foreground)]">
                  {t('viewsTrendTitle')}
                </h2>
                <p className="mt-1 text-xs text-[color:var(--color-muted-foreground)]">
                  {t('viewsChartLegend')}
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
                    href: (`/${locale}/monitor` + buildAccountsQuery(parsed, { period })) as Route,
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
                    href: (`/${locale}/monitor` + buildAccountsQuery(parsed, { chartRange })) as Route,
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
                    href: (`/${locale}/monitor` +
                      buildAccountsQuery(parsed, { chartPlatform })) as Route,
                    active: parsed.chartPlatform === chartPlatform,
                  }))}
                />
              </div>
            </div>
            {/* key 绑定 group+period+range+chartPlatform:切组/切图表控制都触发 Suspense fallback 重新显示,
                避免"展示旧曲线骨架/旧数据混在一起"的错觉。 */}
            <Suspense
              key={`chart-${parsed.group}-${parsed.period}-${parsed.chartRange}-${parsed.chartPlatform}`}
              fallback={<GroupViewsChartSkeleton />}
            >
              <GroupViewsChartAsync
                viewer={viewer}
                groupId={parsed.group}
                period={parsed.period}
                chartRange={parsed.chartRange}
                platform={parsed.chartPlatform}
                locale={locale}
                emptyLabel={t('viewsTrendEmpty')}
              />
            </Suspense>
          </Card>

          <Suspense fallback={<TaskQueueSkeleton />}>
            <TaskQueueSection currentGroupId={parsed.group} viewer={viewer} />
          </Suspense>
          {/* 焦点感知刷新 — 5s 周期(tab 可见时)+ 切回前台/focus/网络恢复立即刷 */}
          <AutoRefresh />

          <Card className="space-y-3 px-5 py-3">
            <AccountsRangeFilter
              current={parsed}
              basePath={`/${locale}/monitor`}
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
            groups={groupsForImport}
            basePath={`/${locale}/monitor`}
          />
        </div>
      </div>
    </div>
  );
}

/* ────────────────── Section: GroupsSidebar (侧栏分组列表) ────────────────── */

async function GroupsSidebarSection({
  current,
  locale,
  viewer,
}: {
  current: AccountsSearchParams;
  locale: string;
  viewer: Viewer;
}) {
  const groups = await listGroupsCached(viewer);
  return <GroupsSidebar groups={groups} current={current} locale={locale} />;
}

function GroupsSidebarSkeleton() {
  return (
    <aside className="w-full flex-shrink-0 space-y-4 lg:w-40">
      <div className="space-y-1">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-9 w-full animate-pulse rounded-md bg-[color:var(--color-muted)]/40"
          />
        ))}
      </div>
    </aside>
  );
}

/* ────────────────── Section: RetryFailedButton (header 重试徽章) ────────────────── */

async function RetryFailedSection({ locale, viewer }: { locale: string; viewer: Viewer }) {
  const [t, stats] = await Promise.all([
    getTranslations({ locale, namespace: 'accountsPage' }),
    getTaskStatsCached(viewer),
  ]);
  if (stats.dead === 0) return null;
  return (
    <form action={retryFailedFetchesForm}>
      <ConfirmSubmitButton
        variant="secondary"
        confirmMessage={t('confirmRetryFailed', { count: stats.dead })}
      >
        {t('retryFailed', { count: stats.dead })}
      </ConfirmSubmitButton>
    </form>
  );
}

/* ────────────────── Section: TaskQueueCard (中间任务计数卡) ────────────────── */

async function TaskQueueSection({
  currentGroupId,
  viewer,
}: {
  currentGroupId?: number;
  viewer: Viewer;
}) {
  // totalAccounts 改从 groups.accountCount 拿 —— 不再依赖 accounts 表查询结果,
  // 这样任务卡和账号表可以独立 stream,任务卡随 task-stats(~50ms 轻查询)立即出。
  const [stats, groups] = await Promise.all([
    getTaskStatsCached(viewer),
    listGroupsCached(viewer),
  ]);
  const totalAccounts =
    currentGroupId === undefined
      ? groups.reduce((sum, g) => sum + g.accountCount, 0)
      : groups.find((g) => g.id === currentGroupId)?.accountCount ?? 0;
  return <TaskQueueCard stats={stats} totalAccounts={totalAccounts} />;
}

function TaskQueueSkeleton() {
  return (
    <div className="mb-6 h-[68px] w-full animate-pulse rounded-md bg-[color:var(--color-muted)]/40" />
  );
}

/**
 * 趋势曲线的 server component 边界 —— 把唯一一个随分组规模线性变重的查询
 * (getGroupViewsTrend) 隔离到这里。
 */
async function GroupViewsChartAsync({
  viewer,
  groupId,
  period,
  chartRange,
  platform,
  locale,
  emptyLabel,
}: {
  viewer: Viewer;
  groupId?: number;
  period: ChartPeriod;
  chartRange: ChartRange;
  platform: Platform | 'all';
  locale: string;
  emptyLabel: string;
}) {
  const data: GroupViewsPoint[] = await getGroupViewsTrend({
    workspaceId: viewer.workspaceId,
    groupId,
    period,
    chartRange,
    platform,
    relation: 'own',
  });
  return <GroupViewsChart data={data} locale={locale} emptyLabel={emptyLabel} />;
}

function GroupViewsChartSkeleton() {
  return (
    <div
      className="h-[260px] w-full animate-pulse rounded-md bg-[color:var(--color-muted)]/40"
      aria-hidden
    />
  );
}
