import { sql } from 'drizzle-orm';
import { unstable_cache } from 'next/cache';

import { db } from '@/lib/db';
import type { AccountRelation } from '@/lib/account-relation';
import type { ChartPeriod, ChartRange } from '@/lib/url-params';
import type { Platform } from '@svm/shared/platforms';

export interface GroupViewsPoint {
  periodStart: string; // 'YYYY-MM-DD'
  label: string;
  totalViews: number;
  videoCount: number;
  avgViews: number;
  viralCount: number;
}

/**
 * Monitor 图表：按视频发布时间聚合，展示每日 / 每周 / 每月发布内容的当前表现。
 * 这里回答的是“这个分组在某个发布周期产出了多少视频、拿到多少播放”，
 * 不是按抓取日期变化的播放量快照。
 *
 * viewer 隔离:viewerIsAdmin=false 时,一切 videos 聚合都限制在该用户
 * 订阅的账号内;组过滤按订阅行 group_id(私有组轴)。这两个标量随 opts
 * 进入 unstable_cache 的 key,不同用户互不串缓存。
 */
async function _getGroupViewsTrend(opts: {
  workspaceId: number;
  groupId?: number;
  period: ChartPeriod;
  chartRange: ChartRange;
  platform?: Platform | 'all';
  /** 订阅层，默认 'own'。竞品页传 'competitor'。opts 进 cache key，两层互不串缓存。 */
  relation?: AccountRelation;
}): Promise<GroupViewsPoint[]> {
  const platform = opts.platform && opts.platform !== 'all' ? opts.platform : undefined;
  const relation = opts.relation ?? 'own';
  const scopeFilter = (idExpr: ReturnType<typeof sql>): ReturnType<typeof sql> => {
    const groupCond =
      opts.groupId === undefined ? sql`` : sql`AND wi.group_id = ${opts.groupId}`;
    return sql`AND EXISTS (
        SELECT 1 FROM workspace_influencers wi
        WHERE wi.influencer_id = ${idExpr}
          AND wi.workspace_id = ${opts.workspaceId}
          AND wi.relation = ${relation}
          ${groupCond}
      )`;
  };
  const rangeStartGroupFilter = scopeFilter(sql`vs.influencer_id`);
  const rangeStartPlatformFilter =
    platform === undefined ? sql`` : sql`AND vs.platform = ${platform}`;
  const videoPlatformFilter =
    platform === undefined ? sql`` : sql`AND v.platform = ${platform}`;
  const rangeStartExpr = {
    '30d': sql`CURRENT_DATE - INTERVAL '29 days'`,
    '90d': sql`CURRENT_DATE - INTERVAL '89 days'`,
    '365d': sql`CURRENT_DATE - INTERVAL '364 days'`,
    all: sql`COALESCE((
      SELECT MIN(vs.published_at)::date
      FROM videos vs
      WHERE vs.published_at IS NOT NULL
        AND vs.published_at < CURRENT_DATE + INTERVAL '1 day'
        ${rangeStartGroupFilter}
        ${rangeStartPlatformFilter}
    ), CURRENT_DATE)`,
  }[opts.chartRange];
  const periodStartExpr = {
    day: sql`(${rangeStartExpr})::date`,
    week: sql`date_trunc('week', ${rangeStartExpr})::date`,
    month: sql`date_trunc('month', ${rangeStartExpr})::date`,
  }[opts.period];
  const periodEndExpr = {
    day: sql`CURRENT_DATE`,
    week: sql`date_trunc('week', CURRENT_DATE)::date`,
    month: sql`date_trunc('month', CURRENT_DATE)::date`,
  }[opts.period];
  const periodStepExpr = {
    day: sql`INTERVAL '1 day'`,
    week: sql`INTERVAL '1 week'`,
    month: sql`INTERVAL '1 month'`,
  }[opts.period];
  const videoBucketExpr = {
    day: sql`date_trunc('day', v.published_at)::date`,
    week: sql`date_trunc('week', v.published_at)::date`,
    month: sql`date_trunc('month', v.published_at)::date`,
  }[opts.period];
  const labelExpr =
    opts.period === 'month'
      ? sql`to_char(b.period_start, 'YYYY-MM')`
      : sql`to_char(b.period_start, 'MM/DD')`;
  const groupFilter = scopeFilter(sql`v.influencer_id`);

  const rows = (await db.execute(sql`
    WITH buckets AS (
      SELECT generate_series(
        ${periodStartExpr},
        ${periodEndExpr},
        ${periodStepExpr}
      )::date AS period_start
    )
    SELECT
      to_char(b.period_start, 'YYYY-MM-DD') AS period_start,
      ${labelExpr} AS label,
      COALESCE(SUM(v.play_count), 0) AS total_views,
      COUNT(v.id) AS video_count,
      COALESCE(ROUND(AVG(v.play_count))::bigint, 0) AS avg_views,
      COUNT(v.id) FILTER (
        WHERE v.play_count >= CASE
          WHEN i.view_threshold > 0 THEN i.view_threshold
          ELSE 30000
        END
      ) AS viral_count
    FROM buckets b
    LEFT JOIN videos v
      ON v.published_at IS NOT NULL
      AND v.published_at >= ${rangeStartExpr}
      AND v.published_at < CURRENT_DATE + INTERVAL '1 day'
      AND ${videoBucketExpr} = b.period_start
      ${groupFilter}
      ${videoPlatformFilter}
    LEFT JOIN influencers i ON i.id = v.influencer_id
    GROUP BY b.period_start
    ORDER BY b.period_start ASC
  `)) as unknown as Array<{
    period_start: string;
    label: string;
    total_views: string | number;
    video_count: string | number;
    avg_views: string | number;
    viral_count: string | number;
  }>;

  return rows.map((r) => ({
    periodStart: r.period_start,
    label: r.label,
    totalViews: Number(r.total_views),
    videoCount: Number(r.video_count),
    avgViews: Number(r.avg_views),
    viralCount: Number(r.viral_count),
  }));
}

/**
 * Chart 数据 60s stale OK(用户已确认),用 unstable_cache 包装:
 * - 命中:~0ms,绕过 DB
 * - Miss:首次 _getGroupViewsTrend 跑一次,后续 60s 内命中
 * - 失效:60s 自然过期;改聚合逻辑时 bump key 版本号 `@v1` 全失效
 *
 * cache key 由 opts 自动派生(unstable_cache 序列化参数)。tag 'group-views'
 * 留作将来跨进程手动失效用(目前不依赖)。
 *
 * 注意:_getGroupViewsTrend 必须直接传给 unstable_cache,不能在内部用闭包
 * 引用 opts ——闭包变量不会进入 cache key,会跨参数串数据。
 */
export const getGroupViewsTrend = unstable_cache(
  _getGroupViewsTrend,
  ['group-views-trend@v4-relation'],
  { revalidate: 60, tags: ['group-views'] },
);
