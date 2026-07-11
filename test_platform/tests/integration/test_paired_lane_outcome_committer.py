from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bench_env.runner.cancellation import CancellationToken, RunCancelled
from test_platform.domain.reports.functional import build_functional_report
from test_platform.execution.event_writer import EventWriter
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    ComparisonRepository,
    ReportInputRepository,
    RunRepository,
)
from test_platform.services.execution import (
    PairedParallelRunExecutor,
    PairedSerialRunExecutor,
)
from test_platform.services.completion import RunCompletionPipeline
from test_platform.tests.integration.test_paired_parallel_run import (
    _build_paired_parallel_run,
)
from test_platform.tests.integration.test_paired_serial_run import (
    _PairedEnv,
    _PairedTaskFactory,
    _ScriptedAgent,
    _build_paired_run,
)
from test_platform.tests.integration.test_single_lane_materialization import _settings


class _DelayedCrashingEnv(_PairedEnv):
    async def get_state(self, required_apps=None):
        await asyncio.sleep(0.1)
        raise RuntimeError("candidate lane crashed before reporting a result")


def _paired_executor(
    mode: str,
    database: Database,
    settings: Any,
    *,
    baseline_env: _PairedEnv,
    candidate_env: _PairedEnv,
):
    materialize_env = _PairedEnv(label="materialize")
    env_by_lane = {"baseline": baseline_env, "candidate": candidate_env}
    call_count = 0

    def env_factory(lane):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return materialize_env
        return env_by_lane[lane.lane_key]

    executor_type = (
        PairedSerialRunExecutor if mode == "serial" else PairedParallelRunExecutor
    )
    return executor_type(
        database,
        settings,
        task_factory=_PairedTaskFactory(),
        env_factory=env_factory,
        agent_factory=lambda lane: _ScriptedAgent(succeed=True),
    )


async def _candidate_crash_snapshot(tmp_path, mode: str) -> dict[str, Any]:
    settings = _settings(tmp_path / mode)
    database = Database(settings)
    database.initialize()
    try:
        run = (
            _build_paired_run(database, settings)
            if mode == "serial"
            else _build_paired_parallel_run(database, settings, repeat_n=1)
        )
        writer = EventWriter(database)
        executor = _paired_executor(
            mode,
            database,
            settings,
            baseline_env=_PairedEnv(label="baseline"),
            candidate_env=_DelayedCrashingEnv(label="candidate"),
        )

        with pytest.raises(RuntimeError, match="candidate lane crashed"):
            await executor.execute_run(run.id, run_event_writer=writer)

        report_input = ReportInputRepository(database).get_for_run(run.id)
        attempts = sorted(
            (
                attempt["lane_key"],
                attempt["outcome"],
                attempt["error_code"],
            )
            for attempt in report_input.episode_attempts
        )
        comparison = report_input.comparison
        assert comparison is not None
        pairs = [
            (
                pair["classification"],
                pair["delta"]["baseline_outcome"],
                pair["delta"]["candidate_outcome"],
            )
            for pair in comparison["pairs"]
        ]
        lane_states = [
            row["state"]
            for row in database.connection.execute(
                """
                SELECT la.state
                FROM lane_attempts la
                JOIN lanes l ON l.id = la.lane_id
                WHERE l.run_id = ?
                ORDER BY l.lane_key
                """,
                (run.id,),
            ).fetchall()
        ]
        terminal_events = sorted(
            (
                event.worker_id,
                event.type,
                event.payload.get("outcome"),
                event.payload.get("error_code"),
            )
            for event in RunRepository(database).list_events(run.id)
            if event.type in {"episode.completed", "episode.error"}
        )
        functional = build_functional_report(report_input)
        return {
            "attempts": attempts,
            "pairs": pairs,
            "lane_states": lane_states,
            "events": terminal_events,
            "functional_summary": functional["summary"],
        }
    finally:
        database.close()


async def _cancelled_grid_snapshot(tmp_path, mode: str) -> dict[str, Any]:
    settings = _settings(tmp_path / mode)
    database = Database(settings)
    database.initialize()
    try:
        run = (
            _build_paired_run(database, settings)
            if mode == "serial"
            else _build_paired_parallel_run(database, settings, repeat_n=1)
        )
        writer = EventWriter(database)
        token = CancellationToken()
        token.cancel()
        database.connection.executescript(
            """
            CREATE TEMP TRIGGER require_cancel_comparison_before_run_finalization
            BEFORE UPDATE OF state ON runs
            WHEN NEW.state = 'cancelled'
              AND OLD.state <> NEW.state
              AND NOT EXISTS (
                SELECT 1 FROM comparisons WHERE run_id = NEW.id
              )
            BEGIN
              SELECT RAISE(ABORT, 'cancelled run finalized before comparison');
            END;
            """
        )

        with pytest.raises(RunCancelled):
            await _paired_executor(
                mode,
                database,
                settings,
                baseline_env=_PairedEnv(label="baseline"),
                candidate_env=_PairedEnv(label="candidate"),
            ).execute_run(run.id, token=token, run_event_writer=writer)

        attempts = database.connection.execute(
            """
            SELECT l.lane_key, ea.outcome, ea.error_code, ea.result_json
            FROM episode_attempts ea
            JOIN lane_attempts la ON la.id = ea.lane_attempt_id
            JOIN lanes l ON l.id = la.lane_id
            WHERE l.run_id = ?
            ORDER BY l.lane_key
            """,
            (run.id,),
        ).fetchall()
        events = sorted(
            (
                event.worker_id,
                event.type,
                event.payload.get("outcome"),
                event.payload.get("error_code"),
            )
            for event in RunRepository(database).list_events(run.id)
            if event.type == "episode.cancelled"
        )
        lane_states = database.connection.execute(
            """
            SELECT l.lane_key, la.state
            FROM lane_attempts la
            JOIN lanes l ON l.id = la.lane_id
            WHERE l.run_id = ?
            ORDER BY l.lane_key
            """,
            (run.id,),
        ).fetchall()
        comparison = ComparisonRepository(database).get_comparison(run.id)
        assert comparison is not None
        return {
            "attempts": [
                (row["lane_key"], row["outcome"], row["error_code"])
                for row in attempts
            ],
            "events": events,
            "lane_states": [
                (row["lane_key"], row["state"]) for row in lane_states
            ],
            "pairs": [
                (
                    pair["classification"],
                    pair["delta"]["baseline_outcome"],
                    pair["delta"]["candidate_outcome"],
                )
                for pair in comparison["pairs"]
            ],
        }
    finally:
        database.close()


@pytest.mark.parametrize("mode", ["serial", "parallel"])
@pytest.mark.asyncio
async def test_paired_commit_orders_lane_facts_before_comparison_and_run(
    tmp_path,
    mode: str,
):
    settings = _settings(tmp_path / mode)
    database = Database(settings)
    database.initialize()
    try:
        run = (
            _build_paired_run(database, settings)
            if mode == "serial"
            else _build_paired_parallel_run(database, settings, repeat_n=1)
        )
        database.connection.executescript(
            """
            CREATE TEMP TABLE paired_commit_audit (
              entity TEXT NOT NULL,
              entity_id TEXT NOT NULL
            );

            CREATE TEMP TRIGGER require_lane_facts_before_comparison
            BEFORE INSERT ON comparisons
            WHEN EXISTS (
              SELECT 1
              FROM lane_attempts la
              JOIN lanes l ON l.id = la.lane_id
              WHERE l.run_id = NEW.run_id
                AND la.state NOT IN ('completed', 'failed', 'cancelled')
            ) OR (
              SELECT COUNT(*)
              FROM episode_attempts ea
              JOIN lane_attempts la ON la.id = ea.lane_attempt_id
              JOIN lanes l ON l.id = la.lane_id
              WHERE l.run_id = NEW.run_id
            ) != 2 * (
              SELECT COUNT(*) FROM episodes WHERE run_id = NEW.run_id
            )
            BEGIN
              SELECT RAISE(ABORT, 'comparison preceded canonical lane facts');
            END;

            CREATE TEMP TRIGGER require_comparison_before_run_finalization
            BEFORE UPDATE OF state ON runs
            WHEN NEW.state IN ('completed', 'failed', 'cancelled')
              AND OLD.state <> NEW.state
              AND NOT EXISTS (
                SELECT 1 FROM comparisons WHERE run_id = NEW.id
              )
            BEGIN
              SELECT RAISE(ABORT, 'run finalized before comparison');
            END;

            CREATE TEMP TRIGGER audit_lane_finalization
            AFTER UPDATE OF state ON lane_attempts
            WHEN NEW.state IN ('completed', 'failed', 'cancelled')
              AND OLD.state <> NEW.state
            BEGIN
              INSERT INTO paired_commit_audit (entity, entity_id)
              VALUES ('lane', NEW.id);
            END;

            CREATE TEMP TRIGGER audit_run_finalization
            AFTER UPDATE OF state ON runs
            WHEN NEW.state IN ('completed', 'failed', 'cancelled')
              AND OLD.state <> NEW.state
            BEGIN
              INSERT INTO paired_commit_audit (entity, entity_id)
              VALUES ('run', NEW.id);
            END;
            """
        )

        detail = await _paired_executor(
            mode,
            database,
            settings,
            baseline_env=_PairedEnv(label="baseline"),
            candidate_env=_PairedEnv(label="candidate"),
        ).execute_run(run.id)

        assert detail.state == "evaluating"
        RunCompletionPipeline(
            database,
            event_writer=EventWriter(database),
        ).complete(run.id)
        assert RunRepository(database).get(run.id).state == "completed"
        audit = database.connection.execute(
            "SELECT entity, COUNT(*) AS n FROM paired_commit_audit GROUP BY entity"
        ).fetchall()
        assert {row["entity"]: row["n"] for row in audit} == {
            "lane": 2,
            "run": 1,
        }
        assert database.connection.execute(
            "SELECT COUNT(*) FROM comparisons WHERE run_id = ?",
            (run.id,),
        ).fetchone()[0] == 1
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_adapters_commit_the_same_cancelled_episode_grid(tmp_path):
    serial = await _cancelled_grid_snapshot(tmp_path, "serial")
    parallel = await _cancelled_grid_snapshot(tmp_path, "parallel")

    assert parallel == serial
    assert serial["attempts"] == [
        ("baseline", "CANCELLED", "CANCELLED"),
        ("candidate", "CANCELLED", "CANCELLED"),
    ]
    assert serial["lane_states"] == [
        ("baseline", "cancelled"),
        ("candidate", "cancelled"),
    ]
    assert serial["pairs"] == [("baseline_error", "CANCELLED", "CANCELLED")]
    assert len(serial["events"]) == 2


@pytest.mark.asyncio
async def test_paired_adapters_commit_the_same_candidate_crash_facts(tmp_path):
    serial = await _candidate_crash_snapshot(tmp_path, "serial")
    parallel = await _candidate_crash_snapshot(tmp_path, "parallel")

    assert parallel == serial
    assert serial["attempts"] == [
        ("baseline", "PASS", None),
        ("candidate", "ERROR", "WORKER_CRASH"),
    ]
    assert serial["pairs"] == [("candidate_error", "PASS", "ERROR")]
    assert serial["lane_states"] == ["completed", "failed"]
    assert serial["functional_summary"]["successes"] == 1
    assert serial["functional_summary"]["errors"] == 1


@pytest.mark.asyncio
async def test_paired_serial_agent_factory_failure_commits_terminal_grid(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _build_paired_run(database, settings)
        materialize_env = _PairedEnv(label="materialize")
        baseline_env = _PairedEnv(label="baseline")
        candidate_env = _PairedEnv(label="candidate")
        envs = iter([materialize_env, baseline_env, candidate_env])

        def agent_factory(lane):
            if lane.lane_key == "candidate":
                raise RuntimeError("candidate agent failed to start")
            return _ScriptedAgent(succeed=True)

        executor = PairedSerialRunExecutor(
            database,
            settings,
            task_factory=_PairedTaskFactory(),
            env_factory=lambda lane: next(envs),
            agent_factory=agent_factory,
        )

        with pytest.raises(RuntimeError, match="candidate agent failed to start"):
            await executor.execute_run(run.id)

        report_input = ReportInputRepository(database).get_for_run(run.id)
        assert sorted(
            (
                attempt["lane_key"],
                attempt["outcome"],
                attempt["error_code"],
            )
            for attempt in report_input.episode_attempts
        ) == [
            ("baseline", "PASS", None),
            ("candidate", "ERROR", "WORKER_CRASH"),
        ]
        detail = RunRepository(database).get(run.id)
        assert [lane["state"] for lane in detail.lane_attempts] == [
            "completed",
            "failed",
        ]
        assert report_input.comparison is not None
        assert [
            pair["classification"] for pair in report_input.comparison["pairs"]
        ] == ["candidate_error"]
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_parallel_agent_factory_failure_commits_terminal_grid(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _build_paired_parallel_run(database, settings, repeat_n=1)
        materialize_env = _PairedEnv(label="materialize")
        baseline_env = _PairedEnv(label="baseline")
        candidate_env = _PairedEnv(label="candidate")
        env_by_lane = {"baseline": baseline_env, "candidate": candidate_env}
        call_count = 0

        def env_factory(lane):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return materialize_env
            return env_by_lane[lane.lane_key]

        def agent_factory(lane):
            if lane.lane_key == "candidate":
                raise RuntimeError("candidate agent failed to start")
            return _ScriptedAgent(succeed=True)

        executor = PairedParallelRunExecutor(
            database,
            settings,
            task_factory=_PairedTaskFactory(),
            env_factory=env_factory,
            agent_factory=agent_factory,
        )

        with pytest.raises(RuntimeError, match="candidate agent failed to start"):
            await executor.execute_run(run.id)

        report_input = ReportInputRepository(database).get_for_run(run.id)
        assert sorted(
            (
                attempt["lane_key"],
                attempt["outcome"],
                attempt["error_code"],
            )
            for attempt in report_input.episode_attempts
        ) == [
            ("baseline", "CANCELLED", "CANCELLED"),
            ("candidate", "ERROR", "WORKER_CRASH"),
        ]
        detail = RunRepository(database).get(run.id)
        assert [lane["state"] for lane in detail.lane_attempts] == [
            "cancelled",
            "failed",
        ]
        assert report_input.comparison is not None
        assert [
            pair["classification"] for pair in report_input.comparison["pairs"]
        ] == ["candidate_error"]
    finally:
        database.close()
