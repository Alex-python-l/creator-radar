import { NextResponse, type NextRequest } from 'next/server';

import { hasGroupAccountNumbers } from '@/lib/actions/account-numbering';
import type { AccountRelation } from '@/lib/account-relation';
import { deriveAccountStatus, type AccountsApiResponse } from '@/lib/api/accounts';
import { getViewer } from '@/lib/auth/viewer';
import {
  listInfluencersWithRecentCount,
  listPlatformAccountCounts,
  listRecentVideosForAccounts,
} from '@/lib/queries/influencers';
import { rangeToSince, type Range } from '@/lib/url-params';
import { isPlatform, type Platform } from '@svm/shared/platforms';

const RANGES = ['today', '7d', '30d', '90d', 'all'] as const;
const MAX_EXPANDED_ACCOUNTS = 20;
const EXPANDED_VIDEOS_PER_ACCOUNT = 20;
function pickRange(raw: string | null): Range {
  return (RANGES as readonly string[]).includes(raw ?? '') ? (raw as Range) : 'all';
}

function pickGroupId(raw: string | null): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

function pickPlatform(raw: string | null): Platform | undefined {
  if (!raw || raw === 'all') return undefined;
  return isPlatform(raw) ? raw : undefined;
}

/** 未知值一律回落 'own' —— 监控页老客户端不带参数,默认自有层。 */
function pickRelation(raw: string | null): AccountRelation {
  return raw === 'competitor' ? 'competitor' : 'own';
}

function pickExpand(raw: string | null): number[] {
  if (!raw) return [];
  const out: number[] = [];
  for (const tok of raw.split(',')) {
    const n = Number(tok);
    if (Number.isInteger(n) && n > 0 && !out.includes(n)) out.push(n);
    if (out.length >= MAX_EXPANDED_ACCOUNTS) break;
  }
  return out;
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const viewer = await getViewer();
  if (!viewer) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const sp = req.nextUrl.searchParams;
  const groupId = pickGroupId(sp.get('group'));
  const platform = pickPlatform(sp.get('platform'));
  const range = pickRange(sp.get('range'));
  const expand = pickExpand(sp.get('expand'));
  const relation = pickRelation(sp.get('relation'));
  const since = rangeToSince(range);
  const numberingActive =
    groupId !== undefined ? await hasGroupAccountNumbers(groupId) : false;

  const [accounts, platformCounts, expandedRaw] = await Promise.all([
    listInfluencersWithRecentCount(viewer, {
      since,
      groupId,
      platform,
      useGroupNumbering: numberingActive,
      relation,
    }),
    listPlatformAccountCounts(viewer, groupId, relation),
    expand.length > 0
      ? listRecentVideosForAccounts(viewer, expand, {
          since,
          perAccountLimit: EXPANDED_VIDEOS_PER_ACCOUNT,
          relation,
        })
      : Promise.resolve([]),
  ]);

  const expandedVideos: Record<number, AccountsApiResponse['expandedVideos'][number]> = {};
  for (const v of expandedRaw) {
    const arr = expandedVideos[v.influencerId] ?? [];
    arr.push({
      id: v.id,
      videoId: v.videoId,
      videoUrl: v.videoUrl,
      description: v.description,
      contentType: v.contentType,
      playCount: v.playCount,
      likeCount: v.likeCount,
      publishedAt: v.publishedAt ? v.publishedAt.toISOString() : null,
    });
    expandedVideos[v.influencerId] = arr;
  }

  const body: AccountsApiResponse = {
    accounts: accounts.map((a) => ({
      id: a.id,
      groupId: a.groupId,
      displayNumber: a.displayNumber,
      platform: a.platform,
      username: a.username,
      displayName: a.displayName,
      alias: a.alias,
      accountStatus: deriveAccountStatus({
        latestFetchStatus: a.latestFetchStatus,
        latestFetchErrorCode: a.latestFetchErrorCode,
        activeJobStatus: a.activeJobStatus,
      }),
      recentVideoCount: a.recentVideoCount,
      recentPlayCount: a.recentPlayCount,
      recentLikeCount: a.recentLikeCount,
      latestFetchStatus: a.latestFetchStatus,
      latestFetchStartedAt: a.latestFetchStartedAt
        ? a.latestFetchStartedAt.toISOString()
        : null,
      latestFetchFinishedAt: a.latestFetchFinishedAt
        ? a.latestFetchFinishedAt.toISOString()
        : null,
      latestFetchError: a.latestFetchError,
      latestFetchErrorCode: a.latestFetchErrorCode,
      activeJobStatus: a.activeJobStatus,
    })),
    platformCounts,
    expandedVideos,
    numberingActive,
  };

  return NextResponse.json(body, {
    headers: { 'Cache-Control': 'private, no-store' },
  });
}
