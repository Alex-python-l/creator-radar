'use server';

import { and, eq, inArray, sql } from 'drizzle-orm';
import { z } from 'zod';

import {
  listNumberedGroupIds,
  regenerateAccountNumbersForGroups,
} from '@/lib/actions/account-numbering';
import { revalidateAccounts } from '@/lib/actions/account-revalidation';
import { isAccountRelation, type AccountRelation } from '@/lib/account-relation';
import { requireViewer, type Viewer } from '@/lib/auth/viewer';
import { db } from '@/lib/db';
import { accountGroups, influencers, jobs, workspaceInfluencers } from '@/lib/db/schema';
import { normalizeBulkAccounts, type BulkAccountInput } from '@/lib/import-parsers';
import { resolveFbUrl } from '@/lib/scraper-client';
import { isPlatform, platformProfileUrl, type Platform } from '@svm/shared/platforms';

const ACCOUNT_FETCH_LOCK_NAMESPACE = 20260617;

/**
 * 给一个 influencer 排队一次抓取：
 * 1) 去重：已有 queued/running 的同账号 account.fetch job 就不重复入队
 * 2) 否则 INSERT jobs，由 scraper worker 消费
 *
 * mode='full'（默认）：批量导入/手动操作 → handler 拉更多视频
 * mode='recent'：每日定时 → 只拉最近一小批
 */
async function enqueueFetch(args: {
  influencerId: number;
  platform: Platform;
  username: string;
  mode?: 'full' | 'recent';
}): Promise<{ jobId: number; deduped: boolean }> {
  const mode = args.mode ?? 'full';

  return db.transaction(async (tx) => {
    await tx.execute(
      sql`SELECT pg_advisory_xact_lock(${ACCOUNT_FETCH_LOCK_NAMESPACE}, ${args.influencerId})`,
    );

    // 去重：已有 queued/running 的同账号 account.fetch job 就不重复入队
    const existing = await tx
      .select({ id: jobs.id })
      .from(jobs)
      .where(
        and(
          eq(jobs.kind, 'account.fetch'),
          inArray(jobs.status, ['queued', 'running']),
          sql`${jobs.payload}->>'influencerId' = ${String(args.influencerId)}`,
        ),
      )
      .limit(1);
    if (existing.length > 0) {
      return { jobId: existing[0].id, deduped: true };
    }

    const [row] = await tx
      .insert(jobs)
      .values({
        kind: 'account.fetch',
        status: 'queued',
        payload: {
          influencerId: args.influencerId,
          platform: args.platform,
          username: args.username,
          mode,
        },
      })
      .returning({ id: jobs.id });

    return { jobId: row.id, deduped: false };
  });
}

/**
 * Remove only the active workspace subscription. The globally deduplicated
 * influencer and history remain available to other workspaces.
 */
export async function deleteAccount(id: number) {
  const viewer = await requireViewer();

  const [sub] = await db
    .select({ groupId: workspaceInfluencers.groupId })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.influencerId, id),
      ),
    )
    .limit(1);
  if (!sub) return;
  const numberedGroupIds = sub.groupId ? await listNumberedGroupIds([sub.groupId]) : [];
  await db.transaction(async (tx) => {
    await tx
      .delete(workspaceInfluencers)
      .where(
        and(
          eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
          eq(workspaceInfluencers.influencerId, id),
        ),
      );
    const [remaining] = await tx
      .select({ count: sql<number>`COUNT(*)` })
      .from(workspaceInfluencers)
      .where(eq(workspaceInfluencers.influencerId, id));
    if (Number(remaining.count) === 0) {
      await tx.update(influencers).set({ isMonitored: false }).where(eq(influencers.id, id));
    }
  });
  await regenerateAccountNumbersForGroups(numberedGroupIds);
  revalidateAccounts(id);
}

/**
 * 分批并发 — share resolve 单 chunk 串行打 scraper,避免代理池被打爆。
 * concurrency=3 是经验值: scraper 1C1G + 代理池通常 3-5 个出口,3 路并发够快又不撞 IP。
 */
async function chunkConcurrent<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const out: R[] = [];
  for (let i = 0; i < items.length; i += limit) {
    const chunk = items.slice(i, i + limit);
    const r = await Promise.all(chunk.map(fn));
    out.push(...r);
  }
  return out;
}

/** Group must belong to the active workspace. */
async function groupInScope(viewer: Viewer, groupId: number): Promise<boolean> {
  const [grp] = await db
    .select({ id: accountGroups.id })
    .from(accountGroups)
    .where(
      and(
        eq(accountGroups.id, groupId),
        eq(accountGroups.workspaceId, viewer.workspaceId),
      ),
    )
    .limit(1);
  return Boolean(grp);
}

export async function bulkAddAccounts(
  accounts: BulkAccountInput[],
  groupId: number,
  relation: AccountRelation = 'own',
): Promise<{
  inserted: number;
  duplicateInDb: number;
  skipped: number;
  resolveErrors?: string[];
  quotaExceeded?: boolean;
}> {
  const viewer = await requireViewer();
  if (!accounts || accounts.length === 0) return { inserted: 0, duplicateInDb: 0, skipped: 0 };
  // server action 是公开 RPC,relation 入参必须校验。
  if (!isAccountRelation(relation)) {
    return { inserted: 0, duplicateInDb: 0, skipped: accounts.length };
  }
  // 竞品无分组轴:忽略 groupId 入参,订阅行 group_id 写 NULL。
  const isCompetitor = relation === 'competitor';
  if (
    !isCompetitor &&
    (!groupId || !Number.isInteger(groupId) || !(await groupInScope(viewer, groupId)))
  ) {
    return { inserted: 0, duplicateInDb: 0, skipped: accounts.length };
  }

  // 先 resolve FB 非稳定 identifier,再按 canonical username 做服务端二次去重。
  // resolve 是网络往返,分批并发 3 路避免 Server Action 被 10+ 条 FB URL 拖过 30s。
  const resolvedChunks = await chunkConcurrent(accounts, 3, async (a) =>
    normalizeBulkAccounts([a], resolveFbUrl),
  );
  const normalized = await normalizeBulkAccounts(
    resolvedChunks.flatMap((r) => r.accounts),
    resolveFbUrl,
  );
  const resolveErrors = [
    ...resolvedChunks.flatMap((r) => r.resolveErrors),
    ...normalized.resolveErrors,
  ];

  // 配额按当前 workspace 订阅数计算。此处非事务读——极端并发下可能少量超额,
  // 对"防公众用户打爆抓取资源"这个目的无碍。
  const [{ cnt }] = await db
    .select({ cnt: sql<number>`COUNT(*)` })
    .from(workspaceInfluencers)
    .where(eq(workspaceInfluencers.workspaceId, viewer.workspaceId));
  let quotaLeft = Math.max(0, viewer.monitorQuota - Number(cnt));

  const numberedGroupIds = isCompetitor ? [] : await listNumberedGroupIds([groupId]);

  const insertedIds: Array<{ id: number; platform: Platform; username: string }> = [];
  let duplicateInDb = 0;
  let quotaExceeded = false;
  for (const a of normalized.accounts) {
    const username = a.username.trim().replace(/^@/, '');
    if (!username || !isPlatform(a.platform)) continue;

    const profileUrl = platformProfileUrl(a.platform, username);

    try {
      // influencers 全局一份(冲突取已有行,历史数据即刻可见),
      // 订阅行才是新增点。已订阅 = duplicateInDb;新 influencer 才入抓取队列。
      if (quotaLeft <= 0) {
        quotaExceeded = true;
        break;
      }
      const inserted = await db
        .insert(influencers)
        // 全局新账号进系统默认组(admin 世界的"未分组");user 的组轴在订阅行。
        .values({
          platform: a.platform,
          username,
          profileUrl,
          isActive: true,
          isMonitored: true,
          groupId: 1,
        })
        .onConflictDoNothing()
        .returning({ id: influencers.id });
      const isNewInfluencer = Boolean(inserted[0]?.id);
      const influencerId =
        inserted[0]?.id ??
        (
          await db
            .select({ id: influencers.id })
            .from(influencers)
            .where(and(eq(influencers.platform, a.platform), eq(influencers.username, username)))
            .limit(1)
        )[0]?.id;
      if (!influencerId) continue;

      const subRows = await db
        .insert(workspaceInfluencers)
        .values({
          workspaceId: viewer.workspaceId,
          influencerId,
          groupId: isCompetitor ? null : groupId,
          relation,
        })
        .onConflictDoNothing()
        .returning({ influencerId: workspaceInfluencers.influencerId });
      if (subRows.length === 0) {
        duplicateInDb += 1; // 已订阅过
        continue;
      }
      quotaLeft -= 1;
      if (!isNewInfluencer) {
        await db
          .update(influencers)
          .set({ isActive: true, isMonitored: true })
          .where(eq(influencers.id, influencerId));
      }
      if (isNewInfluencer) {
        insertedIds.push({ id: influencerId, platform: a.platform, username });
      } else {
        // 已有全局账号:订阅即见历史数据,计入 inserted 口径(对用户是"加成功")。
        insertedIds.push({ id: influencerId, platform: a.platform, username: '' });
      }
    } catch {
      // 单条失败跳过，不阻塞整个 batch
    }
  }

  if (insertedIds.length > 0) {
    await regenerateAccountNumbersForGroups(numberedGroupIds);
  }

  for (const a of insertedIds) {
    // username 为空 = 复用已有全局账号,已在每日刷新池内,不重复入队。
    if (a.username) {
      await enqueueFetch({ influencerId: a.id, platform: a.platform, username: a.username });
    }
  }

  revalidateAccounts();
  return {
    inserted: insertedIds.length,
    duplicateInDb,
    skipped: accounts.length - insertedIds.length - duplicateInDb,
    ...(resolveErrors.length > 0 ? { resolveErrors } : {}),
    ...(quotaExceeded ? { quotaExceeded: true } : {}),
  };
}

/**
 * 重试当前最新状态仍为死信的活跃账号。
 *
 * jobs.dead 是历史任务行,同一账号可累积多条,账号后续成功后旧 dead 行也不会自动消失。
 * 所以按钮计数和重试集合都按"每个账号最新 account.fetch job 是否 dead"判断。
 * 点击后仍把本轮看到的所有 dead 行标 retired,顺手清理已删除账号和后来成功账号的旧死信。
 */
export async function retryFailedFetches(
  relation: AccountRelation = 'own',
): Promise<{ enqueued: number; deduped: number }> {
  const viewer = await requireViewer();
  if (!isAccountRelation(relation)) return { enqueued: 0, deduped: 0 };
  const deadJobRows = (await db.execute(sql`
    SELECT j.id
    FROM jobs j
    WHERE j.kind = 'account.fetch'
      AND j.status = 'dead'
      AND j.payload->>'influencerId' ~ '^[0-9]+$'
      AND EXISTS (
        SELECT 1 FROM workspace_influencers wi
        WHERE wi.workspace_id = ${viewer.workspaceId}
          AND wi.influencer_id = (j.payload->>'influencerId')::int
          AND wi.relation = ${relation}
      )
  `)) as Array<{ id: number }>;

  if (deadJobRows.length === 0) {
    return { enqueued: 0, deduped: 0 };
  }

  const retryableRows = (await db.execute(sql`
    WITH latest_account_fetch_jobs AS (
      SELECT DISTINCT ON ((payload->>'influencerId')::int)
        (payload->>'influencerId')::int AS influencer_id,
        status
      FROM jobs
      WHERE kind = 'account.fetch'
        AND payload->>'influencerId' ~ '^[0-9]+$'
      ORDER BY
        (payload->>'influencerId')::int,
        COALESCE(updated_at, created_at) DESC,
        id DESC
    )
    SELECT
      i.id AS influencer_id,
      i.platform,
      i.username
    FROM latest_account_fetch_jobs latest_jobs
    JOIN influencers i ON i.id = latest_jobs.influencer_id
    WHERE i.is_active = true
      AND latest_jobs.status = 'dead'
      AND EXISTS (
        SELECT 1 FROM workspace_influencers wi
        WHERE wi.workspace_id = ${viewer.workspaceId}
          AND wi.influencer_id = i.id
          AND wi.relation = ${relation}
      )
  `)) as Array<{
    influencer_id: number;
    platform: string;
    username: string;
  }>;

  const eligible = retryableRows.filter((r) => isPlatform(r.platform));

  const results = await Promise.all(
    eligible.map((r) =>
      enqueueFetch({
        influencerId: r.influencer_id,
        platform: r.platform as Platform,
        username: r.username,
        mode: 'full',
      }),
    ),
  );

  const deadJobIds = deadJobRows.map((r) => r.id);
  await db
    .update(jobs)
    .set({ status: 'retired', updatedAt: new Date() })
    .where(
      inArray(
        jobs.id,
        deadJobIds,
      ),
    );

  let enqueued = 0;
  let deduped = 0;
  for (const r of results) {
    if (r.deduped) deduped++;
    else enqueued++;
  }
  revalidateAccounts();
  return { enqueued, deduped };
}

export async function retryFailedFetchesForm(): Promise<void> {
  await retryFailedFetches();
}

export async function triggerFetch(id: number) {
  const viewer = await requireViewer();
  const [row] = await db.select().from(influencers).where(eq(influencers.id, id)).limit(1);
  if (!row) return { error: 'account not found' };

  const [sub] = await db
    .select({ influencerId: workspaceInfluencers.influencerId })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.influencerId, id),
      ),
    )
    .limit(1);
  if (!sub) return { error: 'account not found' };

  if (!isPlatform(row.platform)) {
    return { error: `unsupported platform: ${row.platform}` };
  }

  await enqueueFetch({
    influencerId: row.id,
    platform: row.platform,
    username: row.username,
  });

  // 不 redirect — 用户在哪个页面就停在哪个页面，状态徽章会自动刷新
  revalidateAccounts(id);
}

export async function triggerFetchForm(id: number): Promise<void> {
  await triggerFetch(id);
}

export async function moveAccountToGroup(accountId: number, groupId: number) {
  const viewer = await requireViewer();
  if (!Number.isInteger(accountId) || accountId <= 0) return { error: 'invalid account id' };
  if (!Number.isInteger(groupId) || groupId <= 0) return { error: 'invalid group id' };

  if (!(await groupInScope(viewer, groupId))) return { error: 'target group not found' };

  // 只允许移动自有账号:竞品行 group_id 恒为 NULL,不进任何分组。
  const [sub] = await db
    .select({ groupId: workspaceInfluencers.groupId })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.influencerId, accountId),
        eq(workspaceInfluencers.relation, 'own'),
      ),
    )
    .limit(1);
  if (!sub) return { error: 'account not found' };
  const numberedGroupIds = await listNumberedGroupIds(
    [sub.groupId, groupId].flatMap((g) => (g == null ? [] : [g])),
  );
  await db
    .update(workspaceInfluencers)
    .set({ groupId })
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.influencerId, accountId),
        eq(workspaceInfluencers.relation, 'own'),
      ),
    );
  await regenerateAccountNumbersForGroups(numberedGroupIds);
  revalidateAccounts(accountId);
  return { ok: true };
}

const bulkMoveSchema = z.object({
  targetGroupId: z.coerce.number().int().positive(),
  accountIds: z.array(z.coerce.number().int().positive()).min(1, '至少选 1 个账号'),
});

export async function moveAccountsToGroup(formData: FormData) {
  const viewer = await requireViewer();
  const targetGroupId = formData.get('targetGroupId');
  const accountIds = formData.getAll('accountIds');
  const parsed = bulkMoveSchema.safeParse({ targetGroupId, accountIds });
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? 'invalid form' };
  }
  const { targetGroupId: gid, accountIds: ids } = parsed.data;

  if (!(await groupInScope(viewer, gid))) return { error: 'target group not found' };

  // 同 moveAccountToGroup:批量移组只作用于自有账号,竞品行不进分组。
  const subs = await db
    .select({ groupId: workspaceInfluencers.groupId })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        inArray(workspaceInfluencers.influencerId, ids),
        eq(workspaceInfluencers.relation, 'own'),
      ),
    );
  const numberedGroupIds = await listNumberedGroupIds([
    gid,
    ...subs.flatMap((s) => (s.groupId == null ? [] : [s.groupId])),
  ]);
  const moved = await db
    .update(workspaceInfluencers)
    .set({ groupId: gid })
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        inArray(workspaceInfluencers.influencerId, ids),
        eq(workspaceInfluencers.relation, 'own'),
      ),
    )
    .returning({ influencerId: workspaceInfluencers.influencerId });
  await regenerateAccountNumbersForGroups(numberedGroupIds);
  revalidateAccounts();
  return { ok: true, moved: moved.length };
}

const ALIAS_MAX_LEN = 50;

/**
 * 设置或清除账号别名。
 * - raw.trim() 后为空 → 写入 NULL（清除别名）
 * - 长度 1..ALIAS_MAX_LEN
 * - 同平台 partial unique（DB 兜底），命中 PG 23505 时返回 'alias duplicate'
 * - 别名不参与抓取/排序/搜索，纯人眼标签
 */
export async function setAccountAlias(
  accountId: number,
  raw: string,
): Promise<{ ok: true } | { error: string }> {
  const viewer = await requireViewer();
  if (!Number.isInteger(accountId) || accountId <= 0) {
    return { error: 'invalid account id' };
  }

  const trimmed = (raw ?? '').trim();
  if (trimmed.length > ALIAS_MAX_LEN) {
    return { error: 'alias too long' };
  }

  const [sub] = await db
    .select({ groupId: workspaceInfluencers.groupId })
    .from(workspaceInfluencers)
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.influencerId, accountId),
      ),
    )
    .limit(1);
  if (!sub) return { error: 'account not found' };
  const numberedGroupIds = sub.groupId ? await listNumberedGroupIds([sub.groupId]) : [];
  await db
    .update(workspaceInfluencers)
    .set({ alias: trimmed || null })
    .where(
      and(
        eq(workspaceInfluencers.workspaceId, viewer.workspaceId),
        eq(workspaceInfluencers.influencerId, accountId),
      ),
    );
  await regenerateAccountNumbersForGroups(numberedGroupIds);

  revalidateAccounts(accountId);
  return { ok: true };
}
