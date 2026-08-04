# Failure Screenshot Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show GeeLark publication failure screenshots inside the current Videos page instead of opening them in a new tab.

**Architecture:** Keep GeeLark screenshot capture unchanged: `publication.fail_screenshot` already reaches the frontend. Add a small frontend helper for preview state and wire `Videos.tsx` to open a modal with the screenshot plus an original-link fallback.

**Tech Stack:** React 19, Vite, TypeScript, Node built-in test runner.

---

### Task 1: Preview State Helper

**Files:**
- Create: `frontend/src/lib/failureScreenshotPreview.ts`
- Test: `frontend/tests/failureScreenshotPreview.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import assert from 'node:assert/strict'
import test from 'node:test'

import { createFailureScreenshotPreview } from '../src/lib/failureScreenshotPreview.ts'

test('creates in-page preview state for a GeeLark failure screenshot', () => {
  assert.deepEqual(
    createFailureScreenshotPreview({
      url: ' https://cdn.example.com/failure.png ',
      platform: 'TikTok',
      detail: ' task failed ',
    }),
    {
      url: 'https://cdn.example.com/failure.png',
      platform: 'TikTok',
      detail: 'task failed',
    },
  )
})

test('ignores empty failure screenshot URLs', () => {
  assert.equal(
    createFailureScreenshotPreview({
      url: ' ',
      platform: 'TikTok',
      detail: 'task failed',
    }),
    null,
  )
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test frontend/tests/failureScreenshotPreview.test.ts`

Expected: FAIL because `frontend/src/lib/failureScreenshotPreview.ts` does not exist yet.

- [ ] **Step 3: Implement the helper**

```ts
export type FailureScreenshotPreview = {
  url: string
  platform: string
  detail: string | null
}

export function createFailureScreenshotPreview({
  url,
  platform,
  detail,
}: {
  url: string | null | undefined
  platform: string
  detail?: string | null
}): FailureScreenshotPreview | null {
  const trimmedUrl = (url ?? '').trim()
  if (!trimmedUrl) return null

  const trimmedDetail = (detail ?? '').trim()
  return {
    url: trimmedUrl,
    platform,
    detail: trimmedDetail || null,
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test frontend/tests/failureScreenshotPreview.test.ts`

Expected: PASS.

### Task 2: Videos Page Modal

**Files:**
- Modify: `frontend/src/pages/Videos.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Add modal state and click handler**

Use `FailureScreenshotPreview | null` state. Replace the failure screenshot `<a target="_blank">` in platform details with a `button` that calls `createFailureScreenshotPreview(...)` and stores the result.

- [ ] **Step 2: Render screenshot modal**

Add a `Modal` below the existing page modals. It should render:
- title from `videos.failureShotTitle`
- failure detail text if present
- `<img src={failureShot.url}>` with `object-contain` and max viewport height
- fallback link from `videos.openOriginalShot`

- [ ] **Step 3: Add translations**

Add:
- `videos.failureShotTitle`
- `videos.failureShotAlt`
- `videos.openOriginalShot`

### Task 3: Verification, Commit, Deploy

**Files:**
- Build output: `frontend/dist`

- [ ] **Step 1: Run focused frontend tests**

Run:
```bash
node --test frontend/tests/failureScreenshotPreview.test.ts frontend/tests/videoTimeline.test.ts frontend/tests/time.test.ts
```

- [ ] **Step 2: Run frontend build**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```

- [ ] **Step 3: Run backend test suite before deployment**

Run:
```bash
uv run pytest -q
```

- [ ] **Step 4: Commit only this feature's source changes**

Run:
```bash
git add docs/superpowers/plans/2026-05-30-failure-screenshot-preview.md \
  frontend/src/lib/failureScreenshotPreview.ts \
  frontend/tests/failureScreenshotPreview.test.ts \
  frontend/src/pages/Videos.tsx \
  frontend/src/i18n/locales/en.json \
  frontend/src/i18n/locales/zh.json
git commit -m "feat(videos): preview failure screenshots inline"
```

- [ ] **Step 5: Deploy using `docs/production-deploy.md`**

Package `app`, `alembic`, root service modules, `pyproject.toml`, `uv.lock`, and fresh `frontend/dist`; upload to `root@139.196.52.92`; extract in `/opt/mkt`; restart `mkt`; smoke test `/`, `/accounts`, and `/api/accounts`.
