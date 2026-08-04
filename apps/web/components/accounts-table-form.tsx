'use client';

import { RefreshCw, Trash2 } from 'lucide-react';
import Link from 'next/link';
import type { Route } from 'next';
import { usePathname, useRouter } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, useTransition } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { createPortal } from 'react-dom';

import { ConfirmSubmitButton } from '@/components/confirm-submit-button';
import { FetchStatusBadge } from '@/components/fetch-status-badge';
import { PlatformMark, formatCompact, formatRelative } from '@/components/format';
import { Button, Card, Select, Td, Th } from '@/components/ui';
import { toggleGroupAccountNumbers } from '@/lib/actions/account-numbering';
import { deleteAccount, moveAccountsToGroup, triggerFetchForm } from '@/lib/actions/accounts';
import { formatAccountListLabel, formatAccountListTitle, formatAccountRawIdentifier } from '@/lib/account-display';
import { translateActionError } from '@/lib/i18n-errors';
import type { AccountsSearchParams } from '@/lib/url-params';
import { applyAccountExpandParam, toggleAccountExpandSet } from '@/lib/url-params';
import { cn } from '@/lib/utils';
import { PlatformLabels, type Platform } from '@svm/shared/platforms';

interface AccountRow {
  id: number;
  /** 竞品订阅行不进分组,为 null。 */
  groupId: number | null;
  displayNumber: number | null;
  platform: string;
  username: string;
  displayName: string | null;
  alias: string | null;
  recentVideoCount: number;
  recentPlayCount: number;
  recentLikeCount: number;
  latestFetchStatus: string | null;
  latestFetchStartedAt: Date | null;
  latestFetchFinishedAt: Date | null;
  latestFetchError: string | null;
  latestFetchErrorCode: string | null;
  activeJobStatus: string | null;
}

interface VideoRow {
  id: number;
  influencerId: number;
  videoId: string;
  videoUrl: string | null;
  description: string | null;
  contentType: string;
  playCount: number;
  likeCount: number;
  publishedAt: Date | null;
}

interface Group {
  id: number;
  name: string;
}

// bulk-action-slot 是兄弟组件 GroupsSidebar 渲染的固定占位节点(见
// components/groups-sidebar.tsx),只有挂载后才能用 document.getElementById 查到,
// 且此后不会再消失/重建。用 useSyncExternalStore 读取这个"外部系统"(DOM),而不是
// "挂载后 setState",符合 react-hooks/set-state-in-effect 的建议;该节点是静态的,
// 所以 subscribe 不需要真正监听变化。
function subscribeToNothing() {
  return () => {};
}

function getBulkActionSlotSnapshot() {
  return document.getElementById('bulk-action-slot');
}

function getBulkActionSlotServerSnapshot() {
  return null;
}

export function AccountsTableForm({
  platform,
  accounts,
  parsed,
  videosByAccount,
  groups,
  numberingActive,
  rangeLabel,
  rangeIsAll,
}: {
  platform: Platform | 'all';
  accounts: AccountRow[];
  parsed: AccountsSearchParams;
  videosByAccount: Record<number, VideoRow[]>;
  groups: Group[];
  numberingActive: boolean;
  rangeLabel: string;
  rangeIsAll: boolean;
}) {
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const t = useTranslations('accountsTable');
  const actionErrors = useTranslations('actionErrors');
  const platformLabel = platform === 'all' ? 'All' : PlatformLabels[platform];
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const parsedExpandKey = useMemo(
    () => [...parsed.expand].sort((a, b) => a - b).join(','),
    [parsed.expand],
  );
  const parsedExpandSnapshot = useMemo(() => {
    const next = new Set<number>();
    if (!parsedExpandKey) return next;
    for (const token of parsedExpandKey.split(',')) {
      const id = Number(token);
      if (Number.isInteger(id) && id > 0) next.add(id);
    }
    return next;
  }, [parsedExpandKey]);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(() => parsedExpandSnapshot);
  const expandedIdsRef = useRef<Set<number>>(parsedExpandSnapshot);
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const slot = useSyncExternalStore(
    subscribeToNothing,
    getBulkActionSlotSnapshot,
    getBulkActionSlotServerSnapshot,
  );

  // parsed.expand 变化(浏览器前进/后退、其他入口改了 URL)时把本地展开状态与 URL
  // 重新对齐;toggleExpandedAccount 产生的乐观本地更新,在 parsedExpandSnapshot 真正
  // 变化追上之前不会被这里打断——用 syncedExpandSnapshot 记录"上一次已同步到的快照"
  // 而非直接比较 expandedIds,避免每次乐观更新后的下一次渲染又被这里覆盖回去。
  // 等价于原先 `useEffect(() => {...}, [parsedExpandSnapshot])`,按 React 官方
  // "渲染期调整状态"写法改写以避免 effect 内 setState。
  const [syncedExpandSnapshot, setSyncedExpandSnapshot] = useState(parsedExpandSnapshot);
  if (parsedExpandSnapshot !== syncedExpandSnapshot) {
    setSyncedExpandSnapshot(parsedExpandSnapshot);
    setExpandedIds(parsedExpandSnapshot);
  }

  // ref 不能在渲染期写 (react-hooks/refs),只能放在事件处理器/effect 里;这个
  // effect 不调用任何 setState,只是让 expandedIdsRef 跟上 parsedExpandSnapshot,
  // 不触发 set-state-in-effect。commit 后很快执行,早于用户下一次点击。
  useEffect(() => {
    expandedIdsRef.current = parsedExpandSnapshot;
  }, [parsedExpandSnapshot]);

  // group/platform/range 任一变化时清空勾选与残留错误,等价于原先以三者为依赖的
  // useEffect,同样改成渲染期调整状态。
  const filterKey = `${parsed.group ?? ''}|${parsed.platform}|${parsed.range}`;
  const [syncedFilterKey, setSyncedFilterKey] = useState(filterKey);
  if (filterKey !== syncedFilterKey) {
    setSyncedFilterKey(filterKey);
    setSelected(new Set());
    setError(null);
  }

  const allChecked = accounts.length > 0 && accounts.every((a) => selected.has(a.id));
  const canToggleNumbering = parsed.group !== undefined && parsed.platform === 'all';

  function toggleOne(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  function toggleAll() {
    if (allChecked) setSelected(new Set());
    else setSelected(new Set(accounts.map((a) => a.id)));
  }

  function toggleExpandedAccount(id: number) {
    const next = toggleAccountExpandSet(expandedIdsRef.current, id);
    expandedIdsRef.current = next;
    setExpandedIds(next);

    const currentParams = new URLSearchParams(
      typeof window === 'undefined' ? '' : window.location.search,
    );
    const query = applyAccountExpandParam(currentParams, next);
    const hash = typeof window === 'undefined' ? '' : window.location.hash;
    router.push(`${pathname}${query}${hash}` as Route, { scroll: false });
  }

  async function bulkMove(formData: FormData) {
    setError(null);
    for (const id of selected) formData.append('accountIds', String(id));
    startTransition(async () => {
      const res = await moveAccountsToGroup(formData);
      if ('error' in res && res.error) setError(translateActionError(res.error, actionErrors));
      else {
        setSelected(new Set());
        queryClient.invalidateQueries({ queryKey: ['accounts'] });
      }
    });
  }

  function toggleNumbering() {
    if (!canToggleNumbering || parsed.group === undefined) return;

    setError(null);
    startTransition(async () => {
      const res = await toggleGroupAccountNumbers(parsed.group!);
      if ('error' in res && res.error) {
        setError(translateActionError(res.error, actionErrors));
        return;
      }

      queryClient.invalidateQueries({ queryKey: ['accounts'] });
      router.refresh();
    });
  }

  return (
    <Card>
      {accounts.length === 0 ? (
        <div className="px-6 py-12 text-center text-sm text-[color:var(--color-muted-foreground)]">
          {t('empty', { platform: platformLabel })}
        </div>
      ) : (
        <>
          <div className="overflow-hidden">
            <table className="w-full table-fixed">
              <colgroup>
                <col className="w-[5%]" />
                <col className="w-[5%]" />
                <col className="w-[11%]" />
                <col className="w-[22%]" />
                <col className="w-[12%]" />
                <col className="w-[14%]" />
                <col className="w-[14%]" />
                <col className="w-[17%]" />
              </colgroup>
              <thead className="border-b border-[color:var(--color-border)]">
                <tr>
                  <Th className="px-1.5 text-center">
                    <input
                      type="checkbox"
                      checked={allChecked}
                      onChange={toggleAll}
                      aria-label={t('selectAll')}
                    />
                  </Th>
                  <Th className="px-0"></Th>
                  <Th className="px-1">
                    {canToggleNumbering ? (
                      <button
                        type="button"
                        onClick={toggleNumbering}
                        disabled={isPending}
                        aria-pressed={numberingActive}
                        title={numberingActive ? t('clearNumbering') : t('generateNumbering')}
                        className={cn(
                          'inline-flex items-center gap-1 rounded px-1 py-0.5 transition hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)] disabled:opacity-50',
                          numberingActive && 'text-[hsl(142_70%_28%)]',
                        )}
                      >
                        ID
                      </button>
                    ) : (
                      <span title={t('numberingUnavailable')}>ID</span>
                    )}
                  </Th>
                  <Th className="px-2">{t('username')}</Th>
                  <Th className="text-right px-1">{rangeIsAll ? t('video') : rangeLabel}</Th>
                  <Th className="text-right px-1">{t('play')}</Th>
                  <Th className="text-right px-1">{t('like')}</Th>
                  <Th className="text-center px-1">{t('actions')}</Th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => {
                  const expanded = expandedIds.has(a.id);
                  const recentCount = Number(a.recentVideoCount) || 0;
                  const accountVideos = videosByAccount[a.id] ?? [];
                  return (
                    <RowAndExpanded
                      key={a.id}
                      account={a}
                      expanded={expanded}
                      onToggleExpand={() => toggleExpandedAccount(a.id)}
                      recentCount={recentCount}
                      expandedVideos={accountVideos}
                      locale={locale}
                      showPlatformMark={platform === 'all'}
                      numberingActive={numberingActive}
                      checked={selected.has(a.id)}
                      onToggle={() => toggleOne(a.id)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>

          {selected.size > 0 && slot && createPortal(
            <form
              action={bulkMove}
              className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-2 shadow-sm flex flex-col gap-1.5"
            >
              <div className="flex items-center justify-between gap-1">
                <PlatformMark platform={platform} />
                <span className="text-[10px] font-semibold text-[color:var(--color-muted-foreground)]">
                  {t('selected', { count: selected.size })}
                </span>
              </div>
              <Select name="targetGroupId" defaultValue="" required className="w-full h-8 text-xs">
                <option value="" disabled>{t('chooseGroup')}</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </Select>
              <div className="flex gap-1">
                <Button type="submit" size="sm" disabled={isPending} className="flex-1 px-2 text-xs">{t('move')}</Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="px-2 text-xs"
                  onClick={() => { setSelected(new Set()); setError(null); }}
                >
                  {t('clearSelection')}
                </Button>
              </div>
              {error && (
                <span className="text-[10px] text-[color:var(--color-destructive)]">{error}</span>
              )}
            </form>,
            slot,
          )}
        </>
      )}
    </Card>
  );
}

function RowAndExpanded({
  account: a,
  expanded,
  onToggleExpand,
  recentCount,
  expandedVideos,
  locale,
  showPlatformMark,
  numberingActive,
  checked,
  onToggle,
}: {
  account: AccountRow;
  expanded: boolean;
  onToggleExpand: () => void;
  recentCount: number;
  expandedVideos: VideoRow[];
  locale: string;
  showPlatformMark: boolean;
  numberingActive: boolean;
  checked: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations('accountsTable');
  const accountLabel = formatAccountListLabel(a);
  const rawIdentifier = formatAccountRawIdentifier(a);
  const title = formatAccountListTitle(a);
  const hasDisplayNumber = numberingActive && a.displayNumber !== null;

  return (
    <>
      <tr className="border-b border-[color:var(--color-border)] last:border-0">
        <Td className="px-1.5 text-center">
          <input
            type="checkbox"
            checked={checked}
            onChange={onToggle}
            aria-label={t('selectAccount', { username: accountLabel })}
          />
        </Td>
        <Td className="px-0 text-center">
          <button
            type="button"
            onClick={onToggleExpand}
            className="inline-flex items-center justify-center w-5 h-6 rounded text-[color:var(--color-muted-foreground)] hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)] cursor-pointer"
            aria-label={expanded ? t('collapse') : t('expand')}
            aria-expanded={expanded}
          >
            {expanded ? '▼' : '▶'}
          </button>
        </Td>
        <Td className="px-1">
          <span
            className={cn(
              'inline-flex items-center justify-center rounded-md border px-1.5 py-0.5 text-xs font-mono font-semibold',
              hasDisplayNumber
                ? 'border-[hsl(142_55%_70%)] bg-[hsl(142_76%_94%)] text-[hsl(142_70%_28%)]'
                : 'border-[color:var(--color-border)] bg-[color:var(--color-muted)] text-[color:var(--color-muted-foreground)]',
            )}
            title={hasDisplayNumber ? `Source ID #${a.id}` : undefined}
          >
            #{hasDisplayNumber ? a.displayNumber : a.id}
          </span>
        </Td>
        <Td className="overflow-hidden px-2">
          <div className="flex items-center gap-1.5 min-w-0">
            {showPlatformMark && <PlatformMark platform={a.platform} />}
            <Link
              href={`/${locale}/accounts/${a.id}` as Route}
              className={cn(
                'font-semibold hover:underline truncate min-w-0',
                a.latestFetchErrorCode === 'account_gone' &&
                  'text-[color:var(--color-destructive)]',
              )}
              title={title}
            >
              {accountLabel}
            </Link>
          </div>
          {accountLabel !== rawIdentifier && (
            <div className="text-xs text-[color:var(--color-muted-foreground)] mt-0.5 truncate">
              {rawIdentifier}
            </div>
          )}
          <div className="mt-1">
            <FetchStatusBadge
              status={a.activeJobStatus ?? a.latestFetchStatus}
              startedAt={a.activeJobStatus ? null : a.latestFetchStartedAt}
              finishedAt={a.activeJobStatus ? null : a.latestFetchFinishedAt}
              errorMessage={a.activeJobStatus ? null : a.latestFetchError}
              errorCode={a.activeJobStatus ? null : a.latestFetchErrorCode}
            />
          </div>
        </Td>
        <Td className={cn('px-1 text-right tabular-nums font-semibold', recentCount === 0 && 'text-[color:var(--color-destructive)]')}>
          {recentCount}
        </Td>
        <Td className={cn('px-1 text-right tabular-nums font-semibold', recentCount === 0 && 'text-[color:var(--color-destructive)]')}>
          {formatCompact(Number(a.recentPlayCount), locale)}
        </Td>
        <Td className={cn('px-1 text-right tabular-nums font-semibold', recentCount === 0 && 'text-[color:var(--color-destructive)]')}>
          {formatCompact(Number(a.recentLikeCount), locale)}
        </Td>
        <Td className="px-1 text-center whitespace-nowrap">
          <form action={triggerFetchForm.bind(null, a.id)} className="inline">
            <ConfirmSubmitButton
              size="sm"
              variant="secondary"
              className="px-1.5"
              aria-label={t('fetchShort')}
              title={t('fetchShort')}
              confirmMessage={t('confirmFetch', { username: accountLabel })}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            </ConfirmSubmitButton>
          </form>
          <form action={deleteAccount.bind(null, a.id)} className="inline ml-0.5">
            <ConfirmSubmitButton
              size="sm"
              variant="danger"
              className="px-1.5"
              aria-label={t('deleteShort')}
              title={t('deleteShort')}
              confirmMessage={t('confirmDelete', { username: accountLabel })}
              destructive
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </ConfirmSubmitButton>
          </form>
        </Td>
      </tr>
      {expanded && (
        <tr className="border-b border-[color:var(--color-border)] last:border-0 bg-[color:var(--color-muted)]/30">
          <td colSpan={8} className="px-6 py-3">
            <ExpandedVideosList videos={expandedVideos} locale={locale} />
          </td>
        </tr>
      )}
    </>
  );
}

function contentTypeLabelKey(contentType: string) {
  return ['video', 'reel', 'carousel', 'post'].includes(contentType)
    ? (contentType as 'video' | 'reel' | 'carousel' | 'post')
    : 'video';
}

function isPlayableContent(contentType: string) {
  return contentType === 'video' || contentType === 'reel';
}

function ContentTypeBadge({ contentType, label }: { contentType: string; label: string }) {
  return (
    <span
      data-content-type={contentType}
      className="inline-flex shrink-0 items-center rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-normal text-[color:var(--color-muted-foreground)]"
    >
      {label}
    </span>
  );
}

function ExpandedVideosList({ videos, locale }: { videos: VideoRow[]; locale: string }) {
  const t = useTranslations('accountsTable');
  const common = useTranslations('common');
  const contentType = useTranslations('contentType');
  if (videos.length === 0) {
    return (
      <div className="text-xs text-[color:var(--color-muted-foreground)] py-3 text-center">
        {t('noVideosInRange')}
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      {videos.map((v) => (
        <div
          key={v.id}
          className="flex items-center gap-3 text-xs py-1 border-b border-[color:var(--color-border)] last:border-0"
        >
          <ContentTypeBadge
            contentType={v.contentType}
            label={contentType(contentTypeLabelKey(v.contentType))}
          />
          <a
            href={v.videoUrl ?? '#'}
            target="_blank"
            rel="noreferrer"
            className="flex-1 min-w-0 line-clamp-1 hover:underline text-[color:var(--color-foreground)]"
          >
            {v.description || `(${common('emptyDescription')}) · ${v.videoId}`}
          </a>
          <span className="tabular-nums font-semibold w-16 text-right">
            ▶ {isPlayableContent(v.contentType) ? formatCompact(v.playCount, locale) : '-'}
          </span>
          <span className="tabular-nums text-[color:var(--color-muted-foreground)] w-14 text-right">
            ♥ {formatCompact(v.likeCount, locale)}
          </span>
          <span className="text-[color:var(--color-muted-foreground)] w-20 text-right whitespace-nowrap">
            {formatRelative(v.publishedAt, locale)}
          </span>
        </div>
      ))}
    </div>
  );
}
