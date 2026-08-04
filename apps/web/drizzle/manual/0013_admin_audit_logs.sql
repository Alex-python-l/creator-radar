-- Platform-administration audit trail. This table intentionally stores only
-- mutation summaries; never write passwords, session tokens, credentials,
-- secrets, or customer business data into before/after.

CREATE TABLE IF NOT EXISTS admin_audit_logs (
  id serial PRIMARY KEY,
  actor_user_id integer REFERENCES users(id) ON DELETE SET NULL,
  action text NOT NULL,
  target_type text NOT NULL,
  target_id text NOT NULL,
  before jsonb,
  after jsonb,
  reason text,
  actor_ip text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS admin_audit_logs_created_idx
  ON admin_audit_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS admin_audit_logs_actor_created_idx
  ON admin_audit_logs (actor_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS admin_audit_logs_target_created_idx
  ON admin_audit_logs (target_type, target_id, created_at DESC);
