ALTER TABLE videos
  ADD COLUMN IF NOT EXISTS content_type text NOT NULL DEFAULT 'video';
