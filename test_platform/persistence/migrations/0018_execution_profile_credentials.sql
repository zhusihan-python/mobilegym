ALTER TABLE execution_profiles
  ADD COLUMN draft_credential_bindings_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE execution_profile_revision_credential_bindings (
  execution_profile_revision_id TEXT NOT NULL
    REFERENCES execution_profile_revisions(id) ON DELETE CASCADE,
  slot TEXT NOT NULL,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  backend TEXT NOT NULL,
  reference_id TEXT NOT NULL,
  private_locator TEXT NOT NULL,
  PRIMARY KEY (execution_profile_revision_id, slot)
);

CREATE INDEX idx_execution_profile_revision_credential_bindings_project
  ON execution_profile_revision_credential_bindings(project_id, reference_id);
