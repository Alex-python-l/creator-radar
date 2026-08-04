'use client';

import Link from 'next/link';
import type { Route } from 'next';
import { useTranslations } from 'next-intl';
import { useMemo } from 'react';

import { buildQuery, type VideosSearchParams } from '@/lib/url-params';
import { cn } from '@/lib/utils';

interface Group {
  id: number;
  name: string;
  accountCount: number;
}

export function GroupsFilter({
  basePath,
  current,
  groups,
}: {
  basePath: string;
  current: VideosSearchParams;
  groups: Group[];
}) {
  const t = useTranslations('groups');
  const common = useTranslations('common');
  const selectedSet = useMemo(() => new Set(current.groups), [current.groups]);

  function hrefFor(groupId: number): Route {
    const next = new Set(selectedSet);
    if (next.has(groupId)) next.delete(groupId);
    else next.add(groupId);
    const q = buildQuery(current, { groups: [...next] });
    return (basePath + q) as Route;
  }

  const allHref = (basePath + buildQuery(current, { groups: [] })) as Route;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-semibold text-[color:var(--color-muted-foreground)] mr-1">
        {common('group')}
      </span>
      <Link
        href={allHref}
        scroll={false}
        className={cn(
          'h-8 px-3 rounded-md text-xs font-semibold inline-flex items-center transition',
          selectedSet.size === 0
            ? 'bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]'
            : 'border border-[color:var(--color-border)] bg-[color:var(--color-card)] text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)]',
        )}
        title={t('clearAllTitle')}
      >
        {t('allMixed')}
      </Link>
      {groups.map((g) => {
        const active = selectedSet.has(g.id);
        return (
          <Link
            key={g.id}
            href={hrefFor(g.id)}
            scroll={false}
            className={cn(
              'h-8 px-3 rounded-md text-xs font-semibold inline-flex items-center gap-1.5 transition',
              active
                ? 'bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]'
                : 'border border-[color:var(--color-border)] bg-[color:var(--color-card)] text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)]',
            )}
          >
            <span>{active ? '☑' : '☐'}</span>
            <span>{g.name}</span>
            <span className="opacity-70">{g.accountCount}</span>
          </Link>
        );
      })}
    </div>
  );
}
