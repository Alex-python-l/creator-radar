-- Own vs competitor monitoring tiers on workspace subscriptions.
-- Existing rows default to 'own' (zero migration). Competitor rows keep
-- group_id NULL: the competitor MVP has no grouping axis, which keeps group
-- counts / group deletion / account numbering naturally own-only.
-- No index: every relation filter runs after a workspace_id narrowing whose
-- row count is bounded by monitor_quota.

ALTER TABLE workspace_influencers
  ADD COLUMN IF NOT EXISTS relation text NOT NULL DEFAULT 'own';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'workspace_influencers_relation_check'
  ) THEN
    ALTER TABLE workspace_influencers
      ADD CONSTRAINT workspace_influencers_relation_check
      CHECK (relation IN ('own', 'competitor'));
  END IF;
END $$;
