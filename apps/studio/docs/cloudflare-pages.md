# Cloudflare Pages front-end deploy

> **DEPRECATED (2026-07-16).** This Cloudflare Pages deploy path
> (`mkt.willtrade.studio`) is a historical leftover and is no longer
> maintained with this repository; it will be retired together with the
> unified portal migration. The authoritative production entrypoint is
> documented in [`docs/production-deploy.md`](production-deploy.md)
> (Aliyun host). Additionally, `frontend/` has been migrated into the
> monorepo pnpm workspace (`@svm/studio-frontend`): `package-lock.json`
> no longer exists, so the `npm ci` build commands in the body below no
> longer work. The content below is kept for historical reference only.

This moves only the React SPA and the browser-facing `/api/*` entrypoint to
Cloudflare. FastAPI, PostgreSQL, GeeLark calls, and `mkt-worker` stay on the
Aliyun host.

## Current target

```text
https://mkt.willtrade.studio
  Cloudflare Pages serves frontend/dist
  Cloudflare Pages Function proxies /api/* to http://139.196.52.92:18081/api/*

http://139.196.52.92:8080
  Existing IP-only nginx -> uvicorn -> FastAPI backend

http://139.196.52.92:18081
  Cloudflare origin-only nginx listener -> uvicorn -> FastAPI backend
  nginx forwards Host as 139.196.52.92:8080 to avoid Aliyun ICP blocking on unfiled hostnames
```

## Current DNS

| Hostname | Type | Target | Proxy | Purpose |
|---|---|---|---|---|
| `mkt.willtrade.studio` | `CNAME` | `geelark-studio.pages.dev` | DNS-only | Browser entrypoint for GeeLark Studio |
| `origin-mkt.willtrade.studio` | `A` | `139.196.52.92` | DNS-only | Legacy/test origin hostname; do not use for Pages API proxy unless the hostname is filed |
| `willtrade.studio` | `MX` / `TXT` only | Cloudflare email records | DNS-only | Mail routing/SPF only |
| `opc.willtrade.studio` | `CNAME` | `756193c8-8636-4995-bd38-b4116a741b3c.cfargotunnel.com` | Proxied | Legacy Tunnel record; not used by GeeLark Studio |

The old apex Tunnel record
`willtrade.studio -> 756193c8-8636-4995-bd38-b4116a741b3c.cfargotunnel.com`
was removed on 2026-06-17. Do not recreate it unless the apex is explicitly
assigned to a new service.

## Free-tier fit

- Cloudflare Pages free tier is enough for the static SPA.
- Static asset requests are free/unlimited on Pages.
- Pages Functions count as Workers requests; the Worker free tier is currently
  100,000 requests/day.
- Cloudflare request body size is currently 100 MB on Free/Pro. That matters
  for `POST /api/videos/upload`; larger uploads need a later object-storage or
  direct-upload path.
- A single Cloudflare Pages static asset must be <= 25 MiB. The Vite bundle is
  expected to stay well below this.

Official references:

- https://pages.cloudflare.com/
- https://developers.cloudflare.com/pages/functions/pricing/
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/pages/platform/limits/

## Files

- `frontend/functions/api/[[path]].js` proxies every `/api/*` request to the
  existing backend, preserving cookies and streaming response bodies.
- `frontend/wrangler.toml` pins the Pages project name, compatibility date,
  build output, and non-secret origin binding.
- `frontend/.dev.vars.example` documents local Wrangler bindings.

## Cloudflare dashboard setup

1. Create a Cloudflare Pages project.
2. Connect the repository.
3. Set the project root directory to `frontend`.
4. Use build command:

   ```bash
   npm ci && npm run build
   ```

5. Use build output directory:

   ```text
   dist
   ```

6. Keep or set the Pages Function variables:

   ```text
   API_ORIGIN=http://139.196.52.92:18081
   MAX_PROXY_BODY_BYTES=104857600
   NODE_VERSION=22
   ```

7. Deploy once and verify the generated `*.pages.dev` URL.
8. Add the real domain in Pages > Custom domains. For this deployment the
   browser URL is:

   ```text
   https://mkt.willtrade.studio
   ```

9. Keep the `mkt.willtrade.studio` CNAME DNS-only, pointing at
   `geelark-studio.pages.dev`. In this zone, proxied traffic is challenged by
   Cloudflare security settings before it reaches Pages.

## Local preview

From `frontend/`:

```bash
cp .dev.vars.example .dev.vars
npm run build
npx wrangler@latest pages dev dist
```

Smoke checks:

```bash
curl -i http://127.0.0.1:8788/api/dashboard
curl -i http://127.0.0.1:8788/accounts
```

Expected:

- `/api/dashboard` returns `401` when not logged in, proving the route reached
  FastAPI.
- `/accounts` serves the SPA.

Do not smoke-test endpoints that start phones unless you intend to spend phone
runtime.

## Manual deploy with Wrangler

If you are logged in to Wrangler:

```bash
cd frontend
npm run build
npx wrangler@latest pages deploy dist --project-name geelark-studio --branch main
```

If Wrangler is not logged in, run:

```bash
npx wrangler@latest login
```

or provide `CLOUDFLARE_API_TOKEN` in the shell/CI environment.

## Known follow-ups

- Add HTTPS on the Aliyun origin or use Cloudflare Tunnel so the Cloudflare to
  origin leg is encrypted.
- Keep the Pages API proxy on the direct IP origin
  `http://139.196.52.92:18081`. The `origin-mkt.willtrade.studio` hostname is
  unfiled and Aliyun returns a Non-compliance ICP page when it is used as the
  HTTP Host.
- Keep `mkt.willtrade.studio` DNS-only. It points to
  `geelark-studio.pages.dev` and avoids the zone-level challenge applied to
  proxied records.
- Do not point the apex `willtrade.studio` back at the old Cloudflare Tunnel.
  The apex currently has only mail-related `MX` / `TXT` records.
- Move large video uploads to R2/OSS/S3-style object storage before making the
  Cloudflare domain the only upload path.
- After the domain is stable, restrict direct origin access where practical.
