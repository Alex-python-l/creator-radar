# Production Deployment

> **Current-use boundary (verified 2026-07-29):** the complete production
> release order is the repository root `docs/DEPLOY.md`, section H. This file
> supplements Studio API/Worker packaging, Alembic, systemd and ingress only.
> Date-named migration/cutover sections below are historical incident and
> rollback context; do not replay them on the current database.

Canonical production target:

- API base: `https://igotyoufrank.cn/studio/api/` (creator-radar's nginx
  strips the `/studio` prefix for api/healthz/static and proxies to this
  uvicorn; the dashboard UI itself lives in apps/web since 2026-07-18 — this
  service is API-only, see "Production ingress" below)
- Raw-IP `http://139.196.52.92:8080` still exists but serves GeeLark API
  callbacks only.
- SSH: `root@139.196.52.92`
- Key: `~/.ssh/svm_server`
- App path: `/opt/mkt`
- public ingress: nginx on `0.0.0.0:8080` (GeeLark callback, unchanged) plus
  creator-radar's nginx `/studio` location on `igotyoufrank.cn` (dashboard UI)
- systemd service: `mkt`
- Runtime: `uvicorn app.main:app --host 127.0.0.1 --port 18080 --workers 1` behind nginx (systemd ExecStart calls `.venv/bin/uvicorn` directly; the `uv` toolchain is only used at deploy time via `uv sync`)

Historical plans under `docs/superpowers/plans/` may mention `1.15.155.204`.
That IP is stale for this app; use `139.196.52.92`.

## Preconditions

Run these locally from the repo root:

```bash
uv run pytest -q
```

The full suite should pass. (No frontend build — the UI moved to apps/web
in 2026-07-18's retirement; this service is API-only.)

## GitHub Actions CI/CD(已退役,2026-07-18)

`.github/workflows/studio.yml` 已删除:仓库工作流是本地提交 + 手动
tar-over-ssh 部署(main 不常推 origin,Actions 从未实际运行;deploy job
也一直挂 if: false 等 secrets)。校验靠本地 `uv run pytest` 与
`pnpm --filter web typecheck/test/build`,部署按本文档手动执行。

## Package

Run from the monorepo root — studio now lives at `apps/studio/` inside
`creator-radar`:

```bash
# 2026-07-17 前端合并后:studio 页面由 apps/web(svm-web)承载,tarball 不再
# 打包 frontend/dist。服务器上现存的 /opt/mkt/frontend/dist 是前端合并的
# 回滚后路(观察期内别删,见 docs/DEPLOY.md E 节)。
RELEASE_COMMIT="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
printf '%s\n' "$RELEASE_COMMIT" > /tmp/creator-radar-release-commit.txt
COPYFILE_DISABLE=1 tar -czf "/tmp/mkt-deploy-${RELEASE_COMMIT}.tar.gz" \
  -C apps/studio \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini scripts geelark_client.py generate_caption.py generate_comment.py \
  brightdata_client.py pyproject.toml uv.lock \
  -C /tmp creator-radar-release-commit.txt
ls -lh "/tmp/mkt-deploy-${RELEASE_COMMIT}.tar.gz"
```

Do not include `data/`, `.env`, local virtualenvs, or worktree/cache folders.

> **`alembic/` matters:** without the migration files in the tarball, `uv run alembic upgrade head` on the server is a silent no-op (it has nothing new to apply) — services then start against a stale schema and crash. A 2026-05-29 deploy hit exactly this and required a follow-up package + re-extract; the column list above is the corrected one.

## Upload

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  "/tmp/mkt-deploy-${RELEASE_COMMIT}.tar.gz" root@139.196.52.92:/tmp/mkt-deploy.tar.gz
```

## Extract And Restart

> **Destructive schema migrations (drop_column, drop_table, alter_column rename, etc.) → run `pg_dump` BEFORE this step.** Otherwise data in the dropped/renamed column is irrecoverable. See the "DB backup before destructive migration" snippet below.

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/root/.local/bin:$PATH"
  cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  chown -R root:root app alembic alembic.ini geelark_client.py generate_caption.py generate_comment.py brightdata_client.py pyproject.toml uv.lock creator-radar-release-commit.txt
  # Re-sync Python deps if pyproject.toml or uv.lock changed
  uv sync --no-dev
  # Apply schema migrations BEFORE restarting services so the new code
  # never queries the old schema. No-op if there is nothing pending.
  uv run alembic upgrade head
  # Both mkt (web tier) and mkt-worker (Procrastinate worker) share app/,
  # so BOTH must restart to pick up the new code. The web tier alone is
  # not enough — the worker keeps running stale bytecode otherwise.
  systemctl restart mkt mkt-worker
  sleep 5
  systemctl is-active mkt mkt-worker
  rm -f /tmp/mkt-deploy.tar.gz
'
```

### DB backup before destructive migration

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  "docker exec svm-postgres pg_dump -U mkt -F c -d mkt \
     > /var/backups/mkt.\$(date +%Y%m%d-%H%M%S).pre-deploy.dump"
```

`pg_dump` is online-safe so the backup can run before any service stop. Keep at least the most recent successful dump until the next clean deploy.

> **First-time server setup:** `uv` must be installed on the server before the above will work.
> ```bash
> ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
>   'curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env'
> ```
> After install, `uv` lives at `/root/.local/bin/uv`. The Extract And Restart command above already exports this path inside its SSH session.

The service must report `active`.

## Production ingress

Three public entries, all ultimately reaching the same web tier on
`127.0.0.1:18080` (Uvicorn must not bind to `0.0.0.0` in production), but
only two of them still proxy directly — since the 2026-07 portal merge into
`creator-radar`, `mkt.igotyoufrank.cn` is a redirect, not a proxy:

- **`https://igotyoufrank.cn/studio/`** (primary, since 2026-07) — lives on
  creator-radar's own nginx (`/etc/nginx/sites-available/radar.conf` on the
  server, not in this repo), which strips the `/studio` prefix and proxies
  to `mkt_backend` in the same 443 `server` block as radar's own `/`.
  Template fragment: this repo's
  [`deploy/nginx-igotyoufrank-studio.conf`](../deploy/nginx-igotyoufrank-studio.conf)
  (a location block meant to be pasted into `radar.conf`, not a standalone
  server). Full current release/rollback runbook: `docs/DEPLOY.md` section H in the
  monorepo root. Cert is creator-radar's `igotyoufrank.cn` one.
- **`https://mkt.igotyoufrank.cn`** (since 2026-07-03; repurposed 2026-07) —
  nginx on 80/443, no longer proxies — now a pure 301 to
  `https://igotyoufrank.cn/studio$request_uri` (see
  [`deploy/nginx-mkt-domain.conf`](../deploy/nginx-mkt-domain.conf)). Kept
  only so old bookmarks/external links still resolve. Its Let's Encrypt cert
  `mkt.igotyoufrank.cn` (standalone cert-name; the `igotyoufrank.cn` cert
  belongs to creator-radar, which owns the apex and `www` on this same box —
  never `--expand` one into the other) is kept alive purely to serve that
  redirect over HTTPS, and keeps renewing automatically via `certbot.timer` +
  the reload-nginx deploy hook. DNS: A record `mkt` → `139.196.52.92` in
  Aliyun DNS (hichina NS).
- **`http://139.196.52.92:8080`** — historical GeeLark callback address.
  Since 2026-07-17 the registered callback URL is
  `https://igotyoufrank.cn/studio/api/geelark/callback?token=…` (switched via
  `scripts/register_geelark_callback.py --base-url https://igotyoufrank.cn/studio`);
  this raw-IP entry is kept temporarily as a fallback during the observation
  window. Still proxies directly (no path prefix) via
  [`deploy/nginx-mkt.conf`](../deploy/nginx-mkt.conf). **Before ever removing
  it**: that file also defines the shared `mkt_backend` upstream which the
  domain-side `/studio` locations in `radar.conf` depend on — move the
  upstream block elsewhere first, or `nginx -t` fails and the domain entry
  goes down with it. Browsers opening this URL 404 on static assets —
  expected, see "Canonical production target" above.

The authoritative ingress templates live in this repo:

- [`deploy/mkt.service`](../deploy/mkt.service)
- [`deploy/nginx-mkt.conf`](../deploy/nginx-mkt.conf) — raw-IP `:8080` entry;
  defines the shared `mkt_backend` upstream
- [`deploy/nginx-mkt-domain.conf`](../deploy/nginx-mkt-domain.conf) — domain
  entry (80 → 301 → 443 TLS); 443 no longer proxies to `mkt_backend`, it's a
  301 to `igotyoufrank.cn/studio`
- [`deploy/nginx-igotyoufrank-studio.conf`](../deploy/nginx-igotyoufrank-studio.conf) —
  `/studio` location fragment for creator-radar's `radar.conf` (not part of
  this app's own nginx sites); reuses `mkt_backend`, defined in
  `nginx-mkt.conf`, same nginx instance

Key invariants:

- Keep Uvicorn `--workers 1`; nginx does not change the single-process lock
  constraint.
- Keep nginx `proxy_buffering off` for `/api/videos/publish-stream` and
  `/api/groups/{group_id}/phones/create` in every entry that still proxies
  (`nginx-mkt.conf` and `nginx-igotyoufrank-studio.conf`), otherwise the
  dashboard log streams stop flushing in real time.
- Keep `client_max_body_size` large enough for video uploads in those same
  two entries.
- Keep `proxy_set_header X-Real-IP` in those same two entries — login/register
  rate limiting keys off it.

### Syncing ingress config to prod

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  deploy/mkt.service \
  root@139.196.52.92:/etc/systemd/system/mkt.service

scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  deploy/nginx-mkt.conf \
  root@139.196.52.92:/etc/nginx/sites-available/mkt-8080.conf

scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  deploy/nginx-mkt-domain.conf \
  root@139.196.52.92:/etc/nginx/sites-available/mkt-domain.conf

ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  apt-get update
  apt-get install -y nginx
  rm -f /etc/nginx/sites-enabled/default
  ln -sf /etc/nginx/sites-available/mkt-8080.conf /etc/nginx/sites-enabled/mkt-8080.conf
  ln -sf /etc/nginx/sites-available/mkt-domain.conf /etc/nginx/sites-enabled/mkt-domain.conf
  nginx -t
  systemctl daemon-reload
  systemctl restart mkt
  systemctl enable --now nginx
  systemctl restart nginx
  sleep 3
  systemctl is-active mkt nginx
'
```

## Historical: Multi-platform-publish migration (2026-05-28; do not replay)

This release adds a `publications` table and nine new `accounts`
columns; it DROPS six columns from `videos`. The Alembic migration
is destructive on `videos`. Back up the production DB first:

```bash
# PG runs in docker (svm-postgres). Host has no pg_dump binary —
# always shell into the container. pg_dump is online-safe so the
# backup can happen before the service stop, keeping downtime minimal.
docker exec svm-postgres pg_dump -U mkt -F c -d mkt \
  > /var/backups/mkt.$(date +%Y%m%d-%H%M%S).before-multi-platform.dump
sudo systemctl stop mkt mkt-worker
cd /opt/mkt
uv run alembic upgrade head
sudo systemctl start mkt mkt-worker
```

Rollback (TikTok subset only):

```bash
sudo systemctl stop mkt mkt-worker
cd /opt/mkt
uv run alembic downgrade -1
# downgrade preserves TikTok state; any YT/IG/FB publications written
# between upgrade and downgrade are lost.
sudo systemctl start mkt mkt-worker
```

Full restore from the dump:

```bash
pg_restore -U mkt -d mkt --clean --if-exists \
  /var/backups/mkt.<timestamp>.before-multi-platform.dump
```

## Smoke Test

Exercises the raw-IP entry directly (the GeeLark-callback path, unaffected
by the portal merge). For the primary `igotyoufrank.cn/studio/` path plus
the `mkt.igotyoufrank.cn` redirect, use the smoke checklist in `docs/DEPLOY.md`
section E in the monorepo root instead.

```bash
curl -sS -o /dev/null -w "/healthz -> %{http_code}\n" \
  http://139.196.52.92:8080/healthz
curl -sS -o /dev/null -w "/api/accounts -> %{http_code}\n" \
  http://139.196.52.92:8080/api/accounts
curl -sS -o /dev/null -w "/ -> %{http_code}\n" \
  http://139.196.52.92:8080/
```

Expected:

- `/healthz` -> `200`
- `/api/accounts` -> `401`
- `/` -> `404` (API-only; the UI is served by `apps/web`)

## mkt-worker.service (canonical unit)

The authoritative systemd unit for `mkt-worker` lives at
[`deploy/mkt-worker.service`](../deploy/mkt-worker.service) in this
repo. The prod copy at `/etc/systemd/system/mkt-worker.service` must
match it byte-for-byte. Drift = an undetectable risk surface: a deploy
that crashes mid-job because the prod unit silently lost
`--shutdown-graceful-timeout`.

Key invariants the unit guarantees:

- `KillSignal=SIGTERM` + `TimeoutStopSec=300` + procrastinate's
  `--shutdown-graceful-timeout=240` give the worker up to 4 minutes to
  finish in-flight jobs before systemd escalates to SIGKILL. Before
  this was added (2026-06-11), prod hit the default 90 s
  `TimeoutStopSec` and saw ~5 `status=9/KILL` events per day,
  hard-aborting whatever publish/warmup job was in flight.
- `--concurrency=16` paired with the PsycopgConnector pool sized
  `min_size=5, max_size=25` in `app/tasks/__init__.py` — see
  `~/.claude/projects/.../memory/procrastinate-pool-exhaustion.md` for
  the 2026-06-10 incident that forced those numbers.

### Syncing the unit to prod (run on every deploy that changes it)

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  deploy/mkt-worker.service \
  root@139.196.52.92:/etc/systemd/system/mkt-worker.service

ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  systemctl daemon-reload
  systemctl restart mkt-worker
  sleep 5
  systemctl is-active mkt-worker
'
```

If the unit file did NOT change this deploy, skip the scp/daemon-reload
— the normal Extract And Restart step already restarts `mkt-worker`
with the existing unit.

## Historical: Phone-lease migration (2026-06-09; do not replay)

The first deploy of this migration drops Procrastinate's `lock=phone:acc{id}` in
favour of a per-account DB lease. After upgrading the code:

1. Edit `/etc/systemd/system/mkt-worker.service` on the prod box. Remove this line:

   ```
   ExecStartPre=/opt/mkt/.venv/bin/python -c 'from app.tasks import reset_stale_doing_jobs; reset_stale_doing_jobs()'
   ```

2. `systemctl daemon-reload && systemctl restart mkt-worker`.

The lease layer makes the stale-doing cleanup unnecessary. The `reset_stale_doing_jobs`
function has been removed from `app/tasks/__init__.py`; leaving the `ExecStartPre`
line in place would error at worker startup, so it must be dropped in the same deploy.

The same deploy also requires a manual SQL cleanup of stranded `doing` jobs from
the old `warmup_account` queue (the ones that prompted this redesign). After the
migration is applied and the new code is live:

   ```sql
   UPDATE procrastinate_jobs
   SET status = 'cancelled'::procrastinate_job_status
   WHERE status = 'doing' AND task_name = 'warmup_account'
     AND scheduled_at < now() - interval '1 hour';
   ```

Expected to cancel ~12 jobs on first run.

## Historical: Multi-user auth cutover (2026-07-03; do not replay)

The single shared `DASHBOARD_PASSWORD` login is replaced by a `users` table
(migration `acb6c657deca`): username + password accounts, self-registration
gated by an invite code. Deploy order:

1. **Backup first** (standard pre-migration dump above; the CI/CD deploy job
   also auto-backs-up when `alembic/versions` changed).
2. Edit `/opt/mkt/.env` **before or with** the deploy (the tarball never
   touches `.env`):
   - add `REGISTER_CODE=<long random string>` (e.g.
     `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`)
   - delete the `DASHBOARD_PASSWORD=...` line (now unused)
3. Deploy normally (`alembic upgrade head` + restart **both** `mkt` and
   `mkt-worker` — they share `app/`).
4. Every operator opens the dashboard, clicks 注册, and creates their own
   account with the invite code. The `users` table starts empty on purpose —
   there is no data migration from the shared password, and every
   pre-cutover session cookie is rejected (the old `{"ok": true}` payload
   fails the new timed-serializer check). Being bounced to the login screen
   right after this deploy is expected, not an incident.
5. The invite code can be rotated later from Settings → 密钥 (it is one of
   the `.env`-backed secrets); rotating it does not affect existing accounts.

Notes: sessions now expire after 30 days (was ~10 years); changing a
password signs out that user's other sessions; login/register are
rate-limited per client IP (nginx must keep sending `X-Real-IP`, which
`deploy/nginx-mkt.conf` already does).

## Operational Notes

- Keep `--workers 1`. APScheduler runs in-process, and multiple worker
  processes would duplicate scheduler jobs.
- Production secrets live in `/opt/mkt/.env`; never overwrite it from a local
  deploy package.
- App data lives under `/opt/mkt/data`; never package or overwrite it.
- Before any risky deploy, create a PostgreSQL `pg_dump -Fc` of database
  `mkt`, verify that the dump is non-empty, and back up `/opt/mkt/data` plus
  the env/systemd/nginx configuration to company-controlled storage.
