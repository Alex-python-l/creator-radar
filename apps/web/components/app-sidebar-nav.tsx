'use client';

import type { Route } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import {
  BarChart3,
  DollarSign,
  GitBranch,
  MessageCircle,
  MessageSquareText,
  Settings,
  Telescope,
  UserRoundX,
  Users,
  UsersRound,
  Video,
  Wrench,
} from 'lucide-react';
import type { ReactNode } from 'react';

import { StudioSidebarWidgets } from '@/components/studio/sidebar-widgets';
import irisMark from '@/assets/auth/iris-mark.png';
import { cn } from '@/lib/utils';

// 统一侧栏的导航区:按当前 section(监控/运营)切换导航集。壳(logo、
// 切换器、语言、用户菜单)保持在 (app)/layout,这里只管中间的 nav。
// 运营能力按工作空间角色控制；平台管理入口独立放在用户菜单。

type StudioTab =
  | 'dashboard'
  | 'accounts'
  | 'costs'
  | 'ops'
  | 'sagas'
  | 'videos'
  | 'comments'
  | 'fbComments'
  | 'settings';

const STUDIO_NAV_GROUPS: Array<{
  id: 'operate' | 'content' | 'system';
  items: Array<{ id: StudioTab; path: string; end?: boolean; icon: ReactNode }>;
}> = [
  {
    id: 'operate',
    items: [
      { id: 'dashboard', path: '', end: true, icon: <BarChart3 size={15} /> },
      { id: 'accounts', path: '/accounts', icon: <Users size={15} /> },
      { id: 'costs', path: '/costs', icon: <DollarSign size={15} /> },
      { id: 'ops', path: '/ops', icon: <Wrench size={15} /> },
      { id: 'sagas', path: '/sagas', icon: <GitBranch size={15} /> },
    ],
  },
  {
    id: 'content',
    items: [
      { id: 'videos', path: '/videos', icon: <Video size={15} /> },
      { id: 'comments', path: '/comments', end: true, icon: <MessageSquareText size={15} /> },
      { id: 'fbComments', path: '/comments/facebook', icon: <MessageCircle size={15} /> },
    ],
  },
  {
    id: 'system',
    items: [{ id: 'settings', path: '/settings', icon: <Settings size={15} /> }],
  },
];

function NavItem({
  href,
  active,
  icon,
  children,
}: {
  href: Route;
  active: boolean;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cn(
        'relative flex min-h-9 items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
        active
          ? 'bg-white/[0.78] font-semibold text-[var(--color-primary)] shadow-[0_5px_18px_rgba(67,47,126,0.05)]'
          : 'font-semibold text-[color:var(--color-sidebar-foreground)] hover:bg-[color:var(--color-muted)]',
      )}
    >
      {active ? (
        <Image src={irisMark} alt="" className="h-4 w-4 shrink-0 object-contain" aria-hidden />
      ) : null}
      {icon && !active ? (
        <span
          className={
            active ? 'text-[var(--color-foreground)]' : 'text-[var(--color-muted-foreground-2)]'
          }
        >
          {icon}
        </span>
      ) : null}
      <span>{children}</span>
    </Link>
  );
}

export function AppSidebarNav() {
  const pathname = usePathname();
  const locale = useLocale();
  const tLayout = useTranslations('layout');
  const tShell = useTranslations('studio.shell');

  const studioBase = `/${locale}/studio`;
  const inStudio = pathname === studioBase || pathname.startsWith(`${studioBase}/`);

  if (inStudio) {
    const isActive = (path: string, end?: boolean) => {
      const href = `${studioBase}${path}`;
      return end ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
    };
    return (
      <>
        <nav className="min-h-0 flex-1 space-y-3 overflow-y-auto">
          {STUDIO_NAV_GROUPS.map((group) => (
            <div key={group.id}>
              <div className="px-3 pb-1.5 text-xs font-semibold uppercase tracking-normal text-[var(--color-muted-foreground-3)]">
                {tShell(`group.${group.id}`)}
              </div>
              <div className="space-y-1">
                {group.items.map((n) => (
                  <NavItem
                    key={n.id}
                    href={`${studioBase}${n.path}` as Route}
                    active={isActive(n.path, n.end)}
                    icon={n.icon}
                  >
                    {tShell(n.id)}
                  </NavItem>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <StudioSidebarWidgets />
      </>
    );
  }

  const radarItems: Array<{ href: Route; label: string; end?: boolean; icon: ReactNode }> = [
    { href: `/${locale}/monitor` as Route, label: tLayout('accounts'), end: true, icon: <UsersRound size={16} /> },
    { href: `/${locale}/competitors` as Route, label: tLayout('competitors'), icon: <Telescope size={16} /> },
    { href: `/${locale}/gone-accounts` as Route, label: tLayout('goneAccounts'), icon: <UserRoundX size={16} /> },
    { href: `/${locale}/videos` as Route, label: tLayout('videos'), icon: <Video size={16} /> },
    { href: `/${locale}/team` as Route, label: locale === 'zh' ? '团队' : 'Team', icon: <Users size={16} /> },
  ];
  return (
    <nav className="space-y-1">
      {radarItems.map((item) => (
        <NavItem
          key={item.href}
          href={item.href}
          active={
            item.end
              ? pathname === item.href
              : pathname === item.href || pathname.startsWith(`${item.href}/`)
          }
          icon={item.icon}
        >
          {item.label}
        </NavItem>
      ))}
    </nav>
  );
}
