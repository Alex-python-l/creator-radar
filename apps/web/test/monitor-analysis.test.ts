import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildMonitorAnalysisSnapshot,
  type MonitorAnalysisAccountInput,
  type MonitorAnalysisVideoInput,
} from '../lib/monitor-analysis.ts';

const now = new Date('2026-07-19T12:00:00.000Z');

function account(
  id: number,
  overrides: Partial<MonitorAnalysisAccountInput> = {},
): MonitorAnalysisAccountInput {
  return {
    id,
    platform: 'tiktok',
    username: `account-${id}`,
    displayName: null,
    alias: null,
    subscriptionCreatedAt: new Date('2026-07-01T00:00:00.000Z'),
    latestFetchStatus: 'success',
    latestFetchStartedAt: new Date('2026-07-19T04:00:00.000Z'),
    latestFetchFinishedAt: new Date('2026-07-19T04:01:00.000Z'),
    latestFetchError: null,
    latestFetchErrorCode: null,
    latestSuccessAt: new Date('2026-07-19T04:01:00.000Z'),
    latestJobStatus: 'success',
    latestJobError: null,
    latestJobAt: new Date('2026-07-19T04:01:00.000Z'),
    ...overrides,
  };
}

function video(
  id: number,
  influencerId: number,
  playCount: number,
  overrides: Partial<MonitorAnalysisVideoInput> = {},
): MonitorAnalysisVideoInput {
  return {
    id,
    influencerId,
    platform: 'tiktok',
    contentType: 'video',
    url: `https://example.com/${id}`,
    description: `Video ${id}`,
    playCount,
    likeCount: 10,
    commentCount: 2,
    shareCount: 1,
    publishedAt: new Date('2026-07-17T12:00:00.000Z'),
    ...overrides,
  };
}

test('classifies current issues, processing and recovery without false silent accounts', () => {
  const accounts = [
    account(1, {
      latestFetchStatus: 'failed',
      latestFetchErrorCode: 'rate_limited',
      latestFetchError: 'try later',
      latestSuccessAt: new Date('2026-07-17T00:00:00.000Z'),
    }),
    account(2, {
      latestSuccessAt: new Date('2026-07-17T00:00:00.000Z'),
    }),
    account(3, {
      latestFetchStatus: null,
      latestSuccessAt: null,
      latestJobStatus: null,
    }),
    account(4, { latestJobStatus: 'queued' }),
    account(5, { latestJobStatus: 'dead', latestJobError: 'retry exhausted' }),
    account(6),
    account(7),
    account(8, {
      latestJobStatus: 'dead',
      latestJobAt: new Date('2026-07-18T00:00:00.000Z'),
      latestSuccessAt: new Date('2026-07-19T04:01:00.000Z'),
    }),
  ];
  const snapshot = buildMonitorAnalysisSnapshot({
    now,
    platform: 'all',
    range: '7d',
    accounts,
    fetches: [
      {
        influencerId: 6,
        status: 'failed',
        errorCode: 'network_error',
        errorMessage: 'timeout',
        startedAt: new Date('2026-07-18T00:00:00.000Z'),
        finishedAt: new Date('2026-07-18T00:01:00.000Z'),
      },
      {
        influencerId: 6,
        status: 'success',
        errorCode: null,
        errorMessage: null,
        startedAt: new Date('2026-07-19T04:00:00.000Z'),
        finishedAt: new Date('2026-07-19T04:01:00.000Z'),
      },
    ],
    videos: [video(1, 7, 100)],
  });

  assert.deepEqual(
    snapshot.health.currentIssues.map((item) => [item.id, item.reason]),
    [[1, 'failed'], [2, 'stale'], [3, 'never_fetched'], [5, 'dead']],
  );
  assert.deepEqual(snapshot.health.processing.map((item) => item.id), [4]);
  assert.deepEqual(snapshot.health.recovered.map((item) => item.id), [6, 8]);
  assert.deepEqual(snapshot.publishing.silentAccounts.map((item) => item.id), [6, 8]);
  assert.deepEqual(snapshot.publishing.uncertainAccounts.map((item) => item.id), [1, 2, 3, 4, 5]);
  assert.equal(snapshot.publishing.postedAccounts[0].id, 7);
});

test('limits publishing samples and applies same-cohort performance thresholds', () => {
  const accounts = [1, 2, 3, 4].map((id) => account(id));
  const videos = accounts.flatMap((item, index) => [
    video(index * 2 + 1, item.id, (index + 1) * 100),
    video(index * 2 + 2, item.id, (index + 1) * 200),
  ]);
  videos.push(
    video(20, 1, 99_999, { publishedAt: new Date('2026-07-19T00:00:01.000Z') }),
    video(21, 1, 50, { contentType: 'post', likeCount: 20, commentCount: 5, shareCount: 5 }),
  );
  const snapshot = buildMonitorAnalysisSnapshot({
    now,
    platform: 'tiktok',
    range: '7d',
    accounts,
    fetches: [],
    videos,
  });
  const videoCohort = snapshot.performance.cohorts.find((item) => item.contentType === 'video');
  const postCohort = snapshot.performance.cohorts.find((item) => item.contentType === 'post');

  assert.ok(videoCohort);
  assert.equal(videoCohort.videoCount, 8, 'content younger than 24h is excluded');
  assert.equal(videoCohort.enoughVideoSample, true);
  assert.equal(videoCohort.topVideos.length, 2);
  assert.equal(videoCohort.bottomVideos.length, 2);
  assert.equal(videoCohort.enoughAccountSample, true);
  assert.equal(videoCohort.topAccounts.length, 1);
  assert.equal(videoCohort.topAccounts[0].id, 4);
  assert.ok(postCohort);
  assert.equal(postCohort.metric, 'daily_engagement');
  assert.equal(postCohort.enoughVideoSample, false);
  assert.equal(postCohort.topVideos.length, 1);
  assert.equal(postCohort.bottomVideos.length, 0, 'small samples are raw rankings, not low performers');
  assert.equal(snapshot.publishing.postedAccounts.find((item) => item.id === 1)?.samples.length, 3);
});

test('handles zero plays and empty attempts safely', () => {
  const snapshot = buildMonitorAnalysisSnapshot({
    now,
    platform: 'all',
    range: '7d',
    accounts: [account(1)],
    fetches: [],
    videos: [video(1, 1, 0, { likeCount: 0, commentCount: 0, shareCount: 0 })],
  });
  assert.equal(snapshot.health.successRate, null);
  assert.equal(snapshot.performance.cohorts[0].topVideos[0].engagementRate, 0);
});
