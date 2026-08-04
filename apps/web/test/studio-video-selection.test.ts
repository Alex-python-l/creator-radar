import assert from 'node:assert/strict'
import test from 'node:test'

import { computeNextSelection } from '../lib/studio/videoSelection.ts'

type Item = { id: number; selectable: boolean }
const sel = (item: Item) => item.selectable

const ITEMS: Item[] = [
  { id: 1, selectable: true },
  { id: 2, selectable: true },
  { id: 3, selectable: false }, // 已发布
  { id: 4, selectable: true },
  { id: 5, selectable: true },
  { id: 6, selectable: false },
  { id: 7, selectable: true },
]

const M = { shift: false, meta: false }
const SHIFT = { shift: true, meta: false }

test('toggle selects an unselected selectable row and sets anchor', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 2, null, M, new Set(), sel,
  )
  assert.deepEqual([...selected], [2])
  assert.equal(nextAnchorId, 2)
})

test('toggle removes an already-selected row', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 2, null, M, new Set([2, 4]), sel,
  )
  assert.deepEqual([...selected].sort(), [4])
  assert.equal(nextAnchorId, 2)
})

test('shift+click with no anchor degrades to toggle', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 5, null, SHIFT, new Set(), sel,
  )
  assert.deepEqual([...selected], [5])
  assert.equal(nextAnchorId, 5)
})

test('shift+click adds inclusive range of selectable rows', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 5, 1, SHIFT, new Set(), sel,
  )
  // 1..5: ids 1,2,4,5 selectable; id 3 skipped
  assert.deepEqual([...selected].sort((a, b) => a - b), [1, 2, 4, 5])
  // anchor unchanged
  assert.equal(nextAnchorId, 1)
})

test('shift+click skips non-selectable rows in range', () => {
  const { selected } = computeNextSelection(
    ITEMS, 7, 3, SHIFT, new Set(), sel,
  )
  // 3..7: ids 3(skip),4,5,6(skip),7
  assert.deepEqual([...selected].sort((a, b) => a - b), [4, 5, 7])
})

test('shift+click works when clicked is above anchor', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 1, 5, SHIFT, new Set(), sel,
  )
  assert.deepEqual([...selected].sort((a, b) => a - b), [1, 2, 4, 5])
  assert.equal(nextAnchorId, 5)
})

test('shift+click is additive: keeps prior selection outside range', () => {
  const { selected } = computeNextSelection(
    ITEMS, 5, 4, SHIFT, new Set([1, 7]), sel,
  )
  // prior 1,7 kept; range 4..5 adds 4,5
  assert.deepEqual([...selected].sort((a, b) => a - b), [1, 4, 5, 7])
})

test('toggle on non-selectable row leaves selection but updates anchor', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 3, null, M, new Set([2]), sel,
  )
  assert.deepEqual([...selected].sort((a, b) => a - b), [2])
  assert.equal(nextAnchorId, 3)
})

test('shift degrades to toggle when anchor not in items', () => {
  const { selected, nextAnchorId } = computeNextSelection(
    ITEMS, 4, 999, SHIFT, new Set(), sel,
  )
  assert.deepEqual([...selected], [4])
  assert.equal(nextAnchorId, 4)
})
