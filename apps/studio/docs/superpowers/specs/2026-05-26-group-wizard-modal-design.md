# Group Wizard — Modal Mode on Accounts Page

**Date:** 2026-05-26
**Branch:** `worktree-feat+group-wizard-modal` (from `main`)

## Goal

On the Accounts page, clicking **"+ New"** in the left `GroupRail` should
open the `GroupWizard` as a floating modal (overlay), instead of taking
over the main content pane. The modal closes via the existing **✕** in
its top-right corner.

## Context

`GroupWizard` already supports two render modes via a `mode` prop:

- `mode="modal"` — black 50% backdrop + centered 800px white card with
  a built-in ✕ close button.
- `mode="fullpane"` — inline rendering (occupies parent container, no
  backdrop, with a step-progress bar at top).

The Accounts page currently uses `fullpane` (see commit `edfc969`,
2026-05-23-ish). The request is to switch back to the modal
presentation.

## Change

**One file:** `frontend/src/pages/Accounts.tsx`.

Replace the wizard branch:

```jsx
{isWizardActive ? (
  <GroupWizard mode="fullpane" onClose={closeWizard} />
) : (
  <>...main content...</>
)}
```

with:

```jsx
<>
  ...main content...
  {isWizardActive && <GroupWizard mode="modal" onClose={closeWizard} />}
</>
```

`GroupWizard.tsx`, `GroupRail.tsx`, the `?wizard=new` URL convention,
and the `closeWizard` cleanup logic are **unchanged**.

## Preserved behavior

- `?wizard=new` URL param still drives wizard visibility (deep link
  friendly).
- Clicking **✕** invokes `handleClose` inside the wizard, which still
  `DELETE`s any draft group created on step 1 before calling `onClose`.
- `closeWizard` removes the `wizard` URL param and refreshes the groups
  list.

## Explicit non-goals

- Do **not** restyle the modal (user opted to keep the existing
  modal look).
- Do **not** add ESC-to-close or click-backdrop-to-close (out of scope;
  current modal doesn't have these, and adding them is a separate
  enhancement).
- Do **not** change `GroupWizard` internals or any of the step
  components (`Step1Proxies`, `Step2Phones`, `Step3Accounts`,
  `Step4Login`).
- Do **not** change `GroupRail`.

## Verification

1. `npm run build` (frontend) — must still pass with no TS errors.
2. `pytest -q` — baseline count unchanged (changes are frontend-only;
   no backend code touched).
3. Manual browser check (dev server on :8080):
   1. Open `/accounts`.
   2. Click **+ New** in the left rail → modal appears as an overlay;
      the account list behind it is still visible.
   3. Click **✕** → modal disappears; URL no longer contains `wizard`.
   4. While on step 1 of the wizard (group row has been created in the
      DB as a draft), click **✕** → Network panel shows `DELETE
      /api/groups/{id}`.
   5. Run the full 4-step wizard end-to-end → a group is created and
      shows up in the rail after close.
