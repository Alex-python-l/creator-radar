import type { Route } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';

import { AdminActionCard, AdminFeedback, AdminPageHeader, Definition, DefinitionList } from '@/components/admin/admin-ui';
import { ConfirmSubmitButton } from '@/components/confirm-submit-button';
import { Badge, Button, Card, Input, Select } from '@/components/ui';
import { removeAdminTeamMember, renameAdminTeam, setAdminTeamMemberRole, setTeamQuota, transferAdminTeamOwner } from '@/lib/admin/actions';
import { getAdminTeam } from '@/lib/admin/queries';

type Props = { params: Promise<{ locale: string; id: string }>; searchParams: Promise<{ notice?: string; error?: string }> };

export default async function AdminTeamDetailPage({ params, searchParams }: Props) {
  const [{ locale, id }, feedback] = await Promise.all([params, searchParams]);
  const teamId = Number(id);
  if (!Number.isInteger(teamId) || teamId <= 0) notFound();
  const [team, t, common] = await Promise.all([getAdminTeam(teamId), getTranslations({ locale, namespace: 'admin.teamDetail' }), getTranslations({ locale, namespace: 'admin.common' })]);
  if (!team) notFound();
  const dateTime = new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en', { dateStyle: 'medium', timeStyle: 'short' });
  const baseHidden = <><input type="hidden" name="locale" value={locale} /><input type="hidden" name="teamId" value={team.id} /></>;
  return <>
    <AdminPageHeader title={team.name} description={`${team.slug} · #${team.id}`} actions={<Link href={`/${locale}/admin/teams` as Route} className="rounded-md border bg-white px-3 py-2 text-sm font-semibold">{common('back')}</Link>} />
    <AdminFeedback locale={locale} {...feedback} />
    <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]">
      <Card className="p-5"><DefinitionList><Definition term={t('workspace')}>#{team.workspaceId}</Definition><Definition term={t('usage')}>{Number(team.usage)}/{team.quota}{Number(team.usage) > team.quota ? <Badge tone="red" className="ml-2">{t('exceeded')}</Badge> : null}</Definition><Definition term={t('created')}>{dateTime.format(team.createdAt)}</Definition><Definition term={t('updated')}>{team.updatedAt ? dateTime.format(team.updatedAt) : '—'}</Definition><Definition term={t('pendingInvites')}>{team.invitations.length}</Definition></DefinitionList></Card>
      <div className="grid gap-4 sm:grid-cols-2">
        <AdminActionCard title={t('renameTitle')}><form action={renameAdminTeam} className="space-y-2">{baseHidden}<Input name="name" defaultValue={team.name} minLength={1} maxLength={60} required /><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required /><Button type="submit" size="sm">{common('save')}</Button></form></AdminActionCard>
        <AdminActionCard title={t('quotaTitle')} description={t('quotaHint')}><form action={setTeamQuota} className="space-y-2">{baseHidden}<Input name="quota" type="number" min="0" max="100000" defaultValue={team.quota} required /><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required /><Button type="submit" size="sm">{common('save')}</Button></form></AdminActionCard>
      </div>
    </div>
    <section className="mt-8"><h2 className="mb-3 text-base font-semibold">{t('members')}</h2><div className="space-y-3">{team.members.map((member) => <Card key={member.memberId} className="p-4"><div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-center"><div><Link href={`/${locale}/admin/users/${member.userId}` as Route} className="font-semibold text-[var(--color-primary)]">{member.name}</Link><div className="text-xs text-[var(--color-muted-foreground)]">{member.email} · {dateTime.format(member.joinedAt)}</div></div><div className="flex flex-wrap items-center gap-2"><Badge tone={member.role === 'owner' ? 'blue' : 'gray'}>{member.role}</Badge>{member.banned ? <Badge tone="red">{t('banned')}</Badge> : null}{member.role !== 'owner' ? <><form action={setAdminTeamMemberRole} className="flex gap-2">{baseHidden}<input type="hidden" name="memberId" value={member.memberId} /><Select name="role" defaultValue={member.role === 'admin' ? 'admin' : 'member'} className="w-28"><option value="member">member</option><option value="admin">admin</option></Select><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required className="w-44" /><ConfirmSubmitButton size="sm" confirmMessage={t('roleConfirm')}>{common('save')}</ConfirmSubmitButton></form><form action={transferAdminTeamOwner} className="flex gap-2">{baseHidden}<input type="hidden" name="memberId" value={member.memberId} /><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required className="w-44" /><ConfirmSubmitButton size="sm" confirmMessage={t('ownerConfirm')}>{t('makeOwner')}</ConfirmSubmitButton></form><form action={removeAdminTeamMember} className="flex gap-2">{baseHidden}<input type="hidden" name="memberId" value={member.memberId} /><Input name="reason" minLength={3} maxLength={500} placeholder={common('reason')} required className="w-44" /><ConfirmSubmitButton destructive size="sm" confirmMessage={t('removeConfirm')}>{t('remove')}</ConfirmSubmitButton></form></> : null}</div></div></Card>)}</div></section>
    {team.invitations.length ? <section className="mt-8"><h2 className="mb-3 text-base font-semibold">{t('pendingInvites')}</h2><Card className="divide-y">{team.invitations.map((invite) => <div key={invite.id} className="flex justify-between px-4 py-3 text-sm"><span>{invite.email} · {invite.role}</span><span className="text-[var(--color-muted-foreground)]">{dateTime.format(invite.expiresAt)}</span></div>)}</Card></section> : null}
  </>;
}
