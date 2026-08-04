import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {test} from 'node:test';

const messagesDir = path.resolve(import.meta.dirname, '..', 'messages');

async function readMessages(locale) {
  const raw = await readFile(path.join(messagesDir, `${locale}.json`), 'utf8');
  return JSON.parse(raw);
}

function flattenKeys(value, prefix = '') {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.entries(value).flatMap(([key, child]) =>
      flattenKeys(child, prefix ? `${prefix}.${key}` : key),
    );
  }

  return [prefix];
}

test('English and Chinese message catalogs expose the same keys', async () => {
  const [en, zh] = await Promise.all([readMessages('en'), readMessages('zh')]);

  assert.deepEqual(flattenKeys(en).sort(), flattenKeys(zh).sort());
});
