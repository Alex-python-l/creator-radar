import 'server-only';

import {
  and,
  asc,
  desc,
  eq,
  gte,
  ilike,
  isNotNull,
  isNull,
  lte,
  or,
  sql,
  type SQL,
} from 'drizzle-orm';

import { db } from '@/lib/db';
import {
  adminAuditLogs,
  invitations,
  members,
  organizations,
  sessions,
  trialApplications,
  users,
  workspaces,
} from '@/lib/db/schema';

export const ADMIN_PAGE_SIZE = 50;

export function adminPage(raw?: string) {
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : 1;
}

export function pageCount(total: number) {
  return Math.max(1, Math.ceil(total / ADMIN_PAGE_SIZE));
}

export async function getAdminOverview() {
  const sevenDaysAgo = new Date(Date.now() - 7 * 86400_000);
  const [userRows, teamRows, workspaceRows, applicationRows, recentAudit] = await Promise.all([
    db
      .select({
        total: sql<number>`COUNT(*)`,
        newUsers: sql<number>`COUNT(*) FILTER (WHERE ${gte(users.createdAt, sevenDaysAgo)})`,
        unverified: sql<number>`COUNT(*) FILTER (WHERE ${users.emailVerified} = false)`,
        banned: sql<number>`COUNT(*) FILTER (WHERE ${users.banned} = true)`,
        admins: sql<number>`COUNT(*) FILTER (WHERE ${users.role} = 'admin')`,
      })
      .from(users),
    db.select({ total: sql<number>`COUNT(*)` }).from(organizations),
    db.execute(sql`
      SELECT
        COUNT(*)::int AS total,
        COUNT(*) FILTER (WHERE w.kind = 'personal')::int AS personal,
        COUNT(*) FILTER (WHERE w.kind = 'team')::int AS team,
        COUNT(*) FILTER (WHERE usage.used > COALESCE(u.monitor_quota, o.monitor_quota, 0))::int AS exceeded
      FROM workspaces w
      LEFT JOIN users u ON u.id = w.user_id
      LEFT JOIN organizations o ON o.id = w.organization_id
      LEFT JOIN LATERAL (
        SELECT COUNT(*)::int AS used
        FROM workspace_influencers wi
        WHERE wi.workspace_id = w.id
      ) usage ON true
    `) as Promise<Array<{ total: number; personal: number; team: number; exceeded: number }>>,
    db
      .select({ total: sql<number>`COUNT(*)` })
      .from(trialApplications)
      .where(eq(trialApplications.status, 'new')),
    db
      .select({
        id: adminAuditLogs.id,
        action: adminAuditLogs.action,
        targetType: adminAuditLogs.targetType,
        targetId: adminAuditLogs.targetId,
        actorName: users.name,
        actorEmail: users.email,
        createdAt: adminAuditLogs.createdAt,
      })
      .from(adminAuditLogs)
      .leftJoin(users, eq(users.id, adminAuditLogs.actorUserId))
      .orderBy(desc(adminAuditLogs.createdAt), desc(adminAuditLogs.id))
      .limit(8),
  ]);

  return {
    users: {
      total: Number(userRows[0]?.total ?? 0),
      newUsers: Number(userRows[0]?.newUsers ?? 0),
      unverified: Number(userRows[0]?.unverified ?? 0),
      banned: Number(userRows[0]?.banned ?? 0),
      admins: Number(userRows[0]?.admins ?? 0),
    },
    teams: Number(teamRows[0]?.total ?? 0),
    workspaces: workspaceRows[0] ?? { total: 0, personal: 0, team: 0, exceeded: 0 },
    pendingApplications: Number(applicationRows[0]?.total ?? 0),
    recentAudit,
  };
}

export type AdminUserFilters = {
  q?: string;
  role?: string;
  state?: string;
  team?: string;
  page?: string;
};

export async function listAdminUsers(filters: AdminUserFilters) {
  const conditions: SQL[] = [];
  const q = filters.q?.trim();
  if (q) {
    const pattern = `%${q}%`;
    conditions.push(
      or(ilike(users.name, pattern), ilike(users.email, pattern), ilike(users.username, pattern))!,
    );
  }
  if (filters.role === 'admin' || filters.role === 'user') conditions.push(eq(users.role, filters.role));
  if (filters.state === 'verified') conditions.push(eq(users.emailVerified, true));
  if (filters.state === 'unverified') conditions.push(eq(users.emailVerified, false));
  if (filters.state === 'banned') conditions.push(eq(users.banned, true));
  if (filters.state === 'active') conditions.push(eq(users.banned, false));
  if (filters.team === 'yes') conditions.push(isNotNull(members.id));
  if (filters.team === 'no') conditions.push(isNull(members.id));

  const where = conditions.length ? and(...conditions) : undefined;
  const page = adminPage(filters.page);
  const [rows, countRows] = await Promise.all([
    db
      .select({
        id: users.id,
        name: users.name,
        username: users.displayUsername,
        email: users.email,
        emailVerified: users.emailVerified,
        role: users.role,
        banned: users.banned,
        monitorQuota: users.monitorQuota,
        createdAt: users.createdAt,
        teamId: organizations.id,
        teamName: organizations.name,
        teamRole: members.role,
        workspaceId: workspaces.id,
        usage: sql<number>`(
          SELECT COUNT(*)::int FROM workspace_influencers wi WHERE wi.workspace_id = ${workspaces.id}
        )`,
        sessions: sql<number>`(
          SELECT COUNT(*)::int FROM sessions s WHERE s.user_id = ${users.id} AND s.expires_at > now()
        )`,
      })
      .from(users)
      .leftJoin(members, eq(members.userId, users.id))
      .leftJoin(organizations, eq(organizations.id, members.organizationId))
      .leftJoin(workspaces, and(eq(workspaces.userId, users.id), eq(workspaces.kind, 'personal')))
      .where(where)
      .orderBy(desc(users.createdAt), desc(users.id))
      .limit(ADMIN_PAGE_SIZE)
      .offset((page - 1) * ADMIN_PAGE_SIZE),
    db
      .select({ total: sql<number>`COUNT(DISTINCT ${users.id})` })
      .from(users)
      .leftJoin(members, eq(members.userId, users.id))
      .where(where),
  ]);
  const total = Number(countRows[0]?.total ?? 0);
  return { rows, total, page, pages: pageCount(total) };
}

export async function getAdminUser(userId: number) {
  const [userRows, sessionRows] = await Promise.all([
    db
      .select({
        id: users.id,
        name: users.name,
        username: users.displayUsername,
        email: users.email,
        emailVerified: users.emailVerified,
        role: users.role,
        banned: users.banned,
        banReason: users.banReason,
        banExpires: users.banExpires,
        monitorQuota: users.monitorQuota,
        createdAt: users.createdAt,
        updatedAt: users.updatedAt,
        teamId: organizations.id,
        teamName: organizations.name,
        teamRole: members.role,
        workspaceId: workspaces.id,
        usage: sql<number>`(
          SELECT COUNT(*)::int FROM workspace_influencers wi WHERE wi.workspace_id = ${workspaces.id}
        )`,
      })
      .from(users)
      .leftJoin(members, eq(members.userId, users.id))
      .leftJoin(organizations, eq(organizations.id, members.organizationId))
      .leftJoin(workspaces, and(eq(workspaces.userId, users.id), eq(workspaces.kind, 'personal')))
      .where(eq(users.id, userId))
      .limit(1),
    db
      .select({
        id: sessions.id,
        createdAt: sessions.createdAt,
        updatedAt: sessions.updatedAt,
        expiresAt: sessions.expiresAt,
        ipAddress: sessions.ipAddress,
        userAgent: sessions.userAgent,
        activeOrganizationId: sessions.activeOrganizationId,
      })
      .from(sessions)
      .where(and(eq(sessions.userId, userId), gte(sessions.expiresAt, new Date())))
      .orderBy(desc(sessions.updatedAt)),
  ]);
  return userRows[0] ? { ...userRows[0], sessions: sessionRows } : null;
}

export type AdminTeamFilters = { q?: string; usage?: string; page?: string };

export async function listAdminTeams(filters: AdminTeamFilters) {
  const conditions: SQL[] = [eq(members.role, 'owner')];
  const q = filters.q?.trim();
  if (q) {
    const pattern = `%${q}%`;
    conditions.push(or(ilike(organizations.name, pattern), ilike(users.email, pattern))!);
  }
  const usedSql = sql`(SELECT COUNT(*) FROM workspace_influencers wi WHERE wi.workspace_id = ${workspaces.id})`;
  if (filters.usage === 'exceeded') conditions.push(sql`${usedSql} > ${organizations.monitorQuota}`);
  if (filters.usage === 'available') conditions.push(sql`${usedSql} <= ${organizations.monitorQuota}`);
  const page = adminPage(filters.page);
  const where = and(...conditions);
  const [rows, countRows] = await Promise.all([
    db
      .select({
        id: organizations.id,
        name: organizations.name,
        slug: organizations.slug,
        quota: organizations.monitorQuota,
        createdAt: organizations.createdAt,
        ownerId: users.id,
        ownerName: users.name,
        ownerEmail: users.email,
        workspaceId: workspaces.id,
        usage: sql<number>`${usedSql}`,
        memberCount: sql<number>`(
          SELECT COUNT(*)::int FROM members team_members WHERE team_members.organization_id = ${organizations.id}
        )`,
      })
      .from(organizations)
      .innerJoin(members, eq(members.organizationId, organizations.id))
      .innerJoin(users, eq(users.id, members.userId))
      .innerJoin(workspaces, and(eq(workspaces.organizationId, organizations.id), eq(workspaces.kind, 'team')))
      .where(where)
      .orderBy(desc(organizations.createdAt), desc(organizations.id))
      .limit(ADMIN_PAGE_SIZE)
      .offset((page - 1) * ADMIN_PAGE_SIZE),
    db
      .select({ total: sql<number>`COUNT(*)` })
      .from(organizations)
      .innerJoin(members, eq(members.organizationId, organizations.id))
      .innerJoin(users, eq(users.id, members.userId))
      .innerJoin(workspaces, and(eq(workspaces.organizationId, organizations.id), eq(workspaces.kind, 'team')))
      .where(where),
  ]);
  const total = Number(countRows[0]?.total ?? 0);
  return { rows, total, page, pages: pageCount(total) };
}

export async function getAdminTeam(teamId: number) {
  const [teamRows, memberRows, inviteRows] = await Promise.all([
    db
      .select({
        id: organizations.id,
        name: organizations.name,
        slug: organizations.slug,
        quota: organizations.monitorQuota,
        createdAt: organizations.createdAt,
        updatedAt: organizations.updatedAt,
        workspaceId: workspaces.id,
        usage: sql<number>`(
          SELECT COUNT(*)::int FROM workspace_influencers wi WHERE wi.workspace_id = ${workspaces.id}
        )`,
      })
      .from(organizations)
      .innerJoin(workspaces, and(eq(workspaces.organizationId, organizations.id), eq(workspaces.kind, 'team')))
      .where(eq(organizations.id, teamId))
      .limit(1),
    db
      .select({
        memberId: members.id,
        userId: users.id,
        name: users.name,
        email: users.email,
        role: members.role,
        banned: users.banned,
        joinedAt: members.createdAt,
      })
      .from(members)
      .innerJoin(users, eq(users.id, members.userId))
      .where(eq(members.organizationId, teamId))
      .orderBy(asc(sql`CASE WHEN ${members.role} = 'owner' THEN 0 ELSE 1 END`), asc(users.name)),
    db
      .select({ id: invitations.id, email: invitations.email, role: invitations.role, expiresAt: invitations.expiresAt })
      .from(invitations)
      .where(and(eq(invitations.organizationId, teamId), eq(invitations.status, 'pending')))
      .orderBy(desc(invitations.createdAt)),
  ]);
  return teamRows[0] ? { ...teamRows[0], members: memberRows, invitations: inviteRows } : null;
}

export type AdminWorkspaceFilters = { q?: string; kind?: string; usage?: string; page?: string };

export async function listAdminWorkspaces(filters: AdminWorkspaceFilters) {
  const conditions: SQL[] = [];
  const q = filters.q?.trim();
  if (q) {
    const pattern = `%${q}%`;
    const numericId = Number(q);
    conditions.push(
      or(
        ilike(users.email, pattern),
        ilike(users.name, pattern),
        ilike(organizations.name, pattern),
        ...(Number.isInteger(numericId) ? [eq(workspaces.id, numericId)] : []),
      )!,
    );
  }
  if (filters.kind === 'personal' || filters.kind === 'team') conditions.push(eq(workspaces.kind, filters.kind));
  const quotaSql = sql`COALESCE(${users.monitorQuota}, ${organizations.monitorQuota}, 0)`;
  const usedSql = sql`(SELECT COUNT(*) FROM workspace_influencers wi WHERE wi.workspace_id = ${workspaces.id})`;
  if (filters.usage === 'exceeded') conditions.push(sql`${usedSql} > ${quotaSql}`);
  if (filters.usage === 'available') conditions.push(sql`${usedSql} <= ${quotaSql}`);
  const where = conditions.length ? and(...conditions) : undefined;
  const page = adminPage(filters.page);
  const [rows, countRows] = await Promise.all([
    db
      .select({
        id: workspaces.id,
        kind: workspaces.kind,
        createdAt: workspaces.createdAt,
        userId: users.id,
        userName: users.name,
        userEmail: users.email,
        teamId: organizations.id,
        teamName: organizations.name,
        quota: sql<number>`${quotaSql}`,
        usage: sql<number>`${usedSql}`,
      })
      .from(workspaces)
      .leftJoin(users, eq(users.id, workspaces.userId))
      .leftJoin(organizations, eq(organizations.id, workspaces.organizationId))
      .where(where)
      .orderBy(desc(workspaces.createdAt), desc(workspaces.id))
      .limit(ADMIN_PAGE_SIZE)
      .offset((page - 1) * ADMIN_PAGE_SIZE),
    db
      .select({ total: sql<number>`COUNT(*)` })
      .from(workspaces)
      .leftJoin(users, eq(users.id, workspaces.userId))
      .leftJoin(organizations, eq(organizations.id, workspaces.organizationId))
      .where(where),
  ]);
  const total = Number(countRows[0]?.total ?? 0);
  return { rows, total, page, pages: pageCount(total) };
}

export type AdminAuditFilters = {
  q?: string;
  action?: string;
  target?: string;
  from?: string;
  to?: string;
  page?: string;
};

export async function listAdminAuditLogs(filters: AdminAuditFilters) {
  const conditions: SQL[] = [];
  const q = filters.q?.trim();
  if (q) {
    const pattern = `%${q}%`;
    conditions.push(or(ilike(users.name, pattern), ilike(users.email, pattern))!);
  }
  if (filters.action?.trim()) conditions.push(ilike(adminAuditLogs.action, `%${filters.action.trim()}%`));
  if (filters.target?.trim()) conditions.push(eq(adminAuditLogs.targetType, filters.target.trim()));
  if (filters.from && /^\d{4}-\d{2}-\d{2}$/.test(filters.from)) {
    conditions.push(gte(adminAuditLogs.createdAt, new Date(`${filters.from}T00:00:00`)));
  }
  if (filters.to && /^\d{4}-\d{2}-\d{2}$/.test(filters.to)) {
    conditions.push(lte(adminAuditLogs.createdAt, new Date(`${filters.to}T23:59:59.999`)));
  }
  const where = conditions.length ? and(...conditions) : undefined;
  const page = adminPage(filters.page);
  const [rows, countRows] = await Promise.all([
    db
      .select({
        id: adminAuditLogs.id,
        action: adminAuditLogs.action,
        targetType: adminAuditLogs.targetType,
        targetId: adminAuditLogs.targetId,
        before: adminAuditLogs.before,
        after: adminAuditLogs.after,
        reason: adminAuditLogs.reason,
        actorIp: adminAuditLogs.actorIp,
        createdAt: adminAuditLogs.createdAt,
        actorId: users.id,
        actorName: users.name,
        actorEmail: users.email,
      })
      .from(adminAuditLogs)
      .leftJoin(users, eq(users.id, adminAuditLogs.actorUserId))
      .where(where)
      .orderBy(desc(adminAuditLogs.createdAt), desc(adminAuditLogs.id))
      .limit(ADMIN_PAGE_SIZE)
      .offset((page - 1) * ADMIN_PAGE_SIZE),
    db
      .select({ total: sql<number>`COUNT(*)` })
      .from(adminAuditLogs)
      .leftJoin(users, eq(users.id, adminAuditLogs.actorUserId))
      .where(where),
  ]);
  const total = Number(countRows[0]?.total ?? 0);
  return { rows, total, page, pages: pageCount(total) };
}
