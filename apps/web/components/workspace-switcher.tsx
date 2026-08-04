'use client';

import { Building2, Loader2, UserRound } from 'lucide-react';
import { useState } from 'react';

import { authClient } from '@/lib/auth/client';
import { cn } from '@/lib/utils';

export function WorkspaceSwitcher({
  active,
  personalName,
  team,
}: {
  active: 'personal' | 'team';
  personalName: string;
  team: { id: number; name: string } | null;
}) {
  const [busy, setBusy] = useState(false);

  const switchTo = async (organizationId: number | null) => {
    setBusy(true);
    const { error } = await authClient.organization.setActive({
      organizationId: organizationId === null ? null : String(organizationId),
    });
    if (error) {
      setBusy(false);
      return;
    }
    window.location.reload();
  };

  return (
    <div className="mb-4 rounded-lg border border-[color:var(--color-border)] bg-white/55 p-1.5">
      <button
        type="button"
        disabled={busy || active === 'personal'}
        onClick={() => switchTo(null)}
        className={cn(
          'flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs font-semibold',
          active === 'personal' ? 'bg-white text-[var(--color-primary)] shadow-sm' : 'text-[var(--color-muted-foreground)]',
        )}
      >
        <UserRound className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{personalName}</span>
      </button>
      {team && (
        <button
          type="button"
          disabled={busy || active === 'team'}
          onClick={() => switchTo(team.id)}
          className={cn(
            'mt-1 flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs font-semibold',
            active === 'team' ? 'bg-white text-[var(--color-primary)] shadow-sm' : 'text-[var(--color-muted-foreground)]',
          )}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Building2 className="h-3.5 w-3.5 shrink-0" />}
          <span className="truncate">{team.name}</span>
        </button>
      )}
    </div>
  );
}
