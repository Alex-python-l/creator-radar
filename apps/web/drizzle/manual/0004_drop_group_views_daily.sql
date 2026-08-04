-- 受控手工迁移：删除 group_views_daily 物化日表。
--
-- 背景：0003 建表时设想给 getGroupViewsTrend 用,但实际接入时发现语义对不上 —
-- 物化表存的是"在 d 日,过去 N 天发布的视频总播放"(滚动窗口累计),
-- web 端 getGroupViewsTrend 要的是"在某个 bucket(day/week/month) 内发布的视频的
-- 总播放/数量/avg/viral"(分桶 + 4 个指标)。即使想接也接不上。
--
-- 物化表只有 day 粒度 / 90 天 / 1 个指标,前端要 day/week/month + 30/90/365/all + 4 个指标。
-- web 端代码零引用 group_views_daily,scraper 还在每小时白白重算。
--
-- 部署顺序:先部署 scraper(去掉 cron 注册和 handler) → 部署 web(无变化) →
-- 跑此迁移 DROP TABLE。顺序错了不会出事 — cron 删了表也只是 INSERT 失败。
--
-- 应用方式(经 SSH 隧道到目标库):
--   psql "$DATABASE_URL" -f apps/web/drizzle/manual/0004_drop_group_views_daily.sql

DROP INDEX IF EXISTS group_views_daily_lookup_idx;
DROP TABLE IF EXISTS group_views_daily;
