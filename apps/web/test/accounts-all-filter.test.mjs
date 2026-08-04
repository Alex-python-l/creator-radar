import assert from 'node:assert/strict';
import { test } from 'node:test';

const {
  buildAccountsQuery,
  parseAccountsSearchParams,
  switchAccountsGroup,
} = await import('../lib/url-params.ts');

test('accounts filters default to all groups and all platforms', () => {
  const parsed = parseAccountsSearchParams({});

  assert.equal(parsed.platform, 'all');
  assert.equal(parsed.group, undefined);
  assert.equal(buildAccountsQuery(parsed, {}), '');
});

test('accounts filters can switch between all and concrete group/platform values', () => {
  const all = parseAccountsSearchParams({});

  assert.equal(
    buildAccountsQuery(all, { group: 3, platform: 'youtube' }),
    '?platform=youtube&group=3',
  );

  const narrowed = parseAccountsSearchParams({ platform: 'youtube', group: '3' });
  assert.equal(buildAccountsQuery(narrowed, { platform: 'all' }), '?group=3');
  assert.equal(switchAccountsGroup(narrowed, undefined), '?platform=youtube');
});
