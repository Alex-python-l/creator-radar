import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

test('accounts API caps expanded accounts and per-account videos', async () => {
  const route = await readFile(
    new URL('../app/api/accounts/route.ts', import.meta.url),
    'utf8',
  );
  const queries = await readFile(
    new URL('../lib/queries/influencers.ts', import.meta.url),
    'utf8',
  );

  assert.match(route, /MAX_EXPANDED_ACCOUNTS\s*=\s*20/);
  assert.match(route, /EXPANDED_VIDEOS_PER_ACCOUNT\s*=\s*20/);
  assert.match(route, /perAccountLimit:\s*EXPANDED_VIDEOS_PER_ACCOUNT/);
  assert.match(queries, /row_number\(\) OVER/);
  assert.match(queries, /PARTITION BY influencer_id/);
  assert.match(queries, /WHERE rn <=/);
});

test('account active-job query ignores malformed job payloads before casting', async () => {
  const src = await readFile(
    new URL('../lib/queries/influencers.ts', import.meta.url),
    'utf8',
  );
  const regexIdx = src.indexOf("payload->>'influencerId' ~ '^[0-9]+$'");
  const castIdx = src.indexOf("(payload->>'influencerId')::int = i.id");

  assert.ok(regexIdx >= 0, 'active job lookup must validate JSON payload before cast');
  assert.ok(castIdx > regexIdx, 'regex guard must appear before integer cast');
});

test('account fetch enqueue dedup is protected by a transaction-scoped advisory lock', async () => {
  const src = await readFile(
    new URL('../lib/actions/accounts.ts', import.meta.url),
    'utf8',
  );
  const lockIdx = src.indexOf('pg_advisory_xact_lock');
  const selectIdx = src.indexOf('.select({ id: jobs.id })');
  const insertIdx = src.indexOf('.insert(jobs)');

  assert.match(src, /db\.transaction/);
  assert.ok(lockIdx >= 0, 'enqueueFetch must take an advisory lock');
  assert.ok(lockIdx < selectIdx, 'lock must be acquired before active-job lookup');
  assert.ok(selectIdx < insertIdx, 'active-job lookup must still happen before insert');
});

test('bulk selection clears when account filters change', async () => {
  const src = await readFile(
    new URL('../components/accounts-table-form.tsx', import.meta.url),
    'utf8',
  );

  // Reset is now driven by a render-time filterKey comparison (React 19
  // set-state-in-effect guidance) instead of a useEffect dependency array,
  // but it must still key off all three filters and still clear selection.
  assert.match(src, /setSelected\(new Set\(\)\)/);
  assert.match(src, /\$\{parsed\.group[^}]*\}\|\$\{parsed\.platform\}\|\$\{parsed\.range\}/);
});

test('import dialog resets target group from current page each time it opens', async () => {
  const src = await readFile(
    new URL('../components/import-dialog.tsx', import.meta.url),
    'utf8',
  );
  const openIdx = src.indexOf('const open = () =>');
  const resetIdx = src.indexOf('setTargetGroupId(defaultGroupId)', openIdx);

  assert.ok(openIdx >= 0);
  assert.ok(resetIdx > openIdx, 'opening the dialog must reset to current defaultGroupId');
});

test('scraper calls are production-hardened', async () => {
  // Radar 不再自持登录 cookie(退役,见 lib/studio-session.ts) —
  // 这里只保留和本测试真正相关的 scraper 内部 token 校验。
  const scraperClient = await readFile(
    new URL('../lib/scraper-client.ts', import.meta.url),
    'utf8',
  );

  assert.match(scraperClient, /SCRAPER_INTERNAL_TOKEN/);
  assert.match(scraperClient, /headers\['x-internal-token'\]/);
});

test('web lint script uses eslint cli instead of deprecated next lint', async () => {
  const pkg = await readFile(new URL('../package.json', import.meta.url), 'utf8');
  const eslintConfig = await readFile(new URL('../eslint.config.mjs', import.meta.url), 'utf8');

  assert.match(pkg, /"lint":\s*"eslint \."/);
  assert.doesNotMatch(pkg, /next lint/);
  assert.match(eslintConfig, /next\/core-web-vitals/);
  assert.match(eslintConfig, /next\/typescript/);
});

test('historical reserved-table cleanup cannot drop the live users table', async () => {
  const migration = await readFile(
    new URL('../drizzle/manual/0006_drop_reserved_tables.sql', import.meta.url),
    'utf8',
  );

  assert.match(migration, /users_id_type IS DISTINCT FROM 'text'/);
  assert.match(migration, /refusing to drop non-empty legacy users placeholder/);
  assert.doesNotMatch(migration, /^DROP TABLE IF EXISTS users;/m);
});

test('authenticated app shell and monitor flow remain usable on narrow screens', async () => {
  const layout = await readFile(
    new URL('../app/[locale]/(app)/layout.tsx', import.meta.url),
    'utf8',
  );
  const monitor = await readFile(
    new URL('../app/[locale]/(app)/monitor/page.tsx', import.meta.url),
    'utf8',
  );
  const groups = await readFile(
    new URL('../components/groups-sidebar.tsx', import.meta.url),
    'utf8',
  );

  assert.match(layout, /flex min-h-screen flex-col[^"]*md:flex-row/);
  assert.match(layout, /w-full[^"]*md:w-\[232px\]/);
  assert.match(monitor, /flex flex-col gap-5 lg:flex-row/);
  assert.match(groups, /w-full[^"]*lg:w-40/);
});

test('production responses carry baseline browser security headers', async () => {
  const config = await readFile(
    new URL('../next.config.ts', import.meta.url),
    'utf8',
  );

  for (const header of [
    'Strict-Transport-Security',
    'X-Content-Type-Options',
    'X-Frame-Options',
    'Referrer-Policy',
    'Permissions-Policy',
  ]) {
    assert.match(config, new RegExp(header));
  }
  assert.match(config, /source:\s*'\/:path\*'/);
});

test('production container builds require an explicit release commit', async () => {
  const webCompose = await readFile(
    new URL('../../../docker-compose.cvm.yml', import.meta.url),
    'utf8',
  );
  const webDockerfile = await readFile(
    new URL('../Dockerfile', import.meta.url),
    'utf8',
  );
  const scraperCompose = await readFile(
    new URL('../../scraper/docker-compose.yml', import.meta.url),
    'utf8',
  );
  const scraperDockerfile = await readFile(
    new URL('../../scraper/Dockerfile', import.meta.url),
    'utf8',
  );

  for (const source of [webCompose, scraperCompose]) {
    assert.match(source, /RELEASE_COMMIT:\s*\\?\$\{RELEASE_COMMIT:\?/);
  }
  for (const source of [webDockerfile, scraperDockerfile]) {
    assert.match(source, /org\.opencontainers\.image\.revision/);
  }
});
