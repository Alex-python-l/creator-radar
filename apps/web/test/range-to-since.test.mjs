/**
 * Regression: range='today' 之前按 UTC 当天 00:00 切窗,等价于北京时间当天 08:00,
 * 凌晨 0~8 点的本地"今天"永远落不进窗口,体感"Today chip 永远没数据"。
 * 现在改成滚动 24 小时窗口,时区中立。任何回退到 UTC midnight 都让此测试失败。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

const { rangeToSince } = await import('../lib/url-params.ts');

test("rangeToSince('today') 是 now-24h 的滚动窗口,不是 UTC 当天 00:00", () => {
  const before = Date.now();
  const since = rangeToSince('today');
  const after = Date.now();

  assert.ok(since instanceof Date, "today 应返回 Date 而非 undefined");

  const delta = before - since.getTime();
  // 容许进程在 before/after 之间花掉的几毫秒
  assert.ok(
    delta >= 86_400_000 - 5 && delta <= 86_400_000 + (after - before) + 5,
    `today 应当 ≈ now - 24h, 实际 delta=${delta}ms`,
  );
});

test("rangeToSince('7d'/'30d'/'90d') 仍按 N*24h 滚动窗口", () => {
  const now = Date.now();
  for (const [range, days] of [['7d', 7], ['30d', 30], ['90d', 90]]) {
    const since = rangeToSince(range);
    const delta = now - since.getTime();
    assert.ok(
      Math.abs(delta - days * 86_400_000) < 1000,
      `${range} 应 ≈ now - ${days}d, 实际 delta=${delta}ms`,
    );
  }
});

test("rangeToSince('all') 返回 undefined", () => {
  assert.equal(rangeToSince('all'), undefined);
});
