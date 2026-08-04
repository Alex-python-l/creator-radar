'use server';

import { and, eq, sql } from 'drizzle-orm';
import { revalidatePath } from 'next/cache';
import { headers } from 'next/headers';
import { z } from 'zod';

import { auth } from '@/lib/auth';
import { requireViewer } from '@/lib/auth/viewer';
import { db } from '@/lib/db';
import { members, users } from '@/lib/db/schema';

const teamNameSchema = z.string().trim().min(1).max(60);
const emailSchema = z.string().trim().email();
const teamRoleSchema = z.enum(['admin', 'member']);

function actionError(error: unknown): string {
  if (error instanceof Error) return error.message;
  return 'operation failed';
}

export async function createTeam(formData: FormData) {
  const viewer = await requireViewer();
  const parsed = teamNameSchema.safeParse(formData.get('name'));
  if (!parsed.success) return { error: '团队名称无效' };
  const slug = `team-${viewer.id}-${Date.now().toString(36)}`;
  try {
    const team = await auth.api.createOrganization({
      headers: await headers(),
      body: { name: parsed.data, slug },
    });
    await auth.api.setActiveOrganization({
      headers: await headers(),
      body: { organizationId: String(team.id) },
    });
    revalidatePath('/', 'layout');
    return { ok: true, organizationId: Number(team.id) };
  } catch (error) {
    return { error: actionError(error) };
  }
}

export async function renameTeam(formData: FormData) {
  const viewer = await requireViewer();
  if (viewer.teamRole !== 'owner' || !viewer.organizationId) return { error: 'forbidden' };
  const parsed = teamNameSchema.safeParse(formData.get('name'));
  if (!parsed.success) return { error: '团队名称无效' };
  try {
    await auth.api.updateOrganization({
      headers: await headers(),
      body: { organizationId: String(viewer.organizationId), data: { name: parsed.data } },
    });
    revalidatePath('/', 'layout');
    return { ok: true };
  } catch (error) {
    return { error: actionError(error) };
  }
}

export async function inviteTeamMember(formData: FormData) {
  const viewer = await requireViewer();
  if (!viewer.canManageMembers || !viewer.organizationId) return { error: 'forbidden' };
  const email = emailSchema.safeParse(formData.get('email'));
  const role = teamRoleSchema.safeParse(formData.get('role'));
  if (!email.success || !role.success) return { error: '邀请信息无效' };
  try {
    await auth.api.createInvitation({
      headers: await headers(),
      body: {
        email: email.data,
        role: role.data,
        organizationId: String(viewer.organizationId),
        resend: true,
      },
    });
    revalidatePath('/team');
    return { ok: true };
  } catch (error) {
    return { error: actionError(error) };
  }
}

export async function updateTeamMemberRole(formData: FormData) {
  const viewer = await requireViewer();
  if (!viewer.canManageMembers || !viewer.organizationId) return { error: 'forbidden' };
  const memberId = z.coerce.number().int().positive().safeParse(formData.get('memberId'));
  const role = teamRoleSchema.safeParse(formData.get('role'));
  if (!memberId.success || !role.success) return { error: 'invalid member' };
  try {
    const [target] = await db
      .select({ role: members.role })
      .from(members)
      .where(
        and(
          eq(members.id, memberId.data),
          eq(members.organizationId, viewer.organizationId),
        ),
      )
      .limit(1);
    if (!target || target.role === 'owner') return { error: 'cannot modify leader' };
    await auth.api.updateMemberRole({
      headers: await headers(),
      body: {
        memberId: String(memberId.data),
        role: role.data,
        organizationId: String(viewer.organizationId),
      },
    });
    revalidatePath('/team');
    return { ok: true };
  } catch (error) {
    return { error: actionError(error) };
  }
}

export async function removeTeamMember(formData: FormData) {
  const viewer = await requireViewer();
  if (!viewer.canManageMembers || !viewer.organizationId) return { error: 'forbidden' };
  const email = emailSchema.safeParse(formData.get('email'));
  if (!email.success) return { error: 'invalid member' };
  try {
    const [target] = await db
      .select({ role: members.role })
      .from(members)
      .innerJoin(users, eq(users.id, members.userId))
      .where(
        and(
          eq(members.organizationId, viewer.organizationId),
          eq(users.email, email.data),
        ),
      )
      .limit(1);
    if (!target || target.role === 'owner') return { error: 'cannot remove leader' };
    await auth.api.removeMember({
      headers: await headers(),
      body: {
        memberIdOrEmail: email.data,
        organizationId: String(viewer.organizationId),
      },
    });
    revalidatePath('/', 'layout');
    return { ok: true };
  } catch (error) {
    return { error: actionError(error) };
  }
}

export async function transferTeamLeadership(formData: FormData) {
  const viewer = await requireViewer();
  if (viewer.teamRole !== 'owner' || !viewer.organizationId) return { error: 'forbidden' };
  const targetId = z.coerce.number().int().positive().safeParse(formData.get('memberId'));
  if (!targetId.success) return { error: 'invalid member' };
  try {
    await db.transaction(async (tx) => {
      await tx.execute(sql`SELECT pg_advisory_xact_lock(20260718, ${viewer.organizationId})`);
      const [target] = await tx
        .select({ id: members.id })
        .from(members)
        .where(
          and(
            eq(members.id, targetId.data),
            eq(members.organizationId, viewer.organizationId!),
          ),
        )
        .limit(1);
      if (!target) throw new Error('member not found');
      await tx
        .update(members)
        .set({ role: 'admin' })
        .where(
          and(eq(members.organizationId, viewer.organizationId!), eq(members.userId, viewer.id)),
        );
      await tx.update(members).set({ role: 'owner' }).where(eq(members.id, target.id));
    });
    revalidatePath('/', 'layout');
    return { ok: true };
  } catch (error) {
    return { error: actionError(error) };
  }
}

export async function leaveTeam() {
  const viewer = await requireViewer();
  if (!viewer.organizationId) return { error: 'not in a team' };
  if (viewer.teamRole === 'owner') return { error: 'transfer leadership before leaving' };
  try {
    await auth.api.leaveOrganization({
      headers: await headers(),
      body: { organizationId: String(viewer.organizationId) },
    });
    await auth.api.setActiveOrganization({
      headers: await headers(),
      body: { organizationId: null },
    });
    revalidatePath('/', 'layout');
    return { ok: true };
  } catch (error) {
    return { error: actionError(error) };
  }
}

// Native form action adapters intentionally discard structured errors. Client
// callers may use the functions above when they need inline error rendering.
export async function createTeamForm(formData: FormData): Promise<void> {
  await createTeam(formData);
}
export async function renameTeamForm(formData: FormData): Promise<void> {
  await renameTeam(formData);
}
export async function inviteTeamMemberForm(formData: FormData): Promise<void> {
  await inviteTeamMember(formData);
}
export async function updateTeamMemberRoleForm(formData: FormData): Promise<void> {
  await updateTeamMemberRole(formData);
}
export async function removeTeamMemberForm(formData: FormData): Promise<void> {
  await removeTeamMember(formData);
}
export async function transferTeamLeadershipForm(formData: FormData): Promise<void> {
  await transferTeamLeadership(formData);
}
export async function leaveTeamForm(): Promise<void> {
  await leaveTeam();
}
