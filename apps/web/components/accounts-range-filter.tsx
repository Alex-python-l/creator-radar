import Link from 'next/link';
import type { Route } from 'next';

import { RANGES, buildAccountsQuery, type AccountsSearchParams } from '@/lib/url-params';
import { cn } from '@/lib/utils';

/** 发布时间范围 chip 条。basePath 决定链接落在哪个页面(monitor / competitors)。 */
export function AccountsRangeFilter({
  current,
  basePath,
  label,
  rangeLabels,
}: {
  current: AccountsSearchParams;
  basePath: string;
  label: string;
  rangeLabels: Record<(typeof RANGES)[number], string>;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-semibold text-[color:var(--color-muted-foreground)] mr-1">
        {label}
      </span>
      {RANGES.map((r) => {
        const active = current.range === r;
        const href = (basePath + buildAccountsQuery(current, { range: r })) as Route;
        return (
          <Link
            key={r}
            href={href}
            scroll={false}
            className={cn(
              'h-8 px-3 rounded-md text-xs font-semibold inline-flex items-center transition',
              active
                ? 'bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]'
                : 'border border-[color:var(--color-border)] bg-[color:var(--color-card)] text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)]',
            )}
          >
            {rangeLabels[r]}
          </Link>
        );
      })}
    </div>
  );
}
