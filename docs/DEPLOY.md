# Creator Radar 生产部署 Runbook

> **当前操作入口：H 节。** A–G 记录历次迁移、切换和回滚背景，不是可重放的发布步骤。
> 新发布必须从备份门禁开始，依次执行 Radar SQL、Studio Alembic、Scraper、
> Studio、Web 与冒烟；动态状态以执行窗口的只读核验为准。

当前是个人/团队工作空间隔离的多用户系统，生产由两台服务器承载：

| 机器 | 角色 | 备注 |
|---|---|---|
| `139.196.52.92`（阿里云上海, Debian11, 2C~1.7G/40G） | **web + Postgres** | 2026-05-26 从腾讯云 `1.15.155.204` 整体迁来（PG `pg_dump -Fc` 流式 restore，web 重新 build）。Docker mirror 走 daocloud + 1ms.run（公网通用）|
| `107.174.1.82`（RackNerd 洛杉矶, Debian13, 1C~1G/19G） | **scraper** | 非专用机：还有用户的 metube/speedtest + 自带本地 PG(占5432)；磁盘紧 |

```
你(国内) ──http:3000──> 阿里云: svm-web ──host网络──> svm-postgres(仅localhost, 手动run, 勿用compose管)
scraper(美) ──autossh systemd 隧道 127.0.0.1:55432──> 阿里云 PG  ← 不公网暴露
scraper(美) ──同隧道反向 -R 18000:8000──> 阿里云 web 调 SCRAPER_BASE_URL=http://127.0.0.1:18000
scraper(美) ──(待住宅代理 PROXY_URL)──> IG/TikTok ；──直连──> YouTube
```

两台 SSH 已禁密码登录，仅 `~/.ssh/svm_server` key。

## A. 阿里云：web（PG 已存在，勿动）

PG 是手动 `docker run` 的 `svm-postgres`，**有数据、无 compose 标签**。schema 已全（含 jobs），**不要跑 drizzle push**。

> **Schema 变更（受控手工迁移，不跑 drizzle-kit）**：`apps/web/drizzle/` 无迁移基线，`generate` 会撞已有数据，故 schema 改动走 `apps/web/drizzle/manual/*.sql`（幂等 `ADD COLUMN IF NOT EXISTS` 等）。**顺序：先在阿里云 PG 跑迁移 SQL → 再部署 scraper（写新列）→ 再部署 web（读新列）→ 最后跑回填脚本**。漏了第一步会 `column ... does not exist` 且 scraper 写日志失败被吞。
> 例：`fetch_logs.error_code` 迁移 `apps/web/drizzle/manual/0001_fetch_logs_error_code.sql`，回填 `apps/scraper/scripts/backfill_error_code.py`（dry-run 默认）。

```bash
ssh -i ~/.ssh/svm_server root@139.196.52.92 'bash -s' < scripts/setup-swap.sh   # 4G，构建防 OOM
# 同步代码（CVM 无 rsync，用 tar over ssh）
# Mac 本地打包两个污染源,各管各的(实测两个都加才完全干净):
# - COPYFILE_DISABLE=1   防 BSD tar 写 ._<filename> AppleDouble 元数据文件
# - --no-xattrs          防 BSD tar 把 xattr 塞进 tar header(否则 Linux tar
#                        解压时刷一大堆 'Ignoring unknown extended header
#                        keyword LIBARCHIVE.xattr.com.apple.provenance' 噪音)
# 两个 Mac 专用,Linux 上设置无副作用。
COPYFILE_DISABLE=1 tar czf - --no-xattrs --exclude node_modules --exclude .next --exclude .git \
  --exclude ./apps/scraper package.json pnpm-*.yaml .dockerignore docker-compose.cvm.yml apps/web packages scripts \
  | ssh -i ~/.ssh/svm_server root@139.196.52.92 'tar xzf - -C /opt/svm'
# 真实 DB 密码写 /opt/svm/.env.cvm（DATABASE_URL=...@127.0.0.1:5432/...; NEXT_PUBLIC_APP_URL=https://igotyoufrank.cn）
# RELEASE_COMMIT 必须是已提交且工作区干净的待发布 commit；compose 会拒绝空值。
RELEASE_COMMIT="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
ssh ... "cd /opt/svm && export RELEASE_COMMIT=$RELEASE_COMMIT && docker compose -f docker-compose.cvm.yml build web && docker compose -f docker-compose.cvm.yml up -d web"
```

> Dockerfile 使用 frozen lockfile；依赖变更后先提交同步后的 `pnpm-lock.yaml`。

访问 `https://igotyoufrank.cn/`（`/` 即看板）。Next 必须绑 `0.0.0.0`——standalone 配置具体 hostname 后 redirect 的 Location 会无视 Host 头改用该值（`127.0.0.1`→`localhost:3000`，locale/登录跳转全坏，2026-07-17 实测踩坑）。3000 不暴露公网靠 iptables（`block-public-3000.service`，拒非 lo 入站）+安全组，公网入口只有 nginx 443。

## B. PG 对国外开口（autossh，端口 55432）+ scraper 反向暴露（18000）

国外机自带 PG 占了 5432，故隧道本地端口用 **55432**。同一条 autossh 同时反向把 scraper 8000 暴露到阿里云 18000（web 通过 `SCRAPER_BASE_URL=http://127.0.0.1:18000` 调）。

```bash
# 国外机生成 /root/.ssh/cvm_tunnel，公钥加到阿里云 /root/.ssh/authorized_keys
# systemd /etc/systemd/system/svm-tunnel.service:
#   autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
#     -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
#     -i /root/.ssh/cvm_tunnel \
#     -L 127.0.0.1:55432:127.0.0.1:5432 \
#     -R 127.0.0.1:18000:127.0.0.1:8000 \
#     root@139.196.52.92
systemctl enable --now svm-tunnel
```

> 换主机时（如本次 1.15.155.204 → 139.196.52.92）：先把美国机 `cvm_tunnel.pub` 加到新机 authorized_keys，再 `sed -i 's|root@<旧>|root@<新>|' /etc/systemd/system/svm-tunnel.service`，`daemon-reload` + `restart svm-tunnel`。

## C. 国外机：scraper

```bash
ssh -i ~/.ssh/svm_server root@107.174.1.82 'bash -s' < scripts/setup-swap.sh 2G
# COPYFILE_DISABLE=1 + --no-xattrs：见 A 节同款解释(两个都加才完全干净)
COPYFILE_DISABLE=1 tar czf - --no-xattrs --exclude .venv --exclude __pycache__ --exclude .env --exclude data -C apps/scraper . \
  | ssh -i ~/.ssh/svm_server root@107.174.1.82 'tar xzf - -C /opt/svm-scraper'
# /opt/svm-scraper/.env: DATABASE_URL=postgresql://postgres:<pw>@127.0.0.1:55432/short_video_monitor
#   INSTAGRAM_USERNAME/PASSWORD=<小号>；PROXY_URL=<住宅代理，待补>
RELEASE_COMMIT="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
ssh ... "cd /opt/svm-scraper && RELEASE_COMMIT=$RELEASE_COMMIT docker compose up -d --build"
```

磁盘紧：构建前 `docker builder prune -af`。勿删用户的 metube/speedtest 镜像。
IG session 让它在美国机经代理重新登录，别从 Mac 搬旧 session（IP 变会触发 challenge）。

## D. 域名接入：igotyoufrank.cn（直连 HTTPS，2026-06-29）

主力域名 `igotyoufrank.cn`（阿里云 DNS/hichina，**已 ICP 备案绑本机**）直连大陆源站，国内最快。

- **安全组**：入方向放行 **80、443**（TCP, 0.0.0.0/0）。该机历史上只开非标端口（8080/18081 等），80/443 是本次新开。
- **nginx**：`/etc/nginx/sites-available/radar.conf`（软链到 sites-enabled）。80 段保留 `/.well-known/acme-challenge/`（webroot `/var/www/letsencrypt`）、其余 `301` 跳 https；443 段 `proxy_pass http://127.0.0.1:3000`。
- **证书**：`certbot certonly --webroot -w /var/www/letsencrypt -d igotyoufrank.cn -d www.igotyoufrank.cn`。自动续期靠 `certbot.timer` + deploy hook `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`（续期后自动 reload nginx）。
- `NEXT_PUBLIC_APP_URL` 仅在 `apps/web/lib/env.ts` 定义、业务未实际使用，改它**无需 rebuild**。

**裸 IP 收口（2026-07-17）**：除 GeeLark 回调外，裸 IP 一律不可访问。

- **80/443**：`/etc/nginx/sites-available/00-default-deny.conf`（`default_server`，`return 444` 静默断开）。nginx 1.18 无 `ssl_reject_handshake`，443 用自签占位证书（`/etc/nginx/default-deny-selfsigned.{crt,key}`，10 年）完成握手后再断。certbot webroot 续期带域名 Host、命中各自 server，不受影响。
- **3000**：iptables 拒非回环入站（systemd `block-public-3000.service` 开机重放；Next 必须听 0.0.0.0，绑回环会把 redirect Location 打成 `localhost:3000`，见 A 节），公网不可达。
- **18081**：CF 隧道回源口（`mkt-cf-origin.conf`）早已随隧道拆除失去存在意义，实际是 `mkt_backend` 的重复公网暴露——已摘 `sites-enabled` 软链停用（`sites-available` 留档）。
- **`139.196.52.92:8080` 暂时保留**：曾是 GeeLark 回调注册地址；2026-07-17 回调已切到 `https://igotyoufrank.cn/studio/api/geelark/callback?token=…`（`apps/studio/scripts/register_geelark_callback.py --base-url https://igotyoufrank.cn/studio`，幂等可回滚），8080 留作观察期后路。**关闭前必须先把 `mkt-8080.conf` 里的 `upstream mkt_backend` 定义挪走**（radar.conf 的 /studio 段引用它，直接摘软链 `nginx -t` 会挂）。
- 改动前 nginx 配置备份：服务器 `/root/backups/nginx-20260717-baremetal/`。
- 待办（控制台，可选）：阿里云安全组仍放行 3000/18081，服务器侧已无监听，顺手可收掉这两条规则（**8080 别动**）。

**CF 隧道（已弃用）**：曾用 Cloudflare Tunnel 验证免备案访问（`radar.willtrade.studio`→隧道→`localhost:3000`），但国内为主、CF 免费版大陆无落地节点偏慢，故 2026-06-29 改直连并拆除——CF 隧道 `creator-radar` 已删、`cloudflared.service` 已 `stop`+`disable`（service 文件保留但 token 失效）。`radar.willtrade.studio` 现 530 失效；CF DNS 里可能残留一条指向死隧道的 orphan CNAME（无害，dash → willtrade.studio → DNS 删 `radar` 记录即可清）。
> 历史坑：原 mkt 隧道 `81e8d0f9` 早已在 CF 被删、cloudflared 空转重连；接管时换过 token，旧文件备份 `cloudflared.service.bak-old81e8d0f9`。mkt 的 nginx(8080/18081) 未动。

## E. GeeLark Studio 并入 /studio（门户统一，2026-07；前端合并 2026-07-17）

`geelark-studio` 已以 `git subtree` 并入本仓 `apps/studio`。**2026-07-17 前端彻底合并**：studio 的 Vite SPA 迁入 `apps/web`（`app/[locale]/(app)/studio/**`，统一侧栏/登录/语言，监控↔运营客户端秒切），studio 后端（systemd `mkt` + `mkt-worker`，`/opt/mkt`）退化为纯 API。部署步骤见 `apps/studio/docs/production-deploy.md`（tarball 不再打包 frontend/dist）。

- **URL 形态**：页面在 `/{locale}/studio/*`（如 `/zh/studio/accounts`），由 svm-web（Next :3000）承载；旧 `/studio/*` 无 locale 路径由 Next middleware 302 补 locale（query 保留），旧 `/studio/groups(/:id)` 由 next.config redirects 转 accounts。
- **nginx**：radar.conf 443 server 内的 `/studio` 段现在**只反代后端直通路径**：`/studio/api/`（含 2048m 上传参数）、`= /studio/healthz`、`/studio/static/` 三段 + 两个 SSE 免缓冲 location（publish-stream、phones/create），全部 rewrite 剥前缀 → `mkt_backend`（:18080）。页面路径落 `location /` 归 Next。权威模板 `apps/studio/deploy/nginx-igotyoufrank-studio.conf`（含回滚注释块）。**⚠ 此三段路径清单必须与 `apps/web/middleware.ts` matcher 的排除清单（`studio/api|studio/healthz|studio/static`）保持一致，改动两处同步。**
- **回滚**（前端合并）：`cp /root/backups/radar.conf.bak-20260717-pre-frontend-merge /etc/nginx/sites-available/radar.conf && nginx -t && systemctl reload nginx`——旧 SPA（`/opt/mkt/frontend/dist` + FastAPI 静态托管代码）在观察期内未删，分钟级恢复。radar 镜像备份 `svm-web:bak-20260717-premerge`。
- `mkt.igotyoufrank.cn` 全量 301 到 `igotyoufrank.cn/studio`（`nginx-mkt-domain.conf`）；裸 IP `:8080`（`nginx-mkt.conf`，定义 `mkt_backend` upstream）留作 GeeLark 回调后路，见 D 节。
- **退役清单（前端合并 Phase 4）已执行 ✅（2026-07-18）**：`apps/studio/frontend/` 已删 + `pnpm-workspace.yaml`/`knip.json` 条目已清 + lockfile 已重生；FastAPI `main.py` 的 SPA 托管段已删（`/static/thumbnails` 鉴权端点保留，未知 `/api` 路径由 FastAPI 原生 404/405）；pytest 的 `test_spa_*`、`test_frontend_brand_icon_assets.py` 已随退（留 `test_unknown_api_paths_never_get_a_200_html_shell` 钉行为）；`studio.yml` 已去 frontend job(后于 2026-07-18 随 CI 整体删除,见 production-deploy.md)。服务器 `/opt/mkt/frontend/dist` 留作死文件、随下次全量部署消失；**回滚兜底改为备份件**：`/root/backups/mkt-20260717-182515.pre-auth-flip.tar.gz`（含旧代码+dist）+ `radar.conf.bak-20260717-pre-frontend-merge`。仅剩手工项：CF dashboard 删 `geelark-studio` Pages 项目（无害可缓）。
- **radar env**：~~`STUDIO_AUTH_VERIFY_URL`~~ **← 2026-07-17 方向已翻转，以 F 节为准**（radar 自持 better-auth，studio 反过来调 radar）。
- **部署陷阱（2026-07-17 实测）**：tar over ssh 是覆盖式解压不删旧文件——`apps/web` 路由结构变动（增删路由组/页面）后直接解包会新旧并存、Next build 报 "two parallel pages resolve to the same path"。同步前先 `rm -rf /opt/svm/apps/web /opt/svm/packages /opt/svm/scripts` 再解包。

### 上线序列（历史：2026-07-17 前端合并）

> 本节保留前端合并事件的回滚背景，不是当前发版入口。当前发布必须执行 H 节。

一次性上线，选无人使用时段，按序执行：

1. **备份**（nginx conf、studio 旧代码、radar 旧镜像各留一份）：

   ```bash
   cp /etc/nginx/sites-available/radar.conf /etc/nginx/sites-available/radar.conf.bak
   # studio:存一份当前线上 /opt/mkt 的快照(注意不是 /tmp 里的部署包——那个部署完就 rm 了)
   ssh -i ~/.ssh/svm_server root@139.196.52.92 '
     mkdir -p /root/backups
     tar -C /opt/mkt -czf /root/backups/mkt-$(date +%Y%m%d-%H%M%S).pre-studio-merge.tar.gz \
       app alembic alembic.ini geelark_client.py generate_caption.py generate_comment.py \
       brightdata_client.py pyproject.toml uv.lock frontend/dist
   '
   # radar:旧镜像存一份 tag
   docker commit svm-web svm-web:bak-$(date +%Y%m%d)
   ```

   若这次 studio 上线含破坏性 schema 迁移，额外按 `production-deploy.md` 的 “DB backup before destructive migration” 先转储一份 `pg_dump`。

2. **部署新 studio** 到 `/opt/mkt`（打包/scp/解包/`alembic upgrade head`/重启 `mkt` + `mkt-worker`；完整命令见 `apps/studio/docs/production-deploy.md` 的 Package / Upload / Extract And Restart 三节，tarball 现从 monorepo 根打包）。

3. **放 nginx**：把 `nginx-igotyoufrank-studio.conf` 的 location 段贴进服务器 `radar.conf` 的 443 server；用新版 `nginx-mkt-domain.conf` 覆盖服务器上的 `/etc/nginx/sites-available/mkt-domain.conf`。改完：

   ```bash
   nginx -t && systemctl reload nginx
   ```

   Step 2 与本步之间存在分钟级“studio 已是新版但 nginx 还没切”的窗口，浏览器直开旧路径可能短暂 404，可接受。

4. **部署新 radar**：这是历史切换步骤；当前环境不要恢复已退役的
   `STUDIO_AUTH_VERIFY_URL`。重新 build + 重启 `svm-web`：

   ```bash
   ssh -i ~/.ssh/svm_server root@139.196.52.92 \
     'cd /opt/svm && docker compose -f docker-compose.cvm.yml build web && docker compose -f docker-compose.cvm.yml up -d web'
   ```

5. **冒烟五连**（见下）。

6. **回滚**（三路相互独立，可单独回退）：

   ```bash
   # nginx
   cp /etc/nginx/sites-available/radar.conf.bak /etc/nginx/sites-available/radar.conf
   nginx -t && systemctl reload nginx

   # studio:用第 1 步的快照覆盖回去,重启(不动 DB;若上线含迁移且已跑,按需另行 alembic downgrade)
   ssh -i ~/.ssh/svm_server root@139.196.52.92 \
     'cd /opt/mkt && tar xzf /root/backups/mkt-<第1步时间戳>.pre-studio-merge.tar.gz && systemctl restart mkt mkt-worker'

   # radar:旧镜像重启 svm-web(参数对齐 docker-compose.cvm.yml 的 web service:host 网络 + .env.cvm)
   docker stop svm-web && docker rm svm-web
   docker run -d --name svm-web --restart unless-stopped --network host \
     --env-file /opt/svm/.env.cvm \
     -e NODE_ENV=production -e PORT=3000 -e HOSTNAME=0.0.0.0 -e LOG_LEVEL=info \
     svm-web:bak-<第1步日期>
   ```

### 冒烟五连（2026-07-17 前端合并后版）

```bash
curl -s -o /dev/null -w 'radar %{http_code}\n' https://igotyoufrank.cn/zh/login  # 登录页 200
curl -fsS https://igotyoufrank.cn/studio/healthz                                # 经 nginx strip → studio 200
curl -fsS http://139.196.52.92:8080/healthz                                     # GeeLark 回调通路 200
curl -s -o /dev/null -w 'studio api %{http_code}\n' https://igotyoufrank.cn/studio/api/accounts   # 401 未登录
curl -s -o /dev/null -w 'studio 页 %{http_code} -> %{redirect_url}\n' https://igotyoufrank.cn/studio/videos
#   ^ 307 → /login?next=/studio/videos(未登录);登录后旧路径 302 补 locale 到 /zh/studio/videos
# 浏览器:登录 → 侧栏「监控|运营」秒切(无整页刷新) → studio 9 页走查(重点:wizard 建手机
# SSE 日志逐行滚动、视频上传进度、costs CSV 下载、缩略图/截图) → 语言切换保持深层路径 → 登出重登
```

## F. 多用户面向公众（2026-07-17，认证中心翻转到 radar）——已执行 ✅

**2026-07-17 已按本节序列上线**：0009 迁移已跑（注意：生产库曾有一张空的历史 `users` 表(text id, NextAuth 风格)挡道，确认 0 行无外键后 DROP 再跑通）；mkt.users 10 人已迁入 radar（全员 admin，免改密）；radar env 已补 BETTER_AUTH_*（`REGISTER_MODE=invite`）；studio 已部署翻转版并配 `RADAR_AUTH_VERIFY_URL`。**全员需重登一次**（旧 itsdangerous cookie 失效）。备份：`/root/backups/{svm,mkt-db}-20260717-182515.pre-multiuser.dump` + `mkt-20260717-182515.pre-auth-flip.tar.gz` + `.env.cvm.bak-20260717`。

设计权威文档 `docs/superpowers/specs/2026-07-17-multi-user-public-design.md`。要点：radar 自持 better-auth（注册/登录/DB session/角色/配额），studio 变 admin-only 并**反向**转发校验（E 节的 `STUDIO_AUTH_VERIFY_URL` 方向作废）；数据订阅制隔离（admin=全局视图，user=订阅视图）；scraper 与 GeeLark `:8080` 回调**零改动**。

### env 变化

`/opt/svm/.env.cvm`（radar）：

```bash
# 新增
BETTER_AUTH_SECRET=<openssl rand -base64 32>
BETTER_AUTH_URL=https://igotyoufrank.cn        # https → secure cookie 自动开
REGISTER_MODE=invite                            # open|invite|closed,开放节奏自己切
REGISTER_CODE=<邀请码,invite 模式用>
DEFAULT_MONITOR_QUOTA=20                        # 普通用户监控上限默认值
# 删除
STUDIO_AUTH_VERIFY_URL=...                      # 方向已翻转,radar 不再调 studio
```

`/opt/mkt/.env`（studio）：

```bash
# 新增(同机内网调 radar;radar 容器只绑 127.0.0.1:3000)
RADAR_AUTH_VERIFY_URL=http://127.0.0.1:3000/api/auth/get-session
# 退役(可删):REGISTER_CODE、DASHBOARD_SECRET_KEY
```

### 上线序列（一次窗口，顺序敏感）

1. **备份**：nginx 不动本次可跳过；PG 转储一份 `pg_dump -Fc` 两个库（svm + mkt）。
2. **迁移 SQL**（先于两个部署）：对 `short_video_monitor` 执行
   `apps/web/drizzle/manual/0009_multi_user.sql`（幂等；建 better-auth 四表 +
   `user_influencers` + `account_groups.owner_id`）。
3. **迁移用户**：`node apps/web/scripts/migrate-users-to-radar.mjs --source <mkt url> --target <svm url>` 先 dry-run 核对，再加 `--apply`。旧 scrypt 哈希原样搬，**迁移账号免改密**，全员 role=admin。
4. **部署 radar**（env 见上，rebuild + 重启 `svm-web`）。
5. **部署 studio**（env 见上，重启 `mkt`；`mkt-worker` 不涉及）。
6. **全员重登一次**（旧 itsdangerous cookie 自然失效）。
7. 想真正开放注册时：`REGISTER_MODE=open` + 重启 web，无需发版。

回滚：radar/studio 各自回退镜像/代码 + 还原 env 即可；0009 的新表/新列对旧版代码无影响（旧版不读它们），无需回滚 DB。

### 冒烟

```bash
curl -s -o /dev/null -w 'radar %{http_code}\n' https://igotyoufrank.cn/zh/login        # 200 登录页
curl -s -o /dev/null -w 'get-session %{http_code}\n' https://igotyoufrank.cn/api/auth/get-session  # 200(body null)
curl -s -o /dev/null -w 'studio 401 %{http_code}\n' https://igotyoufrank.cn/studio/api/accounts    # 401 未登录
curl -fsS http://139.196.52.92:8080/healthz                                            # GeeLark 回调通路 200
# 浏览器:注册(邀请码) → user 只见自己订阅;admin 登录 → 全局视图 + 可进 /studio;
# user 开 /studio → "仅限管理员";改密后其他端会话失效
```

## G. 个人与团队工作空间（2026-07-18）

本节取代 F 节的 invite/admin 全局数据模型。上线必须使用维护窗口，并严格按
“备份 → 两库迁移 → 归属回填 → 部署 → 冒烟”执行。

### 新环境变量

Radar `/opt/svm/.env.cvm`：

```bash
REGISTER_MODE=open                         # open|closed；不再有公共邀请码
RESEND_API_KEY=re_...
RESEND_FROM=Iris <auth@notify.example.com>
```

生产使用已验证的 `notify.igotyoufrank.cn` 发信子域，发件地址为
`Iris <auth@notify.igotyoufrank.cn>`。除 Resend 要求的 SPF/DKIM 外，DNS 必须保留：

```text
TXT  _dmarc.notify  v=DMARC1; p=none;
```

验证码属于敏感事务邮件，Resend Domain → Configuration 的 Open/Click Tracking
保持关闭，避免追踪像素或重写链接影响投递。

Studio `/opt/mkt/.env`：

```bash
RADAR_AUTH_VERIFY_URL=http://127.0.0.1:3000/api/workspace/session
WORKSPACE_SECRETS_KEY=<base64 编码的 32 字节随机密钥>
STUDIO_LEGACY_WORKSPACE_ID=<下方 bootstrap 脚本输出的 team workspace id>
```

`WORKSPACE_SECRETS_KEY` 生成命令：

```bash
python -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
```

该密钥必须单独备份。已有密文时缺失、格式错误或不匹配都会让 Studio 启动失败；
不要在完成凭据迁移前删除旧 `.env` 中的 GeeLark/Gemini 值。

### 维护窗口上线顺序

1. 停止 web、studio、worker；备份 Radar DB、Studio DB，以及
   `/opt/mkt/data`（uploads、thumbnails、screenshots）。
2. Radar 执行 `apps/web/drizzle/manual/0010_workspaces.sql`。它会创建所有
   个人 workspace、撤销旧 session，并且只在第一次执行时把存量账号设为待验证。
3. 先 dry-run，再创建存量团队并回填共享 Radar 数据：

   ```bash
   node apps/web/scripts/bootstrap-legacy-team.mjs \
     --db "$DATABASE_URL" --leader-email leader@example.com --team-name '运营团队'
   node apps/web/scripts/bootstrap-legacy-team.mjs \
     --db "$DATABASE_URL" --leader-email leader@example.com --team-name '运营团队' --apply
   ```

   记录输出的 team `workspace.id`。脚本校验恰好一个 leader，并输出成员、分组、
   订阅计数；存量团队额度设为 `2147483647`（实际不限额）。
4. Studio 设置同一个 `STUDIO_LEGACY_WORKSPACE_ID` 后执行
   `uv run alembic upgrade head`。迁移为租户表增加 `workspace_id`、唯一键和
   FORCE RLS，并创建 `workspace_secrets`。
5. 设置 `WORKSPACE_SECRETS_KEY`，先 dry-run、再迁移旧凭据和文件：

   ```bash
   PYTHONPATH=/opt/mkt uv run python scripts/migrate_workspace_data.py --workspace-id <ID>
   PYTHONPATH=/opt/mkt uv run python scripts/migrate_workspace_data.py --workspace-id <ID> --apply
   ```

   核对输出的文件计数及两个 encrypted 状态后，才从 Studio `.env` 删除
   `GEELARK_TOKEN`、`GEMINI_API_KEY`。
6. 部署并启动 Radar、Studio、worker。所有存量用户需通过真实邮箱验证后重登。

### 隔离冒烟

- A、B 各自在个人空间创建数据，互相猜测列表、详情、导出、截图 URL 均为 404/空。
- A、B 加入同一团队后能看到相同 Radar/Studio 数据；任一方切回个人空间后立即隔离，
  不存在旧的 30 秒 Studio 鉴权缓存窗口。
- member 可使用运营能力，但 `/api/settings/secrets` 返回 403；leader/admin 可看掩码并更新。
- 跨 workspace 的 publication/task/account ID 在 Studio 返回 404；回调按 GeeLark task
  反查并只更新原 workspace。

## H. 当前全量发布流程（核验于 2026-07-29）

本节是当前发布入口，取代 E/F 的历史切换步骤，并补齐 G 之后的 Radar
`0011–0015`。生产禁止 `drizzle push`；所有命令先在隔离库验证，再在维护窗口执行。

### 发布身份与备份门禁

```bash
RELEASE_COMMIT="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
git show --no-patch --format='%H %cI %s' "$RELEASE_COMMIT"
```

Web 与 scraper 的 compose build 必须显式传
`RELEASE_COMMIT=$RELEASE_COMMIT`；镜像会写入
`org.opencontainers.image.revision`。Studio 包名和包内
`release-commit.txt` 也必须记录同一值。任一产物无法回指该 commit，不发布。

维护窗口开始前，在线执行两个数据库的 `pg_dump -Fc`，并备份
`/opt/mkt/data`、两套 env、nginx 配置及当前 Web 镜像。校验备份文件非空并记录
时间、大小和校验和；没有发布窗口内的新备份，不执行迁移或重启。

### 迁移和部署顺序

1. Radar DB 依次执行并核验：
   `0011_pricing_applications.sql`、
   `0012_legacy_team_accounts.sql`、
   `0013_admin_audit_logs.sql`、
   `0014_workspace_influencer_relation.sql`、
   `0015_account_group_workspace_uniqueness.sql`。
   `0012` 依赖 `legacy-team` 工作空间；执行前确认恰好一个目标，执行后确认
   `manual_migration_markers` 恰好一条。五个脚本均再次执行一次验证幂等。
   `0015` 会在仍有 `workspace_id IS NULL` 的旧分组时拒绝执行；必须先完成
   G 节 workspace bootstrap，不能绕过检查直接删旧唯一索引。
2. Studio 执行 `uv run alembic heads`，必须只有
   `7b2d9f4a1c63`；再执行 `uv run alembic upgrade head`，核对
   `alembic_version`。
3. 部署 scraper（新字段写入方），确认容器 health、隧道和四个平台 lane。
4. 部署 Studio API，再同时重启 `mkt` 与 `mkt-worker`；确认 API health、
   worker heartbeat、callback inbox/outbox 无积压。
5. 最后部署 Web；确认镜像 revision 等于 `$RELEASE_COMMIT`，再执行域名、
   登录、邀请、个人额度 `0`、团队额度和租户隔离冒烟。

### RPA Watchdog（启用独立评论 RPA 时）

`apps/rpa-watchdog` 是单独的进程内状态服务，不并入 `mkt` 或
`mkt-worker`。只有 RPA 已配置向该服务发送启动/用户名心跳时才部署；未启用这条
RPA 流程时，不要把它加入基础发布的强制健康门禁。

从仓库根目录打包，产物必须与本次其他服务使用同一个提交：

```bash
RELEASE_COMMIT="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
printf '%s\n' "$RELEASE_COMMIT" > /tmp/creator-radar-release-commit.txt
COPYFILE_DISABLE=1 tar -czf "/tmp/rpa-watchdog-${RELEASE_COMMIT}.tar.gz" \
  -C apps/rpa-watchdog \
  main.py config.py error_screenshot.py video_blacklist.py \
  pyproject.toml uv.lock README.md .env.example deploy \
  -C /tmp creator-radar-release-commit.txt
```

目标机目录固定为 `/opt/geelark-watchdog`。首次部署先创建目录和生产 `.env`；
后续包不含 `.env`，不得覆盖已有凭据：

新建或升级生产 `.env` 时确认回环阈值为：

```text
RECENT_USER_HIT_THRESHOLD=3
```

旧部署若仍显式配置为 `2`，必须在重启前改为 `3`；否则环境变量会覆盖本版代码默认值。

```bash
install -d -m 0750 /opt/geelark-watchdog
tar -xzf /tmp/rpa-watchdog-<commit>.tar.gz -C /opt/geelark-watchdog
cd /opt/geelark-watchdog
uv sync --no-dev
cp deploy/geelark-watchdog.service /etc/systemd/system/geelark-watchdog.service
systemctl daemon-reload
systemctl enable --now geelark-watchdog
curl --fail http://127.0.0.1:8002/health
```

同时在 `/opt/mkt/.env` 配置 Studio 的服务端代理，并重启 `mkt`：

```text
RPA_WATCHDOG_BASE_URL=http://127.0.0.1:8002
RPA_WATCHDOG_TIMEOUT_SECONDS=8
```

不配置额外 token 或 workspace ID。Studio 为每个请求读取当前 workspace 已加密保存
的 `GEELARK_TOKEN`，watchdog 用该 token 调 GeeLark 并隔离任务列表。因此各工作空间
各看各的、各跑各的；没有配置 GeeLark token 的 workspace 在 Ops 中返回 503。
配置后执行 `systemctl restart mkt`；worker 不调用 watchdog，无需单独重启。

升级时先确认没有需要保留的活跃任务，再 `systemctl restart geelark-watchdog`。
服务必须保持 `--workers 1` 且只监听 `127.0.0.1`。如需公网心跳，由 nginx
仅转发 RPA 必需路径；禁止直接开放 8002。冒烟至少核对：

- `/health` 的 `tokenMode=per-request-geelark-token`；
- 启动心跳后任务为 `running`，且 `firstHeartbeatReceived=true`；
- 测试任务连续命中用户名缓存时进入 `stopping`；
- 日志确认清理顺序为截图、cancel、等待 3/4/7、stop phone；
- `rpa_error_screenshots/`、`ignored_video_links.jsonl` 和日志目录有持久化空间。

回滚代码可重新解压上一提交的包并重启；任务状态在内存中，任何重启都会丢失，
因此回滚前也必须先处置活跃 RPA，不能把进程重启当成无状态操作。

### 当前配置与冒烟

- Radar：`REGISTER_MODE=open`、`DEFAULT_MONITOR_QUOTA=0`，
  `BETTER_AUTH_URL=https://igotyoufrank.cn`，邮件配置完整。
- Studio：`RADAR_AUTH_VERIFY_URL` 指向 `/api/workspace/session`，
  `WORKSPACE_SECRETS_KEY` 与 `STUDIO_LEGACY_WORKSPACE_ID` 均存在；至少一个
  团队工作空间已配置所需 GeeLark/Gemini/Bright Data 密钥。
- Scraper：内部 token 两端一致；代理与平台凭据存在。发布前最近一次计划抓取不能
  出现整个平台全失败。
- HTTP：80→HTTPS；域名 443 证书有效；裸 IP 80/443 收口；3000 公网拒绝；
  `/studio/healthz`=200、未登录 `/studio/api/accounts`=401。
- 响应至少包含 HSTS、`X-Content-Type-Options`、`X-Frame-Options`、
  `Referrer-Policy` 和 `Permissions-Policy`。
  2026-07-29 对最终 `/zh` 的只读核验未观察到这些头，当前属于待修复门禁，
  不能把本条当作已通过。

### 回滚触发条件

出现认证循环、跨租户可见、任务持续积压、任一核心服务无法恢复、迁移约束错误、
Studio 回调/worker 停摆或四个平台中任一计划抓取整批失败，立即停止放量。优先回滚
对应镜像/代码和 env；数据库只有在新代码已经写入不兼容数据或迁移本身损坏数据时，
才使用本窗口备份恢复。回滚后重复只读 health、会话、额度、隔离和队列检查。

## 待办

- **住宅代理**：填 `/opt/svm-scraper/.env` 的 `PROXY_URL` 后 `docker compose restart`。未配前 IG/TikTok 大概率被风控。
- **内部 API 保护**：web 与 scraper 已支持 `SCRAPER_INTERNAL_TOKEN`；生产必须在
  两端配置同一个非空值，不能依赖“未配置即放行”。
- **备份**：给阿里云 PG 加自动备份，并做一次恢复演练记录。
- **磁盘**：2026-07-29 阿里云根盘约 83%，先确认 `/opt`、Docker、日志和备份归属，
  再清理可重建内容并建立阈值告警；禁止触碰 PostgreSQL volume。
- **版本追溯**：Web 镜像尚未记录 revision，Studio 与 Scraper 已有 commit 线索；
  下次发布必须让三套产物都回指同一 `RELEASE_COMMIT`。
- **凭据轮换**：建议轮换两台 root 密码（曾明文传输），并确认 SSH 仍保持 key-only。
- **代理分层**：IG 用 sticky 单出口；TikTok/Facebook/YouTube 优先用平台代理池或 `COMMON_PROXY_POOL`，不要频繁换 IG 出口 IP。
