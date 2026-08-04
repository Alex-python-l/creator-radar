# Facebook 自动评论 — 设计文档

- 日期：2026-06-16
- 状态：Draft（待 plan）
- 关联代码：`app/services/engagement_service.py`、`geelark_client.py`、`frontend/src/pages/Comments.tsx`

## 1. 背景

GeeLark Open API 提供 `POST /open/v1/rpa/task/faceBookAutoComment` 端点，允许在指定 Facebook 帖子下进行 RPA 自动评论。本项目已具备完整的 TikTok 评论体系（engagement_tasks / runs、Studio AI 文案生成、Procrastinate 调度、超时回收、screenshot 抓取），需在此基础上扩展支持 Facebook，作为多平台评论的第二条路径。

帐号侧基础设施已就绪：`accounts.facebook_logged_in`、`accounts.last_posted_at_facebook` 已存在，`app/services/platforms.py` 集中管理 4 个平台的列映射。

## 2. 目标与非目标

### 2.1 目标
- 在不破坏现有 TikTok 评论流程的前提下，新增 Facebook 评论端到端能力：从手机筛选、目标帖子选择、评论文案准备，到 GeeLark 任务下发、状态回收、UI 展示。
- 评论文案支持两种来源模式：**Studio AI 生成**（复用 Gemini + comment_prompts 体系）/ **用户手填候选池**（最多 10 条，由 GeeLark 在 RPA 内挑一条发出）。
- 目标帖子支持两种来源：**手填 Facebook 帖子 URL** / **已成功发布的自有 FB publication**（owned）。
- UI 独立页面 `/comments/facebook`，不与现有 TikTok Comments 页混用。

### 2.2 非目标
- 不实现 FB 评论"自动发现"（discover）来源；FB 上没有现成的 owned-style 内容发现路径。
- 不引入 feature flag —— 路径完全独立，旧入口零影响。
- 不抽取 TikTok / FB 共用 UI 组件；新页面以拷贝方式从 `Comments.tsx` 派生，避免提前抽象。
- 不调整现有 `comment_probability` 字段语义；FB 路径忽略它。
- 不为 FB 评论新增冷却（cooldown）—— 与现有 TikTok 评论一致，不限制重复评论同一帖子。

### 2.3 关键未知 — GeeLark `keyword` 字段语义

GeeLark 中英文文档均未明确说明 `keyword`（必填，1–10 个，每个 ≤100 字）字段的内部用途。最合理推测是 RPA 进入帖子后用于"是否值得评论 / 选择哪条候选"的触发匹配。

**设计决策**：黑盒透传 —— UI 让用户每次任务手填关键词列表，后端不做语义解释，原样传给 GeeLark。该决策与项目里 TikTok `commentProbability` 的透传处理方式一致。

## 3. 数据模型与迁移

新增一个 Alembic revision，**全部为非破坏性 ADD COLUMN / CREATE INDEX**。

### 3.1 `engagement_tasks` 新增列

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `platform` | `VARCHAR` NOT NULL | `'tiktok'` | 历史行回填 `'tiktok'`；新行写实际平台。受 `app.services.platforms.PLATFORMS` 校验 |
| `keywords` | `JSON` NOT NULL | `'[]'::json` | FB 任务必填 1–10 个，每个 ≤100 字；TikTok 任务始终为 `[]` |

新增复合索引 `idx_engagement_tasks_platform_status (platform, status)`，给前端 FB 评论页列表查询用。

### 3.2 `engagement_runs` 新增列

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `comment_candidates` | `JSON` NULL | NULL | FB 候选评论池（1–10 条，每条 ≤8000 字）。TikTok 始终为 NULL；卡片展示时用现有 `comment_text` 字段（FB 写候选池里"主条"作展示用） |

### 3.3 `EngagementTask.action` 字符串

- 已有：`'tiktok_ai_comment'`
- 新增：`'facebook_auto_comment'`

### 3.4 历史数据兼容
- 历史 `engagement_tasks` 行：`platform = 'tiktok'`、`keywords = []`，行为与当前完全一致。
- 历史 `engagement_runs.comment_candidates = NULL`，不影响 TikTok 路径。

### 3.5 Alembic 迁移骨架

```python
def upgrade():
    op.add_column("engagement_tasks",
        sa.Column("platform", sa.String(),
                  nullable=False, server_default="tiktok"))
    op.add_column("engagement_tasks",
        sa.Column("keywords", sa.JSON(),
                  nullable=False, server_default=sa.text("'[]'::json")))
    op.create_index("idx_engagement_tasks_platform_status",
                    "engagement_tasks", ["platform", "status"])
    op.add_column("engagement_runs",
        sa.Column("comment_candidates", sa.JSON(), nullable=True))

def downgrade():
    op.drop_index("idx_engagement_tasks_platform_status",
                  table_name="engagement_tasks")
    op.drop_column("engagement_runs", "comment_candidates")
    op.drop_column("engagement_tasks", "keywords")
    op.drop_column("engagement_tasks", "platform")
```

PG 16 下 `ADD COLUMN ... server_default` 不锁全表；安全。

## 4. GeeLark 客户端

`geelark_client.py` 新增方法，签名沿用项目里其他 `create_*_task` 的风格：

```python
def create_facebook_auto_comment_task(
    self,
    env_id: str,
    post_address: str,
    comments: list[str],
    keywords: list[str],
    *,
    schedule_at: int | None = None,
    name: str | None = None,
    remark: str | None = None,
) -> str:
    """Schedule a Facebook auto-comment task. Returns the task ID."""
```

**行为约束**：

- `scheduleAt`：缺省 `int(time.time())`
- `id = env_id`
- `postAddress = post_address`（**不在 client 层截断** — URL 截断会破坏链接；长度由 service 层 §5.1 校验。详见 §11 已知风险）
- `comment` = 入参 `comments`；service 层保证 1 ≤ len ≤ 10，每条 ≤ 8000 字（`[:8000]` 兜底）
- `keyword` = 入参 `keywords`；service 层保证 1 ≤ len ≤ 10，每个 ≤ 100 字（`[:100]` 兜底）
- `name[:128]`、`remark[:200]`，与 TikTok 一致
- 端点：`POST /open/v1/rpa/task/faceBookAutoComment`
- 返回 `data.taskId`；找不到抛 `RuntimeError(f"facebook comment task created but no taskId: {data}")`

## 5. Service 层

### 5.1 平台校验 & URL 归一化

```python
ACTION_TIKTOK_AI_COMMENT = "tiktok_ai_comment"
ACTION_FACEBOOK_AUTO_COMMENT = "facebook_auto_comment"

PLATFORM_TO_ACTION = {
    "tiktok":   ACTION_TIKTOK_AI_COMMENT,
    "facebook": ACTION_FACEBOOK_AUTO_COMMENT,
}

_FB_POST_RE = re.compile(
    r"^https://(www\.|web\.|m\.|mbasic\.)?facebook\.com/"
    r"(?:[\w.\-]+/posts/|permalink\.php\?|share/[pv]/|reel/|video\.php\?|story\.php\?|"
    r"groups/[\w.\-]+/posts/|[\w.\-]+/videos/)"
    r"[\w./?=&%\-]+$"
)
_FB_SHORT_RE = re.compile(r"^https://fb\.(?:watch|com)/[A-Za-z0-9_\-]+/?(?:[?#].*)?$")

def normalize_target_link(raw: str, *, platform: str = "tiktok") -> str:
    """规范化目标链接；platform='facebook' 时按 FB URL 规则校验，**不做截断**。
       超长链接抛 ValueError，由调用方决定是否提示用户改用短链。"""
```

### 5.2 资格判断

```python
def _eligible_account(account: dict | None, *, platform: str) -> bool:
    base = bool(
        account
        and (account.get("geelark_phone_id") or "").strip()
        and account.get("warmed_up") == 1
    )
    if not base:
        return False
    col = LOGGED_IN_COL[platform]   # 来自 app.services.platforms
    return account.get(col) == 1
```

调用现有 `_eligible_account()` 的所有路径需要传 `platform` 参数。

### 5.3 关键词池 & FB 候选池 helpers

```python
FB_KEYWORD_MAX_LEN = 100
FB_COMMENT_MAX_LEN = 8000

def _sanitize_keywords(raw: list[str] | None) -> list[str]:
    """返回 1 ≤ N ≤ 10 个非空 keyword，每个 strip 后 ≤ 100 字，保序去重。
       不满足时抛 ValueError。"""

def _sanitize_fb_candidates(raw: list[str] | None) -> list[str]:
    """返回 1 ≤ N ≤ 10 条非空候选评论，每条 strip 后 ≤ 8000 字。
       不满足时抛 ValueError。"""
```

### 5.4 `create_comment_batch` 扩展

签名加 `platform="tiktok"` 与 `keywords=None`：

```python
def create_comment_batch(
    session, *,
    platform: str = "tiktok",
    account_ids: list[int],
    target_links: list[str] | None = None,
    target_publication_ids: list[int] | None = None,
    comments_per_video: int = 1,         # FB 路径下含义为"每帖候选评论数"，仍 1..10
    group_id: int | None = None,
    comment_probability: int = 100,       # FB 路径忽略
    comment_mode: str = COMMENT_MODE_GEELARK_AI,
    comment_prompt_id: int | null = None,
    provided_comments: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict: ...
```

**FB 路径差异点**：
- `platform="facebook"` 下：`comment_mode` 只允许 `studio_ai` 或 `provided`（拒 `geelark_ai`，因 GeeLark FB 接口无 `useAi` 字段）。
- `keywords` 必填，走 `_sanitize_keywords`；TikTok 路径若收到非空 `keywords` 抛 `ValueError`（防 UI 误传，避免脏数据写入）。
- `target_publication_ids` 中的 publication 须 `platform == "facebook"`（现有代码硬编码 `"tiktok"`，本次按入参参数化）。
- `action` 写 `'facebook_auto_comment'`，`EngagementTask.platform = "facebook"`、`keywords = [...]`。
- 账号-目标编排策略与 TikTok 一致：`itertools.cycle(accounts)` 按 `(target × comments_per_video)` 笛卡儿展开。

### 5.5 `submit_run_to_geelark` 分派

按 `task.action` 分派：

```python
if parent_action == ACTION_TIKTOK_AI_COMMENT:
    task_id = client.create_tiktok_ai_comment_task(...)
elif parent_action == ACTION_FACEBOOK_AUTO_COMMENT:
    candidates = _fb_run_candidates(session, run, parent, generated_text=comment_text)
    task_id = client.create_facebook_auto_comment_task(
        env_id=account["geelark_phone_id"],
        post_address=run["target_link"],
        comments=candidates,
        keywords=parent["keywords"],
        schedule_at=schedule_at,
        name="GeeLark Studio FB Comment",
        remark=f"engagement_run_id={run_id}",
    )
```

`_fb_run_candidates` 行为：

- **`comment_mode == "studio_ai"`**：对同一 `prompt_body + target_title` 调用 `generate_comment.generate_comment_for_target(...)` `comments_per_video` 次，每次 Gemini 独立返回一条评论；K 次结果组装为候选池；写入 `EngagementRun.comment_candidates`；`comment_text` 取候选池第一条作展示用。
- **`comment_mode == "provided"`**：从 `provided_comments` 池抽 `comments_per_video` 条（沿用现有 `_allocate_provided_pool` 在 K > pool size 时的循环逻辑）；写入 `comment_candidates`；`comment_text` 取第一条。

### 5.6 `check_engagement_run` / 超时回收

逻辑完全复用 —— `client.query_tasks([task_id])` 返回的 status (3 / 4 / 7) 与 screenshot 抓取路径与 TikTok 共用。**无需改动。**

### 5.7 其他 service / repo 改动

- `engagement_repo.create_task`：加 `platform`、`keywords` 形参（默认值兼容旧调用）
- `engagement_repo.list_tasks` / `list_runs`：加可选 `platform` 过滤
- `eligible_phones.list_eligible_commenters(platform="tiktok")`：参数化，FB 时切到 `accounts.facebook_logged_in`
- `publications.list_comment_targets(platform="tiktok")`：参数化

## 6. API 层

### 6.1 创建任务 — 新增独立 endpoint

`POST /api/engagement/comment/facebook`：

```jsonc
{
  "account_ids":            [int, ...],
  "target_links":           [str, ...],     // FB 帖子 URL，manual 路径
  "target_publication_ids": [int, ...],     // owned 路径
  "candidates_per_post":    int,            // 1..10；落到 comments_per_video 列
  "keywords":               [str, ...],     // 必填 1..10
  "comment_mode":           "studio_ai" | "provided",
  "comment_prompt_id":      int | null,     // studio_ai 时必填
  "provided_comments":      [str, ...] | null,  // provided 时必填
  "group_id":               int | null
}
```

后端转发：

```python
engagement_service.create_comment_batch(
    session,
    platform="facebook",
    keywords=body["keywords"],
    comments_per_video=body["candidates_per_post"],
    ...
)
```

返回与现有 `/api/engagement/comment` 一致：`{task_id, task_ids, run_ids, created_runs, skipped_runs, deferred_runs}`。

**为何不复用** `/api/engagement/comment`：字段集差异显著（无 `comment_probability`、新增 `keywords`、`comment_mode` 取值受限），独立端点比共用更清晰。两端 handler 都很薄。

### 6.2 列表 / 筛选 — 现有 endpoint 加 `?platform=` 参数

| Endpoint | 改动 |
|---|---|
| `GET /api/engagement/runs?platform=facebook&...` | `engagement_tasks.platform` 过滤；不传时返所有平台（向后兼容） |
| `GET /api/engagement/tasks?platform=facebook&...` | 同上 |
| `GET /api/engagement/eligible-phones?platform=facebook` | 切到 `facebook_logged_in` 检查 |
| `GET /api/engagement/targets?platform=facebook` | `list_comment_targets` 按 platform 过滤 |
| `GET /api/engagement/tasks/{id}` | 无需改动；返回里多 `platform / keywords` 字段 |

### 6.3 错误处理

- 业务校验抛 `ValueError`（URL 不合法、keywords 越界、comment_mode 不被接受等）→ 400 + detail
- Procrastinate `AppNotOpen` → 复用现有 `_defer_submit_engagement_run` 兜底
- 创建任务时若有任一账号不符合 `facebook_logged_in == 1`：与 TikTok 行为一致，直接 400 抛出 `account X is not an eligible Facebook commenter`

## 7. 前端

### 7.1 路由 & 导航

- `frontend/src/App.tsx`：新增路由 `/comments/facebook`
- `frontend/src/components/Shell.tsx`：左侧导航在"评论"项同级新增"FB 评论"条目（扁平结构，与项目风格一致）
- 现有 `/comments` 继续展示 TikTok，向后兼容（书签 / i18n key 不破）

### 7.2 新页面 `frontend/src/pages/FbComments.tsx`

整体结构从 `Comments.tsx` 拷贝派生，**不抽公共组件**（YAGNI）。差异：

- 调 `api.engagementRuns({ platform: 'facebook', ... })`
- 调 `api.createFbEngagementComment(payload)`
- `RecordCard` 文案：`target_link` 标签改为"帖子"；`comment_candidates.length >= 2` 时在主评论下追加灰色 `+N 候选`（N = `comment_candidates.length - 1`）
- 失败截图查看（`/runs/{id}/screenshot`）复用现有逻辑

### 7.3 新组件 `frontend/src/components/FbCommentModal.tsx`

三步向导骨架与 `CommentModal` 一致。

**Step 1 — 选目标**：
- Tab：`Owned`（`api.engagementTargets({ platform: 'facebook' })`） / `Manual`（手填 URL）
- 去掉 Discover Tab
- URL 校验：前端轻量正则提示 + 后端权威校验

**Step 2 — 选手机 + 关键词**：
- 手机列表：`api.eligiblePhones({ platform: 'facebook' })`
- 新增"关键词"区域：textarea 多行输入，1–10 行，每行 ≤100 字；前端硬限制 + 越界 disable 提交
- 旁加 `?` tooltip：`"GeeLark 在打开帖子后，根据这些关键词决定是否评论 / 选择哪条候选。文档未公开细节，建议填与帖子主题相关的词。"`

**Step 3 — 评论文案**：
- 候选条数 selector：`candidates_per_post`，1–10，默认 3
- 标签文案：`"每帖候选评论数（GeeLark 从中随机挑一条发）"`
- 模式 Radio：`Studio AI 生成` / `手填候选`
- Studio AI：选 prompt（复用 `api.commentPrompts()`）
- 手填：textarea 一行一条，1–10 行

**提交 payload**（与 §6.1 一一对应）。

### 7.4 `src/api.ts`

- 新增 `FbCommentSubmitPayload` 类型
- 新增 `api.createFbEngagementComment(payload)` → `POST /api/engagement/comment/facebook`
- 给 `api.engagementRuns / engagementTargets / eligiblePhones` 增加可选 `platform?: 'tiktok' | 'facebook'` 形参

### 7.5 i18n

`frontend/src/locales/{zh,en}.json` 新增 `fbComments.*` 命名空间，关键 key：`title / newComment / keywords / keywordsHelp / candidatesPerPost / candidatesHelp / targetIsPost / pickFbPost`。

### 7.6 视觉

完全复用现有 `ui.tsx` primitives 与 design tokens；`PageTitle` 标签为"Facebook 评论"。

## 8. 测试

按现有 `tests/` 目录结构与命名风格补充。

### 8.1 单元测试

| 文件 | 用例 |
|---|---|
| `tests/test_geelark_client.py` | `create_facebook_auto_comment_task` payload 字段名（`postAddress`、`comment`、`keyword`）、缺省 `scheduleAt`、`taskId` 缺失抛 RuntimeError |
| `tests/test_engagement_service.py` | `normalize_target_link` 对 FB URL 正/反例；`_sanitize_keywords` / `_sanitize_fb_candidates` 边界；`create_comment_batch(platform='facebook', ...)` 写入 `engagement_tasks.platform/keywords`；`comment_mode='geelark_ai'` 在 FB 路径被拒；TikTok 路径回归 |
| `tests/test_eligible_phones_repo.py` | `list_eligible_commenters(platform='facebook')` 切到 `facebook_logged_in` |
| `tests/test_publications_repo.py` | `list_comment_targets(platform='facebook')` 只返 FB publication |
| `tests/test_engagement_repo.py` | 新列读写；`list_runs(platform=...)` 过滤 |

### 8.2 集成测试

| 文件 | 用例 |
|---|---|
| `tests/test_engagement_facebook_flow.py`（新） | 端到端：POST `/api/engagement/comment/facebook` → `submit_engagement_run_task` → mock GeeLark 返回 taskId → `check_engagement_run` 三条主路径 (status 3 / 4 / 7) |
| `tests/test_engagement_api.py`（扩） | `?platform=facebook` 过滤 runs / tasks / eligible-phones / targets 列表 |

### 8.3 基线
- 保持 `uv run pytest -q` ≥ 385 passing（CLAUDE.md 约定）。
- 新增用例不破现有 TikTok 路径。
- 测试中复用 / 新建 fake GeeLark client：写 plan 阶段先验证 `tests/conftest.py` 已有 fake 的形态，决定复用还是局部新建。

## 9. 迁移、回滚、部署

### 9.1 部署顺序（沿用 `docs/production-deploy.md`）

1. 本地 `uv run pytest -q` 全绿
2. `cd frontend && npm run build` 通过
3. 打 tar → scp 到 aliyun `/opt/mkt`
4. **PG 备份**：`docker exec svm-postgres pg_dump -U postgres mkt > backup_pre_fb_comment.sql`（按 CLAUDE.md 习惯，即便非破坏迁移）
5. SSH 至 prod：`cd /opt/mkt && uv run alembic upgrade head`
6. `sudo systemctl restart mkt mkt-worker`
7. 烟雾：用一台已登入 FB 的手机做一次手填 URL 评论，确认 task SUCCESS

### 9.2 回滚预案

| 故障 | 应对 |
|---|---|
| FB 任务全部 FAILURE，TikTok 不受影响 | 后端无需回滚 DB；前端可隐藏入口；查 GeeLark 文档 / 截图 |
| 现有 TikTok 评论 regression | `systemctl stop mkt mkt-worker` → `git revert` 应用层代码 → 重新部署。DB 迁移**不回滚**（新列对旧代码无害） |
| Alembic 迁移失败（极不可能） | `alembic downgrade -1`；必要时用 §9.1 step 4 的 dump 恢复 |

### 9.3 灰度 / Feature Flag

**不加**。理由：路径完全独立（独立 endpoint / UI 页面 / action 值），旧入口零影响；项目无现成 flag 基础设施。

### 9.4 向后兼容

- 新列对旧 ORM / SQL 完全透明
- 现有 `/api/engagement/comment` 行为不变（默认 `platform="tiktok"`）
- 现有 `/api/engagement/runs` 不带 `?platform=` 时返**所有平台** —— 前端 TikTok Comments 页本身不传该参数，行为不变

## 10. 实施切片（供 plan 参考）

1. Alembic migration（§3.5）
2. `geelark_client.create_facebook_auto_comment_task` + 单测（§4 + §8.1 第 1 行）
3. `engagement_service` FB 分支 + 单测（§5 + §8.1 第 2 行）
4. repository 层 platform 参数化（`engagement_repo` / `eligible_phones` / `publications`）+ 单测（§5.7 + §8.1 第 3–5 行）
5. API endpoint `POST /api/engagement/comment/facebook` + 列表 endpoint 加 `?platform=` + 集成测试（§6 + §8.2）
6. 前端 `FbComments` 页 + `FbCommentModal` + `api.ts` 改动 + i18n（§7）
7. 端到端冒烟 + 部署（§9）

## 11. 已知风险

### 11.1 `postAddress` 128 字符限制

GeeLark FB 评论 API 文档声明 `postAddress` 最长 128 字符。但典型的 FB 帖子 URL（含 `pfbid` 长 token）经常超过 130 字符，例如：

```
https://www.facebook.com/zuck/posts/pfbid0KdQH3tBfeY4MzZqGmJSXh3HfzCQ7eVtw7TTPjLgwwAcXjszJj4QbBz1B6w7akG8nl
```

**应对策略**：
1. 不在客户端 / 服务端做截断（截断会破坏链接）。
2. service 层 `normalize_target_link` 对 FB 路径校验 `len(link) > 128` 时抛 `ValueError`，UI 把错误展示给用户。
3. §9.1 step 7 烟雾测试时必须**使用一条真实长 URL** 验证：若 GeeLark 后端实际可接受更长链接（文档不准），降低校验阈值或移除；若实际拒绝，spec 后续追加"用户需先手动获取短链"的说明。

### 11.2 `keyword` 字段语义未公开

见 §2.3。本期透传，不做行为绑定；若烟雾测试发现 GeeLark 行为与预期不符（例如帖子内无关键词时 GeeLark 不评论），将在使用文档中明确说明，不阻塞本期发布。

### 11.3 GeeLark 端缺乏 `useAi` 参数

FB 评论接口不像 TikTok 那样支持 `useAi=1` 让 GeeLark 自己生成评论。我们必须始终提供 `comment` 候选池 —— 这是 `comment_mode='geelark_ai'` 在 FB 路径被拒的根本原因。
