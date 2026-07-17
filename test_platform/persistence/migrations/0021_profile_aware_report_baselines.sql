ALTER TABLE baselines ADD COLUMN strictness_version INTEGER;
ALTER TABLE baselines ADD COLUMN execution_profile_revision_id TEXT;
ALTER TABLE baselines ADD COLUMN execution_profile_revision_hash TEXT;
ALTER TABLE baselines ADD COLUMN lane_fingerprint TEXT;
ALTER TABLE baselines ADD COLUMN source_run_attempt_id TEXT;
ALTER TABLE baselines ADD COLUMN compatibility_preflight_json TEXT;
