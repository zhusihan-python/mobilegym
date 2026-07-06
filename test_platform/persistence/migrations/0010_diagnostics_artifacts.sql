CREATE TABLE IF NOT EXISTS diagnostics (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE CASCADE,
  lane_attempt_id TEXT REFERENCES lane_attempts(id) ON DELETE SET NULL,
  episode_attempt_id TEXT REFERENCES episode_attempts(id) ON DELETE SET NULL,
  comparison_id TEXT REFERENCES comparisons(id) ON DELETE SET NULL,
  comparison_pair_id TEXT REFERENCES comparison_pairs(id) ON DELETE SET NULL,
  gate_result_id TEXT REFERENCES quality_gate_results(id) ON DELETE SET NULL,
  entity_type TEXT NOT NULL,
  code TEXT NOT NULL,
  category TEXT NOT NULL,
  phase TEXT,
  severity TEXT NOT NULL,
  retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
  message TEXT NOT NULL,
  recommended_action TEXT,
  raw_json TEXT NOT NULL,
  artifact_refs_json TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  run_attempt_id TEXT REFERENCES run_attempts(id) ON DELETE SET NULL,
  lane_attempt_id TEXT REFERENCES lane_attempts(id) ON DELETE SET NULL,
  episode_attempt_id TEXT REFERENCES episode_attempts(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  media_type TEXT,
  size_bytes INTEGER,
  sha256 TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_diagnostics_run_code
  ON diagnostics(run_id, code);
CREATE INDEX IF NOT EXISTS idx_diagnostics_run_entity
  ON diagnostics(run_id, entity_type, category, severity);
CREATE INDEX IF NOT EXISTS idx_artifacts_episode_kind
  ON artifacts(episode_attempt_id, kind);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_kind
  ON artifacts(run_id, kind);
