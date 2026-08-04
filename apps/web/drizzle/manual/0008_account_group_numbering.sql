CREATE TABLE IF NOT EXISTS account_group_numbering (
  group_id integer PRIMARY KEY,
  enabled_at timestamptz NOT NULL DEFAULT now(),
  regenerated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT account_group_numbering_group_fk
    FOREIGN KEY (group_id) REFERENCES account_groups(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS account_group_numbers (
  group_id integer NOT NULL,
  influencer_id integer NOT NULL,
  display_no integer NOT NULL,
  sort_label text NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (group_id, influencer_id),
  CONSTRAINT account_group_numbers_group_fk
    FOREIGN KEY (group_id) REFERENCES account_group_numbering(group_id) ON DELETE CASCADE,
  CONSTRAINT account_group_numbers_influencer_fk
    FOREIGN KEY (influencer_id) REFERENCES influencers(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS account_group_numbers_group_display_no_uq
  ON account_group_numbers (group_id, display_no);

CREATE INDEX IF NOT EXISTS account_group_numbers_influencer_idx
  ON account_group_numbers (influencer_id);
