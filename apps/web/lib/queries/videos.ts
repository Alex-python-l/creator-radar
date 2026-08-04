import { type SQL, and, asc, desc, eq, gte, inArray } from 'drizzle-orm';

import { db } from '@/lib/db';
import { influencers, videos, workspaceInfluencers } from '@/lib/db/schema';
import type { AccountRelation } from '@/lib/account-relation';
import type { ViewerScope } from '@/lib/queries/scope';
import type { SortCol, Order } from '@/lib/url-params';
import type { Platform } from '@svm/shared/platforms';

export interface VideoWithAccount {
  id: number;
  videoId: string;
  videoUrl: string | null;
  description: string | null;
  thumbnailUrl: string | null;
  durationSeconds: number | null;
  playCount: number;
  likeCount: number;
  commentCount: number;
  shareCount: number;
  contentType: string;
  publishedAt: Date | null;
  capturedAt: Date;
  // 关联账号
  influencerId: number;
  influencerUsername: string;
  influencerDisplayName: string | null;
  influencerAlias: string | null;
  influencerPlatform: string;
}

export async function listVideos(
  viewer: ViewerScope,
  opts: {
    platform?: Platform;
    influencerIds?: number[];
    groupIds?: number[];
    since?: Date;
    sortBy: SortCol;
    order: Order;
    limit?: number;
    /** 订阅层过滤。⚠ 与其他查询不同，这里**没有默认值**：账号详情页
     *  (accounts/[id]) 对 own/competitor 两层账号都要能列视频，不传即不过滤。
     *  列表页必须显式传：videos 页传 'own'，竞品页传 'competitor'。 */
    relation?: AccountRelation;
  },
): Promise<VideoWithAccount[]> {
  const where: SQL[] = [];
  if (opts.platform) where.push(eq(videos.platform, opts.platform));
  if (opts.influencerIds && opts.influencerIds.length > 0)
    where.push(inArray(videos.influencerId, opts.influencerIds));
  if (opts.groupIds && opts.groupIds.length > 0)
    where.push(inArray(workspaceInfluencers.groupId, opts.groupIds));
  if (opts.since) where.push(gte(videos.publishedAt, opts.since));

  const sortColumn = {
    play_count: videos.playCount,
    like_count: videos.likeCount,
    comment_count: videos.commentCount,
    share_count: videos.shareCount,
    published_at: videos.publishedAt,
  }[opts.sortBy];

  const orderFn = opts.order === 'asc' ? asc : desc;

  // user 的可见性/组轴/别名全部来自订阅行:内连接即隔离。
  const base = db
    .select({
      id: videos.id,
      videoId: videos.videoId,
      videoUrl: videos.videoUrl,
      description: videos.description,
      thumbnailUrl: videos.thumbnailUrl,
      durationSeconds: videos.durationSeconds,
      playCount: videos.playCount,
      likeCount: videos.likeCount,
      commentCount: videos.commentCount,
      shareCount: videos.shareCount,
      contentType: videos.contentType,
      publishedAt: videos.publishedAt,
      capturedAt: videos.capturedAt,
      influencerId: videos.influencerId,
      influencerUsername: influencers.username,
      influencerDisplayName: influencers.displayName,
      influencerAlias: workspaceInfluencers.alias,
      influencerPlatform: influencers.platform,
    })
    .from(videos)
    .innerJoin(influencers, eq(videos.influencerId, influencers.id));

  const scoped = base.innerJoin(
    workspaceInfluencers,
    and(
      eq(workspaceInfluencers.influencerId, influencers.id),
      eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
      ...(opts.relation ? [eq(workspaceInfluencers.relation, opts.relation)] : []),
    ),
  );

  const rows = await scoped
    .where(where.length > 0 ? and(...where) : undefined)
    .orderBy(orderFn(sortColumn))
    .limit(opts.limit ?? 200);

  return rows;
}
