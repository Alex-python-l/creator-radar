import { sql } from 'drizzle-orm';

import { db } from '@/lib/db';
import {
  analysisSince,
  buildMonitorAnalysisSnapshot,
  type MonitorAnalysisAccountInput,
  type MonitorAnalysisFetchInput,
  type MonitorAnalysisPlatform,
  type MonitorAnalysisRange,
  type MonitorAnalysisSnapshot,
  type MonitorAnalysisVideoInput,
} from '@/lib/monitor-analysis';
import { isPlatform } from '@svm/shared/platforms';

export class MonitorAnalysisGroupNotFoundError extends Error {}

function toDate(value: Date | string | null): Date | null {
  if (value == null) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export async function getMonitorAnalysisSnapshot(
  viewer: { workspaceId: number },
  options: {
    groupId?: number;
    platform: MonitorAnalysisPlatform;
    range: MonitorAnalysisRange;
    now?: Date;
  },
): Promise<MonitorAnalysisSnapshot> {
  if (options.groupId !== undefined) {
    const groups = (await db.execute(sql`
      SELECT id FROM account_groups
      WHERE id = ${options.groupId} AND workspace_id = ${viewer.workspaceId}
      LIMIT 1
    `)) as unknown as Array<{ id: number }>;
    if (groups.length === 0) throw new MonitorAnalysisGroupNotFoundError();
  }

  const now = options.now ?? new Date();
  const since = analysisSince(now, options.range);
  const groupFilter = options.groupId === undefined ? sql`` : sql`AND wi.group_id = ${options.groupId}`;
  const platformFilter = options.platform === 'all' ? sql`` : sql`AND i.platform = ${options.platform}`;
  const accountRows = (await db.execute(sql`
    SELECT
      i.id, i.platform, i.username, i.display_name, wi.alias,
      wi.created_at AS subscription_created_at,
      lf.status AS latest_fetch_status,
      lf.started_at AS latest_fetch_started_at,
      lf.finished_at AS latest_fetch_finished_at,
      lf.error_message AS latest_fetch_error,
      lf.error_code AS latest_fetch_error_code,
      ls.finished_at AS latest_success_at,
      lj.status AS latest_job_status,
      lj.last_error AS latest_job_error,
      lj.job_at AS latest_job_at
    FROM workspace_influencers wi
    JOIN influencers i ON i.id = wi.influencer_id
    LEFT JOIN LATERAL (
      SELECT status, started_at, finished_at, error_message, error_code
      FROM fetch_logs
      WHERE influencer_id = i.id
      ORDER BY id DESC LIMIT 1
    ) lf ON TRUE
    LEFT JOIN LATERAL (
      SELECT COALESCE(finished_at, started_at) AS finished_at
      FROM fetch_logs
      WHERE influencer_id = i.id AND status = 'success'
      ORDER BY id DESC LIMIT 1
    ) ls ON TRUE
    LEFT JOIN LATERAL (
      SELECT status, last_error, COALESCE(updated_at, created_at) AS job_at
      FROM jobs
      WHERE kind = 'account.fetch'
        AND payload->>'influencerId' ~ '^[0-9]+$'
        AND (payload->>'influencerId')::int = i.id
      ORDER BY id DESC LIMIT 1
    ) lj ON TRUE
    WHERE wi.workspace_id = ${viewer.workspaceId}
      AND wi.relation = 'own'
      ${groupFilter}
      ${platformFilter}
    ORDER BY i.id
  `)) as unknown as Array<{
    id: number;
    platform: string;
    username: string;
    display_name: string | null;
    alias: string | null;
    subscription_created_at: Date | string;
    latest_fetch_status: string | null;
    latest_fetch_started_at: Date | string | null;
    latest_fetch_finished_at: Date | string | null;
    latest_fetch_error: string | null;
    latest_fetch_error_code: string | null;
    latest_success_at: Date | string | null;
    latest_job_status: string | null;
    latest_job_error: string | null;
    latest_job_at: Date | string | null;
  }>;

  const accounts: MonitorAnalysisAccountInput[] = accountRows.flatMap((row) => {
    if (!isPlatform(row.platform)) return [];
    return [{
      id: Number(row.id),
      platform: row.platform,
      username: row.username,
      displayName: row.display_name,
      alias: row.alias,
      subscriptionCreatedAt: toDate(row.subscription_created_at) ?? now,
      latestFetchStatus: row.latest_fetch_status,
      latestFetchStartedAt: toDate(row.latest_fetch_started_at),
      latestFetchFinishedAt: toDate(row.latest_fetch_finished_at),
      latestFetchError: row.latest_fetch_error,
      latestFetchErrorCode: row.latest_fetch_error_code,
      latestSuccessAt: toDate(row.latest_success_at),
      latestJobStatus: row.latest_job_status,
      latestJobError: row.latest_job_error,
      latestJobAt: toDate(row.latest_job_at),
    }];
  });
  const ids = accounts.map((account) => account.id);
  if (ids.length === 0) {
    return buildMonitorAnalysisSnapshot({ ...options, now, accounts: [], fetches: [], videos: [] });
  }

  const idList = sql.join(ids.map((id) => sql`${id}`), sql`, `);
  const fetchRows = (await db.execute(sql`
    SELECT influencer_id, status, error_code, error_message, started_at, finished_at
    FROM fetch_logs
    WHERE influencer_id IN (${idList}) AND started_at >= ${since.toISOString()}
    ORDER BY started_at DESC
  `)) as unknown as Array<{
    influencer_id: number;
    status: string;
    error_code: string | null;
    error_message: string | null;
    started_at: Date | string;
    finished_at: Date | string | null;
  }>;
  const fetches: MonitorAnalysisFetchInput[] = fetchRows.flatMap((row) => {
    const startedAt = toDate(row.started_at);
    return startedAt ? [{
      influencerId: Number(row.influencer_id),
      status: row.status,
      errorCode: row.error_code,
      errorMessage: row.error_message,
      startedAt,
      finishedAt: toDate(row.finished_at),
    }] : [];
  });

  const videoRows = (await db.execute(sql`
    SELECT id, influencer_id, platform, video_id, video_url, description,
      play_count, like_count, comment_count, share_count, content_type, published_at
    FROM videos
    WHERE influencer_id IN (${idList}) AND published_at >= ${since.toISOString()}
    ORDER BY published_at DESC
  `)) as unknown as Array<{
    id: number;
    influencer_id: number;
    platform: string;
    video_id: string;
    video_url: string | null;
    description: string | null;
    play_count: number;
    like_count: number;
    comment_count: number;
    share_count: number;
    content_type: string;
    published_at: Date | string | null;
  }>;
  const videos: MonitorAnalysisVideoInput[] = videoRows.flatMap((row) => {
    const publishedAt = toDate(row.published_at);
    if (!isPlatform(row.platform)) return [];
    return [{
      id: Number(row.id),
      influencerId: Number(row.influencer_id),
      platform: row.platform,
      contentType: row.content_type,
      url: row.video_url,
      description: row.description,
      playCount: Number(row.play_count),
      likeCount: Number(row.like_count),
      commentCount: Number(row.comment_count),
      shareCount: Number(row.share_count),
      publishedAt,
    }];
  });

  return buildMonitorAnalysisSnapshot({ ...options, now, accounts, fetches, videos });
}
