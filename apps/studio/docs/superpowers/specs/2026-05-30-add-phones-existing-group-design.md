# Add Phones to Existing Group — Design

Date: 2026-05-30
Status: Approved (brainstorming complete; awaiting implementation plan)

## Goal

Separate the concept of creating a group from the account setup flow that
creates GeeLark phones, assigns credentials, marks login status, and starts
warmup.

Today the Accounts page exposes that setup flow only through **+ New** in the
left `GroupRail`. That makes the user create or think about a new group even
when the actual job is to add more phone environments to an existing group.

The desired behavior is:

- **New group** creates a group only.
- **Add phones** appears for an existing active group and reuses the current
  wizard flow against that group.

## Current Context

Relevant existing pieces:

- `frontend/src/components/GroupRail.tsx`
  - Renders the left group list and the current **+ New** button.
- `frontend/src/pages/Accounts.tsx`
  - Owns selected group state through `?group=...`.
  - Opens `GroupWizard` through `?wizard=new`.
  - Already renders selected group actions such as Warmup, Publish, and the
    group menu.
- `frontend/src/components/wizard/GroupWizard.tsx`
  - Already accepts `initialGroupId?: number | null`.
  - Stores `state.groupId` and passes it through all wizard steps.
  - Currently deletes `state.groupId` on close while still on step 1.
- `frontend/src/components/wizard/Step1Proxies.tsx`
  - Creates a group only when `state.groupId === null`.
  - Always validates pasted proxies against `/api/groups/{groupId}/proxies/check`.
- `frontend/src/components/wizard/Step2Phones.tsx`
  - Calls `/api/groups/{groupId}/phones/create` with selected proxies and count.
- Backend already supports creating phones for any existing group through
  `POST /api/groups/{group_id}/phones/create`.

The main missing piece is the UI entry point and safe wizard mode handling.

## Approaches Considered

### Recommended: reuse `GroupWizard` with an existing-group mode

Add a selected-group **Add phones** entry point that opens `GroupWizard` with
`initialGroupId` set to the currently selected group. Keep the same four steps:
proxy validation, phone creation, credential assignment, login/warmup.

Trade-offs:

- Lowest backend risk because the needed endpoints already exist.
- Preserves the known operator flow.
- Requires small wizard copy/cleanup changes so existing groups are not treated
  as disposable draft groups.

### Alternative: create a separate Add Phones modal

Build a new modal that only contains proxy validation and phone creation.

Trade-offs:

- Simpler for users who only want new GeeLark profiles.
- Duplicates wizard logic and skips the existing credential/login continuation.
- The user explicitly chose to reuse the flow, so this is not the selected path.

### Alternative: keep New Group as the only entry point

Continue requiring operators to start from **New group** and manually move or
merge work into existing groups later.

Trade-offs:

- No implementation work.
- Keeps the current confusing behavior and does not support the stated workflow.

## Proposed UX

### Group rail

The left rail keeps a group creation action, but the label should make the
scope clear: **+ New group** or equivalent short text if space is tight.

Clicking it creates a group only. This can be implemented as a small modal or
prompt using the existing `POST /api/groups` endpoint. It should not open the
phone/account wizard.

### Selected group header

When a non-archived group is selected on the Accounts page, show an **Add
phones** button alongside the existing group-level actions.

Clicking **Add phones** opens the existing wizard in modal mode with:

```tsx
<GroupWizard
  mode="modal"
  initialGroupId={selectedGroupId}
  onClose={closeWizard}
/>
```

The flow then reuses all four existing steps:

1. Validate proxies for the selected group.
2. Create GeeLark phones in that group.
3. Parse and assign credentials to available rows in that group.
4. Mark logged-in accounts and optionally start warmup.

### Wizard copy

The wizard title should reflect mode:

- New setup flow for a newly created group: **New group**
- Existing selected group flow: **Add phones**

Step labels can remain the same.

Step 1 should not require editing the group name when `initialGroupId` is set.
It may show the selected group name as read-only context or omit the name field
entirely in existing-group mode. The proxy textarea and phone count remain
editable.

## Cleanup And Safety

The current `GroupWizard.handleClose` deletes `state.groupId` when the user
closes the wizard on step 1. That is only safe for a draft group created by the
wizard itself.

Implementation must distinguish:

- `initialGroupId === null`: the wizard may create a new draft group during
  step 1, and closing on step 1 may delete that draft if no phones were created.
- `initialGroupId !== null`: the group existed before the wizard opened and
  must never be deleted by wizard close handling.

This can be represented with an internal `createdGroupId` flag or a derived
`ownsDraftGroup` boolean.

## Data Flow

New group only:

```text
GroupRail + New group
  -> create group name modal
  -> POST /api/groups
  -> reload groups
  -> select ?group={id}
```

Add phones to existing group:

```text
Selected group header Add phones
  -> open GroupWizard(initialGroupId={id})
  -> Step1Proxies validates proxies against group id
  -> Step2Phones calls /api/groups/{id}/phones/create
  -> Step3Accounts assigns credentials to phones in that group
  -> Step4Login marks logged-in accounts and can start warmup
  -> close wizard, reload groups and rows
```

## Error Handling

- Creating a new group should surface duplicate-name and empty-name failures in
  the same toast/modal area used by the Accounts page.
- Add phones should be disabled for archived groups.
- If a selected group disappears before opening the wizard, the UI should reload
  groups and avoid opening the wizard.
- Existing SSE and validation errors inside wizard steps should continue to be
  shown in the step body.

## Testing

Frontend verification:

1. `cd frontend && npm run build`
2. Open Accounts page.
3. Click **New group**, create a group, confirm no phone wizard opens.
4. Select the created group and click **Add phones**.
5. Confirm the wizard opens in Add phones mode and uses the selected group.
6. Close the wizard on step 1 and confirm the selected group still exists.
7. Run through proxy validation and phone creation and confirm new phone rows
   appear in the selected group.

Backend verification:

- No new backend API should be required.
- Existing tests for group creation and `/groups/{id}/phones/create` should
  remain passing.

Regression checks:

- Existing group settings, archive, delete, warmup, and publish actions still
  work from the selected group header.
- URL-driven group selection (`/accounts?group={id}` and `/groups/{id}` redirect)
  still lands on the correct group.
