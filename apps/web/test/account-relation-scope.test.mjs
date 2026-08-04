import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// 静态源码断言:own/competitor 订阅分层的查询隔离。
// 监控页语义已收敛为"自己的账号",这里锁住各查询点的 relation 过滤,
// 防止后续重构悄悄把竞品混回自有视图(或反之)。

const scopeSrc = await readFile(new URL('../lib/queries/scope.ts', import.meta.url), 'utf8');
const influencersSrc = await readFile(
  new URL('../lib/queries/influencers.ts', import.meta.url),
  'utf8',
);
const groupViewsSrc = await readFile(
  new URL('../lib/queries/group-views.ts', import.meta.url),
  'utf8',
);
const monitorAnalysisSrc = await readFile(
  new URL('../lib/queries/monitor-analysis.ts', import.meta.url),
  'utf8',
);
const videosSrc = await readFile(new URL('../lib/queries/videos.ts', import.meta.url), 'utf8');

test('scope 助手支持按 relation 追加订阅层过滤', () => {
  assert.match(
    scopeSrc,
    /scopeJoinSql\(\s*viewer:\s*ViewerScope,\s*relation\?:\s*AccountRelation\s*\)/,
    'scopeJoinSql must accept an optional relation',
  );
  assert.match(
    scopeSrc,
    /wi\.relation\s*=\s*\$\{relation\}/,
    'scopeJoinSql must filter on wi.relation when given',
  );
  assert.match(
    scopeSrc,
    /wiv\.relation\s*=\s*\$\{relation\}/,
    'scopeVisibleExistsSql must filter on wiv.relation when given',
  );
});

test('influencers 查询默认锁定 own 层', () => {
  assert.match(
    influencersSrc,
    /listPlatformAccountCounts\(\s*viewer:\s*ViewerScope,\s*groupId\?:\s*number,\s*relation:\s*AccountRelation\s*=\s*'own'/,
    'listPlatformAccountCounts must default to own',
  );
  assert.match(
    influencersSrc,
    /scopeJoinSql\(viewer,\s*opts\.relation\s*\?\?\s*'own'\)/,
    'listInfluencersWithRecentCount must default relation to own',
  );
  assert.match(
    influencersSrc,
    /getFetchTaskStats\(\s*viewer:\s*ViewerScope,\s*relation:\s*AccountRelation\s*=\s*'own'/,
    'getFetchTaskStats must default to own',
  );
  assert.match(
    influencersSrc,
    /wiv\.relation\s*=\s*\$\{relation\}/,
    'getFetchTaskStats jobScope EXISTS must filter on relation',
  );
});

test('listGroupsWithCount 的分组计数只含自有账号', () => {
  assert.match(
    influencersSrc,
    /ON wi\.group_id = g\.id AND wi\.workspace_id = \$\{viewer\.workspaceId\}\s*\n\s*AND wi\.relation = 'own'/,
    'group counts must join only own-tier subscriptions',
  );
});

test('竞品概览/计数查询存在且限定 competitor 层', () => {
  assert.match(
    influencersSrc,
    /countWorkspaceInfluencers/,
    'countWorkspaceInfluencers must exist',
  );
  assert.match(
    influencersSrc,
    /scopeJoinSql\(viewer,\s*'competitor'\)/,
    'listCompetitorOverview must scope to competitor tier',
  );
});

test('group-views 趋势带 relation 过滤且 cache key 已 bump', () => {
  assert.match(
    groupViewsSrc,
    /wi\.relation\s*=\s*\$\{relation\}/,
    'trend scopeFilter EXISTS must filter on relation',
  );
  assert.match(
    groupViewsSrc,
    /group-views-trend@v4-relation/,
    'unstable_cache key must be bumped for the relation-aware aggregation',
  );
});

test('监控页 AI 分析快照只统计自有账号', () => {
  assert.match(
    monitorAnalysisSrc,
    /AND wi\.relation = 'own'/,
    'analysis snapshot main query must filter to own tier',
  );
});

test('listVideos 的 relation 无默认值(详情页对两层开放)', () => {
  assert.match(
    videosSrc,
    /relation\?:\s*AccountRelation/,
    'listVideos must accept optional relation',
  );
  assert.match(
    videosSrc,
    /opts\.relation\s*\?\s*\[eq\(workspaceInfluencers\.relation,\s*opts\.relation\)\]\s*:\s*\[\]/,
    'listVideos must only filter when relation explicitly given',
  );
});
