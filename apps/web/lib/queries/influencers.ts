import { and, desc, eq, sql } from 'drizzle-orm';

import { db } from '@/lib/db';
import { fetchLogs, influencers, videos, workspaceInfluencers } from '@/lib/db/schema';
import type { AccountRelation } from '@/lib/account-relation';
import {
  scopeAliasColSql,
  scopeGroupColSql,
  scopeJoinSql,
  scopeVisibleExistsSql,
  type ViewerScope,
} from '@/lib/queries/scope';
import { isPlatform, type Platform } from '@svm/shared/platforms';

export async function listPlatformAccountCounts(
  viewer: ViewerScope,
  groupId?: number,
  relation: AccountRelation = 'own',
): Promise<Partial<Record<Platform, number>>> {
  const groupFilter =
    groupId !== undefined ? sql`WHERE ${scopeGroupColSql(viewer)} = ${groupId}` : sql``;
  const rows = (await db.execute(sql`
    SELECT i.platform, COUNT(*)::int AS account_count
    FROM influencers i
    ${scopeJoinSql(viewer, relation)}
    ${groupFilter}
    GROUP BY i.platform
  `)) as unknown as Array<{ platform: string; account_count: number | string }>;

  const counts: Partial<Record<Platform, number>> = {};
  for (const row of rows) {
    if (isPlatform(row.platform)) counts[row.platform] = Number(row.account_count);
  }
  return counts;
}

/**
 * 调用方（page.tsx + route handler）期望的账号行类型。
 * 显式标注让 db.execute(sql) 的原生结果在 service 边界做一次严格 reshape,
 * 上游再次重构 SQL 不会悄悄改变 prop 形状。
 *
 * 字段精简（相较早期版本）：删去了前端不用的
 * followerCount / followingCount / videoCount / avatarUrl / createdAt / lastFetchedAt，
 * 保留 displayName + platform + groupId 以及 recent_* / latest_fetch_* / activeJobStatus。
 */
export interface InfluencerWithRecentCount {
  id: number;
  /** 竞品订阅行不进分组,group_id 为 NULL。 */
  groupId: number | null;
  displayNumber: number | null;
  platform: string;
  username: string;
  displayName: string | null;
  alias: string | null;
  recentVideoCount: number;
  recentPlayCount: number;
  recentLikeCount: number;
  latestFetchStatus: string | null;
  latestFetchStartedAt: Date | null;
  latestFetchFinishedAt: Date | null;
  latestFetchError: string | null;
  latestFetchErrorCode: string | null;
  activeJobStatus: string | null;
}

export interface GoneAccount {
  id: number;
  groupId: number;
  groupName: string;
  platform: string;
  username: string;
  displayName: string | null;
  alias: string | null;
  createdAt: Date;
  latestFetchStartedAt: Date | null;
  latestFetchFinishedAt: Date | null;
  latestFetchError: string | null;
  latestFetchErrorCode: string | null;
}

/**
 * 每个账号 + 给定时间窗口内发布的视频数 + 最新一条 fetch_log + 是否有活跃 job。
 * since 不传时统计该账号历史所有视频。账号无视频也会出现在结果里(沉默账号)。
 *
 * **性能历史**:原实现用 4 个 fetch_logs 相关子查询 + 1 个 jobs 相关子查询,
 * 在"未分组"(系统默认组,装多数账号) 切换时是首页第二重的查询。
 * 重写为 LATERAL JOIN 一次性拉最新 fetch_log 整行,N+1 PK 查找折成 N 次 index lookup,
 * 配合手工迁移 0002 加的 fetch_logs (influencer_id, id DESC) 索引。
 *
 * **结构**:
 * - 主表 influencers 按 group_id / platform 过滤（两者均可选，走 sql.join 拼 AND）
 * - LATERAL videos:按 influencer_id (+ 可选 published_at) 聚合 COUNT/SUM
 * - LATERAL fetch_logs:每账号最新一条状态行(走新索引)
 * - LATERAL jobs:每账号最新一条 queued/running 的 account.fetch job
 *
 * ⚠ 必须 toISOString() — drizzle@0.36 + postgres-js@3.4.9 在 sql template 中
 * 直接嵌 Date 时,wire-layer Bind 不走 timestamp serializer,
 * 直接把 Date 实例传给 Buffer.byteLength() 抛 ERR_INVALID_ARG_TYPE。
 * 触发条件:首页点 Today/7d/30d/90d chip → since=Date → 整页 SSR 炸。
 * 用 drizzle column-aware (gte(col, date)) 不会炸,因为它知道 column 是
 * timestamp 会 wrap Parameter;此处是 raw sql template,无 column 信息。
 *
 * BigInt(SUM) 在 postgres-js 默认返回 string,边界处 Number() cast 成 number。
 * 播放量极端情况下 > 2^53 才会有精度问题,业务上不可能。
 */
export async function listInfluencersWithRecentCount(
  viewer: ViewerScope,
  opts: {
    since?: Date;
    groupId?: number;
    platform?: Platform;
    useGroupNumbering?: boolean;
    /** 订阅层，默认 'own' —— 防止调用方忘传时把竞品混进自有账号视图。 */
    relation?: AccountRelation;
  },
): Promise<InfluencerWithRecentCount[]> {
  const groupCol = scopeGroupColSql(viewer);
  const sinceFilter = opts.since
    ? sql`AND published_at >= ${opts.since.toISOString()}`
    : sql``;
  const whereClauses: ReturnType<typeof sql>[] = [];
  if (opts.groupId !== undefined) whereClauses.push(sql`${groupCol} = ${opts.groupId}`);
  if (opts.platform !== undefined) whereClauses.push(sql`i.platform = ${opts.platform}`);
  const whereSql =
    whereClauses.length === 0
      ? sql``
      : sql`WHERE ${sql.join(whereClauses, sql` AND `)}`;
  const numberingJoin =
    opts.groupId !== undefined
      ? sql`LEFT JOIN account_group_numbers agn ON agn.group_id = ${groupCol} AND agn.influencer_id = i.id`
      : sql``;
  const displayNumberSql =
    opts.groupId !== undefined
      ? sql`agn.display_no AS display_number,`
      : sql`NULL::int AS display_number,`;
  const orderSql =
    opts.groupId !== undefined && opts.useGroupNumbering
      ? sql`ORDER BY agn.display_no ASC NULLS LAST, i.id ASC`
      : sql`ORDER BY i.id ASC`;

  const rows = (await db.execute(sql`
    SELECT
      i.id,
      ${groupCol} AS group_id,
      ${displayNumberSql}
      i.platform,
      i.username,
      i.display_name,
      ${scopeAliasColSql(viewer)} AS alias,
      v.recent_video_count,
      v.recent_play_count,
      v.recent_like_count,
      lf.status         AS latest_fetch_status,
      lf.started_at     AS latest_fetch_started_at,
      lf.finished_at    AS latest_fetch_finished_at,
      lf.error_message  AS latest_fetch_error,
      lf.error_code     AS latest_fetch_error_code,
      aj.status         AS active_job_status
    FROM influencers i
    ${scopeJoinSql(viewer, opts.relation ?? 'own')}
    ${numberingJoin}
    LEFT JOIN LATERAL (
      SELECT
        COUNT(*)::int                          AS recent_video_count,
        COALESCE(SUM(play_count), 0)::bigint   AS recent_play_count,
        COALESCE(SUM(like_count), 0)::bigint   AS recent_like_count
      FROM videos
      WHERE influencer_id = i.id
        ${sinceFilter}
    ) v ON TRUE
    LEFT JOIN LATERAL (
      SELECT status, started_at, finished_at, error_message, error_code
      FROM fetch_logs
      WHERE influencer_id = i.id
      ORDER BY id DESC
      LIMIT 1
    ) lf ON TRUE
    LEFT JOIN LATERAL (
      SELECT status
      FROM jobs
      WHERE kind = 'account.fetch'
        AND status IN ('queued', 'running')
        AND payload->>'influencerId' ~ '^[0-9]+$'
        AND (payload->>'influencerId')::int = i.id
      ORDER BY id DESC
      LIMIT 1
    ) aj ON TRUE
    ${whereSql}
    ${orderSql}
  `)) as unknown as Array<{
    id: number;
    group_id: number;
    display_number: number | null;
    platform: string;
    username: string;
    display_name: string | null;
    alias: string | null;
    recent_video_count: number;
    recent_play_count: string | number;
    recent_like_count: string | number;
    latest_fetch_status: string | null;
    // postgres-js 在 db.execute(sql`raw`) 路径上对 timestamptz 直接给 ISO string;
    // 走 drizzle ORM (db.select()) 才会自动 deserialize 为 Date。
    // 这里 raw query 边界统一在下面 map 内 toDate() 转一次,接口契约保持 Date|null。
    latest_fetch_started_at: Date | string | null;
    latest_fetch_finished_at: Date | string | null;
    latest_fetch_error: string | null;
    latest_fetch_error_code: string | null;
    active_job_status: string | null;
  }>;

  return rows.map((r) => ({
    id: r.id,
    groupId: r.group_id,
    displayNumber: r.display_number == null ? null : Number(r.display_number),
    platform: r.platform,
    username: r.username,
    displayName: r.display_name,
    alias: r.alias,
    recentVideoCount: r.recent_video_count,
    recentPlayCount: Number(r.recent_play_count),
    recentLikeCount: Number(r.recent_like_count),
    latestFetchStatus: r.latest_fetch_status,
    latestFetchStartedAt: toDate(r.latest_fetch_started_at),
    latestFetchFinishedAt: toDate(r.latest_fetch_finished_at),
    latestFetchError: r.latest_fetch_error,
    latestFetchErrorCode: r.latest_fetch_error_code,
    activeJobStatus: r.active_job_status,
  }));
}

export async function listGoneAccounts(
  viewer: ViewerScope,
  relation: AccountRelation = 'own',
): Promise<GoneAccount[]> {
  const rows = (await db.execute(sql`
    SELECT
      i.id,
      ${scopeGroupColSql(viewer)} AS group_id,
      g.name AS group_name,
      i.platform,
      i.username,
      i.display_name,
      ${scopeAliasColSql(viewer)} AS alias,
      i.created_at,
      lf.started_at AS latest_fetch_started_at,
      lf.finished_at AS latest_fetch_finished_at,
      lf.error_message AS latest_fetch_error,
      lf.error_code AS latest_fetch_error_code
    FROM influencers i
    ${scopeJoinSql(viewer, relation)}
    JOIN account_groups g ON g.id = ${scopeGroupColSql(viewer)}
    JOIN LATERAL (
      SELECT started_at, finished_at, error_message, error_code
      FROM fetch_logs
      WHERE influencer_id = i.id
      ORDER BY id DESC
      LIMIT 1
    ) lf ON TRUE
    WHERE lf.error_code = 'account_gone'
    ORDER BY COALESCE(lf.finished_at, lf.started_at) DESC, i.id ASC
  `)) as unknown as Array<{
    id: number;
    group_id: number;
    group_name: string;
    platform: string;
    username: string;
    display_name: string | null;
    alias: string | null;
    created_at: Date | string;
    latest_fetch_started_at: Date | string | null;
    latest_fetch_finished_at: Date | string | null;
    latest_fetch_error: string | null;
    latest_fetch_error_code: string | null;
  }>;

  return rows.map((r) => ({
    id: r.id,
    groupId: r.group_id,
    groupName: r.group_name,
    platform: r.platform,
    username: r.username,
    displayName: r.display_name,
    alias: r.alias,
    createdAt: toDate(r.created_at) ?? new Date(0),
    latestFetchStartedAt: toDate(r.latest_fetch_started_at),
    latestFetchFinishedAt: toDate(r.latest_fetch_finished_at),
    latestFetchError: r.latest_fetch_error,
    latestFetchErrorCode: r.latest_fetch_error_code,
  }));
}

/** raw sql 边界 — timestamptz 列在 db.execute(sql`...`) 路径上可能是 Date 或 ISO string,统一转 Date。 */
function toDate(v: Date | string | null | undefined): Date | null {
  if (v == null) return null;
  return v instanceof Date ? v : new Date(v);
}

/**
 * 任务计数：queued / running / 今天 failed / 今天 success / 可重试死信账号。
 * admin=全局;user=只统计自己订阅账号的 account.fetch 任务(payload 关联)。
 */
export async function getFetchTaskStats(
  viewer: ViewerScope,
  relation: AccountRelation = 'own',
): Promise<{
  queued: number;
  running: number;
  failedToday: number;
  successToday: number;
  dead: number;
}> {
  // jobs 与账号的关联只在 payload 里;只统计当前 workspace 指定订阅层的账号。
  const jobScope = sql`AND kind = 'account.fetch'
        AND payload->>'influencerId' ~ '^[0-9]+$'
        AND EXISTS (
          SELECT 1 FROM workspace_influencers wiv
          WHERE wiv.influencer_id = (payload->>'influencerId')::int
            AND wiv.workspace_id = ${viewer.workspaceId}
            AND wiv.relation = ${relation}
        )`;

  const startOfToday = new Date();
  startOfToday.setUTCHours(0, 0, 0, 0);

  const [activeRows, todayRows, deadRows] = await Promise.all([
    db.execute(sql`
      SELECT status, COUNT(*)::int AS c
      FROM jobs
      WHERE status IN ('queued', 'running')
        ${jobScope}
      GROUP BY status
    `) as Promise<Array<{ status: string; c: number | string }>>,
    db.execute(sql`
      SELECT status, COUNT(*)::int AS c
      FROM jobs
      WHERE updated_at >= ${startOfToday.toISOString()}
        AND status IN ('success', 'failed')
        ${jobScope}
      GROUP BY status
    `) as Promise<Array<{ status: string; c: number | string }>>,
    db.execute(sql`
      WITH latest_account_fetch_jobs AS (
        SELECT DISTINCT ON ((payload->>'influencerId')::int)
          (payload->>'influencerId')::int AS influencer_id,
          status
        FROM jobs
        WHERE kind = 'account.fetch'
          AND payload->>'influencerId' ~ '^[0-9]+$'
        ORDER BY
          (payload->>'influencerId')::int,
          COALESCE(updated_at, created_at) DESC,
          id DESC
      )
      SELECT COUNT(*)::int AS c
      FROM latest_account_fetch_jobs latest_jobs
      JOIN influencers i ON i.id = latest_jobs.influencer_id
      WHERE i.is_active = true
        AND latest_jobs.status = 'dead'
        ${scopeVisibleExistsSql(viewer, sql`i.id`, relation)}
    `) as Promise<Array<{ c: number | string }>>,
  ]);

  let queued = 0;
  let running = 0;
  let failedToday = 0;
  let successToday = 0;
  for (const r of activeRows) {
    if (r.status === 'queued') queued = Number(r.c);
    else if (r.status === 'running') running = Number(r.c);
  }
  for (const r of todayRows) {
    if (r.status === 'success') successToday = Number(r.c);
    else if (r.status === 'failed') failedToday = Number(r.c);
  }
  const dead = Number(deadRows[0]?.c ?? 0);
  return { queued, running, failedToday, successToday, dead };
}

/**
 * 拿一组账号在时间窗口内的视频列表（多账号一次查），
 * 用于账号管理页展开行内 inline 显示视频。
 */
export async function listRecentVideosForAccounts(
  viewer: ViewerScope,
  influencerIds: number[],
  opts: { since?: Date; perAccountLimit?: number; relation?: AccountRelation } = {},
) {
  if (influencerIds.length === 0) return [];
  const visible = await db
    .select({ id: workspaceInfluencers.influencerId })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.relation, opts.relation ?? 'own'),
      ),
    );
  const visibleSet = new Set(visible.map((r) => r.id));
  influencerIds = influencerIds.filter((id) => visibleSet.has(id));
  if (influencerIds.length === 0) return [];
  const idsSql = sql.join(influencerIds.map((id) => sql`${id}`), sql`, `);
  const sinceFilter = opts.since
    ? sql`AND published_at >= ${opts.since.toISOString()}`
    : sql``;
  const rankFilter = opts.perAccountLimit
    ? sql`WHERE rn <= ${opts.perAccountLimit}`
    : sql``;

  // 只选展开列表实际用到的列。原来 select() 会把每行 ~1.6KB 的 raw jsonb
  // 一起拉出来经 RSC 序列化传输 → 展开巨慢。前端 VideoRow 只用这 8 个小字段。
  const rows = (await db.execute(sql`
    WITH ranked AS (
      SELECT
        id,
        influencer_id,
        video_id,
        video_url,
        description,
        content_type,
        play_count,
        like_count,
        published_at,
        row_number() OVER (
          PARTITION BY influencer_id
          ORDER BY published_at DESC NULLS LAST, id DESC
        ) AS rn
      FROM videos
      WHERE influencer_id IN (${idsSql})
        ${sinceFilter}
    )
    SELECT
      id,
      influencer_id,
      video_id,
      video_url,
      description,
      content_type,
      play_count,
      like_count,
      published_at
    FROM ranked
    ${rankFilter}
    ORDER BY published_at DESC NULLS LAST, id DESC
  `)) as unknown as Array<{
    id: number;
    influencer_id: number;
    video_id: string;
    video_url: string | null;
    description: string | null;
    content_type: string;
    play_count: number;
    like_count: number;
    published_at: Date | string | null;
  }>;

  return rows.map((r) => ({
    id: r.id,
    influencerId: r.influencer_id,
    videoId: r.video_id,
    videoUrl: r.video_url,
    description: r.description,
    contentType: r.content_type,
    playCount: Number(r.play_count),
    likeCount: Number(r.like_count),
    publishedAt: toDate(r.published_at),
  }));
}

/**
 * 取单个账号(详情页入口门)。user 必须已订阅,否则 null(→404);
 * 且 alias/groupId 换成订阅行上的私有视图值,下游展示零分支。
 */
export async function getInfluencer(viewer: ViewerScope, id: number) {
  const [row] = await db.select().from(influencers).where(eq(influencers.id, id)).limit(1);
  if (!row) return null;

  const [sub] = await db
    .select({ groupId: workspaceInfluencers.groupId, alias: workspaceInfluencers.alias })
    .from(workspaceInfluencers)
    .where(
      sql`${workspaceInfluencers.workspaceId} = ${viewer.workspaceId} AND ${workspaceInfluencers.influencerId} = ${id}`,
    )
    .limit(1);
  if (!sub) return null;
  return { ...row, alias: sub.alias, groupId: sub.groupId ?? row.groupId };
}

export async function listVideosByInfluencer(id: number, limit = 100) {
  return db
    .select()
    .from(videos)
    .where(eq(videos.influencerId, id))
    .orderBy(desc(videos.playCount))
    .limit(limit);
}

export async function listFetchLogsByInfluencer(id: number, limit = 10) {
  return db
    .select()
    .from(fetchLogs)
    .where(eq(fetchLogs.influencerId, id))
    .orderBy(desc(fetchLogs.startedAt))
    .limit(limit);
}

/**
 * 列出 viewer 的分组，每组带账号数（含 0 的组）。
 * admin=系统组(owner IS NULL,成员按 influencers.group_id);
 * user=自己的私有组(成员按订阅行 group_id)。
 * 用于账号管理页左栏 tab 和视频排行页 chip 多选。
 */
export async function listGroupsWithCount(
  viewer: ViewerScope,
): Promise<Array<{ id: number; name: string; sortOrder: number; accountCount: number }>> {
  const rows = (await db.execute(sql`
        SELECT g.id, g.name, g.sort_order, COUNT(wi.influencer_id)::int AS account_count
        FROM account_groups g
        LEFT JOIN workspace_influencers wi
          ON wi.group_id = g.id AND wi.workspace_id = ${viewer.workspaceId}
          AND wi.relation = 'own'
        WHERE g.workspace_id = ${viewer.workspaceId}
        GROUP BY g.id, g.name, g.sort_order
        ORDER BY g.sort_order ASC
      `)) as unknown as GroupCountRow[];
  return rows.map((r) => ({
    id: r.id,
    name: r.name,
    sortOrder: r.sort_order,
    accountCount: Number(r.account_count),
  }));
}

interface GroupCountRow {
  id: number;
  name: string;
  sort_order: number;
  account_count: number | string;
}

/** 指定订阅层的账号数（竞品页 header 计数 / TaskQueueCard totalAccounts）。 */
export async function countWorkspaceInfluencers(
  viewer: ViewerScope,
  relation: AccountRelation,
): Promise<number> {
  const [row] = await db
    .select({ c: sql<number>`COUNT(*)::int` })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.relation, relation),
      ),
    );
  return Number(row?.c ?? 0);
}

export interface CompetitorOverviewRow {
  id: number;
  platform: string;
  username: string;
  displayName: string | null;
  alias: string | null;
  followerCount: number;
  videoCount: number;
  lastFetchedAt: Date | null;
}

/** 竞品概览：按粉丝规模降序的当前快照（无历史曲线，follower_count 是抓取时点值）。 */
export async function listCompetitorOverview(
  viewer: ViewerScope,
): Promise<CompetitorOverviewRow[]> {
  const rows = (await db.execute(sql`
    SELECT
      i.id,
      i.platform,
      i.username,
      i.display_name,
      ${scopeAliasColSql(viewer)} AS alias,
      i.follower_count,
      i.video_count,
      i.last_fetched_at
    FROM influencers i
    ${scopeJoinSql(viewer, 'competitor')}
    ORDER BY i.follower_count DESC, i.id ASC
  `)) as unknown as Array<{
    id: number;
    platform: string;
    username: string;
    display_name: string | null;
    alias: string | null;
    follower_count: number | string;
    video_count: number | string;
    last_fetched_at: Date | string | null;
  }>;

  return rows.map((r) => ({
    id: r.id,
    platform: r.platform,
    username: r.username,
    displayName: r.display_name,
    alias: r.alias,
    followerCount: Number(r.follower_count),
    videoCount: Number(r.video_count),
    lastFetchedAt: toDate(r.last_fetched_at),
  }));
}
