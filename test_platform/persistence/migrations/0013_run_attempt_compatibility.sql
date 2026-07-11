-- TP-H10: store redacted compatibility preflight summary per run attempt.
ALTER TABLE run_attempts ADD COLUMN compatibility_json TEXT;
