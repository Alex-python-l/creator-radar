import Image from 'next/image';
import Link from 'next/link';

import {AppSidebarNav} from '@/components/app-sidebar-nav';
import {BotanicalAccent} from '@/components/botanical-accent';
import {LanguageSwitcher} from '@/components/language-switcher';
import {ProductSwitcher} from '@/components/product-switcher';
import {UserMenu} from '@/components/user-menu';
import {WorkspaceSwitcher} from '@/components/workspace-switcher';
import {getUserTeam, requireViewer} from '@/lib/auth/viewer';
import type {AppLocale} from '@/i18n/routing';
import irisMark from '@/assets/auth/iris-mark.png';
import watercolorField from '@/assets/auth/iris-watercolor-field-v2.webp';

type Props = Readonly<{
  children: React.ReactNode;
  params: Promise<{locale: string}>;
}>;

// 业务页统一壳:侧栏 + 登录门。requireViewer() 查库校验会话
// (middleware 只做乐观 cookie 检查),未登录 302 /login。
export default async function AppLayout({children, params}: Props) {
  const viewer = await requireViewer();
  const team = await getUserTeam(viewer.id);
  const {locale} = await params;
  const currentLocale = locale as AppLocale;

  return (
    <div className="flex min-h-screen flex-col bg-[color:var(--color-background)] md:flex-row">
      <aside className="relative z-20 flex w-full shrink-0 flex-col overflow-hidden border-b border-[color:var(--color-sidebar-border)] bg-[color:var(--color-sidebar)] px-4 py-6 md:sticky md:top-0 md:h-screen md:w-[232px] md:border-b-0 md:border-r">
        <BotanicalAccent
          variant="sprig"
          sizes="420px"
          mirrored
          className="absolute -left-44 top-20 w-[420px] rotate-[24deg] opacity-[0.15]"
        />
        <BotanicalAccent
          variant="cluster"
          sizes="420px"
          className="absolute -bottom-20 -left-[104px] h-[570px] w-[428px] max-w-none object-contain object-bottom opacity-[0.24]"
        />
        <BotanicalAccent
          variant="petals"
          sizes="180px"
          motion="drift"
          className="absolute -right-10 top-[42%] w-[180px] opacity-[0.12]"
        />
        <div className="relative z-10 flex min-h-0 flex-1 flex-col">
          <Link href={`/${currentLocale}/monitor`} className="mb-6 flex items-center gap-2.5 px-2">
            <Image
              src={irisMark}
              alt=""
              className="h-8 w-auto"
              aria-hidden="true"
            />
            <div className="text-[21px] font-semibold text-[color:var(--color-foreground)] [font-family:var(--font-serif)]">
              Iris
            </div>
          </Link>
          <ProductSwitcher />
          <WorkspaceSwitcher
            active={viewer.workspaceKind}
            personalName={viewer.username}
            team={team ? {id: team.id, name: team.name} : null}
          />
          <AppSidebarNav />
          <div className="mt-auto space-y-2 px-2 pt-4">
            <LanguageSwitcher />
            <UserMenu username={viewer.username} isAdmin={viewer.isAdmin} />
          </div>
        </div>
      </aside>
      <main className="relative min-w-0 flex-1 overflow-x-auto bg-[color:var(--color-background)]">
        <Image
          src={watercolorField}
          alt=""
          fill
          priority
          unoptimized
          sizes="calc(100vw - 232px)"
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 object-cover opacity-[0.28]"
        />
        <BotanicalAccent
          variant="sprig"
          sizes="760px"
          mirrored
          className="absolute -right-72 -top-28 w-[760px] -rotate-[9deg] opacity-[0.12]"
        />
        <BotanicalAccent
          variant="petals"
          sizes="360px"
          motion="drift"
          className="absolute right-[8%] top-8 w-[360px] opacity-[0.1]"
        />
        <BotanicalAccent
          variant="meadow"
          sizes="calc(100vw - 232px)"
          className="absolute bottom-0 right-0 w-full opacity-[0.1]"
        />
        <div className="relative z-10 min-h-screen">{children}</div>
      </main>
    </div>
  );
}
