WITH ranked_active_names AS (
  SELECT
    id,
    ROW_NUMBER() OVER (
      PARTITION BY project_id, name_key
      ORDER BY created_at ASC, id ASC
    ) AS duplicate_rank
  FROM execution_profiles
  WHERE archived_at IS NULL
)
UPDATE execution_profiles
SET
  name = name || ' [migrated duplicate ' || id || ']',
  name_key = name_key || ' [migrated duplicate ' || id || ']'
WHERE id IN (
  SELECT id
  FROM ranked_active_names
  WHERE duplicate_rank > 1
);

CREATE UNIQUE INDEX idx_execution_profiles_active_name_key
  ON execution_profiles(project_id, name_key)
  WHERE archived_at IS NULL;
