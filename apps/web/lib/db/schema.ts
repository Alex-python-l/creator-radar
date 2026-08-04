import { sql } from 'drizzle-orm';
import {
  boolean,
  foreignKey,
  index,
  integer,
  jsonb,
  pgTable,
  primaryKey,
  serial,
  text,
  timestamp,
  uniqueIndex,
} from 'drizzle-orm/pg-core';

/**
 * 认证四表 = better-auth 官方 CLI generate 的标准形状(usePlural +
 * generateId:'serial' + username/admin 插件 + monitorQuota 附加字段),
 * 仅把列改成 timestamptz 对齐库内惯例。密码哈希存 accounts 表
 * (providerId='credential'),迁移的 studio 旧账号哈希带 scrypt$ 前缀,
 * 由 lib/auth 的 verify 分流兼容。字段增删必须与 lib/auth/index.ts
 * 的插件/附加字段配置同步,否则 better-auth 运行时报 schema 不匹配。
 *
 * role 是平台运维权限，不参与 Radar/Studio 的工作空间数据范围判断。
 * monitor_quota 是个人工作空间订阅上限，团队额度存 organizations。
 */
export const users = pgTable('users', {
  id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
  name: text('name').notNull(),
  email: text('email').notNull().unique(),
  emailVerified: boolean('email_verified').default(false).notNull(),
  image: text('image'),
  createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true })
    .defaultNow()
    .$onUpdate(() => new Date())
    .notNull(),
  username: text('username').unique(),
  displayUsername: text('display_username'),
  role: text('role'),
  banned: boolean('banned').default(false),
  banReason: text('ban_reason'),
  banExpires: timestamp('ban_expires', { withTimezone: true }),
  monitorQuota: integer('monitor_quota').default(0),
});

/**
 * Better Auth Organization plugin tables. Product copy calls an organization a
 * "team"; nested Organization teams are deliberately not enabled.
 */
export const organizations = pgTable('organizations', {
  id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
  name: text('name').notNull(),
  slug: text('slug').notNull().unique(),
  logo: text('logo'),
  metadata: text('metadata'),
  monitorQuota: integer('monitor_quota').notNull().default(0),
  createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true })
    .defaultNow()
    .$onUpdate(() => new Date()),
});

export const members = pgTable(
  'members',
  {
    id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
    organizationId: integer('organization_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    userId: integer('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    role: text('role').notNull().default('member'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [
    uniqueIndex('members_user_uq').on(t.userId),
    uniqueIndex('members_org_user_uq').on(t.organizationId, t.userId),
    uniqueIndex('members_one_owner_per_org_uq')
      .on(t.organizationId)
      .where(sql`${t.role} = 'owner'`),
    index('members_organization_idx').on(t.organizationId),
  ],
);

export const invitations = pgTable(
  'invitations',
  {
    id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
    organizationId: integer('organization_id')
      .notNull()
      .references(() => organizations.id, { onDelete: 'cascade' }),
    email: text('email').notNull(),
    role: text('role').notNull(),
    status: text('status').notNull().default('pending'),
    expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
    inviterId: integer('inviter_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [
    index('invitations_organization_idx').on(t.organizationId),
    index('invitations_email_idx').on(t.email),
  ],
);

/** One private workspace per user and one shared workspace per organization. */
export const workspaces = pgTable(
  'workspaces',
  {
    id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
    kind: text('kind').notNull(),
    userId: integer('user_id').references(() => users.id, { onDelete: 'cascade' }),
    organizationId: integer('organization_id').references(() => organizations.id, {
      onDelete: 'cascade',
    }),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  },
  (t) => [
    uniqueIndex('workspaces_user_uq').on(t.userId),
    uniqueIndex('workspaces_organization_uq').on(t.organizationId),
  ],
);

export const sessions = pgTable(
  'sessions',
  {
    id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
    expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
    token: text('token').notNull().unique(),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true })
      .$onUpdate(() => new Date())
      .notNull(),
    ipAddress: text('ip_address'),
    userAgent: text('user_agent'),
    userId: integer('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    impersonatedBy: text('impersonated_by'),
    activeOrganizationId: integer('active_organization_id').references(() => organizations.id, {
      onDelete: 'set null',
    }),
  },
  (t) => [index('sessions_userId_idx').on(t.userId)],
);

export const accounts = pgTable(
  'accounts',
  {
    id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
    accountId: text('account_id').notNull(),
    providerId: text('provider_id').notNull(),
    userId: integer('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    accessToken: text('access_token'),
    refreshToken: text('refresh_token'),
    idToken: text('id_token'),
    accessTokenExpiresAt: timestamp('access_token_expires_at', { withTimezone: true }),
    refreshTokenExpiresAt: timestamp('refresh_token_expires_at', { withTimezone: true }),
    scope: text('scope'),
    password: text('password'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true })
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (t) => [index('accounts_userId_idx').on(t.userId)],
);

export const verifications = pgTable(
  'verifications',
  {
    id: integer('id').generatedByDefaultAsIdentity().primaryKey(),
    identifier: text('identifier').notNull(),
    value: text('value').notNull(),
    expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true })
      .defaultNow()
      .$onUpdate(() => new Date())
      .notNull(),
  },
  (t) => [index('verifications_identifier_idx').on(t.identifier)],
);

export type User = typeof users.$inferSelect;
export type NewUser = typeof users.$inferInsert;

/**
 * Platform administration audit trail. Payloads contain only the fields that
 * changed; secrets, credentials, session tokens, and customer business data
 * must never be written here.
 */
export const adminAuditLogs = pgTable(
  'admin_audit_logs',
  {
    id: serial('id').primaryKey(),
    actorUserId: integer('actor_user_id').references(() => users.id, {
      onDelete: 'set null',
    }),
    action: text('action').notNull(),
    targetType: text('target_type').notNull(),
    targetId: text('target_id').notNull(),
    before: jsonb('before'),
    after: jsonb('after'),
    reason: text('reason'),
    actorIp: text('actor_ip'),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    index('admin_audit_logs_created_idx').on(t.createdAt),
    index('admin_audit_logs_actor_created_idx').on(t.actorUserId, t.createdAt),
    index('admin_audit_logs_target_created_idx').on(t.targetType, t.targetId, t.createdAt),
  ],
);

export type AdminAuditLog = typeof adminAuditLogs.$inferSelect;
export type NewAdminAuditLog = typeof adminAuditLogs.$inferInsert;

/**
 * 账号分组。owner_id NULL = 系统组(admin 全局视图专用,含 id=1 未分组);
 * 普通用户自建组 owner_id=自己。同名唯一按 owner 划分(见手工迁移 0009:
 * 系统组之间用 partial unique index 保持全局唯一)。
 */
export const accountGroups = pgTable(
  'account_groups',
  {
    id: serial('id').primaryKey(),
    name: text('name').notNull(),
    workspaceId: integer('workspace_id').references(() => workspaces.id, {
      onDelete: 'cascade',
    }),
    /** @deprecated migration compatibility; new code uses workspaceId. */
    ownerId: integer('owner_id').references(() => users.id, { onDelete: 'cascade' }),
    sortOrder: integer('sort_order').notNull().default(0),
    createdAt: timestamp('created_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    index('account_groups_sort_idx').on(t.sortOrder),
    index('account_groups_owner_idx').on(t.ownerId),
    index('account_groups_workspace_idx').on(t.workspaceId),
    // 名称唯一性已在 0010/0015 迁移为 workspace 内唯一：
    // account_groups_workspace_name_uq ON (workspace_id,name)
    // WHERE workspace_id IS NOT NULL。Drizzle 不表达 partial index。
  ],
);

export type AccountGroup = typeof accountGroups.$inferSelect;
export type NewAccountGroup = typeof accountGroups.$inferInsert;

/**
 * 监控的账号。
 */
export const influencers = pgTable(
  'influencers',
  {
    id: serial('id').primaryKey(),
    platform: text('platform').notNull(),
    agentName: text('agent_name').notNull().default(''),
    username: text('username').notNull(),
    displayName: text('display_name'),
    avatarUrl: text('avatar_url'),
    profileUrl: text('profile_url'),
    bio: text('bio'),

    followerCount: integer('follower_count').notNull().default(0),
    followingCount: integer('following_count').notNull().default(0),
    videoCount: integer('video_count').notNull().default(0),

    accountCategory: text('account_category').notNull().default(''),
    accountType: text('account_type').notNull().default(''),
    robotNumber: text('robot_number').notNull().default(''),
    country: text('country').notNull().default(''),
    alias: text('alias'),
    groupId: integer('group_id')
      .notNull()
      .default(1)
      .references(() => accountGroups.id),

    isMonitored: boolean('is_monitored').notNull().default(false),
    isActive: boolean('is_active').notNull().default(true),
    viewThreshold: integer('view_threshold').notNull().default(0),

    lastFetchedAt: timestamp('last_fetched_at', { withTimezone: true }),
    createdAt: timestamp('created_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex('influencers_platform_username_uq').on(t.platform, t.username),
    index('influencers_monitored_idx').on(t.isMonitored, t.isActive),
    index('influencers_group_id_idx').on(t.groupId),
  ],
);

/**
 * Per-group opt-in state for display numbering in Account Management.
 *
 * The state row is separate from the detail rows so an empty numbered group can
 * remain in numbering mode and regenerate when accounts enter later.
 */
export const accountGroupNumbering = pgTable(
  'account_group_numbering',
  {
    groupId: integer('group_id').primaryKey(),
    enabledAt: timestamp('enabled_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
    regeneratedAt: timestamp('regenerated_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    foreignKey({
      name: 'account_group_numbering_group_fk',
      columns: [t.groupId],
      foreignColumns: [accountGroups.id],
    }).onDelete('cascade'),
  ],
);

/**
 * Per-account display numbers for a numbered group.
 *
 * Source identity remains influencers.id; this table only controls the visual
 * badge and display ordering in Account Management.
 */
export const accountGroupNumbers = pgTable(
  'account_group_numbers',
  {
    groupId: integer('group_id').notNull(),
    influencerId: integer('influencer_id').notNull(),
    displayNo: integer('display_no').notNull(),
    sortLabel: text('sort_label').notNull(),
    generatedAt: timestamp('generated_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    primaryKey({ columns: [t.groupId, t.influencerId] }),
    foreignKey({
      name: 'account_group_numbers_group_fk',
      columns: [t.groupId],
      foreignColumns: [accountGroupNumbering.groupId],
    }).onDelete('cascade'),
    foreignKey({
      name: 'account_group_numbers_influencer_fk',
      columns: [t.influencerId],
      foreignColumns: [influencers.id],
    }).onDelete('cascade'),
    uniqueIndex('account_group_numbers_group_display_no_uq').on(t.groupId, t.displayNo),
    index('account_group_numbers_influencer_idx').on(t.influencerId),
  ],
);

/**
 * 普通用户 ↔ 监控账号的订阅关系(admin 全局视图不走本表)。
 *
 * influencers/videos 全局一份:多人监控同一红人只抓一次,新订阅立即
 * 看到历史数据。group_id/alias 是订阅者私有的视图属性——弱引用
 * account_groups(应用层保证组 owner=订阅者),NULL=未分组;与
 * influencers.group_id/alias(admin 世界的全局属性)互不干扰。
 */
export const userInfluencers = pgTable(
  'user_influencers',
  {
    userId: integer('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    influencerId: integer('influencer_id')
      .notNull()
      .references(() => influencers.id, { onDelete: 'cascade' }),
    groupId: integer('group_id'),
    alias: text('alias'),
    createdAt: timestamp('created_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    primaryKey({ columns: [t.userId, t.influencerId] }),
    index('user_influencers_influencer_idx').on(t.influencerId),
    index('user_influencers_user_group_idx').on(t.userId, t.groupId),
  ],
);

export type UserInfluencer = typeof userInfluencers.$inferSelect;
export type NewUserInfluencer = typeof userInfluencers.$inferInsert;

/** Workspace-scoped subscription and presentation metadata. */
export const workspaceInfluencers = pgTable(
  'workspace_influencers',
  {
    workspaceId: integer('workspace_id')
      .notNull()
      .references(() => workspaces.id, { onDelete: 'cascade' }),
    influencerId: integer('influencer_id')
      .notNull()
      .references(() => influencers.id, { onDelete: 'cascade' }),
    groupId: integer('group_id'),
    alias: text('alias'),
    /** 订阅层：'own'=自己的账号（默认，存量全部归此），'competitor'=竞品。
     *  CHECK 约束在 DB 侧（见 drizzle/manual/0014）。竞品行 group_id 恒为 NULL。 */
    relation: text('relation').notNull().default('own'),
    createdAt: timestamp('created_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    primaryKey({ columns: [t.workspaceId, t.influencerId] }),
    index('workspace_influencers_influencer_idx').on(t.influencerId),
    index('workspace_influencers_workspace_group_idx').on(t.workspaceId, t.groupId),
  ],
);

export type WorkspaceInfluencer = typeof workspaceInfluencers.$inferSelect;
export type NewWorkspaceInfluencer = typeof workspaceInfluencers.$inferInsert;

/**
 * 视频条目。raw 字段存抓取时的原始 payload。
 */
export const videos = pgTable(
  'videos',
  {
    id: serial('id').primaryKey(),
    influencerId: integer('influencer_id')
      .notNull()
      .references(() => influencers.id, { onDelete: 'cascade' }),
    platform: text('platform').notNull(),
    videoId: text('video_id').notNull(),
    videoUrl: text('video_url'),
    description: text('description'),
    thumbnailUrl: text('thumbnail_url'),
    durationSeconds: integer('duration_seconds'),

    playCount: integer('play_count').notNull().default(0),
    likeCount: integer('like_count').notNull().default(0),
    commentCount: integer('comment_count').notNull().default(0),
    shareCount: integer('share_count').notNull().default(0),
    contentType: text('content_type').notNull().default('video'),

    publishedAt: timestamp('published_at', { withTimezone: true }),
    capturedAt: timestamp('captured_at', { withTimezone: true })
      .notNull()
      .defaultNow(),

    raw: jsonb('raw'),
  },
  (t) => [
    uniqueIndex('videos_platform_videoid_uq').on(t.platform, t.videoId),
    index('videos_influencer_published_idx').on(t.influencerId, t.publishedAt),
    index('videos_play_count_idx').on(t.playCount),
    // drizzle 不表达 partial index `WHERE`,DB 侧实际带 WHERE published_at IS NOT NULL
    // (见 drizzle/manual/0008_*.sql)。此处仅声明索引存在,避免 db:push 误删。
    index('videos_published_at_idx').on(t.publishedAt),
  ],
);

/**
 * 抓取日志。每次抓一个账号写一行。
 */
export const fetchLogs = pgTable(
  'fetch_logs',
  {
    id: serial('id').primaryKey(),
    influencerId: integer('influencer_id').references(() => influencers.id, {
      onDelete: 'cascade',
    }),
    platform: text('platform').notNull(),
    status: text('status').notNull(),
    foundCount: integer('found_count').notNull().default(0),
    newCount: integer('new_count').notNull().default(0),
    errorMessage: text('error_message'),
    errorCode: text('error_code'),
    startedAt: timestamp('started_at', { withTimezone: true })
      .notNull()
      .defaultNow(),
    finishedAt: timestamp('finished_at', { withTimezone: true }),
  },
  (t) => [
    index('fetch_logs_started_idx').on(t.startedAt),
    // 服务 listInfluencersWithRecentCount 的 LATERAL "取该账号最新 fetch_log"
    // 子查询。实际 DB 是 (influencer_id, id DESC),drizzle schema 不表达 DESC,
    // 但 PG BTREE 反向扫等价。手工迁移在 drizzle/manual/0002_*.sql。
    index('fetch_logs_influencer_id_idx').on(t.influencerId, t.id),
  ],
);

export type Influencer = typeof influencers.$inferSelect;
export type NewInfluencer = typeof influencers.$inferInsert;
export type AccountGroupNumbering = typeof accountGroupNumbering.$inferSelect;
export type NewAccountGroupNumbering = typeof accountGroupNumbering.$inferInsert;
export type AccountGroupNumber = typeof accountGroupNumbers.$inferSelect;
export type NewAccountGroupNumber = typeof accountGroupNumbers.$inferInsert;
export type Video = typeof videos.$inferSelect;
export type NewVideo = typeof videos.$inferInsert;
export type FetchLog = typeof fetchLogs.$inferSelect;

export const jobs = pgTable(
  'jobs',
  {
    id: serial('id').primaryKey(),
    kind: text('kind').notNull(),
    payload: jsonb('payload').notNull(),
    status: text('status').notNull().default('queued'),
    retryCount: integer('retry_count').notNull().default(0),
    maxRetries: integer('max_retries').notNull().default(2),
    nextRunAt: timestamp('next_run_at', { withTimezone: true }).notNull().defaultNow(),
    lockedBy: text('locked_by'),
    lockedAt: timestamp('locked_at', { withTimezone: true }),
    lastError: text('last_error'),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }),
  },
  (t) => [index('jobs_status_next_run_idx').on(t.status, t.nextRunAt)],
);

export type Job = typeof jobs.$inferSelect;
export type NewJob = typeof jobs.$inferInsert;

/**
 * Public trial / pricing applications. Access to this table is admin-only;
 * the public form writes through a validated Server Action.
 */
export const trialApplications = pgTable(
  'trial_applications',
  {
    id: serial('id').primaryKey(),
    companyName: text('company_name').notNull(),
    contactName: text('contact_name').notNull(),
    workEmail: text('work_email'),
    wechat: text('wechat'),
    expectedAccounts: integer('expected_accounts').notNull(),
    plan: text('plan').notNull(),
    locale: text('locale').notNull().default('zh'),
    notes: text('notes'),
    status: text('status').notNull().default('new'),
    organizationId: integer('organization_id').references(() => organizations.id, {
      onDelete: 'set null',
    }),
    grantedQuota: integer('granted_quota'),
    processedBy: integer('processed_by').references(() => users.id, {
      onDelete: 'set null',
    }),
    processedAt: timestamp('processed_at', { withTimezone: true }),
    consentAt: timestamp('consent_at', { withTimezone: true }).notNull().defaultNow(),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true })
      .notNull()
      .defaultNow()
      .$onUpdate(() => new Date()),
  },
  (t) => [
    index('trial_applications_status_created_idx').on(t.status, t.createdAt),
    index('trial_applications_email_idx').on(t.workEmail),
    index('trial_applications_organization_idx').on(t.organizationId),
  ],
);

export type TrialApplication = typeof trialApplications.$inferSelect;
export type NewTrialApplication = typeof trialApplications.$inferInsert;
