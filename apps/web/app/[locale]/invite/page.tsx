import { notFound } from 'next/navigation';

import { AcceptInvitation } from '@/components/accept-invitation';
import { requireViewer } from '@/lib/auth/viewer';
import { db } from '@/lib/db';
import { invitations } from '@/lib/db/schema';
import { eq } from 'drizzle-orm';

export default async function InvitePage({
  searchParams,
}: {
  searchParams: Promise<{ id?: string }>;
}) {
  await requireViewer();
  const { id } = await searchParams;
  if (!id || !/^\d+$/.test(id)) notFound();
  const [invitation] = await db
    .select({ organizationId: invitations.organizationId })
    .from(invitations)
    .where(eq(invitations.id, Number(id)))
    .limit(1);
  if (!invitation) notFound();
  return (
    <AcceptInvitation
      invitationId={id}
      organizationId={String(invitation.organizationId)}
    />
  );
}
