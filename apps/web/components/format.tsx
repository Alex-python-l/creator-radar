import type { SimpleIcon } from 'simple-icons';
import { siFacebook, siInstagram, siTiktok, siYoutube } from 'simple-icons';

export function formatCompact(n: number | null | undefined, locale = 'en'): string {
  if (!n && n !== 0) return '—';
  return new Intl.NumberFormat(locale === 'zh' ? 'zh-CN' : 'en', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(n);
}

export function formatRelative(date: Date | null | undefined, locale = 'zh'): string {
  if (!date) return '—';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (locale === 'en') {
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
    return date.toLocaleDateString('en');
  }
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return date.toLocaleDateString('zh-CN');
}

export function formatDate(date: Date | null | undefined, locale = 'zh'): string {
  if (!date) return '—';
  return date.toLocaleDateString(locale === 'zh' ? 'zh-CN' : 'en');
}

const platformIcons: Record<string, SimpleIcon> = {
  tiktok: siTiktok,
  instagram: siInstagram,
  facebook: siFacebook,
  youtube: siYoutube,
};

const platformFallback: Record<string, { bg: string; fg: string; label: string; title: string }> = {
  douyin: { bg: '#000', fg: '#fff', label: '抖', title: 'Douyin' },
};

export function PlatformMark({ platform }: { platform: string }) {
  const icon = platformIcons[platform];
  if (icon) {
    return (
      <span
        aria-hidden="true"
        title={`${icon.title} logo`}
        className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-[color:var(--color-border)] bg-white p-0.5"
      >
        <svg viewBox="0 0 24 24" className="h-full w-full" role="img">
          <path d={icon.path} fill={`#${icon.hex}`} />
        </svg>
      </span>
    );
  }

  const fb = platformFallback[platform];
  if (fb) {
    return (
      <span
        aria-hidden="true"
        title={`${fb.title} logo`}
        className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-[color:var(--color-border)] text-[10px] font-bold"
        style={{ background: fb.bg, color: fb.fg }}
      >
        {fb.label}
      </span>
    );
  }

  return (
    <span
      aria-hidden="true"
      className="inline-flex h-5 w-5 shrink-0 rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)]"
    />
  );
}
