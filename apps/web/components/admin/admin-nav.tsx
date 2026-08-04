'use client';

import type { Route } from 'next';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslations } from 'next-intl';
import {
  ClipboardList,
  FileClock,
  LayoutDashboard,
  PanelsTopLeft,
  Users,
  UsersRound,
} from 'lucide-react';

import { cn } from '@/lib/utils';

const items = [
  { id: 'overview', path: '', icon: LayoutDashboard },
  { id: 'users', path: '/users', icon: Users },
  { id: 'teams', path: '/teams', icon: UsersRound },
  { id: 'workspaces', path: '/workspaces', icon: PanelsTopLeft },
  { id: 'applications', path: '/applications', icon: ClipboardList },
  { id: 'audit', path: '/audit', icon: FileClock },
] as const;

export function AdminNav({ locale }: { locale: string }) {
  const pathname = usePathname();
  const t = useTranslations('admin.nav');
  const base = `/${locale}/admin`;
  return (
    <nav className="space-y-1">
      {items.map((item) => {
        const href = `${base}${item.path}`;
        const active = item.path
          ? pathname === href || pathname.startsWith(`${href}/`)
          : pathname === base;
        const Icon = item.icon;
        return (
          <Link
            key={item.id}
            href={href as Route}
            className={cn(
              'flex h-9 items-center gap-2.5 rounded-md px-3 text-sm font-semibold transition-colors',
              active
                ? 'bg-[var(--color-primary)] text-white shadow-[0_5px_14px_rgba(96,69,194,0.18)]'
                : 'text-[var(--color-sidebar-foreground)] hover:bg-[var(--color-muted)]',
            )}
          >
            <Icon size={16} aria-hidden />
            {t(item.id)}
          </Link>
        );
      })}
    </nav>
  );
}
