-- 受控手工迁移：group_views_daily 物化日表，给趋势图 getGroupViewsTrend 用。
--
-- 为什么手工而非 drizzle-kit generate：apps/web/drizzle/ 无迁移基线（历史靠 push）。
-- 空基线下 generate 会撞已有数据。沿用 0001/0002 的幂等 IF NOT EXISTS 模式。
--
-- 维护：scraper 端 APScheduler 每小时整点跑一次全量重算（90 天 × N 组 × 4 窗口）,
-- UPSERT 到此表。最右一天是当天 partial（截至重算时点）,曲线观察趋势够用。
--
-- group_id = 0 sentinel 表示"ALL"（不分组的曲线）。account_groups 表 PK 从 1 开始,
-- 0 永远不会冲突。
--
-- 部署顺序：先跑此迁移 → 部署 scraper（cron 启动开始写数据）→ 部署 web（读新表）。
-- 第一次 cron 跑完前曲线为空,可手跑一次 rebuild 脚本兜底。
--
-- 应用方式（经 SSH 隧道到目标库）：
--   psql "$DATABASE_URL" -f apps/web/drizzle/manual/0003_group_views_daily.sql

CREATE TABLE IF NOT EXISTS group_views_daily (
  date         date    NOT NULL,
  group_id     integer NOT NULL,  -- 0 = ALL（不分组）,1+ 对应 account_groups.id
  window_days  integer NOT NULL,  -- 7 / 14 / 30 / 90
  total_play_count bigint NOT NULL DEFAULT 0,
  computed_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, group_id, window_days)
);

CREATE INDEX IF NOT EXISTS group_views_daily_lookup_idx
  ON group_views_daily (group_id, window_days, date DESC);
