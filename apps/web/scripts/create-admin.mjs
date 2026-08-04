#!/usr/bin/env node
/**
 * 直接向 radar 库插一个 admin 账号(本地开发 bootstrap / 生产应急)。
 *
 * 密码哈希用并入前 studio 的 scrypt$ 格式——lib/auth 的 verify 分流原生
 * 兼容,脚本因此不依赖 next/better-auth 运行时,只要一个 DATABASE_URL。
 *
 * 用法:
 *   node apps/web/scripts/create-admin.mjs \
 *     --db postgresql://127.0.0.1:5432/svm_dev \
 *     --username will --email will@example.com --password 'xxxxxxxx'
 */
import { randomBytes, scryptSync } from 'node:crypto';
import postgres from 'postgres';

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  const v = i === -1 ? undefined : process.argv[i + 1];
  return v && !v.startsWith('--') ? v : undefined;
}

const dbUrl = arg('db') ?? process.env.DATABASE_URL;
const username = arg('username');
const email = arg('email');
const password = arg('password');

if (!dbUrl || !username || !email || !password || password.length < 8) {
  console.error(
    'usage: create-admin.mjs --db <url> --username <u> --email <e> --password <p(>=8)>',
  );
  process.exit(1);
}

// 与 apps/web/lib/auth/legacy-password.ts 的格式/参数一致。
const N = 2 ** 14, r = 8, p = 1;
const salt = randomBytes(16);
const dk = scryptSync(password, salt, 32, { N, r, p, maxmem: 2 ** 26 });
const hash = `scrypt$${N}$${r}$${p}$${salt.toString('hex')}$${dk.toString('hex')}`;

const sql = postgres(dbUrl, { max: 1 });
try {
  const [existing] = await sql`
    SELECT id FROM users
    WHERE lower(username) = lower(${username}) OR lower(email) = lower(${email})
    LIMIT 1`;
  if (existing) {
    console.error(`user already exists (id=${existing.id}); not touching it`);
    process.exit(2);
  }
  const [row] = await sql.begin(async (tx) => {
    const [u] = await tx`
      INSERT INTO users (name, email, email_verified, username, display_username,
                         role, created_at, updated_at)
      VALUES (${username}, ${email.toLowerCase()}, false,
              ${username.toLowerCase()}, ${username}, 'admin', now(), now())
      RETURNING id`;
    await tx`
      INSERT INTO accounts (account_id, provider_id, user_id, password,
                            created_at, updated_at)
      VALUES (${String(u.id)}, 'credential', ${u.id}, ${hash}, now(), now())`;
    const [workspace] = await tx`
      INSERT INTO workspaces (kind, user_id)
      VALUES ('personal', ${u.id})
      RETURNING id`;
    await tx`
      INSERT INTO account_groups (name, owner_id, workspace_id, sort_order)
      VALUES ('未分组', ${u.id}, ${workspace.id}, 0)`;
    return [u];
  });
  console.log(`created admin #${row.id} (${username})`);
} finally {
  await sql.end();
}
