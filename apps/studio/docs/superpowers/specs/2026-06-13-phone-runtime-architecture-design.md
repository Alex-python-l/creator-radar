# 云手机运行与任务编排目标架构

**Date**: 2026-06-13  
**Status**: Phase 0a 实测完成,findings 已回写;Phase 0b/1/2/3/4 已实施(2026-06-15)

## Phase 0a Findings (2026-06-15 实测)

**Token / env / phone**: 实测在 prod (139.196.52.92) 用 `/opt/mkt/.env` 的 `GEELARK_TOKEN`,phone 选 M8 (env_id `620176013586720343`,最近活动 3 天前),通过 `scripts/probe_geelark_cancel.py --schema both` 跑了两次 ~2 分钟的探针,各启 phone + 提交一个 5 分钟 warmup task + 等 15s + cancel + 6 轮 poll。

### `/open/v1/task/cancel`

**Schema 实测结果(2 次独立 round-trip):**

| Schema | Request body | Response | 结论 |
|---|---|---|---|
| `{"ids":[task_id]}` (复数) | `{"ids":["624075400535895195"]}` | `code=0`, `data={"totalAmount":1,"successAmount":1,"failAmount":0}` | ✅ 接受 |
| `{"id": task_id}` (单数) | `{"id":"624075281132480087"}` | `code=40004`, `msg="Key: 'TaskListCancelReq.IDs' Error:Field validation for 'IDs' failed on the 'required' tag"` | ❌ 拒绝 |

服务端 struct 字段名是 `IDs`(proto 风格),JSON 输入接受小写 `ids`。**`api_reference.md` 文档正确,`openapi-en.yaml` 写的 `id` 单数错误**。

**Cancel 强一致性**:cancel HTTP 200 后约 6 秒,`/open/v1/task/detail` 报 `status=7` (cancelled)。提交→submit pre-cancel poll 报 `status=1` (waiting),即 task 还未进入 running 也能直接 cancel。

**结论**:cancel 强可用 → **Phase 0b 实施 cancel + stop + verify**,timeout 流程承诺"到预算点就 cancel GeeLark task"。`geelark_client.py` 加 `cancel_tasks(task_ids: list[str])` 方法用 `{"ids": task_ids}` schema。

### `/open/v1/callback/*`

**Live URL 确认**:`/open/v1/callback/set` + `/open/v1/callback/get`(2026-06-09 discovery 已验过,这次再次确认)。`/webhook/*` 是 OpenAPI yaml 错误,不存在。

**注册流程实测(2026-06-15 07:13 起)**:
1. 生成 `GEELARK_CALLBACK_TOKEN=9vDw4twn6iXUCxKRcPpsHz2_7Of0ZoyT`,写入 prod `.env`。
2. `systemctl restart mkt` 后 `/healthz` 200。
3. `scripts/register_geelark_callback.py --base-url http://139.196.52.92:8080` 调 `/open/v1/callback/set` 注册 `http://139.196.52.92:8080/api/geelark/callback?token=...`,响应 `{}`,`/callback/get` 读回一致。

**真实 payload schema(n=6,18 分钟窗口)**:

```json
{
  "type": 15,
  "packageName": "com.instagram.android" | "com.zhiliaoapp.musically" | "com.facebook.katana" | "com.google.android.youtube",
  "appVersionId": "<numeric string>",
  "id": "<task_id, 18-digit numeric string>",
  "result": 1
}
```

**关键观察**:
- 字段名 `id`(不是 `taskId` / `task_id`),18 位数字字符串。当前 `_best_effort_parse` 已能抓到。
- 状态字段是 `result`(不是 `status`),`_best_effort_parse` 当前漏抓 — Phase 1 实施时**必须改 parser**。
- **同一个 `id` 重复出现配不同 `packageName`** (e.g. task `624074419957982807` 在 26 秒内收到 4 条 callback:instagram → facebook → youtube → tiktok)。说明 `type=15` 不是 "task done" 事件,而像是**单 task 多 app 状态广播**(可能是 "RPA 在该 app 上完成一步" / "登录态变更" 之类)。
- 当前样本 `result` 全为 `1`,无 failure 样本。
- 触发延迟:GeeLark 端事件 → 我方 endpoint 收到约 < 1 秒(`received_at` 是 endpoint 写入时间)。
- 源 IP `120.79.x.x` / `120.77.x.x`(阿里云华南/华东)。

**结论**:callback endpoint 端到端工作,但 **`type=15` 不是 task terminal 事件,不能用它驱动 Phase 1 的 stop_phones**。Phase 1 callback 接入前需要:
1. 继续观察其他 `type` 值(等真实业务跑到 status=3/4/7 时是否触发 `type` ≠ 15 的 callback)。
2. 找 GeeLark 文档或人工询问 `type` 枚举含义。
3. 找 `result` ≠ 1 的样本,确认其与 task status 4/7 的对应关系。
4. Phase 1 callback finalize 路径以**已找到的 task-done event type 为准**,在那之前 polling 仍然是主路径。

**对 Phase 1 决策的影响**:callback Phase 0 endpoint 已上 prod 在收数据。Phase 1 不能立刻切到 "callback as main path";polling 暂留为主路径。Phase 4b external_detected 已经 ship,Phase 4c Ops 视图已经 ship,这些不依赖 callback,继续按 polling + watchdog 跑。

## 背景

当前系统由 FastAPI web tier、React dashboard、PostgreSQL 和 `mkt-worker` 组成。web tier 负责 API 和页面，`mkt-worker` 通过 Procrastinate 执行 warmup、publish、comment/engagement、账单同步和各种定时扫描。

最近的成本异常暴露出一个核心问题：**云手机运行时间是直接花钱的资源，但现在它不是系统里的第一等对象**。代码里有 publish、warmup、engagement、idle sweeper、cost transaction、worker job、GeeLark task 等多套状态，但缺少一个统一的“这台云手机为什么被开机、谁拥有它、预算到什么时候、是否真的停止”的账本。

两个具体样例说明问题：

- R3 在 6 月 13 日 02:04 的 6 分钟扣费不是人工操作，而是 publish。页面显示 `Unattributed` 是因为当前归因用 `publications.posted_at` 这种业务时间猜窗口，提交时间在扣费窗口外，导致漏归因。
- R5 在 6 月 13 日 02:26 的 31 分钟 publish 是 GeeLark task 真实跑了 31 分钟。新超时代码已经尝试 stop phone，但 GeeLark task 仍然在 18:26:46 成功结束，说明 `stop_phones` 不等于取消 RPA task，后续必须接入 task cancel 并验证停止结果。

因此最优架构不是简单多加几个 worker，也不是把未知扣费继续拆更多分类，而是围绕“每一分钟云手机运行都必须有 owner、budget、stop proof”重建任务编排和成本归因。

## 当前状态映射

这不是从零重写。仓库里已经有几块中间态，它们应该被纳入迁移路径，而不是被新设计绕开。

| 当前能力 | 位置 | 在目标架构里的位置 | 迁移判断 |
|---|---|---|---|
| account-level phone lease | `accounts.phone_busy_until`、`accounts.warmup_task_id`、`try_acquire_phone_lease`、`release_phone_lease`(2026-06-09 `phone-task-lease-redesign` 落地产物) | `phone_sessions` 的 v0 版本 | 不直接废弃;Phase 1 先 dual-write,再切读路径 |
| manual phone hold | `POST /api/accounts/{id}/phone/hold`,写 `phone_busy_until` | `phone_sessions.source='manual'` | 复用现有 UX,把状态从 account 列搬到 session 行 |
| idle phone sweeper | `app/tasks/idle_phone_sweeper.py`,读 `phone_busy_until` 判定是否 idle | watchdog / stop backstop | 迁移期必须 dual-read,不能让 sweeper 误停有 session 的 phone |
| GeeLark transaction mirror | `geelark_transactions` 表、`app/repositories/transactions.py` | cost attribution 的输入表 | 保留;新增的是固化归因结果,不是重建交易镜像 |
| 分散的 start/stop | warmup、posting、engagement、geelark_service、idle sweeper | `Phone Runtime Manager` 唯一出口 | 逐步收敛,不一次性搬完所有路径 |
| GeeLark task-done callback | 2026-06-09 [`geelark-task-callback-design`](2026-06-09-geelark-task-callback-design.md) **Approved 但未实施**;只 commit 了 design+plan(0a73b36),无 `geelark_callback_log` 表 / `/api/geelark/callback` endpoint / register 脚本 | 本 spec 的 callback 主路径基础 | 复用其 Phase 0(抓 payload)、`geelark_callback_log` 表设计、token 鉴权方案,不重复造 |
| `/open/v1/task/cancel` | 文档里有(api_reference.md:365 标 ⚠️ "未实测"),`geelark_client.py` 没接入 | timeout 取消路径 | 必须 Phase 0a 实测 |

关键修正：`phone_sessions` 不是“凭空新增一个概念”，而是把当前 `phone_busy_until` 这一列 lease 抽成独立表，并补上 `source`、`owner`、`budget_deadline_at`、`stop_requested_at`、`stopped_at`、`stop_reason`、`cost_transaction_id` 等审计字段。

## 目标

- 每一笔扣费都能解释为 `publish`、`warmup`、`engagement/comment`、`manual/external` 或真正无法归因的异常。
- `manual/external` 不能靠猜；只有确认不是系统内 publish、warmup、engagement/comment 后，才进入这类。
- 任何系统任务开机时必须有运行预算，到点后必须 cancel GeeLark task、stop cloud phone、验证 stopped，并留下审计记录。
- worker 重启、卡死、网络超时、GeeLark 慢接口，都不能留下长期隐藏运行的云手机。
- 成本页面从 ledger 解释支出，而不是用任务完成时间临时猜测。
- 架构要允许短期继续使用 Procrastinate，长期可以平滑迁移到 Temporal 这类工作流引擎。

## 非目标

- 不重写整个 dashboard。
- 不替换 PostgreSQL。
- 不立刻强制替换 Procrastinate。
- 不追求完美修复所有历史账单；历史数据只能在已有 GeeLark transaction 和本地任务记录范围内尽量补归因。
- 不把 `Unattributed` 再拆成很多小类。分类可以保持少，但每个分类背后的判定要可靠。

## 总体架构

推荐架构是：

```text
FastAPI API
  |
  | create publish/warmup/comment intent
  v
Task Run State Machine
  |
  | acquire runtime budget
  v
Phone Runtime Manager
  |
  | start / submit / cancel / stop / verify
  v
GeeLark OpenAPI

GeeLark callbacks if verified + polling fallback
  |
  v
Task state + phone session ledger
  |
  v
Cost attribution ledger
  |
  v
Costs dashboard / alerts / audit
```

核心变化是把“开云手机”从各业务服务里抽出来，变成唯一入口：`Phone Runtime Manager`。publish、warmup、engagement/comment、人工保留、外部检测，都必须创建或关联一个 `phone_session`。

## Worker 的定位

worker 不是可有可无的后台进程。最优架构里，worker 应该负责这些事情：

- 执行用户请求之外的长耗时任务，例如提交 GeeLark RPA、轮询状态、同步账单。
- 串行化会花钱或会冲突的云手机操作，避免同一台 phone 同时 publish 和 warmup。
- 执行可靠重试，但重试必须基于明确状态机，而不是重复启动新的云手机。
- 执行超时处理：cancel task、stop phone、verify stopped、写入失败原因。
- 执行 watchdog：发现系统外开机、失联 session、长时间 running job。
- 维护成本账本，把 GeeLark transaction 归因到具体任务或外部 session。

现在的 `mkt-worker` 有必要保留，但职责需要从“跑后台函数”升级为“任务编排器”。短期可以继续用 Procrastinate；中长期当任务状态和事件模型稳定后，Temporal 会更适合这类长流程、可取消、可追踪的 workflow。

## 核心组件

### 1. Phone Runtime Manager

这是唯一允许直接调用这些 GeeLark 操作的组件：

- `start_phones`
- `stop_phones`
- publish / warmup / engagement task submit
- task status query
- task cancel
- stop verification

它对上层提供更高层接口：

```python
start_session(account_id, source, owner_type, owner_id, budget_seconds)
submit_geelark_task(session_id, task_payload)
finalize_success(session_id, geelark_status)
finalize_failure(session_id, reason)
timeout_session(session_id)
stop_and_verify(session_id, reason)
```

业务服务不再自己随手 start/stop phone。它们只声明“我要为 publication 3178 使用 R5，预算 15 分钟”，后续由 runtime manager 负责。

### 2. Phone Sessions

新增 `phone_sessions` 表，作为云手机运行账本。它是当前 account-level lease 的演进版：

- 现在：`accounts.phone_busy_until` 只能表达“这台 phone 暂时别碰”，无法说明为什么、谁持有、停机是否成功、归到哪笔 cost。
- 目标：`phone_sessions` 一行表达一次可计费运行，包含 source、owner、预算、stop proof 和成本归因。

迁移时不能直接删除 `phone_busy_until`。Phase 1 应先让 runtime manager 创建 `phone_sessions`，同时继续写 `accounts.phone_busy_until`，让现有 `idle_phone_sweeper`、daily scan、manual hold release 仍然安全工作。等所有读路径都切到 `phone_sessions` 后，再考虑把 account-level lease 退化成兼容字段或删除。

建议字段：

```sql
id                     bigserial primary key
account_id              bigint not null
env_id                  varchar(64) not null
source                  varchar(32) not null
owner_type              varchar(32)
owner_id                bigint
geelark_task_id         varchar(64)
started_at              timestamp not null
budget_deadline_at      timestamp not null
stop_requested_at       timestamp
stopped_at              timestamp
status                  varchar(32) not null      -- ACTIVE / STOPPING / STOPPED / TIMEOUT / STOP_VERIFY_FAILED
final_status            varchar(32)               -- 仅终态写入；ACTIVE/STOPPING 期间为 NULL
stop_reason             text
last_observed_status    varchar(32)
cost_transaction_id     varchar(64)
created_by              varchar(32) not null
created_at              timestamp not null
updated_at              timestamp not null
```

`status` 是运行时事实(R5 那种 "业务已 TIMEOUT、手机还在 STOPPING" 的中间态由它承载),`final_status` 只在终态时回填,便于报表查询。约束:

```sql
-- 同一 account_id 同时只能有一个 in-flight session。
-- 用 stopped_at IS NULL 而不是 status,因为 stopped_at 是物理事实,
-- 不会因为状态机命名变动而失效。
CREATE UNIQUE INDEX idx_phone_sessions_one_active
  ON phone_sessions (account_id)
  WHERE stopped_at IS NULL;
```

`source` 控制少量明确分类：

- `publish`
- `warmup`
- `engagement`
- `manual`
- `external_detected`

这里的 `manual`/`external_detected` 不是默认兜底。只有 watchdog 观察到 phone running，但系统没有 active `phone_session`，或者用户在 dashboard 显式声明人工保留，才使用这些 source。否则成本归因仍应先查 publish、warmup、engagement。

现有 manual hold 已经存在，不需要重新设计入口。目标只是把它从“写一个很长的 `phone_busy_until`”改成“创建 `phone_sessions(source='manual')`，并为了迁移期继续刷新 `phone_busy_until`”。

### 3. Task Runs

新增 `task_runs`，把业务任务状态和手机运行状态分开。大多数时候，一个 publish/warmup/comment task 确实对应一个 `phone_session`，但不能合并成一张表，原因是二者生命周期在异常时会分叉：

- R5 这种场景里，`task_run` 可以已经 `TIMEOUT`，但 `phone_session` 仍处于 `STOPPING` 或 `STOP_VERIFY_FAILED`。
- 人工 hold 只有 `phone_session`，没有业务 `task_run`。
- 一个未来的业务任务如果重试，可能有多个 attempts，每次 attempt 对应不同 `phone_session`。

`task_runs` 表达“业务任务现在走到哪一步”：

```text
PLANNED
PHONE_STARTING
SUBMITTED_TO_GEELARK
RUNNING
SUCCESS
FAILURE
TIMEOUT
CANCELLED
```

建议字段：

```sql
id                     bigserial primary key
kind                   varchar(32) not null
ref_type               varchar(32) not null
ref_id                 bigint not null
account_id              bigint not null
phone_session_id        bigint
geelark_task_id         varchar(64)
state                  varchar(32) not null
attempt                integer not null default 1
planned_at             timestamp not null
started_at             timestamp
finished_at            timestamp
deadline_at            timestamp
failure_reason         text
created_at             timestamp not null
updated_at             timestamp not null
```

第一版不引入 `task_events` 表。Procrastinate 已经有自己的调度事件表，如果再加一个事件表，需要明确事实来源，容易在第一阶段制造混乱。Phase 2 先用 `task_runs` 当前状态和少量 timestamp 字段解决可恢复性；如果后续 audit 需要不可变业务状态转移，再单独引入 `task_events`，并限定它只记录业务状态转移，不记录 worker 调度事件。

这样 R5 这种 31 分钟问题至少可以直接看到：业务任务何时提交、何时超时、最终业务状态是什么；phone session 里看到是否调用 cancel、是否 stop 成功、GeeLark 最终观测状态是什么。

### 4. GeeLark Callback 是条件优化

如果 GeeLark 确实支持业务 task callback，并且 payload 能稳定携带 `task_id` 和 terminal status，那么 callback 应该成为主路径，polling 只做兜底。但这不是当前可依赖事实，必须先在 Phase 0a 实测。

实测通过后的建议路径：

1. submit task 时保存 `geelark_task_id` 到 `task_runs` 和 `phone_sessions`。
2. GeeLark callback 到 `/api/geelark/callback`。
3. web tier 只做验签、记录 raw payload、快速 defer finalize job。
4. worker finalize：
   - 查 `geelark_task_id` 对应的 `task_run` 和 `phone_session`
   - 更新 terminal task state
   - stop phone
   - verify stopped
   - 释放 session
5. polling fallback 定期检查仍在 running 的 task，避免 callback 丢失。

callback 不应该直接在 web request 里做 stop phone，因为 stop、verify、DB transition 都是可重试的副作用，放在 worker 更清晰。

如果 GeeLark 不支持业务 callback，或者只能配置 URL 但不会发送 task terminal payload，则本节降级为“polling 仍是主路径”。`task_runs` 和 `phone_sessions` 仍然成立，只是 finalize 触发源来自 polling，而不是 callback。

### 5. Timeout 必须 Cancel + Stop + Verify

当前问题之一是 `stop_phones` 可能没有取消 GeeLark RPA task。目标行为应该是：

```text
deadline reached
  -> mark task_run TIMEOUT_PENDING
  -> call GeeLark task cancel
  -> call stop_phones
  -> poll/verify phone stopped
  -> mark phone_session TIMEOUT or STOPPED
  -> mark task_run TIMEOUT
  -> alert if cancel or stop verification failed
```

这里需要在 `geelark_client.py` 补 GeeLark cancel API。现有本地文档里提到 `/open/v1/task/cancel`，但需要用生产 token 实测返回语义，确认是按 task id 取消，还是按 env/task batch 取消。

这也是 Phase 0a 的硬阻塞项。只有确认 cancel 能对正在运行的 GeeLark RPA task 生效，timeout 才能承诺“到预算点就终止 task”。如果 cancel 只能 best-effort 或根本不能取消已运行 task，Phase 0b 仍然要做 stop + verify + alert，但文档里的 timeout SLA 必须降级为“停止云手机计费并标记业务 timeout”，不能承诺 GeeLark task 本身立即结束。

每类任务应该有默认预算：

| Source | 建议默认预算 | 说明 |
|---|---:|---|
| warmup | 8-12 分钟 | warmup 本身可控，超过通常是异常 |
| publish | 15 分钟 | 正常 TikTok publish 不应跑到 30 分钟 |
| engagement/comment | 10-15 分钟 | 视 comment/search 流程稳定性调整 |
| manual | 30 分钟可续期 | 人工操作应该显式 hold，并显示剩余时间 |
| external_detected | 5 分钟观察/告警 | 未知开机默认不长期放任 |

这些预算应是 settings，可按实际数据调。

### 6. Watchdog：检测人工或外部开机

因为云手机可以被人工从 GeeLark 控制台启动，系统必须承认“系统外开机”存在。

watchdog 定期做两类检查：

1. 本地有 active `phone_session`，但已超过 `budget_deadline_at`：触发 timeout flow。
2. GeeLark 显示 phone running，但本地没有 active `phone_session`：创建 `external_detected` session。

`external_detected` 的处理策略可以分两档：

- 初期：只在成本页和日志标红，不自动 stop，避免误伤人工正在处理的账号。
- 稳定后：超过 5-10 分钟仍未被人工认领，则自动 stop，并记录 stop_reason。

现有 dashboard 已经有 manual hold 能力，会写 `phone_busy_until`。目标不是新增一个新的人工操作入口，而是让这个入口改为创建 `phone_session(source='manual')`，并在迁移期继续维护 `phone_busy_until`，这样人工成本也能被解释。

### 7. 成本归因 Ledger

现有系统已经有 `geelark_transactions` 镜像表和成本页面。问题不在交易同步本身，而在归因逻辑仍按 transaction 的 env/time 窗口猜 publish/warmup/comment，容易发生 R3 这种漏归因。

目标归因顺序应改成：

1. `geelark_transactions.env_id` 匹配 `phone_sessions.env_id`。
2. transaction 的计费窗口和 `phone_sessions.started_at/stopped_at` 有重叠。
3. 优先选择 overlap 最大或 stop 时间最接近的 session。
4. 通过 `phone_session.owner_type/owner_id` 找到 publish/warmup/engagement 业务对象。
5. 如果没有 session，再进入 legacy fallback：按 publication task id、engagement run、warmup marker 尝试历史归因。
6. 仍然没有命中，才显示 `Unattributed`。

建议新增 `cost_attributions` 表，把归因结果固化，而不是每次页面打开都重新猜。`geelark_transactions` 继续作为 GeeLark 原始账单镜像，不被替代：

```sql
transaction_id      varchar(64) primary key
phone_session_id    bigint
task_run_id         bigint
kind                varchar(32) not null
confidence          varchar(16) not null
reason              text not null
created_at          timestamp not null
updated_at          timestamp not null
```

`kind` 保持简单：

- `publish`
- `warmup`
- `engagement`
- `manual`
- `external_detected`
- `unattributed`

这样概览饼图也会更可信：它展示的是账本归因结果，而不是查询时的临时推断。

## 关键流程

### Publish 正常流程

```text
auto_publish_scan/manual publish
  -> create task_run(kind=publish, ref=publication)
  -> runtime_manager.start_session(source=publish, owner=publication, budget=15m)
  -> start phone
  -> submit GeeLark publish task
  -> save geelark_task_id
  -> callback or polling observes success
  -> stop phone + verify stopped
  -> publication SUCCESS
  -> task_run SUCCESS
  -> phone_session SUCCESS
  -> cost sync attributes transaction to phone_session
```

### Publish 超时流程

```text
task_run deadline reached
  -> call GeeLark cancel task
  -> stop phone
  -> verify stopped
  -> publication FAILURE/TIMEOUT
  -> task_run TIMEOUT
  -> phone_session TIMEOUT
  -> cost page shows Publish timeout, not manual or unattributed
```

### 外部开机流程

```text
watchdog sees GeeLark phone running
  -> no active local phone_session
  -> create phone_session(source=external_detected)
  -> show in Costs/Ops
  -> if not manually claimed and exceeds threshold, stop + verify
```

这能覆盖“云手机能人工操作人工开机”的事实，同时避免把系统 publish/warmup/comment 的失败归到人工。

### Cost Sync 流程

```text
sync GeeLark transactions
  -> upsert geelark_transactions
  -> for each new transaction:
       match env_id + time overlap to phone_sessions
       write cost_attributions
  -> Costs overview reads cost_attributions summary
```

## 迁移过渡策略

这部分必须保守，因为任何迁移错误都会变成真实扣费。

### Step 1：只加表，不切读路径

- 添加 `phone_sessions` 和必要索引。
- 保持 `accounts.phone_busy_until` / `warmup_task_id` 现有语义不变。
- `idle_phone_sweeper`、daily warmup scan、publish/engagement lease 判断继续读 account-level lease。

### Step 2：Runtime Manager dual-write

- 新的 `Phone Runtime Manager` 在 acquire 时创建 active `phone_session`。
- `phone_sessions` 写入与 `accounts.phone_busy_until` 刷新必须在**同一 DB transaction commit**,绝不允许"紧邻事务"。partial-write 会让 sweeper 看到无主 phone 或反过来错过应停 phone。
- 不变式:任意时刻 `accounts.phone_busy_until >= phone_sessions.budget_deadline_at`。续期 budget 时两边一起刷新;runtime manager 拒绝任何只写一边的路径。
- release/timeout 时同时关闭 `phone_session` 并清理/刷新 account-level lease。
- manual hold endpoint 改为调用 runtime manager,但返回值和前端行为保持不变。
- **既有 hold 的迁移**:上线 dual-write 那一刻,遍历所有 `phone_busy_until` 仍在未来的账号,按其剩余时间创建 `phone_sessions(source='manual', budget_deadline_at=phone_busy_until)`。**不重新套 30 分钟默认 budget**,否则人工正在用的 phone 会在用户没注意时被自动 stop。

### Step 3：Sweeper dual-read

`idle_phone_sweeper` 的 idle 判定从：

```text
running in GeeLark AND (phone_busy_until IS NULL OR expired)
```

改为：

```text
running in GeeLark
AND no active phone_session for env_id
AND (phone_busy_until IS NULL OR expired)
```

迁移期必须两个条件都认为 idle 才 stop。这样即使 dual-write 某一边暂时失败，也更偏向“不误停”，再由 overdue session watchdog 处理异常。

### Step 4：业务读路径切 session

- publish、warmup、engagement 的 busy 判断改读 active `phone_sessions`。
- account-level lease 保留为兼容字段，继续被写入一段时间。
- 观察至少 24-48 小时，确认 active sessions 能正常创建、关闭、归因，且 sweeper 没有误停。

### Step 5：清理旧 lease 语义

只有当所有运行路径都不再依赖 `phone_busy_until` 作为事实来源后，才考虑：

- 把 `phone_busy_until` 降级为展示/兼容字段。
- 或删除该列和 `warmup_task_id`，让 `phone_sessions` 成为唯一事实来源。

这一步不是 Phase 1 的目标，避免把架构迁移和列清理绑在同一次生产发布里。

## 编排引擎选择

### 方案 A：短期强化 Procrastinate

继续使用现有 worker 和 Postgres job queue，但把可靠性放到业务表：

- `phone_sessions` 控制手机占用和预算。
- `task_runs` 控制任务状态。
- Procrastinate 只负责 defer、retry、cron。

优点：改动可控，最贴近现有系统，能最快修复成本风险。  
缺点：状态机和补偿逻辑要自己写，长流程可视化弱。

这是推荐的第一阶段。

### 方案 B：中长期迁移 Temporal

Temporal 适合 publish/warmup/comment 这种长流程：

- workflow history 天然记录每一步。
- activity retry、timeout、cancel 语义更完整。
- worker 重启后能从历史恢复。
- 查询 task 为什么 stuck 更容易。

优点：长期可靠性和可观测性最好。  
缺点：引入新基础设施，部署和学习成本高；如果在当前状态直接迁移，会把已有混乱状态搬进新系统。

推荐在 `phone_sessions`、`task_runs`、callback、cost attribution 稳定后再评估迁移。

### 方案 C：换 Celery/RQ

Celery/RQ 能替代 Procrastinate 做后台任务，但不能本质解决“云手机运行账本、取消、stop proof、成本归因”这些问题。

不推荐作为主要方向。

## 分阶段实施计划

### Phase 0a：GeeLark API 实测

目标是先确认关键外部能力,预计 1-2 天。

**离线调研结论(2026-06-13,实测前已确定的事实)**:

- Cancel 端点:`docs/geelark/api_reference.md:365` 列出 `/open/v1/task/cancel`,标 ⚠️ "require valid task IDs"(图例 = 未在我们仓库实测过)。**Schema 文档自相矛盾**:`api_reference.md` 和 `rpa_tasks.md` 都用 `{"ids":["taskId"]}` 复数 batch,`docs/geelark/openapi-en.yaml:1359` 用 `{"id":"taskId"}` 单数对象。优先按复数版本实测,因为同源 yaml 已被 2026-06-09 callback discovery 证实把 `/callback/*` 错写成 `/webhook/*`,credibility 偏低。
- Callback 端点:2026-06-09 callback design 的 Discovery 已经验证过——**live endpoint 是 `/open/v1/callback/set` 与 `/callback/get`**,OpenAPI yaml 写的 `/webhook/*` 实际返回 404。`/callback/set` 接受任意 URL 无验证;payload schema 未文档化。设计的 Phase 0 是先建 `geelark_callback_log` 表 + `/api/geelark/callback` 落库 endpoint 抓真实样本——这条路径**应该直接复用**,不重新发明。
- HMAC / 签名:GeeLark 没有公开签名机制。当前共识仅用 query string token (`?token=<GEELARK_CALLBACK_TOKEN>`)。本 spec 沿用。

**实测项**:

- `/open/v1/task/cancel` 实测:
  - 实际接受单数 `id` 还是复数 `ids` (或两者都行)。
  - 对 `status=2`(running) 的 RPA task 是否真的能取消;cancel 后 task detail 的 `status` 是否变 7(cancelled) 以及多久才变。
  - 返回成功(`code=0`) 是代表 "已停止" 还是 "已接受取消请求"。
  - 对 `status=3`(已完成) 的 terminal task 调 cancel 的响应。
- GeeLark callback 实测(复用 2026-06-09 design 的 Phase 0):
  - 部署 `geelark_callback_log` 表 + `/api/geelark/callback` log-only endpoint。
  - 调 `/open/v1/callback/set` 注册 URL,再触发一次 warmup,看是否收到 POST。
  - payload 是否含稳定 `taskId` / `status` 字段。
  - 对所有 terminal status (3/4/7) 是否都回调;到达时延对比当前 polling。
- 结论写回本 spec:
  - cancel 强可用:Phase 0b 实施 cancel + stop + verify。
  - cancel 弱可用:Phase 0b 只能实施 stop + verify + alert,并降低 timeout SLA。
  - callback 可用:Phase 2 可以把 callback 设为主路径。
  - callback 不可用:Phase 2 保持 polling 主路径。
- **Phase 0a DoD**:在本 spec 顶部新增 "Phase 0a Findings" 一节(放在"背景"之前),至少包含:
  - 实测日期、token 来源、所选 env_id / account_id。
  - cancel:HTTP 路径、request/response 示例、对 running RPA task 的实际效果、cancel → task detail 状态切换的可观察时延。
  - callback:是否能注册业务 callback URL、payload schema、是否对所有 terminal status 都回调、到达时延对比 polling。
  - 对 Phase 0b / Phase 2 设计的决策影响,以及任何被推翻的本 spec 条目。
  - 结论只在 commit message 或聊天里出现不算 DoD,必须落到 spec。

### Phase 0b：立即止血

目标是用 3-5 天修补当前真实成本风险。

- 基于 0a 结果接入 cancel；若 cancel 不可靠，也要实现 stop + verify + alert。
- timeout 时执行 cancel/best-effort cancel + stop + verify，而不是只 stop。
- 修复 publish 成本归因对 `posted_at` 的依赖,至少先用 `geelark_task_id`、task submit/success/check 时间补强 R3 这类漏归因。
- 在 cost detail 里显示 attribution confidence/reason,便于排查。**Phase 0b 不持久化这两个字段**——只在 `/api/costs/...` 响应里现算现返回,`cost_attributions` 表留给 Phase 3。不要提前给 `geelark_transactions` 加列。

### Phase 1：引入 Phone Sessions

- 添加 `phone_sessions` 表。
- 实现 `Phone Runtime Manager`。
- runtime manager 创建/关闭 session 时 dual-write `accounts.phone_busy_until`。
- publish、warmup、engagement/comment 全部通过 runtime manager 开机和停机。
- active session 唯一约束：同一 `account_id/env_id` 同一时间只能有一个 active session。
- `idle_phone_sweeper` 改为 dual-read：没有 active session 且 account lease 过期，才判定 idle。
- 保留现有 Procrastinate worker，但逐步去掉业务服务里的分散 start/stop。

### Phase 2：Task Runs 和 Finalize 路径

- 添加 `task_runs`，暂不添加 `task_events`。
- 所有 terminal transition 统一走 finalize helpers。
- 如果 0a 证明 callback 可用：callback 作为主路径，polling 作为兜底。
- 如果 0a 证明 callback 不可用：polling 仍是主路径，但写入 `task_runs` 状态。
- 超时、取消、失败、成功都有明确 task state 和 timestamps。

### Phase 3：成本归因固化

- 添加 `cost_attributions` 表。
- transaction sync 后异步归因。
- Costs overview、pie chart、transaction table 都读 attribution ledger。
- historical fallback 仅用于没有 phone_session 的老数据。

### Phase 4：Watchdog 和人工操作闭环

- 定期检测外部开机。
- 复用现有 manual hold / release，把状态迁移到 `phone_sessions(source='manual')`。
- external session 超时告警或自动 stop。
- 增加 Ops 视图：active sessions、overdue sessions、stop failed sessions。

### Phase 5：评估 Temporal

- 当上面的状态模型稳定后，再决定是否把 Procrastinate workflow 迁到 Temporal。
- 迁移时保持 `phone_sessions` 和 `task_runs` 作为业务事实表，Temporal 只替换编排层。

## 风险和取舍

- 表会变多，系统显得更重。但这是云手机按分钟扣费系统应有的账本复杂度。
- task cancel 的 GeeLark API 语义需要实测。如果 cancel 不是强一致，仍要依赖 stop verification 和 watchdog。
- callback 可能不可用或 payload 不稳定；设计不能依赖 callback 才正确，必须保留 polling 主路径/兜底。
- `phone_sessions` 与 `phone_busy_until` dual-write 期间会有一致性风险。迁移期 sweeper 应 dual-read 且偏向不误停，再用 overdue watchdog 找出残留 session。
- 自动 stop 外部开机可能误伤人工操作，所以初期建议先告警和展示，再逐步自动化。
- 旧历史数据没有 `phone_session`，只能 best-effort 补归因，不能保证全部准确。

## 推荐决策

推荐按这个顺序推进：

1. 先补 task cancel + stop verification，降低继续烧钱的风险。
2. 再做 `phone_sessions` 和 `Phone Runtime Manager`，把开机入口统一。
3. 然后做 `task_runs` 和统一 finalize 路径；callback 只有实测可用才设为主路径。
4. 最后固化 cost attribution，让成本页解释每一笔扣费。
5. Temporal 作为中长期演进，不作为当前阻塞项。

这条路径的重点不是“worker 要不要存在”，而是让 worker 只做一件明确的事：可靠地编排所有会花钱的云手机动作，并把每一步写进账本。

## Review 关注点

请重点 review 这些问题：

- `manual/external_detected` 是否先只告警展示，还是超过阈值自动 stop。
- publish 默认预算是 15 分钟是否合适。
- 是否接受先继续用 Procrastinate，把 Temporal 放到状态模型稳定之后。
- Phase 0a 的 cancel/callback 实测是否作为所有后续实现的强制入口。
- `task_events` 是否接受先不做，只保留为后续 audit 扩展。
