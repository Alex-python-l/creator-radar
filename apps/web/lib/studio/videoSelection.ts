export type SelectionModifiers = {
  shift: boolean
  meta: boolean
}

export type SelectionResult = {
  selected: Set<number>
  nextAnchorId: number | null
}

export function computeNextSelection<T extends { id: number }>(
  items: readonly T[],
  clickedId: number,
  anchorId: number | null,
  modifiers: SelectionModifiers,
  prevSelected: ReadonlySet<number>,
  isSelectable: (item: T) => boolean,
): SelectionResult {
  if (modifiers.shift && anchorId !== null) {
    const a = items.findIndex((v) => v.id === anchorId)
    const b = items.findIndex((v) => v.id === clickedId)
    if (a >= 0 && b >= 0) {
      const lo = Math.min(a, b)
      const hi = Math.max(a, b)
      const next = new Set(prevSelected)
      for (let i = lo; i <= hi; i++) {
        const v = items[i]
        if (isSelectable(v)) next.add(v.id)
      }
      return { selected: next, nextAnchorId: anchorId }
    }
    // anchor or clicked missing — fall through to toggle
  }
  const next = new Set(prevSelected)
  const clicked = items.find((v) => v.id === clickedId)
  if (clicked && isSelectable(clicked)) {
    if (next.has(clickedId)) next.delete(clickedId)
    else next.add(clickedId)
  }
  return { selected: next, nextAnchorId: clickedId }
}
