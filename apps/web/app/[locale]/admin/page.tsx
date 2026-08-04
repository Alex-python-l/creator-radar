import type { Route } from 'next';
import Link from 'next/link';
import { ClipboardList, FileClock, PanelsTopLeft, Users, UsersRound } from 'lucide-react';
import { getTranslations } from 'next-intl/server';

import { AdminPageHeader, EmptyAdminState } from '@/components/admin/admin-ui';
import { Badge, DataTableShell, MetricCard, TBody, Td, Th, THead, Tr } from '@/components/ui';
import { getAdminOverview } from '@/lib/admin/queries';

type Props = { params: Promise<{ locale: string }> };

export default async function AdminOverviewPage({ params }: Props) {
  const { locale } = await params;
  const [data, t] = await Promise.all([
    getAdminOverview(),
    getTranslations({ locale, namespace: 'admin.overview' }),
  ]);
  const dateTime = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', {
    dateStyle: 'medium', timeStyle: 'short',
  });

  return (
    <>
      <AdminPageHeader title={t('title')} description={t('description')} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label={t('totalUsers')} value={data.users.total} description={t('newUsers', { count: data.users.newUsers })} tone="blue" icon={<Users size={18} />} />
        <MetricCard label={t('teams')} value={data.teams} description={t('admins', { count: data.users.admins })} tone="gray" icon={<UsersRound size={18} />} />
        <MetricCard label={t('workspaces')} value={data.workspaces.total} description={t('workspaceKinds', { personal: data.workspaces.personal, team: data.workspaces.team })} tone={data.workspaces.exceeded ? 'red' : 'green'} icon={<PanelsTopLeft size={18} />} />
        <MetricCard label={t('pendingApplications')} value={data.pendingApplications} description={t('needsReview')} tone={data.pendingApplications ? 'orange' : 'green'} icon={<ClipboardList size={18} />} />
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <Link href={`/${locale}/admin/users?state=unverified` as Route} className="rounded-xl border bg-white/80 p-4 text-sm shadow-sm transition hover:bg-[var(--color-muted)]">
          <div className="flex items-center justify-between"><span className="font-semibold">{t('unverified')}</span><Badge tone={data.users.unverified ? 'orange' : 'green'}>{data.users.unverified}</Badge></div>
        </Link>
        <Link href={`/${locale}/admin/users?state=banned` as Route} className="rounded-xl border bg-white/80 p-4 text-sm shadow-sm transition hover:bg-[var(--color-muted)]">
          <div className="flex items-center justify-between"><span className="font-semibold">{t('banned')}</span><Badge tone={data.users.banned ? 'red' : 'green'}>{data.users.banned}</Badge></div>
        </Link>
        <Link href={`/${locale}/admin/workspaces?usage=exceeded` as Route} className="rounded-xl border bg-white/80 p-4 text-sm shadow-sm transition hover:bg-[var(--color-muted)]">
          <div className="flex items-center justify-between"><span className="font-semibold">{t('quotaExceeded')}</span><Badge tone={data.workspaces.exceeded ? 'red' : 'green'}>{data.workspaces.exceeded}</Badge></div>
        </Link>
      </div>

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold"><FileClock size={17} />{t('recentAudit')}</h2>
          <Link href={`/${locale}/admin/audit` as Route} className="text-sm font-semibold text-[var(--color-primary)]">{t('viewAll')}</Link>
        </div>
        {data.recentAudit.length ? (
          <DataTableShell>
            <table className="w-full">
              <THead><tr><Th>{t('actor')}</Th><Th>{t('action')}</Th><Th>{t('target')}</Th><Th>{t('time')}</Th></tr></THead>
              <TBody>{data.recentAudit.map((row) => (
                <Tr key={row.id}><Td>{row.actorName || row.actorEmail || '—'}</Td><Td><code className="text-xs">{row.action}</code></Td><Td>{row.targetType} #{row.targetId}</Td><Td>{dateTime.format(row.createdAt)}</Td></Tr>
              ))}</TBody>
            </table>
          </DataTableShell>
        ) : <EmptyAdminState>{t('noAudit')}</EmptyAdminState>}
      </section>
    </>
  );
}
