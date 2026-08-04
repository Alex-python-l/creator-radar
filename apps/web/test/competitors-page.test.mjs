import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// 静态源码断言:竞品分析页的结构约束(仿 radar-layout.test.mjs 风格)。

const pageSrc = await readFile(
  new URL('../app/[locale]/(app)/competitors/page.tsx', import.meta.url),
  'utf8',
);
const navSrc = await readFile(
  new URL('../components/app-sidebar-nav.tsx', import.meta.url),
  'utf8',
);

test('竞品页鉴权在数据渲染之前', () => {
  const viewerIdx = pageSrc.indexOf('await requireViewer()');
  const returnIdx = pageSrc.indexOf('return (');
  assert.ok(viewerIdx > -1, 'page must call requireViewer');
  assert.ok(viewerIdx < returnIdx, 'requireViewer must run before rendering');
});

test('竞品页各数据模块都限定 competitor 层', () => {
  assert.match(
    pageSrc,
    /getFetchTaskStats\(viewer,\s*'competitor'\)/,
    'task stats must scope to competitor',
  );
  assert.match(
    pageSrc,
    /countWorkspaceInfluencers\(viewer,\s*'competitor'\)/,
    'account count must scope to competitor',
  );
  assert.match(
    pageSrc,
    /relation:\s*'competitor'/,
    'trend/videos queries must pass competitor relation',
  );
  assert.match(
    pageSrc,
    /relation="competitor"/,
    'AccountsPanel and ImportDialog must run in competitor mode',
  );
});

test('竞品页不渲染分组侧栏和 AI 分析卡', () => {
  assert.doesNotMatch(
    pageSrc,
    /from '@\/components\/groups-sidebar'/,
    'competitor page has no group axis',
  );
  assert.doesNotMatch(
    pageSrc,
    /from '@\/components\/monitor-ai-analysis'/,
    'AI analysis card is own-tier only for now',
  );
});

test('竞品页爆款卡不复用 VideosTable(避免 videos 参数体系与本页 URL 冲突)', () => {
  assert.doesNotMatch(pageSrc, /from '@\/components\/videos-table'/);
});

test('侧栏导航包含竞品分析入口', () => {
  assert.match(navSrc, /\/competitors/, 'radar nav must link to /competitors');
  assert.match(navSrc, /Telescope/, 'competitors nav item uses the Telescope icon');
  assert.match(
    navSrc,
    /tLayout\('competitors'\)/,
    'competitors nav label must come from layout namespace',
  );
});
