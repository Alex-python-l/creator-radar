import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const route = readFileSync(new URL('../app/api/monitor/analysis-snapshot/route.ts', import.meta.url), 'utf8');
const query = readFileSync(new URL('../lib/queries/monitor-analysis.ts', import.meta.url), 'utf8');
const component = readFileSync(new URL('../components/monitor-ai-analysis.tsx', import.meta.url), 'utf8');
const page = readFileSync(new URL('../app/[locale]/(app)/monitor/page.tsx', import.meta.url), 'utf8');

test('monitor snapshot requires a session and passes only the active workspace to its query', () => {
  assert.match(route, /const viewer = await getViewer\(\)/);
  assert.match(route, /status: 401/);
  assert.match(route, /getMonitorAnalysisSnapshot\(viewer/);
  assert.match(query, /wi\.workspace_id = \$\{viewer\.workspaceId\}/);
  assert.match(query, /workspace_id = \$\{viewer\.workspaceId\}/);
  assert.match(route, /MonitorAnalysisGroupNotFoundError/);
  assert.match(route, /status: 404/);
});

test('AI analysis card is above the chart and invalidates reports when scope changes', () => {
  assert.ok(page.indexOf('<MonitorAiAnalysis') < page.indexOf("{t('viewsTrendTitle')}"));
  assert.match(component, /fetch\('\/studio\/api\/monitor\/analysis'/);
  assert.match(component, /\[groupId, platform, locale\]/);
  assert.match(component, /setRange\(event\.target\.value as MonitorAnalysisRange\);\s*setResult\(null\)/);
  assert.match(component, /disabled=\{loading\}/);
});

test('AI analysis exposes account and original-content links with fact-only fallback states', () => {
  assert.match(component, /href=\{`\/\$\{locale\}\/accounts\/\$\{account\.id\}`\}/);
  assert.match(component, /target="_blank" rel="noreferrer"/);
  assert.match(component, /missing_key/);
  assert.match(component, /aiFailed/);
  assert.match(component, /currentIssues/);
  assert.match(component, /silentAccounts/);
  assert.match(component, /enoughVideoSample/);
});
