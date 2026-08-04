import { and, asc, eq } from 'drizzle-orm';

import {
  createTeamForm,
  inviteTeamMemberForm,
  leaveTeamForm,
  removeTeamMemberForm,
  renameTeamForm,
  transferTeamLeadershipForm,
  updateTeamMemberRoleForm,
} from '@/lib/actions/team';
import { getUserTeam, requireViewer } from '@/lib/auth/viewer';
import { db } from '@/lib/db';
import { invitations, members, users } from '@/lib/db/schema';

export default async function TeamPage({ params }: { params: Promise<{ locale: string }> }) {
  const viewer = await requireViewer();
  const { locale } = await params;
  const zh = locale === 'zh';
  const joinedTeam = await getUserTeam(viewer.id);

  if (!joinedTeam) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <h1 className="text-3xl font-semibold [font-family:var(--font-serif)]">{zh ? '创建团队' : 'Create a team'}</h1>
        <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
          {zh ? '创建者将成为团队 leader；你的个人空间会一直保留。' : 'The creator becomes the team leader; your personal workspace remains private.'}
        </p>
        <form action={createTeamForm} className="mt-6 flex max-w-md gap-2">
          <input name="name" required maxLength={60} placeholder={zh ? '团队名称' : 'Team name'} className="h-10 flex-1 rounded-md border bg-white px-3 text-sm" />
          <button className="rounded-md bg-[var(--color-primary)] px-4 text-sm font-semibold text-white">{zh ? '创建' : 'Create'}</button>
        </form>
      </div>
    );
  }

  if (viewer.workspaceKind !== 'team' || !viewer.organizationId) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <h1 className="text-3xl font-semibold [font-family:var(--font-serif)]">{joinedTeam.name}</h1>
        <p className="mt-3 text-sm text-[var(--color-muted-foreground)]">
          {zh ? '请先从左侧工作空间切换器进入团队，再管理成员。' : 'Switch to the team workspace in the sidebar to manage members.'}
        </p>
      </div>
    );
  }

  const [teamMembers, pendingInvites] = await Promise.all([
    db
      .select({ id: members.id, userId: users.id, name: users.name, email: users.email, role: members.role })
      .from(members)
      .innerJoin(users, eq(users.id, members.userId))
      .where(eq(members.organizationId, viewer.organizationId))
      .orderBy(asc(members.createdAt)),
    db
      .select({ id: invitations.id, email: invitations.email, role: invitations.role })
      .from(invitations)
      .where(and(eq(invitations.organizationId, viewer.organizationId), eq(invitations.status, 'pending'))),
  ]);

  return (
    <div className="mx-auto max-w-4xl space-y-8 p-8">
      <div>
        <h1 className="text-3xl font-semibold [font-family:var(--font-serif)]">{viewer.workspaceName}</h1>
        <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">{zh ? '团队成员共享监控与运营数据。' : 'Members share monitoring and operations data.'}</p>
      </div>

      {viewer.canManageMembers && (
        <div className="grid gap-4 md:grid-cols-2">
          {viewer.teamRole === 'owner' && (
            <form action={renameTeamForm} className="rounded-xl border bg-white/70 p-4">
              <div className="text-sm font-semibold">{zh ? '团队名称' : 'Team name'}</div>
              <div className="mt-3 flex gap-2"><input name="name" defaultValue={viewer.workspaceName} required className="h-9 flex-1 rounded-md border px-3 text-sm" /><button className="rounded-md border px-3 text-sm">{zh ? '保存' : 'Save'}</button></div>
            </form>
          )}
          <form action={inviteTeamMemberForm} className="rounded-xl border bg-white/70 p-4">
            <div className="text-sm font-semibold">{zh ? '邮件邀请成员' : 'Invite by email'}</div>
            <div className="mt-3 flex gap-2"><input name="email" type="email" required placeholder="name@example.com" className="h-9 min-w-0 flex-1 rounded-md border px-3 text-sm" /><select name="role" className="rounded-md border px-2 text-sm"><option value="member">member</option><option value="admin">admin</option></select><button className="rounded-md border px-3 text-sm">{zh ? '邀请' : 'Invite'}</button></div>
          </form>
        </div>
      )}

      <section className="rounded-xl border bg-white/70 p-4">
        <h2 className="text-sm font-semibold">{zh ? '成员' : 'Members'}</h2>
        <div className="mt-3 divide-y">
          {teamMembers.map((member) => (
            <div key={member.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
              <div className="min-w-0 flex-1"><div className="font-medium">{member.name}</div><div className="truncate text-xs text-[var(--color-muted-foreground)]">{member.email}</div></div>
              <span className="rounded-full bg-[var(--color-muted)] px-2 py-1 text-xs">{member.role === 'owner' ? 'leader' : member.role}</span>
              {viewer.canManageMembers && member.role !== 'owner' && member.userId !== viewer.id && (
                <>
                  <form action={updateTeamMemberRoleForm}><input type="hidden" name="memberId" value={member.id} /><input type="hidden" name="role" value={member.role === 'admin' ? 'member' : 'admin'} /><button className="text-xs text-[var(--color-primary)]">{member.role === 'admin' ? '设为 member' : '设为 admin'}</button></form>
                  {viewer.teamRole === 'owner' && <form action={transferTeamLeadershipForm}><input type="hidden" name="memberId" value={member.id} /><button className="text-xs text-[var(--color-primary)]">{zh ? '转让 leader' : 'Transfer leader'}</button></form>}
                  <form action={removeTeamMemberForm}><input type="hidden" name="email" value={member.email} /><button className="text-xs text-red-600">{zh ? '移除' : 'Remove'}</button></form>
                </>
              )}
            </div>
          ))}
        </div>
      </section>

      {pendingInvites.length > 0 && <section className="rounded-xl border bg-white/70 p-4"><h2 className="text-sm font-semibold">{zh ? '待接受邀请' : 'Pending invitations'}</h2><div className="mt-2 space-y-2 text-sm">{pendingInvites.map((invite) => <div key={invite.id}>{invite.email} · {invite.role}</div>)}</div></section>}

      {viewer.teamRole !== 'owner' && <form action={leaveTeamForm}><button className="text-sm text-red-600">{zh ? '退出团队' : 'Leave team'}</button></form>}
    </div>
  );
}
