import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const { buildAccountsQuery, parseAccountsSearchParams } = await import(
  '../lib/url-params.ts'
);

test('monitor chart controls parse period and range independently', () => {
  const defaults = parseAccountsSearchParams({});
  assert.equal(defaults.period, 'week');
  assert.equal(defaults.chartRange, '90d');

  assert.equal(parseAccountsSearchParams({ period: 'day' }).period, 'day');
  assert.equal(parseAccountsSearchParams({ period: 'month' }).period, 'month');
  assert.equal(parseAccountsSearchParams({ period: 'year' }).period, 'week');

  assert.equal(parseAccountsSearchParams({ chartRange: '30d' }).chartRange, '30d');
  assert.equal(parseAccountsSearchParams({ chartRange: '365d' }).chartRange, '365d');
  assert.equal(parseAccountsSearchParams({ chartRange: 'all' }).chartRange, 'all');
  assert.equal(parseAccountsSearchParams({ chartRange: '7d' }).chartRange, '90d');
});

test('monitor chart normalizes day+all to week+all (avoid 300+ bucket render)', () => {
  // day + all 会生成数百日级 bucket,recharts 渲染明显卡;parse 阶段强制降级到 week。
  const both = parseAccountsSearchParams({ period: 'day', chartRange: 'all' });
  assert.equal(both.period, 'week');
  assert.equal(both.chartRange, 'all');

  // day + 其它 range 不受影响
  assert.equal(parseAccountsSearchParams({ period: 'day', chartRange: '90d' }).period, 'day');
  assert.equal(parseAccountsSearchParams({ period: 'day', chartRange: '365d' }).period, 'day');

  // 不带 period 显式给 chartRange=all 时,默认 period 已是 week,不受影响
  assert.equal(parseAccountsSearchParams({ chartRange: 'all' }).period, 'week');
});

test('monitor chart query omits default controls and encodes explicit choices', () => {
  const current = parseAccountsSearchParams({});

  assert.equal(buildAccountsQuery(current, {}), '');
  assert.equal(buildAccountsQuery(current, { period: 'day' }), '?period=day');
  assert.equal(buildAccountsQuery(current, { period: 'month' }), '?period=month');
  assert.equal(buildAccountsQuery(current, { chartRange: '30d' }), '?chartRange=30d');
  assert.equal(buildAccountsQuery(current, { chartRange: 'all' }), '?chartRange=all');
  assert.equal(
    buildAccountsQuery(current, { period: 'day', chartRange: '365d' }),
    '?period=day&chartRange=365d',
  );
});

test('monitor chart query aggregates by video published_at buckets', async () => {
  const src = await readFile(
    new URL('../lib/queries/group-views.ts', import.meta.url),
    'utf8',
  );

  assert.match(src, /date_trunc\(/);
  assert.match(src, /date_trunc\('day'/);
  assert.match(src, /published_at/);
  assert.match(src, /COUNT\(v\.id\)/i);
  assert.match(src, /chartRange/);
  assert.match(src, /INTERVAL '89 days'/);
  assert.match(src, /MIN\(vs\.published_at\)/);
  assert.doesNotMatch(src, /FROM group_views_daily/);

  // 0008 迁移加了 videos(published_at) 部分索引,schema.ts 必须同步声明
  // (不带 WHERE,见迁移文件注释)避免 db:push 误删。
  const schema = await readFile(
    new URL('../lib/db/schema.ts', import.meta.url),
    'utf8',
  );
  assert.match(schema, /index\('videos_published_at_idx'\)\.on\(t\.publishedAt\)/);
});

test('monitor chart controls are collapsed dropdowns', async () => {
  const src = await readFile(
    new URL('../app/[locale]/(app)/monitor/page.tsx', import.meta.url),
    'utf8',
  );

  assert.match(src, /ChartDropdownFilter/);
  assert.match(src, /viewsRangeLabel/);
  assert.match(src, /viewsRangeAll/);
  assert.doesNotMatch(src, /function ViewsPeriodFilter/);

  // 折叠交互(details/summary)已随组件搬到共享文件,monitor / competitors 两页共用。
  const dropdown = await readFile(
    new URL('../components/chart-dropdown-filter.tsx', import.meta.url),
    'utf8',
  );
  assert.match(dropdown, /<details/);
  assert.match(dropdown, /<summary/);
});

test('monitor chart copy explains line and bar meanings', async () => {
  const zh = await readFile(new URL('../messages/zh.json', import.meta.url), 'utf8');
  const en = await readFile(new URL('../messages/en.json', import.meta.url), 'utf8');

  assert.match(zh, /曲线/);
  assert.match(zh, /柱子/);
  assert.match(zh, /近 30 天/);
  assert.match(zh, /近 1 年/);
  assert.match(zh, /全部/);
  assert.match(en, /Line/);
  assert.match(en, /Bars/);
  assert.match(en, /Last 30 days/);
  assert.match(en, /Last 1 year/);
  assert.match(en, /All/);
});
