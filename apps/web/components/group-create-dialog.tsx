'use client';

import { useRef, useState, useTransition } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Plus, X } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { Button } from '@/components/ui';
import { createGroup } from '@/lib/actions/groups';
import { translateActionError } from '@/lib/i18n-errors';

export function GroupCreateDialog() {
  const t = useTranslations('groups');
  const common = useTranslations('common');
  const actionErrors = useTranslations('actionErrors');
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  function openDialog() {
    setError(null);
    setOpen(true);
  }

  function closeDialog() {
    setOpen(false);
    setError(null);
  }

  async function handleSubmit(formData: FormData) {
    setError(null);
    startTransition(async () => {
      const res = await createGroup(formData);
      if ('error' in res && res.error) {
        setError(translateActionError(res.error, actionErrors));
        return;
      }
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
      closeDialog();
      if (inputRef.current) inputRef.current.value = '';
    });
  }

  return (
    <>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        onClick={openDialog}
        className="h-8 w-8 px-0"
        aria-label={t('create')}
        title={t('create')}
      >
        <Plus className="h-4 w-4" aria-hidden />
      </Button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <div className="w-full max-w-sm rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-6 shadow-lg">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold">{t('create')}</h3>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={closeDialog}
                className="h-8 w-8 px-0"
                aria-label={common('close')}
                title={common('close')}
              >
                <X className="h-4 w-4" aria-hidden />
              </Button>
            </div>
            <form action={handleSubmit} className="flex flex-col gap-2">
              <input
                ref={inputRef}
                name="name"
                placeholder={t('namePlaceholder')}
                autoFocus
                maxLength={40}
                className="flex h-9 w-full rounded-md border border-[color:var(--color-input)] bg-[color:var(--color-background)] px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-[color:var(--color-muted-foreground)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--color-ring)]"
              />
              {error && <span className="text-xs text-[color:var(--color-destructive)]">{error}</span>}
              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" size="sm" variant="ghost" onClick={closeDialog}>
                  {common('cancel')}
                </Button>
                <Button type="submit" size="sm" disabled={isPending}>
                  {common('create')}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
