-- Iris public pricing / trial applications. Apply after 0010_workspaces.sql.
-- Existing explicit user/team quotas are preserved; only defaults change.

ALTER TABLE users
  ALTER COLUMN monitor_quota SET DEFAULT 0;

ALTER TABLE organizations
  ALTER COLUMN monitor_quota SET DEFAULT 0;

CREATE TABLE IF NOT EXISTS trial_applications (
  id serial PRIMARY KEY,
  company_name text NOT NULL,
  contact_name text NOT NULL,
  work_email text,
  wechat text,
  expected_accounts integer NOT NULL,
  plan text NOT NULL,
  locale text NOT NULL DEFAULT 'zh',
  notes text,
  status text NOT NULL DEFAULT 'new',
  organization_id integer REFERENCES organizations(id) ON DELETE SET NULL,
  granted_quota integer,
  processed_by integer REFERENCES users(id) ON DELETE SET NULL,
  processed_at timestamptz,
  consent_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT trial_applications_contact_check
    CHECK (nullif(trim(work_email), '') IS NOT NULL OR nullif(trim(wechat), '') IS NOT NULL),
  CONSTRAINT trial_applications_expected_accounts_check
    CHECK (expected_accounts > 0 AND expected_accounts <= 100000),
  CONSTRAINT trial_applications_plan_check
    CHECK (plan IN ('trial', 'pro', 'scale', 'custom')),
  CONSTRAINT trial_applications_locale_check
    CHECK (locale IN ('zh', 'en')),
  CONSTRAINT trial_applications_status_check
    CHECK (status IN ('new', 'contacted', 'approved', 'rejected')),
  CONSTRAINT trial_applications_granted_quota_check
    CHECK (granted_quota IS NULL OR (granted_quota >= 0 AND granted_quota <= 100000))
);

CREATE INDEX IF NOT EXISTS trial_applications_status_created_idx
  ON trial_applications (status, created_at DESC);

CREATE INDEX IF NOT EXISTS trial_applications_email_idx
  ON trial_applications (lower(work_email));

CREATE INDEX IF NOT EXISTS trial_applications_organization_idx
  ON trial_applications (organization_id);
