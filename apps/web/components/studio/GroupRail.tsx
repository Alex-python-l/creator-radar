'use client'

import { useStudioT } from '@/lib/studio/i18n'
import { Plus } from 'lucide-react'
import { Badge, Button } from '@/components/ui'
import { cn } from '@/lib/studio/cn'

export type GroupSummary = {
  id: number
  name: string
  notes?: string | null
  caption_prompt_id?: number | null
  counts: {
    phones: number
    assigned: number
    logged_in: number
    warmed: number
    published: number
    pending: number
    failed: number
  }
}

export const ALL_ACCOUNTS = 'all' as const

type Props = {
  groups: GroupSummary[]
  totalAccounts: number
  selected: number | 'all'
  onSelect: (id: number | 'all') => void
  onNewGroup: () => void
}

export function GroupRail(props: Props) {
  const { groups, totalAccounts, selected, onSelect, onNewGroup } = props
  const t = useStudioT()

  const failedTotal = groups.reduce((sum, g) => sum + (g.counts.failed ?? 0), 0)
  const pendingTotal = groups.reduce((sum, g) => sum + (g.counts.pending ?? 0), 0)

  return (
    <aside className="w-full border-b border-[var(--color-border)] bg-[var(--color-card)] p-3 lg:sticky lg:top-0 lg:h-[calc(100vh-0px)] lg:w-[248px] lg:shrink-0 lg:self-start lg:border-b-0 lg:border-r lg:overflow-y-auto">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[13px] font-semibold text-[var(--color-foreground)]">
            {t('accounts.groupRail.title')}
          </div>
        </div>
        <Button size="sm" variant="secondary" onClick={onNewGroup}>
          <Plus size={13} />
          {t('accounts.groupRail.new')}
        </Button>
      </div>

      <div className="mt-3 space-y-1">
        <Row
          label={t('accounts.groupRail.allAccounts')}
          countLabel={t('accounts.groupRail.phoneCount', { count: totalAccounts })}
          active={selected === ALL_ACCOUNTS}
          pending={pendingTotal}
          failed={failedTotal}
          onClick={() => onSelect(ALL_ACCOUNTS)}
        />
        {groups.map((g) => (
          <Row
            key={g.id}
            label={g.name}
            countLabel={t('accounts.groupRail.phoneCount', { count: g.counts.phones })}
            active={selected === g.id}
            pending={g.counts.pending}
            failed={g.counts.failed}
            onClick={() => onSelect(g.id)}
          />
        ))}
      </div>
    </aside>
  )
}

function Row({
  label,
  countLabel,
  active,
  pending,
  failed,
  onClick,
}: {
  label: string
  countLabel: string
  active: boolean
  pending: number
  failed: number
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'group relative flex w-full items-center justify-between gap-3 rounded-[7px] px-2.5 py-2 text-left',
        'transition-colors',
        active
          ? 'bg-[var(--color-gray-bg)] text-[var(--color-foreground)]'
          : 'text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)]',
      )}
    >
      {active ? (
        <span
          className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-[var(--color-primary)]"
          aria-hidden
        />
      ) : null}
      <span className="min-w-0">
        <span className="block truncate text-[12.5px] font-medium">{label}</span>
        <span className="mt-0.5 block text-[11px] text-[var(--color-muted-foreground-2)]">
          {countLabel}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-1">
        {failed > 0 ? <Badge tone="red">{failed}</Badge> : null}
        {pending > 0 ? <Badge tone="orange">{pending}</Badge> : null}
      </span>
    </button>
  )
}
