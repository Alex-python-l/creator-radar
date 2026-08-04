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
