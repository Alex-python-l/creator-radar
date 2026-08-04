import { Platforms, type Platform } from '@svm/shared/platforms';

export const RANGES = ['today', '7d', '30d', '90d', 'all'] as const;
export type Range = (typeof RANGES)[number];

/** Monitor 图表的时间聚合粒度：按视频发布时间聚合。 */
export const CHART_PERIODS = ['day', 'week', 'month'] as const;
export type ChartPeriod = (typeof CHART_PERIODS)[number];

/** Monitor 图表展示的发布时间范围，独立于账号列表的 range 过滤。 */
export const CHART_RANGES = ['30d', '90d', '365d', 'all'] as const;
export type ChartRange = (typeof CHART_RANGES)[number];

export const SORT_COLS = [
  'play_count',
  'like_count',
  'comment_count',
  'share_count',
  'published_at',
] as const;
export type SortCol = (typeof SORT_COLS)[number];

const ORDERS = ['desc', 'asc'] as const;
export type Order = (typeof ORDERS)[number];

export interface VideosSearchParams {
  platform: Platform;
  range: Range;
  sort: SortCol;
  order: Order;
  groups: number[];
}

function pickOne<T extends string>(allowed: readonly T[], raw: string | undefined, fallback: T): T {
  return (allowed as readonly string[]).includes(raw ?? '') ? (raw as T) : fallback;
}

export function parseVideosSearchParams(
  search: Record<string, string | string[] | undefined>,
): VideosSearchParams {
  const platformRaw = Array.isArray(search.platform) ? search.platform[0] : search.platform;
  const rangeRaw = Array.isArray(search.range) ? search.range[0] : search.range;
  const sortRaw = Array.isArray(search.sort) ? search.sort[0] : search.sort;
  const orderRaw = Array.isArray(search.order) ? search.order[0] : search.order;
  const groupsRaw = Array.isArray(search.groups) ? search.groups[0] : search.groups;

  const groups: number[] = [];
  if (groupsRaw) {
    for (const token of groupsRaw.split(',')) {
      const n = Number(token);
      if (Number.isInteger(n) && n > 0 && !groups.includes(n)) groups.push(n);
    }
  }

  return {
    platform: pickOne(Platforms, platformRaw, 'tiktok'),
    range: pickOne(RANGES, rangeRaw, 'all'),
    sort: pickOne(SORT_COLS, sortRaw, 'play_count'),
    order: pickOne(ORDERS, orderRaw, 'desc'),
    groups,
  };
}

/**
 * 把 range 转成 published_at >= Date 的下限；'all' 返回 undefined。
 *
 * 'today' 语义是"最近 24 小时"——刻意避开"自然日 UTC 00:00"的边界：
 * 原本按 UTC 切就等于北京时间当天 08:00，凌晨 0~8 点的本地"今天"永远落不进窗口，
 * 加上 scraper 不实时，体感"今天总是没数据"。改成滚动 24h 后时区中立、边界稳定。
 */
export function rangeToSince(range: Range): Date | undefined {
  if (range === 'all') return undefined;
  const now = new Date();
  const days = range === 'today' ? 1 : range === '7d' ? 7 : range === '30d' ? 30 : 90;
  return new Date(now.getTime() - days * 86400_000);
}

/** 构造 URLSearchParams 字符串（不带 ?）。已是默认值的 key 省略。 */
export function buildQuery(
  current: VideosSearchParams,
  overrides: Partial<VideosSearchParams>,
): string {
  const merged: VideosSearchParams = { ...current, ...overrides };
  const params = new URLSearchParams();
  if (merged.platform !== 'tiktok') params.set('platform', merged.platform);
  if (merged.range !== 'all') params.set('range', merged.range);
  if (merged.sort !== 'play_count') params.set('sort', merged.sort);
  if (merged.order !== 'desc') params.set('order', merged.order);
  if (merged.groups.length > 0)
    params.set('groups', [...merged.groups].sort((a, b) => a - b).join(','));
  const s = params.toString();
  return s ? `?${s}` : '';
}

/**
 * 账号管理页用的简化 search params：只关心 range 和 expand。
 * expand 是逗号分隔的 influencer id 列表，控制哪些账号行展开。
 */
export interface AccountsSearchParams {
  platform: Platform | 'all';
  range: Range;
  expand: Set<number>;
  group?: number;
  period: ChartPeriod;
  chartRange: ChartRange;
  /** Monitor 图表的平台过滤,独立于账号列表的 platform。 */
  chartPlatform: Platform | 'all';
}

export function parseAccountsSearchParams(
  search: Record<string, string | string[] | undefined>,
): AccountsSearchParams {
  const platformRaw = Array.isArray(search.platform) ? search.platform[0] : search.platform;
  const rangeRaw = Array.isArray(search.range) ? search.range[0] : search.range;
  const expandRaw = Array.isArray(search.expand) ? search.expand[0] : search.expand;
  const groupRaw = Array.isArray(search.group) ? search.group[0] : search.group;
  const periodRaw = Array.isArray(search.period) ? search.period[0] : search.period;
  const chartRangeRaw = Array.isArray(search.chartRange)
    ? search.chartRange[0]
    : search.chartRange;
  const chartPlatformRaw = Array.isArray(search.chartPlatform)
    ? search.chartPlatform[0]
    : search.chartPlatform;
  const expand = parseAccountExpandValue(expandRaw);
  let group: number | undefined;
  if (groupRaw) {
    const n = Number(groupRaw);
    if (Number.isInteger(n) && n > 0) group = n;
  }
  const period = pickOne(CHART_PERIODS, periodRaw, 'week');
  const chartRange = pickOne(CHART_RANGES, chartRangeRaw, '90d');
  const platform =
    platformRaw === undefined || platformRaw === 'all'
      ? 'all'
      : pickOne(Platforms, platformRaw, 'all' as Platform | 'all');
  const chartPlatform =
    chartPlatformRaw === undefined || chartPlatformRaw === 'all'
      ? 'all'
      : pickOne(Platforms, chartPlatformRaw, 'all' as Platform | 'all');

  // day + all 会生成数百个日 bucket（一年多 = 364 个 Bar 元素）,recharts
  // 渲染明显卡;强制降级到 week。week + all 通常 50 多个 bucket 已经足够。
  const normalizedPeriod = period === 'day' && chartRange === 'all' ? 'week' : period;

  return {
    platform,
    range: (RANGES as readonly string[]).includes(rangeRaw ?? '') ? (rangeRaw as Range) : 'all',
    expand,
    group,
    period: normalizedPeriod,
    chartRange,
    chartPlatform,
  };
}

export function buildAccountsQuery(
  current: AccountsSearchParams,
  overrides: {
    platform?: Platform | 'all';
    range?: Range;
    expand?: Set<number>;
    group?: number;
    period?: ChartPeriod;
    chartRange?: ChartRange;
    chartPlatform?: Platform | 'all';
  },
): string {
  const platform = Object.hasOwn(overrides, 'platform') ? overrides.platform : current.platform;
  const range = overrides.range ?? current.range;
  const expand = overrides.expand ?? current.expand;
  const group = Object.hasOwn(overrides, 'group') ? overrides.group : current.group;
  const period = overrides.period ?? current.period;
  const chartRange = overrides.chartRange ?? current.chartRange;
  const chartPlatform = Object.hasOwn(overrides, 'chartPlatform')
    ? overrides.chartPlatform
    : current.chartPlatform;
  const params = new URLSearchParams();
  if (platform && platform !== 'all') params.set('platform', platform);
  if (range !== 'all') params.set('range', range);
  if (expand.size > 0) params.set('expand', [...expand].sort((a, b) => a - b).join(','));
  if (group !== undefined) params.set('group', String(group));
  if (period !== 'week') params.set('period', period);
  if (chartRange !== '90d') params.set('chartRange', chartRange);
  if (chartPlatform && chartPlatform !== 'all') params.set('chartPlatform', chartPlatform);
  const s = params.toString();
  return s ? `?${s}` : '';
}

function parseAccountExpandValue(raw: string | null | undefined): Set<number> {
  const expand = new Set<number>();
  if (!raw) return expand;
  for (const token of raw.split(',')) {
    const n = Number(token);
    if (Number.isInteger(n) && n > 0) expand.add(n);
  }
  return expand;
}

export function toggleAccountExpandSet(current: Set<number>, id: number): Set<number> {
  const next = new Set(current);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export function applyAccountExpandParam(params: URLSearchParams, expand: Set<number>): string {
  const next = new URLSearchParams(params);
  if (expand.size > 0) next.set('expand', [...expand].sort((a, b) => a - b).join(','));
  else next.delete('expand');
  const s = next.toString();
  return s ? `?${s}` : '';
}

/** 切换到某个组。切组时清空 expand —— 不同组的展开状态没意义。 */
export function switchAccountsGroup(current: AccountsSearchParams, groupId?: number): string {
  return buildAccountsQuery(current, { group: groupId, expand: new Set() });
}
