import { NextResponse } from 'next/server';

import { getViewer } from '@/lib/auth/viewer';
import type { MonitorAnalysisRange } from '@/lib/monitor-analysis';
import {
  getMonitorAnalysisSnapshot,
  MonitorAnalysisGroupNotFoundError,
} from '@/lib/queries/monitor-analysis';
import { isPlatform } from '@svm/shared/platforms';

export const dynamic = 'force-dynamic';

const RANGES = new Set<MonitorAnalysisRange>(['today', '7d', '30d']);

export async function POST(request: Request) {
  const viewer = await getViewer();
  if (!viewer) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'invalid_request' }, { status: 400 });
  }
  if (!body || typeof body !== 'object') {
    return NextResponse.json({ error: 'invalid_request' }, { status: 400 });
  }
  const value = body as Record<string, unknown>;
  const groupId = value.groupId;
  const platform = value.platform;
  const range = value.range;
  if (
    (groupId !== undefined && (!Number.isInteger(groupId) || Number(groupId) <= 0)) ||
    (platform !== 'all' && (typeof platform !== 'string' || !isPlatform(platform))) ||
    typeof platform !== 'string' ||
    typeof range !== 'string' ||
    !RANGES.has(range as MonitorAnalysisRange)
  ) {
    return NextResponse.json({ error: 'invalid_request' }, { status: 400 });
  }

  try {
    const snapshot = await getMonitorAnalysisSnapshot(viewer, {
      groupId: groupId === undefined ? undefined : Number(groupId),
      platform,
      range: range as MonitorAnalysisRange,
    });
    return NextResponse.json(snapshot, {
      headers: { 'Cache-Control': 'private, no-store' },
    });
  } catch (error) {
    if (error instanceof MonitorAnalysisGroupNotFoundError) {
      return NextResponse.json({ error: 'group_not_found' }, { status: 404 });
    }
    throw error;
  }
}
