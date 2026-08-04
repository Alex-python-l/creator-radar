import { NextResponse } from 'next/server';

import { getViewer } from '@/lib/auth/viewer';

export const dynamic = 'force-dynamic';

/** Studio 的唯一租户鉴权入口。工作空间只从服务端会话解析。 */
export async function GET() {
  const viewer = await getViewer();
  if (!viewer) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  return NextResponse.json({
    user: { id: viewer.id, username: viewer.username },
    workspace: {
      id: viewer.workspaceId,
      kind: viewer.workspaceKind,
      name: viewer.workspaceName,
      organizationId: viewer.organizationId,
      role: viewer.teamRole,
      monitorQuota: viewer.monitorQuota,
      canManageMembers: viewer.canManageMembers,
      canManageSecrets: viewer.canManageSecrets,
    },
  });
}
