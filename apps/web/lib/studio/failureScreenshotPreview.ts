export type FailureScreenshotPreview = {
  url: string
  platform: string
  detail: string | null
}

export function createFailureScreenshotPreview({
  url,
  platform,
  detail,
}: {
  url: string | null | undefined
  platform: string
  detail?: string | null
}): FailureScreenshotPreview | null {
  const trimmedUrl = (url ?? '').trim()
  if (!trimmedUrl) return null

  const trimmedDetail = (detail ?? '').trim()
  return {
    url: trimmedUrl,
    platform,
    detail: trimmedDetail || null,
  }
}
