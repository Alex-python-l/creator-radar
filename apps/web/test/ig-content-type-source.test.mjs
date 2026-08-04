import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const schema = readFileSync(new URL('../lib/db/schema.ts', import.meta.url), 'utf8');
const migration = readFileSync(
  new URL('../drizzle/manual/0005_videos_content_type.sql', import.meta.url),
  'utf8',
);
const scraperModel = readFileSync(
  new URL('../../scraper/app/db/models.py', import.meta.url),
  'utf8',
);
const scrapeHandler = readFileSync(
  new URL('../../scraper/app/handlers/scrape.py', import.meta.url),
  'utf8',
);
const tiktokScraper = readFileSync(
  new URL('../../scraper/app/scrapers/tiktok.py', import.meta.url),
  'utf8',
);
const videosQuery = readFileSync(new URL('../lib/queries/videos.ts', import.meta.url), 'utf8');
const influencersQuery = readFileSync(
  new URL('../lib/queries/influencers.ts', import.meta.url),
  'utf8',
);
const accountsApi = readFileSync(new URL('../lib/api/accounts.ts', import.meta.url), 'utf8');
const accountsRoute = readFileSync(
  new URL('../app/api/accounts/route.ts', import.meta.url),
  'utf8',
);
const accountsPanel = readFileSync(
  new URL('../components/accounts-panel.tsx', import.meta.url),
  'utf8',
);
const videosTable = readFileSync(new URL('../components/videos-table.tsx', import.meta.url), 'utf8');
const accountsTable = readFileSync(
  new URL('../components/accounts-table-form.tsx', import.meta.url),
  'utf8',
);
const zh = readFileSync(new URL('../messages/zh.json', import.meta.url), 'utf8');
const en = readFileSync(new URL('../messages/en.json', import.meta.url), 'utf8');

test('videos schema and manual migration define content type', () => {
  assert.match(schema, /contentType:\s*text\('content_type'\)\.notNull\(\)\.default\('video'\)/);
  assert.match(migration, /ADD COLUMN IF NOT EXISTS content_type text NOT NULL DEFAULT 'video'/);
});

test('scraper model and upsert persist content type', () => {
  assert.match(scraperModel, /content_type: Mapped\[str\]/);
  assert.match(scrapeHandler, /content_type=v\.content_type/);
  assert.match(scrapeHandler, /"content_type": v\.content_type/);
  assert.match(tiktokScraper, /content_type: str = "video"/);
});

test('web queries and accounts API expose content type', () => {
  assert.match(videosQuery, /contentType:\s*string;/);
  assert.match(videosQuery, /contentType:\s*videos\.contentType/);

  assert.match(influencersQuery, /content_type/);
  assert.match(influencersQuery, /contentType:\s*r\.content_type/);

  assert.match(accountsApi, /contentType:\s*string;/);

  assert.match(accountsRoute, /contentType:\s*v\.contentType/);

  assert.match(accountsPanel, /contentType:\s*string;/);
});

test('web UI renders content type labels and content wording', () => {
  assert.match(videosTable, /ContentTypeBadge/);
  assert.match(videosTable, /isPlayableContent/);
  assert.match(accountsTable, /ContentTypeBadge/);
  assert.match(zh, /"contentType"/);
  assert.match(zh, /"content": "内容"/);
  assert.match(en, /"contentType"/);
  assert.match(en, /"content": "Content"/);
});
