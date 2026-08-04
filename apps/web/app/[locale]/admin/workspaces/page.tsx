import type { Route } from 'next';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';

import { AdminPageHeader, AdminPagination, EmptyAdminState } from '@/components/admin/admin-ui';
import { Badge, DataTableShell, TBody, Td, Th, THead, Tr } from '@/components/ui';
import { listAdminWorkspaces, type AdminWorkspaceFilters } from '@/lib/admin/queries';

type Props = { params: Promise<{ locale: string }>; searchParams: Promise<AdminWorkspaceFilters> };

export default async function AdminWorkspacesPage({ params, searchParams }: Props) {
  const [{ locale }, filters] = await Promise.all([params, searchParams]);
  const [result, t, common] = await Promise.all([listAdminWorkspaces(filters), getTranslations({ locale, namespace: 'admin.workspaces' }), getTranslations({ locale, namespace: 'admin.common' })]);
  const date = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', { dateStyle: 'medium' });
  return <>
    <AdminPageHeader title={t('title')} description={t('description')} />
    <form className="mb-5 flex flex-wrap gap-2 rounded-xl border bg-white/80 p-3 shadow-sm"><input name="q" defaultValue={filters.q} placeholder={t('search')} className="h-9 min-w-64 rounded-md border bg-white px-3 text-sm" /><select name="kind" defaultValue={filters.kind ?? ''} className="h-9 rounded-md border bg-white px-3 text-sm"><option value="">{t('allKinds')}</option><option value="personal">personal</option><option value="team">team</option></select><select name="usage" defaultValue={filters.usage ?? ''} className="h-9 rounded-md border bg-white px-3 text-sm"><option value="">{t('allUsage')}</option><option value="available">{t('withinQuota')}</option><option value="exceeded">{t('exceeded')}</option></select><button className="h-9 rounded-md bg-[var(--color-primary)] px-4 text-sm font-semibold text-white">{common('filter')}</button></form>
    {result.rows.length ? <DataTableShell><table className="w-full"><THead><tr><Th>{t('workspace')}</Th><Th>{t('kind')}</Th><Th>{t('owner')}</Th><Th>{t('usage')}</Th><Th>{t('created')}</Th><Th>{t('govern')}</Th></tr></THead><TBody>{result.rows.map((row) => { const ownerHref = row.kind === 'team' ? `/${locale}/admin/teams/${row.teamId}` : `/${locale}/admin/users/${row.userId}`; return <Tr key={row.id}><Td className="font-mono text-xs">#{row.id}</Td><Td><Badge tone={row.kind === 'team' ? 'blue' : 'gray'}>{row.kind}</Badge></Td><Td>{row.kind === 'team' ? row.teamName : <><div>{row.userName}</div><div className="text-xs text-[var(--color-muted-foreground)]">{row.userEmail}</div></>}</Td><Td><span className="tabular-nums">{Number(row.usage)}/{Number(row.quota)}</span>{Number(row.usage) > Number(row.quota) ? <Badge tone="red" className="ml-2">{t('exceeded')}</Badge> : null}</Td><Td>{date.format(row.createdAt)}</Td><Td><Link href={ownerHref as Route} className="font-semibold text-[var(--color-primary)]">{t('openOwner')}</Link></Td></Tr>; })}</TBody></table></DataTableShell> : <EmptyAdminState>{common('empty')}</EmptyAdminState>}
    <AdminPagination basePath={`/${locale}/admin/workspaces`} page={result.page} pages={result.pages} search={filters} previousLabel={common('previous')} nextLabel={common('next')} />
  </>;
}
