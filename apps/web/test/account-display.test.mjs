import assert from 'node:assert/strict';
import { test } from 'node:test';

const { formatAccountDisplayName } = await import('../lib/account-display.ts');

test('account display: displayName wins for facebook internal identifiers', () => {
  assert.equal(
    formatAccountDisplayName({
      platform: 'facebook',
      username: 'profile.php?id=61590123456789',
      displayName: 'Jeannie Study',
    }),
    'Jeannie Study',
  );
});

test('account display: facebook people path becomes a readable name', () => {
  assert.equal(
    formatAccountDisplayName({
      platform: 'facebook',
      username: 'people/Jeannie-Study/pfbidOLD',
      displayName: null,
    }),
    'Jeannie Study',
  );
});

test('account display: facebook numeric ids are labelled instead of shown as @username', () => {
  assert.equal(
    formatAccountDisplayName({
      platform: 'facebook',
      username: 'profile.php?id=61590123456789',
      displayName: null,
    }),
    'Facebook ID 61590123456789',
  );
  assert.equal(
    formatAccountDisplayName({
      platform: 'facebook',
      username: '61590123456789',
      displayName: null,
    }),
    'Facebook ID 61590123456789',
  );
});

test('account display: facebook vanity usernames stay readable without @ prefix', () => {
  assert.equal(
    formatAccountDisplayName({
      platform: 'facebook',
      username: 'Cristiano',
      displayName: null,
    }),
    'Cristiano',
  );
});

test('account display: unresolved facebook share links get a readable fallback', () => {
  assert.equal(
    formatAccountDisplayName({
      platform: 'facebook',
      username: '',
      rawUrl: 'https://www.facebook.com/share/1CTxoxSKCJ/?mibextid=wwXIfr',
      displayName: null,
    }),
    'Facebook Link 1CTxoxSKCJ',
  );
});

test('account display: non-facebook accounts keep familiar @handle labels', () => {
  assert.equal(
    formatAccountDisplayName({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: null,
    }),
    '@khaby.lame',
  );
});

const { formatAccountListLabel } = await import('../lib/account-display.ts');

test('list label: alias wins over displayName and username', () => {
  assert.equal(
    formatAccountListLabel({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: 'Khaby Lame',
      alias: '水滴 7 号',
    }),
    '水滴 7 号',
  );
});

test('list label: alias trims whitespace, blank alias falls back to displayName', () => {
  assert.equal(
    formatAccountListLabel({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: 'Khaby Lame',
      alias: '   ',
    }),
    'Khaby Lame',
  );
});

test('list label: null alias falls back to displayName', () => {
  assert.equal(
    formatAccountListLabel({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: 'Khaby Lame',
      alias: null,
    }),
    'Khaby Lame',
  );
});

test('list label: missing alias and displayName falls back to @username', () => {
  assert.equal(
    formatAccountListLabel({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: null,
      alias: null,
    }),
    '@khaby.lame',
  );
});

test('list label: facebook special cases still apply when alias absent', () => {
  assert.equal(
    formatAccountListLabel({
      platform: 'facebook',
      username: 'profile.php?id=61590123456789',
      displayName: null,
      alias: null,
    }),
    'Facebook ID 61590123456789',
  );
});

const { formatAccountListTitle } = await import('../lib/account-display.ts');

test('list title: three-segment join with alias / displayName / rawIdentifier', () => {
  assert.equal(
    formatAccountListTitle({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: 'Khaby Lame',
      alias: '水滴 7 号',
    }),
    '水滴 7 号 · Khaby Lame · @khaby.lame',
  );
});

test('list title: skips missing alias and displayName, keeps rawIdentifier', () => {
  assert.equal(
    formatAccountListTitle({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: null,
      alias: null,
    }),
    '@khaby.lame',
  );
});

test('list title: dedupes when alias equals displayName', () => {
  assert.equal(
    formatAccountListTitle({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: 'Khaby Lame',
      alias: 'Khaby Lame',
    }),
    'Khaby Lame · @khaby.lame',
  );
});

test('list title: blank alias is skipped', () => {
  assert.equal(
    formatAccountListTitle({
      platform: 'tiktok',
      username: 'khaby.lame',
      displayName: 'Khaby Lame',
      alias: '   ',
    }),
    'Khaby Lame · @khaby.lame',
  );
});
