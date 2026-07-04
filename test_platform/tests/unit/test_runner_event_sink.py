"""P1.3: the PlatformRunnerEventSink bridges bench_env ExecutionEvents into the
platform EventWriter, so episode.started / episode.step_recorded / episode.cancelled
reach the events table and (via the broker) the SSE stream."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from bench_env.runner.events import ExecutionEvent

from test_platform.config import PlatformSettings
from test_platform.execution.event_writer import EventWriter
from test_platform.execution.runner_sink import PlatformRunnerEventSink
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database


def _utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _database(tmp_path) -> Database:
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    return database


def _seed_run(database: Database, run_id: str = "r1") -> str:
    c = database.connection
    c.execute(
        "INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
        "VALUES ('p1','P','p','p',NULL,'2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
    )
    c.execute(
        "INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
        "VALUES ('w1','p1','W','{}','2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
    )
    c.execute(
        "INSERT INTO workflow_versions "
        "(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
        "VALUES ('wv1','w1',1,'published','{}','h','2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
    )
    # runs BEFORE run_attempts (FK).
    c.execute(
        "INSERT INTO runs "
        "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
        " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at) "
        f"VALUES ('{run_id}','p1','wv1',NULL,'running','{{}}','h','runs/{run_id}', "
        " 1, NULL, '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    c.execute(
        "INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, created_at) "
        f"VALUES ('ra1','{run_id}',1,'initial','running','2026-07-04T00:00:00.000Z')"
    )
    c.commit()
    return run_id


def test_runner_sink_persists_episode_events_with_identity(tmp_path):
    """bench_env ExecutionEvents flow through the sink into the events table."""
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        writer = EventWriter(database)
        sink = PlatformRunnerEventSink(
            writer,
            run_id=run_id,
            run_attempt_id="ra1",
            lane_id="lane1",
            lane_attempt_id="la1",
            worker_id="serial",
        )

        sink.emit(ExecutionEvent(
            type="episode.started",
            timestamp=_utc(),
            phase="execute",
            worker_id="serial",
            task_id="fake.Task",
            trial_id=0,
            payload={"episode_key": "fake.Task|i0|s1|r1|t0", "max_steps": 5},
        ))
        sink.emit(ExecutionEvent(
            type="episode.step_recorded",
            timestamp=_utc(),
            phase="record",
            worker_id="serial",
            task_id="fake.Task",
            trial_id=0,
            payload={"step": 1},
        ))

        rows = database.connection.execute(
            "SELECT type, run_attempt_id, lane_id, worker_id FROM events "
            "WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        assert [r["type"] for r in rows] == ["episode.started", "episode.step_recorded"]
        for r in rows:
            assert r["run_attempt_id"] == "ra1"
            assert r["lane_id"] == "lane1"
            assert r["worker_id"] == "serial"
    finally:
        database.close()


def test_runner_sink_publishes_to_broker(tmp_path):
    """Sink-originated events reach SSE subscribers."""
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        broker = SSEBroker()
        writer = EventWriter(database, broker)
        sink = PlatformRunnerEventSink(writer, run_id=run_id, worker_id="serial")

        async def main():
            broker.bind_loop(asyncio.get_running_loop())
            sub = await broker.subscribe(run_id)
            sink.emit(ExecutionEvent(
                type="episode.started", timestamp=_utc(), payload={"step": 0}
            ))
            received = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            assert received is not None
            assert received.type == "episode.started"

        asyncio.run(main())
    finally:
        database.close()


def test_runner_sink_never_raises_on_writer_failure(tmp_path):
    """A broken EventWriter must not propagate into the runner."""
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)

        class BrokenWriter:
            def emit(self, *a, **kw):
                raise RuntimeError("writer exploded")

        sink = PlatformRunnerEventSink(BrokenWriter(), run_id=run_id)  # type: ignore[arg-type]
        # Must not raise.
        sink.emit(ExecutionEvent(type="episode.started", timestamp=_utc(), payload={}))
    finally:
        database.close()


def test_runner_sink_resolves_episode_id_from_episode_key(tmp_path):
    """VS-07: when an episode_key_resolver is provided, the sink maps the
    runner's episode_key to the persisted episodes.id and writes it to the
    episode_id column of the event."""
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        # Insert one episode row so the resolver can map episode_key -> id.
        database.connection.execute(
            "INSERT INTO episodes "
            "(id, run_id, episode_key, materialization_key, pair_key, task_base_id, "
            " task_id, instance_id, instance_seed, template_index, trial_id, max_steps, created_at) "
            "VALUES ('ep-1', ?, 'fake.Task|i0|s1|r1|t0', 'mk', 'pk', 'fake.Task', "
            " 'fake.Task', 0, 1, NULL, 0, 15, '2026-07-04T00:00:00.000Z')",
            (run_id,),
        )
        database.connection.commit()

        writer = EventWriter(database)

        def resolver(episode_key: str) -> str | None:
            row = database.connection.execute(
                "SELECT id FROM episodes WHERE run_id = ? AND episode_key = ?",
                (run_id, episode_key),
            ).fetchone()
            return str(row["id"]) if row else None

        sink = PlatformRunnerEventSink(
            writer,
            run_id=run_id,
            run_attempt_id="ra1",
            lane_id="lane1",
            lane_attempt_id="la1",
            worker_id="W0",
            episode_key_resolver=resolver,
        )

        sink.emit(ExecutionEvent(
            type="episode.started",
            timestamp=_utc(),
            phase="execute",
            worker_id="W0",
            task_id="fake.Task",
            trial_id=0,
            episode_key="fake.Task|i0|s1|r1|t0",
            payload={"max_steps": 5},
        ))

        row = database.connection.execute(
            "SELECT episode_id, worker_id FROM events WHERE run_id = ? AND type = 'episode.started'",
            (run_id,),
        ).fetchone()
        assert row is not None
        assert row["episode_id"] == "ep-1"
        assert row["worker_id"] == "W0"
    finally:
        database.close()


def test_runner_sink_leaves_episode_id_null_when_resolver_returns_none(tmp_path):
    """An unknown episode_key resolves to None → episode_id stays null (no drop)."""
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        writer = EventWriter(database)

        def resolver(episode_key: str) -> str | None:
            return None  # unknown key

        sink = PlatformRunnerEventSink(
            writer, run_id=run_id, episode_key_resolver=resolver,
        )
        sink.emit(ExecutionEvent(
            type="episode.started",
            timestamp=_utc(),
            episode_key="does-not-exist",
            payload={},
        ))

        row = database.connection.execute(
            "SELECT episode_id FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row is not None
        assert row["episode_id"] is None
    finally:
        database.close()


def test_runner_sink_without_resolver_leaves_episode_id_null(tmp_path):
    """No resolver (e.g. CLI path) → episode_id is null even with an episode_key."""
    database = _database(tmp_path)
    try:
        run_id = _seed_run(database)
        writer = EventWriter(database)
        sink = PlatformRunnerEventSink(writer, run_id=run_id, worker_id="serial")
        sink.emit(ExecutionEvent(
            type="episode.started",
            timestamp=_utc(),
            episode_key="fake.Task|i0|s1|r1|t0",
            payload={},
        ))
        row = database.connection.execute(
            "SELECT episode_id FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row is not None
        assert row["episode_id"] is None
    finally:
        database.close()
