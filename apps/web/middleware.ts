import { getSessionCookie } from 'better-auth/cookies';
import createMiddleware from 'next-intl/middleware';
import { NextResponse, type NextRequest } from 'next/server';

import { routing } from '@/i18n/routing';

const intlMiddleware = createMiddleware(routing);

// /login 与其 locale 变体(/zh/login、/en/login)。
const LOGIN_PATH_RE = /^\/(?:zh\/|en\/)?login(?:\/|$)/;
const PUBLIC_PATH_RE = /^\/(?:(?:zh|en)(?:\/apply)?|apply)?\/?$/;

/**
 * 乐观门:只看 session cookie 存不存在(better-auth 约定名,不查库),
 * 挡住绝大多数未登录流量。真校验(查 sessions 表、过期、ban)在 server
 * 层 getViewer() 做——伪造 cookie 的请求会在页面里再被弹回登录页,
 * 所以 login 页这里无条件放行,避免"假 cookie 在 middleware 与页面
 * 之间来回 302"的循环。
 */
export default function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (LOGIN_PATH_RE.test(pathname) || PUBLIC_PATH_RE.test(pathname)) return intlMiddleware(req);

  if (!getSessionCookie(req)) {
    const next = encodeURIComponent(pathname + search);
    const locale = pathname.match(/^\/(zh|en)(?:\/|$)/)?.[1];
    const loginPath = locale ? `/${locale}/login` : '/login';
    return NextResponse.redirect(new URL(`${loginPath}?next=${next}`, req.url));
  }

  return intlMiddleware(req);
}

export const config = {
  // studio 页面走本应用(intl 补 locale + cookie 门);只排除后端直通路径
  // (api/healthz/static 由 nginx 剥前缀反代 FastAPI,dev 走 rewrites)。
  // 此清单必须与 nginx 的 /studio 反代段一致,见 docs/DEPLOY.md E 节。
  matcher: '/((?!api|trpc|_next|_vercel|studio/api|studio/healthz|studio/static|.*\\..*).*)',
};
