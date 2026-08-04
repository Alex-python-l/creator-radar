'use client';

import { useRef, useState, useTransition } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { Upload } from 'lucide-react';

import { Button } from '@/components/ui';
import { bulkAddAccounts } from '@/lib/actions/accounts';
import { formatAccountDisplayName, formatAccountRawIdentifier } from '@/lib/account-display';
import type { AccountRelation } from '@/lib/account-relation';
import { parseImportText, type ParseResult } from '@/lib/import-parsers';
import { FetchEnabledPlatforms, PlatformLabels, type Platform } from '@svm/shared/platforms';

interface ImportDialogGroup {
  id: number;
  name: string;
}

export function ImportDialog({
  groups = [],
  defaultGroupId = 0,
  relation = 'own',
}: {
  /** own 模式必传;competitor 模式无分组轴,可省略。 */
  groups?: ImportDialogGroup[];
  defaultGroupId?: number;
  relation?: AccountRelation;
}) {
  const t = useTranslations('importDialog');
  const common = useTranslations('common');
  const isCompetitor = relation === 'competitor';
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [defaultPlatform, setDefaultPlatform] = useState<Platform>('tiktok');
  const [targetGroupId, setTargetGroupId] = useState<number>(defaultGroupId);
  const [text, setText] = useState('');
  const [result, setResult] = useState<ParseResult>({ accounts: [], errors: [] });
  const [feedback, setFeedback] = useState<string>('');
  const [resolveErrors, setResolveErrors] = useState<string[]>([]);
  const [isPending, startTransition] = useTransition();
  const queryClient = useQueryClient();

  const reparse = (raw: string, platform: Platform) => {
    setResult(parseImportText(raw, platform));
  };

  const open = () => {
    setText('');
    setResult({ accounts: [], errors: [] });
    setFeedback('');
    setResolveErrors([]);
    setTargetGroupId(defaultGroupId);
    dialogRef.current?.showModal();
  };
  const close = () => dialogRef.current?.close();

  const onTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    reparse(e.target.value, defaultPlatform);
  };

  const onPlatformChange = (p: Platform) => {
    setDefaultPlatform(p);
    reparse(text, p);
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const t = await f.text();
    setText(t);
    reparse(t, defaultPlatform);
  };

  const onSubmit = () => {
    if (result.accounts.length === 0) return;
    startTransition(async () => {
      const r = await bulkAddAccounts(
        result.accounts.map((a) => ({
          platform: a.platform,
          username: a.username,
          needsResolve: a.needsResolve,
          rawUrl: a.rawUrl,
        })),
        targetGroupId,
        relation,
      );
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
      setFeedback(
        r.quotaExceeded
          ? t('quotaExceeded', { inserted: r.inserted })
          : t('feedback', { inserted: r.inserted, duplicateInDb: r.duplicateInDb, skipped: r.skipped }),
      );
      setResolveErrors(r.resolveErrors ?? []);
      setTimeout(() => {
        // 配额满时不自动关弹窗,让用户看到原因。
        if ((!r.resolveErrors || r.resolveErrors.length === 0) && !r.quotaExceeded) {
          close();
          setText('');
          setResult({ accounts: [], errors: [] });
          setFeedback('');
          setResolveErrors([]);
        }
      }, 1200);
    });
  };

  return (
    <>
      <Button variant="secondary" onClick={open}>
        <Upload className="h-3.5 w-3.5" aria-hidden />
        {isCompetitor ? t('openCompetitor') : t('open')}
      </Button>
      <dialog
        ref={dialogRef}
        className="m-auto rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-0 backdrop:bg-black/40 max-w-2xl w-full"
      >
        <div className="p-6 space-y-4">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-lg font-bold">{isCompetitor ? t('competitorTitle') : t('title')}</h2>
              <p className="mt-1 text-xs text-[color:var(--color-muted-foreground)]">
                {isCompetitor ? t('competitorDescription') : t('description')}
              </p>
              <p className="mt-1 text-xs text-[color:var(--color-muted-foreground)]">
                {t('fetchNote')}
              </p>
            </div>
            <button
              type="button"
              onClick={close}
              className="text-[color:var(--color-muted-foreground)] hover:text-[color:var(--color-foreground)] text-xl leading-none"
              aria-label={common('close')}
            >
              ×
            </button>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-semibold text-[color:var(--color-muted-foreground)]">
              {t('defaultPlatform')}
            </span>
            {FetchEnabledPlatforms.filter((p) => p !== 'douyin').map((p: Platform) => (
              <button
                key={p}
                type="button"
                onClick={() => onPlatformChange(p)}
                className={`h-8 px-3 rounded-md text-xs font-semibold ${
                  defaultPlatform === p
                    ? 'bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]'
                    : 'border border-[color:var(--color-border)] bg-[color:var(--color-card)] hover:bg-[color:var(--color-muted)]'
                }`}
              >
                {PlatformLabels[p]}
              </button>
            ))}
            {!isCompetitor && (
              <>
                <span className="ml-2 text-xs font-semibold text-[color:var(--color-muted-foreground)]">
                  {t('targetGroup')}
                </span>
                <select
                  value={targetGroupId}
                  onChange={(e) => setTargetGroupId(Number(e.target.value))}
                  className="h-8 px-2 rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] text-xs"
                  aria-label={t('targetGroup')}
                >
                  {groups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </>
            )}
            <span className="ml-auto text-xs text-[color:var(--color-muted-foreground)]">
              {t('uploadCsv')}
            </span>
            <input
              type="file"
              accept=".csv,text/csv,text/plain"
              onChange={onFile}
              className="text-xs"
            />
          </div>

          <textarea
            value={text}
            onChange={onTextChange}
            rows={10}
            placeholder={'@khaby.lame\nhttps://www.tiktok.com/@tiktok\ninstagram,instagram\n...'}
            className="w-full rounded-md border border-[color:var(--color-input)] bg-[color:var(--color-background)] p-3 text-sm font-mono"
          />

          <div className="text-sm space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold">
                {t('willImport', { count: result.accounts.length })}
                {result.errors.length > 0 && (
                  <span className="text-[color:var(--color-muted-foreground)] font-normal">
                    {' '}
                    {t('skippedRows', { count: result.errors.length })}
                  </span>
                )}
              </span>
            </div>

            {result.accounts.length > 0 && (
              <div className="max-h-32 overflow-y-auto rounded border border-[color:var(--color-border)] p-2 space-y-1 bg-[color:var(--color-background)]">
                {result.accounts.map((a, idx) => (
                  <div key={idx} className="text-xs flex gap-2">
                    <span className="text-[color:var(--color-muted-foreground)] w-16">
                      {PlatformLabels[a.platform]}
                    </span>
                    <span
                      className="font-semibold truncate"
                      title={a.rawUrl ?? formatAccountRawIdentifier(a)}
                    >
                      {formatAccountDisplayName(a)}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {result.errors.length > 0 && (
              <details className="text-xs text-[color:var(--color-muted-foreground)]">
                <summary className="cursor-pointer">{t('viewSkipped', { count: result.errors.length })}</summary>
                <div className="mt-1 max-h-24 overflow-y-auto space-y-0.5 pl-2">
                  {result.errors.map((e, idx) => (
                    <div key={idx} className="truncate">
                      <span className="text-[color:var(--color-destructive)]">{translateImportError(e.reason, t)}</span>
                      <span className="ml-2 opacity-70">{e.line}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>

          {feedback && (
            <div className="text-sm text-[hsl(142_71%_30%)] bg-[hsl(142_71%_92%)] rounded px-3 py-2">
              {feedback}
            </div>
          )}

          {resolveErrors.length > 0 && (
            <details className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-background)] px-3 py-2 text-xs text-[color:var(--color-destructive)]">
              <summary className="cursor-pointer font-semibold">
                {t('resolveErrors', { count: resolveErrors.length })}
              </summary>
              <div className="mt-1 max-h-28 overflow-y-auto space-y-0.5">
                {resolveErrors.map((e, idx) => (
                  <div key={idx} className="truncate" title={e}>
                    {e}
                  </div>
                ))}
              </div>
            </details>
          )}

          <div className="flex justify-end gap-2 pt-2 border-t border-[color:var(--color-border)]">
            <Button variant="secondary" onClick={close} disabled={isPending}>
              {common('cancel')}
            </Button>
            <Button onClick={onSubmit} disabled={isPending || result.accounts.length === 0}>
              {isPending ? t('submitting') : t('submit', { count: result.accounts.length })}
            </Button>
          </div>
        </div>
      </dialog>
    </>
  );
}

function translateImportError(
  reason: string,
  t: (key: string, values?: Record<string, string | number>) => string,
) {
  if (reason === '空行') return t('errors.empty');
  if (reason === '用户名为空') return t('errors.emptyUsername');
  if (reason === '无效的用户名') return t('errors.invalidUsername');

  const duplicate = reason.match(/^重复: @(.+)$/);
  if (duplicate) return t('errors.duplicate', { username: duplicate[1] });

  return reason;
}
