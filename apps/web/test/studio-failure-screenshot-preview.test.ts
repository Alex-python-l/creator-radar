import assert from 'node:assert/strict'
import test from 'node:test'

import { createFailureScreenshotPreview } from '../lib/studio/failureScreenshotPreview.ts'

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
