export type Platform = 'tiktok' | 'youtube' | 'instagram' | 'facebook'
export const PLATFORMS: Platform[] = ['tiktok', 'youtube', 'instagram', 'facebook']

export type AccountPublication = {
  id: number
  video_id: number
  platform: Platform
  account_id: number | null
  result: 'SUCCESS' | 'FAILURE'
  caption: string | null
  notes: string | null
  fail_code: number | null
  fail_screenshot: string | null
  geelark_task_id: string | null
  posted_at: string | null
  last_checked_at: string | null
  created_at: string | null
  original_filename: string | null
  stored_path: string | null
  uploaded_at: string | null
  group_id: number | null
}

export type PublicationAttempt = {
  id: number
  publication_id: number
  attempt_no: number
  stage: string
  source: 'gemini' | 'geelark' | 'server' | 'account_config' | string
  message: string
  error_code: string | null
  fail_screenshot: string | null
  geelark_task_id: string | null
  created_at: string | null
}

export type EngagementTaskStatus =
  | 'PENDING' | 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'PARTIAL_FAILURE' | 'FAILURE'

export type EngagementRunResult =
  | 'PENDING' | 'QUEUED' | 'INITIATED' | 'SUCCESS' | 'FAILURE' | 'SKIPPED'

export type EngagementPlatform = 'tiktok' | 'facebook'

export type FbCommentSubmitPayload = {
  account_ids: number[]
  target_links: string[]
  target_publication_ids: number[]
  candidates_per_post: number
  keywords: string[]
  comment_mode: 'studio_ai' | 'provided'
  comment_prompt_id?: number | null
  provided_comments?: string[] | null
  group_id?: number | null
}

export type EngagementTask = {
  id: number
  group_id: number | null
  action: string
  source: string
  target_links: string[]
  target_publication_ids: number[]
  comment_probability: number
  comment_mode: 'geelark_ai' | 'studio_ai' | 'provided'
  comment_prompt_id: number | null
  comment_brief: string | null
  status: EngagementTaskStatus
  summary: string | null
  created_at: string | null
  updated_at: string | null
  finished_at: string | null
}

export type EngagementRun = {
  id: number
  engagement_task_id: number
  account_id: number | null
  account_profile_name?: string | null
  target_publication_id?: number | null
  target_account_id?: number | null
  target_link: string
  result: EngagementRunResult
  notes: string | null
  geelark_task_id: string | null
  fail_screenshot: string | null
  result_screenshot?: string | null
  cost: number | null
  schedule_at?: number | null
  target_title?: string | null
  target_description?: string | null
  comment_text?: string | null
  comment_source?: string | null
  comment_generated_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  last_checked_at?: string | null
  created_at?: string | null
  task_status?: EngagementTaskStatus
  task_source?: string
  task_comment_mode?: 'geelark_ai' | 'studio_ai' | 'provided'
  task_created_at?: string | null
  task_updated_at?: string | null
  task_finished_at?: string | null
  activity_at?: string | null
}

export type EngagementRunsStats = {
  total: number
  success: number
  running: number
  queued: number
  failed: number
}

export type CommentTarget = {
  publication_id: number
  video_id: number
  video_filename: string | null
  group_id: number | null
  account_id: number | null
  account_profile_name: string | null
  share_link: string
  posted_at: string | null
  share_link_captured_at: string | null
}

export type EligiblePhone = {
  id: number
  profile_name: string | null
  group_id: number | null
  group_name: string | null
  busy: boolean
  daily_limit_reached: boolean
  daily_used: number
  daily_limit: number
}

export type DiscoverTarget = {
  url: string
  title: string | null
  author_nickname: string | null
  play_count: number | null
  like_count: number | null
  create_time: string | null   // ISO
  search_keyword: string
}

export type DashboardException =
  | {
      kind: 'publication_failed'
      severity: 'red'
      publication_id: number
      account: string
      account_id: number | null
      video: string
      platform: string
      reason: string
    }
  | {
      kind: 'proxy_down'
      severity: 'orange'
      account_id: number
      account: string
      hours_since: number
    }
  | {
      kind: 'not_logged_in'
      severity: 'orange'
      account_id: number
      account: string
      platform: string
    }
  | {
      kind: 'warmup_stuck'
      severity: 'yellow'
      account_id: number
      account: string
      hours_since: number
    }
  | {
      kind: 'proxy_expiring'
      severity: 'red' | 'orange' | 'yellow'
      account_id: number
      account: string
      group_name: string
      days_remaining: number
    }

export type DashboardData = {
  today: { done: number; failed: number; in_progress: number }
  exceptions: DashboardException[]
  exceptions_total: number
  accounts: { total: number; ready: number; warming: number; not_started: number }
  videos: { unscheduled: number; total: number }
  database: {
    query_ms: number | null
    size_bytes: number | null
    connections: number | null
    active_queries: number | null
    locks: number | null
    tables: number | null
  }
}

export type HealthData = {
  ok: boolean | null
  db?: string | null
  worker_last_seen?: number | null
  error?: string
}

// Fired on any 401 so the app root can flip back to the login screen.
// Previously req() set location.hash='#login', but the app uses
// BrowserRouter — nothing consumed the hash, so an expired session left
// every poll silently 401-ing against stale data with no way back to
// login short of a manual full refresh.
export const AUTH_EXPIRED_EVENT = 'auth:expired'

// nginx 在生产剥掉 /studio 前缀;dev 由 next.config rewrites 代理到本地
// FastAPI(:8080)。二者一致。
export const API_ORIGIN = '/studio'

async function req(path: string, opts: RequestInit = {}) {
  const r = await fetch(API_ORIGIN + '/api' + path, { credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, ...opts })
  if (r.status === 401) {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw new Error('unauthorized')
  }
  const body = await r.json().catch(() => null)
  if (!r.ok) {
    const detail = body && (body.detail || body.message)
    const message = typeof detail === 'string'
      ? detail
      : detail
      ? JSON.stringify(detail)
      : `HTTP ${r.status}`
    throw new Error(message)
  }
  return body
}

export type ReplaceProxyItem = {
  account_id: number
  profile_name: string | null
  proxy: string
  geelark_synced: boolean
  db_updated: boolean
  error: string | null
}

export type ReplaceProxiesResult = {
  items: ReplaceProxyItem[]
  total: number
  synced: number
  failed: number
}

export type CaptionPrompt = {
  id: number
  name: string
  body: string
  created_at: string | null
  updated_at: string | null
}

export type CommentPrompt = {
  id: number
  name: string
  body: string
  created_at: string | null
  updated_at: string | null
}

async function healthReq(): Promise<HealthData> {
  try {
    const r = await fetch(API_ORIGIN + '/healthz', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
    const body = await r.json().catch(() => null)
    if (!body || typeof body !== 'object') {
      return { ok: null, error: `HTTP ${r.status}` }
    }
    return {
      ok: typeof body.ok === 'boolean' ? body.ok : null,
      db: typeof body.db === 'string' ? body.db : null,
      worker_last_seen:
        typeof body.worker_last_seen === 'number' ? body.worker_last_seen : null,
      error: r.ok ? undefined : `HTTP ${r.status}`,
    }
  } catch (e) {
    return {
      ok: null,
      error: e instanceof Error ? e.message : String(e),
    }
  }
}

export type WalletData = {
  balance?: number
  giftMoney?: number
  availableTimeAddOn?: number
  error?: string
}

export type CostAttribution = {
  kind: 'publish' | 'warmup' | 'engagement' | 'idle'
  label: string
  ref_type: string | null
  ref_id: number | null
  confidence: 'high' | 'medium' | 'low'
  reason: string | null
}

export type CostTransaction = {
  id: string
  time: string
  amount_usd: number
  used_min: number
  env_id: string
  account: { id: number; profile_name: string | null } | null
  group: { id: number; name: string | null } | null
  attribution: CostAttribution
}

export type CostKpis = {
  today_spent: number
  mtd_spent: number
  day_avg_30d: number
  idle_30d_pct: number
}

export type CostSummaryGroup = {
  key: string
  label: string
  count: number
  total: number
}

export type OpsSession = {
  id: number
  account_id: number
  profile_name: string | null
  env_id: string
  source: string
  owner_type: string | null
  owner_id: number | null
  geelark_task_id: string | null
  status: string
  started_at: string
  budget_deadline_at: string
  created_by: string
  overdue: boolean
}

export type RpaWatchdogHealth = {
  success: boolean
  service: string
  startupTimeoutSeconds: number
  firstUserTimeoutSeconds: number
  watchdogTimeoutSeconds: number
  recentUserCacheSize: number
  recentUserHitThreshold: number
  tokenMode: 'per-request-geelark-token'
  errorScreenshotEnabled: boolean
  videoBlacklistCount: number
}

export type RpaWatchdogTask = {
  taskId: string
  deviceId: string
  registeredAt: string
  secondsSinceRegistered: number
  startupSource: string
  firstHeartbeatReceived: boolean
  firstUserReceived: boolean
  lastProgressId: string
  lastNewProgressAt: string
  lastRequestAt: string
  secondsSinceNewProgress: number | null
  status: string
  heartbeatCount: number
  newProgressCount: number
  recentUsers: string[]
  recentUserCount: number
  consecutiveCacheHits: number
  cleanupReason: string | null
  cleanupError: string | null
  geelarkTaskStatus: number | null
}

export type StartRpaWatchdogTask = {
  deviceId: string
  flowId: string
  scheduleAt?: number | null
  paramMap: Record<string, unknown>
}

export type SagaKind = 'publish' | 'warmup' | 'engagement'

export type SagaView = {
  saga_id: number
  kind: SagaKind
  business_ref: string
  account_id: number
  phone_session_id: number | null
  geelark_task_id: string | null
  current_step: string
  attempt: number
  planned_at: string
  started_at: string | null
  updated_at: string
  finished_at: string | null
  deadline_at: string | null
  elapsed_seconds: number
  seconds_at_current_step: number
  step_budget_seconds: number | null
  budget_remaining_seconds: number | null
  is_stuck: boolean
  is_step_stuck: boolean
  is_terminal: boolean
  failure_reason: string | null
}

export type SagaSummary = {
  in_flight_by_kind: Partial<Record<SagaKind, number>>
  stuck: number
  step_stuck: number
}

export type Me = { id: number; username: string; email: string }

export const api = {
  // 登录/注册/登出/改密/用户管理在主站(radar better-auth,2026-07-17 翻转):
  // 登录页 /login,登出 POST /api/auth/sign-out(见 App.tsx)。这里只剩
  // 登录态探测；401=未登录，403=当前工作空间角色无权访问该端点。
  me: (): Promise<Me> => req('/me'),
  healthz: healthReq,
  wallet: (): Promise<WalletData> => req('/wallet'),
  opsSessions: (status: 'active' | 'overdue' = 'active'):
    Promise<{ items: OpsSession[] }> =>
    req(`/ops/sessions?status=${status}`),
  opsReleaseSession: (id: number): Promise<{ ok: true; already_stopped?: boolean }> =>
    req(`/ops/sessions/${id}/release`, { method: 'POST' }),
  opsRpaWatchdogHealth: (): Promise<RpaWatchdogHealth> =>
    req('/ops/rpa-watchdog/health'),
  opsRpaWatchdogTasks: (): Promise<{ success: boolean; count: number; tasks: RpaWatchdogTask[] }> =>
    req('/ops/rpa-watchdog/tasks'),
  opsStartRpaWatchdogTask: (body: StartRpaWatchdogTask):
    Promise<{ success: boolean; taskId: string; deviceId: string; flowId: string }> =>
    req('/ops/rpa-watchdog/start', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  opsStopRpaWatchdogTask: (taskId: string):
    Promise<{ success: boolean; scheduled: boolean; status: string }> =>
    req(`/ops/rpa-watchdog/tasks/${encodeURIComponent(taskId)}/stop`, {
      method: 'POST',
    }),
  opsDeleteRpaWatchdogTask: (taskId: string):
    Promise<{ success: boolean; deleted: boolean; taskId: string }> =>
    req(`/ops/rpa-watchdog/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
    }),
  opsSagas: (kind?: SagaKind, limit = 500):
    Promise<{ items: SagaView[] }> => {
    const q = new URLSearchParams()
    if (kind) q.set('kind', kind)
    q.set('limit', String(limit))
    return req(`/ops/sagas/in-flight?${q.toString()}`)
  },
  opsSagaSummary: (): Promise<SagaSummary> =>
    req('/ops/sagas/summary'),
  opsDescribeSaga: (kind: SagaKind, businessRef: string):
    Promise<SagaView> =>
    req(`/ops/sagas/${kind}/${encodeURIComponent(businessRef)}`),
  accounts: () => req('/accounts'),
  updateAccount: (id: number, b: Record<string, unknown>) =>
    req(`/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(b) }),
  deleteAccount: (id: number) => req(`/accounts/${id}`, { method: 'DELETE' }),
  batchDeleteAccounts: (ids: number[]) =>
    req('/accounts/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) }),
  replaceProxies: (accountIds: number[], proxies: string[]):
    Promise<ReplaceProxiesResult> =>
    req('/accounts/replace-proxies', {
      method: 'POST',
      body: JSON.stringify({ account_ids: accountIds, proxies }),
    }),
  loginToggle: (id: number) =>
    req(`/accounts/${id}/login-toggle`, { method: 'PATCH' }),
  holdPhone: (id: number, hours?: number): Promise<{ phone_busy_until: string }> =>
    req(`/accounts/${id}/phone/hold`,
        { method: 'POST',
          body: JSON.stringify(hours != null ? { hours } : {}) }),
  releasePhone: (id: number): Promise<{ ok: true }> =>
    req(`/accounts/${id}/phone/release`, { method: 'POST' }),
  accountPublications: (id: number): Promise<AccountPublication[]> =>
    req(`/accounts/${id}/publications`),
  importGeelark: () => req('/accounts/import-from-geelark', { method: 'POST' }),
  videos: (params: {
    group_id?: number | 'all' | 'ungrouped'
    status?: 'all' | 'pending' | 'in_progress' | 'handled'
    cursor?: number | string | null
    limit?: number
  } = {}) => {
    const q = new URLSearchParams()
    if (params.group_id !== undefined) q.set('group_id', String(params.group_id))
    if (params.status !== undefined) q.set('status', params.status)
    if (params.cursor != null) q.set('cursor', String(params.cursor))
    if (params.limit !== undefined) q.set('limit', String(params.limit))
    const qs = q.toString()
    return req('/videos' + (qs ? '?' + qs : ''))
  },
  videoDetail: (id: number) => req(`/videos/${id}/detail`),
  deleteVideo: (id: number) => req(`/videos/${id}`, { method: 'DELETE' }),
  importList: (folderUrl: string) =>
    req('/videos/import/list', { method: 'POST', body: JSON.stringify({ folder_url: folderUrl }) }),
  importDrive: (files: { id: string; name: string }[], groupId?: number | null, platforms?: string[]) =>
    req('/videos/import', {
      method: 'POST',
      body: JSON.stringify({
        files,
        ...(groupId != null ? { group_id: groupId } : {}),
        ...(platforms != null ? { platforms } : {}),
      }),
    }),
  importStatus: () => req('/videos/import/status'),
  setVideoAccount: (id: number, accountId: number | null) =>
    req(`/videos/${id}`,
        { method: 'PATCH', body: JSON.stringify({ account_id: accountId }) }),
  assignVideos: (video_ids: number[], group_id: number) =>
    req('/videos/assign', { method: 'POST', body: JSON.stringify({ video_ids, group_id }) }),
  publicationAttempts: (pubId: number): Promise<PublicationAttempt[]> =>
    req(`/videos/publications/${pubId}/attempts`),
  retryPublication: (pubId: number) =>
    req(`/videos/publications/${pubId}/retry`, { method: 'POST' }),
  dashboard: (): Promise<DashboardData> => req('/dashboard'),
  dashboardExceptionSummary: (): Promise<{ summary: string }> =>
    req('/dashboard/exceptions/summary', { method: 'POST' }),
  settings: () => req('/settings'),
  updateSettings: (b: Record<string, unknown>) =>
    req('/settings', { method: 'PATCH', body: JSON.stringify(b) }),
  geminiModels: () => req('/settings/gemini-models'),
  secrets: () => req('/settings/secrets'),
  updateSecrets: (b: Record<string, string>) =>
    req('/settings/secrets', { method: 'PATCH', body: JSON.stringify(b) }),
  markAccountsWarmedUp: (account_ids: number[]) =>
    req('/accounts/mark-warmed-up',
        { method: 'POST', body: JSON.stringify({ account_ids }) }),
  setWarmupMode: (account_ids: number[], mode: 'count' | 'daily') =>
    req('/accounts/warmup-mode',
        { method: 'POST', body: JSON.stringify({ account_ids, mode }) }),
  createGroup: (body: {
    name: string
    notes?: string | null
  }) => req('/groups', { method: 'POST', body: JSON.stringify(body) }),
  patchGroup: (id: number,
               body: { name?: string; caption_prompt_id?: number | null }) =>
    req(`/groups/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteGroup: (id: number) =>
    req(`/groups/${id}`, { method: 'DELETE' }),
  captionPrompts: (): Promise<CaptionPrompt[]> =>
    req('/caption-prompts'),
  createCaptionPrompt: (body: { name: string; body: string }) =>
    req('/caption-prompts', { method: 'POST', body: JSON.stringify(body) }),
  updateCaptionPrompt: (id: number,
                        body: { name?: string; body?: string }) =>
    req(`/caption-prompts/${id}`,
        { method: 'PATCH', body: JSON.stringify(body) }),
  deleteCaptionPrompt: (id: number) =>
    req(`/caption-prompts/${id}`, { method: 'DELETE' }),
  commentPrompts: (): Promise<CommentPrompt[]> =>
    req('/comment-prompts'),
  createCommentPrompt: (body: { name: string; body: string }) =>
    req('/comment-prompts', { method: 'POST', body: JSON.stringify(body) }),
  updateCommentPrompt: (id: number,
                        body: { name?: string; body?: string }) =>
    req(`/comment-prompts/${id}`,
        { method: 'PATCH', body: JSON.stringify(body) }),
  deleteCommentPrompt: (id: number) =>
    req(`/comment-prompts/${id}`, { method: 'DELETE' }),
  createEngagementComment: (body: {
    account_ids: number[]
    target_links?: string[]
    target_publication_ids?: number[]
    comments_per_video: number
    comment_prompt_id?: number | null
  }): Promise<{ task_id: number; created_runs: number; deferred_runs: number }> =>
    req('/engagement/comment', { method: 'POST', body: JSON.stringify(body) }),
  createFbEngagementComment: (
    body: FbCommentSubmitPayload,
  ): Promise<{
    task_id: number
    task_ids: number[]
    run_ids: number[]
    created_runs: number
    skipped_runs: number
    deferred_runs: number
  }> =>
    req('/engagement/comment/facebook', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  engagementTasks: (groupId?: number): Promise<EngagementTask[]> => {
    const q = new URLSearchParams()
    if (groupId != null) q.set('group_id', String(groupId))
    const qs = q.toString()
    return req('/engagement/tasks' + (qs ? '?' + qs : ''))
  },
  engagementRuns: (params: {
    groupId?: number
    limit?: number
    beforeId?: number
    ids?: number[]
    beforeActivityAt?: string
    status?: 'all' | 'success' | 'running' | 'queued' | 'failed'
    source?: 'all' | 'owned' | 'manual'
    platform?: EngagementPlatform
  } = {}): Promise<{ items: EngagementRun[] }> => {
    const q = new URLSearchParams()
    if (params.groupId != null) q.set('group_id', String(params.groupId))
    if (params.limit != null) q.set('limit', String(params.limit))
    if (params.beforeId != null) q.set('before_id', String(params.beforeId))
    if (params.ids) {
      for (const id of params.ids) q.append('ids', String(id))
    }
    if (params.beforeActivityAt != null)
      q.set('before_activity_at', params.beforeActivityAt)
    if (params.status && params.status !== 'all') q.set('status', params.status)
    if (params.source && params.source !== 'all') q.set('source', params.source)
    if (params.platform) q.set('platform', params.platform)
    const qs = q.toString()
    return req('/engagement/runs' + (qs ? '?' + qs : ''))
  },
  engagementRunsStats: (params: {
    groupId?: number
    source?: 'all' | 'owned' | 'manual'
    platform?: EngagementPlatform
  } = {}): Promise<EngagementRunsStats> => {
    const q = new URLSearchParams()
    if (params.groupId != null) q.set('group_id', String(params.groupId))
    if (params.source && params.source !== 'all') q.set('source', params.source)
    if (params.platform) q.set('platform', params.platform)
    const qs = q.toString()
    return req('/engagement/runs/stats' + (qs ? '?' + qs : ''))
  },
  engagementTargets: (
    opts: { groupId?: number; platform?: EngagementPlatform } = {},
  ): Promise<{ items: CommentTarget[] }> => {
    const qs = new URLSearchParams()
    if (opts.groupId != null) qs.set('group_id', String(opts.groupId))
    if (opts.platform) qs.set('platform', opts.platform)
    return req('/engagement/targets' + (qs.toString() ? '?' + qs.toString() : ''))
  },
  eligiblePhones: (
    opts: { platform?: EngagementPlatform } = {},
  ): Promise<{ items: EligiblePhone[] }> => {
    const qs = new URLSearchParams()
    if (opts.platform) qs.set('platform', opts.platform)
    return req('/engagement/eligible-phones' + (qs.toString() ? '?' + qs.toString() : ''))
  },
  discoverTargets: (limit = 20000): Promise<{ items: DiscoverTarget[] }> =>
    req(`/engagement/discover-targets?limit=${limit}`),
  engagementTask: (id: number): Promise<{ task: EngagementTask; runs: EngagementRun[] }> =>
    req(`/engagement/tasks/${id}`),
  costTransactions: (params: {
    page?: number
    page_size?: number
    account_id?: number | null
    group_id?: number | null
    kind?: CostAttribution['kind'] | null
    date_from?: string | null
    date_to?: string | null
  }): Promise<{
    items: CostTransaction[]
    total: number
    page: number
    page_size: number
  }> => {
    const q = new URLSearchParams()
    if (params.page != null) q.set('page', String(params.page))
    if (params.page_size != null) q.set('page_size', String(params.page_size))
    if (params.account_id != null) q.set('account_id', String(params.account_id))
    if (params.group_id != null) q.set('group_id', String(params.group_id))
    if (params.kind) q.set('kind', params.kind)
    if (params.date_from) q.set('date_from', params.date_from)
    if (params.date_to) q.set('date_to', params.date_to)
    const qs = q.toString()
    return req('/costs/transactions' + (qs ? '?' + qs : ''))
  },
  costKpis: (): Promise<CostKpis> => req('/costs/kpis'),
  costSummary: (params: {
    group_by: 'day' | 'account' | 'group' | 'kind'
    date_from?: string | null
    date_to?: string | null
  }): Promise<{ groups: CostSummaryGroup[] }> => {
    const q = new URLSearchParams()
    q.set('group_by', params.group_by)
    if (params.date_from) q.set('date_from', params.date_from)
    if (params.date_to) q.set('date_to', params.date_to)
    return req('/costs/summary?' + q.toString())
  },
  costExportCsvUrl: (params: {
    date_from?: string | null
    date_to?: string | null
    account_id?: number | null
  }): string => {
    const q = new URLSearchParams()
    if (params.date_from) q.set('date_from', params.date_from)
    if (params.date_to) q.set('date_to', params.date_to)
    if (params.account_id != null) q.set('account_id', String(params.account_id))
    const qs = q.toString()
    return API_ORIGIN + '/api/costs/export.csv' + (qs ? '?' + qs : '')
  },
}
