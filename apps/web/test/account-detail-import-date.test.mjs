import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

test('account detail page displays account import date', async () => {
  const pageSrc = await readFile(
    new URL('../app/[locale]/(app)/accounts/[id]/page.tsx', import.meta.url),
    'utf8',
  );
  const zh = JSON.parse(await readFile(new URL('../messages/zh.json', import.meta.url), 'utf8'));
  const en = JSON.parse(await readFile(new URL('../messages/en.json', import.meta.url), 'utf8'));

  assert.equal(zh.accountDetail.importedAt, '导入日期：{date}');
  assert.equal(en.accountDetail.importedAt, 'Imported: {date}');
  assert.match(pageSrc, /t\('importedAt'/, 'detail page must render importedAt label');
  assert.match(pageSrc, /formatDate\(account\.createdAt,\s*locale\)/, 'detail page must format account.createdAt');
});
