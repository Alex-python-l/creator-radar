import type { Route } from 'next';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';

import { AdminPageHeader, AdminPagination, EmptyAdminState } from '@/components/admin/admin-ui';
import { Badge, DataTableShell, TBody, Td, Th, THead, Tr } from '@/components/ui';
import { listAdminTeams, type AdminTeamFilters } from '@/lib/admin/queries';

type Props = { params: Promise<{ locale: string }>; searchParams: Promise<AdminTeamFilters> };

export default async function AdminTeamsPage({ params, searchParams }: Props) {
  const [{ locale }, filters] = await Promise.all([params, searchParams]);
  const [result, t, common] = await Promise.all([
    listAdminTeams(filters),
    getTranslations({ locale, namespace: 'admin.teams' }),
    getTranslations({ locale, namespace: 'admin.common' }),
  ]);
  const date = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', { dateStyle: 'medium' });
  return <>
    <AdminPageHeader title={t('title')} description={t('description')} />
    <form className="mb-5 flex flex-wrap gap-2 rounded-xl border bg-white/80 p-3 shadow-sm"><input name="q" defaultValue={filters.q} placeholder={t('search')} className="h-9 min-w-64 rounded-md border bg-white px-3 text-sm" /><select name="usage" defaultValue={filters.usage ?? ''} className="h-9 rounded-md border bg-white px-3 text-sm"><option value="">{t('allUsage')}</option><option value="available">{t('withinQuota')}</option><option value="exceeded">{t('exceeded')}</option></select><button className="h-9 rounded-md bg-[var(--color-primary)] px-4 text-sm font-semibold text-white">{common('filter')}</button></form>
    {result.rows.length ? <DataTableShell><table className="w-full"><THead><tr><Th>{t('team')}</Th><Th>{t('owner')}</Th><Th>{t('members')}</Th><Th>{t('usage')}</Th><Th>{t('workspace')}</Th><Th>{t('created')}</Th></tr></THead><TBody>{result.rows.map((row) => <Tr key={row.id}><Td><Link href={`/${locale}/admin/teams/${row.id}` as Route} className="font-semibold text-[var(--color-primary)]">{row.name}</Link><div className="text-xs text-[var(--color-muted-foreground)]">{row.slug}</div></Td><Td><Link href={`/${locale}/admin/users/${row.ownerId}` as Route}>{row.ownerName}</Link><div className="text-xs text-[var(--color-muted-foreground)]">{row.ownerEmail}</div></Td><Td>{Number(row.memberCount)}</Td><Td><span className="tabular-nums">{Number(row.usage)}/{row.quota}</span>{Number(row.usage) > row.quota ? <Badge tone="red" className="ml-2">{t('exceeded')}</Badge> : null}</Td><Td>#{row.workspaceId}</Td><Td>{date.format(row.createdAt)}</Td></Tr>)}</TBody></table></DataTableShell> : <EmptyAdminState>{common('empty')}</EmptyAdminState>}
    <AdminPagination basePath={`/${locale}/admin/teams`} page={result.page} pages={result.pages} search={filters} previousLabel={common('previous')} nextLabel={common('next')} />
  </>;
}
