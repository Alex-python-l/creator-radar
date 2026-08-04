import { parseBackendIso } from './time.ts'

const UNKNOWN_UPLOAD_DATE_KEY = 'unknown'

type TimelinePublication = {
  result: string | null
  posted_at?: string | null
}

export type TimelineVideo = {
  id: number
  uploaded_at?: string | null
  publications?: TimelinePublication[]
}

export type VideoDateGroup<T extends TimelineVideo> = {
  key: string
  videos: T[]
}

function uploadTimestamp(video: TimelineVideo) {
  const d = video.uploaded_at ? parseBackendIso(video.uploaded_at) : null
  return d ? d.getTime() : Number.NEGATIVE_INFINITY
}

function uploadDateKey(video: TimelineVideo) {
  const d = video.uploaded_at ? parseBackendIso(video.uploaded_at) : null
  if (!d) return UNKNOWN_UPLOAD_DATE_KEY
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${month}-${day}`
}

export function groupVideosByUploadDate<T extends TimelineVideo>(
  videos: T[],
): VideoDateGroup<T>[] {
  const sorted = [...videos].sort((a, b) => {
    const byTime = uploadTimestamp(b) - uploadTimestamp(a)
    return byTime || b.id - a.id
  })
  const groups = new Map<string, T[]>()
  for (const video of sorted) {
    const key = uploadDateKey(video)
    groups.set(key, [...(groups.get(key) ?? []), video])
  }
  return [...groups.entries()].map(([key, groupedVideos]) => ({
    key,
    videos: groupedVideos,
  }))
}

export function platformIconIsMuted(
  publication: { result?: string | null } | undefined,
): boolean {
  return publication?.result !== 'SUCCESS'
}

export function videoDetailsDisclosureIcon(expanded: boolean): '▾' | '▸' {
  return expanded ? '▾' : '▸'
}

export type VisiblePublicationResult =
  | 'SUCCESS'
  | 'FAILURE'
  | 'INITIATED'
  | 'PENDING'

export function publicationDisplayResult(
  publication: { result?: string | null } | undefined,
): VisiblePublicationResult {
  if (publication?.result === 'SUCCESS') return 'SUCCESS'
  if (publication?.result === 'FAILURE') return 'FAILURE'
  if (publication?.result === 'INITIATED' || publication?.result === 'QUEUED') {
    return 'INITIATED'
  }
  return 'PENDING'
}

export function videoDisplayResult(
  video: { publications?: Array<{ result?: string | null }> },
): VisiblePublicationResult {
  const publications = video.publications ?? []
  const results = publications.map(publicationDisplayResult)
  if (results.includes('FAILURE')) return 'FAILURE'
  if (results.length > 0 && results.every((result) => result === 'SUCCESS')) {
    return 'SUCCESS'
  }
  if (results.includes('INITIATED')) return 'INITIATED'
  return 'PENDING'
}
