CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE CASCADE,
  schema_version INTEGER NOT NULL,
  input_hash TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, run_attempt_id, schema_version, input_hash)
);

CREATE TABLE IF NOT EXISTS quality_gate_results (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE CASCADE,
  verdict TEXT NOT NULL,
  thresholds_json TEXT NOT NULL,
  observed_json TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baselines (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id),
  workflow_version_id TEXT NOT NULL REFERENCES workflow_versions(id),
  run_plan_hash TEXT NOT NULL,
  task_source_digest TEXT NOT NULL,
  target_revision_ids_json TEXT NOT NULL,
  lane_key TEXT NOT NULL,
  target_revision_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_run
  ON reports(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gate_results_run
  ON quality_gate_results(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_baselines_reproducibility
  ON baselines(project_id, task_source_digest, target_revision_id, lane_key, created_at DESC);
