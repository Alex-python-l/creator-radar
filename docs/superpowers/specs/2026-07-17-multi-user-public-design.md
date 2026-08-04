# 多用户系统·面向公众(设计)

- 日期:2026-07-17
- 状态:已实施(同日)。**实施与本文的差异**:认证不自写,采用 **better-auth 1.6**
  (drizzle adapter + username/admin 插件 + `monitorQuota` 附加字段 + serial id)——
  cookie 为其 DB session(`better-auth.session_token`),非本文草拟的 JWT;
  邀请码在 sign-up before hook 校验;`session_ver` 由 DB session 天然取代;
  studio 转发的端点是 better-auth 标准 `GET /api/auth/get-session`。
  另:普通用户注册时自动建私有"未分组"(databaseHook),user 世界与 admin
  世界同构,URL/组件零特判。
- 前置:2026-07-16 门户统一(已上线)。彼时"一套账号全员通行、不加角色"的决策是内部工具语境;本次面向公众,该语境作废。

## 背景与目标

产品要向公众开放。现状有两个根本不匹配:

1. **认证中心在内部工具里**:users 表与注册/登录都在 studio(`mkt` 库,FastAPI),radar 只转发校验。公众产品的账号体系不能寄生在云手机运营后台上——注册流量、限流、账号管理都不该打到 studio。
2. **数据零隔离**:所有 influencers/videos/分组全员共享。公众用户必须只看自己添加的监控账号,且 studio(烧钱的 GeeLark 运营)绝不能对公众开放。

目标:radar 自持多用户体系(注册/登录/角色/配额),公众用户数据隔离,studio 收编为 admin-only 内部工具。scraper 与 GeeLark `:8080` 回调链路零改动。

## 决策

| 议题 | 决定 |
|---|---|
| 认证中心 | **翻转**:users 表迁到 radar(`short_video_monitor` 库,Drizzle),radar 签发 JWT cookie;studio 的 `require_auth` 改为转发调 radar `GET /api/auth/verify`(与现状方向相反,同机内网 `127.0.0.1:3000`) |
| cookie | 沿用名字 `session`(path=/,httponly,samesite=lax,30 天),格式换 JWT HS256(`jose`);老 itsdangerous cookie 验签失败即自然登出,团队重登一次 |
| 密码 | 沿用 studio 的 scrypt 格式 `scrypt$n$r$p$saltHex$hashHex`,Node `crypto.scryptSync` 完全兼容——迁移用户免改密 |
| 角色 | `users.role`:`admin`(团队,全局视图 + 可进 studio) / `user`(公众,订阅视图);存量用户迁移时全部 admin |
| 数据模型 | **订阅制 + admin 全局视图**:`influencers`/`videos`/`jobs` 保持全局一份(多人监控同一红人只抓一次,不撞 scraper 吞吐瓶颈;新用户加号即见历史数据);普通用户经 `user_influencers`(user_id, influencer_id, group_id, alias)看数据;**admin 不走订阅表,查询保持现状**——存量数据零迁移,团队工作流不变 |
| 分组 | `account_groups.owner_id`(NULL=系统组,admin 世界) ;普通用户自建组 owner_id=自己;numbering 机制挂 group 不用改 |
| 注册 | `REGISTER_MODE=invite|open|closed`(env,默认 invite 沿用邀请码);何时真开放由运营切 env 决定;登录/注册进程内滑窗限流(10 次/60s/IP,信任 nginx X-Real-IP) |
| 配额 | `users.monitor_quota`(默认取 env `DEFAULT_MONITOR_QUOTA`,缺省 20),只对 `user` 角色生效,订阅数达上限拒绝添加 |
| scraper | **零改动**。admin 全局视图 = 所有 influencer 都有人看,每日刷新继续扫全量 active;公众用户退订后无人看的账号由 admin 用现有删除/失效流程清理 |
| studio 前端 | Login 页退役,改整页跳主站 `/login?next=/studio/`;登出调主站 `/api/auth/logout`;改密 UI 移到主站 |
| dev bypass | `AUTH_DEV_BYPASS` 删除(面向公众不留 fail-open 后门);本地用 `scripts/create-admin.mjs` 建号真登录 |

## 非目标(本期不做)

- 邮箱验证 / 找回密码(无邮件基建;email 先收集,后续接阿里云邮件推送)
- 手机号/微信登录、计费付费、多团队/组织模型
- studio 数据 per-user 化(它是 admin-only 内部工具)
- 合库(`mkt.users` 迁走后表保留弃用,不动其他)

## 1. 数据模型(svm 库,drizzle/manual 幂等迁移)

```sql
users(id, username, email, password_hash, role 'user', session_ver 1,
      monitor_quota 20, created_at)
  -- lower(username)/lower(email) 唯一索引,大小写不敏感,同 studio 语义
user_influencers(user_id FK→users CASCADE, influencer_id FK→influencers CASCADE,
                 group_id NULL, alias NULL, created_at, PK(user_id, influencer_id))
  -- group_id 弱引用 account_groups(应用层保证 owner 一致),NULL=未分组
account_groups + owner_id NULL  -- NULL=系统组(admin);唯一约束改 (owner_id, name)
```

## 2. 鉴权流

- **radar middleware**(edge,可跑 jose):验签 + exp,不查库。未登录 → `/{locale}/login?next=`;`/login` 路径放行。严格校验(用户存在、`session_ver` 吊销)在 server 层 `getCurrentUser()`(React `cache()`,每请求一次查库),所有页面/action/route handler 经 `requireViewer()`/`requireAdmin()` 收口。
- **JWT payload**:`{sub, un, role, v, iat, exp}`;改密 `session_ver+1` 吊销他端。
- **studio `require_auth`**:cookie 转发 → radar `GET /api/auth/verify`(全量校验,返回 `{id, username, role}`,`no-store`)→ 非 200 → 401;`role != 'admin'` → 403;网络异常 → 503。30s TTL 进程内缓存(单 worker)。GeeLark 回调(`?token=`)与 `/healthz` 不走此路径,原样不动。

## 3. 查询隔离(viewer 语义)

所有数据查询函数加 `viewer` 参数:

- `admin` → 现状 SQL 原样(全局)。
- `user` → `influencers` JOIN `user_influencers ui ON ui.user_id = $me`;组轴用 `ui.group_id`(NULL 显示为"未分组"),别名用 `ui.alias`;组列表 `WHERE owner_id = $me`。
- 添加账号:upsert 全局 `influencers`(冲突取已有 id)+ insert 订阅行;**已有数据即刻可见**,无活跃 job 才 enqueue(去重逻辑不变)。
- 删除:admin=真删(现状);user=退订(删订阅行)。
- 全局运维面(job-stats、重试死信、gone-accounts 全量)admin-only;普通用户各按订阅过滤。

## 4. 上线序列(一次窗口)

1. 阿里云 PG 跑 manual SQL(users/user_influencers/owner_id)。
2. `node scripts/migrate-users-to-radar.mjs`(mkt.users → svm.users,role=admin,hash 原样)。
3. 部署 web(新 env:`SESSION_SECRET`、`REGISTER_MODE`、`REGISTER_CODE`、`DEFAULT_MONITOR_QUOTA`、`AUTH_COOKIE_SECURE=1`;删 `STUDIO_AUTH_VERIFY_URL`)。
4. 部署 studio(新 env:`RADAR_AUTH_VERIFY_URL=http://127.0.0.1:3000/api/auth/verify`;`DASHBOARD_SECRET_KEY`/`REGISTER_CODE` 退役)。
5. 全员重新登录(旧 cookie 自然失效)。回滚 = 回退两个部署 + env,新表无破坏性。

## 风险

| 风险 | 应对 |
|---|---|
| studio→radar verify 形成运行依赖(radar 挂→studio 401/503) | 同机内网 + 30s 缓存;studio 是内部低频工具,可接受;503 与 401 区分显示 |
| 公众注册滥用打爆抓取配额 | REGISTER_MODE 默认 invite;配额默认 20;限流;开放节奏运营控制 |
| `:8080` GeeLark 回调被误伤 | 本次不触碰 nginx 与 geelark 路由;上线检查单含回调可达性验证 |
| 老 cookie/两套体系过渡混乱 | 同名 cookie 原子切换,旧格式验签失败即重登,无并存窗口 |
