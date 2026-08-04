# TikTok 账号管理器重写 — 设计文档

日期：2026-05-19
状态：已通过 brainstorming，待 spec 评审

## 1. 背景与动机

现有系统是「一堆独立 Python 脚本 + 薄 Flask 按钮面板 + Google Sheet 当数据库 + Google Drive 存视频」。
用户反馈的核心痛点（按优先级）：

1. **要到处跳、手动点很多次**：Sheet + GeeLark 桌面端 + dashboard 三处来回；warmup 一天点 2~3 次、设头像点两次、发完还要手动 Check Status。
2. **填 Google Sheet 太繁琐**：手动维护列、每次粘 Sheet URL、不能动 Auto 列。

用户决策（brainstorming 确认）：

- 彻底不要 Google Sheet。
- 视频直接在 dashboard 上传，彻底不要 Google Drive。
- 自动化采用**混合模式**：warmup / 发帖状态回查 / 头像结果回查 走后台定时自动；创建云手机 / 发视频走手动一键触发。
- 存量账号从 GeeLark API 自动导入（接受预热进度与历史发帖记录丢失）。
- TikTok 登录保持人工（在 GeeLark 桌面端做），dashboard 上只用开关标记。
- 改造路线：**彻底重写**为后端 API + 现代前端 SPA。
- 数据库：**SQLite + WAL**（单人单进程场景，零运维，备份=拷一个文件）。

## 2. 目标与非目标

### 目标
- 所有日常操作在一个网页内完成，不再跳 Google Sheet / Google Drive。
- 重复性操作（warmup、状态回查、头像回查）后台自动，无需人工点击。
- 账号数据在页面内行内编辑，替代手填表格。
- 视频本地直传，替代 Drive 文件夹 + folder ID。
- 复用现有成熟资产：`geelark_client.py`、Gemini 文案逻辑、视频分配算法、各脚本业务流程。

### 非目标（YAGNI）
- 不做 RPA 自动登录 TikTok（保持人工标记）。
- 不做多用户 / 权限体系（单人使用）。
- 不做多进程 / 水平扩展（单进程是硬约束）。
- 不迁移 Google Sheet 历史数据（预热进度、历史发帖日志丢弃）。
- 不保留任何 Google Sheets / Drive 集成代码。

## 3. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | FastAPI (Python) | 复用 `geelark_client.py` / Gemini / 分配算法等核心资产 |
| 数据库 | SQLite + WAL，单文件 `app.db` | 单人单进程，零运维，备份=拷文件 |
| 后台调度 | APScheduler（进程内） | 定时 warmup / 状态回查 / 头像回查 |
| 前端 | React + Vite + TypeScript（SPA） | 现代 UX，本地开发快 |
| 实时日志 | SSE (Server-Sent Events) | 长任务实时流式日志 |
| 部署 | FastAPI 单端口托管打包后的前端静态文件 | 不引入 nginx/docker 复杂度，单进程单 worker |

**硬约束**：必须单进程单 worker 启动（APScheduler 进程内调度 + SSE + SQLite 写入共同要求）。不得用 `gunicorn -w >1`。

**彻底移除**：`gspread`、Google Sheets API、Google Drive API、`service_account.json`、`normalize_profiles.py`、`full_reset_normalize.py`、`recover_duplicates.py`、`import_geelark_profiles.py`、`sync_groups.py`、`verify_profiles.py`、旧 `app.py` / `templates/`。

**保留复用**：`geelark_client.py`（原样）、`generate_caption.py` 的 Gemini 调用、`video_utils.py` 的分配算法（2h 冷却 / 最久未发优先）与各脚本业务流程（搬入后端 service 层）。

## 4. 数据模型（SQLite）

### accounts（替代 profiles 标签）
```
id                INTEGER PK
geelark_phone_id  TEXT   -- 与 GeeLark 云手机唯一关联键；导入/匹配靠它不靠名字
profile_name      TEXT UNIQUE
email             TEXT
proxy             TEXT
profile_pic_url   TEXT
group_name        TEXT
tiktok_logged_in  BOOLEAN DEFAULT 0   -- 页面手动开关
pic_status        TEXT   -- none / initiated / done / failed
pic_task_id       TEXT
warmed_up         BOOLEAN DEFAULT 0
time_spent_warming INTEGER DEFAULT 0  -- 秒，累计
max_warm_time     INTEGER DEFAULT 500 -- 秒
last_warm_at      TEXT
last_warm_result  TEXT
last_warm_notes   TEXT
last_posted_at    TEXT
created_at        TEXT
updated_at        TEXT
```

### videos（替代 videos 标签 + 承载本地上传）
```
id               INTEGER PK
original_filename TEXT
stored_path      TEXT    -- 本地上传文件路径；发成功后可清理置空
caption          TEXT    -- 发帖时 Gemini 生成
account_id       INTEGER FK -> accounts.id  -- 分配前为 NULL
result           TEXT    -- PENDING / INITIATED / SUCCESS / FAILURE
geelark_task_id  TEXT
notes            TEXT
uploaded_at      TEXT
posted_at        TEXT
last_checked_at  TEXT
```
视频生命周期：上传=PENDING → 一键发布分配账号并提交=INITIATED → 后台轮询刷成 SUCCESS / FAILURE。

### settings（key-value，页面可改）
默认分组、默认 Android 版本、默认 max_warm_time、warmup 调度时间点、发帖冷却小时数。
密钥 `GEELARK_TOKEN` / `GEMINI_API_KEY` 仍走 `.env`，不进库。

### job_runs（后台任务历史）
```
id          INTEGER PK
job_type    TEXT  -- warmup / status_check / pic_check / create / post
started_at  TEXT
finished_at TEXT
status      TEXT  -- running / ok / error
summary     TEXT  -- 人类可读摘要，供仪表盘时间线展示
```

## 5. 后端 API

分层：FastAPI 路由（薄）→ service 层（业务流程）→ `geelark_client.py` + SQLite。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET/POST/PATCH/DELETE | `/api/accounts` | 账号增删改查 |
| PATCH | `/api/accounts/{id}/login-toggle` | 切换「已登录 TikTok」 |
| POST | `/api/accounts/create-profiles` | 一键对未创建账号建云手机 + 装 TikTok（SSE） |
| POST | `/api/accounts/set-pics` | 提交头像 RPA + 回查上轮结果 |
| POST | `/api/accounts/import-from-geelark` | 从 GeeLark 拉现有云手机建账号记录 |
| POST | `/api/videos/upload` | 多文件直传，落本地 + 建 PENDING 行 |
| GET/DELETE | `/api/videos` | 列出 / 删除待发与历史视频 |
| POST | `/api/videos/publish` | 一键发布：取 PENDING → 分配 → 生成文案 → 上传 GeeLark → 提交（SSE） |
| POST | `/api/phones/stop-all` | 紧急停所有云手机 |
| GET | `/api/dashboard` | 首页聚合统计 + 最近 job_runs |
| GET/PATCH | `/api/settings` | 读写默认配置 |
| GET | `/api/jobs/stream` | SSE 长任务实时日志 |

GeeLark 异常按现有 `_post()` 抛错风格捕获，写入 job_run / 视频 notes；任何启动云手机的流程在每条退出路径（含失败）必须停机。

## 6. 后台调度（APScheduler）

| 定时任务 | 频率 | 行为 |
|---|---|---|
| 自动 warmup | settings 配置时间点（默认 09:00 / 14:00 / 20:00） | 取 `tiktok_logged_in=1 且 warmed_up=0` 账号，跑现有 warmup 批次逻辑（3 台一批、随机时长、用完停机），累计 time_spent_warming，达 max_warm_time 置 warmed_up=1，写 job_run |
| 发帖状态回查 | 每 5 分钟 | 扫 `result=INITIATED` 视频，查 GeeLark 任务，刷 SUCCESS/FAILURE，写 job_run |
| 头像结果回查 | 每 10 分钟 | 扫 `pic_status=initiated`，查任务刷 done/failed |

后台任务与手动「一键发布」之间加进程内锁，避免对同一批账号/手机并发操作。

## 7. 前端 UX

React + Vite + TS SPA，左侧导航 4 个区。单密码登录页（沿用现有方式）。

### 仪表盘
- 统计卡片：账号总数 / 已登录 / 已预热 / 待发视频 / 今日已发
- 「最近后台动作」时间线（读 job_runs）
- 常驻红色「🛑 紧急停所有云手机」

### 账号管理
- 可行内编辑表格：email / proxy / 头像URL / max_warm_time
- 「+ 添加账号」弹窗
- 状态徽章：未创建 / 已创建 / 已登录 / 预热中（进度条 time/max）/ 已预热；头像状态徽章
- 行内「已登录 TikTok」开关
- 顶部一键：▶ 创建云手机、🖼 设置头像、从 GeeLark 导入

### 视频发布
- 拖拽 / 多选本地视频直传 → PENDING 队列
- ▶ 一键发布 → 弹分配预览（视频→账号，按最久未发 / 2h 冷却）→ 确认执行 → SSE 实时日志面板
- 发帖历史表：账号 / 文件 / 文案 / 结果徽章 / 时间 / notes；INITIATED 行随后台回查自动刷新

### 设置
- 默认分组 / Android 版本 / max_warm_time
- Warmup 调度时间点（可增删改）
- 发帖冷却小时数
- 密钥状态：仅显示「已配置 / 未配置」（值在 .env，不显示不可改）

### 长任务体验
建账号、一键发布走 SSE 实时滚日志；过程可离开页面，回来从 job_runs 看结果。

## 8. 存量迁移

首次部署：用户在「账号管理」点「从 GeeLark 导入」，调 GeeLark `list_phones`，对每台云手机建 `accounts` 行，填入 geelark_phone_id / profile_name / group_name；proxy 若 `list_phones` 返回则一并写入，否则留空待用户补。（实现阶段需先核实 `list_phones` 响应字段，spec 不假设其一定含 proxy。）

**已知代价（用户已接受）**：GeeLark 不存 email、profile_pic_url、预热进度、历史发帖记录（proxy 视 API 字段而定）。导入后这些字段为空 / 归零，用户需在账号管理页一次性补 email、头像 URL（必要时补 proxy）；存量账号预热时长从 0 重新累计；历史发帖日志不保留。账号本身与 TikTok 登录态不受影响，照常可发帖。

## 9. 风险与缓解

- **GeeLark RPA / 任务可靠性**：状态回查兜底，失败写 notes，不自动重试 INITIATED/UNKNOWN（沿用现有防重复发帖策略）。
- **云手机计费**：所有启动云手机的流程在每条退出路径停机；保留紧急停机按钮。
- **单进程约束**：部署文档明确单 worker；APScheduler 与手动任务加锁。
- **SQLite 写入**：WAL 模式；单进程单用户不触发锁竞争。
- **本地视频磁盘占用**：发帖成功后清理 stored_path 文件并置空字段。

## 10. 验收标准

- 不再依赖任何 Google API；删除 gspread / Drive / service_account。
- 可在 dashboard 完成：导入存量、增删改账号、标记登录、创建云手机、设头像、上传视频、一键发布、查看历史。
- warmup / 状态回查 / 头像回查后台按配置自动执行，仪表盘可见 job_runs。
- 单条命令本地/服务器单进程启动，SQLite 文件持久化。
