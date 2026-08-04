import type { Route } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';

import { AdminActionCard, AdminFeedback, AdminPageHeader, Definition, DefinitionList } from '@/components/admin/admin-ui';
import { ConfirmSubmitButton } from '@/components/confirm-submit-button';
import { Badge, Button, Card, Input, Select } from '@/components/ui';
import { deleteAdminUser, revokeUserSessions, setUserBan, setUserQuota, setUserRole } from '@/lib/admin/actions';
import { getAdminUser } from '@/lib/admin/queries';

type Props = { params: Promise<{ locale: string; id: string }>; searchParams: Promise<{ notice?: string; error?: string }> };

export default async function AdminUserDetailPage({ params, searchParams }: Props) {
  const [{ locale, id }, feedback] = await Promise.all([params, searchParams]);
  const userId = Number(id);
  if (!Number.isInteger(userId) || userId <= 0) notFound();
  const [user, t, common] = await Promise.all([
    getAdminUser(userId),
    getTranslations({ locale, namespace: 'admin.userDetail' }),
    getTranslations({ locale, namespace: 'admin.common' }),
  ]);
  if (!user) notFound();
  const dateTime = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', { dateStyle: 'medium', timeStyle: 'short' });
  const hidden = <><input type="hidden" name="locale" value={locale} /><input type="hidden" name="userId" value={user.id} /></>;

  return (
    <>
      <AdminPageHeader title={user.name} description={`${user.email} · #${user.id}`} actions={<Link href={`/${locale}/admin/users` as Route} className="rounded-md border bg-white px-3 py-2 text-sm font-semibold">{common('back')}</Link>} />
      <AdminFeedback locale={locale} {...feedback} />
      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <Card className="p-5">
          <div className="mb-4 flex flex-wrap gap-2"><Badge tone={user.role === 'admin' ? 'blue' : 'gray'}>{user.role || 'user'}</Badge>{user.banned ? <Badge tone="red">{t('banned')}</Badge> : <Badge tone="green">{t('active')}</Badge>}{user.emailVerified ? <Badge tone="green">{t('verified')}</Badge> : <Badge tone="orange">{t('unverified')}</Badge>}</div>
          <DefinitionList>
            <Definition term={t('username')}>{user.username || '—'}</Definition><Definition term={t('created')}>{dateTime.format(user.createdAt)}</Definition><Definition term={t('updated')}>{dateTime.format(user.updatedAt)}</Definition><Definition term={t('workspace')}>#{user.workspaceId ?? '—'}</Definition><Definition term={t('personalUsage')}>{Number(user.usage)}/{user.monitorQuota ?? 0}</Definition><Definition term={t('team')}>{user.teamId ? <Link href={`/${locale}/admin/teams/${user.teamId}` as Route} className="text-[var(--color-primary)]">{user.teamName} · {user.teamRole}</Link> : '—'}</Definition>{user.banned ? <><Definition term={t('banReason')}>{user.banReason || '—'}</Definition><Definition term={t('banExpires')}>{user.banExpires ? dateTime.format(user.banExpires) : t('permanent')}</Definition></> : null}
          </DefinitionList>
        </Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <AdminActionCard title={t('quotaTitle')} description={t('quotaHint')}>
            <form action={setUserQuota} className="space-y-2">{hidden}<Input name="quota" type="number" min="0" max="100000" defaultValue={user.monitorQuota ?? 0} required /><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required /><Button type="submit" size="sm">{common('save')}</Button></form>
          </AdminActionCard>
          <AdminActionCard title={t('roleTitle')} description={t('roleHint')}>
            <form action={setUserRole} className="space-y-2">{hidden}<Select name="role" defaultValue={user.role === 'admin' ? 'admin' : 'user'}><option value="user">user</option><option value="admin">admin</option></Select><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required /><ConfirmSubmitButton size="sm" confirmMessage={t('roleConfirm')}>{common('save')}</ConfirmSubmitButton></form>
          </AdminActionCard>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <AdminActionCard title={user.banned ? t('unbanTitle') : t('banTitle')} description={user.banned ? t('unbanHint') : t('banHint')}>
          <form action={setUserBan} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">{hidden}<input type="hidden" name="banned" value={user.banned ? 'false' : 'true'} /><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required /><Input name="expiresAt" type="datetime-local" disabled={Boolean(user.banned)} aria-label={t('banExpires')} /><ConfirmSubmitButton destructive={!user.banned} size="sm" confirmMessage={user.banned ? t('unbanConfirm') : t('banConfirm')}>{user.banned ? t('unban') : t('ban')}</ConfirmSubmitButton></form>
        </AdminActionCard>
        <AdminActionCard title={t('sessionsTitle')} description={t('sessionsHint', { count: user.sessions.length })}>
          <form action={revokeUserSessions} className="flex gap-2">{hidden}<Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required className="min-w-0 flex-1" /><ConfirmSubmitButton destructive size="sm" confirmMessage={t('sessionsConfirm')}>{t('revokeSessions')}</ConfirmSubmitButton></form>
        </AdminActionCard>
      </div>

      <AdminActionCard className="mt-4 border-red-200" title={t('deleteTitle')} description={t('deleteHint')}>
        <form action={deleteAdminUser} className="grid gap-2 lg:grid-cols-[1fr_1fr_auto]">
          {hidden}
          <Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required />
          <Input name="confirmation" type="email" placeholder={t('deleteEmailPlaceholder', {email: user.email})} required />
          <ConfirmSubmitButton destructive size="sm" confirmMessage={t('deleteConfirm', {email: user.email})}>{t('delete')}</ConfirmSubmitButton>
        </form>
      </AdminActionCard>

      <section className="mt-8"><h2 className="mb-3 text-base font-semibold">{t('sessions')}</h2><div className="space-y-2">{user.sessions.length ? user.sessions.map((session) => <Card key={session.id} className="grid gap-2 p-4 text-sm sm:grid-cols-4"><div><span className="text-xs text-[var(--color-muted-foreground)]">{t('lastSeen')}</span><div>{dateTime.format(session.updatedAt)}</div></div><div><span className="text-xs text-[var(--color-muted-foreground)]">IP</span><div>{session.ipAddress || '—'}</div></div><div className="sm:col-span-2"><span className="text-xs text-[var(--color-muted-foreground)]">User-Agent</span><div className="truncate" title={session.userAgent || ''}>{session.userAgent || '—'}</div></div></Card>) : <Card className="p-6 text-center text-sm text-[var(--color-muted-foreground)]">{t('noSessions')}</Card>}</div></section>
    </>
  );
}
