ALTER TABLE influencers
  ADD COLUMN IF NOT EXISTS alias text;

CREATE UNIQUE INDEX IF NOT EXISTS influencers_platform_alias_uq
  ON influencers (platform, alias)
  WHERE alias IS NOT NULL;
