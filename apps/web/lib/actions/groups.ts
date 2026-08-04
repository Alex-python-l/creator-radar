'use server';

import { and, asc, eq, sql } from 'drizzle-orm';
import { revalidatePath } from 'next/cache';
import { z } from 'zod';

import { requireViewer, type Viewer } from '@/lib/auth/viewer';
import { db } from '@/lib/db';
import { accountGroups, workspaceInfluencers } from '@/lib/db/schema';

const DEFAULT_GROUP_ID = 1;
const LOCALES = ['zh', 'en'] as const;

function revalidateGroups() {
  revalidatePath('/monitor');
  for (const locale of LOCALES) {
    revalidatePath(`/${locale}/monitor`);
    revalidatePath(`/${locale}/videos`);
  }
}

/** Postgres unique_violation SQLSTATE。比匹配 e.message 字符串稳健。 */
function isUniqueViolation(e: unknown): boolean {
  return (
    typeof e === 'object' &&
    e !== null &&
    'code' in e &&
    (e as { code?: string }).code === '23505'
  );
}

function ownedGroupWhere(viewer: Viewer, id: number) {
  return and(
    eq(accountGroups.id, id),
    eq(accountGroups.workspaceId, viewer.workspaceId),
  );
}

const nameSchema = z
  .string()
  .trim()
  .min(1, '组名不能为空')
  .max(40, '组名最多 40 字符');

export async function createGroup(formData: FormData) {
  const viewer = await requireViewer();
  const parsed = nameSchema.safeParse(formData.get('name'));
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? '组名无效' };
  }

  const scope = eq(accountGroups.workspaceId, viewer.workspaceId);
  const [{ maxSort }] = await db
    .select({ maxSort: sql<number>`COALESCE(MAX(${accountGroups.sortOrder}), 0)` })
    .from(accountGroups)
    .where(scope);

  try {
    const [row] = await db
      .insert(accountGroups)
      .values({
        name: parsed.data,
        workspaceId: viewer.workspaceId,
        ownerId: viewer.workspaceKind === 'personal' ? viewer.id : null,
        sortOrder: Number(maxSort) + 1,
      })
      .returning({ id: accountGroups.id });
    revalidateGroups();
    return { ok: true, id: row.id };
  } catch (e) {
    if (isUniqueViolation(e)) {
      return { error: '已存在同名组' };
    }
    return { error: e instanceof Error ? e.message : 'insert failed' };
  }
}

export async function renameGroup(id: number, formData: FormData) {
  const viewer = await requireViewer();
  const parsed = nameSchema.safeParse(formData.get('name'));
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? '组名无效' };
  }
  try {
    const rows = await db
      .update(accountGroups)
      .set({ name: parsed.data })
      .where(ownedGroupWhere(viewer, id))
      .returning({ id: accountGroups.id });
    if (rows.length === 0) return { error: 'group not found' };
    revalidateGroups();
    return { ok: true };
  } catch (e) {
    if (isUniqueViolation(e)) {
      return { error: '已存在同名组' };
    }
    return { error: e instanceof Error ? e.message : 'update failed' };
  }
}

export async function deleteGroup(id: number) {
  const viewer = await requireViewer();

  const [group] = await db
    .select({ id: accountGroups.id })
    .from(accountGroups)
    .where(ownedGroupWhere(viewer, id))
    .limit(1);
  if (!group) return { error: 'group not found' };

  const [{ groupCnt }] = await db
    .select({ groupCnt: sql<number>`COUNT(*)` })
    .from(accountGroups)
    .where(eq(accountGroups.workspaceId, viewer.workspaceId));
  if (Number(groupCnt) <= 1 || id === DEFAULT_GROUP_ID) {
    return { error: '至少保留一个分组' };
  }
  const [{ cnt }] = await db
    .select({ cnt: sql<number>`COUNT(*)` })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.groupId, id),
      ),
    );
  if (Number(cnt) > 0) {
    return { error: `组里还有 ${cnt} 个账号，先移走` };
  }

  await db.delete(accountGroups).where(ownedGroupWhere(viewer, id));
  revalidateGroups();
  return { ok: true };
}

/**
 * 与相邻组（按 sortOrder）交换 sort_order 值。
 * direction='up' = 跟前一个换；'down' = 跟后一个换。
 */
export async function reorderGroup(id: number, direction: 'up' | 'down') {
  const viewer = await requireViewer();
  const scope = eq(accountGroups.workspaceId, viewer.workspaceId);
  const rows = await db
    .select({ id: accountGroups.id, sortOrder: accountGroups.sortOrder })
    .from(accountGroups)
    .where(scope)
    .orderBy(asc(accountGroups.sortOrder));
  const idx = rows.findIndex((r) => r.id === id);
  if (idx < 0) return { error: 'group not found' };
  const swapIdx = direction === 'up' ? idx - 1 : idx + 1;
  if (swapIdx < 0 || swapIdx >= rows.length) {
    return { ok: true };
  }
  const a = rows[idx];
  const b = rows[swapIdx];
  await db.transaction(async (tx) => {
    await tx.update(accountGroups).set({ sortOrder: b.sortOrder }).where(eq(accountGroups.id, a.id));
    await tx.update(accountGroups).set({ sortOrder: a.sortOrder }).where(eq(accountGroups.id, b.id));
  });
  revalidateGroups();
  return { ok: true };
}
