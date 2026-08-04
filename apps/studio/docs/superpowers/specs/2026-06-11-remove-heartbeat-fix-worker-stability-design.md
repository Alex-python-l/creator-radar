# 移除 healthchecks heartbeat,并修两个 worker 稳定性 bug

**日期**: 2026-06-11
**作者**: will + Claude
**状态**: 已批准,待写实施计划

## 背景与动机

healthchecks.io 的 dead-man's-switch 邮件在过去 24 小时内触发了 233 次 down(总 ping 288 次,80% 失败),实际系统并未真正宕机。诊断结论:

1. **生产 worker 内部事件循环周期性被卡 ~90 秒**——`app/tasks/auto_publish.py:auto_publish_scan_task` 是 `async def`,但函数体直接同步调用 `posting_service.check_initiated()` / `plan_assignments()`,二者内部串行做 GeeLark httpx + PG IO,**未用 `asyncio.to_thread` 隔离**。同一时刻多个不同 task(reclaim、check_publication_status、idle_phone_sweeper、heartbeat 等)的 `lasted` 字段一致且同一秒结束,是事件循环阻塞的明确指纹。
2. **mkt-worker 进程频繁被 systemd `SIGKILL` 强杀**——24 小时内 5 次 `Main process exited, code=killed, status=9/KILL` / `Failed with result 'timeout'`,因 systemd 默认 `TimeoutStopSec=90s` 不够 procrastinate 处理在跑 job。
3. **heartbeat 自身设计耦合**——同时依赖 worker 主事件循环、procrastinate 队列、PG 调度、出境网络。任何一环抖动都触发 down 邮件,导致告警语义混淆、告警疲劳。

经决策:**完全移除 healthchecks/heartbeat 监控**,顺手修上述两个独立的 worker 稳定性 bug。本次工作的产物是一个更瘦、更稳的 worker 进程,无替代监控方案。

## 取舍记录

| 选项 | 决定 |
|---|---|
| 替换为 process_alive + loop_alive 双 check | **否决**。用户选择完全删除。 |
| 只删 healthchecks,bug 后续再修 | **否决**。两个 bug 持续烧 GeeLark 时长费 + 每次部署有任务被强中断风险。 |
| 一起做 | **采纳**。三块改动互相独立、可分 commit。 |

风险已告知用户:删除后,worker / web / PG 静默宕机时无外部告警,只能通过业务结果异常或日志巡检发现。

## 目标

- worker 内部不再产生任何 `heartbeat_task` 相关代码、任务、日志。
- `auto_publish_scan_task` 主体在工作线程跑,事件循环不再被它阻塞。
- `mkt-worker.service` 在 `systemctl restart` / `stop` 时优雅退出,日志中不再出现 `status=9/KILL`。

## 非目标

- **不**新增任何替代监控方案(systemd timer、loop_alive 协程、业务层 health 等)。
- **不**重构其他可能也忘了 `asyncio.to_thread` 的任务——只修今天日志直接证实的 `auto_publish_scan`。其他可疑点(如 `pic_check`、`proxy_check`、各种 `reclaim_*`)未在卡顿窗口贡献阻塞,本次不动。
- **不**改 `/healthz` web 端点(它独立于 heartbeat,健康检查用途不同)。
- **不**修动 procrastinate 版本、PG 连接池配置、worker concurrency。

## 改动清单

### A. 删 healthchecks 全链路

| 路径 | 动作 | 备注 |
|---|---|---|
| `app/tasks/heartbeat.py` | 整文件删除 | |
| `app/tasks/__init__.py` | `import_paths` 列表移除 `"app.tasks.heartbeat"` | line 40 附近 |
| `app/config.py` | 删除 `HEALTHCHECK_URL = os.getenv(...)` 行 | line 44 |
| `.env.example` | 删 healthchecks 那一节(line 30-32) | |
| `tests/test_tasks_periodic.py` | 删 `import app.tasks.heartbeat` 与相关 cron / 注册断言;新增断言 `app.tasks.heartbeat` **不在** `import_paths` 里,作为防回归 | |
| `CLAUDE.md` | 删 `HEALTHCHECKS_PING_URL` 行(变量名是历史脏数据,顺手清掉) | line 146 |
| `AGENTS.md` | 同上 | line 146 |
| 生产 `/opt/mkt/.env` | 部署步骤中同步删 `HEALTHCHECK_URL=...` 行 | 不删也无害,但保持干净 |
| healthchecks.io 网站 | **用户手动** pause/delete 那个 check | 代码无法操作 |

历史 `docs/superpowers/specs/`、`docs/superpowers/plans/` 中的 heartbeat 引用 **保留不动**——那是历史快照。

### B. 修 `auto_publish_scan` 阻塞事件循环

文件:`app/tasks/auto_publish.py`。

**改造模式**:与 `app/tasks/idle_phone_sweeper.py` 同形。同步主体抽到 `_auto_publish_scan_sync()`,`async def` 顶层只保留 `to_thread` 调用与 `defer_async`。

**目标结构**:

```python
def _auto_publish_scan_sync() -> tuple[list[int], datetime | None]:
    """同步执行 check_initiated + plan_assignments,返回:
    - 要 defer 的 publication_id 列表
    - 若无可调度任务,下次扫描的就绪时间(否则 None)
    """
    from app.services.platforms import PLATFORMS
    from app.services import geelark_service, posting_service
    from app.repositories import settings as srepo

    pub_ids: list[int] = []
    cooldown_hours = 2.0
    with session_scope() as session:
        posting_service.check_initiated(
            session, geelark_service.make_client(),
        )
    for platform in PLATFORMS:
        with session_scope() as session:
            cooldown_hours = float(
                srepo.get_all(session).get("post_cooldown_hours") or 2
            )
            pub_ids.extend(posting_service.plan_assignments(
                session, platform=platform, cooldown_hours=cooldown_hours,
            ))
    next_ready: datetime | None = None
    if not pub_ids:
        with session_scope() as session:
            next_ready = posting_service.next_publish_scan_ready_at(
                session, cooldown_hours=cooldown_hours,
            )
    return pub_ids, next_ready


@procrastinate_app.periodic(cron="0 * * * *")
@procrastinate_app.task(queue="default", name="auto_publish_scan",
                         queueing_lock="auto_publish_scan")
async def auto_publish_scan_task(timestamp: int):
    pub_ids, next_ready = await asyncio.to_thread(_auto_publish_scan_sync)
    for pid in pub_ids:
        await publish_publication_task.defer_async(publication_id=pid)
    if not pub_ids and next_ready is not None:
        await _defer_auto_publish_scan_at(next_ready)
    return f"{len(pub_ids)} publication(s) deferred"
```

**契约保持不变**:
- 触发频率(cron `0 * * * *` + `wake_auto_publish_scan` 唤醒)不变。
- queue / queueing_lock / 任务名不变。
- 返回字符串仍是 `f"{N} publication(s) deferred"`。
- `_defer_auto_publish_scan_at(next_ready)` 必须留在 `async def` 顶层(它是 awaitable)。

### C. 修 worker 优雅关停

**仓库内**:新增 `deploy/mkt-worker.service` 作为权威模板(目前 `/etc/systemd/system/mkt-worker.service` 是生产唯一副本,无版本控制——这是历史负债,本次顺手补)。

模板内容(关键改动加注):

```ini
[Unit]
Description=mkt worker — procrastinate consumer
After=docker.service mkt.service
Requires=docker.service

[Service]
WorkingDirectory=/opt/mkt
EnvironmentFile=/opt/mkt/.env
Environment=PYTHONPATH=/opt/mkt
ExecStart=/opt/mkt/.venv/bin/python -m procrastinate \
    -a app.tasks.procrastinate_app worker \
    --queues phones,default \
    --concurrency=16 \
    --shutdown-graceful-timeout=240
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=300
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**新增项解释**:
- `--shutdown-graceful-timeout=240`:procrastinate 3.8.1 内置 flag。收到 SIGTERM 后停止拉新 job,最多等 240 秒让手头 job 完成。
- `TimeoutStopSec=300`:systemd 等够 5 分钟才升级到 SIGKILL,留 60 秒余量给 procrastinate 的 240 秒。
- `KillSignal=SIGTERM`:显式声明,避免后续 systemd 默认值变更踩坑。

**部署文档**:`docs/production-deploy.md` 新增"同步 systemd unit"步骤:
- `scp deploy/mkt-worker.service` 到 `/etc/systemd/system/mkt-worker.service`
- `systemctl daemon-reload`
- `systemctl restart mkt-worker`

## 数据流

- 旧:procrastinate 调度 → `heartbeat_task` 每 5 分钟跑 → httpx → healthchecks.io ping。
- 新:**这条路径不存在**。`mkt` / `mkt-worker` 进程存活由 systemd `Restart=always` 自管。无外部监控告警。

`auto_publish_scan_task` 的对外契约不变,只是其同步主体被搬到工作线程。

## 测试策略

### 单元/集成测试

| 测试文件 | 改动 |
|---|---|
| `tests/test_tasks_periodic.py` | 删 heartbeat 相关 import / cron 断言;**新增**:从 `app.tasks` 的 `import_paths` 读取,断言 `app.tasks.heartbeat` **不在**列表里。 |
| `tests/test_auto_publish_scan.py`(可能新建) | 新增至少 2 个用例:(a) 无 PENDING 时 `_auto_publish_scan_sync()` 返回 `([], next_ready_or_None)`;(b) 有 PENDING 时返回非空 `pub_ids`。用 mock client 避免真打 GeeLark。 |

`uv run pytest -q` 必须维持 ≥ 已有 baseline(目前 385+)。

### 生产验证(部署后)

1. `journalctl -u mkt-worker --since "10 min ago" | grep heartbeat_task` 应为 0 行。
2. `journalctl -u mkt-worker --since "1 hour ago" | grep -E "lasted 9[0-9]\."` 触发频率应显著下降(预期接近 0;若残留即提示尚有其他未发现的同类 bug,但不阻塞本次发布)。
3. 主动 `systemctl restart mkt-worker` 一次:
   - 日志中 **不再** 出现 `status=9/KILL` 或 `Failed with result 'timeout'`。
   - 日志中应看到 `Stopped worker on queues phones, default` 优雅退出标记。
4. `auto_publish_scan` 跑一次后,业务行为(publication 状态推进)正常。

## 回滚

三块改动各自独立 commit。任一块出问题:`git revert <sha>` + 重新打 tarball 部署即可。

- 删 healthchecks 的 commit 回滚:`app/tasks/heartbeat.py` 恢复,但生产 `.env` 里 `HEALTHCHECK_URL` 若已被部署步骤删除,需 `ssh ... 'echo HEALTHCHECK_URL=... >> /opt/mkt/.env'` 写回。
- `auto_publish_scan` 改造的 commit 回滚:对外契约未变,纯函数主体重构,回滚即旧行为。
- systemd unit 回滚:`scp` 老版本 unit 覆盖,`systemctl daemon-reload + restart`。

## 不在 scope 内的已知风险(记录,不修)

- 其他 `async def` task 中可能也有未发现的同步阻塞(已扫读 `idle_phone_sweeper`、`auto_publish`、`publish`、`heartbeat` 共 4 个;`engagement`、`discover_tiktok`、`sync_transactions`、`daily_warmup`、`backfill_thumbnails`、`pic_check`、`proxy_check`、`warmup` 未逐一审计)。本次只修日志直接证实阻塞的 `auto_publish_scan`;未来若再观察到类似 90 秒同步卡顿,按同样模式排查。
- `psycopg.pool: server closed the connection unexpectedly` 警告未根治。本次不在 scope。

## 实施顺序建议

1. 删 healthchecks 全链路(改动最大,但风险最低)。
2. 改造 `auto_publish_scan`(逻辑改动,需测试覆盖)。
3. 加 `deploy/mkt-worker.service` 模板 + 更新部署文档。
4. 一次性 deploy 三件套,按"生产验证"清单跑完。
