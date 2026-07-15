ALTER TABLE lanes
  ADD COLUMN execution_profile_revision_id TEXT
  REFERENCES execution_profile_revisions(id);

ALTER TABLE lanes
  ADD COLUMN execution_profile_revision_hash TEXT;
