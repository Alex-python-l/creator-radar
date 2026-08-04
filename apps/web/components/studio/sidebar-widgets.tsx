'use client';

import { Activity, Circle, WalletCards } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api, type HealthData, type WalletData } from '@/lib/studio/api';
import { useStudioT } from '@/lib/studio/i18n';
import { cn } from '@/lib/studio/cn';

// 原 Shell.tsx 底部的钱包余额 + GeeLark 健康灯。只在侧栏的 studio(运营)
// 段挂载——离开运营页面组件卸载,轮询随之停止,radar 页面零开销。
function healthState(health: HealthData | null): {
  tone: 'green' | 'orange' | 'gray' | 'red';
  labelKey: string;
  detail?: string;
} {
  if (!health) return { tone: 'gray', labelKey: 'shell.health.loading' };
  if (health.ok === null) {
    return { tone: 'gray', labelKey: 'shell.health.unknown', detail: health.error };
  }
  if (health.ok === false) {
    return { tone: 'red', labelKey: 'shell.health.degraded', detail: health.db ?? health.error };
  }
  if (!health.worker_last_seen) {
    return { tone: 'orange', labelKey: 'shell.health.degraded' };
  }
  const ageSeconds = Date.now() / 1000 - health.worker_last_seen;
  if (ageSeconds > 15 * 60) {
    return {
      tone: 'orange',
      labelKey: 'shell.health.stale',
      detail: `${Math.floor(ageSeconds / 60)}m`,
    };
  }
  return { tone: 'green', labelKey: 'shell.health.ok' };
}

export function StudioSidebarWidgets() {
  const t = useStudioT();

  const [wallet, setWallet] = useState<WalletData | null>(null);
  useEffect(() => {
    const load = () => api.wallet().then(setWallet).catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const [health, setHealth] = useState<HealthData | null>(null);
  useEffect(() => {
    const load = () =>
      api.healthz().then(setHealth).catch(() => {
        setHealth({ ok: null, error: 'unreachable' });
      });
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  const balanceLabel =
    wallet && typeof wallet.balance === 'number'
      ? `$${wallet.balance.toFixed(2)}`
      : t('shell.balanceUnavailable');
  const healthView = healthState(health);
  const healthTone = {
    green: 'text-[var(--color-green)]',
    orange: 'text-[var(--color-orange)]',
    red: 'text-[var(--color-destructive)]',
    gray: 'text-[var(--color-muted-foreground-3)]',
  }[healthView.tone];

  return (
    <div data-studio className="mt-4 flex flex-col gap-2.5 px-2">
      <div className="rounded-[var(--radius-card)] border border-[var(--color-border)] bg-white/65 px-3 py-2.5 text-xs backdrop-blur-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 text-[var(--color-muted-foreground-2)]">
            <WalletCards size={13} />
            {t('shell.wallet.title')}
          </span>
          <span className="font-semibold tabular-nums text-[var(--color-foreground-2)]">
            {balanceLabel}
          </span>
        </div>
      </div>

      <div
        className="flex items-center justify-between gap-2 rounded-[var(--radius-card)] border border-[var(--color-border)] bg-white/65 px-3 py-2.5 text-xs backdrop-blur-sm"
        title={healthView.detail}
      >
        <span className="inline-flex items-center gap-1.5 text-[var(--color-muted-foreground-2)]">
          <Activity size={13} />
          {t('shell.health.label')}
        </span>
        <span className={cn('inline-flex items-center gap-1.5 font-semibold', healthTone)}>
          <Circle size={8} fill="currentColor" />
          {t(healthView.labelKey)}
        </span>
      </div>
    </div>
  );
}
