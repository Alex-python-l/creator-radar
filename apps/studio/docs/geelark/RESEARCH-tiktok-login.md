# GeeLark TikTok 自动登录 + 登录状态确认 — API 研究笔记

日期：2026-05-24
来源：
- 官方文档仓库 `GeeLark/geelark-openapi` (`docs/en/openapi.yaml` 已归档到本目录 `openapi-en.yaml`)
- 官方 Python SDK 仓库 `GeeLark/awesome-geelark-skill` (`references/*` 已归档到本目录)
- 现网文档站 https://open.geelark.cn （SPA 页面，不可 fetch，请直接看本目录的 YAML/MD 副本）

---

## 1. 自动登录 TikTok 账号

### 唯一一个登录接口：`/open/v1/rpa/task/tiktokLogin`

**请求体（`TikTokLoginRequest`）**

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | ✅ | string | 云手机 ID（即 `accounts.geelark_phone_id`） |
| `scheduleAt` | ✅ | int | Unix 时间戳；用 `int(time.time())` 表示「立即」 |
| `account` | ✅ | string | TikTok 账号（用户名 / 邮箱 / 手机号，≤64） |
| `password` | ✅ | string | 密码（≤64） |
| `name` | ❌ | string | 任务名（仅展示，≤128） |
| `remark` | ❌ | string | 备注（≤200） |

**返回**：`TaskResponse` → `data.taskId`（单个 string；不是数组，跟 `task/add` 返回 `taskIds[]` 不同）

**性质**：异步 RPA 任务。GeeLark 在云手机里自动操作 TikTok app 完成登录流程。

### 本仓库里的现状

`geelark_client.py:173-186` 已经实现了 `create_tiktok_login_task(env_id, account, password)`，返回 `data["taskId"]`。但 **`app/services/` 里没有任何调用方** —— 这是个完成度 90% 但没接通的功能。

`accounts` 表里也已经有 `tiktok_logged_in INTEGER DEFAULT 0` 字段（`app/db.py:15`），同样没有写入方。

### 已知限制 ⚠️

GeeLark 的 TikTok 自动登录 **只支持账号 + 密码**：

- ❌ 不支持 Google / Facebook / Apple OAuth 登录
- ❌ 不支持「输入手机号 → 等用户填短信验证码」的交互式流程
- ❌ 不支持邮箱验证码二次验证
- ❌ 不支持 2FA / TOTP

所以任何账号一旦触发 TikTok 的风控（CAPTCHA、邮箱验证、设备验证码），任务就直接失败。

---

## 2. 确认是否已登录

⚠️ **GeeLark 没有 "isLogin" / "getAccountInfo" / "getLoginStatus" 这样的直接查询接口**。所有方案都是间接的，且各有取舍。

### 方案 A：依赖 tiktokLogin 任务的结果（最便宜，最常用）

提交 `tiktokLogin` 后，轮询 `/open/v1/rpa/task/detail`：

```json
POST /open/v1/rpa/task/detail
{"id": "<taskId>"}
```

返回 `TaskDetailResponse.data`：

| 字段 | 含义 |
|---|---|
| `status` | `1=waiting`, `2=running`, `3=completed`, `4=failed`, `7=cancelled` |
| `failCode` / `failDesc` | 失败时的错误码 / 描述 |
| `result` | 任务结果（结构依赖任务类型；对 login 任务文档没明示是否带额外字段） |
| `startAt` / `endAt` / `cost` | 时间与计费 |

**判断**：`status == 3` 视为登录成功；`status == 4` 视为失败，读 `failDesc` 展示给用户。

> CLAUDE.md 里描述的 `2=running, 3=done, 4=failed, 7=cancelled` 跟新版 OpenAPI 一致；只是 OpenAPI 还多了 `1=waiting`。`/open/v1/rpa/task/query` 也支持按 `status` 筛。

**优点**：实现最简单，跟现有 `posting_service.check_initiated` 模式完全一致。
**缺点**：只在「最近一次跑过登录任务」窗口内有效；无法告诉你「这个号当下还登着吗」。

### 方案 B：截图 + 视觉判断

两步 API（异步）：

1. `POST /open/v1/phone/screenshot` `{id}` → `data.taskId`
2. 轮询 `POST /open/v1/phone/screenshot/result` `{id}` → `data.screenshotUrl`（图片下载链接）

配合：
- `POST /open/v1/phone/app/start` `{id, packageName: "com.zhiliaoapp.musically"}` 启动 TikTok（注意有些区域包名是 `com.ss.android.ugc.trill`）

**判断**：已登录时 TikTok 首页是视频流（"For You" 标签）；未登录时会出现 "Sign up for TikTok" 横幅。可以用图像识别或 OCR 判断关键字。

**优点**：能查任意时刻的登录状态。
**缺点**：实现复杂；要保活云手机（按分钟计费）；OCR 准确率难调；不同 TikTok 版本 UI 会变。

### 方案 C：ADB shell / 远程 shell 直查

两条路：

1. **GeeLark 托管 shell**：`POST /open/v1/phone/shell/execute` `{id, command}` → `data.output`。不需要本地 ADB。
2. **直连 ADB**：`POST /open/v1/phone/adb/status/set` `{id, status:1}` 开启 → `POST /open/v1/phone/adb/info` `{id}` → `{adbAddress, debugPort}` → 本地 `adb connect …`

可执行的检查：
- `dumpsys activity activities | grep musically` 看当前 Activity 是否是登录态的 MainTabActivity
- `run-as` / 读 TikTok shared_prefs（需 root；GeeLark 支持 `/phone/root/set` 开 root）

**优点**：精确，对 TikTok 版本不敏感。
**缺点**：TikTok 大部分状态加密；需要解 Android 内部状态；实现工作量大。

### 方案 D：探针式 RPA 任务

跑一个**幂等**的、依赖登录态的任务来反推：

- `/open/v1/rpa/task/tiktokProfileEdit` 不带任何字段（理论上只需 `id` + `scheduleAt`）
- 或者非常低噪声的 `tiktokRandomStar` `likeProbability:0`

如果任务 `status==3` 视为登录有效；`status==4` 且 `failDesc` 暗示未登录则视为登出。

**优点**：直接验证「登录态对功能是否有效」。
**缺点**：成本（云手机分钟费 + 任务费）；可能引入副作用；`failDesc` 文本没有契约，难稳定解析。

### 方案 E：Webhook 异步事件

`POST /open/v1/webhook/set` `{webhookUrl}` 注册回调；任务状态变化时 GeeLark 主动 POST 到我们 endpoint。

**用法**：替代轮询，降负载；但 **只在任务事件触发**，仍不能回答「现在登着没」。

**对本项目的现实性**：需要公网可达的 endpoint。生产服 1.15.155.204 是公网 IP（参见 memory `mkt-deployment`），可行但要做签名校验。

---

## 3. 综合建议（Phase 拆解）

### Phase 1（最小可用）— 集成已有 `create_tiktok_login_task`

1. 新增 `app/services/login_service.py`：
   - `submit_login(conn, account_id, credentials)`：调 `create_tiktok_login_task`，把 `taskId` 存到 accounts 表新加的 `tiktok_login_task_id` 字段。
   - `check_login_status(conn)`：APScheduler 周期任务（建议跟 `status_check` 复用 5 分钟节奏），对所有 `tiktok_login_task_id != NULL AND tiktok_logged_in = 0` 的账号调 `/rpa/task/detail`，按 `status` 写回 `tiktok_logged_in`、`tiktok_login_failed_at`、`tiktok_login_fail_reason`。
2. `accounts` 表加列（沿用 `_migrate` 模式）：
   - `tiktok_login_task_id TEXT`
   - `tiktok_login_failed_at TEXT`
   - `tiktok_login_fail_reason TEXT`
   - `tiktok_login_started_at TEXT`（提交时间，用来 detect 任务"卡住"）
3. 凭据存储：**目前没有 secrets 表**，要么新加 `tiktok_credentials(account_id, password_enc)`（推荐：单独表 + 字段加密），要么把密码放在 accounts 表加密列。**密码以明文落到 SQLite 是最低标准**，至少要 Fernet 或类似对称加密 + 主密钥放 .env。
4. API + UI：
   - `POST /api/accounts/{id}/login` 接收 `{account, password}` → 调 `submit_login`。
   - 账号列表加 "TikTok Login" 状态列 + 「登录」按钮。
   - 批量导入凭据（CSV：phone_id, account, password）也是高价值，对应已有的 `credentials_parser.py`（已经存在！值得复用）。
5. 发布前置条件：`posting_service.publish_video` 应当拒绝 `tiktok_logged_in != 1` 的账号 —— 防止把 video 喂给没登录的号。

### Phase 2（可选增强）

- 截图巡检：每周对一部分账号跑「启动 TikTok → 截图 → OCR `Sign up`」做存活探针。
- 失败原因分类：失败时把 `failDesc` 解析为 enum（`bad_password`, `captcha`, `email_verify_required`, `device_block` …），UI 直接给操作建议。

### Phase 3（可选）

- 接入 Webhook 替代部分轮询。

---

## 4. 关键参考端点速查

| 用途 | Endpoint |
|---|---|
| 触发 TikTok 登录 | `POST /open/v1/rpa/task/tiktokLogin` |
| 查询任务详情（含 result/failCode） | `POST /open/v1/rpa/task/detail` |
| 批量查任务（轻量） | `POST /open/v1/rpa/task/batchQuery` |
| 列出任务 | `POST /open/v1/rpa/task/query`（支持按 status 筛） |
| 取消任务 | `POST /open/v1/rpa/task/cancel` |
| 重试失败任务 | `POST /open/v1/rpa/task/retry` |
| 截屏（异步） | `POST /open/v1/phone/screenshot` → `/result` |
| 启动 App | `POST /open/v1/phone/app/start` |
| 已装 App 列表 | `POST /open/v1/phone/app/list` |
| Shell 命令（云端代执行） | `POST /open/v1/phone/shell/execute` |
| 启用本地 ADB | `POST /open/v1/phone/adb/status/set` + `/phone/adb/info` |
| Webhook 注册 | `POST /open/v1/webhook/set` |

任务状态码（`status` 字段）：`1=waiting, 2=running, 3=completed, 4=failed, 7=cancelled`。

---

## 5. 实现偏好（已和用户对齐，留给未来实现）

- **凭据存储**：明文写 `accounts.tiktok_password` 一列就行。后续若要加密再迁移。
  > 风险点显式记录：DB 文件 `data/app.db` 一旦泄漏即等于密码全泄。生产服上要靠文件系统权限和服务器隔离兜底；不要把 DB 同步到任何外部位置。
- **凭据来源 / 录入方式**：CSV 批量导入。复用已有的 `app/services/credentials_parser.py`，扩 columns 为 `phone_id, account, password`（或 `geelark_phone_id, tiktok_account, tiktok_password`）。
- **UI**：暂不做单条手填表单、暂不集成进 Group Wizard。只在 Accounts 页加一个「Import credentials (CSV)」入口。

实现顺序建议（真要做时）：
1. `accounts` 表加 4 列（`tiktok_password`, `tiktok_login_task_id`, `tiktok_login_started_at`, `tiktok_login_failed_at`, `tiktok_login_fail_reason`），走 `_migrate` 模式。
2. 扩 `credentials_parser.py` 解析新格式。
3. 新增 `app/services/login_service.py` 两个函数：`submit_login` / `check_pending_logins`。
4. `scheduler.py` 把 `check_pending_logins` 挂到现有 5 分钟节奏的 `status_check` 同周期。
5. `posting_service.publish_video` 前置检查 `tiktok_logged_in==1`，否则拒绝。
6. `accounts.py` API 加 CSV 上传端点 + UI 加按钮。

---

## 6. 实测勘误（2026-05-24，针对生产环境 token）

⚠️ **本目录的 `openapi-en.yaml` 路径前缀大量错误**。`/phone/app/*`、`/phone/screenshot`、`/phone/shell/*`、`/phone/adb/*`、`/rpa/task/detail`、`/rpa/task/batchQuery`、`/rpa/task/query` 全部 **404**。
**Source of truth 是官方 SDK 仓库 `GeeLark/awesome-geelark-skill/scripts/geelark_client.py`** 里的 `ALLOWED_ENDPOINTS` 列表，以及实测。

### 真实可用的端点速查（实测通过）

| 用途 | 实际路径 | OpenAPI yaml 写的（错） | 关键字段 |
|---|---|---|---|
| 启动手机 | `/open/v1/phone/start` | 同 | `{ids:[]}` |
| 停止手机 | `/open/v1/phone/stop` | 同 | `{ids:[]}` |
| 手机状态 | `/open/v1/phone/status` | 同 | `{ids:[]}` → `data.successDetails[].status`（**0=running, 2=stopped**；跟任务 status 含义不同！） |
| 启动 App | `/open/v1/app/start` | `/phone/app/start` ❌ | `{envId, packageName}` ← **字段叫 `envId` 不是 `id`** |
| 截图触发 | `/open/v1/phone/screenShot` | `/phone/screenshot` ❌ | `{id}` → `data.taskId`（**大写 S**！） |
| 截图取结果 | `/open/v1/phone/screenShot/result` | `/phone/screenshot/result` ❌ | 实测 `{id}` 不够；**疑似需要 `{id, taskId}`** —— 待最终验证 |
| Shell 执行 | `/open/v1/shell/execute` | `/phone/shell/execute` ❌ | `{id, command}` → `data.output` |
| ADB 数据 | `/open/v1/adb/getData` | `/phone/adb/info` ❌ | （SDK 已有，未实测） |
| ADB 开关 | `/open/v1/adb/setStatus` | `/phone/adb/status/set` ❌ | （SDK 已有，未实测） |
| 任务详情 | `/open/v1/task/detail` | `/rpa/task/detail` ❌ | `{id}` |
| 任务查询 | `/open/v1/task/query` | `/rpa/task/query` ❌ | `{ids:[]}` — **必须传 ids，不能裸列** |
| **任务历史** | `/open/v1/task/historyRecords` | yaml 没收录 | `{page, pageSize}` → 倒序列出所有 RPA 任务（不需要 ids，**这是查"曾登录过"的零成本入口**） |
| 任务重启 | `/open/v1/task/restart` | `/rpa/task/retry` ❌ | （SDK 用 restart） |
| RPA 任务通用 add | `/open/v1/task/rpa/add` 或 `/open/v1/task/add` | — | 现有 `posting_service` 已在用 `/task/add` |
| TikTok 登录 | `/open/v1/rpa/task/tiktokLogin` | 同 ✅ | `{id, scheduleAt, account, password}` |
| TikTok 发布 | `/open/v1/rpa/task/tiktokPublish` ❌ 实测 404 | `/rpa/task/tiktokPublish` | 项目里用的是 `/task/add` 加 `taskType:1`，**这才是对的** |

### 真实可用的「确认登录」方案（实测重排）

#### ⭐ 方案 ZC1：历史任务反推（**零成本，强推荐先做**）

```python
# 列最近 N 页的 RPA 任务历史，按 envId + planName 关键字筛 tiktokLogin 成功记录
post("/open/v1/task/historyRecords", {"page":1,"pageSize":50})
# 返回 data.items[] 每项含 envId, taskType, planName, status, scheduleAt
# 已知 taskType 取值（实测见到的）：42 = TikTok AI Comment。tiktokLogin 的 taskType 待真实跑一次确认。
```

- **优点**：零成本；可以快速回答「这个 envId 在过去 X 天有过 tiktokLogin status=3（成功）记录吗？」
- **缺点**：只回答"曾登录"，不回答"现在还登着吗"。历史 total=1000 条；过老的会被截断。
- **建议**：作为账号入库时的初始信号；登录任务跑完后写一次 `tiktok_logged_in=1`，之后用其它信号维护。

#### 方案 SC：截图 + 视觉（**实测可行，待补 `taskId` 参数**）

完整流程（实测通过部分）：

```python
post("/open/v1/phone/start",     {"ids":[pid]})           # ✅
# poll /phone/status 直到 status != 2
post("/open/v1/app/start",       {"envId":pid, "packageName":"com.zhiliaoapp.musically"})  # ✅
# wait 20-30s for UI
trig = post("/open/v1/phone/screenShot", {"id":pid})       # ✅ → data.taskId
# poll /phone/screenShot/result（**疑似需要传 taskId，不是 id**） → data.downloadLink
img = requests.get(downloadLink).content                   # ✅
post("/open/v1/phone/stop",      {"ids":[pid]})
```

- 成本：单次 ~1-2 分钟手机时长（GeeLark 按分钟计费）+ 极少 API 费
- **判定**：首页有 "For You" / 视频流 = 已登录；有 "Sign up for TikTok" / "Use phone or email" 全屏按钮 = 未登录
- 待解决：`/screenShot/result` 参数到底要传 `id` 还是 `{id, taskId}` —— 实测第 1 次 trigger 后 poll 第 1 次拿到 `status=1`，第 2 次 trigger 后却 30 次 poll 全 `status=None`。强烈怀疑要传 taskId 关联。**真要做时先 debug 这一步。**

#### 方案 SH：shell exec（**已确认接口可用，最精确**）

```python
post("/open/v1/shell/execute", {"id":pid, "command":"dumpsys activity activities | grep -E 'ResumedActivity|musically'"})
# → data.output 文本
```

- 看 `mResumedActivity` 是不是 `com.zhiliaoapp.musically/.MainActivity`（已登录）或 `.../LoginActivity` / `.../SignUpActivity`（未登录）
- 优点：不依赖 OCR；TikTok 版本兼容性远高于截图
- 成本：和截图同量级（也要 phone 跑起来 + 启动 app）
- 待实测：Activity 类名是否稳定（不同 TikTok 版本可能不同）

### 修订实现建议（替换原 Phase 1）

1. **先做零成本能力**：写一个 `app/services/login_status_service.py`：
   - `recent_login_records(envId, days=30) -> list[TaskRecord]`：分页拉 `task/historyRecords`，本地按 envId 过滤；缓存最近一次的 status / scheduleAt。
   - `def likely_logged_in_from_history(envId) -> bool | None`：根据上一步结果给出 `True / False / None(unknown)`。
2. **配合**：导入账号时（或 admin 手动刷新时）调用上面这两个函数，把 `tiktok_logged_in` 字段从 `0` 提升为 `1`，避免每个号都要重新跑 login。
3. **截图/shell 路径放 Phase 2**：作为「定期巡检」或「发布失败后的根因诊断」工具，不进主流程。
4. **修正本仓库的 `geelark_client.py`**：把 `_post` 用到的所有 `/open/v1/...` 路径与上表对齐；尤其确认 `posting_service`、`warmup_service` 用的路径都是项目自验过的（这些之前一直能跑，所以应该已经对了）。

### 路径勘误的根因

GeeLark 的 OpenAPI yaml 是「面向用户的文档」，里面把不同操作按业务域**分组写**了（`/phone/app/*` 看起来更"整齐"），但**实际部署的网关 URL 是平铺的**（`/open/v1/app/start`）。SDK 才是 source of truth。这种文档/实现不一致是 GeeLark 的已知坑，要项目里专门记一笔。

---

## 7. 还未验证 / 待澄清

- TikTok login 任务 `result` 字段是否返回登录后的 TikTok 用户名 / unique_id —— 若是，可以自动写回 `accounts.tiktok_username`。需要实跑一次任务看返回。
- `failCode` / `failDesc` 对 login 任务的稳定取值集 —— 未在文档里枚举，要靠真实失败样本积累映射。
- `keybox/upload` 的实际用途（Android keybox attestation? 还是账号会话池？）—— 文档极简，参数只有 `id` + `keyboxId`；没有看到创建 keybox 的接口。需要问 GeeLark 客服或在桌面端验证。
