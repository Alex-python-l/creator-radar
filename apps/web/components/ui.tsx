'use client'

// 统一 UI 原子件(2026-07-18 起 radar 与 studio 共用一套)。**以 radar 为主**:
// - Button 默认 variant='primary'、disabled 用 opacity、无按压缩放——radar
//   的原始约定;studio 调用点显式传 variant 适配。
// - Card/Panel 阴影走 --shadow-card* token,取值即 Tailwind shadow-sm/md/lg
//   (radar 原生观感),见 app/studio.css。
// - Modal/Toast/Drawer 的文案走 studio.common.*——公众页外层 provider 只带
//   studio.shell 子集,radar 页面如需引入这三件,先补词条供给再用。
import {
  type ReactNode,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type TdHTMLAttributes,
  type ThHTMLAttributes,
  type MouseEventHandler,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { createPortal } from 'react-dom'


import { useStudioT } from '@/lib/studio/i18n'
import { cn } from '@/lib/utils'

/* ─────────────────────── Button ─────────────────────── */
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
}

const variantClass: Record<ButtonVariant, string> = {
  primary:
    'bg-[var(--color-primary)] text-[var(--color-primary-foreground)] hover:opacity-90',
  secondary:
    'bg-white/75 border border-[var(--color-border)] text-[var(--color-foreground)] hover:bg-[var(--color-muted)]',
  ghost:
    'text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)] hover:text-[var(--color-foreground)]',
  danger:
    'border border-[hsl(0_84%_85%)] bg-transparent text-[var(--color-destructive)] hover:bg-[var(--color-destructive-bg)]',
}

const sizeClass = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-9 px-4 text-sm',
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cn(
        // radar 原始基类:cursor-pointer 显式声明(radar 页面没有
        // [data-studio] 的全局 button 光标规则),disabled 用透明度。
        'inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-md font-semibold',
        'transition disabled:cursor-not-allowed disabled:opacity-50',
        sizeClass[size],
        variantClass[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

/* ─────────────────────── Badge (status pill) ─────────────────────── */
type BadgeTone = 'blue' | 'green' | 'red' | 'orange' | 'yellow' | 'gray'
const badgeTone: Record<BadgeTone, { bg: string; fg: string }> = {
  blue: { bg: 'bg-[var(--color-blue-bg)]', fg: 'text-[var(--color-blue)]' },
  green: { bg: 'bg-[var(--color-green-bg)]', fg: 'text-[var(--color-green)]' },
  red: { bg: 'bg-[var(--color-destructive-bg)]', fg: 'text-[var(--color-destructive)]' },
  orange: { bg: 'bg-[var(--color-orange-bg)]', fg: 'text-[var(--color-orange)]' },
  yellow: { bg: 'bg-[var(--color-yellow-bg)]', fg: 'text-[var(--color-yellow)]' },
  gray: { bg: 'bg-[var(--color-gray-bg)]', fg: 'text-[var(--color-muted-foreground)]' },
}

export function Badge({
  tone = 'gray',
  children,
  className,
}: {
  tone?: BadgeTone
  children: ReactNode
  className?: string
}) {
  const t = badgeTone[tone]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium',
        t.bg,
        t.fg,
        className,
      )}
    >
      {children}
    </span>
  )
}

/* ─────────────────────── Cards & section headings ─────────────────────── */
export function Card({
  children,
  className,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'bg-white/[0.82] border border-[var(--color-border)] rounded-[var(--radius-card)] backdrop-blur-[2px]',
        'shadow-[var(--shadow-card-sm)]',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
}

export function PageTitle({
  title,
  subtitle,
  right,
}: {
  title: string
  subtitle?: ReactNode
  right?: ReactNode
}) {
  return (
    <header className="mb-6 flex items-end justify-between gap-4">
      <div>
        <h1 className="text-[28px] font-semibold text-[var(--color-foreground)] [font-family:var(--font-serif)]">
          {title}
        </h1>
        {subtitle ? (
          <p className="mt-1.5 text-xs text-[var(--color-muted-foreground)]">{subtitle}</p>
        ) : null}
      </div>
      {right ? <div className="flex items-center gap-2">{right}</div> : null}
    </header>
  )
}

export function SectionTitle({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <h2
      className={cn(
        'text-sm font-semibold text-[var(--color-foreground)] mb-2.5 px-1',
        className,
      )}
    >
      {children}
    </h2>
  )
}

/* ─────────────────────── Console layout primitives ─────────────────────── */
export function PageHeader({
  eyebrow,
  title,
  description,
  meta,
  actions,
  className,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <header
      className={cn(
        'flex flex-col gap-3 mb-6',
        'md:flex-row md:items-end md:justify-between',
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow ? (
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-normal text-[var(--color-primary)]">
            {eyebrow}
          </div>
        ) : null}
        <h1 className="text-[28px] font-semibold text-[var(--color-foreground)] [font-family:var(--font-serif)]">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 text-xs leading-relaxed text-[var(--color-muted-foreground)]">
            {description}
          </p>
        ) : null}
        {meta ? <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div> : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-2 md:justify-end">{actions}</div>
      ) : null}
    </header>
  )
}

export function Panel({
  title,
  description,
  right,
  children,
  className,
  bodyClassName,
  accent = false,
}: {
  title?: ReactNode
  description?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  accent?: boolean
}) {
  return (
    <section
      className={cn(
        'relative overflow-hidden bg-white/[0.82] border border-[var(--color-border)] backdrop-blur-[2px]',
        'rounded-[var(--radius-card)] shadow-[var(--shadow-card-sm)]',
        accent && 'before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[3px] before:bg-[var(--color-primary)]',
        className,
      )}
    >
      {title || description || right ? (
        <div className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] bg-[#faf7f3]/80 px-4 py-3">
          <div className="min-w-0">
            {title ? (
              <h2 className="text-sm font-semibold text-[var(--color-foreground)] truncate">
                {title}
              </h2>
            ) : null}
            {description ? (
              <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">{description}</p>
            ) : null}
          </div>
          {right ? <div className="shrink-0">{right}</div> : null}
        </div>
      ) : null}
      <div className={cn('p-4', bodyClassName)}>{children}</div>
    </section>
  )
}

export function Toolbar({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-2 rounded-[var(--radius-card)] border border-[var(--color-border)]',
        'bg-white/[0.78] px-4 py-2.5 shadow-[var(--shadow-card-sm)] backdrop-blur-[2px]',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function FilterBar({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-2 rounded-[var(--radius-card)]',
        'border border-[var(--color-border)] bg-[var(--color-sidebar)] px-4 py-2.5',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  className,
}: {
  value: T
  options: Array<{ value: T; label: ReactNode; count?: number }>
  onChange: (value: T) => void
  ariaLabel?: string
  className?: string
}) {
  return (
    <div
      className={cn(
        'inline-flex min-h-9 items-center rounded-md border border-[var(--color-border)] bg-[var(--color-gray-bg)] p-[2px]',
        className,
      )}
      role="group"
      aria-label={ariaLabel}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            aria-pressed={active}
            className={cn(
              'inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium',
              'transition-colors whitespace-nowrap',
              active
                ? 'bg-[var(--color-card)] text-[var(--color-foreground)] shadow-[0_1px_1px_rgba(15,23,42,0.08)]'
                : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
            )}
          >
            <span>{option.label}</span>
            {option.count !== undefined ? (
              <span className="text-xs tabular-nums text-[var(--color-muted-foreground-2)]">
                {option.count}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

export function MetricCard({
  label,
  value,
  description,
  tone = 'gray',
  icon,
  onClick,
}: {
  label: ReactNode
  value: ReactNode
  description?: ReactNode
  tone?: 'blue' | 'green' | 'orange' | 'red' | 'gray'
  icon?: ReactNode
  onClick?: () => void
}) {
  const toneClass = {
    blue: 'border-l-[var(--color-blue)]',
    green: 'border-l-[var(--color-green)]',
    orange: 'border-l-[var(--color-orange)]',
    red: 'border-l-[var(--color-destructive)]',
    gray: 'border-l-[var(--color-border-strong)]',
  }[tone]
  const Wrapper: 'button' | 'div' = onClick ? 'button' : 'div'
  return (
    <Wrapper
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cn(
        'min-h-[104px] rounded-[var(--radius-card)] border border-l-[3px] border-[var(--color-border)]',
        'bg-white/[0.82] p-4 text-left shadow-[var(--shadow-card-sm)] backdrop-blur-[2px]',
        'flex flex-col justify-between',
        toneClass,
        onClick && 'transition-colors hover:bg-[var(--color-muted)]',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="text-[12px] font-medium text-[var(--color-muted-foreground)]">{label}</div>
        {icon ? <div className="text-[var(--color-muted-foreground-2)]">{icon}</div> : null}
      </div>
      <div>
        <div className="text-[30px] leading-none font-semibold tabular-nums text-[var(--color-foreground)]">
          {value}
        </div>
        {description ? (
          <div className="mt-2 text-[12px] leading-snug text-[var(--color-muted-foreground)]">
            {description}
          </div>
        ) : null}
      </div>
    </Wrapper>
  )
}

export function DataTableShell({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'overflow-x-auto rounded-[var(--radius-card)] border border-[var(--color-border)]',
        'bg-[var(--color-card)] shadow-[var(--shadow-card-sm)]',
        className,
      )}
    >
      {children}
    </div>
  )
}

/* ─────────────────────── StatCard ─────────────────────── */
export function StatCard({
  label,
  value,
  caption,
  tone,
  onClick,
}: {
  label: string
  value: number | string
  caption?: ReactNode
  tone?: 'blue' | 'green' | 'orange' | 'red' | 'gray'
  onClick?: () => void
}) {
  const valueColor =
    tone === 'blue'
      ? 'text-[var(--color-blue)]'
      : tone === 'green'
        ? 'text-[var(--color-green)]'
        : tone === 'orange'
          ? 'text-[var(--color-orange)]'
          : tone === 'red'
            ? 'text-[var(--color-destructive)]'
            : 'text-[var(--color-foreground)]'
  const interactive = onClick != null
  const Wrapper: 'button' | 'div' = interactive ? 'button' : 'div'
  return (
    <Wrapper
      onClick={onClick}
      type={interactive ? 'button' : undefined}
      className={cn(
        // Mirror the Card primitive so the non-interactive case is
        // visually identical to the previous `<Card>`-wrapped StatCard.
        'bg-[var(--color-card)] border border-[var(--color-border)] rounded-[var(--radius-card)]',
        'shadow-[var(--shadow-card-sm)]',
        'px-5 py-4 min-w-[170px] flex-1',
        'text-left',
        interactive &&
          'cursor-pointer transition-colors hover:bg-[var(--color-muted)]',
      )}
    >
      <div className="text-[12.5px] text-[var(--color-muted-foreground)] font-medium">{label}</div>
      <div
        className={cn(
          'mt-2.5 text-[34px] leading-none font-semibold tracking-[-0.02em] tabular-nums',
          valueColor,
        )}
      >
        {value}
      </div>
      {caption ? (
        <div className="mt-2 text-[12.5px] text-[var(--color-muted-foreground)]">{caption}</div>
      ) : null}
    </Wrapper>
  )
}

/* ─────────────────────── Inputs ─────────────────────── */
export function TextField({
  label,
  hint,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label?: string; hint?: string }) {
  return (
    <label className="block">
      {label ? (
        <span className="block text-xs font-medium text-[var(--color-foreground-2)] mb-1.5">
          {label}
        </span>
      ) : null}
      <input
        {...rest}
        className={cn(
          'w-full bg-[var(--color-card)] border border-[var(--color-input)]',
          'h-9 rounded-md px-3 text-sm',
          'text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground-3)]',
          'focus:border-[var(--color-ring)] focus:ring-2 focus:ring-[var(--color-ring)]/20',
          'transition-shadow',
          rest.className,
        )}
      />
      {hint ? (
        <span className="block mt-1.5 text-xs text-[var(--color-muted-foreground)]">{hint}</span>
      ) : null}
    </label>
  )
}

/* ─────────────────────── Toggle (iOS-style switch) ─────────────────────── */
export function Toggle({
  checked,
  onChange,
  ariaLabel,
}: {
  checked: boolean
  onChange: () => void
  ariaLabel?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={onChange}
      className={cn(
        'relative inline-flex h-6 w-10 items-center rounded-full transition-colors',
        checked ? 'bg-[var(--color-green)]' : 'bg-[#d2d2d7]',
      )}
    >
      <span
        className={cn(
          'inline-block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.18)]',
          'transition-transform',
          checked ? 'translate-x-[18px]' : 'translate-x-[2px]',
        )}
      />
    </button>
  )
}

/* ─────────────────────── Progress bar (slim, rounded) ─────────────────────── */
export function ProgressBar({
  value,
  total,
  tone = 'blue',
}: {
  value: number
  total: number
  tone?: 'blue' | 'green'
}) {
  const pct = total > 0 ? Math.min(100, Math.max(0, (value / total) * 100)) : 0
  const fill = tone === 'green' ? 'bg-[var(--color-green)]' : 'bg-[var(--color-primary)]'
  return (
    <div className="flex items-center gap-2.5 min-w-[150px]">
      <div className="relative flex-1 h-2 rounded-full bg-[var(--color-gray-bg)] overflow-hidden">
        <div
          className={cn('absolute inset-y-0 left-0 rounded-full transition-[width]', fill)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-[var(--color-muted-foreground)] whitespace-nowrap">
        {value}/{total}
      </span>
    </div>
  )
}

/* ─────────────────────── Table ─────────────────────── */
export function Table({ children }: { children: ReactNode }) {
  return (
    <Card className="overflow-x-auto p-0">
      <table className="w-full border-collapse">{children}</table>
    </Card>
  )
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="border-b border-[var(--color-border)]">{children}</thead>
}

export function Th({
  children,
  align = 'left',
  className,
  ...rest
}: ThHTMLAttributes<HTMLTableCellElement> & {
  align?: 'left' | 'right' | 'center'
}) {
  return (
    <th
      scope="col"
      className={cn(
        'text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)] py-2.5 px-4',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        align === 'left' && 'text-left',
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  )
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody>{children}</tbody>
}

export function Tr({
  children,
  className,
  onClick,
}: {
  children: ReactNode
  className?: string
  onClick?: MouseEventHandler<HTMLTableRowElement>
}) {
  return (
    <tr
      className={cn(
        'border-b border-[var(--color-border)] last:border-b-0',
        'hover:bg-[var(--color-muted)] transition-colors',
        className,
      )}
      onClick={onClick}
    >
      {children}
    </tr>
  )
}

export function Td({
  children,
  align = 'left',
  className,
  ...rest
}: TdHTMLAttributes<HTMLTableCellElement> & {
  align?: 'left' | 'right' | 'center'
}) {
  return (
    <td
      className={cn(
        'px-4 py-3 align-middle text-sm text-[var(--color-foreground)]',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        className,
      )}
      {...rest}
    >
      {children}
    </td>
  )
}

/* ─────────────────────── Modal (sheet style) ─────────────────────── */
export function Modal({
  open,
  onClose,
  title,
  children,
  onSubmit,
  submitLabel,
  submitTone = 'primary',
  submitDisabled = false,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  onSubmit?: (e: FormEvent<HTMLFormElement>) => void
  submitLabel?: string
  submitTone?: ButtonVariant
  submitDisabled?: boolean
}) {
  const t = useStudioT()
  const ref = useRef<HTMLDialogElement | null>(null)
  useEffect(() => {
    const dlg = ref.current
    if (!dlg) return
    if (open && !dlg.open) dlg.showModal()
    if (!open && dlg.open) dlg.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
      className="bg-transparent p-0 m-auto"
    >
      <form
        method="dialog"
        onSubmit={(e) => {
          if (onSubmit) onSubmit(e)
        }}
        className="w-[440px] max-w-[92vw] bg-[var(--color-card)] rounded-[var(--radius-card)] shadow-[0_24px_48px_rgba(0,0,0,0.18)] border border-[var(--color-border)] overflow-hidden"
      >
        <div className="px-6 py-5 border-b border-[var(--color-border)] flex items-center justify-between">
          <div className="text-[15px] font-bold text-[var(--color-foreground)]">{title}</div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 rounded-full hover:bg-[var(--color-gray-bg)] text-[var(--color-muted-foreground)] flex items-center justify-center"
            aria-label={t('common.close')}
          >
            ✕
          </button>
        </div>
        <div className="p-6">{children}</div>
        {onSubmit ? (
          <div className="px-6 py-4 border-t border-[var(--color-border)] flex justify-end gap-2 bg-[var(--color-muted)]">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" variant={submitTone} disabled={submitDisabled}>
              {submitLabel ?? t('common.confirm')}
            </Button>
          </div>
        ) : null}
      </form>
    </dialog>
  )
}

/* ─────────────────────── EmptyState ─────────────────────── */
export function EmptyState({
  title,
  children,
  className,
}: {
  title?: string
  children?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('px-6 py-12 text-center', className)}>
      {title ? (
        <div className="text-sm font-bold text-[var(--color-foreground-2)]">{title}</div>
      ) : null}
      {children ? (
        <div className="mt-1 text-xs text-[var(--color-muted-foreground)]">{children}</div>
      ) : null}
    </div>
  )
}

/* ─────────────────────── Toast ─────────────────────── */
export function Toast({
  message,
  tone = 'blue',
  onDismiss,
}: {
  message: string
  tone?: BadgeTone
  onDismiss: () => void
}) {
  const trans = useStudioT()
  useEffect(() => {
    const timer = setTimeout(onDismiss, 3600)
    return () => clearTimeout(timer)
  }, [onDismiss])
  const tn = badgeTone[tone]
  return (
    <div className="fixed bottom-6 right-6 z-50 fade-up">
      <div
        className={cn(
          'flex items-center gap-3 px-4 py-3 rounded-[var(--radius-card)] shadow-[0_8px_24px_rgba(0,0,0,0.12)]',
          'bg-[var(--color-card)] border border-[var(--color-border)]',
        )}
      >
        <span className={cn('inline-block w-2 h-2 rounded-full', tn.bg, tn.fg.replace('text-', 'bg-'))} />
        <span className="text-sm text-[var(--color-foreground)]">{message}</span>
        <button
          onClick={onDismiss}
          className="text-[var(--color-muted-foreground-2)] hover:text-[var(--color-foreground-2)] ml-1 text-xs"
          aria-label={trans('common.dismiss')}
        >
          ✕
        </button>
      </div>
    </div>
  )
}

/* ─────────────────────── Dot (small status indicator) ─────────────────────── */
// `size` keys map to Tailwind w-/h- utilities. Listed inline so PurgeCSS
// keeps them — class names assembled at runtime would otherwise be dropped.
const DOT_SIZE: Record<'xs' | 'sm' | 'md' | 'lg', string> = {
  xs: 'w-1.5 h-1.5',  // 6px — legacy default, used in subtle inline contexts
  sm: 'w-2 h-2',      // 8px
  md: 'w-2.5 h-2.5',  // 10px — table-row status dots
  lg: 'w-3 h-3',      // 12px
}
export function Dot({
  tone = 'green',
  size = 'xs',
}: {
  tone?: 'green' | 'orange' | 'red' | 'gray' | 'blue'
  size?: 'xs' | 'sm' | 'md' | 'lg'
}) {
  const c = {
    green: 'bg-[var(--color-green)]',
    orange: 'bg-[var(--color-orange)]',
    red: 'bg-[var(--color-destructive)]',
    gray: 'bg-[var(--color-muted-foreground-3)]',
    blue: 'bg-[var(--color-blue)]',
  }[tone]
  return <span className={cn('inline-block rounded-full', DOT_SIZE[size], c)} />
}

/* ─────────────────────── Drawer (right-side slide-in) ───────────────────────
 *
 * Rewritten from scratch. Earlier versions layered <dialog>.showModal() with
 * Tailwind's translate-x utilities and CSS transitions — that combination had
 * subtle frame-alignment bugs that read as a "bounce" on open.
 *
 * The new model is the simplest thing that works:
 *   • React portal into <body> — no top-layer quirks, full control over stacking
 *   • CSS @keyframes (not transition) with `fill-mode: both` — the `from`
 *     state is applied the instant the panel mounts, so the very first
 *     painted frame is already at translateX(100%). No "where do we start"
 *     race conditions.
 *   • React mount/unmount drives the animation; an internal `mounted`
 *     latch keeps the panel in the tree long enough for the exit animation
 *     to finish, then unmounts cleanly.
 *   • ESC + body scroll lock are wired manually to replace what <dialog>
 *     used to give us for free.
 */
export const DRAWER_EXIT_MS = 200

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
}: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
}) {
  const t = useStudioT()
  // `mounted` lags `open` by the exit-animation duration on close.
  const [mounted, setMounted] = useState(open)
  const panelRef = useRef<HTMLDivElement | null>(null)
  // The element focused at the moment the Drawer opened — restored on close.
  const triggerRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (open) {
      const raf = requestAnimationFrame(() => setMounted(true))
      return () => cancelAnimationFrame(raf)
    }
    if (!mounted) return
    const timer = setTimeout(() => setMounted(false), DRAWER_EXIT_MS)
    return () => clearTimeout(timer)
  }, [open, mounted])

  // Focus management: remember the previously focused element on open,
  // focus the first focusable in the panel, and restore on close.
  useEffect(() => {
    if (!open) return
    triggerRef.current = (document.activeElement as HTMLElement) ?? null
    // Wait two frames: the portal mounts on the next paint, and the
    // browser may still be resolving the click that opened the Drawer
    // (which moves focus back to <body>). Double-rAF puts us safely
    // after both, so our focus() call wins.
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        const panel = panelRef.current
        if (!panel) return
        const first = panel.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)
        ;(first ?? panel).focus()
      })
    })
    return () => {
      cancelAnimationFrame(raf1)
      if (raf2) cancelAnimationFrame(raf2)
      const trigger = triggerRef.current
      if (trigger && typeof trigger.focus === 'function') trigger.focus()
    }
  }, [open])

  useEffect(() => {
    if (!mounted) return
    const original = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = original
    }
  }, [mounted])

  // Keyboard handling. ESC closes; Tab traps focus inside the panel.
  // Listener is on window (not the panel) so portal/event-delegation quirks
  // don't drop it, but every branch guards on target to avoid:
  //   (a) double-closing with a native <dialog> Modal layered on top —
  //       Modals are <dialog>s; their ESC is handled by the UA in the top
  //       layer, but the event still bubbles to window. We skip if the
  //       active element / target sits inside an open <dialog>.
  //   (b) collapsing the Drawer when ESC dismisses an in-panel <select>
  //       popup — browsers fire keydown on the select itself.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const panel = panelRef.current
      // Skip if ESC is from a layered <dialog> Modal (top layer).
      if (target && target.closest('dialog')) return
      // Skip if the event is from outside our panel (some other widget).
      if (panel && target && !panel.contains(target)) return
      if (e.key === 'Escape') {
        if (target && (target.tagName === 'SELECT' || target.tagName === 'OPTION')) return
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key === 'Tab' && panel) {
        const focusables = Array.from(
          panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
        ).filter((el) => !el.hasAttribute('disabled'))
        if (focusables.length === 0) {
          e.preventDefault()
          panel.focus()
          return
        }
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        const active = document.activeElement as HTMLElement | null
        if (e.shiftKey && (active === first || !panel.contains(active))) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!mounted) return null

  return createPortal(
    // portal 挂 document.body,脱离 studio layout 的 [data-studio] 子树——
    // 这里自带作用域属性,元素级样式(a 色/焦点/光标)才会生效。
    <div data-studio className="fixed inset-0 z-[1000]">
      <div
        onClick={onClose}
        aria-hidden="true"
        className={cn(
          'absolute inset-0 bg-[rgba(0,0,0,0.34)] backdrop-blur-[4px]',
          open ? 'drawer-backdrop-in' : 'drawer-backdrop-out',
        )}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          'absolute top-0 right-0 bottom-0',
          'flex flex-col w-[480px] max-w-[100vw] h-full',
          'bg-[var(--color-card)] border-l border-[var(--color-border)]',
          'shadow-[-12px_0_32px_rgba(0,0,0,0.12)]',
          'outline-none',
          open ? 'drawer-panel-in' : 'drawer-panel-out',
        )}
      >
        <div className="px-6 py-5 border-b border-[var(--color-border)] flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[15px] font-bold text-[var(--color-foreground)] truncate">
              {title}
            </div>
            {subtitle ? (
              <div className="mt-0.5 text-xs text-[var(--color-muted-foreground)] truncate">
                {subtitle}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 rounded-full hover:bg-[var(--color-gray-bg)] text-[var(--color-muted-foreground)] flex items-center justify-center flex-shrink-0"
            aria-label={t('common.close')}
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-6">{children}</div>
        {footer ? (
          <div className="px-6 py-4 border-t border-[var(--color-border)] bg-[var(--color-muted)]">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}

/* ─────────────────────── Input / Select(原 radar 原子件) ─────────────────────── */
export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'h-9 rounded-md border border-[color:var(--color-input)] bg-[color:var(--color-background)] px-3 text-sm',
        'focus:outline-none focus:ring-2 focus:ring-[color:var(--color-ring)]/20 focus:border-[color:var(--color-ring)]',
        className,
      )}
      {...rest}
    />
  )
}

export function Select({ className, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        'h-9 rounded-md border border-[color:var(--color-input)] bg-[color:var(--color-background)] px-3 text-sm',
        'focus:outline-none focus:ring-2 focus:ring-[color:var(--color-ring)]/20 focus:border-[color:var(--color-ring)]',
        className,
      )}
      {...rest}
    />
  )
}
