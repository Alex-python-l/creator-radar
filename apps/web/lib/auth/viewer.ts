import 'server-only';

import { and, eq } from 'drizzle-orm';
import type { Route } from 'next';
import { headers } from 'next/headers';
import { redirect } from 'next/navigation';
import { cache } from 'react';

import { auth } from '@/lib/auth';
import { db } from '@/lib/db';
import { members, organizations, workspaces } from '@/lib/db/schema';
import { env } from '@/lib/env';

export type WorkspaceKind = 'personal' | 'team';
export type TeamRole = 'owner' | 'admin' | 'member';

export type UserTeam = {
  id: number;
  name: string;
  role: TeamRole;
};

/** Authoritative user and active workspace resolved from the database. */
export type Viewer = {
  id: number;
  username: string;
  role: 'admin' | 'user';
  isAdmin: boolean;
  workspaceId: number;
  workspaceKind: WorkspaceKind;
  workspaceName: string;
  organizationId: number | null;
  teamRole: TeamRole | null;
  monitorQuota: number;
  canManageMembers: boolean;
  canManageSecrets: boolean;
};

async function personalWorkspace(userId: number, username: string, quota: number) {
  const [workspace] = await db
    .select({ id: workspaces.id })
    .from(workspaces)
    .where(and(eq(workspaces.kind, 'personal'), eq(workspaces.userId, userId)))
    .limit(1);
  if (!workspace) throw new Error(`personal workspace missing for user ${userId}`);
  return {
    workspaceId: workspace.id,
    workspaceKind: 'personal' as const,
    workspaceName: username,
    organizationId: null,
    teamRole: null,
    monitorQuota: quota,
    canManageMembers: false,
    canManageSecrets: true,
  };
}

/** Cookie session plus fresh membership lookup; stale active teams fall back to personal. */
export const getViewer = cache(async (): Promise<Viewer | null> => {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) return null;
  const u = session.user;
  if (!u.emailVerified) return null;
  const userId = Number(u.id);
  const username = u.displayUsername ?? u.username ?? u.name;
  const globalRole = u.role === 'admin' ? 'admin' : 'user';
  const personalQuota = u.monitorQuota ?? env.DEFAULT_MONITOR_QUOTA;
  const activeOrganizationId = Number(
    (session.session as { activeOrganizationId?: string | number | null }).activeOrganizationId,
  );

  let workspace;
  if (Number.isInteger(activeOrganizationId) && activeOrganizationId > 0) {
    const [team] = await db
      .select({
        workspaceId: workspaces.id,
        organizationId: organizations.id,
        name: organizations.name,
        monitorQuota: organizations.monitorQuota,
        role: members.role,
      })
      .from(members)
      .innerJoin(organizations, eq(organizations.id, members.organizationId))
      .innerJoin(workspaces, eq(workspaces.organizationId, organizations.id))
      .where(
        and(
          eq(members.userId, userId),
          eq(members.organizationId, activeOrganizationId),
          eq(workspaces.kind, 'team'),
        ),
      )
      .limit(1);
    if (team) {
      const teamRole: TeamRole =
        team.role === 'owner' || team.role === 'admin' ? team.role : 'member';
      workspace = {
        workspaceId: team.workspaceId,
        workspaceKind: 'team' as const,
        workspaceName: team.name,
        organizationId: team.organizationId,
        teamRole,
        monitorQuota: team.monitorQuota,
        canManageMembers: teamRole === 'owner' || teamRole === 'admin',
        canManageSecrets: teamRole === 'owner' || teamRole === 'admin',
      };
    }
  }
  workspace ??= await personalWorkspace(userId, username, personalQuota);

  return {
    id: userId,
    username,
    role: globalRole,
    isAdmin: globalRole === 'admin',
    ...workspace,
  };
});

export async function requireViewer(): Promise<Viewer> {
  const viewer = await getViewer();
  if (!viewer) redirect('/login' as Route);
  return viewer;
}

/** Platform-account administration only; never changes workspace data scope. */
export async function requireAdmin(): Promise<Viewer> {
  const viewer = await requireViewer();
  if (!viewer.isAdmin) redirect('/' as Route);
  return viewer;
}

/** Locale-preserving gate for the dedicated platform administration shell. */
export async function requireAdminLocale(locale: string): Promise<Viewer> {
  const normalizedLocale = locale === 'en' ? 'en' : 'zh';
  const viewer = await getViewer();
  if (!viewer) {
    redirect(`/${normalizedLocale}/login?next=${encodeURIComponent(`/${normalizedLocale}/admin`)}` as Route);
  }
  if (!viewer.isAdmin) redirect(`/${normalizedLocale}/monitor` as Route);
  return viewer;
}

export async function requireTeamManager(): Promise<Viewer> {
  const viewer = await requireViewer();
  if (!viewer.canManageMembers || !viewer.organizationId) {
    throw new Error('team manager permission required');
  }
  return viewer;
}

export async function getUserTeam(userId: number): Promise<UserTeam | null> {
  const [team] = await db
    .select({ id: organizations.id, name: organizations.name, role: members.role })
    .from(members)
    .innerJoin(organizations, eq(organizations.id, members.organizationId))
    .where(eq(members.userId, userId))
    .limit(1);
  if (!team) return null;
  return {
    id: team.id,
    name: team.name,
    role: team.role === 'owner' || team.role === 'admin' ? team.role : 'member',
  };
}
