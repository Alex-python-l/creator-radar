import type { Platform } from '@svm/shared/platforms';

export type MonitorAnalysisRange = 'today' | '7d' | '30d';
export type MonitorAnalysisPlatform = Platform | 'all';

export interface MonitorAnalysisAccountInput {
  id: number;
  platform: Platform;
  username: string;
  displayName: string | null;
  alias: string | null;
  subscriptionCreatedAt: Date;
  latestFetchStatus: string | null;
  latestFetchStartedAt: Date | null;
  latestFetchFinishedAt: Date | null;
  latestFetchError: string | null;
  latestFetchErrorCode: string | null;
  latestSuccessAt: Date | null;
  latestJobStatus: string | null;
  latestJobError: string | null;
  latestJobAt: Date | null;
}

export interface MonitorAnalysisFetchInput {
  influencerId: number;
  status: string;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: Date;
  finishedAt: Date | null;
}

export interface MonitorAnalysisVideoInput {
  id: number;
  influencerId: number;
  platform: Platform;
  contentType: string;
  url: string | null;
  description: string | null;
  playCount: number;
  likeCount: number;
  commentCount: number;
  shareCount: number;
  publishedAt: Date | null;
}

export interface AnalysisAccountRef {
  id: number;
  platform: Platform;
  username: string;
  name: string;
}

export interface HealthAccount extends AnalysisAccountRef {
  reason: 'failed' | 'dead' | 'stale' | 'never_fetched';
  errorCode: string | null;
  errorMessage: string | null;
  lastSuccessAt: string | null;
}

export interface PerformanceVideo {
  id: number;
  account: AnalysisAccountRef;
  contentType: string;
  url: string | null;
  description: string | null;
  publishedAt: string;
  primaryMetric: number;
  engagementRate: number | null;
  playCount: number;
  engagementCount: number;
}

export interface MonitorAnalysisSnapshot {
  generatedAt: string;
  scope: {
    groupId: number | null;
    platform: MonitorAnalysisPlatform;
    range: MonitorAnalysisRange;
    since: string;
  };
  health: {
    status: 'healthy' | 'processing' | 'issues';
    totalAccounts: number;
    successAttempts: number;
    failedAttempts: number;
    successRate: number | null;
    currentIssues: HealthAccount[];
    processing: Array<AnalysisAccountRef & { status: string }>;
    recovered: Array<AnalysisAccountRef & { recoveredAt: string }>;
    errorBreakdown: Array<{ code: string; count: number }>;
  };
  publishing: {
    postedCount: number;
    silentCount: number;
    uncertainCount: number;
    postedAccounts: Array<AnalysisAccountRef & { count: number; samples: PerformanceVideo[] }>;
    silentAccounts: AnalysisAccountRef[];
    uncertainAccounts: Array<AnalysisAccountRef & { reason: string }>;
  };
  performance: {
    cohorts: Array<{
      platform: Platform;
      contentType: string;
      metric: 'daily_plays' | 'daily_engagement';
      videoCount: number;
      enoughVideoSample: boolean;
      topVideos: PerformanceVideo[];
      bottomVideos: PerformanceVideo[];
      accountCount: number;
      enoughAccountSample: boolean;
      topAccounts: Array<AnalysisAccountRef & { contentCount: number; medianMetric: number }>;
      bottomAccounts: Array<AnalysisAccountRef & { contentCount: number; medianMetric: number }>;
    }>;
  };
}

const STALE_MS = 36 * 60 * 60 * 1000;
const MIN_CONTENT_AGE_MS = 24 * 60 * 60 * 1000;

export function analysisSince(now: Date, range: MonitorAnalysisRange): Date {
  const days = range === 'today' ? 1 : range === '7d' ? 7 : 30;
  return new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
}

function accountRef(account: MonitorAnalysisAccountInput): AnalysisAccountRef {
  return {
    id: account.id,
    platform: account.platform,
    username: account.username,
    name: account.alias || account.displayName || account.username,
  };
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

function rankedEnds<T>(items: T[], enough: boolean): { top: T[]; bottom: T[] } {
  if (!enough) return { top: items.slice(0, 5), bottom: [] };
  const count = Math.max(1, Math.ceil(items.length / 4));
  return { top: items.slice(0, Math.min(count, 5)), bottom: items.slice(-Math.min(count, 5)).reverse() };
}

function performanceVideo(
  video: MonitorAnalysisVideoInput,
  account: MonitorAnalysisAccountInput,
  now: Date,
): PerformanceVideo | null {
  if (!video.publishedAt) return null;
  const ageDays = Math.max(1, (now.getTime() - video.publishedAt.getTime()) / 86_400_000);
  const engagementCount = video.likeCount + video.commentCount + video.shareCount;
  const isPlayable = video.contentType.toLowerCase() === 'video' || video.contentType.toLowerCase() === 'reel';
  return {
    id: video.id,
    account: accountRef(account),
    contentType: video.contentType,
    url: video.url,
    description: video.description,
    publishedAt: video.publishedAt.toISOString(),
    primaryMetric: isPlayable ? video.playCount / ageDays : engagementCount / ageDays,
    engagementRate: isPlayable ? engagementCount / Math.max(video.playCount, 1) : null,
    playCount: video.playCount,
    engagementCount,
  };
}

export function buildMonitorAnalysisSnapshot(input: {
  now: Date;
  groupId?: number;
  platform: MonitorAnalysisPlatform;
  range: MonitorAnalysisRange;
  accounts: MonitorAnalysisAccountInput[];
  fetches: MonitorAnalysisFetchInput[];
  videos: MonitorAnalysisVideoInput[];
}): MonitorAnalysisSnapshot {
  const { now, accounts, fetches } = input;
  const since = analysisSince(now, input.range);
  const accountById = new Map(accounts.map((account) => [account.id, account]));
  const currentIssues: HealthAccount[] = [];
  const processing: MonitorAnalysisSnapshot['health']['processing'] = [];
  const recovered: MonitorAnalysisSnapshot['health']['recovered'] = [];
  const issueReason = new Map<number, string>();

  for (const account of accounts) {
    const ref = accountRef(account);
    if (account.latestJobStatus === 'queued' || account.latestJobStatus === 'running') {
      processing.push({ ...ref, status: account.latestJobStatus });
    }
    let reason: HealthAccount['reason'] | null = null;
    let errorCode = account.latestFetchErrorCode;
    let errorMessage = account.latestFetchError;
    const deadAfterSuccess = account.latestJobStatus === 'dead' && (
      !account.latestSuccessAt ||
      !account.latestJobAt ||
      account.latestJobAt >= account.latestSuccessAt
    );
    if (deadAfterSuccess) {
      reason = 'dead';
      errorCode = 'dead_letter';
      errorMessage = account.latestJobError;
    } else if (account.latestFetchStatus === 'failed') {
      reason = 'failed';
    } else if (!account.latestSuccessAt) {
      if (now.getTime() - account.subscriptionCreatedAt.getTime() > STALE_MS) reason = 'never_fetched';
    } else if (now.getTime() - account.latestSuccessAt.getTime() > STALE_MS) {
      reason = 'stale';
    }
    if (reason) {
      issueReason.set(account.id, reason);
      currentIssues.push({
        ...ref,
        reason,
        errorCode,
        errorMessage,
        lastSuccessAt: account.latestSuccessAt?.toISOString() ?? null,
      });
      continue;
    }
    const failedBeforeSuccess = fetches.some(
      (fetch) =>
        fetch.influencerId === account.id &&
        fetch.status === 'failed' &&
        account.latestSuccessAt !== null &&
        fetch.startedAt < account.latestSuccessAt,
    );
    const deadBeforeSuccess = account.latestJobStatus === 'dead' &&
      account.latestJobAt !== null &&
      account.latestSuccessAt !== null &&
      account.latestJobAt >= since &&
      account.latestJobAt < account.latestSuccessAt;
    if ((failedBeforeSuccess || deadBeforeSuccess) && account.latestSuccessAt) {
      recovered.push({ ...ref, recoveredAt: account.latestSuccessAt.toISOString() });
    }
  }

  const successAttempts = fetches.filter((fetch) => fetch.status === 'success').length;
  const failedFetches = fetches.filter((fetch) => fetch.status === 'failed');
  const failedAttempts = failedFetches.length;
  const attempts = successAttempts + failedAttempts;
  const errors = new Map<string, number>();
  for (const fetch of failedFetches) {
    const code = fetch.errorCode || 'unknown';
    errors.set(code, (errors.get(code) || 0) + 1);
  }

  const videosByAccount = new Map<number, MonitorAnalysisVideoInput[]>();
  for (const video of input.videos) {
    if (!video.publishedAt || video.publishedAt < since) continue;
    const existing = videosByAccount.get(video.influencerId) || [];
    existing.push(video);
    videosByAccount.set(video.influencerId, existing);
  }
  const postedAccounts: MonitorAnalysisSnapshot['publishing']['postedAccounts'] = [];
  const silentAccounts: AnalysisAccountRef[] = [];
  const uncertainAccounts: MonitorAnalysisSnapshot['publishing']['uncertainAccounts'] = [];
  for (const account of accounts) {
    const videos = (videosByAccount.get(account.id) || []).sort(
      (a, b) => (b.publishedAt?.getTime() || 0) - (a.publishedAt?.getTime() || 0),
    );
    if (videos.length > 0) {
      postedAccounts.push({
        ...accountRef(account),
        count: videos.length,
        samples: videos.slice(0, 3).flatMap((video) => {
          const item = performanceVideo(video, account, now);
          return item ? [item] : [];
        }),
      });
    } else if (issueReason.has(account.id)) {
      uncertainAccounts.push({ ...accountRef(account), reason: issueReason.get(account.id)! });
    } else if (
      account.latestJobStatus === 'queued' ||
      account.latestJobStatus === 'running' ||
      (!account.latestSuccessAt && now.getTime() - account.subscriptionCreatedAt.getTime() <= STALE_MS)
    ) {
      uncertainAccounts.push({ ...accountRef(account), reason: account.latestJobStatus || 'pending_first_fetch' });
    } else {
      silentAccounts.push(accountRef(account));
    }
  }

  const eligible = input.videos.filter(
    (video) =>
      video.publishedAt &&
      video.publishedAt >= since &&
      now.getTime() - video.publishedAt.getTime() >= MIN_CONTENT_AGE_MS,
  );
  const grouped = new Map<string, MonitorAnalysisVideoInput[]>();
  for (const video of eligible) {
    const key = `${video.platform}\u0000${video.contentType.toLowerCase()}`;
    const items = grouped.get(key) || [];
    items.push(video);
    grouped.set(key, items);
  }
  const cohorts: MonitorAnalysisSnapshot['performance']['cohorts'] = [];
  for (const videos of grouped.values()) {
    const first = videos[0];
    const playable = first.contentType.toLowerCase() === 'video' || first.contentType.toLowerCase() === 'reel';
    const ranked = videos
      .flatMap((video) => {
        const account = accountById.get(video.influencerId);
        const item = account ? performanceVideo(video, account, now) : null;
        return item ? [item] : [];
      })
      .sort((a, b) => b.primaryMetric - a.primaryMetric || b.engagementCount - a.engagementCount);
    const enoughVideoSample = ranked.length >= 4;
    const videoEnds = rankedEnds(ranked, enoughVideoSample);
    const perAccount = new Map<number, PerformanceVideo[]>();
    for (const item of ranked) {
      const items = perAccount.get(item.account.id) || [];
      items.push(item);
      perAccount.set(item.account.id, items);
    }
    const accountsRanked = [...perAccount.values()]
      .filter((items) => items.length >= 2)
      .map((items) => ({
        ...items[0].account,
        contentCount: items.length,
        medianMetric: median(items.map((item) => item.primaryMetric)),
      }))
      .sort((a, b) => b.medianMetric - a.medianMetric);
    const enoughAccountSample = accountsRanked.length >= 4;
    const accountEnds = rankedEnds(accountsRanked, enoughAccountSample);
    cohorts.push({
      platform: first.platform,
      contentType: first.contentType,
      metric: playable ? 'daily_plays' : 'daily_engagement',
      videoCount: ranked.length,
      enoughVideoSample,
      topVideos: videoEnds.top,
      bottomVideos: videoEnds.bottom,
      accountCount: accountsRanked.length,
      enoughAccountSample,
      topAccounts: accountEnds.top,
      bottomAccounts: accountEnds.bottom,
    });
  }

  return {
    generatedAt: now.toISOString(),
    scope: {
      groupId: input.groupId ?? null,
      platform: input.platform,
      range: input.range,
      since: since.toISOString(),
    },
    health: {
      status: currentIssues.length > 0 ? 'issues' : processing.length > 0 ? 'processing' : 'healthy',
      totalAccounts: accounts.length,
      successAttempts,
      failedAttempts,
      successRate: attempts > 0 ? successAttempts / attempts : null,
      currentIssues,
      processing,
      recovered,
      errorBreakdown: [...errors.entries()]
        .map(([code, count]) => ({ code, count }))
        .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code)),
    },
    publishing: {
      postedCount: postedAccounts.length,
      silentCount: silentAccounts.length,
      uncertainCount: uncertainAccounts.length,
      postedAccounts,
      silentAccounts,
      uncertainAccounts,
    },
    performance: { cohorts },
  };
}
