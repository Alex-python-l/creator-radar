import Link from 'next/link';
import type { Route } from 'next';
import { ChevronDown } from 'lucide-react';

import { cn } from '@/lib/utils';

/**
 * 图表筛选下拉(details/summary 原生交互,无 client JS)。
 * options 的 href 由调用页预构建 —— 组件本身路径无关,monitor / competitors 两页共用。
 */
export function ChartDropdownFilter({
  label,
  currentLabel,
  options,
}: {
  label: string;
  currentLabel: string;
  options: Array<{
    key: string;
    label: string;
    href: Route;
    active: boolean;
  }>;
}) {
  return (
    <details className="relative" name="chart-filter">
      <summary
        className="flex h-8 cursor-pointer list-none items-center gap-1.5 rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] px-3 text-xs font-semibold text-[color:var(--color-foreground)] transition hover:bg-[color:var(--color-muted)] [&::-webkit-details-marker]:hidden"
        aria-label={`${label}: ${currentLabel}`}
      >
        <span className="text-[color:var(--color-muted-foreground)]">{label}</span>
        <span>{currentLabel}</span>
        <ChevronDown className="h-3.5 w-3.5 text-[color:var(--color-muted-foreground)]" aria-hidden />
      </summary>
      <div className="absolute right-0 z-20 mt-2 min-w-32 overflow-hidden rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] shadow-lg">
        {options.map((option) => (
          <Link
            key={option.key}
            href={option.href}
            scroll={false}
            className={cn(
              'flex h-8 items-center px-3 text-xs font-semibold transition',
              option.active
                ? 'bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]'
                : 'text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)]',
            )}
          >
            {option.label}
          </Link>
        ))}
      </div>
    </details>
  );
}
