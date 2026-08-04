import 'server-only';

import { headers } from 'next/headers';

import { requireAdmin } from '@/lib/auth/viewer';
import type { NewAdminAuditLog } from '@/lib/db/schema';

export async function getAdminMutationContext() {
  const [actor, requestHeaders] = await Promise.all([requireAdmin(), headers()]);
  const actorIp =
    requestHeaders.get('x-real-ip') ??
    requestHeaders.get('x-forwarded-for')?.split(',')[0]?.trim() ??
    null;
  return { actor, actorIp };
}

export function adminAuditEntry(
  context: Awaited<ReturnType<typeof getAdminMutationContext>>,
  entry: Omit<NewAdminAuditLog, 'actorUserId' | 'actorIp'>,
): NewAdminAuditLog {
  return {
    ...entry,
    actorUserId: context.actor.id,
    actorIp: context.actorIp,
  };
}
