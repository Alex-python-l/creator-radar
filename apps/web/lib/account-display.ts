interface AccountDisplayInput {
  platform: string;
  username: string;
  displayName?: string | null;
  rawUrl?: string | null;
  alias?: string | null;
}

const FB_PROFILE_ID_RE = /(?:^|\/)profile\.php\?id=(\d+)/i;
const FB_PEOPLE_RE = /(?:^|\/)people\/([^/]+)/i;
const FB_SHARE_RE = /(?:^|\/)share\/([^/?#]+)/i;

export function formatAccountDisplayName(account: AccountDisplayInput): string {
  const displayName = account.displayName?.trim();
  if (displayName) return displayName;

  const username = cleanUsername(account.username);
  if (account.platform !== 'facebook') return username ? `@${username}` : 'Unknown account';

  const profileId = username.match(FB_PROFILE_ID_RE)?.[1] ?? username.match(/^\d+$/)?.[0];
  if (profileId) return `Facebook ID ${profileId}`;

  const peopleSlug = username.match(FB_PEOPLE_RE)?.[1];
  if (peopleSlug) return humanizeSlug(peopleSlug);

  const shareCode = username.match(FB_SHARE_RE)?.[1] ?? account.rawUrl?.match(FB_SHARE_RE)?.[1];
  if (shareCode) return `Facebook Link ${shareCode}`;

  return username || 'Facebook Account';
}

export function formatAccountRawIdentifier(account: Pick<AccountDisplayInput, 'platform' | 'username'>): string {
  const username = cleanUsername(account.username);
  if (!username) return account.platform === 'facebook' ? 'Facebook Account' : 'Unknown account';
  return account.platform === 'facebook' ? username : `@${username}`;
}

function cleanUsername(username: string): string {
  return username.trim().replace(/^@/, '');
}

function humanizeSlug(slug: string): string {
  try {
    return decodeURIComponent(slug).replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
  } catch {
    return slug.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
  }
}

export function formatAccountListLabel(account: AccountDisplayInput): string {
  const alias = account.alias?.trim();
  if (alias) return alias;
  return formatAccountDisplayName(account);
}

/**
 * 列表 hover tooltip：alias · displayName · rawIdentifier 三段去重拼接。
 * - 任意一段缺失或只是空白都跳过
 * - 重复段（例如 alias 和 displayName 相同）只保留首次出现，保持优先级语义
 */
export function formatAccountListTitle(account: AccountDisplayInput): string {
  const rawIdentifier = formatAccountRawIdentifier(account);
  return [account.alias, account.displayName, rawIdentifier]
    .filter((s): s is string => Boolean(s?.trim()))
    .filter((s, i, arr) => arr.indexOf(s) === i)
    .join(' · ');
}
