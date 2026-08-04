import type { Route } from 'next';
import Link from 'next/link';
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight } from 'lucide-react';
import { getTranslations } from 'next-intl/server';
import type { ReactNode } from 'react';

import { Card } from '@/components/ui';
import { cn } from '@/lib/utils';

export function AdminPageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col justify-between gap-4 md:flex-row md:items-end">
      <div>
        <h1 className="text-[28px] font-semibold [font-family:var(--font-serif)]">{title}</h1>
        {description ? <p className="mt-1.5 text-sm text-[var(--color-muted-foreground)]">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

export function AdminActionCard({
  title,
  description,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn('p-4', className)}>
      <h2 className="text-sm font-semibold">{title}</h2>
      {description ? <p className="mt-1 text-xs leading-5 text-[var(--color-muted-foreground)]">{description}</p> : null}
      <div className="mt-4">{children}</div>
    </Card>
  );
}

export async function AdminFeedback({
  locale,
  notice,
  error,
}: {
  locale: string;
  notice?: string;
  error?: string;
}) {
  const code = error || notice;
  if (!code) return null;
  const t = await getTranslations({ locale, namespace: 'admin.feedback' });
  const translated = t.has(code) ? t(code) : code.replaceAll('_', ' ');
  return (
    <div
      role={error ? 'alert' : 'status'}
      className={cn(
        'mb-5 flex items-center gap-2 rounded-lg border px-4 py-3 text-sm',
        error
          ? 'border-red-200 bg-red-50 text-red-700'
          : 'border-emerald-200 bg-emerald-50 text-emerald-700',
      )}
    >
      {error ? <AlertCircle size={16} aria-hidden /> : <CheckCircle2 size={16} aria-hidden />}
      {translated}
    </div>
  );
}

export function AdminPagination({
  basePath,
  page,
  pages,
  search,
  previousLabel,
  nextLabel,
}: {
  basePath: string;
  page: number;
  pages: number;
  search: Record<string, string | undefined>;
  previousLabel: string;
  nextLabel: string;
}) {
  if (pages <= 1) return null;
  const href = (target: number) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(search)) if (value) params.set(key, value);
    params.set('page', String(target));
    return `${basePath}?${params}` as Route;
  };
  return (
    <div className="mt-4 flex items-center justify-between text-sm">
      <span className="text-[var(--color-muted-foreground)]">{page} / {pages}</span>
      <div className="flex gap-2">
        {page > 1 ? (
          <Link href={href(page - 1)} className="inline-flex h-9 items-center gap-1 rounded-md border bg-white px-3 font-semibold">
            <ChevronLeft size={15} /> {previousLabel}
          </Link>
        ) : null}
        {page < pages ? (
          <Link href={href(page + 1)} className="inline-flex h-9 items-center gap-1 rounded-md border bg-white px-3 font-semibold">
            {nextLabel} <ChevronRight size={15} />
          </Link>
        ) : null}
      </div>
    </div>
  );
}

export function EmptyAdminState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border bg-white/75 p-10 text-center text-sm text-[var(--color-muted-foreground)]">
      {children}
    </div>
  );
}

export function DefinitionList({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[130px_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">{children}</dl>;
}

export function Definition({ term, children }: { term: ReactNode; children: ReactNode }) {
  return (
    <>
      <dt className="text-[var(--color-muted-foreground)]">{term}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}
