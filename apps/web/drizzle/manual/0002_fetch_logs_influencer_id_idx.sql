-- 受控手工迁移：fetch_logs 加 (influencer_id, id DESC) 复合索引。
--
-- 为什么：listInfluencersWithRecentCount 重写为 LATERAL JOIN 后,需要按
-- (influencer_id, id DESC) 取"该账号最新一条 fetch_log"。当前 fetch_logs 只有
-- (started_at) 单列索引,LATERAL 子查询会走全表扫 by influencer_id, 在
-- 未分组(系统默认组,装多数账号)切换时 N × O(全表扫) 撑住整个慢查询。
-- 加这个索引让 LATERAL 走 index-only,每个 LATERAL ~0.1ms。
--
-- 同时也加 (influencer_id, started_at DESC) 不需要 —— 既有 started_at 索引
-- 主要服务 fetch_logs_started_idx 全局时间范围查询(rebuild stats 之类),
-- 不和"按账号取最新"重叠。
--
-- 应用方式（经 SSH 隧道到目标库）：
--   psql "$DATABASE_URL" -f apps/web/drizzle/manual/0002_fetch_logs_influencer_id_idx.sql
--
-- 注：CONCURRENTLY 不锁表,生产环境直接跑安全。IF NOT EXISTS 让脚本幂等可重跑。

CREATE INDEX CONCURRENTLY IF NOT EXISTS fetch_logs_influencer_id_idx
  ON fetch_logs (influencer_id, id DESC);
