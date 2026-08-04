import { NextResponse } from 'next/server';
import { inArray, sql } from 'drizzle-orm';

import { getViewer } from '@/lib/auth/viewer';
import { db } from '@/lib/db';
import { jobs } from '@/lib/db/schema';

/**
 * 轻量任务状态端点,给 AutoRefresh 决定 polling 频率用。
 * 只查 active(queued+running)数量,不查 today/dead 等统计 —
 * 那些走 RSC getFetchTaskStats,在 router.refresh() 时一并更新。
 *
 * 调用频率:active 时 5s,idle 时 60s。单 SQL 走 jobs_status_next_run_idx,
 * 走 partial scan,生产 DB 压力可忽略。
 */
export async function GET(): Promise<NextResponse> {
  const viewer = await getViewer();
  if (!viewer) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const rows = await db
    .select({ c: sql<number>`COUNT(*)` })
    .from(jobs)
    .where(inArray(jobs.status, ['queued', 'running']));
  const active = Number(rows[0]?.c ?? 0);

  return NextResponse.json(
    { active },
    { headers: { 'Cache-Control': 'private, no-store' } },
  );
}
