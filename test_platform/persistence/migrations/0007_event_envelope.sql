-- VS-06: extend the events envelope to carry full run/lane/episode/worker
-- identity (IMPLEMENTATION_DESIGN §13.1) and allow cancel idempotency to replay
-- the original response body.
--
-- All new event columns are nullable so VS-05's `run.created` rows (which only
-- populated run_id/sequence/type/entity_type/entity_id/occurred_at/payload_json)
-- remain readable. New events populate both the typed identity columns and the
-- legacy entity_type/entity_id pair for backward compatibility.

ALTER TABLE events ADD COLUMN run_attempt_id TEXT REFERENCES run_attempts(id);
ALTER TABLE events ADD COLUMN lane_id TEXT;
ALTER TABLE events ADD COLUMN lane_attempt_id TEXT;
ALTER TABLE events ADD COLUMN episode_id TEXT;
ALTER TABLE events ADD COLUMN episode_attempt_id TEXT;
ALTER TABLE events ADD COLUMN worker_id TEXT;
ALTER TABLE events ADD COLUMN payload_version INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_events_run_attempt
  ON events(run_attempt_id);

-- Cancel idempotency: the first response must be replayable verbatim for
-- repeated `Idempotency-Key` submissions on POST /runs/{id}/cancel.
ALTER TABLE idempotency_keys ADD COLUMN response_json TEXT;
