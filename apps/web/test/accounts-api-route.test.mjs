import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// 这个测试不打 DB,只静态检查鉴权路径和 query string parsing 逻辑
const src = await readFile(
  new URL('../app/api/accounts/route.ts', import.meta.url),
  'utf8',
);

test('GET /api/accounts 鉴权走 better-auth 会话(未登录 → 401)', () => {
  // 确认 route 复用 getViewer(better-auth get-session,与页面同一套),而非自持校验
  assert.match(
    src,
    /import\s*\{\s*getViewer\s*\}\s*from\s*'@\/lib\/auth\/viewer'/,
    'route must import getViewer from @/lib/auth/viewer',
  );
  assert.match(
    src,
    /if\s*\(!viewer\)\s*\{[\s\S]*?status:\s*401/,
    'route must return 401 when there is no viewer',
  );
});

test('GET /api/accounts 鉴权发生在业务逻辑之前', () => {
  // 确认 getViewer 调用在任何 DB 查询(listInfluencersWithRecentCount)之前
  const verifyCallIdx = src.indexOf('getViewer(');
  const queryCallIdx = src.indexOf('listInfluencersWithRecentCount(');
  assert.ok(verifyCallIdx > -1, 'route must call getViewer');
  assert.ok(queryCallIdx > -1, 'route must call listInfluencersWithRecentCount');
  assert.ok(
    verifyCallIdx < queryCallIdx,
    'getViewer call must come before business logic (DB queries)',
  );
});

test('GET /api/accounts 不再引用旧口令门(auth-token)', () => {
  assert.doesNotMatch(src, /verifyToken|AUTH_COOKIE|auth-token|AUTH_SECRET/, 'route must not reference the retired password-gate module');
});

test('GET /api/accounts exposes derived accountStatus', async () => {
  const apiTypes = await readFile(
    new URL('../lib/api/accounts.ts', import.meta.url),
    'utf8',
  );

  assert.match(
    apiTypes,
    /accountStatus:\s*'active'\s*\|\s*'gone'\s*\|\s*'unknown'/,
    'AccountRow must expose accountStatus for API consumers',
  );
  assert.match(
    src,
    /accountStatus:\s*deriveAccountStatus\(/,
    'route must derive accountStatus from latest fetch state',
  );
});

test('GET /api/accounts 解析 relation 参数且未知值回落 own', () => {
  assert.match(
    src,
    /raw === 'competitor'\s*\?\s*'competitor'\s*:\s*'own'/,
    'pickRelation must fall back to own for unknown values',
  );
  assert.match(
    src,
    /listPlatformAccountCounts\(viewer,\s*groupId,\s*relation\)/,
    'relation must flow into platform counts',
  );
});

test('客户端 fetcher 的 queryKey/URL 携带 relation(两层缓存互不串)', async () => {
  const apiTypes = await readFile(
    new URL('../lib/api/accounts.ts', import.meta.url),
    'utf8',
  );
  assert.match(
    apiTypes,
    /'accounts',\s*\n\s*q\.relation \?\? 'own'/,
    'accountsQueryKey must include relation',
  );
  assert.match(
    apiTypes,
    /if \(q\.relation === 'competitor'\) p\.set\('relation', 'competitor'\)/,
    'buildAccountsUrl must serialize competitor relation',
  );
});
