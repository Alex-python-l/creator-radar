-- 受控手工迁移:videos 加 (published_at) 部分索引。
--
-- 为什么:getGroupViewsTrend 在 chartRange='all' 时跑两次
-- MIN(published_at) WHERE published_at IS NOT NULL 子查询(buckets 生成
-- 与主 LEFT JOIN range 各一次)。现有 (influencer_id, published_at) 复合
-- 索引 leading column 不是 published_at,Postgres planner 选 Seq Scan
-- 19k+ 行(实测 20ms/次)。加单列部分索引后 IndexOnly Scan 1 row。
--
-- 同时主聚合按 date_trunc('week'/'day'/'month', published_at) 分桶,
-- 这里的 published_at 范围 + IS NOT NULL 过滤也吃这个索引。
--
-- 排除 NULL 的 27 行索引更小,几乎所有视频都有 published_at。
--
-- 应用方式(经 SSH 隧道到目标库):
--   psql "$DATABASE_URL" -f apps/web/drizzle/manual/0008_videos_published_at_idx.sql
-- 或直接到容器:
--   docker exec svm-postgres psql -U postgres -d short_video_monitor \
--     -f /tmp/0008_videos_published_at_idx.sql
--
-- IF NOT EXISTS 幂等可重复。CONCURRENTLY 不锁表(19k 行虽瞬秒,养成习惯)。

CREATE INDEX CONCURRENTLY IF NOT EXISTS videos_published_at_idx
  ON videos (published_at)
  WHERE published_at IS NOT NULL;
