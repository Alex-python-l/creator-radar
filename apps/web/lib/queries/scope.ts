import { sql, type SQL } from 'drizzle-orm';

import type { AccountRelation } from '@/lib/account-relation';

export type ViewerScope = { workspaceId: number };

/** Every business query joins the active workspace subscription.
 *  传 relation 时限定订阅层（own=自己的账号 / competitor=竞品）。 */
export function scopeJoinSql(viewer: ViewerScope, relation?: AccountRelation): SQL {
  const join = sql`JOIN workspace_influencers wi
    ON wi.influencer_id = i.id AND wi.workspace_id = ${viewer.workspaceId}`;
  if (!relation) return join;
  return sql`${join} AND wi.relation = ${relation}`;
}

export function scopeGroupColSql(viewer: ViewerScope): SQL {
  void viewer;
  return sql`wi.group_id`;
}

export function scopeAliasColSql(viewer: ViewerScope): SQL {
  void viewer;
  return sql`wi.alias`;
}

export function scopeVisibleExistsSql(
  viewer: ViewerScope,
  influencerIdExpr: SQL,
  relation?: AccountRelation,
): SQL {
  const relationFilter = relation ? sql` AND wiv.relation = ${relation}` : sql``;
  return sql`AND EXISTS (
    SELECT 1 FROM workspace_influencers wiv
    WHERE wiv.influencer_id = ${influencerIdExpr}
      AND wiv.workspace_id = ${viewer.workspaceId}${relationFilter}
  )`;
}
