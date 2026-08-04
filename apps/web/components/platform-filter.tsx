import type { Route } from 'next';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';

import { cn } from '@/lib/utils';
import { buildQuery, type VideosSearchParams } from '@/lib/url-params';
import { FetchEnabledPlatforms, PlatformLabels, type Platform } from '@svm/shared/platforms';

interface Props {
  basePath: string;
  current: VideosSearchParams;
}

export async function PlatformFilter({ basePath, current }: Props) {
  const t = await getTranslations('common');

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-semibold text-[color:var(--color-muted-foreground)] mr-1">
        {t('platform')}
      </span>
      {FetchEnabledPlatforms.filter((p) => p !== 'douyin').map((p: Platform) => {
        const active = current.platform === p;
        const href = `${basePath}${buildQuery(current, { platform: p })}` as Route;
        return (
          <Link
            key={p}
            href={href}
            scroll={false}
            className={cn(
              'h-8 px-3 rounded-md text-xs font-semibold inline-flex items-center transition',
              active
                ? 'bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]'
                : 'border border-[color:var(--color-border)] bg-[color:var(--color-card)] text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)]',
            )}
          >
            {PlatformLabels[p]}
          </Link>
        );
      })}
    </div>
  );
}
