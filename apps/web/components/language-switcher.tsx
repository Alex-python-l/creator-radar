'use client';

import Link from 'next/link';
import type {Route} from 'next';
import {Languages} from 'lucide-react';
import {useLocale} from 'next-intl';
import {usePathname, useSearchParams} from 'next/navigation';

import {routing, type AppLocale} from '@/i18n/routing';

const NEXT_LABEL: Record<AppLocale, string> = {
  zh: 'English',
  en: '中文',
};

export function LanguageSwitcher() {
  const locale = useLocale() as AppLocale;
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const query = searchParams.toString();

  const target = (routing.locales.find((l) => l !== locale) ?? locale) as AppLocale;
  const pathWithoutLocale = pathname.replace(/^\/(zh|en)(?=\/|$)/, '') || '';
  const href = `/${target}${pathWithoutLocale}${query ? `?${query}` : ''}`;

  return (
    <Link
      href={href as Route}
      className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] px-2 text-xs font-semibold text-[color:var(--color-foreground)] transition hover:bg-[color:var(--color-muted)]"
      aria-label={`Switch to ${NEXT_LABEL[target]}`}
    >
      <Languages className="h-3.5 w-3.5" aria-hidden />
      {NEXT_LABEL[target]}
    </Link>
  );
}
