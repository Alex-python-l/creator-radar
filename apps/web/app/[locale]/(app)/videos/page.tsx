import { connection } from 'next/server';
import {getTranslations} from 'next-intl/server';

import { Card } from '@/components/ui';
import { GroupsFilter } from '@/components/groups-filter';
import { PlatformFilter } from '@/components/platform-filter';
import { RangeFilter } from '@/components/range-filter';
import { VideosTable } from '@/components/videos-table';
import { requireViewer } from '@/lib/auth/viewer';
import { listGroupsWithCount } from '@/lib/queries/influencers';
import { listVideos } from '@/lib/queries/videos';
import { parseVideosSearchParams, rangeToSince } from '@/lib/url-params';

export const dynamic = 'force-dynamic';

export default async function VideosPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();

  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'videosPage' });
  const sp = await searchParams;
  const parsed = parseVideosSearchParams(sp);
  const since = rangeToSince(parsed.range);

  const viewer = await requireViewer();
  const [list, groups] = await Promise.all([
    listVideos(viewer, {
      platform: parsed.platform,
      groupIds: parsed.groups.length > 0 ? parsed.groups : undefined,
      since,
      sortBy: parsed.sort,
      order: parsed.order,
      limit: 200,
      relation: 'own',
    }),
    listGroupsWithCount(viewer),
  ]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-10">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">{t('title')}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-muted-foreground)]">
          {t('description')}
        </p>
      </div>

      <Card className="mb-6 p-4 space-y-3">
        <PlatformFilter basePath={`/${locale}/videos`} current={parsed} />
        <RangeFilter basePath={`/${locale}/videos`} current={parsed} />
        <GroupsFilter
          basePath={`/${locale}/videos`}
          current={parsed}
          groups={groups.map((g) => ({ id: g.id, name: g.name, accountCount: g.accountCount }))}
        />
      </Card>

      <Card>
        <div className="border-b border-[color:var(--color-border)] px-6 py-4">
          <h2 className="text-sm font-semibold">
            {t('listTitle')} <span className="text-[color:var(--color-muted-foreground)]">·</span>{' '}
            <span className="text-[color:var(--color-muted-foreground)]">
              {t('listCount', { count: list.length, limit: 200 })}
            </span>
          </h2>
        </div>
        <VideosTable
          videos={list}
          current={parsed}
          basePath={`/${locale}/videos`}
          showAccountCol={true}
        />
      </Card>
    </div>
  );
}
