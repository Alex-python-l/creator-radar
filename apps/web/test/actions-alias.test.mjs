import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const src = await readFile(
  new URL('../lib/actions/accounts.ts', import.meta.url),
  'utf8',
);

test('setAccountAlias is exported as a server action', () => {
  assert.match(src, /export async function setAccountAlias\(/);
});

test('setAccountAlias trims input and treats empty string as null clear', () => {
  // 必须 trim 输入并把空字符串映射为 null
  assert.match(src, /\.trim\(\)/);
  // null fallback —— "trimmed || null" 或 "trimmed.length === 0" 写法都行,这里只要见到 null 即可
  assert.match(src, /alias:\s*\w+\s*\|\|\s*null|alias:\s*null/);
});

test('setAccountAlias enforces 1-50 length on non-empty input', () => {
  assert.match(src, /alias too long/);
  assert.match(src, /\b50\b/);
});

test('setAccountAlias maps PG unique_violation to alias duplicate error', () => {
  assert.match(src, /23505/);
  assert.match(src, /alias duplicate/);
});

test('setAccountAlias calls revalidateAccounts after update', () => {
  // 复用现有 revalidateAccounts(id) helper
  assert.match(src, /revalidateAccounts\(\s*\w+\s*\)/);
});

const errSrc = await readFile(
  new URL('../lib/i18n-errors.ts', import.meta.url),
  'utf8',
);

test('translateActionError covers alias too long and alias duplicate', () => {
  assert.match(errSrc, /'alias too long'/);
  assert.match(errSrc, /aliasTooLong/);
  assert.match(errSrc, /'alias duplicate'/);
  assert.match(errSrc, /aliasDuplicate/);
});
