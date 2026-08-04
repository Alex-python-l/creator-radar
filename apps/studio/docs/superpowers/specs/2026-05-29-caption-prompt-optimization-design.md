# Caption Prompt 优化 + 多题材支持 — Design Spec

**Date**: 2026-05-29
**Status**: Draft for user review
**相关代码**: `generate_caption.py`, `app/services/posting_service.py`, `app/models.py`, `app/repositories/{settings,groups}.py`, `app/api/{settings,groups}.py`, `frontend/src/pages/{Settings,Accounts}.tsx`

## 背景

当前 caption 生成提示词硬编码在 [generate_caption.py:34-50](../../../generate_caption.py)：

- 题材硬绑 IELTS（"Must include IELTS"、"#IELTS"），整个项目只能跑这一个赛道。
- 字数硬卡 50–150 字符，含 3–5 hashtag 后正文只剩 60–90 字，hook 易套模板化。
- Hook 示例只列三个（"My IELTS journey…"、"I can't believe…"、"POV…"），在 IELTS TikTok 上已被滥用。
- 缺"避免事项"清单（AI 味词、em dash）。
- `thinking_budget=0` 永远关闭，质量/成本权衡硬编码。
- 没有按 group/账号区分人设的能力。

业务方要求：**支持多赛道**（按 group 划分），并修复上述质量问题。

## 范围

### 改

1. **Prompt 结构**：硬编码 prompt → 系统骨架（写死）+ 用户填的"账号定位 brief"。
2. **数据模型**：
   - `Setting` 新增 3 key：`caption_brief`、`caption_max_len`、`caption_high_quality`。
   - `groups` 表新增 `caption_brief` 列（nullable，留空 = 继承 settings）。
3. **调用链**：`generate_caption_for_local_file()` 新增 `brief` / `max_len` / `thinking_budget` 参数；`posting_service._caption()` 接收 `acc` 并按 group→settings 解析配置。
4. **Sanitizer**：`sanitize_caption()` 接受 `max_len` 参数，废弃 `CAPTION_MAX_LEN=150` 常量。
5. **UI**：Settings 加 "Caption" section（brief textarea + max_len + high-quality toggle）；Group header 加 "Group settings" 入口（modal 编辑 name + brief）。
6. **后端 API**：`PATCH /api/groups/{id}` 扩展支持 `caption_brief` 字段。
7. **Migration**：`db.py` 启动时 `ALTER TABLE groups ADD COLUMN caption_brief` + 插入 3 个 settings 默认值，幂等。

### 不改

- `geelark_client.py`、`posting_service` 的发布主流程、cooldown 计算、SSE 等所有非 caption 路径。
- Per-video `caption` 字段（用户手动写 caption 跳过生成的口子）已存在，本次不动。
- `gemini_model` 选择器、`/api/settings/gemini-models` 接口。
- 鉴权、调度、worker、deploy。

## 数据模型

### Settings 新增 key

| Key | 默认值 | 含义 |
|---|---|---|
| `caption_brief` | 见下方 `DEFAULT_CAPTION_BRIEF` | 全局"账号定位"段；group 没自定义时兜底使用 |
| `caption_max_len` | `"220"` | 整段 caption（含 hashtag）目标字符上限；也是 sanitizer 截断硬上限 |
| `caption_high_quality` | `"0"` | `"1"` 启用 thinking_budget=4096；`"0"` 关闭（保持当前行为） |

### Groups 表新增字段

```python
# app/models.py
class Group(SQLModel, table=True):
    ...
    caption_brief: Optional[str] = None   # 留空 → 走 settings.caption_brief
```

### 取值优先级

```
brief           = group.caption_brief or settings.caption_brief
max_len         = settings.caption_max_len  (永远全局，不分 group)
thinking_budget = 4096 if settings.caption_high_quality == "1" else 0
model           = settings.gemini_model     (沿用现有逻辑)
```

## Prompt 模板

### 系统骨架（硬编码于 `generate_caption.py`）

```text
Write ONE short TikTok caption for the given video.

Output rules (STRICT — violating any of these makes the caption unusable):
- Output ONLY the final caption text. No preamble like "Here is…", "Below is…", "Here are a few options…".
- No Markdown. No **bold**, no headings, no "Title:" / "Caption:" labels.
- Do NOT provide multiple versions, alternatives, or "Option 1 / Option 2".
- Total length: target {max_len} characters or fewer, INCLUDING hashtags.

Content rules:
- Open with a relatable or emotional hook. Vary the hook every time — don't reuse the same opener.
- Casual, personal, mobile-friendly tone. 1–2 emojis.
- End with 3–5 hashtags that fit the lane.
- Ignore any usernames, watermarks, or third-party handles in the video — never copy them into the caption.
- Sound authentic, not promotional.

Avoid (these read as AI-written):
- Filler superlatives: "amazing", "literally", "incredible", "absolutely", "truly"
- Em dashes (—) and en dashes (–); use commas, periods, or line breaks
- Generic vocatives ("guys", "y'all") unless they fit the lane

Account positioning (stay in this lane):
{brief}
```

### `DEFAULT_CAPTION_BRIEF`（兼容当前 IELTS 业务）

```text
Real-person creator in the IELTS / study-abroad lane. Audience: first-time IELTS test-takers, 18–25.
Voice: first-person, encouraging, slightly self-deprecating. Not a tutor, not promoting a course.
Must mention "IELTS" naturally.
Hashtags must include #IELTS. Companions: #ieltspreparation, #ieltstips, #studyabroad, #studymotivation.
```

此默认值与 §"Prompt 模板 → DEFAULT_CAPTION_BRIEF" 一致，会被 `db.py::_seed_caption_defaults()` 在首次启动时插入 `settings.caption_brief`。

### Brief 用法示例（用户后续新建 group 时参考）

```text
Real-person creator sharing US college application experience.
Audience: Chinese high-schoolers prepping for Common App.
Voice: first-person, peer-to-peer. Not an admissions consultant.
Hashtags must include #studyabroad. Companions: #commonapp #collegeessay #internationalstudent #cs申请.
```

## 调用链改造

### `generate_caption.py`

```python
CAPTION_MAX_LEN_DEFAULT = 220   # 替换原 CAPTION_MAX_LEN = 150

PROMPT_TEMPLATE = """..."""     # 上文系统骨架

def build_prompt(brief: str | None, max_len: int) -> str:
    return PROMPT_TEMPLATE.format(
        brief=(brief or "").strip()
              or "(no specific positioning — write a generic short-form video caption)",
        max_len=max_len,
    )

def generate_caption_for_local_file(
    local_path: str,
    *,
    brief: str | None = None,           # 新增
    max_len: int | None = None,         # 新增
    thinking_budget: int = 0,           # 新增
    model: str | None = None,           # 已有
) -> str:
    ...
    prompt = build_prompt(brief, max_len or CAPTION_MAX_LEN_DEFAULT)
    response = client.models.generate_content(
        model=chosen_model,
        contents=[{"role": "user", "parts": [
            {"file_data": {"file_uri": ..., "mime_type": "video/mp4"}},
            {"text": prompt},
        ]}],
        config={"thinking_config": {"thinking_budget": thinking_budget}},
    )
    raw = (response.text or "").strip()
    ...
    return sanitize_caption(raw, max_len=max_len or CAPTION_MAX_LEN_DEFAULT)
```

向后兼容：所有新参数有默认值，旧调用（测试、CLI）不动。

### `sanitize_caption` 微调

```python
def sanitize_caption(raw: str, *, max_len: int = CAPTION_MAX_LEN_DEFAULT) -> str:
    ...
    if len(out) > max_len:
        out = _truncate_preserving_hashtags(out, max_len)
    return out
```

### `app/services/posting_service.py`

```python
def _caption(session, local_path: str, acc: dict) -> str:
    from generate_caption import generate_caption_for_local_file
    cfg = _resolve_caption_config(session, acc.get("group_id"))
    return generate_caption_for_local_file(
        local_path,
        brief=cfg["brief"],
        max_len=cfg["max_len"],
        thinking_budget=cfg["thinking_budget"],
        model=cfg["model"],
    )

def _resolve_caption_config(session, group_id: int | None) -> dict:
    from app.repositories import settings as set_repo
    s = set_repo.get_all(session)
    group_brief = None
    if group_id is not None:
        from app.repositories import groups as g_repo
        g = g_repo.get(session, group_id)
        group_brief = (g or {}).get("caption_brief")
    return {
        "brief":           (group_brief or s.get("caption_brief") or "").strip() or None,
        "max_len":         int(s.get("caption_max_len") or 220),
        "thinking_budget": 4096 if str(s.get("caption_high_quality") or "0") == "1" else 0,
        "model":           (s.get("gemini_model") or "").strip() or None,
    }
```

两处 `_caption()` 调用点（`_post_one` 和 `submit_video_to_geelark`）都已经有 `acc` 在上下文，加一个参数即可。

## UI 改造

### Settings 页 (`frontend/src/pages/Settings.tsx`)

现有 `keys.map(k => <TextField>)` 自动渲染所有 settings key。需要把三个 caption-related key 从通用 map 中剥出来，单独 render 成一个 Caption section：

```tsx
const CAPTION_KEYS = ['caption_brief', 'caption_max_len', 'caption_high_quality']
const schedulerKeys = keys.filter(k => !CAPTION_KEYS.includes(k))

// 新增 section（在 Scheduler 之后、Secrets 之前）
<section>
  <SectionTitle>Caption generation</SectionTitle>
  <Card className="p-6 flex flex-col gap-5">
    <TextArea
      label="Default account brief"
      rows={6}
      hint="Fallback used when a group has no brief of its own. Describe the account positioning, audience, voice, and required hashtags."
      value={s.caption_brief ?? ''}
      onChange={(e) => update('caption_brief', e.target.value)}
    />
    <div className="grid grid-cols-2 gap-5">
      <TextField
        label="Max length (chars, incl. hashtags)"
        type="number"
        value={s.caption_max_len ?? '220'}
        onChange={(e) => update('caption_max_len', e.target.value)}
      />
      <Toggle
        label="High-quality mode"
        hint="Enables Gemini thinking (slower, costlier)."
        checked={s.caption_high_quality === '1'}
        onChange={(v) => update('caption_high_quality', v ? '1' : '0')}
      />
    </div>
  </Card>
</section>
```

如果 `TextArea` 和 `Toggle` 在 `components/ui` 里不存在，在同文件 inline 一个最小版（保留现有视觉风格）。i18n 文案先 hardcode（项目其他 UI 也是英文为主），后续如果要 i18n 再统一处理。

### Group 编辑入口 (`frontend/src/pages/Accounts.tsx:658`)

Group header 右侧现在有 Restore/Archive 按钮区。新增 **"Group settings"** 按钮 → 打开 modal（复用项目已有的 modal 模式 / 直接 inline 一个最小 modal）。

Modal 字段：
- Group name（可改，复用现有 `gs.rename` 路径）
- Caption brief（多行 textarea，留空 = 继承 settings 默认；hint 标注"Leave blank to use the global default"）

保存 → `PATCH /api/groups/{id}` 带 `{name, caption_brief}`，成功后刷新 groups list。

## 后端 API 改造

### `PATCH /api/groups/{group_id}` 扩展

```python
# app/api/groups.py
@router.patch("/{group_id}")
def patch_group(group_id: int, body: dict, session: Session = Depends(get_session)):
    from app.services import groups_service as gs
    if gs.get(session, group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    if "name" in body:
        try:
            gs.rename(session, group_id, body["name"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if "caption_brief" in body:
        gs.update_caption_brief(session, group_id, body["caption_brief"])
    return {"ok": True}
```

`groups_service.update_caption_brief()`：

```python
def update_caption_brief(session: Session, group_id: int, brief: str | None) -> None:
    obj = session.get(Group, group_id)
    if obj is None:
        return
    # 空字符串 / None / 全空白都存为 None，保持 "留空 = 继承 settings" 语义
    obj.caption_brief = (brief or "").strip() or None
    session.add(obj)
    session.commit()
```

## Migration

项目用 **PostgreSQL + Alembic** 管 schema（不是 SQLite 也不是 db.py 手写 ALTER）。已有的 settings 默认值约定：`app/config.py:SETTINGS_DEFAULTS` 是集中字典，`tests/conftest.py` 每次 TRUNCATE 后从它 reseed；生产环境 baseline migration 不插默认值，靠 `GET /api/settings` 在前端展示时兜底。

本次需要三步：

### 1) Alembic migration — Groups 加 `caption_brief` 列

新建 `alembic/versions/<rev>_add_group_caption_brief.py`（用 `uv run alembic revision -m "add group.caption_brief"` 生成）：

```python
def upgrade() -> None:
    op.add_column("groups",
        sa.Column("caption_brief",
                  sqlmodel.sql.sqltypes.AutoString(),
                  nullable=True))

def downgrade() -> None:
    op.drop_column("groups", "caption_brief")
```

不 seed 数据 —— 与 baseline / 现有 migration 风格一致。

### 2) `app/config.py` — `SETTINGS_DEFAULTS` 加 3 个新 key

```python
DEFAULT_CAPTION_BRIEF = """Real-person creator in the IELTS / study-abroad lane. Audience: first-time IELTS test-takers, 18–25.
Voice: first-person, encouraging, slightly self-deprecating. Not a tutor, not promoting a course.
Must mention "IELTS" naturally.
Hashtags must include #IELTS. Companions: #ieltspreparation, #ieltstips, #studyabroad, #studymotivation."""

SETTINGS_DEFAULTS = {
    "default_group": "AU Accounts",
    "default_os": "Android 15",
    "default_max_warm_time": "500",
    "warmup_times": "09:00,14:00,20:00",
    "post_cooldown_hours": "2",
    "caption_brief": DEFAULT_CAPTION_BRIEF,
    "caption_max_len": "220",
    "caption_high_quality": "0",
}
```

测试 conftest 已经从此字典 reseed，自动覆盖新增 key。

### 3) `GET /api/settings` 兜底（保证生产首次进 UI 看到默认值）

[app/api/settings.py:11](../../../app/api/settings.py):

```python
@router.get("")
def get_settings(session: Session = Depends(get_session)):
    actual = srepo.get_all(session)
    return {**config.SETTINGS_DEFAULTS, **actual}   # 缺的 key 用默认值兜底
```

效果：用户首次进 Settings 页看到 caption_brief 已经预填了 IELTS 默认文本（与之前 IELTS 业务一致）；改任何字段后 PATCH 入库；后续 GET 返回入库值覆盖默认值。

`_resolve_caption_config` 也已用 `or 220` 等代码层兜底（见 §调用链改造），所以即使 settings 表整张为空，发布流程也不会崩。

**生产部署影响**：阿里云 `mkt` 上线时跑一次 `alembic upgrade head` 加列；老 caption 行为完全保留（GET 兜底文本 = 当前 IELTS 内容）；无需手动数据回填或停服。

## 测试

### 回归（不应该破）

- `tests/test_caption_sanitize.py` — 全部 case 走默认参数分支，应继续 pass
- `tests/test_gemini_model_picker.py` — 同上

### 新加（4 文件，约 12 case）

1. `tests/test_caption_prompt_build.py`
   - `build_prompt(brief, max_len)` 正确替换占位符
   - `brief=None` / 空字符串 / 全空白 → 通用兜底文本（不含 "IELTS"）
   - `max_len=220` 实际进入 prompt 文本

2. `tests/test_caption_config_resolution.py`
   - group.caption_brief 非空 → 优先
   - group.caption_brief 为空 → 兜底到 settings
   - account.group_id 为 None → 直接取 settings
   - settings.caption_high_quality="1" → thinking_budget=4096
   - settings.caption_high_quality 缺失或 "0" → thinking_budget=0
   - settings.caption_max_len 缺失 → 默认 220

3. `tests/test_groups_api_caption.py`
   - `PATCH /api/groups/{id}` 接受 `caption_brief`
   - 空字符串 / null / 全空白 → 存为 `None`
   - 同请求 name + brief 一起改，两个都生效

4. `tests/test_caption_thinking_budget.py`
   - high_quality 关 → 调用 Gemini 时 `thinking_config.thinking_budget=0`
   - high_quality 开 → `thinking_budget=4096`

总目标：`uv run pytest -q` 保持现有 297 pass + 新增约 12 pass = ~309 pass。

## 不在范围内

- 多语言 caption（中文 caption、日文 caption 等）— 当前 prompt 全英文输出，若未来要中文 brief + 中文 caption，再加 `caption_language` 设置。
- Per-account brief 覆盖 — 当前粒度按 group 已足够，不引入 account-level 字段避免增加 UI 复杂度。
- Per-video brief override — 用户手动写 `videos.caption` 已经能完全跳过生成，不需要额外 brief override。
- 多模板库（命名模板 + 上传时选）— 在 brainstorm 阶段已经排除，按 group 挂载满足当前业务。
- A/B 测试不同 brief 的效果对比 — 后续需求，先做基础设施。
