import type { Platform } from '@svm/shared/platforms';
import type { AccountRelation } from '@/lib/account-relation';
import type { Range } from '@/lib/url-params';

/** GET /api/accounts 返回形状,route handler 与客户端 fetcher 共用类型。 */
export interface AccountsApiResponse {
  accounts: AccountRow[];
  platformCounts: Partial<Record<Platform, number>>;
  expandedVideos: Record<number, AccountVideoRow[]>;
  numberingActive: boolean;
}

export interface AccountRow {
  id: number;
  /** 竞品订阅行不进分组,group_id 为 NULL。 */
  groupId: number | null;
  displayNumber: number | null;
  platform: string;
  username: string;
  displayName: string | null;
  alias: string | null;
  accountStatus: 'active' | 'gone' | 'unknown';
  recentVideoCount: number;
  recentPlayCount: number;
  recentLikeCount: number;
  latestFetchStatus: string | null;
  latestFetchStartedAt: string | null; // ISO
  latestFetchFinishedAt: string | null;
  latestFetchError: string | null;
  latestFetchErrorCode: string | null;
  activeJobStatus: string | null;
}

export interface AccountVideoRow {
  id: number;
  videoId: string;
  videoUrl: string | null;
  description: string | null;
  contentType: string;
  playCount: number;
  likeCount: number;
  publishedAt: string | null; // ISO
}

export function deriveAccountStatus(input: {
  latestFetchStatus: string | null;
  latestFetchErrorCode: string | null;
  activeJobStatus?: string | null;
}): 'active' | 'gone' | 'unknown' {
  if (input.activeJobStatus) return 'unknown';
  if (input.latestFetchErrorCode === 'account_gone') return 'gone';
  if (input.latestFetchStatus === 'success') return 'active';
  return 'unknown';
}

export interface AccountsQuery {
  group?: number;
  platform?: Platform | 'all';
  range: Range;
  expand: number[];
  /** 订阅层,不传= 'own'(监控页)。竞品页传 'competitor'。 */
  relation?: AccountRelation;
}

export function accountsQueryKey(q: AccountsQuery): readonly unknown[] {
  return [
    'accounts',
    q.relation ?? 'own',
    q.group ?? null,
    q.platform ?? 'all',
    q.range,
    [...q.expand].sort((a, b) => a - b),
  ] as const;
}

export function buildAccountsUrl(q: AccountsQuery): string {
  const p = new URLSearchParams();
  if (q.relation === 'competitor') p.set('relation', 'competitor');
  if (q.group !== undefined) p.set('group', String(q.group));
  if (q.platform && q.platform !== 'all') p.set('platform', q.platform);
  if (q.range !== 'all') p.set('range', q.range);
  if (q.expand.length > 0)
    p.set('expand', [...q.expand].sort((a, b) => a - b).join(','));
  const s = p.toString();
  return s ? `/api/accounts?${s}` : '/api/accounts';
}

export async function fetchAccounts(q: AccountsQuery): Promise<AccountsApiResponse> {
  const res = await fetch(buildAccountsUrl(q), { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`accounts fetch failed: ${res.status}`);
  return res.json();
}
