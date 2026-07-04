from __future__ import annotations

import asyncio
import json

from test_platform.config import PlatformSettings
from test_platform.execution.event_writer import EventWriter
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database


# Reuse the minimal run-seed helper from the unit test by inlining it here so
# this integration test stays self-contained.
def _database(tmp_path) -> Database:
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    return database


def _seed_run(database: Database, run_id: str = "r1") -> str:
    database.connection.execute(
        "INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
        "VALUES ('p1', 'P', 'p', 'p', NULL, '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.execute(
        "INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
        "VALUES ('w1', 'p1', 'W', '{}', '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.execute(
        "INSERT INTO workflow_versions "
        "(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
        "VALUES ('wv1', 'w1', 1, 'published', '{}', 'h', '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.execute(
        "INSERT INTO runs "
        "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
        " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at) "
        f"VALUES ('{run_id}', 'p1', 'wv1', NULL, 'queued', '{{}}', 'h', 'runs/{run_id}', "
        " 1, NULL, '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.commit()
    return run_id


def test_emit_writes_full_envelope_and_bumps_sequence(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        # run_attempt_id has a FK to run_attempts; create the parent row.
        database.connection.execute(
            "INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, created_at) "
            "VALUES ('attempt1', ?, 1, 'initial', 'running', '2026-07-04T00:00:00.000Z')",
            (run_id,),
        )
        database.connection.commit()
        writer = EventWriter(database)

        event = writer.emit(
            run_id,
            "lane.started",
            {"lane_key": "candidate"},
            run_attempt_id="attempt1",
            lane_id="lane1",
            lane_attempt_id="laneattempt1",
            entity_type="lane",
            entity_id="lane1",
        )
        assert event is not None

        row = database.connection.execute(
            "SELECT * FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row["type"] == "lane.started"
        assert row["sequence"] == 1
        assert row["payload_version"] == 1
        assert row["run_attempt_id"] == "attempt1"
        assert row["lane_id"] == "lane1"
        assert row["lane_attempt_id"] == "laneattempt1"
        assert row["entity_type"] == "lane"
        assert row["entity_id"] == "lane1"
        assert json.loads(row["payload_json"]) == {"lane_key": "candidate"}

        next_seq = database.connection.execute(
            "SELECT next_event_sequence FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert next_seq["next_event_sequence"] == 2
    finally:
        database.close()


def test_emit_publishes_committed_event_to_broker(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        broker = SSEBroker()
        writer = EventWriter(database, broker)

        async def main():
            broker.bind_loop(asyncio.get_running_loop())
            subscription = await broker.subscribe(run_id)
            event = writer.emit(run_id, "run.started", {"state": "running"})
            assert event is not None

            received = await asyncio.wait_for(subscription.queue.get(), timeout=1.0)
            assert received is not None
            assert received.sequence == 1
            assert received.type == "run.started"

        asyncio.run(main())
    finally:
        database.close()


class _FlakyConnection:
    """Wraps a real connection but raises on INSERT INTO events.

    sqlite3.Connection.execute is a read-only C attribute and cannot be patched
    directly, so we wrap the real connection and forward everything else. The
    flakiness fires only once so the recovery path (subsequent successful write)
    can be verified in the same test.
    """

    def __init__(self, real, *, fail_on: str, call_log: list[str] | None = None) -> None:
        self._real = real
        self._fail_on = fail_on
        self._call_log = call_log
        self._failed = False

    def execute(self, statement, *args, **kwargs):
        normalized = statement.strip()
        if self._call_log is not None:
            self._call_log.append(normalized)
        if not self._failed and normalized.startswith(self._fail_on):
            self._failed = True
            raise RuntimeError("simulated storage failure")
        return self._real.execute(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FlakyDatabase:
    """A Database stand-in whose `.connection` returns a flaky wrapper.

    EventWriter reads `database._lock` and `database.connection`; we expose both
    so the writer is exercised against the failing connection.
    """

    def __init__(self, real, *, fail_on: str, call_log: list[str] | None = None) -> None:
        self._real = real
        self._lock = real._lock  # noqa: SLF100 — share the real RLock
        self.connection = _FlakyConnection(real.connection, fail_on=fail_on, call_log=call_log)


def test_emit_never_raises_when_database_write_fails(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        flaky = _FlakyDatabase(database, fail_on="INSERT INTO events")
        writer = EventWriter(flaky)  # type: ignore[arg-type]

        # Invariant: emit returns None instead of propagating the exception.
        assert writer.emit(run_id, "run.started", {}) is None
    finally:
        database.close()


def test_emit_failure_rolls_back_transaction_leaving_no_hole(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        call_log: list[str] = []
        flaky = _FlakyDatabase(database, fail_on="INSERT INTO events", call_log=call_log)
        writer = EventWriter(flaky)  # type: ignore[arg-type]

        assert writer.emit(run_id, "run.started", {}) is None

        # ROLLBACK was issued after the failure — the transaction did not commit
        # half-applied state.
        assert any("ROLLBACK" in entry for entry in call_log)

        # Sequence counter is untouched: no hole, no lock residue, subsequent
        # writes still succeed from sequence 1.
        row = database.connection.execute(
            "SELECT next_event_sequence FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["next_event_sequence"] == 1

        event = writer.emit(run_id, "run.started", {"ok": True})
        assert event is not None
        assert event.sequence == 1
    finally:
        database.close()


def test_emit_swallows_broker_publish_failure(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)

        class BrokenBroker:
            def publish(self, event):
                raise RuntimeError("broker exploded")

        writer = EventWriter(database, BrokenBroker())  # type: ignore[arg-type]

        # Persisted fine; broker failure did not propagate.
        event = writer.emit(run_id, "run.started", {})
        assert event is not None
        assert event.sequence == 1

        # And the event is still in the database (broker failure does not roll
        # back the committed write).
        row = database.connection.execute(
            "SELECT sequence FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row["sequence"] == 1
    finally:
        database.close()


def test_emit_never_raises_on_non_serializable_payload(tmp_path):
    """A payload that cannot be JSON-serialized must not propagate (P1.1)."""
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        writer = EventWriter(database)

        # A set is not JSON-serializable -> canonical_json raises TypeError.
        bad_payload = {"steps": {1, 2, 3}}

        result = writer.emit(run_id, "episode.step_recorded", bad_payload)

        # Invariant: emit returns None instead of raising.
        assert result is None
        # And no partial row was committed.
        rows = database.connection.execute(
            "SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert int(rows["n"]) == 0
    finally:
        database.close()
