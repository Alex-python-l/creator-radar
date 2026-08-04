import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const ops = await readFile(
  new URL('../components/studio/pages/Ops.tsx', import.meta.url),
  'utf8',
)
const panel = await readFile(
  new URL('../components/studio/ops/RpaWatchdogPanel.tsx', import.meta.url),
  'utf8',
)
const api = await readFile(
  new URL('../lib/studio/api.ts', import.meta.url),
  'utf8',
)

test('Studio Ops exposes the RPA watchdog control surface', () => {
  assert.match(ops, /RPA Watchdog/)
  assert.match(ops, /RpaWatchdogPanel/)
  assert.match(panel, /opsRpaWatchdogHealth/)
  assert.match(panel, /opsStartRpaWatchdogTask/)
  assert.match(panel, /opsStopRpaWatchdogTask/)
  assert.match(panel, /opsDeleteRpaWatchdogTask/)
})

test('the browser calls Studio proxy routes and never carries the watchdog token', () => {
  assert.match(api, /\/ops\/rpa-watchdog\/health/)
  assert.match(api, /\/ops\/rpa-watchdog\/start/)
  assert.doesNotMatch(api, /X-Watchdog-Token/)
  assert.doesNotMatch(panel, /WATCHDOG_ACCESS_TOKEN/)
  assert.doesNotMatch(panel, /GEELARK_TOKEN/)
})
