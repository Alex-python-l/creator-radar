# Monitor 图表 `chartRange=all` 性能优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把首页 `chartRange='all'` 的图表加载从 50–75ms（冷）+ 频繁重算降到「命中 ≈0ms / 冷 ≈10–25ms」，并消除 `day+all` 组合下 recharts 渲染 364+ buckets 的卡顿。

**Architecture:**
1. **缓存为主路径**：`getGroupViewsTrend` 用 `next/cache` 的 `unstable_cache` 包装，`revalidate=60s`，按 (groupId, period, chartRange, platform) 派生 key。命中率 95%+ 时绝大多数请求绕过 DB。
2. **索引兜底冷加载**：加 `videos(published_at)` 部分索引，让全表 `MIN(published_at)` 子查询从 Seq Scan 20ms 变成 IndexOnly ~1ms。
3. **UI 归一化 + 关动画**：`day+all` 在 URL parse 阶段强制改成 `week+all`，recharts 关 enter animation，去掉 364 bars 渲染卡。

**Tech Stack:** Next 15.1 (`unstable_cache`)、Drizzle ORM 0.36 + postgres-js 3.4、recharts 3.8、PG 15+（`CREATE INDEX CONCURRENTLY ... IF NOT EXISTS` + 部分索引 `WHERE` 子句）、node native test runner（`test/*.test.mjs`）。

**部署顺序（由 [docs/DEPLOY.md](docs/DEPLOY.md) 推导）：**
1. 阿里云 PG 跑 0008 索引迁移（先；幂等可重复）
2. 部署 web 新构建（后；依赖 unstable_cache 包装）
3. 无需 scraper 改动、无需回填脚本

---

## 文件结构

| 路径 | 操作 | 责任 |
|---|---|---|
| `apps/web/drizzle/manual/0008_videos_published_at_idx.sql` | 新建 | `videos(published_at) WHERE published_at IS NOT NULL` 部分索引迁移 |
| `apps/web/lib/db/schema.ts` | 修改（`videos` 表的 indexes 数组） | drizzle schema 同步声明同名索引，保持 introspect 一致 |
| `apps/web/lib/queries/group-views.ts` | 修改 | 拆出内部 `_getGroupViewsTrend`，`export const getGroupViewsTrend = unstable_cache(...)` |
| `apps/web/lib/url-params.ts` | 修改（`parseAccountsSearchParams` 末尾） | `period === 'day' && chartRange === 'all'` → `period = 'week'` 归一化 |
| `apps/web/components/group-views-chart.tsx` | 修改 | `<Bar>` 和 `<Area>` 加 `isAnimationActive={false}` |
| `apps/web/test/monitor-chart-period.test.mjs` | 修改 | 加 `day+all → week` 归一化测试用例 |
| `apps/web/test/group-views-trend-cache.test.mjs` | 新建 | 源码 grep 验证 `unstable_cache` + revalidate 60 配置 |
| `apps/web/test/group-views-chart-animation.test.mjs` | 新建 | 源码 grep 验证 `isAnimationActive={false}` |

---

## Task 1: URL 归一化 — `day+all → week+all`

**Files:**
- Modify: `apps/web/lib/url-params.ts:141-150`
- Test: `apps/web/test/monitor-chart-period.test.mjs:9-22`

- [ ] **Step 1: 加失败测试**

把 `apps/web/test/monitor-chart-period.test.mjs:9` 的第一个 `test('monitor chart controls parse period and range independently', ...)` 末尾新增一个独立 `test`：

```javascript
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
```

- [ ] **Step 2: 跑测试验证 fail**

```bash
cd apps/web && npm test -- --test-name-pattern='normalizes day\+all'
```

预期：FAIL — `expected 'week', got 'day'`

- [ ] **Step 3: 改 `parseAccountsSearchParams` 加归一化**

修改 `apps/web/lib/url-params.ts`。当前 `parseAccountsSearchParams` 末尾长这样（约 130–149 行）：

```typescript
  const period = pickOne(CHART_PERIODS, periodRaw, 'week');
  const chartRange = pickOne(CHART_RANGES, chartRangeRaw, '90d');
  // ... platform / chartPlatform 解析 ...

  return {
    platform,
    range: (RANGES as readonly string[]).includes(rangeRaw ?? '') ? (rangeRaw as Range) : 'all',
    expand,
    group,
    period,
    chartRange,
    chartPlatform,
  };
```

把 return 之前加 normalize、return 里 shorthand `period,` 改成显式 `period: normalizedPeriod,`：

```typescript
  const period = pickOne(CHART_PERIODS, periodRaw, 'week');
  const chartRange = pickOne(CHART_RANGES, chartRangeRaw, '90d');
  // ... platform / chartPlatform 解析 ...

  // day + all 会生成数百个日 bucket（一年多 = 364 个 Bar 元素）,recharts
  // 渲染明显卡;强制降级到 week。week + all 通常 50 多个 bucket 已经足够。
  const normalizedPeriod = period === 'day' && chartRange === 'all' ? 'week' : period;

  return {
    platform,
    range: (RANGES as readonly string[]).includes(rangeRaw ?? '') ? (rangeRaw as Range) : 'all',
    expand,
    group,
    period: normalizedPeriod,
    chartRange,
    chartPlatform,
  };
```

其它字段（`platform` / `chartPlatform` / `expand` / `group` 等）保持原解析逻辑不动。

- [ ] **Step 4: 跑测试验证 pass**

```bash
cd apps/web && npm test -- --test-name-pattern='monitor chart'
```

预期：全部 PASS（含 5 个 monitor chart 相关 test，新加的归一化测试也 pass）

- [ ] **Step 5: 全量回归**

```bash
cd apps/web && npm test
```

预期：所有现存 test 全部 PASS（归一化只在 `day+all` 组合下触发，对其它组合无影响）。

- [ ] **Step 6: Commit**

```bash
git add apps/web/lib/url-params.ts apps/web/test/monitor-chart-period.test.mjs
git commit -m "$(cat <<'EOF'
perf(monitor): 归一化 day+all → week+all 避免 recharts 渲染 300+ bars

day period + all range 会生成一年多的日 bucket(~364 Bar 元素),recharts
初次 mount 动画明显卡。week + all 通常 50 多个 bucket,既保留趋势又流畅。
EOF
)"
```

---

## Task 2: 加 `videos(published_at)` 部分索引迁移

**Files:**
- Create: `apps/web/drizzle/manual/0008_videos_published_at_idx.sql`
- Modify: `apps/web/lib/db/schema.ts:107-111` (videos 表 indexes 数组)
- Test: `apps/web/test/monitor-chart-period.test.mjs` (新增一个 assertion 验证 schema 同步)

- [ ] **Step 1: 写迁移 SQL**

新建 `apps/web/drizzle/manual/0008_videos_published_at_idx.sql`：

```sql
-- 受控手工迁移:videos 加 (published_at) 部分索引。
--
-- 为什么:getGroupViewsTrend 在 chartRange='all' 时跑两次
-- MIN(published_at) WHERE published_at IS NOT NULL 子查询(buckets 生成
-- 与主 LEFT JOIN range 各一次)。现有 (influencer_id, published_at) 复合
-- 索引 leading column 不是 published_at,Postgres planner 选 Seq Scan
-- 19k+ 行(实测 20ms/次)。加单列部分索引后 IndexOnly Scan 1 row。
--
-- 同时主聚合按 date_trunc('week'/'day'/'month', published_at) 分桶,
-- 这里的 published_at 范围 + IS NOT NULL 过滤也吃这个索引。
--
-- 排除 NULL 的 27 行索引更小,几乎所有视频都有 published_at。
--
-- 应用方式(经 SSH 隧道到目标库):
--   psql "$DATABASE_URL" -f apps/web/drizzle/manual/0008_videos_published_at_idx.sql
-- 或直接到容器:
--   docker exec svm-postgres psql -U postgres -d short_video_monitor \
--     -f /tmp/0008_videos_published_at_idx.sql
--
-- IF NOT EXISTS 幂等可重复。CONCURRENTLY 不锁表(19k 行虽瞬秒,养成习惯)。

CREATE INDEX CONCURRENTLY IF NOT EXISTS videos_published_at_idx
  ON videos (published_at)
  WHERE published_at IS NOT NULL;
```

- [ ] **Step 2: 同步 schema.ts（让 drizzle introspect 一致）**

修改 `apps/web/lib/db/schema.ts:107-111`，在 `index('videos_play_count_idx').on(t.playCount),` 之后加一行：

```typescript
  (t) => [
    uniqueIndex('videos_platform_videoid_uq').on(t.platform, t.videoId),
    index('videos_influencer_published_idx').on(t.influencerId, t.publishedAt),
    index('videos_play_count_idx').on(t.playCount),
    // drizzle 不表达 partial index `WHERE`,DB 侧实际带 WHERE published_at IS NOT NULL
    // (见 drizzle/manual/0008_*.sql)。此处仅声明索引存在,避免 db:push 误删。
    index('videos_published_at_idx').on(t.publishedAt),
  ],
```

注意：drizzle-orm 0.36 的 `index()` 不支持 partial WHERE，所以 schema 声明保持无 WHERE 版本，**实际 DB 是部分索引**——这种"声明与实物有差异"的模式在该项目已有先例（见 0002 迁移注释里的 `(influencer_id, id DESC)` 同样问题）。

- [ ] **Step 3: 加 schema 同步 assertion**

把 `apps/web/test/monitor-chart-period.test.mjs:38-52` 那个 `test('monitor chart query aggregates by video published_at buckets', ...)` 末尾加：

```javascript
  // 0008 迁移加了 videos(published_at) 部分索引,schema.ts 必须同步声明
  // (不带 WHERE,见迁移文件注释)避免 db:push 误删。
  const schema = await readFile(
    new URL('../lib/db/schema.ts', import.meta.url),
    'utf8',
  );
  assert.match(schema, /index\('videos_published_at_idx'\)\.on\(t\.publishedAt\)/);
```

- [ ] **Step 4: 跑测试验证 pass**

```bash
cd apps/web && npm test -- --test-name-pattern='monitor chart query aggregates'
```

预期：PASS

- [ ] **Step 5: typecheck**

```bash
cd apps/web && npm run typecheck
```

预期：无错误（drizzle `index().on()` 类型推断仍通过）。

- [ ] **Step 6: Commit**

```bash
git add apps/web/drizzle/manual/0008_videos_published_at_idx.sql apps/web/lib/db/schema.ts apps/web/test/monitor-chart-period.test.mjs
git commit -m "$(cat <<'EOF'
perf(db): 加 videos(published_at) 部分索引服务 chartRange=all

getGroupViewsTrend 在 chartRange=all 时跑两次 MIN(published_at) 子查询,
现有 (influencer_id, published_at) 复合索引 leading column 不对,planner
走 Seq Scan 19k 行 ×2(实测各 20ms)。加 (published_at) WHERE NOT NULL
部分索引后变 IndexOnly ~1ms。drizzle schema 同步声明避免 db:push 误删。
EOF
)"
```

---

## Task 3: `getGroupViewsTrend` 用 `unstable_cache` 包装（60s revalidate）

**Files:**
- Modify: `apps/web/lib/queries/group-views.ts:21-26, 136`
- Test: `apps/web/test/group-views-trend-cache.test.mjs` (新建)

- [ ] **Step 1: 加失败测试**

新建 `apps/web/test/group-views-trend-cache.test.mjs`：

```javascript
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

  // cache key 含版本号,改实现时手动 bump 即可全失效
  assert.match(src, /group-views-trend@v1/);
});
```

- [ ] **Step 2: 跑测试验证 fail**

```bash
cd apps/web && npm test -- --test-name-pattern='unstable_cache'
```

预期：FAIL — 多个 `does not match` 报错（next/cache import / unstable_cache / etc 都还没加）。

- [ ] **Step 3: 改 `group-views.ts` 拆内部 + 包装**

修改 `apps/web/lib/queries/group-views.ts`：

3a. 顶部 import 区（第 1 行附近）加：

```typescript
import { unstable_cache } from 'next/cache';
```

3b. 把 `export async function getGroupViewsTrend(opts: {...}): Promise<GroupViewsPoint[]>` 改名为内部 `_getGroupViewsTrend`，**整个函数体不动**。即把 `21` 行的 `export async function getGroupViewsTrend(` 改成 `async function _getGroupViewsTrend(`。

3c. 文件末尾（136 行 `return rows.map(...)` 闭合后）新增：

```typescript

/**
 * Chart 数据 60s stale OK(用户已确认),用 unstable_cache 包装:
 * - 命中:~0ms,绕过 DB
 * - Miss:首次 _getGroupViewsTrend 跑一次,后续 60s 内命中
 * - 失效:60s 自然过期;改聚合逻辑时 bump key 版本号 `@v1` 全失效
 *
 * cache key 由 opts 自动派生(unstable_cache 序列化参数)。tag 'group-views'
 * 留作将来跨进程手动失效用(目前不依赖)。
 *
 * 注意:_getGroupViewsTrend 必须直接传给 unstable_cache,不能在内部用闭包
 * 引用 opts ——闭包变量不会进入 cache key,会跨参数串数据。
 */
export const getGroupViewsTrend = unstable_cache(
  _getGroupViewsTrend,
  ['group-views-trend@v1'],
  { revalidate: 60, tags: ['group-views'] },
);
```

- [ ] **Step 4: 跑测试验证 pass**

```bash
cd apps/web && npm test -- --test-name-pattern='unstable_cache'
```

预期：PASS（所有 6 个 grep assertion 都命中）。

- [ ] **Step 5: typecheck + 全量回归**

```bash
cd apps/web && npm run typecheck && npm test
```

预期：typecheck 0 错（`unstable_cache` 在 next 15.1 是稳定签名 `(fn, keyParts, opts) => fn`）。全量 test PASS。

- [ ] **Step 6: Commit**

```bash
git add apps/web/lib/queries/group-views.ts apps/web/test/group-views-trend-cache.test.mjs
git commit -m "$(cat <<'EOF'
perf(monitor): getGroupViewsTrend 用 unstable_cache 60s revalidate

chart 数据 60s stale OK(已确认)。包装后:
- 命中 ~0ms 绕过 DB(95%+ 请求)
- Miss 时跑一次 _getGroupViewsTrend(配合 0008 索引 ~10-25ms)
- 60s 自然过期;改聚合逻辑时 bump cache key 版本号
EOF
)"
```

---

## Task 4: recharts 关 enter animation（消除剩余渲染卡）

**Files:**
- Modify: `apps/web/components/group-views-chart.tsx:94-108`
- Test: `apps/web/test/group-views-chart-animation.test.mjs` (新建)

- [ ] **Step 1: 加失败测试**

新建 `apps/web/test/group-views-chart-animation.test.mjs`：

```javascript
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// recharts 默认 enter animation 在数十~上百 data points 时初次 mount 明显卡
// (切 chartRange 触发 Suspense fallback → 新数据 → mount → 全部 Bar 单独动画)。
// 切组合的高频路径关掉更顺滑。Tooltip 等交互不受影响。
test('group views chart disables enter animation on Bar and Area', async () => {
  const src = await readFile(
    new URL('../components/group-views-chart.tsx', import.meta.url),
    'utf8',
  );

  // recharts 的 Bar 和 Area 都接受 isAnimationActive prop
  const barMatches = src.match(/<Bar[\s\S]*?\/>/g) ?? [];
  const areaMatches = src.match(/<Area[\s\S]*?\/>/g) ?? [];

  assert.ok(barMatches.length >= 1, 'expected at least one <Bar /> in chart');
  assert.ok(areaMatches.length >= 1, 'expected at least one <Area /> in chart');

  for (const m of barMatches) {
    assert.match(m, /isAnimationActive=\{false\}/, `Bar missing isAnimationActive={false}: ${m}`);
  }
  for (const m of areaMatches) {
    assert.match(m, /isAnimationActive=\{false\}/, `Area missing isAnimationActive={false}: ${m}`);
  }
});
```

- [ ] **Step 2: 跑测试验证 fail**

```bash
cd apps/web && npm test -- --test-name-pattern='disables enter animation'
```

预期：FAIL — `Bar missing isAnimationActive={false}`。

- [ ] **Step 3: 改组件**

修改 `apps/web/components/group-views-chart.tsx:94-108`，在 `<Bar>` 和 `<Area>` 元素上各加 `isAnimationActive={false}`：

```tsx
          <Bar
            yAxisId="videos"
            dataKey="videoCount"
            fill="var(--color-muted-foreground)"
            opacity={0.22}
            radius={[3, 3, 0, 0]}
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            yAxisId="views"
            dataKey="totalViews"
            stroke="var(--color-primary)"
            strokeWidth={2}
            fill={`url(#${gradId})`}
            isAnimationActive={false}
          />
```

- [ ] **Step 4: 跑测试验证 pass**

```bash
cd apps/web && npm test -- --test-name-pattern='disables enter animation'
```

预期：PASS。

- [ ] **Step 5: Commit**

```bash
git add apps/web/components/group-views-chart.tsx apps/web/test/group-views-chart-animation.test.mjs
git commit -m "$(cat <<'EOF'
perf(monitor): 关 recharts Bar/Area enter animation

切 chartRange 触发 Suspense fallback → 新数据 → mount 动画在 50+ Bar
元素上单独跑,初次切换明显卡。关掉后 Tooltip 等交互不受影响。
EOF
)"
```

---

## Task 5: 部署索引迁移到生产 PG（必须先于 web 部署）

**Files:** 无代码改动 — 部署操作 + 实测验证记录。

- [ ] **Step 1: 经隧道把 SQL 拷到目标库容器**

```bash
scp -i ~/.ssh/svm_server apps/web/drizzle/manual/0008_videos_published_at_idx.sql root@139.196.52.92:/tmp/
ssh -i ~/.ssh/svm_server root@139.196.52.92 'docker cp /tmp/0008_videos_published_at_idx.sql svm-postgres:/tmp/'
```

预期：无输出（scp + docker cp 静默成功）。

- [ ] **Step 2: 跑迁移**

```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 'docker exec svm-postgres psql -U postgres -d short_video_monitor -f /tmp/0008_videos_published_at_idx.sql'
```

预期输出：
```
CREATE INDEX
```

（如果索引已存在则输出 `NOTICE: relation "videos_published_at_idx" already exists, skipping` —— 这是幂等 IF NOT EXISTS 正确行为。）

- [ ] **Step 3: 验证索引存在 + 大小**

```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 "docker exec svm-postgres psql -U postgres -d short_video_monitor -c \"SELECT indexname, pg_size_pretty(pg_relation_size(indexrelid)) FROM pg_indexes JOIN pg_class ON relname = indexname WHERE tablename = 'videos' ORDER BY indexname\""
```

预期：列表里应出现 `videos_published_at_idx`，大小约 500KB–1MB（19k 行的 BTREE）。

- [ ] **Step 4: 实测前后耗时（验证根因诊断）**

把对话中的 chart all/week 测试 SQL 跑 5 次取均值，对比修复前的 50–66ms：

```bash
cat > /tmp/perf_check.sql <<'SQL'
\timing on
SELECT count(*) FROM (
  WITH buckets AS (
    SELECT generate_series(
      date_trunc('week', COALESCE((SELECT MIN(vs.published_at)::date FROM videos vs WHERE vs.published_at IS NOT NULL AND vs.published_at < CURRENT_DATE + INTERVAL '1 day'), CURRENT_DATE))::date,
      date_trunc('week', CURRENT_DATE)::date, INTERVAL '1 week')::date AS period_start
  )
  SELECT to_char(b.period_start, 'YYYY-MM-DD'), COALESCE(SUM(v.play_count), 0)
  FROM buckets b
  LEFT JOIN videos v ON v.published_at IS NOT NULL
    AND v.published_at >= COALESCE((SELECT MIN(vs.published_at)::date FROM videos vs WHERE vs.published_at IS NOT NULL AND vs.published_at < CURRENT_DATE + INTERVAL '1 day'), CURRENT_DATE)
    AND v.published_at < CURRENT_DATE + INTERVAL '1 day'
    AND date_trunc('week', v.published_at)::date = b.period_start
  LEFT JOIN influencers i ON i.id = v.influencer_id
  GROUP BY b.period_start
) t;
SQL
scp -i ~/.ssh/svm_server /tmp/perf_check.sql root@139.196.52.92:/tmp/
ssh -i ~/.ssh/svm_server root@139.196.52.92 "docker cp /tmp/perf_check.sql svm-postgres:/tmp/perf_check.sql && for i in 1 2 3 4 5; do docker exec svm-postgres psql -U postgres -d short_video_monitor -f /tmp/perf_check.sql 2>&1 | grep -E '^Time:'; done"
```

预期：5 次平均 ≤ 25ms（比修复前的 ~50ms 至少减半）。如果没明显下降，检查 Step 5。

- [ ] **Step 5: 验证 EXPLAIN 走了新索引**

```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 "docker exec svm-postgres psql -U postgres -d short_video_monitor -c \"EXPLAIN (ANALYZE, BUFFERS) SELECT MIN(published_at) FROM videos WHERE published_at IS NOT NULL AND published_at < CURRENT_DATE + INTERVAL '1 day'\""
```

预期 plan 含 `Index Only Scan using videos_published_at_idx`（**不**是 `Seq Scan on videos`），actual time < 1ms。

如果还是 Seq Scan：跑 `ANALYZE videos;` 让 planner 更新统计后重测。

---

## Task 6: 部署 web + 端到端验证

**Files:** 无代码改动 — 构建 + 部署 + 手动验证。

- [ ] **Step 1: 本地 build 验证**

```bash
cd apps/web && npm run build
```

预期：编译成功，无 type 错误。`unstable_cache` 在 next 15.1 是稳定 API，不会报 deprecation warning。

- [ ] **Step 2: 按 DEPLOY.md A 节流程部署 web**

参考 `docs/DEPLOY.md` A 节的 tar+ssh+docker compose build 流程。从仓库根：

```bash
COPYFILE_DISABLE=1 tar czf - --no-xattrs --exclude node_modules --exclude .next --exclude .git \
  --exclude ./apps/scraper package.json pnpm-*.yaml .dockerignore docker-compose.cvm.yml apps/web packages scripts \
  | ssh -i ~/.ssh/svm_server root@139.196.52.92 'tar xzf - -C /opt/svm'

ssh -i ~/.ssh/svm_server root@139.196.52.92 'cd /opt/svm && docker compose -f docker-compose.cvm.yml build web && docker compose -f docker-compose.cvm.yml up -d web'
```

预期：build 成功，容器重启后 `docker compose ps` 显示 web 状态 `Up`。

- [ ] **Step 3: 端到端实测（命中 vs 冷加载）**

用浏览器开发者工具 Network 面板访问 `http://139.196.52.92:3000/zh?chartRange=all`：

第一次（冷加载，cache miss）：
- chart Suspense 段 ttfb 预期 < 100ms（含 DB 10-25ms + 网络 RTT）

刷新页面（cache hit）：
- chart Suspense 段 ttfb 预期 < 50ms（几乎全是网络 RTT，DB 没动）

切换 chartRange 30d ↔ 90d ↔ all：
- 第一次切到某组合：冷加载（~100ms）
- 60s 内回切：命中（~50ms）
- 视觉上无可感知的"卡"

- [ ] **Step 4: 验证 day+all 强制归一化**

访问 `http://139.196.52.92:3000/zh?period=day&chartRange=all`：
- URL 不变（server 不重定向，仅 parse 阶段归一化）
- 图表实际渲染按 week period（约 50 多个 Bar，不是 364 个）
- chart 控件的 period 下拉显示当前选中 `Day`（URL 字面值）—— 这是预期的"轻归一化"：URL 保留用户意图，渲染按归一化结果

如果觉得 UI 体验不一致（用户看到"day" 但实际是 week），后续可考虑在 ChartDropdownFilter 高亮逻辑里把 day+all 的 active 改成 week —— 本次 plan 范围内**不做**这个改动，先观察。

- [ ] **Step 5: 关闭 task list**

```bash
# 把本 plan 标记完成
```

记录最终实测耗时（命中/冷加载/day+all 渲染）到本文件末尾「实测结果」区。

---

## 实测结果（部署后填）

- chart all/week 冷加载：__ms (5 次平均，目标 ≤25ms)
- chart all/week 命中：__ms (目标 <5ms 仅 unstable_cache 开销)
- day+all 切换无卡顿：是 / 否
- 用户主观体感："不再明显卡顿"：是 / 否

---

## 回滚方案

如果发现严重问题：

1. **revert 代码**：`git revert <四个 commit>` 然后按 Task 6 Step 2 重部署
2. **删索引**（可选，索引本身无副作用）：
   ```sql
   DROP INDEX CONCURRENTLY IF EXISTS videos_published_at_idx;
   ```
3. **清缓存**（如果 cache 数据怀疑损坏）：重启 web 容器，`unstable_cache` 是进程内存（Next 15 也支持外部 store，当前默认进程内）。

每一步都独立可回滚。索引可以保留即使代码 revert（无害，可能利好其它查询）。
