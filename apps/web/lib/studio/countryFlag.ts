// ISO 3166-1 alpha-2 → Unicode emoji flag.
//
// Each letter in the country code maps to a Regional Indicator Symbol
// (U+1F1E6..U+1F1FF). Pasting two of them in a row renders as that
// country's flag on every modern OS / browser. Returns an empty string
// for anything that isn't a clean 2-letter code so callers can fall back
// to no-flag rendering without checking validity twice.
export function countryFlag(code: string | null | undefined): string {
  if (!code) return ''
  const cleaned = code.trim().toUpperCase()
  if (cleaned.length !== 2 || !/^[A-Z]{2}$/.test(cleaned)) return ''
  const base = 0x1f1e6 - 'A'.charCodeAt(0)
  return String.fromCodePoint(
    base + cleaned.charCodeAt(0),
    base + cleaned.charCodeAt(1),
  )
}
