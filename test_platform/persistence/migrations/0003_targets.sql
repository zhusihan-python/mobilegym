CREATE TABLE IF NOT EXISTS targets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS target_revisions (
  id TEXT PRIMARY KEY,
  target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  metadata_json TEXT NOT NULL,
  metadata_hash TEXT NOT NULL,
  health_status TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  resolved_at TEXT NOT NULL,
  UNIQUE(target_id, metadata_hash)
);

CREATE INDEX IF NOT EXISTS idx_targets_project_id ON targets(project_id);
CREATE INDEX IF NOT EXISTS idx_target_revisions_target_id
  ON target_revisions(target_id, resolved_at DESC);
