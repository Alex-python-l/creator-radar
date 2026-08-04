# Comments Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move TikTok AI comment operations out of Accounts into a dedicated Comments sidebar tab.

**Architecture:** Keep the existing engagement submission API as the command path. Add a read-only engagement targets endpoint for successful TikTok publications with captured share links, then build a focused React page that selects a group, commenter accounts, manual links or owned video targets, probability, and shows recent engagement tasks.

**Tech Stack:** FastAPI, SQLModel/PostgreSQL, React 19, Vite, existing i18n JSON, existing UI primitives.

---

### Task 1: Backend Owned Targets Endpoint

**Files:**
- Modify: `app/repositories/publications.py`
- Modify: `app/api/engagement.py`
- Test: `tests/test_api_engagement.py`

- [ ] Add `list_comment_targets(session, group_id=None, limit=100)` joining `Publication`, `Video`, and `Account`, returning successful TikTok publications with non-empty `share_link`.
- [ ] Add `GET /api/engagement/targets?group_id=...` returning that list.
- [ ] Add tests for filtering to `SUCCESS + tiktok + share_link`, group filtering, and auth-protected response.

### Task 2: Frontend API And Navigation

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/Shell.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] Add `CommentTarget` type and `api.engagementTargets(groupId?)`.
- [ ] Add `/comments` route and sidebar nav item.
- [ ] Add English and Chinese copy for the Comments page.

### Task 3: Comments Page

**Files:**
- Create: `frontend/src/pages/Comments.tsx`

- [ ] Load groups, accounts, engagement targets, and recent tasks.
- [ ] Let the user select a group, commenter accounts, target mode (`Manual links` or `Owned published videos`), target videos, and comment probability.
- [ ] Submit through `api.createEngagementComment` using `target_links` and `target_publication_ids`.
- [ ] Show empty states when no share links exist yet.

### Task 4: Remove Old Accounts Entry

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] Remove the Accounts bulk `AI Comment` button, modal state, submit handler, and recent engagement block.
- [ ] Keep Accounts focused on account inventory and posting/warmup operations.

### Task 5: Verification And Deploy

**Files:**
- No source files unless verification exposes a defect.

- [ ] Run backend focused tests.
- [ ] Run `frontend npm run build`.
- [ ] Run full pytest suite on a clean test DB.
- [ ] Preview `/comments` locally with seeded data and verify manual-link + owned-target flows enqueue tasks.
- [ ] Package and deploy from the clean worktree per `docs/production-deploy.md`.
