import { and, desc, eq, ilike, or, sql, type SQL } from 'drizzle-orm';
import { getTranslations } from 'next-intl/server';

import { AdminPageHeader, AdminPagination, EmptyAdminState } from '@/components/admin/admin-ui';
import { Button, Card, Input, Select } from '@/components/ui';
import { grantTrialApplicationQuota, updateTrialApplicationStatus } from '@/lib/actions/trial-applications';
import { ADMIN_PAGE_SIZE, adminPage, pageCount } from '@/lib/admin/queries';
import { db } from '@/lib/db';
import { members, organizations, trialApplications, users } from '@/lib/db/schema';
import { APPLICATION_STATUSES, type ApplicationStatus } from '@/lib/trial-applications';

export const dynamic = 'force-dynamic';

type ApplicationFilters = { status?: string; q?: string; page?: string };
type Props = { params: Promise<{ locale: string }>; searchParams: Promise<ApplicationFilters> };

export default async function AdminApplicationsPage({ params, searchParams }: Props) {
  const [{ locale }, search] = await Promise.all([params, searchParams]);
  const t = await getTranslations({ locale, namespace: 'applicationsAdmin' });
  const status = APPLICATION_STATUSES.includes(search.status as ApplicationStatus) ? search.status as ApplicationStatus : null;
  const query = search.q?.trim() ?? '';
  const page = adminPage(search.page);
  const conditions: SQL[] = [];
  if (status) conditions.push(eq(trialApplications.status, status));
  if (query) {
    const pattern = `%${query}%`;
    conditions.push(or(ilike(trialApplications.companyName, pattern), ilike(trialApplications.contactName, pattern), ilike(trialApplications.workEmail, pattern), ilike(trialApplications.wechat, pattern))!);
  }
  const applicationQuery = db
    .select({
      id: trialApplications.id, companyName: trialApplications.companyName, contactName: trialApplications.contactName,
      workEmail: trialApplications.workEmail, wechat: trialApplications.wechat, expectedAccounts: trialApplications.expectedAccounts,
      plan: trialApplications.plan, notes: trialApplications.notes, status: trialApplications.status,
      grantedQuota: trialApplications.grantedQuota, createdAt: trialApplications.createdAt,
      organizationId: organizations.id, organizationName: organizations.name, organizationQuota: organizations.monitorQuota,
    })
    .from(trialApplications)
    .leftJoin(users, sql`lower(${users.email}) = lower(${trialApplications.workEmail})`)
    .leftJoin(members, and(eq(members.userId, users.id), eq(members.role, 'owner')))
    .leftJoin(organizations, eq(organizations.id, members.organizationId))
    .where(conditions.length ? and(...conditions) : undefined);
  const [applications, countRows] = await Promise.all([
    applicationQuery.orderBy(desc(trialApplications.createdAt)).limit(ADMIN_PAGE_SIZE).offset((page - 1) * ADMIN_PAGE_SIZE),
    db.select({ total: sql<number>`COUNT(*)` }).from(trialApplications).where(conditions.length ? and(...conditions) : undefined),
  ]);
  const pages = pageCount(Number(countRows[0]?.total ?? 0));
  const dateTime = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', { dateStyle: 'medium', timeStyle: 'short' });

  return <>
    <AdminPageHeader title={t('title')} description={t('description')} actions={<form className="flex flex-wrap gap-2"><Input name="q" defaultValue={query} placeholder={t('searchPlaceholder')} className="min-w-56" /><Select name="status" defaultValue={status ?? ''}><option value="">{t('allStatuses')}</option>{APPLICATION_STATUSES.map((item) => <option key={item} value={item}>{t(`status.${item}`)}</option>)}</Select><Button type="submit">{t('filter')}</Button></form>} />
    {applications.length ? <div className="space-y-4">{applications.map((application) => <Card key={application.id} className="p-5"><div className="grid gap-5 xl:grid-cols-[1.2fr_1fr_1.2fr]">
      <div><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold">{application.companyName}</h2><span className="rounded-full bg-[var(--color-muted)] px-2.5 py-1 text-xs font-semibold text-[var(--color-primary)]">{application.plan.toUpperCase()}</span><span className="rounded-full border px-2.5 py-1 text-xs">{t(`status.${application.status}`)}</span></div><dl className="mt-4 grid grid-cols-[88px_1fr] gap-x-3 gap-y-2 text-sm"><dt className="text-[var(--color-muted-foreground)]">{t('contact')}</dt><dd>{application.contactName}</dd><dt className="text-[var(--color-muted-foreground)]">{t('email')}</dt><dd>{application.workEmail || '—'}</dd><dt className="text-[var(--color-muted-foreground)]">{t('wechat')}</dt><dd>{application.wechat || '—'}</dd><dt className="text-[var(--color-muted-foreground)]">{t('expected')}</dt><dd>{application.expectedAccounts}</dd><dt className="text-[var(--color-muted-foreground)]">{t('submitted')}</dt><dd>{dateTime.format(application.createdAt)}</dd></dl>{application.notes ? <p className="mt-4 rounded-lg bg-[var(--color-muted)]/60 p-3 text-sm">{application.notes}</p> : null}</div>
      <div className="rounded-lg border border-dashed p-4"><h3 className="text-sm font-semibold">{t('matchedTeam')}</h3>{application.organizationId ? <div className="mt-3 text-sm"><p>{application.organizationName}</p><p className="mt-1 text-[var(--color-muted-foreground)]">{t('currentQuota', { quota: application.organizationQuota ?? 0 })}</p></div> : <p className="mt-3 text-sm leading-6 text-amber-700">{t('teamNotFound')}</p>}<form action={updateTrialApplicationStatus} className="mt-5 flex gap-2"><input type="hidden" name="applicationId" value={application.id} /><Select name="status" defaultValue={application.status} className="min-w-0 flex-1">{APPLICATION_STATUSES.map((item) => <option key={item} value={item}>{t(`status.${item}`)}</option>)}</Select><Button type="submit" variant="secondary">{t('save')}</Button></form></div>
      <div><h3 className="text-sm font-semibold">{t('grantQuota')}</h3><p className="mt-2 text-xs leading-5 text-[var(--color-muted-foreground)]">{t('grantHint')}</p><div className="mt-4 grid grid-cols-4 gap-2 xl:grid-cols-2">{[0, 20, 50, 200].map((quota) => <form key={quota} action={grantTrialApplicationQuota}><input type="hidden" name="applicationId" value={application.id} /><input type="hidden" name="quota" value={quota} /><Button type="submit" variant="secondary" size="sm" disabled={!application.organizationId} className="w-full">{quota === 0 ? t('revoke') : quota}</Button></form>)}</div><form action={grantTrialApplicationQuota} className="mt-2 flex gap-2"><input type="hidden" name="applicationId" value={application.id} /><Input name="quota" type="number" min="0" max="100000" placeholder={t('customQuota')} disabled={!application.organizationId} className="min-w-0 flex-1" /><Button type="submit" disabled={!application.organizationId}>{t('grant')}</Button></form>{application.grantedQuota !== null ? <p className="mt-3 text-xs text-[var(--color-muted-foreground)]">{t('lastGranted', { quota: application.grantedQuota })}</p> : null}</div>
    </div></Card>)}</div> : <EmptyAdminState>{t('empty')}</EmptyAdminState>}
    <AdminPagination basePath={`/${locale}/admin/applications`} page={page} pages={pages} search={search} previousLabel={locale === 'zh' ? '上一页' : 'Previous'} nextLabel={locale === 'zh' ? '下一页' : 'Next'} />
  </>;
}
