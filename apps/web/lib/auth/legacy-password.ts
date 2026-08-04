import { scryptSync, timingSafeEqual } from 'node:crypto';

/**
 * 并入前 studio(Python hashlib.scrypt)的密码哈希格式:
 *   scrypt$n$r$p$saltHex$hashHex
 * 参数内嵌于哈希串,dklen=32。迁移账号(scripts/migrate-users-to-radar.mjs
 * 原样搬 hash)靠本函数免改密登录;better-auth 自己的新哈希不带该前缀,
 * 走默认校验(见 lib/auth/index.ts 的 password.verify 分流)。
 */
export function isLegacyScryptHash(hash: string): boolean {
  return hash.startsWith('scrypt$');
}

export function verifyLegacyScrypt(password: string, stored: string): boolean {
  try {
    const [algo, n, r, p, saltHex, hashHex] = stored.split('$');
    if (algo !== 'scrypt') return false;
    const expected = Buffer.from(hashHex, 'hex');
    const actual = scryptSync(password, Buffer.from(saltHex, 'hex'), expected.length, {
      N: Number(n),
      r: Number(r),
      p: Number(p),
      maxmem: 2 ** 26, // 与 Python 侧 _MAXMEM 一致,N=2^14,r=8 时实际 ~16MiB
    });
    return expected.length === actual.length && timingSafeEqual(expected, actual);
  } catch {
    return false;
  }
}
