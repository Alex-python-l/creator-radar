import { toNextJsHandler } from 'better-auth/next-js';

import { auth } from '@/lib/auth';

// better-auth 全部端点挂 /api/auth/*(sign-in/sign-up/sign-out/get-session/
// change-password 等)。studio 后端的 require_auth 也转发调这里的
// GET /api/auth/get-session 做统一登录校验。
export const { GET, POST } = toNextJsHandler(auth.handler);
