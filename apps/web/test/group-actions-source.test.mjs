import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

test('group sidebar actions are icon-only triggers', async () => {
  const [createSrc, manageSrc, sidebarSrc] = await Promise.all([
    readFile(new URL('../components/group-create-dialog.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/group-manage-dialog.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/groups-sidebar.tsx', import.meta.url), 'utf8'),
  ]);

  assert.match(createSrc, /import \{ Plus, X \} from 'lucide-react'/);
  assert.match(createSrc, /aria-label=\{t\('create'\)\}/);
  assert.match(createSrc, /<Plus className="h-4 w-4" aria-hidden \/>/);
  assert.doesNotMatch(createSrc, /<Button[^>]*>\s*\{t\('create'\)\}\s*<\/Button>/);

  assert.match(manageSrc, /import \{ Settings, X \} from 'lucide-react'/);
  assert.match(manageSrc, /aria-label=\{t\('manage'\)\}/);
  assert.match(manageSrc, /<Settings className="h-4 w-4" aria-hidden \/>/);
  assert.doesNotMatch(manageSrc, /<Button[^>]*>\s*\{t\('manage'\)\}\s*<\/Button>/);

  assert.match(sidebarSrc, /className="flex gap-1 border-t/);
});

test('new group opens as a modal instead of expanding inline', async () => {
  const src = await readFile(
    new URL('../components/group-create-dialog.tsx', import.meta.url),
    'utf8',
  );

  assert.match(src, /open && \(/);
  assert.match(src, /fixed inset-0 z-50 flex items-center justify-center bg-black\/40/);
  assert.doesNotMatch(src, /if \(!open\)/);
});
