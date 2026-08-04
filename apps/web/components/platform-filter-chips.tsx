'use client';

import Link from 'next/link';
import type { Route } from 'next';

import { PlatformMark, formatCompact } from '@/components/format';
import {
  buildAccountsQuery,
  type AccountsSearchParams,
} from '@/lib/url-params';
import { cn } from '@/lib/utils';
import { FetchEnabledPlatforms, PlatformLabels, type Platform } from '@svm/shared/platforms';

export function PlatformFilterChips({
  current,
  locale,
  label,
  platformCounts,
  basePath,
  onPrefetch,
}: {
  current: AccountsSearchParams;
  locale: string;
  label: string;
  platformCounts: Partial<Record<Platform, number>>;
  /** 筛选链接的页面前缀,如 `/${locale}/monitor` 或 `/${locale}/competitors`。 */
  basePath: string;
  onPrefetch?: (platform: Platform | 'all') => void;
}) {
  const options = ['all', ...FetchEnabledPlatforms.filter((p) => p !== 'douyin')] as Array<
    Platform | 'all'
  >;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-semibold text-[color:var(--color-muted-foreground)] mr-1">
        {label}
      </span>
      {options.map((p) => {
        const active = current.platform === p;
        const accountCount =
          p === 'all'
            ? Object.values(platformCounts).reduce((s, n) => s + (n ?? 0), 0)
            : platformCounts[p] ?? 0;
        const href = (basePath +
          buildAccountsQuery(current, { platform: p, expand: new Set() })) as Route;
        return (
          <Link
            key={p}
            href={href}
            scroll={false}
            onMouseEnter={() => onPrefetch?.(p)}
            className={cn(
              'inline-flex h-9 min-w-[128px] items-center gap-2 rounded-md px-2.5 text-xs font-semibold transition',
              active
                ? 'bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]'
                : 'border border-[color:var(--color-border)] bg-[color:var(--color-card)] text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)]',
            )}
          >
            <span className="flex min-w-0 items-center gap-2">
              {p === 'all' ? (
                <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)] text-[10px] font-bold">
                  All
                </span>
              ) : (
                <PlatformMark platform={p} />
              )}
              <span className="truncate">{p === 'all' ? 'All' : PlatformLabels[p]}</span>
            </span>
            <span className="ml-auto text-xs font-semibold tabular-nums">
              {formatCompact(accountCount, locale)}
            </span>
          </Link>
        );
      })}
    </div>
  );
}
