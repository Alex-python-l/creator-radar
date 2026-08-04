import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

test('gone accounts page is linked from nav and filters by latest account_gone log', async () => {
  // 统一侧栏(2026-07-17)后导航项在 app-sidebar-nav.tsx,不再在 layout.tsx
  const [navSrc, querySrc, zhRaw, enRaw] = await Promise.all([
    readFile(new URL('../components/app-sidebar-nav.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../lib/queries/influencers.ts', import.meta.url), 'utf8'),
    readFile(new URL('../messages/zh.json', import.meta.url), 'utf8'),
    readFile(new URL('../messages/en.json', import.meta.url), 'utf8'),
  ]);
  const zh = JSON.parse(zhRaw);
  const en = JSON.parse(enRaw);

  assert.equal(zh.layout.goneAccounts, '失效账号');
  assert.equal(en.layout.goneAccounts, 'Gone accounts');
  assert.equal(zh.goneAccountsPage.title, '失效账号');
  assert.equal(en.goneAccountsPage.title, 'Gone accounts');

  assert.match(navSrc, /`\/\$\{locale\}\/gone-accounts`/);
  assert.match(navSrc, /tLayout\('goneAccounts'\)/);

  assert.match(querySrc, /export async function listGoneAccounts\(/);
  assert.match(querySrc, /lf\.error_code\s*=\s*'account_gone'/);
  assert.match(querySrc, /ORDER BY COALESCE\(lf\.finished_at,\s*lf\.started_at\) DESC/);
});
