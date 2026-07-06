CREATE TABLE IF NOT EXISTS run_attempt_episode_selection (
  id TEXT PRIMARY KEY,
  run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id) ON DELETE CASCADE,
  lane_id TEXT NOT NULL REFERENCES lanes(id) ON DELETE CASCADE,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_attempt_id, lane_id, episode_id)
);

CREATE INDEX IF NOT EXISTS idx_attempt_selection_attempt
  ON run_attempt_episode_selection(run_attempt_id, lane_id, episode_id);
