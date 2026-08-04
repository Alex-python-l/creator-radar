export const Platforms = ['tiktok', 'instagram', 'facebook', 'youtube', 'douyin'] as const;
export type Platform = (typeof Platforms)[number];

export const PlatformLabels: Record<Platform, string> = {
  tiktok: 'TikTok',
  instagram: 'Instagram',
  facebook: 'Facebook',
  youtube: 'YouTube',
  douyin: 'Douyin',
};

/** 当前能主动抓取的平台。Douyin 暂保留枚举,UI 不展示抓取入口。 */
export const FetchEnabledPlatforms: readonly Platform[] = ['tiktok', 'instagram', 'facebook', 'youtube', 'douyin'];

export function isPlatform(value: string): value is Platform {
  return (Platforms as readonly string[]).includes(value);
}

/** 账号在对应平台的主页 URL。 */
export function platformProfileUrl(platform: Platform, username: string): string {
  const u = username.replace(/^@/, '');
  switch (platform) {
    case 'tiktok':
      return `https://www.tiktok.com/@${u}`;
    case 'instagram':
      return `https://www.instagram.com/${u}/`;
    case 'youtube':
      if (u.startsWith('channel/') || u.startsWith('c/') || u.startsWith('user/')) {
        return `https://www.youtube.com/${u}`;
      }
      return `https://www.youtube.com/@${u}`;
    case 'douyin':
      return `https://www.douyin.com/user/${u}`;
    case 'facebook':
      return `https://www.facebook.com/${u}`;
  }
}
