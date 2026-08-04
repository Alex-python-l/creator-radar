import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const { applyAccountExpandParam, toggleAccountExpandSet } = await import('../lib/url-params.ts');

test('account expand toggle can update the current URL without dropping other state', () => {
  const current = new URLSearchParams(
    'platform=youtube&range=30d&group=3&period=day&chartRange=all',
  );
  const expanded = toggleAccountExpandSet(new Set([9]), 10);

  assert.deepEqual([...expanded].sort((a, b) => a - b), [9, 10]);
  assert.equal(
    applyAccountExpandParam(current, expanded),
    '?platform=youtube&range=30d&group=3&period=day&chartRange=all&expand=9%2C10',
  );
});

test('account expand control is driven by a client click handler, not a stale precomputed href', async () => {
  const src = await readFile(
    new URL('../components/accounts-table-form.tsx', import.meta.url),
    'utf8',
  );

  assert.match(src, /onToggleExpand/, 'row expand control should use a click handler');
  assert.doesNotMatch(src, /const toggleHref =/, 'do not precompute expand href from stale parsed props');
  assert.doesNotMatch(src, /href=\{toggleHref\}/, 'expand control should not be a static Link href');
});
