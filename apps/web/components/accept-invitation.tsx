'use client';

import { Loader2 } from 'lucide-react';
import { useLocale } from 'next-intl';
import { useEffect, useState } from 'react';

import { authClient } from '@/lib/auth/client';

export function AcceptInvitation({ invitationId, organizationId }: {
  invitationId: string;
  organizationId: string;
}) {
  const locale = useLocale();
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    authClient.organization.acceptInvitation({ invitationId }).then(({ error: acceptError }) => {
      if (!live) return;
      if (acceptError) {
        setError(acceptError.message ?? 'Unable to accept invitation');
        return;
      }
      authClient.organization.setActive({ organizationId }).then(({ error: switchError }) => {
        if (!live) return;
        if (switchError) {
          setError(switchError.message ?? 'Unable to switch workspace');
          return;
        }
        window.location.assign(`/${locale}/monitor`);
      });
    });
    return () => {
      live = false;
    };
  }, [invitationId, locale, organizationId]);

  return (
    <div className="flex min-h-svh items-center justify-center bg-[var(--color-background)] p-6">
      <div className="rounded-xl border bg-white p-6 text-center shadow-sm">
        {error ? <p className="text-sm text-red-600">{error}</p> : <><Loader2 className="mx-auto h-5 w-5 animate-spin text-[var(--color-primary)]" /><p className="mt-3 text-sm">正在加入团队…</p></>}
      </div>
    </div>
  );
}
