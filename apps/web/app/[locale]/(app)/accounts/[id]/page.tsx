import type { Route } from 'next';
import { notFound } from 'next/navigation';
import { connection } from 'next/server';
import { getTranslations } from 'next-intl/server';

import { AliasEditor } from '@/components/alias-editor';
import { BackLink } from '@/components/back-link';
import { Button, Card } from '@/components/ui';
import { PlatformMark, formatCompact, formatDate, formatRelative } from '@/components/format';
import { RangeFilter } from '@/components/range-filter';
import { VideosTable } from '@/components/videos-table';
import { triggerFetchForm } from '@/lib/actions/accounts';
import { formatAccountDisplayName, formatAccountRawIdentifier } from '@/lib/account-display';
import { requireViewer } from '@/lib/auth/viewer';
import {
  getInfluencer,
  listFetchLogsByInfluencer,
} from '@/lib/queries/influencers';
import { listVideos } from '@/lib/queries/videos';
import {
  parseVideosSearchParams,
  rangeToSince,
  type VideosSearchParams,
} from '@/lib/url-params';
import { isPlatform, platformProfileUrl } from '@svm/shared/platforms';

export const dynamic = 'force-dynamic';

export default async function AccountDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();

  const { locale, id: idParam } = await params;
  const t = await getTranslations({ locale, namespace: 'accountDetail' });
  const fetchStatus = await getTranslations({ locale, namespace: 'fetchStatus' });
  const id = Number(idParam);
  if (!Number.isFinite(id)) notFound();

  const viewer = await requireViewer();
  // user 未订阅的账号在这里就是不存在(404),不泄露全局库内容。
  const account = await getInfluencer(viewer, id);
  if (!account) notFound();
  const accountLabel = formatAccountDisplayName(account);
  const rawIdentifier = formatAccountRawIdentifier(account);

  const sp = await searchParams;
  // 账号详情页：platform 锁定为该账号的，sort/order/range 跟随 URL
  const parsed = parseVideosSearchParams(sp);
  const effective: VideosSearchParams = {
    ...parsed,
    platform: isPlatform(account.platform) ? account.platform : 'tiktok',
  };
  const since = rangeToSince(effective.range);

  const [videoList, logs] = await Promise.all([
    listVideos(viewer, {
      influencerIds: [id],
      since,
      sortBy: effective.sort,
      order: effective.order,
      limit: 100,
    }),
    listFetchLogsByInfluencer(id, 5),
  ]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-10">
      <BackLink
        href={`/${locale}/monitor` as Route}
        className="mb-4 inline-flex items-center text-sm text-[color:var(--color-muted-foreground)] hover:text-[color:var(--color-foreground)]"
      >
        {t('back')}
      </BackLink>

      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <PlatformMark platform={account.platform} />
            <h1 className="text-2xl font-bold tracking-tight">
              {isPlatform(account.platform) ? (
                <a
                  href={platformProfileUrl(account.platform, account.username)}
                  target="_blank"
                  rel="noreferrer"
                  className="hover:underline"
                  title={t('openProfile')}
                >
                  {accountLabel}
                </a>
              ) : (
                <>{accountLabel}</>
              )}
            </h1>
          </div>
          {accountLabel !== rawIdentifier && (
            <div className="mt-1 text-sm text-[color:var(--color-muted-foreground)]">
              {rawIdentifier}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-4 text-sm">
            <span>
              <span className="text-[color:var(--color-muted-foreground)]">{t('followers')}</span>{' '}
              <span className="font-semibold tabular-nums">
                {formatCompact(account.followerCount, locale)}
              </span>
            </span>
            <span>
              <span className="text-[color:var(--color-muted-foreground)]">{t('following')}</span>{' '}
              <span className="font-semibold tabular-nums">
                {formatCompact(account.followingCount, locale)}
              </span>
            </span>
            <span>
              <span className="text-[color:var(--color-muted-foreground)]">{t('totalVideos')}</span>{' '}
              <span className="font-semibold tabular-nums">
                {formatCompact(account.videoCount, locale)}
              </span>
            </span>
          </div>
          <div className="mt-2 text-xs text-[color:var(--color-muted-foreground)]">
            {t('lastFetched', {
              time: formatRelative(account.lastFetchedAt, locale),
              count: videoList.length,
            })}
          </div>
          <div className="mt-1 text-xs text-[color:var(--color-muted-foreground)]">
            {t('importedAt', { date: formatDate(account.createdAt, locale) })}
          </div>
        </div>
        <form action={triggerFetchForm.bind(null, id)}>
          <Button type="submit">{t('fetchNow')}</Button>
        </form>
      </div>

      <AliasEditor accountId={id} initialAlias={account.alias} />

      {logs.length > 0 && (
        <Card className="mb-6 p-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[color:var(--color-muted-foreground)]">
            {t('recentFetchLogs')}
          </h3>
          <div className="space-y-1.5 text-sm">
            {logs.map((log) => (
              <div key={log.id} className="flex items-center gap-3">
                <span
                  className={`inline-flex h-5 items-center rounded px-2 text-xs font-semibold ${
                    log.status === 'success'
                      ? 'bg-[hsl(142_71%_92%)] text-[hsl(142_71%_30%)]'
                      : 'bg-[hsl(0_84%_94%)] text-[color:var(--color-destructive)]'
                  }`}
                >
                  {log.status === 'success' || log.status === 'failed'
                    ? fetchStatus(log.status)
                    : log.status}
                </span>
                <span className="text-[color:var(--color-muted-foreground)]">
                  {formatRelative(log.startedAt, locale)}
                </span>
                {log.status === 'success' ? (
                  <span className="text-[color:var(--color-muted-foreground)]">
                    {t('logSuccess', { found: log.foundCount, created: log.newCount })}
                  </span>
                ) : (
                  <span className="text-[color:var(--color-destructive)] truncate">
                    {log.errorMessage}
                  </span>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="mb-6 p-4">
        <RangeFilter basePath={`/${locale}/accounts/${id}`} current={effective} />
      </Card>

      <Card>
        <div className="border-b border-[color:var(--color-border)] px-6 py-4">
          <h2 className="text-sm font-semibold">
            {t('videoList')} <span className="text-[color:var(--color-muted-foreground)]">·</span>{' '}
            <span className="text-[color:var(--color-muted-foreground)]">
              {videoList.length}
            </span>
          </h2>
        </div>
        <VideosTable
          videos={videoList}
          current={effective}
          basePath={`/${locale}/accounts/${id}`}
          showAccountCol={false}
        />
      </Card>
    </div>
  );
}
