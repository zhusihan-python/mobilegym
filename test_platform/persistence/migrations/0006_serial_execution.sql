CREATE TABLE IF NOT EXISTS prepared_tasks (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  materialization_key TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, materialization_key)
);

ALTER TABLE episodes ADD COLUMN prepared_task_id TEXT REFERENCES prepared_tasks(id);

CREATE TABLE IF NOT EXISTS episode_attempts (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  lane_attempt_id TEXT NOT NULL REFERENCES lane_attempts(id) ON DELETE CASCADE,
  attempt_no INTEGER NOT NULL,
  state TEXT NOT NULL,
  outcome TEXT,
  error_code TEXT,
  result_json TEXT,
  artifact_root TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(episode_id, lane_attempt_id, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_prepared_tasks_run
  ON prepared_tasks(run_id, materialization_key);
CREATE INDEX IF NOT EXISTS idx_episode_attempts_lane_outcome
  ON episode_attempts(lane_attempt_id, outcome);
