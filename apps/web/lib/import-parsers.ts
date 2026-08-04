import { platformProfileUrl, type Platform, isPlatform } from '@svm/shared/platforms';

interface ParsedAccount {
  platform: Platform;
  username: string;
  sourceLine: string;
  /** FB share 短链不含 username,入库前 Server Action 需调 scraper /resolve-fb-url
   * follow 302 拿真实 profile URL。username 为空字符串占位。*/
  needsResolve?: boolean;
  /** needsResolve=true 时的原始 URL,传给 resolve endpoint。*/
  rawUrl?: string;
}

export interface BulkAccountInput {
  platform: Platform;
  username: string;
  /** FB URL 或非稳定 people/share identifier 需要入库前由 scraper resolve 成 canonical。 */
  needsResolve?: boolean;
  rawUrl?: string;
}

export interface NormalizedBulkAccounts {
  accounts: Array<{
    platform: Platform;
    username: string;
  }>;
  skipped: number;
  resolveErrors: string[];
}

type ResolveFb = (
  rawUrl: string,
) => Promise<{ ok: true; identifier: string } | { ok: false; error: string }>;

export interface ParseResult {
  accounts: ParsedAccount[];
  errors: { line: string; reason: string }[];
}

// 非 FB 平台:简单 vanity 提取
const URL_PATTERNS: Array<{ platform: Platform; regex: RegExp }> = [
  { platform: 'tiktok', regex: /tiktok\.com\/@?([\w.\-_]+)/i },
  { platform: 'instagram', regex: /instagram\.com\/([\w.\-_]+)/i },
  { platform: 'douyin', regex: /douyin\.com\/(?:user\/)?([\w.\-_]+)/i },
];

// FB 四种 identifier 形态(优先级从高到低 — 见 docs/2026-05-25-facebook-scraper-design.md):
// 1. profile.php?id=<numeric>  — 没设 vanity 的 Page,FB 默认 URL
// 2. share/<code>              — 短链,不含 username,需 server-side follow 302
// 3. people/<name>/<token>     — token 是 pfbid<...> 或 numeric_id
// 4. <vanity> (兜底)           — facebook.com/<vanity>
const FB_PROFILE_PHP_RE = /facebook\.com\/profile\.php\?id=(\d+)/i;
const FB_STORY_ID_RE = /facebook\.com\/story\.php\?(?:[^#&]*&)*id=(\d+)(?:[&#]|$)/i;
const FB_NUMERIC_PATH_RE = /facebook\.com\/(\d{5,})(?:[/?#]|$)/i;
const FB_SHARE_RE = /facebook\.com\/share\/[^/?]+/i;
const FB_PEOPLE_PATH_RE = /facebook\.com\/people\/([\w.\-]+)\/(pfbid\w+|\d+)/i;
const FB_VANITY_RE = /facebook\.com\/([\w.\-_]+)/i;
// vanity 兜底前要拦掉的系统路径(与 scraper _FB_RESERVED_PATHS 对齐)
const FB_VANITY_RESERVED = new Set([
  'reel', 'reels', 'watch', 'videos', 'video', 'photo', 'photos', 'story', 'stories',
  'story.php',
  'groups', 'events', 'marketplace', 'pages', 'p',
  'login', 'signup', 'recover', 'checkpoint', 'security', 'settings',
  'help', 'business', 'ads', 'gaming', 'search', 'hashtag',
  'profile.php', 'people', 'share',
]);

/** 解析 FB URL → 纯数字 id 或 needsResolve。返回 null 表示无法识别。 */
export function parseFacebookUrl(
  s: string,
): { username: string; needsResolve?: boolean; rawUrl?: string } | null {
  // 1. profile.php?id=<numeric> → 纯数字 id(自洽,无需 resolve)
  let m = s.match(FB_PROFILE_PHP_RE);
  if (m) return { username: m[1] };
  // 2. story.php?...&id=<numeric> → 纯数字账号 id(share/r final URL)
  m = s.match(FB_STORY_ID_RE);
  if (m) return { username: m[1] };
  // 3. facebook.com/<numeric> → 纯数字 id
  m = s.match(FB_NUMERIC_PATH_RE);
  if (m) return { username: m[1] };
  // 4. share/<code> → needsResolve(不含 id)
  if (FB_SHARE_RE.test(s)) return { username: '', needsResolve: true, rawUrl: s };
  // 5. people/<name>/<token> → needsResolve(统一成纯数字 id;pfbid 会漂移)
  if (FB_PEOPLE_PATH_RE.test(s)) return { username: '', needsResolve: true, rawUrl: s };
  // 6. vanity 兜底 → needsResolve(vanity 可改名,统一成纯数字 id)
  m = s.match(FB_VANITY_RE);
  if (m && !FB_VANITY_RESERVED.has(m[1].toLowerCase())) {
    return { username: '', needsResolve: true, rawUrl: s };
  }
  return null;
}

const YT_HANDLE_RE = /youtube\.com\/@([\w.\-_]+)/i;
const YT_CHANNEL_RE = /youtube\.com\/channel\/([A-Za-z0-9_-]+)/i;
const YT_PREFIX_RE = /youtube\.com\/(c|user)\/([\w.\-_]+)/i;

function parseYoutubeUrl(s: string): { username: string } | null {
  let m = s.match(YT_HANDLE_RE);
  if (m) return { username: m[1] };
  m = s.match(YT_CHANNEL_RE);
  if (m) return { username: `channel/${m[1]}` };
  m = s.match(YT_PREFIX_RE);
  if (m) return { username: `${m[1]}/${m[2]}` };
  return null;
}

function tryParseUrl(
  s: string,
): { platform: Platform; username: string; needsResolve?: boolean; rawUrl?: string } | null {
  // FB 优先 — 4 条特殊规则,通用 vanity 兜底正则会误识别 share/people 等
  if (/facebook\.com/i.test(s)) {
    const fb = parseFacebookUrl(s);
    return fb ? { platform: 'facebook', ...fb } : null;
  }
  if (/youtube\.com/i.test(s)) {
    const yt = parseYoutubeUrl(s);
    return yt ? { platform: 'youtube', ...yt } : null;
  }
  for (const { platform, regex } of URL_PATTERNS) {
    const m = s.match(regex);
    if (m && m[1]) {
      // 排除 IG 系统路径 (reel/p/explore 等)
      if (
        platform === 'instagram' &&
        ['reel', 'reels', 'p', 'explore', 'accounts'].includes(m[1].toLowerCase())
      ) {
        continue;
      }
      return { platform, username: m[1] };
    }
  }
  return null;
}

function cleanUsername(raw: string): string {
  return raw.trim().replace(/^@/, '');
}

// 各平台 vanity / handle 合法字符集 — 字母数字下划线 / `.` / `-`。
// 2026-05 事故:用户粘"Rinko Yamato"等带空格人名,前端不拦就入库,scraper 抓通用页
// 又把 JS bundle 名 "connection_quality" 当 display_name,造成 26 条幽灵账号。
const VANITY_RE = /^[\w.\-]+$/;

export function parseImportText(raw: string, defaultPlatform: Platform): ParseResult {
  const accounts: ParsedAccount[] = [];
  const errors: { line: string; reason: string }[] = [];
  const seen = new Set<string>();

  const lines = raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);

  // 检测第一行是否表头（含 username/platform 关键字）
  let startIdx = 0;
  if (lines.length > 0) {
    const firstLower = lines[0].toLowerCase();
    if (
      (firstLower.includes('username') || firstLower.includes('用户')) &&
      firstLower.includes('platform')
    ) {
      startIdx = 1;
    } else if (firstLower === 'username' || firstLower === '用户名' || firstLower === 'url') {
      startIdx = 1;
    }
  }

  for (let i = startIdx; i < lines.length; i++) {
    const line = lines[i];
    // 分隔：tab 优先（Excel 复制），逗号次之
    const cols = line.includes('\t')
      ? line.split('\t').map((c) => c.trim())
      : line.split(',').map((c) => c.trim());

    let parsed: {
      platform: Platform;
      username: string;
      needsResolve?: boolean;
      rawUrl?: string;
    } | null = null;
    if (cols.length === 0 || !cols[0]) {
      errors.push({ line, reason: '空行' });
      continue;
    }

    // 第一列是 URL 还是 username？
    const fromUrl = tryParseUrl(cols[0]);
    if (fromUrl) {
      parsed = fromUrl;
      // 第二列如果是合法 platform，覆盖（少见场景）
      if (cols[1] && isPlatform(cols[1].toLowerCase())) {
        parsed.platform = cols[1].toLowerCase() as Platform;
      }
    } else {
      // 纯 username
      const username = cleanUsername(cols[0]);
      if (!username) {
        errors.push({ line, reason: '用户名为空' });
        continue;
      }
      if (!VANITY_RE.test(username)) {
        errors.push({ line, reason: '无效的用户名' });
        continue;
      }
      // 第二列 platform
      const colPlatform = cols[1]?.trim().toLowerCase();
      const platform =
        colPlatform && isPlatform(colPlatform) ? (colPlatform as Platform) : defaultPlatform;
      parsed = { platform, username };
    }

    // share URL 时 username='',用 rawUrl 做去重 key 防多个 share 撞 key
    const dedupId = parsed.needsResolve ? (parsed.rawUrl ?? '') : parsed.username;
    const key = `${parsed.platform}:${dedupId.toLowerCase()}`;
    if (seen.has(key)) {
      errors.push({ line, reason: `重复: ${parsed.username || parsed.rawUrl}` });
      continue;
    }
    seen.add(key);
    accounts.push({ ...parsed, sourceLine: line });
  }

  return { accounts, errors };
}

function needsFacebookResolve(account: BulkAccountInput, username: string): boolean {
  if (account.platform !== 'facebook') return false;
  if (account.needsResolve) return true;
  if (!username) return true;
  if (/^\d+$/.test(username)) return false; // 纯数字 id 已稳定,无需 resolve
  return true;
}

function resolveTargetUrl(account: BulkAccountInput, username: string): string {
  if (account.rawUrl) return account.rawUrl;
  return platformProfileUrl(account.platform, username);
}

/**
 * 批量导入的服务端归一化：先把 FB 非稳定 identifier resolve 成 canonical，
 * 再按 platform + canonical username 做二次去重。
 */
export async function normalizeBulkAccounts(
  accounts: BulkAccountInput[],
  resolveFb: ResolveFb,
): Promise<NormalizedBulkAccounts> {
  const normalized: NormalizedBulkAccounts['accounts'] = [];
  const resolveErrors: string[] = [];
  const seen = new Set<string>();
  let skipped = 0;

  for (const account of accounts) {
    if (!isPlatform(account.platform)) {
      skipped += 1;
      continue;
    }

    let username = cleanUsername(account.username);
    if (needsFacebookResolve(account, username)) {
      const targetUrl = resolveTargetUrl(account, username);
      const resolved = await resolveFb(targetUrl);
      if (!resolved.ok) {
        skipped += 1;
        resolveErrors.push(`${targetUrl}: ${resolved.error}`);
        continue;
      }
      username = cleanUsername(resolved.identifier);
    }

    if (!username) {
      skipped += 1;
      continue;
    }

    const key = `${account.platform}:${username.toLowerCase()}`;
    if (seen.has(key)) {
      skipped += 1;
      continue;
    }
    seen.add(key);
    normalized.push({ platform: account.platform, username });
  }

  return { accounts: normalized, skipped, resolveErrors };
}
