import { cn } from '@/lib/utils';
import { formatRelative } from '@/components/format';
import { useLocale, useTranslations } from 'next-intl';

interface Props {
  status: string | null;
  // raw SQL 子查询返回 timestamptz 字符串；schema-aware 查询返回 Date；旧 SQLite 时代为 unix 秒 number
  startedAt: Date | number | string | null;
  finishedAt: Date | number | string | null;
  errorMessage: string | null;
  errorCode: string | null;
}

function toDate(value: Date | number | string | null | undefined): Date | null {
  if (value == null) return null;
  if (value instanceof Date) return value;
  if (typeof value === 'number') return new Date(value * 1000);
  // Postgres 迁移后 raw sql 标量子查询的 timestamptz 返回字符串
  // （如 "2026-05-15 05:15:28+00"），不再是 SQLite 时代的 unix 秒 number。
  if (typeof value === 'string') {
    const d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }
  return null;
}

// 只用颜色表达状态：圆点，无文字。具体状态/错误放 hover title。
const DOT_CLASS: Record<string, string> = {
  queued: 'bg-[hsl(45_93%_47%)]',
  running: 'bg-[hsl(214_93%_52%)] animate-pulse',
  success: 'bg-[hsl(142_71%_42%)]',
  failed: 'bg-[color:var(--color-destructive)]',
};

// failed 时按 error_code 细分点色。account_gone/creds_missing 红；
// rate_limited 橙；其余（network/login/challenge/browser/unknown）灰。
const ERROR_DOT_CLASS: Record<string, string> = {
  account_gone: 'bg-[color:var(--color-destructive)]',
  creds_missing: 'bg-[color:var(--color-destructive)]',
  rate_limited: 'bg-[hsl(25_95%_53%)]',
};

// error_code 是 DB 自由文本列：scraper 只写这些已知码，但历史数据/手工写入
// 可能出现未知码。tc() 命中缺失 i18n key 会报错，故未知码统一降级 unknown。
// 这套必须与 messages/*.json 的 errorCode.* key 一致。
const KNOWN_ERROR_CODES = new Set([
  'account_gone',
  'creds_missing',
  'rate_limited',
  'auth_challenge',
  'login_required',
  'browser_unavailable',
  'network_error',
  'unknown',
]);

function normalizeErrorCode(code: string | null): string {
  return code && KNOWN_ERROR_CODES.has(code) ? code : 'unknown';
}

/**
 * 账号最新抓取状态：仅一个彩色圆点 + 相对时间。
 * 具体状态文案与 failed 的 errorMessage 走 hover title，不在界面写字。
 */
export function FetchStatusBadge({
  status,
  startedAt,
  finishedAt,
  errorMessage,
  errorCode,
}: Props) {
  const locale = useLocale();
  const t = useTranslations('fetchStatus');
  const tc = useTranslations('errorCode');

  const isFailed = status === 'failed';
  const code = isFailed ? normalizeErrorCode(errorCode) : null;

  const dotCls = isFailed
    ? (ERROR_DOT_CLASS[code as string] ?? 'bg-[color:var(--color-muted-foreground)]')
    : status
      ? (DOT_CLASS[status] ?? 'bg-[color:var(--color-muted-foreground)]')
      : 'bg-[color:var(--color-muted-foreground)]';

  // failed → 用 errorCode 的中文标签；其它状态沿用 fetchStatus 文案
  const label = isFailed
    ? tc(code as string)
    : status
      ? (status in DOT_CLASS ? t(status) : status)
      : t('never');
  const tooltip = isFailed && errorMessage ? `${label} — ${errorMessage}` : label;

  const time = status
    ? toDate(status === 'success' || status === 'failed' ? finishedAt : startedAt)
    : null;

  return (
    <div className="flex items-center gap-1.5" title={tooltip} aria-label={tooltip}>
      <span className={cn('inline-block h-2.5 w-2.5 shrink-0 rounded-full', dotCls)} />
      {time && (
        <span className="text-[10px] text-[color:var(--color-muted-foreground)] whitespace-nowrap">
          {formatRelative(time, locale)}
        </span>
      )}
    </div>
  );
}
