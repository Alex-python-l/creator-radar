# 账号分组展示编号实施计划

> **For agentic workers / 给智能体执行者：** REQUIRED SUB-SKILL：实现本计划时，推荐使用 `superpowers:subagent-driven-development` 逐任务派发和复核；不派发子智能体时使用 `superpowers:executing-plans` 按任务顺序执行。任务用 `- [ ]` 追踪状态。

**目标：** 在 Account Management 的具体分组页中，为账号表增加“持久化展示编号”。用户只能在某个分组的 All 分类点击 `ID` 表头生成或取消编号；编号后，该分组下 TikTok、Instagram、Facebook、YouTube 等平台筛选页也显示并按同一套编号排序。

**核心做法：** 新增“编号状态表 + 编号明细表”。状态表记录某个分组是否处于编号模式，明细表记录每个账号当前展示编号。后端 action 负责生成、取消、重编；账号 API 返回编号状态和每行展示编号；前端只负责触发和展示，业务动作继续使用 `influencers.id`。

**本计划修正点：** 旧方案只用编号明细表判断是否启用编号，会在“已编号分组变空”时丢失编号状态。中文执行版改成两张表：状态表负责“是否启用”，明细表负责“编号是多少”，这样新入或移出账号时更稳定。

## 1. 必须遵守的边界

- 编号是数据库持久化记录，不是 URL、React state、TanStack Query cache、浏览器本地存储或临时状态。
- 只有具体分组的 All 视图可以点击 `ID` 表头生成或取消编号。
- 全局混合 `ALL` 视图没有 `group` 参数，不能生成或取消编号。
- 具体分组的平台筛选视图继承编号并按编号排序，但这些页面点击 `ID` 表头不生效。
- 展示编号不是源 ID。`influencers.id` 仍然用于详情链接、行 key、展开行、删除、移动、抓取、后台任务和 API 主标识。
- 每次生成或重编都基于当前分组账号全集，从 `1..N` 重新编号。
- 排序依据复用 `formatAccountListLabel()`：alias 优先，其次 displayName，再其次格式化 username；同名时用源 ID 稳定兜底。
- 取消编号必须完全取消：删除该分组编号状态和编号明细。
- 新增、删除、移动账号时，如果受影响分组在变更前已经处于编号模式，变更后自动重编；未编号分组不能因为账号变化自动开启编号。
- 修改账号 alias 会改变 `formatAccountListLabel()` 的排序结果，因此 alias 保存成功后，如果该账号所在分组已经启用编号，也要自动重编该分组。scraper 更新 `displayName` 不纳入本次自动重编，避免扩大到抓取链路。
- 生产库迁移按 `docs/DEPLOY.md` 走手写 SQL，不对生产库使用 `drizzle-kit push`。
- 本需求不改 scraper、任务队列抓取逻辑、视频业务逻辑。

## 2. 页面触发规则

允许触发生成或取消编号：

```text
/(zh|en)?group=<groupId>
/(zh|en)?group=<groupId>&platform=all
```

前端判断条件：

```ts
parsed.group !== undefined && parsed.platform === 'all'
```

不允许触发：

```text
/(zh|en)
/(zh|en)?platform=tiktok
/(zh|en)?group=<groupId>&platform=tiktok
/(zh|en)?group=<groupId>&platform=instagram
/(zh|en)?group=<groupId>&platform=facebook
/(zh|en)?group=<groupId>&platform=youtube
```

注意：平台筛选页虽然不能触发生成或取消，但如果该分组已经启用编号，仍然显示浅绿色编号，并按 `display_no` 排序。平台筛选页看到的编号可能不连续，这是正确的，因为它只是分组全集的子集。

## 3. 数据模型

新增两张表。

### `account_group_numbering`

负责记录“某个分组是否处于编号模式”。

```ts
export const accountGroupNumbering = pgTable('account_group_numbering', {
  groupId: integer('group_id')
    .primaryKey()
    .references(() => accountGroups.id, { onDelete: 'cascade' }),
  enabledAt: timestamp('enabled_at', { withTimezone: true }).notNull().defaultNow(),
  regeneratedAt: timestamp('regenerated_at', { withTimezone: true }).notNull().defaultNow(),
});
```

### `account_group_numbers`

负责记录“某个分组内某个账号当前展示几号”。

```ts
export const accountGroupNumbers = pgTable(
  'account_group_numbers',
  {
    groupId: integer('group_id')
      .notNull()
      .references(() => accountGroupNumbering.groupId, { onDelete: 'cascade' }),
    influencerId: integer('influencer_id')
      .notNull()
      .references(() => influencers.id, { onDelete: 'cascade' }),
    displayNo: integer('display_no').notNull(),
    sortLabel: text('sort_label').notNull(),
    generatedAt: timestamp('generated_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    primaryKey({ columns: [t.groupId, t.influencerId] }),
    uniqueIndex('account_group_numbers_group_display_no_uq').on(t.groupId, t.displayNo),
    index('account_group_numbers_influencer_idx').on(t.influencerId),
  ],
);
```

判断编号是否启用时，只查 `account_group_numbering`。不能用 `account_group_numbers` 是否有行判断，因为空分组也可能处于编号模式。

手写迁移 `apps/web/drizzle/manual/0008_account_group_numbering.sql` 内容：

```sql
CREATE TABLE IF NOT EXISTS account_group_numbering (
  group_id integer PRIMARY KEY REFERENCES account_groups(id) ON DELETE CASCADE,
  enabled_at timestamptz NOT NULL DEFAULT now(),
  regenerated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_group_numbers (
  group_id integer NOT NULL REFERENCES account_group_numbering(group_id) ON DELETE CASCADE,
  influencer_id integer NOT NULL REFERENCES influencers(id) ON DELETE CASCADE,
  display_no integer NOT NULL,
  sort_label text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (group_id, influencer_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS account_group_numbers_group_display_no_uq
  ON account_group_numbers (group_id, display_no);

CREATE INDEX IF NOT EXISTS account_group_numbers_influencer_idx
  ON account_group_numbers (influencer_id);
```

## 4. 文件修改地图

| 文件 | 职责 | 操作 |
| --- | --- | --- |
| `apps/web/lib/db/schema.ts` | 定义编号状态表和编号明细表 | 修改 |
| `apps/web/drizzle/manual/0008_account_group_numbering.sql` | 生产可执行的幂等 SQL 迁移 | 新建 |
| `apps/web/lib/actions/account-revalidation.ts` | 抽出账号页 revalidate helper | 新建 |
| `apps/web/lib/actions/account-numbering.ts` | 编号生成、取消、状态查询、重编 | 新建 |
| `apps/web/lib/actions/accounts.ts` | 在新增、删除、移动账号和修改 alias 后自动重编原本已编号的分组 | 修改 |
| `apps/web/lib/queries/influencers.ts` | join 展示编号，并在编号启用时按编号排序 | 修改 |
| `apps/web/lib/api/accounts.ts` | API 类型增加 `numberingActive` 和 `displayNumber` | 修改 |
| `apps/web/app/api/accounts/route.ts` | 返回编号状态和每行展示编号 | 修改 |
| `apps/web/components/accounts-panel.tsx` | 把 API 编号状态传给表格 | 修改 |
| `apps/web/components/accounts-table-form.tsx` | `ID` 表头触发、绿色编号徽标展示 | 修改 |
| `apps/web/messages/en.json` | 英文文案 | 修改 |
| `apps/web/messages/zh.json` | 中文文案 | 修改 |
| `apps/web/test/account-group-numbering-source.test.mjs` | 锁定边界的源码级回归测试 | 新建 |

## 5. 后端接口

新建 `apps/web/lib/actions/account-numbering.ts`，导出：

```ts
export async function toggleGroupAccountNumbers(
  groupId: number,
): Promise<{ ok: true; active: boolean } | { error: string }>;

export async function hasGroupAccountNumbers(groupId: number): Promise<boolean>;

export async function listNumberedGroupIds(groupIds: number[]): Promise<number[]>;

export async function regenerateAccountNumbersForGroups(groupIds: number[]): Promise<void>;
```

职责：

- `toggleGroupAccountNumbers()`：给表头点击用。已启用则取消；未启用则启用并生成编号。
- `hasGroupAccountNumbers()`：给 API 判断当前分组是否启用编号。
- `listNumberedGroupIds()`：账号变更前快照哪些分组原本已编号。
- `regenerateAccountNumbersForGroups()`：账号变更后重编这些分组。

重编流程必须在事务中完成：

```text
确认分组处于编号模式
-> 删除该 group_id 的旧 account_group_numbers 明细
-> 读取该 group_id 当前全部账号
-> 用 formatAccountListLabel() 排序
-> 写入 displayNo = index + 1
-> 更新 account_group_numbering.regenerated_at
```

空分组重编时，状态表保留，明细表为空。

## 6. API 形状

`apps/web/lib/api/accounts.ts` 增加：

```ts
export interface AccountsApiResponse {
  accounts: AccountRow[];
  platformCounts: Partial<Record<Platform, number>>;
  expandedVideos: Record<number, AccountVideoRow[]>;
  numberingActive: boolean;
}

export interface AccountRow {
  id: number;
  displayNumber: number | null;
  // 其他现有字段保持不变
}
```

`apps/web/app/api/accounts/route.ts` 在有 `groupId` 时读取：

```ts
const numberingActive =
  groupId !== undefined ? await hasGroupAccountNumbers(groupId) : false;
```

调用账号查询时传：

```ts
useGroupNumbering: numberingActive,
```

返回每行时传：

```ts
displayNumber: a.displayNumber,
```

响应 body 顶层也必须返回：

```ts
numberingActive,
```

## 7. 查询排序规则

`apps/web/lib/queries/influencers.ts` 给 `listInfluencersWithRecentCount()` 增加参数：

```ts
useGroupNumbering?: boolean;
```

查询里必须同时处理“有分组”和“无分组”两种 SQL 形态，避免无分组页面 SELECT 一个不存在的 `agn` 别名。

```ts
const numberingJoin =
  opts.groupId !== undefined
    ? sql`LEFT JOIN account_group_numbers agn ON agn.group_id = i.group_id AND agn.influencer_id = i.id`
    : sql``;

const displayNumberSql =
  opts.groupId !== undefined
    ? sql`agn.display_no AS display_number,`
    : sql`NULL::int AS display_number,`;

const orderSql = opts.useGroupNumbering
  ? sql`ORDER BY agn.display_no ASC NULLS LAST, i.id ASC`
  : sql`ORDER BY i.id ASC`;
```

有分组时 join 编号明细：

```sql
LEFT JOIN account_group_numbers agn
  ON agn.group_id = i.group_id
 AND agn.influencer_id = i.id
```

SELECT 增加条件片段：

```ts
${displayNumberSql}
```

FROM 和 ORDER BY 位置使用片段：

```ts
${numberingJoin}
${whereSql}
${orderSql}
```

这样编号启用时按展示编号排；未启用时保持原来的源 ID 顺序。

## 8. 账号变更后的自动重编

修改 `apps/web/lib/actions/accounts.ts`，真实函数名以仓库为准：

- `deleteAccount(id)`
- `bulkAddAccounts(accounts, groupId)`
- `moveAccountToGroup(accountId, groupId)`
- `moveAccountsToGroup(formData)`
- `setAccountAlias(accountId, raw)`

统一模式：

```text
变更前读取受影响 groupId
-> listNumberedGroupIds([...groupIds]) 快照已编号分组
-> 执行新增/删除/移动
-> regenerateAccountNumbersForGroups(numberedGroupIds)
-> revalidateAccounts(...)
```

具体要求：

- 删除账号：快照账号原分组；删除后重编原本已编号的原分组。
- 新增账号：快照目标分组；只有确实插入新账号后才重编。
- 单个移动：快照旧分组和新分组；移动后只重编其中原本已编号的分组。
- 批量移动：快照所有旧分组和目标分组；移动后只重编其中原本已编号的分组。
- 修改 alias：保存成功前读取账号所在分组；保存成功后，如果该分组原本已编号，则重编该分组。

## 9. 前端交互

`apps/web/components/accounts-panel.tsx` 把 API 字段传给表格：

```tsx
numberingActive={data?.numberingActive ?? false}
```

`apps/web/components/accounts-table-form.tsx`：

```ts
const canToggleNumbering = parsed.group !== undefined && parsed.platform === 'all';
```

可触发时，`ID` 表头渲染为 button：

```tsx
<button
  type="button"
  onClick={toggleNumbering}
  disabled={isPending}
  aria-pressed={numberingActive}
  title={numberingActive ? t('clearNumbering') : t('generateNumbering')}
>
  ID
</button>
```

不可触发时，`ID` 只是普通文本，并通过 title 说明只能在具体分组 All 分类中修改编号。

编号徽标：

```ts
const hasDisplayNumber = numberingActive && a.displayNumber !== null;
```

样式使用浅绿色，和原源 ID 灰色徽标区分：

```tsx
hasDisplayNumber
  ? 'border-[hsl(142_55%_70%)] bg-[hsl(142_76%_94%)] text-[hsl(142_70%_28%)]'
  : 'border-[color:var(--color-border)] bg-[color:var(--color-muted)] text-[color:var(--color-muted-foreground)]'
```

显示：

```tsx
#{hasDisplayNumber ? a.displayNumber : a.id}
```

源 ID 仍可放在 tooltip：

```tsx
title={hasDisplayNumber ? `Source ID #${a.id}` : undefined}
```

## 10. 文案

`apps/web/messages/en.json` 的 `accountsTable` 增加：

```json
"generateNumbering": "Generate group numbering",
"clearNumbering": "Clear group numbering",
"numberingUnavailable": "Group numbering can only be changed from a group's All platform view"
```

`apps/web/messages/zh.json` 的 `accountsTable` 增加：

```json
"generateNumbering": "生成分组编号",
"clearNumbering": "取消分组编号",
"numberingUnavailable": "只能在具体分组的 All 分类中修改编号"
```

## 11. 推荐执行顺序

- [ ] **任务 1：加源码级测试**
  新建 `apps/web/test/account-group-numbering-source.test.mjs`，锁定表结构、action 导出、API 字段、查询排序、UI 触发边界。先运行并确认失败：

  测试文件内容：

  ```js
  import assert from 'node:assert/strict';
  import { readFile } from 'node:fs/promises';
  import test from 'node:test';

  const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');

  test('account group numbering has persistent state and detail tables', async () => {
    const [schema, migration] = await Promise.all([
      read('../lib/db/schema.ts'),
      read('../drizzle/manual/0008_account_group_numbering.sql'),
    ]);

    assert.match(schema, /primaryKey/);
    assert.match(schema, /accountGroupNumbering\s*=\s*pgTable\(\s*'account_group_numbering'/);
    assert.match(schema, /accountGroupNumbers\s*=\s*pgTable\(\s*'account_group_numbers'/);
    assert.match(schema, /references\(\(\)\s*=>\s*accountGroupNumbering\.groupId/);
    assert.match(migration, /CREATE TABLE IF NOT EXISTS account_group_numbering/);
    assert.match(migration, /CREATE TABLE IF NOT EXISTS account_group_numbers/);
    assert.match(migration, /PRIMARY KEY \(group_id, influencer_id\)/);
    assert.match(migration, /ON DELETE CASCADE/);
  });

  test('account numbering actions expose toggle, detect, list, and regenerate helpers', async () => {
    const src = await read('../lib/actions/account-numbering.ts');

    assert.match(src, /export async function toggleGroupAccountNumbers/);
    assert.match(src, /export async function hasGroupAccountNumbers/);
    assert.match(src, /export async function listNumberedGroupIds/);
    assert.match(src, /export async function regenerateAccountNumbersForGroups/);
    assert.match(src, /accountGroupNumbering/);
    assert.match(src, /formatAccountListLabel/);
    assert.match(src, /Intl\.Collator/);
    assert.match(src, /db\.transaction/);
  });

  test('account mutations regenerate only groups that were already numbered', async () => {
    const src = await read('../lib/actions/accounts.ts');

    assert.match(src, /listNumberedGroupIds/);
    assert.match(src, /regenerateAccountNumbersForGroups/);
    assert.match(src, /deleteAccount/);
    assert.match(src, /bulkAddAccounts/);
    assert.match(src, /moveAccountToGroup/);
    assert.match(src, /moveAccountsToGroup/);
    assert.match(src, /setAccountAlias/);
  });

  test('accounts API exposes numbering state and display numbers', async () => {
    const [types, route] = await Promise.all([
      read('../lib/api/accounts.ts'),
      read('../app/api/accounts/route.ts'),
    ]);

    assert.match(types, /numberingActive:\s*boolean/);
    assert.match(types, /displayNumber:\s*number\s*\|\s*null/);
    assert.match(route, /hasGroupAccountNumbers/);
    assert.match(route, /numberingActive/);
    assert.match(route, /displayNumber:\s*a\.displayNumber/);
  });

  test('account query safely joins and orders by display number only when numbering is active', async () => {
    const src = await read('../lib/queries/influencers.ts');

    assert.match(src, /useGroupNumbering\?:\s*boolean/);
    assert.match(src, /numberingJoin/);
    assert.match(src, /displayNumberSql/);
    assert.match(src, /NULL::int AS display_number/);
    assert.match(src, /LEFT JOIN account_group_numbers/);
    assert.match(src, /ORDER BY agn\.display_no ASC NULLS LAST,\s*i\.id ASC/);
    assert.match(src, /ORDER BY i\.id ASC/);
  });

  test('ID header toggles numbering only on a concrete group All view', async () => {
    const src = await read('../components/accounts-table-form.tsx');

    assert.match(src, /canToggleNumbering/);
    assert.match(src, /parsed\.group !== undefined && parsed\.platform === 'all'/);
    assert.match(src, /toggleGroupAccountNumbers/);
    assert.match(src, /numberingActive/);
    assert.match(src, /displayNumber/);
    assert.match(src, /bg-\[hsl\(142_76%_94%\)\]/);
  });
  ```

  ```bash
  corepack pnpm --filter web test -- test/account-group-numbering-source.test.mjs
  ```

- [ ] **任务 2：加数据库 schema 和手写迁移**
  修改 `apps/web/lib/db/schema.ts`，新建 `apps/web/drizzle/manual/0008_account_group_numbering.sql`。本地验证：

  `apps/web/lib/db/schema.ts` 的 `drizzle-orm/pg-core` import 必须增加：

  ```ts
  primaryKey,
  ```

  ```bash
  corepack pnpm --filter web test -- test/account-group-numbering-source.test.mjs
  corepack pnpm --filter web db:push
  ```

- [ ] **任务 3：抽出 revalidate helper**
  新建 `apps/web/lib/actions/account-revalidation.ts`，让账号 action 和编号 action 共用 `revalidateAccounts()`。验证：

  ```bash
  corepack pnpm --filter web test
  ```

- [ ] **任务 4：实现编号 action**
  新建 `apps/web/lib/actions/account-numbering.ts`，实现 toggle、has、list、regenerate。验证 focused test。

- [ ] **任务 5：账号变更自动重编**
  修改 `apps/web/lib/actions/accounts.ts`，在 `deleteAccount`、`bulkAddAccounts`、`moveAccountToGroup`、`moveAccountsToGroup`、`setAccountAlias` 中加入“变更前快照、变更后重编”。

- [ ] **任务 6：扩展查询和 API**
  修改 query、API 类型、route，让接口返回 `numberingActive` 和 `displayNumber`。

- [ ] **任务 7：实现前端触发和展示**
  修改 panel、table、i18n。只有具体分组 All 页的 `ID` 是按钮；编号启用后用浅绿色徽标显示展示编号。

- [ ] **任务 8：本地手动验收**
  用 `/zh?group=<groupId>`、`/zh?group=<groupId>&platform=tiktok`、`/zh`、`/zh?platform=tiktok` 分别验证触发边界和继承效果。

- [ ] **任务 9：最终验证**

  ```bash
  corepack pnpm --filter web test -- test/account-group-numbering-source.test.mjs
  corepack pnpm --filter web test -- test/i18n-messages.test.mjs
  corepack pnpm --filter web test
  corepack pnpm --filter web typecheck
  corepack pnpm --filter web build
  ```

## 12. 手动验收标准

具体分组 All 页：

- 点击 `ID` 第一次生成编号。
- 行徽标变浅绿色。
- 显示 `#1`、`#2`、`#3`。
- 排序按展示编号。
- 业务动作仍然使用源 ID。

同一分组平台筛选页：

- `ID` 表头不触发开关。
- 显示同一套绿色编号。
- 编号可能不连续，这是正确的。

再次回到具体分组 All 页：

- 再点 `ID` 取消编号。
- 绿色徽标消失。
- 显示恢复源 ID。
- 排序恢复源 ID 顺序。

账号变更：

- 已编号分组新增账号后自动重编。
- 已编号分组移出或删除账号后自动重编。
- 已编号分组内账号 alias 修改后自动重编。
- 未编号分组不会因为账号进入或离开自动开启编号。
- 已编号分组变空后仍保留编号模式；之后有账号进入时会生成新编号。

## 13. 部署说明

生产先执行手写 SQL：

```bash
psql "$DATABASE_URL" -f apps/web/drizzle/manual/0008_account_group_numbering.sql
```

再部署 Web。

部署后默认所有分组都未编号；只有用户在具体分组 All 页点击 `ID` 后，该分组才进入编号模式。

## 14. 可维护性判断

- 影响范围集中在账号管理链路：数据库 schema、server action、账号查询/API、Account Management 前端表格。
- 源 ID 不变，风险主要在展示层和排序层，不会改动抓取任务或账号真实身份。
- 两张表比单张明细表多一点 schema，但解决了空分组状态丢失问题，后续“手动重新编号”“拖拽排序”也更容易扩展。
- 生成和重编都放在 `account-numbering.ts`，账号增删移动只调用 helper，不分散排序规则。
- 平台筛选只继承，不生成新编号，用户心智更稳定。

## 15. 执行前再次确认

当前文档只是在计划层校对和收口，不代表功能已经实现。恢复执行时，先从任务 1 的源码级测试开始，再按任务 2 到任务 9 顺序推进。
