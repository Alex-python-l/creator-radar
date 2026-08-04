'use server';

import { asc, eq, inArray, sql } from 'drizzle-orm';

import { formatAccountListLabel } from '@/lib/account-display';
import { compareAccountNumberingLabels } from '@/lib/account-numbering-sort';
import { revalidateAccounts } from '@/lib/actions/account-revalidation';
import { requireViewer } from '@/lib/auth/viewer';
import { db } from '@/lib/db';
import {
  accountGroupNumbering,
  accountGroupNumbers,
  accountGroups,
  influencers,
  workspaceInfluencers,
} from '@/lib/db/schema';

const ACCOUNT_NUMBERING_LOCK_NAMESPACE = 20260709;

interface NumberingAccount {
  id: number;
  platform: string;
  username: string;
  displayName: string | null;
  alias: string | null;
}

type DbOrTx = typeof db | Parameters<Parameters<typeof db.transaction>[0]>[0];

/**
 * Group membership and alias always come from workspace subscriptions.
 */
async function listGroupMemberAccounts(
  tx: DbOrTx,
  groupId: number,
): Promise<NumberingAccount[]> {
  const [group] = await tx
    .select({ workspaceId: accountGroups.workspaceId })
    .from(accountGroups)
    .where(eq(accountGroups.id, groupId))
    .limit(1);
  if (!group) return [];

  if (group.workspaceId === null) return [];

  return tx
    .select({
      id: influencers.id,
      platform: influencers.platform,
      username: influencers.username,
      displayName: influencers.displayName,
      alias: workspaceInfluencers.alias,
    })
    .from(workspaceInfluencers)
    .innerJoin(influencers, eq(influencers.id, workspaceInfluencers.influencerId))
    .where(
      sql`${workspaceInfluencers.groupId} = ${groupId} AND ${workspaceInfluencers.workspaceId} = ${group.workspaceId}`,
    )
    .orderBy(asc(influencers.id));
}

function pickValidGroupIds(groupIds: number[]): number[] {
  return [...new Set(groupIds.filter((id) => Number.isInteger(id) && id > 0))];
}

function buildNumberRows(groupId: number, accounts: NumberingAccount[]) {
  return accounts
    .map((account) => ({
      account,
      label: formatAccountListLabel(account),
    }))
    .sort((a, b) => {
      const byLabel = compareAccountNumberingLabels(a.label, b.label);
      return byLabel !== 0 ? byLabel : a.account.id - b.account.id;
    })
    .map(({ account, label }, index) => ({
      groupId,
      influencerId: account.id,
      displayNo: index + 1,
      sortLabel: label,
    }));
}

async function regenerateGroupNumbers(groupId: number): Promise<void> {
  await db.transaction(async (tx) => {
    await tx.execute(
      sql`SELECT pg_advisory_xact_lock(${ACCOUNT_NUMBERING_LOCK_NAMESPACE}, ${groupId})`,
    );

    const [state] = await tx
      .select({ groupId: accountGroupNumbering.groupId })
      .from(accountGroupNumbering)
      .where(eq(accountGroupNumbering.groupId, groupId))
      .limit(1);
    if (!state) return;

    const accounts = await listGroupMemberAccounts(tx, groupId);

    const numbered = buildNumberRows(groupId, accounts);

    await tx.delete(accountGroupNumbers).where(eq(accountGroupNumbers.groupId, groupId));

    if (numbered.length > 0) {
      await tx.insert(accountGroupNumbers).values(numbered);
    }

    await tx
      .update(accountGroupNumbering)
      .set({ regeneratedAt: new Date() })
      .where(eq(accountGroupNumbering.groupId, groupId));
  });
}

export async function hasGroupAccountNumbers(groupId: number): Promise<boolean> {
  if (!Number.isInteger(groupId) || groupId <= 0) return false;

  const [row] = await db
    .select({ count: sql<number>`COUNT(*)::int` })
    .from(accountGroupNumbering)
    .where(eq(accountGroupNumbering.groupId, groupId));

  return Number(row?.count ?? 0) > 0;
}

export async function listNumberedGroupIds(groupIds: number[]): Promise<number[]> {
  const ids = pickValidGroupIds(groupIds);
  if (ids.length === 0) return [];

  const rows = await db
    .select({ groupId: accountGroupNumbering.groupId })
    .from(accountGroupNumbering)
    .where(inArray(accountGroupNumbering.groupId, ids));

  return rows.map((row) => row.groupId);
}

export async function regenerateAccountNumbersForGroups(groupIds: number[]): Promise<void> {
  const ids = pickValidGroupIds(groupIds);
  if (ids.length === 0) return;

  for (const groupId of ids) {
    await regenerateGroupNumbers(groupId);
  }
}

export async function toggleGroupAccountNumbers(
  groupId: number,
): Promise<{ ok: true; active: boolean } | { error: string }> {
  const viewer = await requireViewer();
  if (!Number.isInteger(groupId) || groupId <= 0) return { error: 'invalid group id' };

  const [group] = await db
    .select({ id: accountGroups.id, workspaceId: accountGroups.workspaceId })
    .from(accountGroups)
    .where(eq(accountGroups.id, groupId))
    .limit(1);
  if (!group) return { error: 'group not found' };
  const owned = group.workspaceId === viewer.workspaceId;
  if (!owned) return { error: 'group not found' };

  let active = false;

  await db.transaction(async (tx) => {
    await tx.execute(
      sql`SELECT pg_advisory_xact_lock(${ACCOUNT_NUMBERING_LOCK_NAMESPACE}, ${groupId})`,
    );

    const [existing] = await tx
      .select({ groupId: accountGroupNumbering.groupId })
      .from(accountGroupNumbering)
      .where(eq(accountGroupNumbering.groupId, groupId))
      .limit(1);

    if (existing) {
      await tx
        .delete(accountGroupNumbering)
        .where(eq(accountGroupNumbering.groupId, groupId));
      active = false;
      return;
    }

    await tx.insert(accountGroupNumbering).values({ groupId }).onConflictDoNothing();

    const accounts = await listGroupMemberAccounts(tx, groupId);

    const numbered = buildNumberRows(groupId, accounts);

    await tx.delete(accountGroupNumbers).where(eq(accountGroupNumbers.groupId, groupId));

    if (numbered.length > 0) {
      await tx.insert(accountGroupNumbers).values(numbered);
    }

    await tx
      .update(accountGroupNumbering)
      .set({ regeneratedAt: new Date() })
      .where(eq(accountGroupNumbering.groupId, groupId));
    active = true;
  });

  revalidateAccounts();
  return { ok: true, active };
}
