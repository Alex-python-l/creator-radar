// Backend stores datetimes as naive UTC (datetime.utcnow()) and FastAPI/Pydantic
// serializes them without a timezone suffix, e.g. "2026-05-28T16:51:49.314669".
// JS's Date constructor treats such strings as local time, which silently shifts
// every rendered timestamp by the user's UTC offset. Normalize to UTC here.
export function parseBackendIso(iso: string): Date | null {
  if (!iso) return null
  // If the string already carries a timezone (Z or ±HH:MM), trust it.
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso)
  const t = Date.parse(hasTz ? iso : iso + 'Z')
  return Number.isFinite(t) ? new Date(t) : null
}

export function formatLocalDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = parseBackendIso(iso)
  if (!d) return iso
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatRelative(
  iso: string | null,
  locale?: string,
  now: Date | number = Date.now(),
): string {
  if (!iso) return '—'
  const d = parseBackendIso(iso)
  if (!d) return iso
  const nowMs = now instanceof Date ? now.getTime() : now
  const diffMs = nowMs - d.getTime()
  const abs = Math.abs(diffMs)
  // Pick the largest unit that gives a number >= 1, so we say "2 hours ago"
  // instead of "120 minutes ago".
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 365 * 24 * 3600 * 1000],
    ['month', 30 * 24 * 3600 * 1000],
    ['day', 24 * 3600 * 1000],
    ['hour', 3600 * 1000],
    ['minute', 60 * 1000],
    ['second', 1000],
  ]
  try {
    const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'always' })
    for (const [unit, ms] of units) {
      if (abs >= ms || unit === 'second') {
        return rtf.format(-Math.round(diffMs / ms), unit)
      }
    }
  } catch {
    // ignore
  }
  return d.toLocaleString()
}
