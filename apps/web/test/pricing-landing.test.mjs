import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';

async function read(path) {
  return readFile(new URL(`../${path}`, import.meta.url), 'utf8');
}

test('localized landing keeps exact monthly prices and routes every plan to apply', async () => {
  const page = await read('app/[locale]/page.tsx');
  const zh = JSON.parse(await read('messages/zh.json'));
  const en = JSON.parse(await read('messages/en.json'));

  assert.equal(zh.marketing.trialPrice, '免费');
  assert.equal(zh.marketing.proPrice, '¥699');
  assert.equal(zh.marketing.scalePrice, '¥1,999');
  assert.equal(en.marketing.trialPrice, 'Free');
  assert.equal(en.marketing.proPrice, '$99');
  assert.equal(en.marketing.scalePrice, '$299');
  assert.match(page, /\['trial', 'pro', 'scale', 'custom'\]/);
  assert.match(page, /href=\{applyHref\(plan\)\}/);
  assert.doesNotMatch(page, /annual|yearly|checkout|payment/i);
});

test('landing presents the supported Monitor and Ops product surfaces', async () => {
  const zh = JSON.parse(await read('messages/zh.json')).marketing;
  const en = JSON.parse(await read('messages/en.json')).marketing;
  const catalog = `${JSON.stringify(zh)}\n${JSON.stringify(en)}\n${await read('app/[locale]/page.tsx')}`;
  for (const platform of ['TikTok', 'Instagram', 'Facebook', 'YouTube']) {
    assert.match(catalog, new RegExp(platform));
  }
  for (const capability of ['Monitor', 'Ops', 'cloud phones', 'proxies', 'comment tasks']) {
    assert.match(catalog, new RegExp(capability, 'i'));
  }
  assert.match(catalog, /iris-ops-dashboard-demo\.png/);
  assert.doesNotMatch(catalog, /sentiment|情绪分析|实时告警|real-time alert|达人发现|influencer discovery|AI 分析/i);
});

test('new users and organizations default to zero monitor quota without rewriting existing rows', async () => {
  const schema = await read('lib/db/schema.ts');
  const env = await read('lib/env.ts');
  const migration = await read('drizzle/manual/0011_pricing_applications.sql');

  assert.equal((schema.match(/monitorQuota: integer\('monitor_quota'\)[^\n]*default\(0\)/g) ?? []).length, 2);
  assert.match(env, /DEFAULT_MONITOR_QUOTA:[\s\S]*nonnegative\(\)[\s\S]*default\(0\)/);
  assert.match(migration, /ALTER COLUMN monitor_quota SET DEFAULT 0/);
  assert.doesNotMatch(migration, /UPDATE\s+(?:users|organizations)\s+SET\s+monitor_quota/i);
});

test('public applications validate consent, contact, honeypot and admin-only quota grants', async () => {
  const actions = await read('lib/actions/trial-applications.ts');
  const form = await read('components/marketing/trial-application-form.tsx');
  const admin = await read('app/[locale]/admin/applications/page.tsx');

  assert.match(actions, /workEmail[\s\S]*wechat[\s\S]*contact_required/);
  assert.match(actions, /consent: z\.literal\('on'\)/);
  assert.match(actions, /website: z\.string\(\)\.max\(0\)/);
  assert.match(actions, /RATE_LIMIT_MAX = 5/);
  assert.match(actions, /await getAdminMutationContext\(\)/);
  assert.match(actions, /update\(organizations\)[\s\S]*monitorQuota: quota\.data/);
  assert.match(form, /name="website"/);
  assert.match(admin, /disabled=\{!application\.organizationId\}/);
});

test('public root and protected monitor routes stay separated', async () => {
  const middleware = await read('middleware.ts');
  const landing = await read('app/[locale]/page.tsx');
  const sidebar = await read('components/app-sidebar-nav.tsx');
  const product = await read('../../packages/shared/src/products.ts');

  assert.match(middleware, /PUBLIC_PATH_RE/);
  assert.match(landing, /redirect\(`\/\$\{locale\}\/monitor`/);
  assert.match(sidebar, /`\/\$\{locale\}\/monitor`/);
  assert.match(product, /path: '\/monitor'/);
});
