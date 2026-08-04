import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');

test('account group numbering has persistent state and detail tables', async () => {
  const [schema, migration] = await Promise.all([
    read('../lib/db/schema.ts'),
    read('../drizzle/manual/0008_account_group_numbering.sql'),
  ]);

  assert.match(schema, /primaryKey/);
  assert.match(schema, /foreignKey/);
  assert.match(schema, /accountGroupNumbering\s*=\s*pgTable\(\s*'account_group_numbering'/);
  assert.match(schema, /accountGroupNumbers\s*=\s*pgTable\(\s*'account_group_numbers'/);
  assert.match(schema, /account_group_numbering_group_fk/);
  assert.match(schema, /account_group_numbers_group_fk/);
  assert.match(schema, /account_group_numbers_influencer_fk/);
  assert.match(migration, /CREATE TABLE IF NOT EXISTS account_group_numbering/);
  assert.match(migration, /CREATE TABLE IF NOT EXISTS account_group_numbers/);
  assert.match(migration, /PRIMARY KEY \(group_id, influencer_id\)/);
  assert.match(migration, /CONSTRAINT account_group_numbers_group_fk/);
  assert.match(migration, /ON DELETE CASCADE/);
});

test('account numbering actions expose toggle, detect, list, and regenerate helpers', async () => {
  const src = await read('../lib/actions/account-numbering.ts');

  assert.match(src, /export async function toggleGroupAccountNumbers/);
  assert.match(src, /export async function hasGroupAccountNumbers/);
  assert.match(src, /export async function listNumberedGroupIds/);
  assert.match(src, /export async function regenerateAccountNumbersForGroups/);
  assert.match(src, /accountGroupNumbering/);
  assert.match(src, /formatAccountListLabel/);
  assert.match(src, /compareAccountNumberingLabels/);
  assert.match(src, /db\.transaction/);
});

test('account mutations regenerate only groups that were already numbered', async () => {
  const src = await read('../lib/actions/accounts.ts');

  assert.match(src, /listNumberedGroupIds/);
  assert.match(src, /regenerateAccountNumbersForGroups/);
  assert.match(src, /deleteAccount/);
  assert.match(src, /bulkAddAccounts/);
  assert.match(src, /moveAccountToGroup/);
  assert.match(src, /moveAccountsToGroup/);
  assert.match(src, /setAccountAlias/);
});

test('accounts API exposes numbering state and display numbers', async () => {
  const [types, route] = await Promise.all([
    read('../lib/api/accounts.ts'),
    read('../app/api/accounts/route.ts'),
  ]);

  assert.match(types, /numberingActive:\s*boolean/);
  assert.match(types, /displayNumber:\s*number\s*\|\s*null/);
  assert.match(route, /hasGroupAccountNumbers/);
  assert.match(route, /numberingActive/);
  assert.match(route, /displayNumber:\s*a\.displayNumber/);
});

test('account query safely joins and orders by display number only when numbering is active', async () => {
  const src = await read('../lib/queries/influencers.ts');

  assert.match(src, /useGroupNumbering\?:\s*boolean/);
  assert.match(src, /numberingJoin/);
  assert.match(src, /displayNumberSql/);
  assert.match(src, /NULL::int AS display_number/);
  assert.match(src, /LEFT JOIN account_group_numbers/);
  assert.match(src, /ORDER BY agn\.display_no ASC NULLS LAST,\s*i\.id ASC/);
  assert.match(src, /ORDER BY i\.id ASC/);
});

test('ID header toggles numbering only on a concrete group All view', async () => {
  const src = await read('../components/accounts-table-form.tsx');

  assert.match(src, /canToggleNumbering/);
  assert.match(src, /parsed\.group !== undefined && parsed\.platform === 'all'/);
  assert.match(src, /toggleGroupAccountNumbers/);
  assert.match(src, /numberingActive/);
  assert.match(src, /displayNumber/);
  assert.match(src, /bg-\[hsl\(142_76%_94%\)\]/);
});
