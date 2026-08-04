'use client';

import Image from 'next/image';
import Link from 'next/link';
import type { Route } from 'next';
import { useQueryClient } from '@tanstack/react-query';

import { GroupCreateDialog } from '@/components/group-create-dialog';
import { GroupManageDialog } from '@/components/group-manage-dialog';
import {
  accountsQueryKey,
  fetchAccounts,
  type AccountsQuery,
} from '@/lib/api/accounts';
import { switchAccountsGroup, type AccountsSearchParams } from '@/lib/url-params';
import { cn } from '@/lib/utils';
import irisMark from '@/assets/auth/iris-mark.png';

interface Group {
  id: number;
  name: string;
  sortOrder: number;
  accountCount: number;
}

export function GroupsSidebar({
  groups,
  current,
  locale,
  allLabel = 'ALL',
}: {
  groups: Group[];
  current: AccountsSearchParams;
  locale: string;
  allLabel?: string;
}) {
  const queryClient = useQueryClient();
  const allCount = groups.reduce((sum, g) => sum + g.accountCount, 0);
  const allHref = (`/${locale}/monitor` + switchAccountsGroup(current, undefined)) as Route;

  function prefetchGroup(groupId: number | undefined) {
    const q: AccountsQuery = {
      group: groupId,
      platform: current.platform,
      range: current.range,
      expand: [],  // 切组会清 expand
    };
    queryClient.prefetchQuery({
      queryKey: accountsQueryKey(q),
      queryFn: () => fetchAccounts(q),
    });
  }

  return (
    <aside className="w-full flex-shrink-0 space-y-4 lg:w-40">
      <div id="bulk-action-slot" className="empty:hidden space-y-2" />
      <div className="space-y-1">
        <Link
          href={allHref}
          scroll={false}
          onMouseEnter={() => prefetchGroup(undefined)}
          className={cn(
            'flex min-h-10 items-center justify-between rounded-md border border-transparent px-3 py-2 text-sm transition',
            current.group === undefined
              ? 'border-[rgba(96,69,194,0.25)] bg-white/75 font-semibold text-[color:var(--color-primary)] shadow-[0_5px_16px_rgba(67,47,126,0.04)]'
              : 'text-[color:var(--color-muted-foreground)] hover:bg-white/50 hover:text-[color:var(--color-foreground)]',
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            {current.group === undefined ? <Image src={irisMark} alt="" className="h-3.5 w-3.5 object-contain" aria-hidden /> : null}
            <span className="truncate">{allLabel}</span>
          </span>
          <span className="ml-2 text-xs tabular-nums">{allCount}</span>
        </Link>
        {groups.map((g) => {
          const active = g.id === current.group;
          const href = (`/${locale}/monitor` + switchAccountsGroup(current, g.id)) as Route;
          return (
            <Link
              key={g.id}
              href={href}
              scroll={false}
              onMouseEnter={() => prefetchGroup(g.id)}
              className={cn(
                'flex min-h-10 items-center justify-between rounded-md border border-transparent px-3 py-2 text-sm transition',
                active
                  ? 'border-[rgba(96,69,194,0.25)] bg-white/75 font-semibold text-[color:var(--color-primary)] shadow-[0_5px_16px_rgba(67,47,126,0.04)]'
                  : 'text-[color:var(--color-muted-foreground)] hover:bg-white/50 hover:text-[color:var(--color-foreground)]',
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                {active ? <Image src={irisMark} alt="" className="h-3.5 w-3.5 object-contain" aria-hidden /> : null}
                <span className="truncate">{g.name}</span>
              </span>
              <span className="ml-2 text-xs tabular-nums">{g.accountCount}</span>
            </Link>
          );
        })}
      </div>
      <div className="flex gap-1 border-t border-[color:var(--color-border)] pt-3">
        <GroupCreateDialog />
        <GroupManageDialog groups={groups} />
      </div>
    </aside>
  );
}
