import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// 静态源码断言:accounts server actions 的订阅层安全约束。

const src = await readFile(new URL('../lib/actions/accounts.ts', import.meta.url), 'utf8');

test('bulkAddAccounts 校验公开 RPC 的 relation 入参', () => {
  assert.match(
    src,
    /bulkAddAccounts\(\s*accounts:\s*BulkAccountInput\[\],\s*groupId:\s*number,\s*relation:\s*AccountRelation\s*=\s*'own'/,
    'bulkAddAccounts must accept relation defaulting to own',
  );
  assert.match(
    src,
    /if\s*\(!isAccountRelation\(relation\)\)/,
    'server action must validate relation before any write',
  );
});

test('competitor 导入分支:group_id 写 NULL 且跳过分组校验/编号', () => {
  assert.match(
    src,
    /groupId:\s*isCompetitor\s*\?\s*null\s*:\s*groupId/,
    'competitor subscription rows must keep group_id NULL',
  );
  assert.match(
    src,
    /!isCompetitor\s*&&[\s\S]*?groupInScope\(viewer,\s*groupId\)/,
    'groupInScope check must be skipped for competitor imports',
  );
  assert.match(
    src,
    /isCompetitor\s*\?\s*\[\]\s*:\s*await listNumberedGroupIds/,
    'competitor imports must not touch account numbering',
  );
});

test('移组操作服务端硬化为只作用于 own 层', () => {
  const occurrences = src.match(/eq\(workspaceInfluencers\.relation,\s*'own'\)/g) ?? [];
  assert.ok(
    occurrences.length >= 4,
    `move-to-group select+update must all guard relation='own' (found ${occurrences.length})`,
  );
});

test('retryFailedFetches 限定订阅层(默认 own)', () => {
  assert.match(
    src,
    /retryFailedFetches\(\s*relation:\s*AccountRelation\s*=\s*'own'/,
    'retryFailedFetches must accept relation defaulting to own',
  );
  const relationExists = src.match(/AND wi\.relation = \$\{relation\}/g) ?? [];
  assert.ok(
    relationExists.length >= 2,
    `both dead-letter EXISTS clauses must filter on relation (found ${relationExists.length})`,
  );
});
