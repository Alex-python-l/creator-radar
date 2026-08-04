import type { Route } from 'next';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';

import { AdminPageHeader, AdminPagination, EmptyAdminState } from '@/components/admin/admin-ui';
import { DataTableShell, TBody, Td, Th, THead, Tr } from '@/components/ui';
import { listAdminAuditLogs, type AdminAuditFilters } from '@/lib/admin/queries';

type Props = { params: Promise<{ locale: string }>; searchParams: Promise<AdminAuditFilters> };

function summary(value: unknown) {
  if (!value || typeof value !== 'object') return '—';
  return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${item == null ? '—' : String(item)}`).join(' · ');
}

export default async function AdminAuditPage({ params, searchParams }: Props) {
  const [{ locale }, filters] = await Promise.all([params, searchParams]);
  const [result, t, common] = await Promise.all([listAdminAuditLogs(filters), getTranslations({ locale, namespace: 'admin.audit' }), getTranslations({ locale, namespace: 'admin.common' })]);
  const dateTime = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', { dateStyle: 'medium', timeStyle: 'short' });
  return <>
    <AdminPageHeader title={t('title')} description={t('description')} />
    <form className="mb-5 flex flex-wrap gap-2 rounded-xl border bg-white/80 p-3 shadow-sm"><input name="q" defaultValue={filters.q} placeholder={t('actorSearch')} className="h-9 min-w-56 rounded-md border bg-white px-3 text-sm" /><input name="action" defaultValue={filters.action} placeholder={t('actionSearch')} className="h-9 min-w-48 rounded-md border bg-white px-3 text-sm" /><select name="target" defaultValue={filters.target ?? ''} className="h-9 rounded-md border bg-white px-3 text-sm"><option value="">{t('allTargets')}</option><option value="user">user</option><option value="team">team</option><option value="team_member">team_member</option><option value="trial_application">trial_application</option></select><input name="from" type="date" defaultValue={filters.from} aria-label={t('from')} className="h-9 rounded-md border bg-white px-3 text-sm" /><input name="to" type="date" defaultValue={filters.to} aria-label={t('to')} className="h-9 rounded-md border bg-white px-3 text-sm" /><button className="h-9 rounded-md bg-[var(--color-primary)] px-4 text-sm font-semibold text-white">{common('filter')}</button></form>
    {result.rows.length ? <DataTableShell><table className="w-full"><THead><tr><Th>{t('time')}</Th><Th>{t('actor')}</Th><Th>{t('action')}</Th><Th>{t('target')}</Th><Th>{t('change')}</Th><Th>{t('reason')}</Th></tr></THead><TBody>{result.rows.map((row) => <Tr key={row.id}><Td className="whitespace-nowrap">{dateTime.format(row.createdAt)}</Td><Td>{row.actorId ? <Link href={`/${locale}/admin/users/${row.actorId}` as Route} className="font-medium">{row.actorName || row.actorEmail}</Link> : '—'}<div className="text-xs text-[var(--color-muted-foreground)]">{row.actorIp || '—'}</div></Td><Td><code className="text-xs">{row.action}</code></Td><Td>{row.targetType} #{row.targetId}</Td><Td className="max-w-md text-xs"><div className="text-red-700">− {summary(row.before)}</div><div className="mt-1 text-emerald-700">+ {summary(row.after)}</div></Td><Td className="max-w-xs text-xs">{row.reason || '—'}</Td></Tr>)}</TBody></table></DataTableShell> : <EmptyAdminState>{common('empty')}</EmptyAdminState>}
    <AdminPagination basePath={`/${locale}/admin/audit`} page={result.page} pages={result.pages} search={filters} previousLabel={common('previous')} nextLabel={common('next')} />
  </>;
}
