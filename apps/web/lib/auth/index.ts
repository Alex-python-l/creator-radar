import { betterAuth } from 'better-auth';
import { drizzleAdapter } from 'better-auth/adapters/drizzle';
import { APIError, createAuthMiddleware } from 'better-auth/api';
import { verifyPassword as defaultVerify } from 'better-auth/crypto';
import { nextCookies } from 'better-auth/next-js';
import { admin, emailOTP, organization, username } from 'better-auth/plugins';
import { eq } from 'drizzle-orm';

import { db } from '@/lib/db';
import * as schema from '@/lib/db/schema';
import { accountGroups, members, workspaces } from '@/lib/db/schema';
import { escapeHtml, sendTransactionalEmail } from '@/lib/email/resend';
import { env } from '@/lib/env';
import { isLegacyScryptHash, verifyLegacyScrypt } from './legacy-password';

const SESSION_MAX_AGE = 30 * 24 * 3600; // 30 天,与并入前 studio 会话一致

export const auth = betterAuth({
  baseURL: env.BETTER_AUTH_URL,
  secret: env.BETTER_AUTH_SECRET,
  database: drizzleAdapter(db, {
    provider: 'pg',
    usePlural: true,
    schema,
  }),
  emailAndPassword: {
    enabled: true,
    minPasswordLength: 8,
    requireEmailVerification: true,
    password: {
      // 迁移账号带 studio 旧格式哈希(scrypt$n$r$p$salt$hash),免改密;
      // 新注册/改密走 better-auth 默认(hash 不覆写)。
      verify: async ({ hash, password }) =>
        isLegacyScryptHash(hash)
          ? verifyLegacyScrypt(password, hash)
          : defaultVerify({ hash, password }),
    },
  },
  emailVerification: {
    expiresIn: 60 * 60,
    sendOnSignUp: true,
    sendOnSignIn: true,
    autoSignInAfterVerification: true,
    sendVerificationEmail: async ({ user, url }) => {
      const safeUrl = escapeHtml(url);
      await sendTransactionalEmail({
        to: user.email,
        subject: 'Verify your Iris email',
        text: `Open this link within one hour to verify your Iris account: ${url}`,
        html: `<p>请在一小时内点击下面的链接验证你的 Iris 账号：</p><p><a href="${safeUrl}">验证邮箱</a></p>`,
      });
    },
  },
  user: {
    additionalFields: {
      // 个人工作空间的监控账号上限;input:false 防注册请求注入。
      // 平台 admin 也不能绕过该额度或工作空间隔离。
      monitorQuota: {
        type: 'number',
        required: false,
        defaultValue: env.DEFAULT_MONITOR_QUOTA,
        input: false,
      },
    },
  },
  session: {
    expiresIn: SESSION_MAX_AGE,
    updateAge: 24 * 3600,
  },
  databaseHooks: {
    user: {
      create: {
        // Every account permanently owns a private workspace, independently
        // of whether it later creates or joins one organization.
        after: async (user) => {
          await db.transaction(async (tx) => {
            const [workspace] = await tx
              .insert(workspaces)
              .values({ kind: 'personal', userId: Number(user.id) })
              .returning({ id: workspaces.id });
            await tx.insert(accountGroups).values({
              name: '未分组',
              workspaceId: workspace.id,
              ownerId: Number(user.id),
              sortOrder: 0,
            });
          });
        },
      },
    },
  },
  advanced: {
    database: {
      // 本库其余表全是 serial 主键,auth 表保持一致(user_influencers/
      // account_groups.owner_id 以 integer 引用 users.id)。
      generateId: 'serial',
    },
    ipAddress: {
      // 生产在 nginx 后面,uvicorn 同款约定:nginx 以 $remote_addr 覆写
      // X-Real-IP,外部伪造不进来;限流按它计数。
      ipAddressHeaders: ['x-real-ip', 'x-forwarded-for'],
    },
  },
  rateLimit: {
    // 默认仅生产启用;登录/注册收紧到 10 次/分钟/IP,对齐并入前 studio。
    customRules: {
      '/sign-in/email': { window: 60, max: 10 },
      '/sign-in/username': { window: 60, max: 10 },
      '/sign-up/email': { window: 60, max: 10 },
      '/send-verification-email': { window: 60, max: 5 },
      '/organization/invite-member': { window: 60, max: 10 },
    },
  },
  hooks: {
    // Registration is public unless operations explicitly closes it.
    before: createAuthMiddleware(async (ctx) => {
      if (ctx.path === '/sign-in/email-otp' && env.REGISTER_MODE === 'open') {
        if (typeof ctx.body?.username !== 'string' || !ctx.body.username.trim()) {
          throw new APIError('BAD_REQUEST', { message: 'username required' });
        }
        return;
      }
      if (ctx.path !== '/sign-up/email') return;
      if (env.REGISTER_MODE === 'closed') {
        throw new APIError('FORBIDDEN', { message: 'registration disabled' });
      }
    }),
  },
  plugins: [
    // 用户名登录(迁移账号习惯 username+密码;email 登录同时可用)。
    // min 2 对齐并入前 studio 的 USERNAME_MIN=2,否则存量短用户名没法登录。
    username({ minUsernameLength: 2 }),
    organization({
      organizationLimit: 1,
      membershipLimit: 100,
      invitationExpiresIn: 48 * 60 * 60,
      disableOrganizationDeletion: true,
      requireEmailVerificationOnInvitation: true,
      allowUserToCreateOrganization: async (user) => {
        const existing = await db
          .select({ id: members.id })
          .from(members)
          .where(eq(members.userId, Number(user.id)))
          .limit(1);
        return existing.length === 0;
      },
      schema: {
        organization: {
          additionalFields: {
            monitorQuota: {
              type: 'number',
              required: true,
              defaultValue: env.DEFAULT_MONITOR_QUOTA,
              input: false,
            },
          },
        },
      },
      organizationHooks: {
        afterCreateOrganization: async ({ organization: created }) => {
          await db.transaction(async (tx) => {
            const [workspace] = await tx
              .insert(workspaces)
              .values({ kind: 'team', organizationId: Number(created.id) })
              .returning({ id: workspaces.id });
            await tx.insert(accountGroups).values({
              name: '未分组',
              workspaceId: workspace.id,
              sortOrder: 0,
            });
          });
        },
        beforeAddMember: async ({ user }) => {
          const existing = await db
            .select({ id: members.id })
            .from(members)
            .where(eq(members.userId, Number(user.id)))
            .limit(1);
          if (existing.length) {
            throw new APIError('CONFLICT', { message: 'user already belongs to a team' });
          }
        },
        beforeUpdateMemberRole: async ({ newRole }) => {
          if (newRole.includes('owner') || newRole.includes(',')) {
            throw new APIError('FORBIDDEN', { message: 'use leader transfer' });
          }
        },
      },
      sendInvitationEmail: async ({ id, email, organization: invitedTeam, inviter }) => {
        const url = `${env.NEXT_PUBLIC_APP_URL}/invite?id=${encodeURIComponent(id)}`;
        const safeUrl = escapeHtml(url);
        const safeTeam = escapeHtml(invitedTeam.name);
        const invitingUser = inviter.user as typeof inviter.user & {
          displayUsername?: string | null;
          username?: string | null;
        };
        const inviterName =
          invitingUser.displayUsername ?? invitingUser.username ?? inviter.user.name;
        await sendTransactionalEmail({
          to: email,
          subject: `Join ${invitedTeam.name} on Iris`,
          text: `${inviterName} invited you to join ${invitedTeam.name} on Iris: ${url}`,
          html: `<p>${escapeHtml(inviterName)} 邀请你加入 Iris 团队 <strong>${safeTeam}</strong>。</p><p><a href="${safeUrl}">接受邀请</a></p>`,
        });
      },
    }),
    emailOTP({
      otpLength: 6,
      expiresIn: 5 * 60,
      allowedAttempts: 3,
      storeOTP: 'hashed',
      disableSignUp: env.REGISTER_MODE === 'closed',
      rateLimit: { window: 60, max: 3 },
      sendVerificationOTP: async ({ email, otp, type }) => {
        const isSignIn = type === 'sign-in';
        const isPasswordReset = type === 'forget-password';
        const subject = isSignIn
          ? 'Your Iris sign-in code / Iris 登录验证码'
          : isPasswordReset
            ? 'Your Iris password reset code / Iris 密码重置验证码'
            : 'Your Iris verification code / Iris 邮箱验证码';
        const purposeEn = isSignIn
          ? 'sign in to Iris'
          : isPasswordReset
            ? 'reset your Iris password'
            : 'verify your Iris email address';
        const purposeZh = isSignIn
          ? '登录 Iris'
          : isPasswordReset
            ? '重置你的 Iris 密码'
            : '验证你的 Iris 邮箱地址';
        await sendTransactionalEmail({
          to: email,
          subject,
          text: `Use this code to ${purposeEn}: ${otp}\n\nThis code expires in 5 minutes. If you did not request it, you can safely ignore this email.\n\n使用以下验证码${purposeZh}：${otp}\n\n验证码将在 5 分钟后失效。如果不是你本人操作，请忽略此邮件。`,
          html: `<p>Use this code to ${purposeEn}:</p><p style="font-size:28px;font-weight:700;letter-spacing:6px">${otp}</p><p>This code expires in 5 minutes. If you did not request it, you can safely ignore this email.</p><hr><p>使用以下验证码${purposeZh}：</p><p style="font-size:28px;font-weight:700;letter-spacing:6px">${otp}</p><p>验证码将在 5 分钟后失效。如果不是你本人操作，请忽略此邮件。</p>`,
        });
      },
    }),
    // 平台 role(user/admin) 只用于账号运维，不能绕过工作空间隔离。
    admin(),
    // 必须最后:Server Action/route 里 set-cookie 桥接 Next.js。
    nextCookies(),
  ],
});
