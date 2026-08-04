import type { Route } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { ArrowLeft, ExternalLink, ShieldCheck } from 'lucide-react';
import { getTranslations } from 'next-intl/server';

import irisMark from '@/assets/auth/iris-mark.png';
import watercolorField from '@/assets/auth/iris-watercolor-field-v2.webp';
import { AdminNav } from '@/components/admin/admin-nav';
import { BotanicalAccent } from '@/components/botanical-accent';
import { LanguageSwitcher } from '@/components/language-switcher';
import { UserMenu } from '@/components/user-menu';
import { requireAdminLocale } from '@/lib/auth/viewer';

type Props = Readonly<{ children: React.ReactNode; params: Promise<{ locale: string }> }>;

export default async function AdminLayout({ children, params }: Props) {
  const { locale } = await params;
  const viewer = await requireAdminLocale(locale);
  const t = await getTranslations({ locale, namespace: 'admin.shell' });

  return (
    <div className="flex min-h-screen bg-[var(--color-background)]">
      <aside className="sticky top-0 z-20 flex h-screen w-[232px] shrink-0 flex-col overflow-hidden border-r border-[var(--color-sidebar-border)] bg-[var(--color-sidebar)] px-4 py-5">
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
          <Link href={`/${locale}/admin` as Route} className="mb-6 flex items-center gap-2.5 px-2">
            <Image src={irisMark} alt="" className="h-8 w-auto" aria-hidden />
            <div>
              <div className="text-lg font-semibold [font-family:var(--font-serif)]">Iris</div>
              <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-primary)]">
                <ShieldCheck size={12} aria-hidden /> {t('title')}
              </div>
            </div>
          </Link>
          <AdminNav locale={locale} />
          <div className="mt-auto space-y-2 border-t pt-4">
            <Link href={`/${locale}/monitor` as Route} className="flex h-8 items-center gap-2 rounded-md px-2 text-xs font-semibold hover:bg-[var(--color-muted)]">
              <ArrowLeft size={14} aria-hidden /> {t('backMonitor')}
            </Link>
            <Link href={`/${locale}/studio` as Route} className="flex h-8 items-center gap-2 rounded-md px-2 text-xs font-semibold hover:bg-[var(--color-muted)]">
              <ExternalLink size={14} aria-hidden /> {t('openOps')}
            </Link>
            <LanguageSwitcher />
            <UserMenu username={viewer.username} isAdmin />
          </div>
        </div>
      </aside>
      <main className="relative min-w-0 flex-1 overflow-x-auto bg-[var(--color-background)]">
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
        <div className="relative z-10 mx-auto min-h-screen max-w-[1600px] px-6 py-7 lg:px-8">{children}</div>
      </main>
    </div>
  );
}
