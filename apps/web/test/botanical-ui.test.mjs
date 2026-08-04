import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';

async function read(path) {
  return readFile(new URL(`../${path}`, import.meta.url), 'utf8');
}

test('botanical accents stay decorative and reduced-motion safe', async () => {
  const component = await read('components/botanical-accent.tsx');
  const css = await read('app/globals.css');

  for (const variant of ['cluster', 'sprig', 'petals', 'meadow']) {
    assert.match(component, new RegExp(`${variant}:`));
  }
  assert.match(component, /alt=""/);
  assert.match(component, /aria-hidden="true"/);
  assert.match(component, /pointer-events-none/);
  assert.match(css, /prefers-reduced-motion:[\s\S]*botanical-drift[\s\S]*botanical-sway/);
});

test('the shared botanical system reaches public, auth, Monitor and Ops surfaces', async () => {
  const files = await Promise.all([
    read('app/[locale]/page.tsx'),
    read('components/login-form.tsx'),
    read('app/[locale]/apply/page.tsx'),
    read('app/[locale]/(app)/layout.tsx'),
    read('app/[locale]/(app)/monitor/page.tsx'),
    read('app/[locale]/(app)/studio/ops/page.tsx'),
  ]);

  for (const source of files) {
    assert.match(source, /BotanicalAccent/);
  }
});

test('workspace botanical accents remain clearly visible without entering content layers', async () => {
  const [layout, admin, monitor, ops] = await Promise.all([
    read('app/[locale]/(app)/layout.tsx'),
    read('app/[locale]/admin/layout.tsx'),
    read('app/[locale]/(app)/monitor/page.tsx'),
    read('app/[locale]/(app)/studio/ops/page.tsx'),
  ]);

  for (const opacity of ['0.24', '0.15', '0.12', '0.1']) {
    assert.match(layout, new RegExp(`opacity-\\[${opacity}\\]`));
    assert.match(admin, new RegExp(`opacity-\\[${opacity}\\]`));
  }
  for (const opacity of ['0.15', '0.14', '0.12']) {
    assert.match(monitor, new RegExp(`opacity-\\[${opacity}\\]`));
  }
  for (const opacity of ['0.14', '0.12']) {
    assert.match(ops, new RegExp(`opacity-\\[${opacity}\\]`));
  }
});
