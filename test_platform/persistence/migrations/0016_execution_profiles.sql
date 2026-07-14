CREATE TABLE IF NOT EXISTS execution_profiles (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL,
  draft_spec_json TEXT NOT NULL,
  head_revision_id TEXT REFERENCES execution_profile_revisions(id),
  archived_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_profiles_project_id
  ON execution_profiles(project_id, created_at ASC);

CREATE TABLE IF NOT EXISTS execution_profile_revisions (
  id TEXT PRIMARY KEY,
  execution_profile_id TEXT NOT NULL
    REFERENCES execution_profiles(id) ON DELETE CASCADE,
  revision_no INTEGER NOT NULL,
  public_spec_json TEXT NOT NULL,
  public_spec_hash TEXT NOT NULL,
  credential_binding_digest TEXT NOT NULL,
  published_at TEXT NOT NULL,
  UNIQUE(execution_profile_id, revision_no)
);

CREATE INDEX IF NOT EXISTS idx_execution_profile_revisions_profile_id
  ON execution_profile_revisions(execution_profile_id, revision_no DESC);
