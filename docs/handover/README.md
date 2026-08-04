# Creator Radar 交接入口

正式交付件：

- [Creator Radar 技术与运维交接手册（Word）](../../documents/交接/Creator-Radar-技术与运维交接手册.docx)

权威技术入口：

- [生产部署 Runbook](../DEPLOY.md)：完整发布顺序以 H 节为准。
- [Studio 生产部署补充](../../apps/studio/docs/production-deploy.md)：Studio API/Worker、Alembic、systemd 与 ingress。
- [Studio 代码说明](../../apps/studio/README.md)：Studio 后端边界、本地启动与任务模型。

Word 手册记录了 2026-07-29 的生产只读核验快照。磁盘、队列、证书、
服务状态、迁移版本和镜像 revision 都是“截至核验时”的动态数据，执行操作前必须重新检查。

本目录和 Git 历史不得保存真实密码、token、SSH 私钥、代理凭据、TOTP seed、
恢复码或付款信息。真实值仅通过公司密码库或当面安全渠道交付。
