import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// recharts 默认 enter animation 在数十~上百 data points 时初次 mount 明显卡
// (切 chartRange 触发 Suspense fallback → 新数据 → mount → 全部 Bar 单独动画)。
// 切组合的高频路径关掉更顺滑。Tooltip 等交互不受影响。
test('group views chart disables enter animation on Bar and Line', async () => {
  const src = await readFile(
    new URL('../components/group-views-chart.tsx', import.meta.url),
    'utf8',
  );

  // recharts 的 Bar 和 Line 都接受 isAnimationActive prop
  const barMatches = src.match(/<Bar[\s\S]*?\/>/g) ?? [];
  const lineMatches = src.match(/<Line[\s\S]*?\/>/g) ?? [];

  assert.ok(barMatches.length >= 1, 'expected at least one <Bar /> in chart');
  assert.ok(lineMatches.length >= 1, 'expected at least one <Line /> in chart');

  for (const m of barMatches) {
    assert.match(m, /isAnimationActive=\{false\}/, `Bar missing isAnimationActive={false}: ${m}`);
  }
  for (const m of lineMatches) {
    assert.match(m, /isAnimationActive=\{false\}/, `Line missing isAnimationActive={false}: ${m}`);
  }
});
