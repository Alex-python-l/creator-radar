'use client';

import { useState, useTransition } from 'react';
import { useTranslations } from 'next-intl';

import { Button, Card, Input } from '@/components/ui';
import { setAccountAlias } from '@/lib/actions/accounts';
import { translateActionError } from '@/lib/i18n-errors';

const ALIAS_MAX_LEN = 50;

export function AliasEditor({
  accountId,
  initialAlias,
}: {
  accountId: number;
  initialAlias: string | null;
}) {
  const t = useTranslations('accountDetail');
  const actionErrors = useTranslations('actionErrors');
  const [value, setValue] = useState(initialAlias ?? '');
  const [error, setError] = useState<string | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);
  const [isPending, startTransition] = useTransition();

  function submit(next: string) {
    if (next.trim() === (initialAlias ?? '').trim()) return;
    setError(null);
    setSavedFlash(false);
    startTransition(async () => {
      const res = await setAccountAlias(accountId, next);
      if ('error' in res) {
        setError(translateActionError(res.error, actionErrors));
      } else {
        setSavedFlash(true);
      }
    });
  }

  return (
    <Card className="mb-6 p-4">
      <form
        className="flex flex-wrap items-center gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit(value);
        }}
      >
        <label
          htmlFor={`alias-${accountId}`}
          className="text-sm font-semibold text-[color:var(--color-foreground)]"
        >
          {t('alias')}
        </label>
        <Input
          id={`alias-${accountId}`}
          value={value}
          maxLength={ALIAS_MAX_LEN}
          placeholder={t('aliasPlaceholder')}
          onChange={(e) => setValue(e.target.value)}
          disabled={isPending}
          className="min-w-[16rem] flex-1"
        />
        <Button type="submit" disabled={isPending} size="sm">
          {t('aliasSave')}
        </Button>
        {(initialAlias ?? '').trim() !== '' && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={isPending}
            onClick={() => {
              setValue('');
              submit('');
            }}
          >
            {t('aliasClear')}
          </Button>
        )}
      </form>
      <p className="mt-2 text-xs text-[color:var(--color-muted-foreground)]">
        {t('aliasHint')}
      </p>
      {error && (
        <p
          role="alert"
          aria-live="polite"
          className="mt-2 text-xs text-[color:var(--color-destructive)]"
        >
          {error}
        </p>
      )}
      {savedFlash && !error && (
        <p
          aria-live="polite"
          className="mt-2 text-xs text-[color:var(--color-muted-foreground)]"
        >
          {t('aliasSaved')}
        </p>
      )}
    </Card>
  );
}
