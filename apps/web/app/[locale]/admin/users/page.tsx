import type { Route } from 'next';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';

import { AdminFeedback, AdminPageHeader, AdminPagination, EmptyAdminState } from '@/components/admin/admin-ui';
import { Badge, DataTableShell, TBody, Td, Th, THead, Tr } from '@/components/ui';
import { listAdminUsers, type AdminUserFilters } from '@/lib/admin/queries';

type Props = { params: Promise<{ locale: string }>; searchParams: Promise<AdminUserFilters & {notice?: string; error?: string}> };

export default async function AdminUsersPage({ params, searchParams }: Props) {
  const [{ locale }, query] = await Promise.all([params, searchParams]);
  const { notice, error, ...filters } = query;
  const [result, t, common] = await Promise.all([
    listAdminUsers(filters),
    getTranslations({ locale, namespace: 'admin.users' }),
    getTranslations({ locale, namespace: 'admin.common' }),
  ]);
  const date = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', { dateStyle: 'medium' });

  return (
    <>
      <AdminPageHeader title={t('title')} description={t('description')} />
      <AdminFeedback locale={locale} notice={notice} error={error} />
      <form className="mb-5 flex flex-wrap gap-2 rounded-xl border bg-white/80 p-3 shadow-sm">
        <input name="q" defaultValue={filters.q} placeholder={t('search')} className="h-9 min-w-64 rounded-md border bg-white px-3 text-sm" />
        <select name="role" defaultValue={filters.role ?? ''} className="h-9 rounded-md border bg-white px-3 text-sm"><option value="">{t('allRoles')}</option><option value="admin">admin</option><option value="user">user</option></select>
        <select name="state" defaultValue={filters.state ?? ''} className="h-9 rounded-md border bg-white px-3 text-sm"><option value="">{t('allStates')}</option><option value="active">{t('active')}</option><option value="verified">{t('verified')}</option><option value="unverified">{t('unverified')}</option><option value="banned">{t('banned')}</option></select>
        <select name="team" defaultValue={filters.team ?? ''} className="h-9 rounded-md border bg-white px-3 text-sm"><option value="">{t('allTeams')}</option><option value="yes">{t('hasTeam')}</option><option value="no">{t('noTeam')}</option></select>
        <button className="h-9 rounded-md bg-[var(--color-primary)] px-4 text-sm font-semibold text-white">{common('filter')}</button>
      </form>
      {result.rows.length ? (
        <DataTableShell>
          <table className="w-full">
            <THead><tr><Th>{t('identity')}</Th><Th>{t('role')}</Th><Th>{t('team')}</Th><Th>{t('personalUsage')}</Th><Th>{t('sessions')}</Th><Th>{t('created')}</Th></tr></THead>
            <TBody>{result.rows.map((row) => (
              <Tr key={row.id}>
                <Td><Link href={`/${locale}/admin/users/${row.id}` as Route} className="font-semibold text-[var(--color-primary)]">{row.name}</Link><div className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">{row.email} · {row.username || '—'}</div></Td>
                <Td><div className="flex flex-wrap gap-1"><Badge tone={row.role === 'admin' ? 'blue' : 'gray'}>{row.role || 'user'}</Badge>{row.banned ? <Badge tone="red">{t('banned')}</Badge> : null}{!row.emailVerified ? <Badge tone="orange">{t('unverified')}</Badge> : null}</div></Td>
                <Td>{row.teamId ? <Link href={`/${locale}/admin/teams/${row.teamId}` as Route} className="font-medium">{row.teamName}</Link> : '—'}{row.teamRole ? <div className="text-xs text-[var(--color-muted-foreground)]">{row.teamRole}</div> : null}</Td>
                <Td><span className="tabular-nums">{Number(row.usage)}/{row.monitorQuota ?? 0}</span></Td>
                <Td>{Number(row.sessions)}</Td><Td>{date.format(row.createdAt)}</Td>
              </Tr>
            ))}</TBody>
          </table>
        </DataTableShell>
      ) : <EmptyAdminState>{common('empty')}</EmptyAdminState>}
      <AdminPagination basePath={`/${locale}/admin/users`} page={result.page} pages={result.pages} search={filters} previousLabel={common('previous')} nextLabel={common('next')} />
    </>
  );
}
