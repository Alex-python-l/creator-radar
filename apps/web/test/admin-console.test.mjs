import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

async function source(path) {
  return readFile(new URL(`../${path}`, import.meta.url), 'utf8');
}

test('admin routes use a locale-preserving server-side admin gate', async () => {
  const [layout, viewer, middleware] = await Promise.all([
    source('app/[locale]/admin/layout.tsx'),
    source('lib/auth/viewer.ts'),
    source('middleware.ts'),
  ]);
  assert.match(layout, /requireAdminLocale\(locale\)/);
  assert.match(viewer, /if \(!viewer\.isAdmin\) redirect\(`\/\$\{normalizedLocale\}\/monitor`/);
  assert.match(viewer, /\/login\?next=/);
  assert.match(middleware, /const loginPath = locale \? `\/\$\{locale\}\/login` : '\/login'/);
});

test('admin queries expose aggregate usage without customer business records', async () => {
  const queries = await source('lib/admin/queries.ts');
  assert.match(queries, /COUNT\(\*\).*workspace_influencers/is);
  assert.match(queries, /\$\{gte\(users\.createdAt, sevenDaysAgo\)\}/);
  assert.doesNotMatch(queries, />= \$\{sevenDaysAgo\}/);
  assert.doesNotMatch(queries, /\binfluencers\b\s*[,}]/);
  assert.doesNotMatch(queries, /\bvideos\b\s*[,}]/);
  assert.doesNotMatch(queries, /studio\/|lib\/studio/);
});

test('admin mutations are audited and omit forbidden support powers', async () => {
  const [actions, applications, userDetail] = await Promise.all([
    source('lib/admin/actions.ts'),
    source('lib/actions/trial-applications.ts'),
    source('app/[locale]/admin/users/[id]/page.tsx'),
  ]);
  assert.match(actions, /getAdminMutationContext/);
  assert.match(actions, /adminAuditEntry/g);
  assert.match(actions, /cannot_ban_self/);
  assert.match(actions, /cannot_demote_self/);
  assert.match(actions, /last_active_admin/);
  assert.match(actions, /cannot_remove_owner/);
  assert.match(actions, /export async function deleteAdminUser/);
  assert.match(actions, /action: 'user\.delete'/);
  assert.match(actions, /cannot_delete_self/);
  assert.match(actions, /cannot_delete_owner/);
  assert.match(actions, /confirmation\.toLowerCase\(\) !== target\.email\.toLowerCase\(\)/);
  assert.match(actions, /tx\.delete\(users\)/);
  assert.match(userDetail, /action=\{deleteAdminUser\}/);
  assert.match(userDetail, /name="confirmation"/);
  assert.doesNotMatch(actions, /impersonate|setPassword|deleteOrganization/);
  assert.match(applications, /application\.status\.update/);
  assert.match(applications, /application\.quota\.grant/);
  assert.match(applications, /db\.transaction/);
});

test('audit migration is idempotent and stores only mutation summaries', async () => {
  const [migration, schema] = await Promise.all([
    source('drizzle/manual/0013_admin_audit_logs.sql'),
    source('lib/db/schema.ts'),
  ]);
  assert.match(migration, /CREATE TABLE IF NOT EXISTS admin_audit_logs/);
  assert.match(migration, /actor_user_id/);
  assert.match(migration, /before jsonb/);
  assert.match(migration, /after jsonb/);
  assert.match(schema, /export const adminAuditLogs/);
  assert.doesNotMatch(migration, /\b(password|session_token|credential|secret)\s+(text|jsonb)\b/i);
});

test('legacy applications route redirects to admin and preserves filters', async () => {
  const legacy = await source('app/[locale]/(app)/applications/page.tsx');
  assert.match(legacy, /admin\/applications/);
  assert.match(legacy, /query\.set\('status'/);
  assert.match(legacy, /query\.set\('q'/);
});
