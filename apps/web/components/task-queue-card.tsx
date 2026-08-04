import { getTranslations } from 'next-intl/server';

import { Card } from '@/components/ui';
import { cn } from '@/lib/utils';

interface Stats {
  queued: number;
  running: number;
  failedToday: number;
  successToday: number;
  dead: number;
}

export async function TaskQueueCard({
  stats,
  totalAccounts,
}: {
  stats: Stats;
  totalAccounts: number;
}) {
  const t = await getTranslations('taskQueue');
  const isActive = stats.queued > 0 || stats.running > 0;
  const totalToday = stats.successToday + stats.failedToday;

  return (
    <Card className="px-5 py-3">
      <div className="flex items-center gap-6 flex-wrap">
        <div className="text-xs font-semibold text-[color:var(--color-muted-foreground)] uppercase tracking-wide">
          {t('title')}
        </div>

        <Stat
          label={t('running')}
          value={stats.running}
          variant="running"
          active={stats.running > 0}
        />
        <Stat
          label={t('queued')}
          value={stats.queued}
          variant="queued"
          active={stats.queued > 0}
        />
        <Stat
          label={t('successToday')}
          value={`${stats.successToday}/${totalAccounts}`}
          variant={stats.failedToday > 0 || stats.dead > 0 ? 'failed' : 'success'}
        />

        {isActive && (
          <span className="text-xs text-[color:var(--color-muted-foreground)] ml-auto">
            {t('refreshing')}
          </span>
        )}
        {!isActive && totalToday > 0 && (
          <span className="text-xs text-[color:var(--color-muted-foreground)] ml-auto">
            {t('idle', { count: totalToday })}
          </span>
        )}
      </div>
    </Card>
  );
}

const VARIANT_CLASS: Record<'running' | 'queued' | 'success' | 'failed' | 'dead', string> = {
  running: 'bg-[hsl(214_93%_92%)] text-[hsl(214_93%_30%)]',
  queued: 'bg-[hsl(45_93%_92%)] text-[hsl(45_93%_30%)]',
  success: 'bg-[hsl(142_71%_92%)] text-[hsl(142_71%_30%)]',
  failed: 'bg-[hsl(0_84%_94%)] text-[color:var(--color-destructive)]',
  dead: 'bg-[hsl(0_84%_88%)] text-[color:var(--color-destructive)]',
};

function Stat({
  label,
  value,
  variant,
  active,
}: {
  label: string;
  value: number | string;
  variant: keyof typeof VARIANT_CLASS;
  active?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={cn(
          'inline-flex h-6 min-w-[2rem] items-center justify-center rounded px-2 text-xs font-mono font-semibold tabular-nums',
          VARIANT_CLASS[variant],
          active && 'ring-1 ring-current/10',
        )}
      >
        {value}
      </span>
      <span className="text-xs text-[color:var(--color-foreground)]">{label}</span>
    </div>
  );
}
