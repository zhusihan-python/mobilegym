ALTER TABLE baselines ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE baselines ADD COLUMN name_key TEXT NOT NULL DEFAULT '';
ALTER TABLE baselines ADD COLUMN archived_at TEXT;

UPDATE baselines
SET display_name = 'Legacy baseline ' || id,
    name_key = lower('Legacy baseline ' || id)
WHERE display_name = '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_baselines_active_project_name
  ON baselines(project_id, name_key)
  WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_baselines_project_created
  ON baselines(project_id, created_at DESC, id DESC);
