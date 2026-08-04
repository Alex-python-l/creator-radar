import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '..', '..');

const SECURITY_HEADERS = [
  {key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains'},
  {key: 'X-Content-Type-Options', value: 'nosniff'},
  {key: 'X-Frame-Options', value: 'DENY'},
  {key: 'Referrer-Policy', value: 'same-origin'},
  {key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()'},
];

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: repoRoot,
  typedRoutes: true,
  transpilePackages: ['@svm/shared'],
  turbopack: {
    root: repoRoot,
  },
  async headers() {
    return [{source: '/:path*', headers: SECURITY_HEADERS}];
  },
  // 旧 SPA 时代的 /studio/groups(/:id) 链接 → accounts(middleware 先补
  // locale,再命中这里,两跳到位)。
  async redirects() {
    return [
      {
        source: '/:locale(zh|en)/studio/groups',
        destination: '/:locale/studio/accounts',
        permanent: false,
      },
      {
        source: '/:locale(zh|en)/studio/groups/:id(\\d+)',
        destination: '/:locale/studio/accounts?group=:id',
        permanent: false,
      },
    ];
  },
  // dev:代理 studio 后端(本地 FastAPI :8080),对应生产 nginx 的三段
  // /studio 反代。生产容器不注入这些 rewrite,防 nginx 漏配时错路由。
  async rewrites() {
    if (process.env.NODE_ENV !== 'development') return [];
    const studio = process.env.STUDIO_API_PROXY ?? 'http://127.0.0.1:8080';
    return [
      { source: '/studio/api/:path*', destination: `${studio}/api/:path*` },
      { source: '/studio/healthz', destination: `${studio}/healthz` },
      { source: '/studio/static/:path*', destination: `${studio}/static/:path*` },
    ];
  },
};

const withNextIntl = createNextIntlPlugin();

export default withNextIntl(nextConfig);
