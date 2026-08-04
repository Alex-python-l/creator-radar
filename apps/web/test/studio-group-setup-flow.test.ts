import assert from 'node:assert/strict'
import test from 'node:test'

import {
  shouldDeleteDraftGroupOnClose,
  wizardModeFromInitialGroup,
  wizardTitleKey,
} from '../lib/studio/groupSetupFlow.ts'

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
