/**
 * Regression: drizzle@0.36 + postgres-js@3.4.9 sql template 不能直接嵌 Date。
 *
 * 报错现场 (2026-05-25, prod):
 *   TypeError: Buffer.byteLength(Date)
 *   ERR_INVALID_ARG_TYPE, digest: 875584130
 *   触发: 首页点 Today/7d/30d/90d chip → listInfluencersWithRecentCount
 *         → sql`AND published_at >= ${date}` → 炸
 *
 * 原因 (verified by repro): db.execute(sql`...${date}...`) 走 client.unsafe,
 * Date 原样推 params,postgres-js wire-layer Bind 没经 timestamp serializer。
 *
 * 防线:
 *  1. drizzle 的 sqlToQuery 在 raw sql template + Date 时,params[i] 仍是 Date
 *     instance — 这一行为本身就是炸点,任何 sql`...${dateInstance}...` 都中招。
 *  2. 用 `gte(col, date)` (drizzle column-aware) 是安全的;raw template 必须
 *     显式 .toISOString()。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { sql } from '../node_modules/drizzle-orm/sql/index.js';
import { PgDialect } from '../node_modules/drizzle-orm/pg-core/index.js';

const dialect = new PgDialect();
const date = new Date('2026-05-18T00:00:00Z');

test('sql template + Date instance: drizzle 把 Date 原样推到 params (这就是炸的原因)', () => {
  const q = dialect.sqlToQuery(sql`AND published_at >= ${date}`);
  assert.equal(q.sql, 'AND published_at >= $1');
  // 这条断言不是 "应该这样",而是 "确认 drizzle 的行为是这样,所以我们必须自己 toISOString"。
  assert.ok(q.params[0] instanceof Date, 'drizzle 在 raw template 里不会包 Date — 别指望 drizzle 修');
});

test('sql template + Date.toISOString(): params 是 string,wire 层安全', () => {
  const q = dialect.sqlToQuery(sql`AND published_at >= ${date.toISOString()}`);
  assert.equal(q.sql, 'AND published_at >= $1');
  assert.equal(typeof q.params[0], 'string');
  assert.equal(q.params[0], '2026-05-18T00:00:00.000Z');
});

test('queries/influencers.ts:listInfluencersWithRecentCount 没再嵌 raw Date', async () => {
  // 源码扫描:确认 sinceFilter 用了 .toISOString()。
  // 任何回退到 ${opts.since} 写法都会让这个 test 失败,挡住回退 PR。
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(
    new URL('../lib/queries/influencers.ts', import.meta.url),
    'utf8',
  );
  // 必须存在 .toISOString() 用法
  assert.match(
    src,
    /sql`AND published_at >= \$\{opts\.since\.toISOString\(\)\}`/,
    'sinceFilter 必须 .toISOString() — 否则首页 range chip 整页炸',
  );
  // 不能存在裸 Date 嵌入
  assert.doesNotMatch(
    src,
    /sql`AND published_at >= \$\{opts\.since\}`/,
    '禁止把 Date instance 原样塞 sql template',
  );
});
