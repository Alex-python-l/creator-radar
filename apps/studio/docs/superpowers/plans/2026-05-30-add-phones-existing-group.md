# Add Phones to Existing Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split empty group creation from the reusable phone/account setup wizard, and add an Add phones entry point for existing groups.

**Architecture:** Keep backend APIs unchanged. Add a tiny frontend helper for wizard flow/cleanup decisions, use it from `GroupWizard`, and wire `Accounts.tsx` so New group creates only a group while Add phones opens the existing wizard with `initialGroupId`.

**Tech Stack:** React 19, TypeScript, Vite, Node `node:test`.

---

## File Map

- Create: `frontend/src/lib/groupSetupFlow.ts`
  - Pure helper for wizard mode, title key, and draft cleanup decision.
- Create: `frontend/tests/groupSetupFlow.test.ts`
  - Node tests proving existing groups are never deleted by wizard close.
- Modify: `frontend/src/components/wizard/GroupWizard.tsx`
  - Track draft group ownership and pass existing group mode into step 1.
- Modify: `frontend/src/components/wizard/Step1Proxies.tsx`
  - Skip group name editing for existing group mode and notify wizard when it created a draft group.
- Modify: `frontend/src/pages/Accounts.tsx`
  - Make New group create an empty group only.
  - Add selected-group Add phones button that opens the wizard for that group.
  - Refresh rows and groups when wizard closes.
- Modify: `frontend/src/components/GroupRail.tsx`
  - Rename the button label to clarify it creates a group only.
- Modify: `frontend/src/api.ts`
  - Add `createGroup`.
- Modify: `frontend/src/i18n/locales/en.json`, `frontend/src/i18n/locales/zh.json`
  - Add wizard copy used by the existing-group mode.

## Task 1: Protect Wizard Flow Logic With Tests

**Files:**
- Create: `frontend/tests/groupSetupFlow.test.ts`
- Create: `frontend/src/lib/groupSetupFlow.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/groupSetupFlow.test.ts`:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  shouldDeleteDraftGroupOnClose,
  wizardModeFromInitialGroup,
  wizardTitleKey,
} from '../src/lib/groupSetupFlow.ts'

test('wizard enters add-phones mode when opened for an existing group', () => {
  assert.equal(wizardModeFromInitialGroup(42), 'addPhones')
  assert.equal(wizardTitleKey(42), 'wizard.title_add_phones')
})

test('wizard enters new-group mode when no initial group is provided', () => {
  assert.equal(wizardModeFromInitialGroup(null), 'newGroup')
  assert.equal(wizardTitleKey(null), 'wizard.title')
})

test('close cleanup deletes only draft groups created by this wizard', () => {
  assert.equal(
    shouldDeleteDraftGroupOnClose({
      initialGroupId: null,
      createdGroupId: 7,
      currentGroupId: 7,
      step: 1,
    }),
    true,
  )
  assert.equal(
    shouldDeleteDraftGroupOnClose({
      initialGroupId: 7,
      createdGroupId: null,
      currentGroupId: 7,
      step: 1,
    }),
    false,
  )
  assert.equal(
    shouldDeleteDraftGroupOnClose({
      initialGroupId: null,
      createdGroupId: 7,
      currentGroupId: 7,
      step: 2,
    }),
    false,
  )
})
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
node --test frontend/tests/groupSetupFlow.test.ts
```

Expected: FAIL because `frontend/src/lib/groupSetupFlow.ts` does not exist.

- [ ] **Step 3: Implement the helper**

Create `frontend/src/lib/groupSetupFlow.ts`:

```ts
export type WizardMode = 'newGroup' | 'addPhones'

export function wizardModeFromInitialGroup(initialGroupId: number | null): WizardMode {
  return initialGroupId === null ? 'newGroup' : 'addPhones'
}

export function wizardTitleKey(initialGroupId: number | null): string {
  return wizardModeFromInitialGroup(initialGroupId) === 'addPhones'
    ? 'wizard.title_add_phones'
    : 'wizard.title'
}

export function shouldDeleteDraftGroupOnClose({
  initialGroupId,
  createdGroupId,
  currentGroupId,
  step,
}: {
  initialGroupId: number | null
  createdGroupId: number | null
  currentGroupId: number | null
  step: number
}): boolean {
  return (
    step === 1 &&
    initialGroupId === null &&
    createdGroupId !== null &&
    currentGroupId === createdGroupId
  )
}
```

- [ ] **Step 4: Verify helper tests pass**

Run:

```bash
node --test frontend/tests/groupSetupFlow.test.ts
```

Expected: PASS.

## Task 2: Wire Existing Group Mode Into the Wizard

**Files:**
- Modify: `frontend/src/components/wizard/GroupWizard.tsx`
- Modify: `frontend/src/components/wizard/Step1Proxies.tsx`
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/zh.json`

- [ ] **Step 1: Update `GroupWizard`**

Use `wizardTitleKey` and `shouldDeleteDraftGroupOnClose`. Add an optional
`initialGroupName` prop, initialize `state.name` from it, track `createdGroupId`,
and pass `existingGroupMode` plus `onDraftGroupCreated` to `Step1Proxies`.

- [ ] **Step 2: Update `Step1Proxies`**

When `existingGroupMode` is true, render read-only group context instead of the
group name and notes inputs, skip the name-required validation, and do not create
a group. When the step creates a draft group for new mode, call
`onDraftGroupCreated(gid)`.

- [ ] **Step 3: Add locale keys**

Add:

```json
"title_add_phones": "Add phones",
"s1": {
  "existing_group": "Adding phones to \"{{name}}\""
}
```

Use the Chinese equivalent in `zh.json`.

## Task 3: Split Accounts Page Entry Points

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/GroupRail.tsx`
- Modify: `frontend/src/pages/Accounts.tsx`

- [ ] **Step 1: Add `api.createGroup`**

Add a `createGroup` wrapper around `POST /api/groups`.

- [ ] **Step 2: Rename GroupRail button**

Change `+ New` to `+ New group`.

- [ ] **Step 3: Make New group create only an empty group**

In `Accounts.tsx`, replace the `onNewGroup={() => setSearchParams({ wizard: 'new' })}`
behavior with a prompt-backed group creation handler that calls `api.createGroup`,
reloads groups, selects the new group, and does not open `GroupWizard`.

- [ ] **Step 4: Add Add phones button**

In the selected active group header, add `+ Add phones`. Clicking it sets
`wizard=phones` while preserving `group={id}`.

- [ ] **Step 5: Pass selected group into the wizard**

When `wizard=phones`, render:

```tsx
<GroupWizard
  mode="modal"
  initialGroupId={selectedGroupId}
  initialGroupName={selectedGroupName}
  onClose={closeWizard}
/>
```

Keep existing `wizard=new` support for direct URLs.

## Task 4: Verify and Commit

**Files:**
- All changed files above.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
node --test frontend/tests/groupSetupFlow.test.ts frontend/tests/videoTimeline.test.ts frontend/tests/time.test.ts frontend/tests/failureScreenshotPreview.test.ts
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS with no TypeScript errors.

- [ ] **Step 3: Review diff**

Run:

```bash
git diff --stat
git diff -- frontend/src/pages/Accounts.tsx frontend/src/components/wizard/GroupWizard.tsx frontend/src/components/wizard/Step1Proxies.tsx
```

Expected: changes are limited to the approved UI split and wizard mode safety.

- [ ] **Step 4: Commit**

Run:

```bash
git add docs/superpowers/plans/2026-05-30-add-phones-existing-group.md \
  frontend/src/lib/groupSetupFlow.ts \
  frontend/tests/groupSetupFlow.test.ts \
  frontend/src/api.ts \
  frontend/src/components/GroupRail.tsx \
  frontend/src/components/wizard/GroupWizard.tsx \
  frontend/src/components/wizard/Step1Proxies.tsx \
  frontend/src/i18n/locales/en.json \
  frontend/src/i18n/locales/zh.json \
  frontend/src/pages/Accounts.tsx
git commit -m "feat(groups): add phones to existing group"
```
