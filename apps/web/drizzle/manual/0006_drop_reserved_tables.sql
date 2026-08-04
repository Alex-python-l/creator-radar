DROP TABLE IF EXISTS ai_reports;

-- `users` used to be an unused Auth.js placeholder with a text primary key.
-- The live table created by 0009 uses an integer primary key and must never be
-- removed when historical manual migrations are replayed.
DO $$
DECLARE
  users_id_type text;
BEGIN
  SELECT data_type INTO users_id_type
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'users'
    AND column_name = 'id';

  IF users_id_type IS DISTINCT FROM 'text' THEN
    RETURN;
  END IF;

  IF EXISTS (SELECT 1 FROM users) THEN
    RAISE EXCEPTION 'refusing to drop non-empty legacy users placeholder';
  END IF;

  DROP TABLE users;
END $$;
