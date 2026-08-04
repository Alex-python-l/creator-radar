import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// chart 数据 60s stale OK(已确认):用 unstable_cache 包装,命中后绕过 DB。
// 测试用源码 grep 而非运行时调用 —— 这函数依赖 db 连接,单元测试边界
// 与 monitor-chart-period 已有模式一致。
test('getGroupViewsTrend wraps internal impl with unstable_cache + 60s revalidate', async () => {
  const src = await readFile(
    new URL('../lib/queries/group-views.ts', import.meta.url),
    'utf8',
  );

  // import 来源是 next/cache
  assert.match(src, /from ['"]next\/cache['"]/);
  assert.match(src, /unstable_cache/);

  // 命名:内部实现 _getGroupViewsTrend,导出名仍是 getGroupViewsTrend(消费方不改)
  assert.match(src, /async function _getGroupViewsTrend/);
  assert.match(src, /export const getGroupViewsTrend = unstable_cache/);

  // 60s revalidate(对应"接受 30-60s stale"的决策)+ tag 便于将来手动失效
  assert.match(src, /revalidate:\s*60/);
  assert.match(src, /tags:\s*\[\s*['"]group-views['"]\s*\]/);

  // cache key 含版本号,改实现时手动 bump 即可全失效(具体版本不锁死)
  assert.match(src, /group-views-trend@v\d+/);
  // 工作区隔离:workspace 标量必须在参数对象里(即进入 cache key),不同工作区不串缓存
  assert.match(src, /workspaceId:\s*number/);
});
