'use server';

import { and, eq, ne, sql } from 'drizzle-orm';
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import type { Route } from 'next';
import { z } from 'zod';

import { adminAuditEntry, getAdminMutationContext } from '@/lib/admin/audit';
import { db } from '@/lib/db';
import {
  adminAuditLogs,
  members,
  organizations,
  sessions,
  users,
} from '@/lib/db/schema';

const idSchema = z.coerce.number().int().positive();
const localeSchema = z.enum(['zh', 'en']).catch('zh');
const reasonSchema = z.string().trim().min(3).max(500);
const quotaSchema = z.coerce.number().int().min(0).max(100_000);
const teamNameSchema = z.string().trim().min(1).max(60);
const teamRoleSchema = z.enum(['admin', 'member']);
const platformRoleSchema = z.enum(['admin', 'user']);
const deletionConfirmationSchema = z.string().trim().min(3).max(320);

function field(formData: FormData, name: string) {
  return String(formData.get(name) ?? '');
}

const KNOWN_ACTION_ERRORS = new Set([
  'user_not_found',
  'team_not_found',
  'member_not_found',
  'last_active_admin',
  'cannot_modify_owner',
  'cannot_remove_owner',
  'already_owner',
  'cannot_delete_self',
  'cannot_delete_owner',
  'confirmation_mismatch',
]);

function actionError(error: unknown) {
  const message = error instanceof Error ? error.message : '';
  return KNOWN_ACTION_ERRORS.has(message) ? message : 'operation_failed';
}

function adminResultPath(locale: string, section: string, id: number, kind: 'notice' | 'error', message: string): Route {
  const params = new URLSearchParams({ [kind]: message });
  return `/${locale}/admin/${section}/${id}?${params}` as Route;
}

function adminListResultPath(locale: string, section: string, kind: 'notice' | 'error', message: string): Route {
  const params = new URLSearchParams({ [kind]: message });
  return `/${locale}/admin/${section}?${params}` as Route;
}

function revalidateAdmin() {
  revalidatePath('/zh/admin', 'layout');
  revalidatePath('/en/admin', 'layout');
}

export async function setUserBan(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const userId = idSchema.parse(formData.get('userId'));
  const banned = z.enum(['true', 'false']).parse(field(formData, 'banned')) === 'true';
  const reason = reasonSchema.parse(field(formData, 'reason'));
  const expiresRaw = field(formData, 'expiresAt').trim();
  const expiresAt = expiresRaw ? new Date(expiresRaw) : null;
  let error: string | null = null;

  if (expiresAt && Number.isNaN(expiresAt.getTime())) error = 'invalid_expiry';
  if (banned && userId === context.actor.id) error = 'cannot_ban_self';
  if (!error) {
    try {
      await db.transaction(async (tx) => {
        await tx.execute(sql`SELECT pg_advisory_xact_lock(20260719, ${userId})`);
        const [target] = await tx
          .select({ banned: users.banned, banReason: users.banReason, banExpires: users.banExpires })
          .from(users)
          .where(eq(users.id, userId))
          .limit(1);
        if (!target) throw new Error('user_not_found');
        const after = {
          banned,
          banReason: banned ? reason : null,
          banExpires: banned ? expiresAt : null,
        };
        await tx.update(users).set(after).where(eq(users.id, userId));
        if (banned) await tx.delete(sessions).where(eq(sessions.userId, userId));
        await tx.insert(adminAuditLogs).values(
          adminAuditEntry(context, {
            action: banned ? 'user.ban' : 'user.unban',
            targetType: 'user',
            targetId: String(userId),
            before: target,
            after,
            reason,
          }),
        );
      });
    } catch (caught) {
      error = actionError(caught);
    }
  }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'users', userId, error ? 'error' : 'notice', error ?? (banned ? 'user_banned' : 'user_unbanned')));
}

export async function setUserRole(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const userId = idSchema.parse(formData.get('userId'));
  const role = platformRoleSchema.parse(field(formData, 'role'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  if (userId === context.actor.id && role !== 'admin') error = 'cannot_demote_self';

  if (!error) {
    try {
      await db.transaction(async (tx) => {
        await tx.execute(sql`SELECT pg_advisory_xact_lock(20260719, 0)`);
        const [target] = await tx.select({ role: users.role }).from(users).where(eq(users.id, userId)).limit(1);
        if (!target) throw new Error('user_not_found');
        if (target.role === 'admin' && role === 'user') {
          const [remaining] = await tx
            .select({ count: sql<number>`COUNT(*)` })
            .from(users)
            .where(and(eq(users.role, 'admin'), eq(users.banned, false), ne(users.id, userId)));
          if (Number(remaining?.count ?? 0) === 0) throw new Error('last_active_admin');
        }
        await tx.update(users).set({ role }).where(eq(users.id, userId));
        await tx.insert(adminAuditLogs).values(
          adminAuditEntry(context, {
            action: 'user.role.update',
            targetType: 'user',
            targetId: String(userId),
            before: { role: target.role },
            after: { role },
            reason,
          }),
        );
      });
    } catch (caught) {
      error = actionError(caught);
    }
  }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'users', userId, error ? 'error' : 'notice', error ?? 'user_role_updated'));
}

export async function setUserQuota(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const userId = idSchema.parse(formData.get('userId'));
  const quota = quotaSchema.parse(formData.get('quota'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  try {
    await db.transaction(async (tx) => {
      const [target] = await tx.select({ quota: users.monitorQuota }).from(users).where(eq(users.id, userId)).limit(1);
      if (!target) throw new Error('user_not_found');
      await tx.update(users).set({ monitorQuota: quota }).where(eq(users.id, userId));
      await tx.insert(adminAuditLogs).values(
        adminAuditEntry(context, {
          action: 'user.quota.update',
          targetType: 'user',
          targetId: String(userId),
          before: { quota: target.quota },
          after: { quota },
          reason,
        }),
      );
    });
  } catch (caught) {
    error = actionError(caught);
  }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'users', userId, error ? 'error' : 'notice', error ?? 'user_quota_updated'));
}

export async function revokeUserSessions(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const userId = idSchema.parse(formData.get('userId'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  try {
    await db.transaction(async (tx) => {
      const [target] = await tx.select({ id: users.id }).from(users).where(eq(users.id, userId)).limit(1);
      if (!target) throw new Error('user_not_found');
      const deleted = await tx.delete(sessions).where(eq(sessions.userId, userId)).returning({ id: sessions.id });
      await tx.insert(adminAuditLogs).values(
        adminAuditEntry(context, {
          action: 'user.sessions.revoke',
          targetType: 'user',
          targetId: String(userId),
          before: { activeSessionCount: deleted.length },
          after: { activeSessionCount: 0 },
          reason,
        }),
      );
    });
  } catch (caught) {
    error = actionError(caught);
  }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'users', userId, error ? 'error' : 'notice', error ?? 'sessions_revoked'));
}

export async function deleteAdminUser(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const userId = idSchema.parse(formData.get('userId'));
  const confirmation = deletionConfirmationSchema.parse(field(formData, 'confirmation'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = userId === context.actor.id ? 'cannot_delete_self' : null;

  if (!error) {
    try {
      await db.transaction(async (tx) => {
        await tx.execute(sql`SELECT pg_advisory_xact_lock(20260719, 0)`);
        const [target] = await tx
          .select({ id: users.id, email: users.email, role: users.role })
          .from(users)
          .where(eq(users.id, userId))
          .limit(1);
        if (!target) throw new Error('user_not_found');
        if (confirmation.toLowerCase() !== target.email.toLowerCase()) {
          throw new Error('confirmation_mismatch');
        }

        const [membership] = await tx
          .select({ organizationId: members.organizationId, role: members.role })
          .from(members)
          .where(eq(members.userId, userId))
          .limit(1);
        if (membership?.role === 'owner') throw new Error('cannot_delete_owner');

        if (target.role === 'admin') {
          const [remaining] = await tx
            .select({ count: sql<number>`COUNT(*)` })
            .from(users)
            .where(and(eq(users.role, 'admin'), eq(users.banned, false), ne(users.id, userId)));
          if (Number(remaining?.count ?? 0) === 0) throw new Error('last_active_admin');
        }

        await tx.insert(adminAuditLogs).values(
          adminAuditEntry(context, {
            action: 'user.delete',
            targetType: 'user',
            targetId: String(userId),
            before: {
              role: target.role ?? 'user',
              teamId: membership?.organizationId ?? null,
              teamRole: membership?.role ?? null,
            },
            after: { deleted: true },
            reason,
          }),
        );
        const deleted = await tx.delete(users).where(eq(users.id, userId)).returning({ id: users.id });
        if (!deleted.length) throw new Error('user_not_found');
      });
    } catch (caught) {
      error = actionError(caught);
    }
  }

  revalidateAdmin();
  if (!error) redirect(adminListResultPath(locale, 'users', 'notice', 'user_deleted'));
  if (error === 'user_not_found') redirect(adminListResultPath(locale, 'users', 'error', error));
  redirect(adminResultPath(locale, 'users', userId, 'error', error));
}

export async function renameAdminTeam(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const teamId = idSchema.parse(formData.get('teamId'));
  const name = teamNameSchema.parse(field(formData, 'name'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  try {
    await db.transaction(async (tx) => {
      const [target] = await tx.select({ name: organizations.name }).from(organizations).where(eq(organizations.id, teamId)).limit(1);
      if (!target) throw new Error('team_not_found');
      await tx.update(organizations).set({ name, updatedAt: new Date() }).where(eq(organizations.id, teamId));
      await tx.insert(adminAuditLogs).values(
        adminAuditEntry(context, {
          action: 'team.name.update', targetType: 'team', targetId: String(teamId),
          before: { name: target.name }, after: { name }, reason,
        }),
      );
    });
  } catch (caught) { error = actionError(caught); }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'teams', teamId, error ? 'error' : 'notice', error ?? 'team_name_updated'));
}

export async function setTeamQuota(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const teamId = idSchema.parse(formData.get('teamId'));
  const quota = quotaSchema.parse(formData.get('quota'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  try {
    await db.transaction(async (tx) => {
      const [target] = await tx.select({ quota: organizations.monitorQuota }).from(organizations).where(eq(organizations.id, teamId)).limit(1);
      if (!target) throw new Error('team_not_found');
      await tx.update(organizations).set({ monitorQuota: quota, updatedAt: new Date() }).where(eq(organizations.id, teamId));
      await tx.insert(adminAuditLogs).values(
        adminAuditEntry(context, {
          action: 'team.quota.update', targetType: 'team', targetId: String(teamId),
          before: { quota: target.quota }, after: { quota }, reason,
        }),
      );
    });
  } catch (caught) { error = actionError(caught); }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'teams', teamId, error ? 'error' : 'notice', error ?? 'team_quota_updated'));
}

export async function setAdminTeamMemberRole(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const teamId = idSchema.parse(formData.get('teamId'));
  const memberId = idSchema.parse(formData.get('memberId'));
  const role = teamRoleSchema.parse(field(formData, 'role'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  try {
    await db.transaction(async (tx) => {
      await tx.execute(sql`SELECT pg_advisory_xact_lock(20260720, ${teamId})`);
      const [target] = await tx
        .select({ userId: members.userId, role: members.role })
        .from(members)
        .where(and(eq(members.id, memberId), eq(members.organizationId, teamId)))
        .limit(1);
      if (!target) throw new Error('member_not_found');
      if (target.role === 'owner') throw new Error('cannot_modify_owner');
      await tx.update(members).set({ role }).where(eq(members.id, memberId));
      await tx.insert(adminAuditLogs).values(
        adminAuditEntry(context, {
          action: 'team.member.role.update', targetType: 'team_member', targetId: String(memberId),
          before: { teamId, userId: target.userId, role: target.role },
          after: { teamId, userId: target.userId, role }, reason,
        }),
      );
    });
  } catch (caught) { error = actionError(caught); }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'teams', teamId, error ? 'error' : 'notice', error ?? 'member_role_updated'));
}

export async function removeAdminTeamMember(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const teamId = idSchema.parse(formData.get('teamId'));
  const memberId = idSchema.parse(formData.get('memberId'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  try {
    await db.transaction(async (tx) => {
      await tx.execute(sql`SELECT pg_advisory_xact_lock(20260720, ${teamId})`);
      const [target] = await tx
        .select({ userId: members.userId, role: members.role })
        .from(members)
        .where(and(eq(members.id, memberId), eq(members.organizationId, teamId)))
        .limit(1);
      if (!target) throw new Error('member_not_found');
      if (target.role === 'owner') throw new Error('cannot_remove_owner');
      await tx.delete(members).where(eq(members.id, memberId));
      await tx
        .update(sessions)
        .set({ activeOrganizationId: null })
        .where(and(eq(sessions.userId, target.userId), eq(sessions.activeOrganizationId, teamId)));
      await tx.insert(adminAuditLogs).values(
        adminAuditEntry(context, {
          action: 'team.member.remove', targetType: 'team_member', targetId: String(memberId),
          before: { teamId, userId: target.userId, role: target.role }, after: null, reason,
        }),
      );
    });
  } catch (caught) { error = actionError(caught); }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'teams', teamId, error ? 'error' : 'notice', error ?? 'member_removed'));
}

export async function transferAdminTeamOwner(formData: FormData): Promise<void> {
  const context = await getAdminMutationContext();
  const locale = localeSchema.parse(field(formData, 'locale'));
  const teamId = idSchema.parse(formData.get('teamId'));
  const memberId = idSchema.parse(formData.get('memberId'));
  const reason = reasonSchema.parse(field(formData, 'reason'));
  let error: string | null = null;
  try {
    await db.transaction(async (tx) => {
      await tx.execute(sql`SELECT pg_advisory_xact_lock(20260720, ${teamId})`);
      const [currentOwner] = await tx
        .select({ id: members.id, userId: members.userId })
        .from(members)
        .where(and(eq(members.organizationId, teamId), eq(members.role, 'owner')))
        .limit(1);
      const [target] = await tx
        .select({ id: members.id, userId: members.userId, role: members.role })
        .from(members)
        .where(and(eq(members.id, memberId), eq(members.organizationId, teamId)))
        .limit(1);
      if (!currentOwner || !target) throw new Error('member_not_found');
      if (target.id === currentOwner.id) throw new Error('already_owner');
      await tx.update(members).set({ role: 'admin' }).where(eq(members.id, currentOwner.id));
      await tx.update(members).set({ role: 'owner' }).where(eq(members.id, target.id));
      await tx.insert(adminAuditLogs).values(
        adminAuditEntry(context, {
          action: 'team.owner.transfer', targetType: 'team', targetId: String(teamId),
          before: { ownerUserId: currentOwner.userId }, after: { ownerUserId: target.userId }, reason,
        }),
      );
    });
  } catch (caught) { error = actionError(caught); }
  revalidateAdmin();
  redirect(adminResultPath(locale, 'teams', teamId, error ? 'error' : 'notice', error ?? 'owner_transferred'));
}
