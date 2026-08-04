import assert from 'node:assert/strict'
import test from 'node:test'

import {
  groupVideosByUploadDate,
  platformIconIsMuted,
  publicationDisplayResult,
  videoDetailsDisclosureIcon,
  videoDisplayResult,
} from '../lib/studio/videoTimeline.ts'

test('groups videos by uploaded date with newest dates and videos first', () => {
  const groups = groupVideosByUploadDate([
    { id: 1, uploaded_at: '2026-05-29T02:00:00', publications: [] },
    { id: 2, uploaded_at: '2026-05-30T01:00:00', publications: [] },
    { id: 3, uploaded_at: '2026-05-30T03:00:00', publications: [] },
    { id: 4, uploaded_at: null, publications: [] },
  ])

  assert.deepEqual(
    groups.map((group) => ({
      key: group.key,
      ids: group.videos.map((video) => video.id),
    })),
    [
      { key: '2026-05-30', ids: [3, 2] },
      { key: '2026-05-29', ids: [1] },
      { key: 'unknown', ids: [4] },
    ],
  )
})

test('mutes every platform icon unless that publication succeeded', () => {
  assert.equal(platformIconIsMuted({ result: 'SUCCESS' }), false)
  assert.equal(platformIconIsMuted({ result: 'FAILURE' }), true)
  assert.equal(platformIconIsMuted({ result: 'QUEUED' }), true)
  assert.equal(platformIconIsMuted({ result: 'INITIATED' }), true)
  assert.equal(platformIconIsMuted({ result: 'PENDING' }), true)
  assert.equal(platformIconIsMuted(undefined), true)
})

test('collapses raw publication states to four visible statuses', () => {
  assert.equal(publicationDisplayResult({ result: 'SUCCESS' }), 'SUCCESS')
  assert.equal(publicationDisplayResult({ result: 'FAILURE' }), 'FAILURE')
  assert.equal(publicationDisplayResult({ result: 'INITIATED' }), 'INITIATED')
  assert.equal(publicationDisplayResult({ result: 'QUEUED' }), 'INITIATED')
  assert.equal(publicationDisplayResult({ result: 'PENDING' }), 'PENDING')
  assert.equal(publicationDisplayResult(undefined), 'PENDING')
})

test('summarizes a video to the same four visible statuses', () => {
  assert.equal(
    videoDisplayResult({

      publications: [{ result: 'SUCCESS' }, { result: 'SUCCESS' }],
    }),
    'SUCCESS',
  )
  assert.equal(
    videoDisplayResult({

      publications: [{ result: 'SUCCESS' }, { result: 'FAILURE' }],
    }),
    'FAILURE',
  )
  assert.equal(
    videoDisplayResult({

      publications: [{ result: 'QUEUED' }, { result: 'PENDING' }],
    }),
    'INITIATED',
  )
  assert.equal(videoDisplayResult({ publications: [] }), 'PENDING')
})

test('uses right and down triangles for the video details disclosure icon', () => {
  assert.equal(videoDetailsDisclosureIcon(false), '▸')
  assert.equal(videoDetailsDisclosureIcon(true), '▾')
})
