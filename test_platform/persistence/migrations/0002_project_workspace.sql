ALTER TABLE projects ADD COLUMN name_key TEXT;

UPDATE projects
SET name_key = lower(trim(name))
WHERE name_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_active_name_key
  ON projects(name_key)
  WHERE archived_at IS NULL;
