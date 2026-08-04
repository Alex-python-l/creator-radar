-- Repair the one-time legacy team bootstrap. The pre-workspace UI treated all
-- influencer rows as monitored, while is_monitored was never populated. The
-- original bootstrap filtered on that stale flag and linked zero accounts.

DO $$
DECLARE
  target_workspace_id integer;
BEGIN
  IF EXISTS (
    SELECT 1 FROM manual_migration_markers
    WHERE name = '0012_legacy_team_accounts'
  ) THEN
    RETURN;
  END IF;

  SELECT w.id INTO target_workspace_id
  FROM workspaces w
  JOIN organizations o ON o.id = w.organization_id
  WHERE w.kind = 'team' AND o.slug = 'legacy-team'
  LIMIT 1;

  IF target_workspace_id IS NULL THEN
    RAISE EXCEPTION 'legacy-team workspace not found';
  END IF;

  INSERT INTO workspace_influencers(
    workspace_id, influencer_id, group_id, alias, created_at
  )
  SELECT
    target_workspace_id,
    i.id,
    CASE WHEN g.workspace_id = target_workspace_id THEN i.group_id ELSE NULL END,
    i.alias,
    now()
  FROM influencers i
  LEFT JOIN account_groups g ON g.id = i.group_id
  ON CONFLICT (workspace_id, influencer_id) DO NOTHING;

  UPDATE influencers i
  SET is_monitored = true
  WHERE EXISTS (
    SELECT 1 FROM workspace_influencers wi
    WHERE wi.workspace_id = target_workspace_id
      AND wi.influencer_id = i.id
  );

  INSERT INTO manual_migration_markers(name)
  VALUES ('0012_legacy_team_accounts');
END $$;
