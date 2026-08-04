import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

async function readAppFile(path) {
  return readFile(new URL(`../${path}`, import.meta.url), 'utf8');
}

test('Radar shell keeps the primary navigation compact', async () => {
  const src = await readAppFile('app/[locale]/(app)/layout.tsx');

  // 桌面壳保持 232px；窄屏先占满宽度并与主内容纵向堆叠。
  assert.match(
    src,
    /w-full shrink-0 flex-col[^"]*md:h-screen[^"]*md:w-\[232px\]/,
    'unified left navigation should use the 232px workspace shell',
  );
  assert.doesNotMatch(src, /h-screen w-56 flex-col/, 'do not regress to the wider w-56 nav');
});

test('Radar group sidebar is wide enough for group labels', async () => {
  const sidebarSrc = await readAppFile('components/groups-sidebar.tsx');
  const pageSrc = await readAppFile('app/[locale]/(app)/monitor/page.tsx');

  assert.match(sidebarSrc, /<aside className="w-full flex-shrink-0[^"]*lg:w-40/);
  assert.match(pageSrc, /<aside className="w-full flex-shrink-0[^"]*lg:w-40/);
  assert.doesNotMatch(sidebarSrc, /w-\[100px\]/);
  assert.doesNotMatch(pageSrc, /w-\[100px\]/);
});

test('Radar platform filter cards show real platform logos and right-aligned account count', async () => {
  const pageSrc = await readAppFile('app/[locale]/(app)/monitor/page.tsx');
  const querySrc = await readAppFile('lib/queries/influencers.ts');
  const chipsSrc = await readAppFile('components/platform-filter-chips.tsx');
  const formatSrc = await readAppFile('components/format.tsx');
  const markSrc = formatSrc.slice(formatSrc.indexOf('export function PlatformMark'));

  assert.match(querySrc, /listPlatformAccountCounts/, 'platform counts should come from a dedicated grouped query');
  // platform filter は client-side AccountsPanel / PlatformFilterChips に移行済み
  assert.match(pageSrc, /AccountsPanel/, 'page should use AccountsPanel for client-side platform filter + table');
  assert.match(chipsSrc, /platformCounts/, 'platform filter chips should receive per-platform counts');
  assert.match(chipsSrc, /<PlatformMark platform=\{p\} \/>/, 'platform cards should render the platform mark');
  assert.match(chipsSrc, /ml-auto text-xs font-semibold tabular-nums/, 'account count should sit on the right side of each card');
  assert.match(markSrc, /<svg[\s\S]*viewBox="0 0 24 24"/, 'platform mark should render SVG logos');
  assert.match(markSrc, /path d=\{icon\.path\}/, 'platform mark should use real logo path data');
  assert.doesNotMatch(
    markSrc,
    /tiktok:\s*'TT'|instagram:\s*'IG'|facebook:\s*'f'|youtube:\s*'YT'/,
    'platform mark must not be text abbreviations pretending to be logos',
  );
});
