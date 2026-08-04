'use client';

import { useState, useTransition } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Settings, X } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { Button } from '@/components/ui';
import { deleteGroup, renameGroup, reorderGroup } from '@/lib/actions/groups';
import { translateActionError } from '@/lib/i18n-errors';

interface Group {
  id: number;
  name: string;
  sortOrder: number;
  accountCount: number;
}

export function GroupManageDialog({ groups }: { groups: Group[] }) {
  const t = useTranslations('groups');
  const common = useTranslations('common');
  const actionErrors = useTranslations('actionErrors');
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const queryClient = useQueryClient();

  if (!open) {
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => setOpen(true)}
        className="h-8 w-8 px-0"
        aria-label={t('manage')}
        title={t('manage')}
      >
        <Settings className="h-4 w-4" aria-hidden />
      </Button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[480px] rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold">{t('title')}</h3>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => { setOpen(false); setError(null); setEditingId(null); }}
            className="h-8 w-8 px-0"
            aria-label={common('close')}
            title={common('close')}
          >
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>
        {error && <div className="mb-3 text-xs text-[color:var(--color-destructive)]">{error}</div>}
        <div className="space-y-2">
          {groups.map((g, idx) => (
            <div key={g.id} className="flex items-center gap-2 rounded-md border border-[color:var(--color-border)] p-2">
              {editingId === g.id ? (
                <form
                  action={(fd) => {
                    setError(null);
                    startTransition(async () => {
                      const res = await renameGroup(g.id, fd);
                      queryClient.invalidateQueries({ queryKey: ['accounts'] });
                      if ('error' in res && res.error) setError(translateActionError(res.error, actionErrors));
                      else setEditingId(null);
                    });
                  }}
                  className="flex flex-1 gap-2"
                >
                  <input
                    name="name"
                    defaultValue={g.name}
                    autoFocus
                    maxLength={40}
                    className="flex-1 rounded-md border border-[color:var(--color-input)] bg-[color:var(--color-background)] px-2 text-sm h-8"
                  />
                  <Button type="submit" size="sm" disabled={isPending}>{common('save')}</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setEditingId(null)}>{common('cancel')}</Button>
                </form>
              ) : (
                <>
                  <span className="flex-1 text-sm font-medium">{g.name}</span>
                  <span className="text-xs text-[color:var(--color-muted-foreground)]">{g.accountCount}</span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={idx === 0 || isPending}
                    onClick={() => startTransition(async () => {
                      const res = await reorderGroup(g.id, 'up');
                      queryClient.invalidateQueries({ queryKey: ['accounts'] });
                      if ('error' in res && res.error) setError(translateActionError(res.error, actionErrors));
                    })}
                  >▲</Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={idx === groups.length - 1 || isPending}
                    onClick={() => startTransition(async () => {
                      const res = await reorderGroup(g.id, 'down');
                      queryClient.invalidateQueries({ queryKey: ['accounts'] });
                      if ('error' in res && res.error) setError(translateActionError(res.error, actionErrors));
                    })}
                  >▼</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setEditingId(g.id)}>{common('rename')}</Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger"
                    disabled={isPending}
                    onClick={() => startTransition(async () => {
                      setError(null);
                      const res = await deleteGroup(g.id);
                      queryClient.invalidateQueries({ queryKey: ['accounts'] });
                      if ('error' in res && res.error) setError(translateActionError(res.error, actionErrors));
                    })}
                  >{common('delete')}</Button>
                </>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
