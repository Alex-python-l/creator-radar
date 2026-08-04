'use client';

import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { useMemo } from 'react';

import { AccountsTableForm } from '@/components/accounts-table-form';
import { PlatformFilterChips } from '@/components/platform-filter-chips';
import { Card } from '@/components/ui';
import type { AccountRelation } from '@/lib/account-relation';
import {
  accountsQueryKey,
  fetchAccounts,
  type AccountsApiResponse,
  type AccountsQuery,
} from '@/lib/api/accounts';
import type { AccountsSearchParams } from '@/lib/url-params';
import type { Platform } from '@svm/shared/platforms';

export function AccountsPanel({
  parsed,
  locale,
  platformLabel,
  rangeLabel,
  rangeIsAll,
  groups,
  basePath,
  relation,
}: {
  parsed: AccountsSearchParams;
  locale: string;
  platformLabel: string;
  rangeLabel: string;
  rangeIsAll: boolean;
  groups: Array<{ id: number; name: string }>;
  /** 平台筛选链接的页面前缀,如 `/${locale}/monitor` 或 `/${locale}/competitors`。 */
  basePath: string;
  /** 订阅层,不传= 'own'(监控页)。竞品页传 'competitor'。 */
  relation?: AccountRelation;
}) {
  const queryClient = useQueryClient();

  const query: AccountsQuery = {
    group: parsed.group,
    platform: parsed.platform,
    range: parsed.range,
    expand: [...parsed.expand],
    relation,
  };

  const { data, isFetching } = useQuery<AccountsApiResponse>({
    queryKey: accountsQueryKey(query),
    queryFn: () => fetchAccounts(query),
    placeholderData: keepPreviousData,
  });

  function prefetchPlatform(p: Platform | 'all') {
    const q: AccountsQuery = { ...query, platform: p, expand: [] };
    queryClient.prefetchQuery({
      queryKey: accountsQueryKey(q),
      queryFn: () => fetchAccounts(q),
    });
  }

  // ISO string → Date（formatRelative 只接受 Date | null）。panel 层负责转换。
  const accounts = useMemo(
    () =>
      (data?.accounts ?? []).map((a) => ({
        ...a,
        latestFetchStartedAt: a.latestFetchStartedAt ? new Date(a.latestFetchStartedAt) : null,
        latestFetchFinishedAt: a.latestFetchFinishedAt ? new Date(a.latestFetchFinishedAt) : null,
      })),
    [data?.accounts],
  );
  const platformCounts = data?.platformCounts ?? {};
  const expandedVideos = useMemo(() => {
    const result: Record<
      number,
      Array<{
        id: number;
        influencerId: number;
        videoId: string;
        videoUrl: string | null;
        description: string | null;
        contentType: string;
        playCount: number;
        likeCount: number;
        publishedAt: Date | null;
      }>
    > = {};
    for (const [idStr, vs] of Object.entries(data?.expandedVideos ?? {})) {
      const id = Number(idStr);
      result[id] = vs.map((v) => ({
        ...v,
        influencerId: id,
        publishedAt: v.publishedAt ? new Date(v.publishedAt) : null,
      }));
    }
    return result;
  }, [data?.expandedVideos]);

  return (
    <div className={isFetching ? 'opacity-70 transition-opacity' : 'transition-opacity'}>
      <Card className="mb-4 space-y-3 px-5 py-3">
        <PlatformFilterChips
          current={parsed}
          locale={locale}
          label={platformLabel}
          platformCounts={platformCounts}
          basePath={basePath}
          onPrefetch={prefetchPlatform}
        />
      </Card>
      <AccountsTableForm
        platform={parsed.platform}
        accounts={accounts}
        parsed={parsed}
        videosByAccount={expandedVideos}
        groups={groups}
        numberingActive={data?.numberingActive ?? false}
        rangeLabel={rangeLabel}
        rangeIsAll={rangeIsAll}
      />
    </div>
  );
}
