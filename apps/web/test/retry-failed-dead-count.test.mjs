import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

test('retry failed badge counts retryable active accounts, not raw dead job rows', async () => {
  const src = await readFile(
    new URL('../lib/queries/influencers.ts', import.meta.url),
    'utf8',
  );

  assert.match(
    src,
    /latest_account_fetch_jobs/,
    'badge count must derive from latest account.fetch job per influencer',
  );
  assert.match(
    src,
    /COALESCE\(updated_at, created_at\)/,
    'latest job ordering must handle queued jobs where updated_at is null',
  );
  assert.match(
    src,
    /i\.is_active = true/,
    'deleted or inactive accounts must not keep the retry badge alive',
  );
  assert.doesNotMatch(
    src,
    /\.from\(jobs\)[\s\S]{0,240}\.where\(eq\(jobs\.status, 'dead'\)\)/,
    'counting all jobs.status=dead rows includes deleted accounts and stale dead history',
  );
});

test('retry action enqueues only latest-dead active accounts and retires stale dead rows', async () => {
  const src = await readFile(
    new URL('../lib/actions/accounts.ts', import.meta.url),
    'utf8',
  );

  assert.match(
    src,
    /latest_account_fetch_jobs/,
    'retry action must select accounts from each influencer latest job state',
  );
  assert.match(
    src,
    /latest_jobs\.status = 'dead'/,
    'only accounts whose latest job is dead should be enqueued',
  );
  assert.match(
    src,
    /deadJobIds/,
    'stale dead rows should still be retired so the badge does not stay inflated',
  );
  assert.doesNotMatch(
    src,
    /leftJoin\(\s*influencers[\s\S]*eq\(jobs\.status, 'dead'\)[\s\S]*eligible = rows\.filter/,
    'old implementation retried any active account with any historical dead row',
  );
});
