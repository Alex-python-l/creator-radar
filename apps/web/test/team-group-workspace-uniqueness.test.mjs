import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const schema = readFileSync(new URL('../lib/db/schema.ts', import.meta.url), 'utf8');
const migration = readFileSync(
  new URL('../drizzle/manual/0015_account_group_workspace_uniqueness.sql', import.meta.url),
  'utf8',
);

test('team group names are unique per workspace rather than globally', () => {
  assert.match(migration, /account_groups_workspace_name_uq/);
  assert.match(migration, /DROP INDEX IF EXISTS account_groups_system_name_uq/);
  assert.match(migration, /DROP INDEX IF EXISTS account_groups_owner_name_uq/);
  assert.doesNotMatch(schema, /uniqueIndex\('account_groups_owner_name_uq'\)/);
});

test('legacy group indexes are removed only after workspace bootstrap', () => {
  assert.match(migration, /account_groups WHERE workspace_id IS NULL/);
  assert.match(migration, /finish workspace bootstrap first/);
});
