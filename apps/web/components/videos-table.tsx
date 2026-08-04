import type { Route } from 'next';
import Link from 'next/link';
import { getLocale, getTranslations } from 'next-intl/server';

import { PlatformMark, formatCompact, formatRelative } from '@/components/format';
import { Td, Th } from '@/components/ui';
import { formatAccountListLabel, formatAccountListTitle } from '@/lib/account-display';
import type { VideoWithAccount } from '@/lib/queries/videos';
import {
  SORT_COLS,
  type SortCol,
  type VideosSearchParams,
  buildQuery,
} from '@/lib/url-params';
import { cn } from '@/lib/utils';

interface Props {
  videos: VideoWithAccount[];
  current: VideosSearchParams;
  basePath: string;
  showAccountCol: boolean;
  emptyText?: string;
}

const RIGHT_ALIGN_COLS: SortCol[] = ['play_count', 'like_count', 'comment_count', 'share_count'];

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

export async function VideosTable({
  videos,
  current,
  basePath,
  showAccountCol,
  emptyText,
}: Props) {
  const locale = await getLocale();
  const common = await getTranslations('common');
  const t = await getTranslations('videosTable');
  const contentType = await getTranslations('contentType');

  if (videos.length === 0) {
    return (
      <div className="px-6 py-12 text-center text-sm text-[color:var(--color-muted-foreground)]">
        {emptyText ?? t('empty')}
      </div>
    );
  }

  return (
    <table className="w-full">
      <thead className="border-b border-[color:var(--color-border)]">
        <tr>
          {showAccountCol && <Th>{t('account')}</Th>}
          <Th className={showAccountCol ? 'w-[35%]' : 'w-[45%]'}>{t('video')}</Th>
          {SORT_COLS.map((col) => (
            <SortableTh
              key={col}
              col={col}
              current={current}
              basePath={basePath}
              label={common(`sort.${col}`)}
            />
          ))}
        </tr>
      </thead>
      <tbody>
        {videos.map((v) => {
          const account = {
            platform: v.influencerPlatform,
            username: v.influencerUsername,
            displayName: v.influencerDisplayName,
            alias: v.influencerAlias,
          };
          const accountLabel = formatAccountListLabel(account);
          const accountTitle = formatAccountListTitle(account);
          return (
            <tr key={v.id} className="border-b border-[color:var(--color-border)] last:border-0">
              {showAccountCol && (
                <Td>
                  <Link
                    href={`/${locale}/accounts/${v.influencerId}` as Route}
                    className="inline-flex items-center gap-2 hover:underline"
                    title={accountTitle}
                  >
                    <span className="inline-flex items-center justify-center rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-1.5 py-0.5 text-xs font-mono font-semibold text-[color:var(--color-muted-foreground)]">
                      #{v.influencerId}
                    </span>
                    <PlatformMark platform={v.influencerPlatform} />
                    <span className="font-semibold">{accountLabel}</span>
                  </Link>
                </Td>
              )}
              <Td>
                <div className="flex max-w-md items-start gap-2">
                  <ContentTypeBadge
                    contentType={v.contentType}
                    label={contentType(contentTypeLabelKey(v.contentType))}
                  />
                  <a
                    href={v.videoUrl ?? '#'}
                    target="_blank"
                    rel="noreferrer"
                    className="line-clamp-2 min-w-0 text-sm text-[color:var(--color-foreground)] hover:underline"
                  >
                    {v.description || `(${common('emptyDescription')}) · ${v.videoId}`}
                  </a>
                </div>
              </Td>
              <Td className="text-right tabular-nums font-semibold">
                {isPlayableContent(v.contentType) ? formatCompact(v.playCount, locale) : '-'}
              </Td>
              <Td className="text-right tabular-nums text-[color:var(--color-muted-foreground)]">
                {formatCompact(v.likeCount, locale)}
              </Td>
              <Td className="text-right tabular-nums text-[color:var(--color-muted-foreground)]">
                {formatCompact(v.commentCount, locale)}
              </Td>
              <Td className="text-right tabular-nums text-[color:var(--color-muted-foreground)]">
                {formatCompact(v.shareCount, locale)}
              </Td>
              <Td className="text-[color:var(--color-muted-foreground)] whitespace-nowrap">
                {formatRelative(v.publishedAt, locale)}
              </Td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

interface SortableThProps {
  col: SortCol;
  current: VideosSearchParams;
  basePath: string;
  label: string;
}

function SortableTh({ col, current, basePath, label }: SortableThProps) {
  const active = current.sort === col;
  const nextOrder = active && current.order === 'desc' ? 'asc' : 'desc';
  const href = `${basePath}${buildQuery(current, { sort: col, order: nextOrder })}` as Route;
  const isRightAlign = RIGHT_ALIGN_COLS.includes(col);
  const arrow = active ? (current.order === 'desc' ? '↓' : '↑') : '↕';

  return (
    <Th className={cn(isRightAlign && 'text-right', 'whitespace-nowrap')}>
      <Link
        href={href}
        scroll={false}
        className={cn(
          'inline-flex items-center gap-1 cursor-pointer hover:text-[color:var(--color-foreground)]',
          active && 'text-[color:var(--color-foreground)]',
        )}
      >
        {label}
        <span className="text-[10px]">{arrow}</span>
      </Link>
    </Th>
  );
}
