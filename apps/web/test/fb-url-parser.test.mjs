/**
 * Facebook URL 解析 — 4 种 identifier 形态 + 用户原始数据 26 URL 回归。
 *
 * 旧版只支持 vanity 一种形态(facebook.com/<vanity>),profile.php?id= / share/ /
 * people/pfbid 三种全部被错认成 'profile.php' / 'share' / 'people' 当 username 入库,
 * scraper 抓取必败。此测试锁死新规则覆盖。
 *
 * 跑法: 需要 node --experimental-strip-types 才能 import .ts(已在 package.json 配)。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

const { parseFacebookUrl, parseImportText, normalizeBulkAccounts } = await import(
  '../lib/import-parsers.ts'
);
const { FetchEnabledPlatforms, platformProfileUrl } = await import(
  '../../../packages/shared/src/platforms.ts'
);

// ---------- 4 种 identifier 形态(优先级) ----------

test('profile.php?id=<numeric>: 提取纯数字 id', () => {
  const r = parseFacebookUrl('https://www.facebook.com/profile.php?id=61589956126863');
  assert.deepEqual(r, { username: '61589956126863' });
});

test('profile.php 带额外 query: 只保留纯数字 id', () => {
  const r = parseFacebookUrl('https://www.facebook.com/profile.php?id=61589956126863&ref=share');
  assert.deepEqual(r, { username: '61589956126863' });
});

test('story.php 带 id query: 提取纯数字账号 id', () => {
  const r = parseFacebookUrl(
    'https://www.facebook.com/story.php?story_fbid=122102908941341636&id=61590249099138&rdid=tMH9QwEIbPwtwLBr',
  );
  assert.deepEqual(r, { username: '61590249099138' });
});

test('facebook.com/<numeric>: 提取纯数字 id', () => {
  const r = parseFacebookUrl('https://www.facebook.com/61590448588289');
  assert.deepEqual(r, { username: '61590448588289' });
});

test('share/<code>: 标记 needsResolve + 保留 rawUrl', () => {
  const r = parseFacebookUrl(
    'https://www.facebook.com/share/1CTxoxSKCJ/?mibextid=wwXIfr',
  );
  assert.deepEqual(r, {
    username: '',
    needsResolve: true,
    rawUrl: 'https://www.facebook.com/share/1CTxoxSKCJ/?mibextid=wwXIfr',
  });
});

test('people/<name>/pfbid<...>: 标记 needsResolve + 保留 rawUrl', () => {
  const pfbid = 'pfbid02pb5DGo8aGp71dmeQmv85sP4GotPPM9FzbfvcayHUfXHsGLQ6jcqKBiEy1gBJQUKHl';
  const rawUrl = `https://www.facebook.com/people/Xshushx-Studied/${pfbid}/`;
  assert.deepEqual(parseFacebookUrl(rawUrl), { username: '', needsResolve: true, rawUrl });
});

test('people/<name>/<numeric>: 标记 needsResolve（统一成纯数字 id）', () => {
  const rawUrl = 'https://www.facebook.com/people/Andra-Alexa/61590139985976/';
  assert.deepEqual(parseFacebookUrl(rawUrl), { username: '', needsResolve: true, rawUrl });
});

test('vanity (含尾随 ? 没 query): 标记 needsResolve', () => {
  const rawUrl = 'https://www.facebook.com/chan.rinkoyamato?';
  assert.deepEqual(parseFacebookUrl(rawUrl), { username: '', needsResolve: true, rawUrl });
});

test('vanity 带 /reels/ 子路径: 标记 needsResolve', () => {
  const rawUrl = 'https://www.facebook.com/Cristiano/reels/';
  assert.deepEqual(parseFacebookUrl(rawUrl), { username: '', needsResolve: true, rawUrl });
});

test('系统路径 login/signup/recover 不被当 vanity', () => {
  assert.equal(parseFacebookUrl('https://www.facebook.com/login/?next=foo'), null);
  assert.equal(parseFacebookUrl('https://www.facebook.com/signup'), null);
  assert.equal(parseFacebookUrl('https://www.facebook.com/help'), null);
});

// ---------- 纯文本 username 字符校验 (2026-05 事故回归) ----------
// 5/26 用户把"Rinko Yamato"这种带空格的人名当 vanity 粘进 textarea,26 条假账号入库,
// scraper 抓 facebook.com/Rinko%20Yamato 拿到通用页又把 JS bundle 名当 display_name。
// 前端必须在入 accounts 前就拦掉,理由暴露给用户而不是静默 skipped。

test('parseImportText: fb 纯文本含空格被拒(不入 accounts)', () => {
  const r = parseImportText('Rinko Yamato\nGene Xvieve', 'facebook');
  assert.equal(r.accounts.length, 0, '人名不应入 accounts');
  assert.equal(r.errors.length, 2);
  assert.ok(r.errors.every((e) => /无效|invalid/i.test(e.reason)), `errors=${JSON.stringify(r.errors)}`);
});

test('parseImportText: fb 纯文本含中文括号被拒', () => {
  const r = parseImportText('Thea Cee（Era Learns）', 'facebook');
  assert.equal(r.accounts.length, 0);
  assert.equal(r.errors.length, 1);
});

test('parseImportText: fb 合法 vanity 仍可入(Cristiano / khaby.lame_)', () => {
  const r = parseImportText('Cristiano\nkhaby.lame_', 'facebook');
  assert.equal(r.errors.length, 0, `errors=${JSON.stringify(r.errors)}`);
  assert.equal(r.accounts.length, 2);
  assert.deepEqual(
    r.accounts.map((a) => a.username),
    ['Cristiano', 'khaby.lame_'],
  );
});

test('parseImportText: fb 纯数字 vanity 入库(归一化由 server 侧 normalizeBulkAccounts 处理)', () => {
  const r = parseImportText('61590448588289', 'facebook');
  assert.equal(r.errors.length, 0);
  assert.equal(r.accounts.length, 1);
  assert.equal(r.accounts[0].username, '61590448588289');
});

test('parseImportText: 其他平台同样拒空格 (tk username 含空格 → 拒绝)', () => {
  const r = parseImportText('khaby lame', 'tiktok');
  assert.equal(r.accounts.length, 0);
  assert.equal(r.errors.length, 1);
});

// ---------- parseImportText: 用户原始 26 URL 回归 ----------

const USER_26_URLS = `
https://www.facebook.com/profile.php?id=61589956126863
https://www.facebook.com/share/1CTxoxSKCJ/?mibextid=wwXIfr
https://www.facebook.com/chan.rinkoyamato?
https://www.facebook.com/people/Xshushx-Studied/pfbid02pb5DGo8aGp71dmeQmv85sP4GotPPM9FzbfvcayHUfXHsGLQ6jcqKBiEy1gBJQUKHl/
https://www.facebook.com/profile.php?id=61590010151652
https://www.facebook.com/profile.php?id=61590438292404
https://www.facebook.com/share/1BVnwvqt2D/?mibextid=wwXIfr
https://www.facebook.com/share/1GNtqGZNAw/?mibextid=wwXIfr
https://www.facebook.com/share/1AhXACJJKP/?mibextid=wwXIfr
https://www.facebook.com/share/18YYFZQDxf/?mibextid=wwXIfr
https://www.facebook.com/share/1GQEohePfV/
https://www.facebook.com/share/1E7mENGYNR/?mibextid=wwXIfr
https://www.facebook.com/share/1FrRgChVSw/?mibextid=wwXIfr
https://www.facebook.com/profile.php?id=61590350996721
https://www.facebook.com/profile.php?id=61590144007248
https://www.facebook.com/share/1Fk5kn7SUq/?mibextid=wwXIfr
https://www.facebook.com/people/Cha-Cha/pfbid0sFiUnzjcGuj8tu65yuT4FyJ5ExaHcQ2TsBNHDoXagHY6iRQCgDrU8qDvSekTesvEl/
https://www.facebook.com/profile.php?id=61590086619861
https://www.facebook.com/people/AV-Learns/pfbid02PijcaAM5HyRWBJeEifyn8W7vVrNtMwJDVqNw9qN3vCLFN3vbbeGSxLiDqEpTR4Ngl/
https://www.facebook.com/share/1Qgs7EjLMy/?mibextid=wwXIfr
https://www.facebook.com/people/Szn-Catt/pfbid0Z4dxPVX8yWSBnsaxfrvGCcYtGV1vKY5e3yrK2Do7njge3HhEN7XpAybUNQppmMV3l/
https://www.facebook.com/share/17WYSb9v76/?mibextid=wwXIfr
https://www.facebook.com/share/1BMLSub3Ws/?mibextid=wwXIfr
https://www.facebook.com/share/1BWWUZcBV7/
https://www.facebook.com/profile.php?id=61589980756302
https://www.facebook.com/profile.php?id=61590365278302
`.trim();

test('用户 26 URL: 全部被识别为 FB platform(无 unparsable)', () => {
  const result = parseImportText(USER_26_URLS, 'facebook');
  assert.equal(result.accounts.length, 26, `期望 26, 实际 ${result.accounts.length}; errors=${JSON.stringify(result.errors)}`);
  assert.equal(result.errors.length, 0);
  for (const a of result.accounts) {
    assert.equal(a.platform, 'facebook');
  }
});

test('用户 26 URL: 形态分类正确(8 纯数字 profile + 18 needsResolve)', () => {
  const result = parseImportText(USER_26_URLS, 'facebook');
  const numericId = result.accounts.filter((a) => /^\d+$/.test(a.username));
  const needsResolve = result.accounts.filter((a) => a.needsResolve === true);
  assert.equal(numericId.length, 8, 'profile.php 纯数字应 8 条');
  assert.equal(needsResolve.length, 18, 'share(13) + people(4) + vanity(1) 应 18 条');
});

// ---------- bulk import server normalization ----------

test('normalizeBulkAccounts: FB people/share resolve 后按 canonical username 去重', async () => {
  assert.equal(typeof normalizeBulkAccounts, 'function');
  const calls = [];
  const result = await normalizeBulkAccounts(
    [
      {
        platform: 'facebook',
        username: 'people/Jeannie-Study/pfbidOLD',
        needsResolve: true,
        rawUrl: 'https://www.facebook.com/people/Jeannie-Study/pfbidOLD/',
      },
      {
        platform: 'facebook',
        username: '',
        needsResolve: true,
        rawUrl: 'https://www.facebook.com/share/jeannie/',
      },
    ],
    async (url) => {
      calls.push(url);
      return { ok: true, identifier: '61590123456789' };
    },
  );

  assert.deepEqual(calls, [
    'https://www.facebook.com/people/Jeannie-Study/pfbidOLD/',
    'https://www.facebook.com/share/jeannie/',
  ]);
  assert.equal(result.accounts.length, 1);
  assert.equal(result.accounts[0].username, '61590123456789');
  assert.equal(result.skipped, 1);
  assert.deepEqual(result.resolveErrors, []);
});

test('normalizeBulkAccounts: FB vanity username 也走 resolver 后入库', async () => {
  const result = await normalizeBulkAccounts(
    [{ platform: 'facebook', username: 'Cristiano' }],
    async (url) => {
      assert.equal(url, 'https://www.facebook.com/Cristiano');
      return { ok: true, identifier: '100044296486382' };
    },
  );

  assert.equal(result.accounts.length, 1);
  assert.equal(result.accounts[0].username, '100044296486382');
  assert.equal(result.skipped, 0);
});

test('normalizeBulkAccounts: FB 纯数字 username 不走 resolver', async () => {
  const calls = [];
  const result = await normalizeBulkAccounts(
    [{ platform: 'facebook', username: '61590448588289' }],
    async (url) => {
      calls.push(url);
      return { ok: false, error: 'should not call resolver' };
    },
  );

  assert.deepEqual(calls, []);
  assert.equal(result.accounts.length, 1);
  assert.equal(result.accounts[0].username, '61590448588289');
  assert.equal(result.skipped, 0);
});

test('normalizeBulkAccounts: FB resolve 失败时跳过并返回错误', async () => {
  const result = await normalizeBulkAccounts(
    [{ platform: 'facebook', username: '', needsResolve: true, rawUrl: 'https://www.facebook.com/share/bad/' }],
    async () => ({ ok: false, error: 'login wall' }),
  );

  assert.equal(result.accounts.length, 0);
  assert.equal(result.skipped, 1);
  assert.deepEqual(result.resolveErrors, ['https://www.facebook.com/share/bad/: login wall']);
});

// ---------- YouTube Shorts monitor ----------

test('youtube: @handle shorts URL 识别为 youtube account', () => {
  const r = parseImportText('https://www.youtube.com/@mkbhd/shorts?si=abc', 'tiktok');
  assert.equal(r.errors.length, 0);
  assert.deepEqual(r.accounts[0], {
    platform: 'youtube',
    username: 'mkbhd',
    sourceLine: 'https://www.youtube.com/@mkbhd/shorts?si=abc',
  });
});

test('youtube: channel/c/user URL 保留 path identifier', () => {
  assert.equal(
    parseImportText('https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw/shorts', 'tiktok').accounts[0]?.username,
    'channel/UC_x5XG1OV2P6uZZ5FSM9Ttw',
  );
  assert.equal(
    parseImportText('https://www.youtube.com/c/Veritasium', 'tiktok').accounts[0]?.username,
    'c/Veritasium',
  );
  assert.equal(
    parseImportText('https://www.youtube.com/user/Google', 'tiktok').accounts[0]?.username,
    'user/Google',
  );
});

test('youtube: fetch enabled and profile URL respects identifier kind', () => {
  assert.equal(FetchEnabledPlatforms.includes('youtube'), true);
  assert.equal(platformProfileUrl('youtube', 'mkbhd'), 'https://www.youtube.com/@mkbhd');
  assert.equal(
    platformProfileUrl('youtube', 'channel/UC_x5XG1OV2P6uZZ5FSM9Ttw'),
    'https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw',
  );
  assert.equal(platformProfileUrl('youtube', 'c/Veritasium'), 'https://www.youtube.com/c/Veritasium');
  assert.equal(platformProfileUrl('youtube', 'user/Google'), 'https://www.youtube.com/user/Google');
});
