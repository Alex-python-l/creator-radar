# Caption Prompt 优化 + 多题材支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把硬编码 IELTS prompt 改成"系统骨架 + 按 group 可覆盖的账号定位 brief"模式，同时打开字数上限和"高质量模式"两个 settings 开关。

**Architecture:** 系统级 prompt 骨架硬编码在 `generate_caption.py`（output 格式 + content rules + avoid 清单）；用户在 Settings 全局 / 每个 group 单独填一段 "account positioning brief"。运行时 `posting_service` 按 `account.group_id → groups.caption_brief or settings.caption_brief` 解析 brief，连同 `max_len` 和 `thinking_budget` 透传给 Gemini。向后兼容：`generate_caption_for_local_file` 的所有新参数都有默认值，老测试和老调用不动。

**Tech Stack:** Python 3.11 + FastAPI + SQLModel + PostgreSQL + Alembic + Gemini google-genai SDK；前端 React 19 + Vite + Tailwind。

**Spec reference:** [docs/superpowers/specs/2026-05-29-caption-prompt-optimization-design.md](../specs/2026-05-29-caption-prompt-optimization-design.md)

---

## File Structure

### 后端（新建）
- `alembic/versions/<auto>_add_group_caption_brief.py` — 加 `groups.caption_brief` 列
- `tests/test_caption_prompt_build.py` — `build_prompt()` 单测
- `tests/test_caption_config_resolution.py` — `_resolve_caption_config()` 单测
- `tests/test_groups_api_caption.py` — `PATCH /api/groups/{id}` 接收 caption_brief 单测
- `tests/test_caption_thinking_budget.py` — high_quality → thinking_budget 透传单测
- `tests/test_settings_api_defaults.py` — `GET /api/settings` 兜底单测

### 后端（修改）
- `app/config.py:40` — `SETTINGS_DEFAULTS` 加 3 个 key + `DEFAULT_CAPTION_BRIEF` 常量
- `app/models.py:20-31` — `Group` 加 `caption_brief: Optional[str]` 字段
- `generate_caption.py:34-50,68-95,196-261` — 删 `CAPTION_PROMPT`/`CAPTION_MAX_LEN`；新增 `PROMPT_TEMPLATE`/`CAPTION_MAX_LEN_DEFAULT`/`build_prompt()`；`sanitize_caption()` 和 `generate_caption_for_local_file()` 加新参数
- `app/services/groups_service.py` — 加 `update_caption_brief()`
- `app/api/groups.py:48-59` — `patch_group` 接受 `caption_brief`
- `app/api/settings.py:11-13` — `GET /api/settings` 合并 SETTINGS_DEFAULTS 兜底
- `app/services/posting_service.py:7-10,20-41,116-160` — `_resolve_caption_config()`、`_caption(session, local_path, acc)`、两处 caller 加 `acc`

### 前端（修改）
- `frontend/src/api.ts:77` — `patchGroup` 支持 `caption_brief` 字段
- `frontend/src/pages/Settings.tsx` — 加 Caption section（textarea + max_len + toggle）
- `frontend/src/pages/Accounts.tsx:658-680` — Group header 加 "Group settings" 按钮 + Modal（编辑 name + brief）

---

## Pre-flight

确保测试 DB 在最新 schema：

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test \
  uv run alembic upgrade head
```

确认起手测试全绿：

```bash
uv run pytest -q
```

Expected: `297 passed`（与 CLAUDE.md 一致）。如果不是 297，先 fix。

---

### Task 1: Alembic migration — Group.caption_brief

**Files:**
- Create: `alembic/versions/<auto>_add_group_caption_brief.py`

- [ ] **Step 1: 生成 revision**

```bash
uv run alembic revision -m "add group.caption_brief"
```

Expected: 输出 `Generating alembic/versions/<hash>_add_group_caption_brief.py ... done`. 复制 `<hash>` 用于下一步。

- [ ] **Step 2: 填 upgrade/downgrade**

打开新生成的 `alembic/versions/<hash>_add_group_caption_brief.py`，把空的 `upgrade()` / `downgrade()` 替换为：

```python
def upgrade() -> None:
    op.add_column(
        "groups",
        sa.Column("caption_brief",
                  sqlmodel.sql.sqltypes.AutoString(),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column("groups", "caption_brief")
```

确保文件顶部 import 含 `import sqlmodel.sql.sqltypes`（参照 `69748d477a7f_publications_table_and_platform_columns.py:12`，新生成 revision 会自动加；如缺，手动加）。

- [ ] **Step 3: 在测试 DB 跑 migration**

```bash
DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test \
  uv run alembic upgrade head
```

Expected: `Running upgrade ... -> <hash>, add group.caption_brief`.

- [ ] **Step 4: 在 prod-like dev DB 同步跑**

```bash
uv run alembic upgrade head
```

Expected: 同上。

- [ ] **Step 5: 验证列已加**

```bash
psql -U Zhuanz -d mkt_test -c "\d groups" | grep caption_brief
```

Expected: 输出含 `caption_brief | character varying`.

- [ ] **Step 6: commit**

```bash
git add alembic/versions/<hash>_add_group_caption_brief.py
git commit -m "feat(db): add groups.caption_brief column"
```

---

### Task 2: Group model — caption_brief field

**Files:**
- Modify: `app/models.py:20-31`

- [ ] **Step 1: 加字段**

把 `Group` 类（[app/models.py:20](../../../app/models.py)）的字段列表里加 `caption_brief`，最终如下：

```python
class Group(SQLModel, table=True):
    __tablename__ = "groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    target_phone_count: Optional[int] = None
    notes: Optional[str] = None
    caption_brief: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    archived_at: Optional[datetime] = None
```

- [ ] **Step 2: 跑现有 group 相关测试不破**

```bash
uv run pytest -q -k "group"
```

Expected: 所有现有 group 测试 pass，无 `column does not exist` 错误（因为 Task 1 已经加了列）。

- [ ] **Step 3: commit**

```bash
git add app/models.py
git commit -m "feat(models): Group.caption_brief field"
```

---

### Task 3: SETTINGS_DEFAULTS — 加 3 个 caption key

**Files:**
- Modify: `app/config.py:40`

- [ ] **Step 1: 写测试断默认值存在**

Create `tests/test_settings_defaults.py`:

```python
"""SETTINGS_DEFAULTS includes the three caption-related keys."""
from app.config import SETTINGS_DEFAULTS


def test_caption_brief_default_is_ielts_friendly():
    assert "caption_brief" in SETTINGS_DEFAULTS
    v = SETTINGS_DEFAULTS["caption_brief"]
    assert "IELTS" in v
    assert "#IELTS" in v


def test_caption_max_len_default_is_220():
    assert SETTINGS_DEFAULTS["caption_max_len"] == "220"


def test_caption_high_quality_default_is_off():
    assert SETTINGS_DEFAULTS["caption_high_quality"] == "0"
```

- [ ] **Step 2: 跑测试看 fail**

```bash
uv run pytest tests/test_settings_defaults.py -v
```

Expected: 3 FAIL — `KeyError: 'caption_brief'` etc.

- [ ] **Step 3: 改 app/config.py**

在 `SETTINGS_DEFAULTS` 上方加常量，并扩 dict：

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

- [ ] **Step 4: 跑测试看 pass**

```bash
uv run pytest tests/test_settings_defaults.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: 跑全测试不破**

```bash
uv run pytest -q
```

Expected: 300 passed（297 + 3 new）.

- [ ] **Step 6: commit**

```bash
git add app/config.py tests/test_settings_defaults.py
git commit -m "feat(config): add caption_brief / caption_max_len / caption_high_quality defaults"
```

---

### Task 4: build_prompt + PROMPT_TEMPLATE

**Files:**
- Create: `tests/test_caption_prompt_build.py`
- Modify: `generate_caption.py:34-50` (delete `CAPTION_PROMPT`, add `PROMPT_TEMPLATE` + `build_prompt`)
- Modify: `generate_caption.py:32` (rename `CAPTION_MAX_LEN` → `CAPTION_MAX_LEN_DEFAULT`)

- [ ] **Step 1: 写测试**

Create `tests/test_caption_prompt_build.py`:

```python
"""Unit tests for generate_caption.build_prompt()."""
import generate_caption


def test_brief_appears_in_output():
    out = generate_caption.build_prompt("Travel vlogger, Tokyo focus.", 220)
    assert "Travel vlogger, Tokyo focus." in out


def test_max_len_is_substituted():
    out = generate_caption.build_prompt("anything", 180)
    assert "180" in out
    assert "220" not in out


def test_none_brief_falls_back_to_generic():
    out = generate_caption.build_prompt(None, 220)
    assert "IELTS" not in out  # no implicit IELTS bias
    assert "generic short-form video caption" in out


def test_empty_brief_falls_back_to_generic():
    out = generate_caption.build_prompt("", 220)
    assert "generic short-form video caption" in out


def test_whitespace_only_brief_falls_back():
    out = generate_caption.build_prompt("   \n  ", 220)
    assert "generic short-form video caption" in out


def test_output_rules_section_present():
    out = generate_caption.build_prompt("x", 220)
    assert "Output rules" in out
    assert "No Markdown" in out
    assert 'Option 1 / Option 2' in out


def test_avoid_section_present():
    out = generate_caption.build_prompt("x", 220)
    assert "Avoid" in out
    assert "amazing" in out
    assert "Em dashes" in out
```

- [ ] **Step 2: 跑测试看 fail**

```bash
uv run pytest tests/test_caption_prompt_build.py -v
```

Expected: 7 FAIL — `AttributeError: module 'generate_caption' has no attribute 'build_prompt'`.

- [ ] **Step 3: 改 generate_caption.py**

替换 [generate_caption.py:30-50](../../../generate_caption.py) 的 `CAPTION_MAX_LEN = 150` + `CAPTION_PROMPT = """..."""` 整段为：

```python
# Hard cap matches the prompt's stated upper bound; TikTok itself allows more,
# but anything past this here is almost always Gemini ignoring the brief.
CAPTION_MAX_LEN_DEFAULT = 220

PROMPT_TEMPLATE = """\
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
"""


def build_prompt(brief: str | None, max_len: int) -> str:
    cleaned = (brief or "").strip()
    if not cleaned:
        cleaned = "(no specific positioning — write a generic short-form video caption)"
    return PROMPT_TEMPLATE.format(brief=cleaned, max_len=max_len)
```

- [ ] **Step 4: 跑测试看 pass**

```bash
uv run pytest tests/test_caption_prompt_build.py -v
```

Expected: 7 PASS.

- [ ] **Step 5: commit**

```bash
git add generate_caption.py tests/test_caption_prompt_build.py
git commit -m "feat(caption): introduce PROMPT_TEMPLATE + build_prompt"
```

---

### Task 5: sanitize_caption — 接受 max_len 参数

**Files:**
- Modify: `generate_caption.py:68-95`

- [ ] **Step 1: 写新测试**

Append to `tests/test_caption_sanitize.py`（如果该文件存在）或 create `tests/test_caption_sanitize_maxlen.py`:

```python
"""sanitize_caption() honors the max_len kwarg."""
import generate_caption


def test_default_max_len_uses_220():
    caption = "x" * 250
    out = generate_caption.sanitize_caption(caption)
    assert len(out) <= 220


def test_explicit_max_len_truncates_shorter():
    caption = "x" * 150
    out = generate_caption.sanitize_caption(caption, max_len=100)
    assert len(out) <= 100


def test_explicit_max_len_allows_longer():
    caption = "hello\n#one #two #three"
    out = generate_caption.sanitize_caption(caption, max_len=500)
    assert out == caption
```

- [ ] **Step 2: 跑看 fail**

```bash
uv run pytest tests/test_caption_sanitize_maxlen.py -v
```

Expected: 2-3 FAIL（unexpected keyword argument `max_len`, or 老 150 上限仍生效）.

- [ ] **Step 3: 改 sanitize_caption 签名**

把 [generate_caption.py:68](../../../generate_caption.py) 的 `def sanitize_caption(raw: str) -> str:` 改成：

```python
def sanitize_caption(raw: str, *, max_len: int = CAPTION_MAX_LEN_DEFAULT) -> str:
    """Clean up a Gemini caption response into something postable to TikTok.

    Raises RuntimeError if the model emitted multiple caption options — we'd
    rather fail loudly and let the caller retry than silently pick one.
    """
    if _MULTI_OPTION_RE.search(raw):
        raise RuntimeError("Gemini returned multiple caption options; rejecting")

    text = _BOLD_RE.sub(r"\1", raw)

    kept = []
    for line in text.split("\n"):
        if _PREAMBLE_RE.match(line):
            continue
        if _OPTION_HEADER_RE.match(line):
            continue
        line = _LABEL_RE.sub("", line)
        kept.append(line)

    out = "\n".join(kept)
    out = re.sub(r"\n{2,}", "\n", out).strip()

    if len(out) > max_len:
        out = _truncate_preserving_hashtags(out, max_len)
    return out
```

（变化点：函数签名加 `*, max_len: int = CAPTION_MAX_LEN_DEFAULT`；最后两行的 `CAPTION_MAX_LEN` 改成 `max_len`。其余逻辑不变。）

- [ ] **Step 4: 跑全 caption 测试**

```bash
uv run pytest tests/test_caption_sanitize.py tests/test_caption_sanitize_maxlen.py -v
```

Expected: 老测试全 pass + 新测试 pass。

- [ ] **Step 5: 跑全套**

```bash
uv run pytest -q
```

Expected: 现有 297 + 3 (defaults) + 7 (build_prompt) + 3 (maxlen) = 310 passed.

- [ ] **Step 6: commit**

```bash
git add generate_caption.py tests/
git commit -m "feat(caption): sanitize_caption accepts max_len; default bumped to 220"
```

---

### Task 6: generate_caption_for_local_file — 新参数 brief/max_len/thinking_budget

**Files:**
- Modify: `generate_caption.py:196-261`

- [ ] **Step 1: 写参数透传测试**

Create `tests/test_caption_thinking_budget.py`:

```python
"""generate_caption_for_local_file passes brief/max_len/thinking_budget
through to the Gemini client correctly."""
import os
import pytest
from unittest.mock import MagicMock, patch

import generate_caption


@pytest.fixture
def stub_local_video(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00" * 16)
    return str(p)


def _mock_client():
    """Build a mocked genai client that simulates a successful caption gen."""
    client = MagicMock()
    uploaded = MagicMock(name="hi-file", uri="files/abc", state=MagicMock(value="2"))
    client.files.upload.return_value = uploaded
    client.files.get.return_value = uploaded
    response = MagicMock()
    response.text = "Hello world\n#one #two #three"
    client.models.generate_content.return_value = response
    return client


def test_thinking_budget_defaults_to_zero(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(stub_local_video)

    call = client.models.generate_content.call_args
    assert call.kwargs["config"]["thinking_config"]["thinking_budget"] == 0


def test_thinking_budget_can_be_overridden(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(
        stub_local_video, thinking_budget=4096
    )

    call = client.models.generate_content.call_args
    assert call.kwargs["config"]["thinking_config"]["thinking_budget"] == 4096


def test_brief_reaches_prompt(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(
        stub_local_video, brief="Tokyo travel vlog account."
    )

    call = client.models.generate_content.call_args
    parts = call.kwargs["contents"][0]["parts"]
    text_part = next(p for p in parts if "text" in p)
    assert "Tokyo travel vlog account." in text_part["text"]


def test_max_len_reaches_prompt(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(
        stub_local_video, max_len=180
    )

    call = client.models.generate_content.call_args
    parts = call.kwargs["contents"][0]["parts"]
    text_part = next(p for p in parts if "text" in p)
    assert "180" in text_part["text"]
```

- [ ] **Step 2: 跑看 fail**

```bash
uv run pytest tests/test_caption_thinking_budget.py -v
```

Expected: 4 FAIL（unexpected keyword argument `brief` / `max_len` / `thinking_budget`，或 prompt 文本不包含 brief）.

- [ ] **Step 3: 改函数签名和实现**

替换 [generate_caption.py:196-261](../../../generate_caption.py) 的整个 `generate_caption_for_local_file`：

```python
def generate_caption_for_local_file(
    local_path: str,
    *,
    brief: str | None = None,
    max_len: int | None = None,
    thinking_budget: int = 0,
    model: str | None = None,
) -> str:
    """
    Upload a local video file to Gemini, generate a caption, and return it.
    Cleans up the Gemini file after use.
    Raises RuntimeError on any failure.

    `brief` is the account-positioning paragraph; falls back to a generic
    placeholder if None / empty.
    `max_len` caps the total caption length (incl. hashtags); defaults to
    CAPTION_MAX_LEN_DEFAULT.
    `thinking_budget` controls Gemini's reasoning token budget; 0 disables
    thinking (faster + cheaper).
    `model` overrides the GEMINI_MODEL default so the dashboard's model
    picker can flow through without touching this module's globals.
    """
    client = _get_client()

    log.info(f"  Uploading to Gemini File API: {os.path.basename(local_path)}")
    with open(local_path, "rb") as f:
        gemini_file = client.files.upload(
            file=f,
            config={"mime_type": "video/mp4"},
        )

    file_name = gemini_file.name
    log.info(f"  Gemini file uploaded: {file_name} — waiting for processing...")

    deadline = time.time() + FILE_PROCESSING_TIMEOUT
    while time.time() < deadline:
        gemini_file = client.files.get(name=file_name)
        state = gemini_file.state
        state_val = state.value if hasattr(state, "value") else str(state)
        if str(state_val) in ("2", "ACTIVE"):
            break
        if str(state_val) in ("3", "FAILED"):
            client.files.delete(name=file_name)
            raise RuntimeError(f"Gemini file processing failed (state={state_val})")
        time.sleep(FILE_PROCESSING_POLL)
    else:
        client.files.delete(name=file_name)
        raise RuntimeError(f"Timed out waiting for Gemini to process file after {FILE_PROCESSING_TIMEOUT}s")

    chosen_model = (model or "").strip() or GEMINI_MODEL
    effective_max_len = max_len or CAPTION_MAX_LEN_DEFAULT
    prompt = build_prompt(brief, effective_max_len)

    log.info(f"  Generating caption (model={chosen_model})...")
    try:
        response = client.models.generate_content(
            model=chosen_model,
            contents=[
                {"role": "user", "parts": [
                    {"file_data": {"file_uri": gemini_file.uri, "mime_type": "video/mp4"}},
                    {"text": prompt},
                ]},
            ],
            config={"thinking_config": {"thinking_budget": thinking_budget}},
        )
        raw = (response.text or "").strip()
    finally:
        try:
            client.files.delete(name=file_name)
        except Exception:
            pass

    if not raw:
        raise RuntimeError("Gemini returned an empty caption")

    caption = sanitize_caption(raw, max_len=effective_max_len)
    if not caption:
        raise RuntimeError("Caption became empty after sanitization")
    return caption
```

- [ ] **Step 4: 跑新测试 + 老 caption 测试**

```bash
uv run pytest tests/test_caption_thinking_budget.py tests/test_caption_sanitize.py tests/test_gemini_model_picker.py -v
```

Expected: 全 pass（老测试不传新参数走默认）。

- [ ] **Step 5: 跑全套**

```bash
uv run pytest -q
```

Expected: 至少 314 passed.

- [ ] **Step 6: commit**

```bash
git add generate_caption.py tests/test_caption_thinking_budget.py
git commit -m "feat(caption): generate_caption_for_local_file accepts brief / max_len / thinking_budget"
```

---

### Task 7: groups_service.update_caption_brief

**Files:**
- Modify: `app/services/groups_service.py`

- [ ] **Step 1: 写测试**

Create `tests/test_groups_service_caption.py`:

```python
"""groups_service.update_caption_brief — empty/None/whitespace -> NULL."""
from app.services import groups_service as gs


def test_set_brief_to_non_empty(session):
    gid = gs.create(session, name="g1")
    gs.update_caption_brief(session, gid, "Travel vlog")
    row = gs.get(session, gid)
    assert row["caption_brief"] == "Travel vlog"


def test_empty_string_clears_brief(session):
    gid = gs.create(session, name="g2")
    gs.update_caption_brief(session, gid, "anything")
    gs.update_caption_brief(session, gid, "")
    row = gs.get(session, gid)
    assert row["caption_brief"] is None


def test_whitespace_only_clears_brief(session):
    gid = gs.create(session, name="g3")
    gs.update_caption_brief(session, gid, "anything")
    gs.update_caption_brief(session, gid, "   \n\t  ")
    row = gs.get(session, gid)
    assert row["caption_brief"] is None


def test_none_clears_brief(session):
    gid = gs.create(session, name="g4")
    gs.update_caption_brief(session, gid, "anything")
    gs.update_caption_brief(session, gid, None)
    row = gs.get(session, gid)
    assert row["caption_brief"] is None


def test_nonexistent_group_is_noop(session):
    gs.update_caption_brief(session, 99999, "x")  # should not raise
```

- [ ] **Step 2: 跑看 fail**

```bash
uv run pytest tests/test_groups_service_caption.py -v
```

Expected: 5 FAIL — `AttributeError: module ... has no attribute 'update_caption_brief'`.

- [ ] **Step 3: 实现函数**

Append to [app/services/groups_service.py](../../../app/services/groups_service.py):

```python
def update_caption_brief(session: Session, group_id: int,
                         brief: str | None) -> None:
    """Set or clear a group's caption_brief. Empty / whitespace / None all
    store as NULL, preserving the 'unset = inherit settings default' semantic
    used by posting_service._resolve_caption_config."""
    from app.models import Group
    obj = session.get(Group, group_id)
    if obj is None:
        return
    obj.caption_brief = (brief or "").strip() or None
    session.add(obj)
    session.commit()
```

- [ ] **Step 4: 跑测试看 pass**

```bash
uv run pytest tests/test_groups_service_caption.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: commit**

```bash
git add app/services/groups_service.py tests/test_groups_service_caption.py
git commit -m "feat(groups): update_caption_brief service method"
```

---

### Task 8: PATCH /api/groups/{id} 接受 caption_brief

**Files:**
- Create: `tests/test_groups_api_caption.py`
- Modify: `app/api/groups.py:48-59`

- [ ] **Step 1: 写测试**

Create `tests/test_groups_api_caption.py`:

```python
"""PATCH /api/groups/{id} accepts caption_brief."""
from app.services import groups_service as gs


def test_patch_caption_brief_only(client, session):
    gid = gs.create(session, name="t1")
    r = client.patch(f"/api/groups/{gid}",
                     json={"caption_brief": "Tokyo travel"})
    assert r.status_code == 200
    assert gs.get(session, gid)["caption_brief"] == "Tokyo travel"


def test_patch_name_and_brief_in_one_request(client, session):
    gid = gs.create(session, name="t2")
    r = client.patch(f"/api/groups/{gid}",
                     json={"name": "renamed", "caption_brief": "Bali vlog"})
    assert r.status_code == 200
    row = gs.get(session, gid)
    assert row["name"] == "renamed"
    assert row["caption_brief"] == "Bali vlog"


def test_patch_brief_empty_string_clears(client, session):
    gid = gs.create(session, name="t3")
    gs.update_caption_brief(session, gid, "set first")
    r = client.patch(f"/api/groups/{gid}", json={"caption_brief": ""})
    assert r.status_code == 200
    assert gs.get(session, gid)["caption_brief"] is None


def test_patch_brief_null_clears(client, session):
    gid = gs.create(session, name="t4")
    gs.update_caption_brief(session, gid, "set first")
    r = client.patch(f"/api/groups/{gid}", json={"caption_brief": None})
    assert r.status_code == 200
    assert gs.get(session, gid)["caption_brief"] is None


def test_patch_unknown_group_returns_404(client):
    r = client.patch("/api/groups/99999", json={"caption_brief": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: 跑看 fail**

```bash
uv run pytest tests/test_groups_api_caption.py -v
```

Expected: 4 FAIL（PATCH 返回 200 但 brief 没存上）+ 1 PASS（404 case，因为 `gs.get` 检查已在）.

- [ ] **Step 3: 改 patch_group**

Replace [app/api/groups.py:48-59](../../../app/api/groups.py) with:

```python
@router.patch("/{group_id}")
def patch_group(group_id: int, body: dict,
                session: Session = Depends(get_session)):
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

- [ ] **Step 4: 跑测试看 pass**

```bash
uv run pytest tests/test_groups_api_caption.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: commit**

```bash
git add app/api/groups.py tests/test_groups_api_caption.py
git commit -m "feat(api): PATCH /api/groups/{id} accepts caption_brief"
```

---

### Task 9: _resolve_caption_config + _caption(acc)

**Files:**
- Create: `tests/test_caption_config_resolution.py`
- Modify: `app/services/posting_service.py:7-10,20-41,116-160`

- [ ] **Step 1: 写测试**

Create `tests/test_caption_config_resolution.py`:

```python
"""posting_service._resolve_caption_config — brief inheritance & toggles."""
from app.services import posting_service as ps
from app.services import groups_service as gs
from app.repositories import settings as srepo


def test_group_brief_wins_over_settings(session):
    gid = gs.create(session, name="g")
    gs.update_caption_brief(session, gid, "group-specific")
    srepo.update(session, {"caption_brief": "global default"})

    cfg = ps._resolve_caption_config(session, gid)
    assert cfg["brief"] == "group-specific"


def test_settings_brief_when_group_has_no_brief(session):
    gid = gs.create(session, name="g")
    srepo.update(session, {"caption_brief": "global default"})

    cfg = ps._resolve_caption_config(session, gid)
    assert cfg["brief"] == "global default"


def test_none_group_id_uses_settings(session):
    srepo.update(session, {"caption_brief": "global default"})
    cfg = ps._resolve_caption_config(session, None)
    assert cfg["brief"] == "global default"


def test_max_len_default_220_when_setting_missing(session):
    # conftest seeds caption_max_len=220 already; remove to simulate missing
    from sqlalchemy import text
    session.execute(text("DELETE FROM settings WHERE key='caption_max_len'"))
    session.commit()
    cfg = ps._resolve_caption_config(session, None)
    assert cfg["max_len"] == 220


def test_max_len_reads_from_settings(session):
    srepo.update(session, {"caption_max_len": "180"})
    cfg = ps._resolve_caption_config(session, None)
    assert cfg["max_len"] == 180


def test_thinking_budget_zero_when_off(session):
    srepo.update(session, {"caption_high_quality": "0"})
    cfg = ps._resolve_caption_config(session, None)
    assert cfg["thinking_budget"] == 0


def test_thinking_budget_4096_when_on(session):
    srepo.update(session, {"caption_high_quality": "1"})
    cfg = ps._resolve_caption_config(session, None)
    assert cfg["thinking_budget"] == 4096


def test_model_passed_through(session):
    srepo.update(session, {"gemini_model": "gemini-3-flash-preview"})
    cfg = ps._resolve_caption_config(session, None)
    assert cfg["model"] == "gemini-3-flash-preview"


def test_blank_brief_returns_none(session):
    # group with whitespace brief should still fall through to settings
    gid = gs.create(session, name="g")
    srepo.update(session, {"caption_brief": "  "})
    cfg = ps._resolve_caption_config(session, gid)
    assert cfg["brief"] is None
```

- [ ] **Step 2: 跑看 fail**

```bash
uv run pytest tests/test_caption_config_resolution.py -v
```

Expected: 全 FAIL — `AttributeError: module ... has no attribute '_resolve_caption_config'`.

- [ ] **Step 3: 实现 _resolve_caption_config 和改 _caption**

替换 [app/services/posting_service.py:7-10](../../../app/services/posting_service.py) 的 `_caption` 函数为：

```python
def _resolve_caption_config(session, group_id: int | None) -> dict:
    """Resolve which brief/max_len/thinking_budget/model to use for a given
    account's group. Group.caption_brief wins; falls back to settings.
    max_len + high_quality + model are always read from settings (no per-group
    override by design)."""
    s = set_repo.get_all(session)
    group_brief = None
    if group_id is not None:
        from app.repositories import groups as g_repo
        g = g_repo.get(session, group_id)
        if g:
            group_brief = g.get("caption_brief")
    brief = (group_brief or s.get("caption_brief") or "").strip() or None
    return {
        "brief":           brief,
        "max_len":         int(s.get("caption_max_len") or 220),
        "thinking_budget": 4096 if str(s.get("caption_high_quality") or "0") == "1" else 0,
        "model":           (s.get("gemini_model") or "").strip() or None,
    }


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
```

- [ ] **Step 4: 改 _caption 两处调用点传入 acc**

[posting_service.py:23](../../../app/services/posting_service.py) `_post_one` 内：

```python
        caption = _caption(session, v["stored_path"], acc)
```

[posting_service.py:141](../../../app/services/posting_service.py) `submit_video_to_geelark` 内（变量名 `acc` 已存在）：

```python
    caption = _caption(session, v["stored_path"], acc)
```

- [ ] **Step 5: 跑测试看 pass**

```bash
uv run pytest tests/test_caption_config_resolution.py -v
```

Expected: 9 PASS.

- [ ] **Step 6: 跑全 posting / publishing 测试不破**

```bash
uv run pytest -q -k "post or publish or caption"
```

Expected: 全 pass（_caption 调用点签名变了，但调用方都已更新）。

- [ ] **Step 7: 跑全套**

```bash
uv run pytest -q
```

Expected: >= 328 passed.

- [ ] **Step 8: commit**

```bash
git add app/services/posting_service.py tests/test_caption_config_resolution.py
git commit -m "feat(posting): per-group caption brief resolution"
```

---

### Task 10: GET /api/settings 兜底默认值

**Files:**
- Create: `tests/test_settings_api_defaults.py`
- Modify: `app/api/settings.py:11-13`

- [ ] **Step 1: 写测试**

Create `tests/test_settings_api_defaults.py`:

```python
"""GET /api/settings should merge SETTINGS_DEFAULTS so that missing keys
still appear with their default value (lets the UI prefill)."""
from sqlalchemy import text
from app.config import SETTINGS_DEFAULTS


def test_get_returns_defaults_for_missing_keys(client, session):
    # Wipe settings entirely
    session.execute(text("TRUNCATE TABLE settings"))
    session.commit()

    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    for k, v in SETTINGS_DEFAULTS.items():
        assert body[k] == v


def test_db_value_wins_over_default(client, session):
    from app.repositories import settings as srepo
    srepo.update(session, {"caption_max_len": "999"})

    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["caption_max_len"] == "999"


def test_partial_settings_get_merged(client, session):
    session.execute(text("TRUNCATE TABLE settings"))
    session.commit()
    from app.repositories import settings as srepo
    srepo.update(session, {"caption_max_len": "180"})  # only one key set

    r = client.get("/api/settings")
    body = r.json()
    assert body["caption_max_len"] == "180"                       # from DB
    assert body["caption_brief"] == SETTINGS_DEFAULTS["caption_brief"]  # from defaults
```

- [ ] **Step 2: 跑看 fail**

```bash
uv run pytest tests/test_settings_api_defaults.py -v
```

Expected: 2-3 FAIL（GET 返回的 dict 不含 caption_brief etc.）.

- [ ] **Step 3: 改 GET handler**

Replace [app/api/settings.py:11-13](../../../app/api/settings.py):

```python
@router.get("")
def get_settings(session: Session = Depends(get_session)):
    from app import config
    actual = srepo.get_all(session)
    return {**config.SETTINGS_DEFAULTS, **actual}
```

- [ ] **Step 4: 跑测试看 pass**

```bash
uv run pytest tests/test_settings_api_defaults.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: commit**

```bash
git add app/api/settings.py tests/test_settings_api_defaults.py
git commit -m "feat(api): GET /api/settings merges SETTINGS_DEFAULTS as fallback"
```

---

### Task 11: 前端 api.ts — patchGroup 支持 caption_brief

**Files:**
- Modify: `frontend/src/api.ts:77`

- [ ] **Step 1: 看现有签名**

[frontend/src/api.ts:77](../../../frontend/src/api.ts):

```ts
patchGroup: (id: number, name: string) =>
    req(`/groups/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
```

- [ ] **Step 2: 改成接受 partial 对象**

替换为：

```ts
patchGroup: (id: number,
             body: { name?: string; caption_brief?: string | null }) =>
    req(`/groups/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
```

- [ ] **Step 3: 找现有 callers 修签名**

```bash
grep -rn "api.patchGroup" frontend/src
```

把每个调用改成 `api.patchGroup(id, { name })`. 通常只在 GroupRail 重命名场景下使用。

- [ ] **Step 4: 跑 frontend build**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: 编译成功，无 TS 错。

- [ ] **Step 5: commit**

```bash
git add frontend/src/api.ts frontend/src
git commit -m "feat(api): patchGroup accepts caption_brief"
```

---

### Task 12: 前端 Settings.tsx — Caption section

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: 改 CAPTION_KEYS 白名单 + 渲染新 section**

修改 [frontend/src/pages/Settings.tsx](../../../frontend/src/pages/Settings.tsx)：

a) 在文件顶部 `CAPTION_MODEL_KEY` 常量旁加：

```ts
const CAPTION_KEYS = ['caption_brief', 'caption_max_len', 'caption_high_quality'] as const
```

b) 在 `keys` 计算（约 line 94）改为同时排除 caption 三个 key：

```ts
const keys = Object.keys(s).filter(
  (k) => k !== CAPTION_MODEL_KEY && !CAPTION_KEYS.includes(k as any)
)
```

c) 在 scheduler `<section>` 块（约 line 108-137）之后、secrets `<section>` 块（约 line 139）之前，插入新的 Caption section：

```tsx
<section>
  <SectionTitle>Caption generation</SectionTitle>
  <Card className="p-6 flex flex-col gap-5">
    <label className="block">
      <span className="block text-[12.5px] font-medium text-[var(--color-ink-2)] mb-1.5">
        Default account brief
      </span>
      <textarea
        rows={6}
        value={s.caption_brief ?? ''}
        onChange={(e) => update('caption_brief', e.target.value)}
        className="w-full bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[8px] px-3 py-2 text-[14px] text-[var(--color-ink)] focus:border-[var(--color-blue)] focus:ring-2 focus:ring-[var(--color-blue-bg)] transition-shadow font-mono leading-relaxed"
      />
      <span className="block mt-1.5 text-[12px] text-[var(--color-ink-3)]">
        Fallback used when a group has no brief of its own. Describe the account positioning, audience, voice, and required hashtags.
      </span>
    </label>

    <div className="grid grid-cols-2 gap-5">
      <TextField
        label="Max length (chars, incl. hashtags)"
        type="number"
        value={s.caption_max_len ?? '220'}
        onChange={(e) => update('caption_max_len', e.target.value)}
      />
      <Toggle
        label="High-quality mode"
        hint="Enables Gemini thinking (slower, costlier — try when caption quality is unstable)."
        checked={s.caption_high_quality === '1'}
        onChange={(v) => update('caption_high_quality', v ? '1' : '0')}
      />
    </div>
  </Card>
</section>
```

d) 确认 `Toggle` 已经从 `'../components/ui'` 导入；如果 import 行没有 `Toggle`，加上：

```tsx
import { Button, Card, PageTitle, SectionTitle, TextField, Toggle } from '../components/ui'
```

- [ ] **Step 2: 检查 Toggle 组件签名**

```bash
grep -A 15 "export function Toggle" frontend/src/components/ui.tsx
```

确认 props 包含 `label`, `hint`, `checked`, `onChange`（如果 prop 名不同，按实际改 Step 1.c 里的 props）。

- [ ] **Step 3: build 验证**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: 编译成功。

- [ ] **Step 4: 起 dev server 浏览器看**

```bash
./run.sh
```

打开 http://localhost:8080 → 登录 → Settings tab，确认：
- 看到 "Caption generation" section
- "Default account brief" textarea 预填了 IELTS 默认文本（来自 GET /api/settings 兜底）
- "Max length" 显示 220
- "High-quality mode" toggle 关闭状态
- 改任意字段 → Save → 刷新页面 → 改动保留

- [ ] **Step 5: commit**

```bash
git add frontend/src/pages/Settings.tsx
git commit -m "feat(ui): Settings caption section (brief / max_len / high-quality)"
```

---

### Task 13: 前端 Accounts.tsx — Group settings modal

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx:658-700`

- [ ] **Step 1: 加 state + handlers**

在 `Accounts` 组件的 state 区（找到其他 `useState` 附近）加：

```tsx
const [groupSettingsOpen, setGroupSettingsOpen] = useState(false)
const [groupSettingsDraft, setGroupSettingsDraft] = useState<{
  name: string; caption_brief: string
}>({ name: '', caption_brief: '' })
```

- [ ] **Step 2: 在 group header actions 加按钮**

找到 [Accounts.tsx:671-680](../../../frontend/src/pages/Accounts.tsx) 现有的 archived/active button 区，在 active 分支的 `<>` fragment 里加 `Group settings` 按钮（紧挨现有按钮）：

```tsx
<button
  style={secondaryBtn}
  onClick={() => {
    const g = (groups as GroupSummary[]).find(x => x.id === selectedGroup)
    if (!g) return
    setGroupSettingsDraft({
      name: g.name,
      caption_brief: (g as any).caption_brief ?? '',
    })
    setGroupSettingsOpen(true)
  }}
>
  Group settings
</button>
```

- [ ] **Step 3: 加 Modal**

在组件 `return` 块末尾（其他 modal 旁，比如 wizard modal 之后），加 GroupSettings modal：

```tsx
<Modal
  open={groupSettingsOpen}
  onClose={() => setGroupSettingsOpen(false)}
  title="Group settings"
>
  <div className="flex flex-col gap-5 p-1">
    <TextField
      label="Group name"
      value={groupSettingsDraft.name}
      onChange={(e) => setGroupSettingsDraft(d => ({ ...d, name: e.target.value }))}
    />
    <label className="block">
      <span className="block text-[12.5px] font-medium text-[var(--color-ink-2)] mb-1.5">
        Caption brief
      </span>
      <textarea
        rows={6}
        value={groupSettingsDraft.caption_brief}
        onChange={(e) => setGroupSettingsDraft(d => ({ ...d, caption_brief: e.target.value }))}
        className="w-full bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[8px] px-3 py-2 text-[14px] text-[var(--color-ink)] focus:border-[var(--color-blue)] focus:ring-2 focus:ring-[var(--color-blue-bg)] transition-shadow font-mono leading-relaxed"
      />
      <span className="block mt-1.5 text-[12px] text-[var(--color-ink-3)]">
        Leave blank to inherit the global default from Settings.
      </span>
    </label>
    <div className="flex justify-end gap-2">
      <Button variant="ghost" onClick={() => setGroupSettingsOpen(false)}>
        Cancel
      </Button>
      <Button
        variant="primary"
        onClick={async () => {
          if (selectedGroup === ALL_ACCOUNTS) return
          await api.patchGroup(selectedGroup as number, {
            name: groupSettingsDraft.name,
            caption_brief: groupSettingsDraft.caption_brief,
          })
          setGroupSettingsOpen(false)
          // Reload groups so the local list picks up the new caption_brief
          reloadGroups()  // or whatever the page uses to refresh
        }}
      >
        Save
      </Button>
    </div>
  </div>
</Modal>
```

注意：`reloadGroups` 的具体名按页面里现有的命名来（grep `setGroups\|reload\|fetch.*groups` 找）。如果没有专用 reloader，用 `window.location.reload()` 暂时兜底（不优雅，但能保证状态一致）。

- [ ] **Step 4: 确认 import**

文件顶部确认有：

```tsx
import { Button, Card, Modal, TextField, … } from '../components/ui'
```

如缺 `Modal`，补上。

- [ ] **Step 5: build 验证**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: 编译成功。

- [ ] **Step 6: 浏览器测**

`./run.sh` → 进 Accounts 页 → 选一个 group → 右上角 `Group settings` 按钮 → modal 打开 → 改 name 和 brief → Save → 关闭后再次打开 → 看是否持久化。

- [ ] **Step 7: commit**

```bash
git add frontend/src/pages/Accounts.tsx
git commit -m "feat(ui): Group settings modal with caption_brief editor"
```

---

### Task 14: 集成验证 + smoke test

**Files:** 无新增

- [ ] **Step 1: 跑全测试套**

```bash
uv run pytest -q
```

Expected: >= 331 passed（297 baseline + 新增的 ~34 个）。如果数对不上，先查为什么。

- [ ] **Step 2: 前端 build**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

Expected: 成功，dist/ 更新。

- [ ] **Step 3: 起 dev server，跑端到端 smoke**

```bash
./run.sh
```

操作清单：

1. 进 Settings → "Caption generation" section 应该可见，brief 预填 IELTS 内容。
2. 改 max_len = 100 → Save。
3. 进 Accounts → 选一个有视频和 warmed-up 账号的 group → `Group settings` → 改 brief 为：
   ```
   Travel vlogger account, Tokyo focus. Audience: 25-35.
   Hashtags must include #tokyo. Companions: #japan #travel #vlog.
   ```
   → Save。
4. 选一个 PENDING 视频 → Publish → 看后台日志：caption 应该是 Travel/Tokyo 主题，含 #tokyo。

如果 caption 仍然是 IELTS 风格，按这个顺序排查：
- `posting_service._resolve_caption_config` 是不是从该 group 取到了 brief？日志里加 `print(cfg)` 临时查
- `generate_caption_for_local_file` 是不是把 brief 拼到 prompt 里？看 raw prompt 文本
- `gemini_model` 是不是用了新版（`gemini-3-flash-preview` 比 2.x 严格遵循 prompt）

- [ ] **Step 4: 改回 max_len = 220 收尾**

避免影响后续工作。

- [ ] **Step 5: 如果一切 OK，无需 commit**（实施过程没有未提交改动）

---

## Done criteria

- `uv run pytest -q` 全绿，case 数 >= 331
- `cd frontend && npm run build` 成功
- 浏览器手测：改 group brief 后发布 → caption 风格按新 brief 走
- 老 IELTS group 不改 brief 时 → caption 与上线前一致（依赖兜底）
- 没有任何文件留下临时 `print()` / 调试代码

## 不在本 plan 范围内

- 多语言 caption（中文/日文输出）
- Per-account brief 覆盖
- A/B 测试 brief 效果
- 把 SETTINGS_DEFAULTS 拆成 Pydantic settings model（更大的重构）
