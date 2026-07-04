from __future__ import annotations

import threading

from test_platform.execution.event_writer import EventWriter
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database
from test_platform.config import PlatformSettings


def _database(tmp_path) -> Database:
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    return database


def _seed_run(database: Database, run_id: str = "r1", project_suffix: str = "1") -> str:
    """Create a minimal run row so events can attach to it."""
    database.connection.execute(
        f"INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
        f"VALUES ('p{project_suffix}', 'P{project_suffix}', 'p{project_suffix}', "
        f"'p{project_suffix}', NULL, '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.execute(
        f"INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
        f"VALUES ('w{project_suffix}', 'p{project_suffix}', 'W{project_suffix}', "
        f"'{{}}', '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.execute(
        f"INSERT INTO workflow_versions "
        f"(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
        f"VALUES ('wv{project_suffix}', 'w{project_suffix}', 1, 'published', '{{}}', 'h', "
        f"'2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.execute(
        "INSERT INTO runs "
        "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
        " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at) "
        f"VALUES ('{run_id}', 'p{project_suffix}', 'wv{project_suffix}', NULL, 'queued', "
        f"'{{}}', 'h', 'runs/{run_id}', 1, NULL, '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    database.connection.commit()
    return run_id


def test_emit_assigns_monotonic_gap_free_sequence_per_run(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        writer = EventWriter(database)

        seqs = []
        for i in range(5):
            event = writer.emit(run_id, f"type.{i}", {"i": i})
            assert event is not None
            seqs.append(event.sequence)

        assert seqs == [1, 2, 3, 4, 5]

        row = database.connection.execute(
            "SELECT next_event_sequence FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["next_event_sequence"] == 6
    finally:
        database.close()


def test_two_runs_have_independent_sequences(tmp_path):
    database = _database(tmp_path)
    try:
        run_a = _seed_run(database, "ra", project_suffix="a")
        _seed_run(database, "rb", project_suffix="b")
        writer = EventWriter(database)

        a1 = writer.emit(run_a, "t", {"n": 1})
        b1 = writer.emit("rb", "t", {"n": 1})
        a2 = writer.emit(run_a, "t", {"n": 2})

        assert a1 is not None and a2 is not None and b1 is not None
        assert a1.sequence == 1
        assert a2.sequence == 2
        assert b1.sequence == 1
    finally:
        database.close()


def test_concurrent_emit_from_threads_produces_unique_sequences(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        writer = EventWriter(database)

        sequences: list[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def worker(idx: int) -> None:
            barrier.wait()
            for i in range(10):
                event = writer.emit(run_id, f"t.{idx}.{i}", {"idx": idx, "i": i})
                assert event is not None
                with lock:
                    sequences.append(event.sequence)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No gaps, no duplicates: sequences form a contiguous 1..N range.
        assert sorted(sequences) == list(range(1, 81))
    finally:
        database.close()


def test_persisted_event_carries_payload_version_and_envelope_fields(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        broker = SSEBroker()
        writer = EventWriter(database, broker)

        event = writer.emit(
            run_id,
            "episode.started",
            {"episode_key": "fake.T|i0|s1|r1|t0"},
            lane_id="lane1",
            episode_id="ep1",
            worker_id="w0",
        )
        assert event is not None
        assert event.payload_version == 1
        assert event.lane_id == "lane1"
        assert event.episode_id == "ep1"
        assert event.worker_id == "w0"
        assert event.sequence == 1
    finally:
        database.close()


def test_emit_returns_none_for_unknown_run(tmp_path):
    database = _database(tmp_path)
    try:
        writer = EventWriter(database)
        assert writer.emit("does-not-exist", "t", {}) is None
    finally:
        database.close()
