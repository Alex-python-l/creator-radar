import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatLocalDateTime,
  formatRelative,
  parseBackendIso,
} from '../lib/studio/time.ts'

test('formatRelative accepts a fixed clock and locale', () => {
  assert.equal(
    formatRelative(
      '2020-01-01T00:00:00',
      'en',
      new Date('2020-01-01T02:00:00Z'),
    ),
    '2 hours ago',
  )
  assert.equal(
    formatRelative(
      '2020-01-01T00:00:00',
      'zh-CN',
      new Date('2020-01-03T00:00:00Z'),
    ),
    '2天前',
  )
})

test('formatRelative keeps empty values and invalid values stable', () => {
  assert.equal(formatRelative(null, 'en'), '—')
  assert.equal(formatRelative('not-a-date', 'en'), 'not-a-date')
})

test('formatLocalDateTime still exposes a precise absolute timestamp', () => {
  assert.notEqual(formatLocalDateTime('2020-01-01T00:00:00'), '—')
})

test('parseBackendIso treats timezone-less backend values as UTC', () => {
  assert.equal(
    parseBackendIso('2026-06-17T06:30:00')?.toISOString(),
    '2026-06-17T06:30:00.000Z',
  )
})
