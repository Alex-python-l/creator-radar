import { createEnv } from '@t3-oss/env-nextjs';
import { z } from 'zod';

export const env = createEnv({
  server: {
    NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),

    DATABASE_URL: z.string().min(1),

    SCRAPER_BASE_URL: z.string().url().optional(),
    SCRAPER_INTERNAL_TOKEN: z.string().optional(),

    // better-auth 的标准 env(库内也会自读):secret 签 session token,
    // URL 决定 cookie secure 与回调基址(生产 https://igotyoufrank.cn)。
    BETTER_AUTH_SECRET: z.string().min(32),
    BETTER_AUTH_URL: z.string().url().default('http://localhost:3000'),

    // 注册策略只保留开放/关闭。团队邀请走一次性邮件链接,不再使用共享邀请码。
    REGISTER_MODE: z.enum(['open', 'closed']).default('open'),
    // 普通用户(role=user)可订阅监控账号上限的默认值;admin 不受限。
    DEFAULT_MONITOR_QUOTA: z.coerce.number().int().nonnegative().default(0),

    // Resend 事务邮件。缺失时发送会明确失败,不会把用户误标为已验证。
    RESEND_API_KEY: z.string().min(10).optional(),
    RESEND_FROM: z.string().min(3).optional(),

    LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
  },
  client: {
    NEXT_PUBLIC_APP_URL: z.string().url().default('http://localhost:3000'),
  },
  experimental__runtimeEnv: {
    NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
  },
  skipValidation: process.env.SKIP_ENV_VALIDATION === '1',
  // shell 环境(如 Claude Desktop)有时把 optional key 设为空字符串("")。
  // 没这开关时 z.string().optional() 只挡 undefined,空串照样进 schema 触发 min(1) 失败。
  emptyStringAsUndefined: true,
});
