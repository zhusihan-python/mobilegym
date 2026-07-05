-- VS-09: Prepared baseline/candidate serial comparison.
--
-- A comparison belongs to one run_attempt and records the baseline/candidate
-- lane ids plus a policy and aggregate summary. Each comparison_pair joins the
-- baseline and candidate episode_attempts for one pair_key, carrying the
-- integrity check result (integrity_json) and the functional delta (delta_json).
--
-- integrity_json records prepared_projection_hash + per-lane actual projection
-- hashes so a pairing violation is auditable. classification mirrors
-- PairClassification (unpaired/pairing_violation/baseline_error/candidate_error/
-- regression/fixed/stable_pass/stable_fail).

CREATE TABLE IF NOT EXISTS comparisons (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  run_attempt_id TEXT NOT NULL REFERENCES run_attempts(id),
  baseline_lane_id TEXT NOT NULL,
  candidate_lane_id TEXT NOT NULL,
  policy_json TEXT NOT NULL DEFAULT '{}',
  summary_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comparison_pairs (
  id TEXT PRIMARY KEY,
  comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
  pair_key TEXT NOT NULL,
  baseline_episode_attempt_id TEXT,
  candidate_episode_attempt_id TEXT,
  classification TEXT NOT NULL,
  integrity_json TEXT NOT NULL DEFAULT '{}',
  delta_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(comparison_id, pair_key)
);

CREATE INDEX IF NOT EXISTS idx_comparison_pairs_comparison
  ON comparison_pairs(comparison_id, pair_key);
CREATE INDEX IF NOT EXISTS idx_comparisons_run
  ON comparisons(run_id);
