'use client';

import {
  adminClient,
  emailOTPClient,
  inferAdditionalFields,
  organizationClient,
  usernameClient,
} from 'better-auth/client/plugins';
import { createAuthClient } from 'better-auth/react';

import type { auth } from './index';

// 浏览器侧 SDK。同源部署,baseURL 省略;inferAdditionalFields 仅取类型,
// 不会把 server 实例打进 bundle。
export const authClient = createAuthClient({
  plugins: [
    usernameClient(),
    emailOTPClient(),
    adminClient(),
    organizationClient(),
    inferAdditionalFields<typeof auth>(),
  ],
});
