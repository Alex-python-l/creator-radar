-- Group names are workspace-scoped after the personal/team workspace migration.
-- 0009's partial owner/system indexes were intentionally kept during 0010 so
-- the legacy shared groups remained protected until bootstrap assigned them to
-- the legacy team workspace. They must now be removed: otherwise every new team
-- conflicts on its required "未分组" group (and cannot reuse any group name).

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM account_groups WHERE workspace_id IS NULL
  ) THEN
    RAISE EXCEPTION
      'account_groups still contains rows without workspace_id; finish workspace bootstrap first';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS account_groups_workspace_name_uq
  ON account_groups(workspace_id, name) WHERE workspace_id IS NOT NULL;

DROP INDEX IF EXISTS account_groups_system_name_uq;
DROP INDEX IF EXISTS account_groups_owner_name_uq;
