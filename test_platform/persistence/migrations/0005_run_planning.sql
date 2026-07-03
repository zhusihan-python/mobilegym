ALTER TABLE runs ADD COLUMN workflow_version_id TEXT REFERENCES workflow_versions(id);
ALTER TABLE runs ADD COLUMN next_event_sequence INTEGER NOT NULL DEFAULT 1;
ALTER TABLE runs ADD COLUMN cancel_requested_at TEXT;

CREATE TABLE IF NOT EXISTS run_attempts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  attempt_no INTEGER NOT NULL,
  reason TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS workflow_node_runs (
  id TEXT PRIMARY KEY,
  run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE CASCADE,
  node_id TEXT NOT NULL,
  node_type TEXT NOT NULL,
  state TEXT NOT NULL,
  input_refs_json TEXT NOT NULL DEFAULT '{}',
  output_refs_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  UNIQUE(run_attempt_id, node_id)
);

CREATE TABLE IF NOT EXISTS lanes (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  lane_key TEXT NOT NULL,
  role TEXT NOT NULL,
  target_id TEXT NOT NULL REFERENCES targets(id),
  target_revision_id TEXT NOT NULL REFERENCES target_revisions(id),
  runner_config_json TEXT NOT NULL,
  reproducibility_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, lane_key)
);

CREATE TABLE IF NOT EXISTS lane_attempts (
  id TEXT PRIMARY KEY,
  lane_id TEXT NOT NULL REFERENCES lanes(id) ON DELETE CASCADE,
  run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE CASCADE,
  state TEXT NOT NULL,
  artifact_root TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  UNIQUE(lane_id, run_attempt_id)
);

CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  episode_key TEXT NOT NULL,
  materialization_key TEXT NOT NULL,
  pair_key TEXT NOT NULL,
  task_base_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  instance_id INTEGER NOT NULL,
  instance_seed INTEGER NOT NULL,
  template_index INTEGER,
  trial_id INTEGER NOT NULL,
  max_steps INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, episode_key)
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  type TEXT NOT NULL,
  entity_type TEXT,
  entity_id TEXT,
  occurred_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT NOT NULL,
  route TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_status INTEGER NOT NULL,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(key, route)
);

CREATE INDEX IF NOT EXISTS idx_runs_project_created
  ON runs(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_attempts_run
  ON run_attempts(run_id, attempt_no DESC);
CREATE INDEX IF NOT EXISTS idx_lanes_run
  ON lanes(run_id, lane_key);
CREATE INDEX IF NOT EXISTS idx_episodes_run
  ON episodes(run_id, episode_key);
CREATE INDEX IF NOT EXISTS idx_events_run_sequence
  ON events(run_id, sequence);
