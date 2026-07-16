ALTER TABLE execution_profiles
  ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 1;
