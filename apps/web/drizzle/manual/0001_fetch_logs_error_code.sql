-- 受控手工迁移：fetch_logs 加结构化错误码列。
--
-- 为什么手工而非 drizzle-kit generate：apps/web/drizzle/ 无迁移基线
-- （历史靠 push）。空基线下 generate 会生成含全部表的初始迁移，撞 CVM
-- 已有数据。故用幂等 ADD COLUMN IF NOT EXISTS，加性、安全、不碰数据、
-- 不 push --force。可重复执行。
--
-- 部署顺序：先在目标 Postgres 跑本文件，再部署 scraper（开始写 error_code）
-- 与 web（读 error_code 渲染），最后跑 scripts/backfill_error_code.py。
--
-- 应用方式（经 SSH 隧道到目标库）：
--   psql "$DATABASE_URL" -f apps/web/drizzle/manual/0001_fetch_logs_error_code.sql
-- 或用 scraper venv：见 scripts/backfill_error_code.py 同款 asyncpg 连法。

ALTER TABLE fetch_logs ADD COLUMN IF NOT EXISTS error_code text;
