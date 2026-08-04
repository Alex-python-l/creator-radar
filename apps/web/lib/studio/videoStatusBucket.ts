import { PLATFORMS } from './api'
import type { VideoListRow } from './useVideoPages'

export type StatusBucket = 'pending' | 'in_progress' | 'handled'

export type BucketWithCount = {
  bucket: StatusBucket
  x: number
  n: number
}

export const BUCKET_LABEL_ZH: Record<StatusBucket, string> = {
  pending: '待处理',
  in_progress: '进行中',
  handled: '已处理',
}

export function deriveStatusBucket(
  platforms: VideoListRow['platforms'],
): BucketWithCount {
  let n = 0
  let ok = 0
  let fail = 0
  let inFlight = 0
  for (const plat of PLATFORMS) {
    const r = platforms[plat]?.result
    if (!r) continue
    n += 1
    if (r === 'SUCCESS') ok += 1
    else if (r === 'FAILURE') fail += 1
    else if (r === 'INITIATED' || r === 'QUEUED') inFlight += 1
  }
  if (inFlight > 0) return { bucket: 'in_progress', x: inFlight, n }
  if (ok + fail > 0) return { bucket: 'handled', x: ok + fail, n }
  return { bucket: 'pending', x: 0, n }
}
