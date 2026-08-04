/**
 * scraper 服务的 HTTP 客户端。
 *
 * 如果配置了 SCRAPER_INTERNAL_TOKEN,Web 会随请求带 x-internal-token,
 * scraper 端据此校验内部调用。
 */
import { env } from './env';

const SCRAPER_BASE_URL = env.SCRAPER_BASE_URL ?? 'http://localhost:8000';

export type ResolveFbResult =
  | { ok: true; identifier: string; finalUrl: string }
  | { ok: false; error: string };

/**
 * 让 scraper 解析 FB URL,返回 canonical identifier。
 *
 * 主要用途: 批量导入时把 share / people/pfbid / vanity 等非稳定入口统一成
 * profile.php?id=<id> 这类稳定账号标识,避免同一主页重复入库。
 *
 * 超时 15s — resolve 一般 <3s,15s 留给单代理失败重试 2-3 次的余量。
 */
export async function resolveFbUrl(rawUrl: string): Promise<ResolveFbResult> {
  let res: Response;
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (env.SCRAPER_INTERNAL_TOKEN) {
    headers['x-internal-token'] = env.SCRAPER_INTERNAL_TOKEN;
  }
  try {
    res = await fetch(`${SCRAPER_BASE_URL}/resolve-fb-url`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ url: rawUrl }),
      signal: AbortSignal.timeout(15_000),
      // Next.js 默认会 cache fetch — 这是动态 server action,禁用缓存
      cache: 'no-store',
    });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'fetch failed' };
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    return { ok: false, error: `scraper ${res.status}: ${body.slice(0, 200)}` };
  }
  try {
    const data = (await res.json()) as { identifier: string; finalUrl: string };
    if (!data.identifier) return { ok: false, error: 'scraper returned empty identifier' };
    return { ok: true, identifier: data.identifier, finalUrl: data.finalUrl };
  } catch {
    return { ok: false, error: 'scraper returned invalid JSON' };
  }
}
