ALTER TABLE diagnostics ADD COLUMN source_event_id TEXT REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE diagnostics ADD COLUMN lane_id TEXT;
ALTER TABLE diagnostics ADD COLUMN episode_id TEXT;
ALTER TABLE diagnostics ADD COLUMN worker_id TEXT;
ALTER TABLE diagnostics ADD COLUMN step INTEGER;
ALTER TABLE diagnostics ADD COLUMN target_id TEXT;
ALTER TABLE diagnostics ADD COLUMN app_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE diagnostics ADD COLUMN task_id TEXT;
ALTER TABLE diagnostics ADD COLUMN lane_key TEXT;
ALTER TABLE diagnostics ADD COLUMN episode_key TEXT;
ALTER TABLE diagnostics ADD COLUMN run_attempt_no INTEGER;
ALTER TABLE diagnostics ADD COLUMN episode_attempt_no INTEGER;
ALTER TABLE diagnostics ADD COLUMN scope TEXT NOT NULL DEFAULT 'run';
ALTER TABLE diagnostics ADD COLUMN artifact_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE diagnostics ADD COLUMN pair_key TEXT;
ALTER TABLE diagnostics ADD COLUMN report_id TEXT;
ALTER TABLE diagnostics ADD COLUMN baseline_episode_attempt_id TEXT;
ALTER TABLE diagnostics ADD COLUMN candidate_episode_attempt_id TEXT;

CREATE INDEX IF NOT EXISTS idx_diagnostics_run_filters
  ON diagnostics(run_id, category, severity, retryable, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_diagnostics_identity
  ON diagnostics(run_id, target_id, task_id, lane_key, episode_key, run_attempt_no);
CREATE UNIQUE INDEX IF NOT EXISTS idx_diagnostics_source_event
  ON diagnostics(source_event_id)
  WHERE source_event_id IS NOT NULL;
