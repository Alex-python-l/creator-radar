// Parse a proxy URL (e.g. socks5://user:pass@host.example.com:1080) and
// return `scheme://host:port` — strips username/password but preserves the
// scheme so the row tooltip tells the user which protocol is in use.
export function parseProxyDebug(proxy: string | null | undefined): string | null {
  if (!proxy) return null
  const trimmed = proxy.trim()
  if (!trimmed) return null
  const schemeMatch = trimmed.match(/^([a-z][a-z0-9+\-.]*):\/\//i)
  const scheme = schemeMatch ? schemeMatch[1].toLowerCase() : null
  try {
    const normalized = scheme
      ? trimmed.replace(/^[a-z][a-z0-9+\-.]*:\/\//i, 'http://')
      : `http://${trimmed}`
    const u = new URL(normalized)
    if (!u.hostname) return trimmed
    const hostPort = u.port ? `${u.hostname}:${u.port}` : u.hostname
    return scheme ? `${scheme}://${hostPort}` : hostPort
  } catch {
    return trimmed
  }
}
