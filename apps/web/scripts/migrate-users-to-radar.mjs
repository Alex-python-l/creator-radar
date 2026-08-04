#!/usr/bin/env node
/**
 * mkt.users(studio 旧登录) → radar 的 better-auth users+accounts,role=admin。
 *
 * 2026-07-17 多用户翻转的一次性迁移。旧 scrypt$ 哈希原样搬进
 * accounts.password(lib/auth 的 verify 分流兼容),迁移账号免改密;
 * session_ver 丢弃——better-auth 是 DB session,全员上线后重登一次。
 *
 * 用法(默认 dry-run,只打印不写):
 *   node apps/web/scripts/migrate-users-to-radar.mjs \
 *     --source postgresql://user:pw@127.0.0.1:5432/mkt \
 *     --target postgresql://user:pw@127.0.0.1:5432/short_video_monitor \
 *     [--apply]
 *
 * 幂等:按 lower(username)/lower(email) 跳过已存在的目标用户,可重复执行。
 * 生产顺序见 docs/DEPLOY.md:先跑 drizzle/manual/0009_multi_user.sql 再跑本脚本。
 */
import postgres from 'postgres';

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1) return fallback;
  const v = process.argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
}

const sourceUrl = arg('source');
const targetUrl = arg('target');
const apply = arg('apply', false) === true;

if (!sourceUrl || !targetUrl) {
  console.error('usage: migrate-users-to-radar.mjs --source <mkt url> --target <svm url> [--apply]');
  process.exit(1);
}

const src = postgres(sourceUrl, { max: 1 });
const dst = postgres(targetUrl, { max: 1 });

try {
  const legacy = await src`
    SELECT id, username, email, password_hash, created_at
    FROM users ORDER BY id`;
  console.log(`source mkt.users: ${legacy.length} row(s)`);

  let migrated = 0;
  let skipped = 0;
  for (const u of legacy) {
    const [dupe] = await dst`
      SELECT id, username FROM users
      WHERE lower(username) = lower(${u.username}) OR lower(email) = lower(${u.email})
      LIMIT 1`;
    if (dupe) {
      console.log(`  skip ${u.username}: target user #${dupe.id} (${dupe.username}) already exists`);
      skipped += 1;
      continue;
    }
    if (!String(u.password_hash).startsWith('scrypt$')) {
      console.log(`  skip ${u.username}: unexpected hash format`);
      skipped += 1;
      continue;
    }
    console.log(`  migrate ${u.username} <${u.email}> role=admin`);
    if (apply) {
      await dst.begin(async (tx) => {
        // better-auth username 插件约定:username 列存 normalized(lower),
        // display_username 存原样;email 一律 lower。
        const [row] = await tx`
          INSERT INTO users (name, email, email_verified, username, display_username,
                             role, created_at, updated_at)
          VALUES (${u.username}, ${String(u.email).toLowerCase()}, false,
                  ${String(u.username).toLowerCase()}, ${u.username},
                  'admin', ${u.created_at}, now())
          RETURNING id`;
        await tx`
          INSERT INTO accounts (account_id, provider_id, user_id, password,
                                created_at, updated_at)
          VALUES (${String(row.id)}, 'credential', ${row.id}, ${u.password_hash},
                  ${u.created_at}, now())`;
      });
      migrated += 1;
    }
  }

  console.log(apply
    ? `done: migrated ${migrated}, skipped ${skipped}`
    : `dry-run: would migrate ${legacy.length - skipped}, skip ${skipped} (add --apply to write)`);
} finally {
  await src.end();
  await dst.end();
}
