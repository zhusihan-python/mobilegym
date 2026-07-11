from __future__ import annotations

import asyncio

import pytest

from test_platform.domain.runs import RunDomainError
from test_platform.execution.event_writer import EventWriter
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ReportRepository, RunRepository
from test_platform.services.completion import RunCompletionPipeline
from test_platform.services.execution import SerialRunExecutor
from test_platform.services.runs import RunSupervisor
from test_platform.tests.integration.test_report_input import (
    _seed_reportable_paired_run,
)
from test_platform.tests.integration.test_serial_run_execution import (
    _ExecutableFakeEnv,
    _ExecutableTaskFactory,
    _FakeAgent,
)
from test_platform.tests.integration.test_single_lane_materialization import _create_run
from test_platform.tests.integration.test_single_lane_materialization import _settings


def _prepare_evaluating_run(database: Database) -> str:
    run_id = _seed_reportable_paired_run(database)
    database.connection.execute(
        "UPDATE runs SET state = 'evaluating', ended_at = NULL WHERE id = ?",
        (run_id,),
    )
    database.connection.execute(
        "UPDATE run_attempts SET state = 'evaluating', ended_at = NULL, "
        "error_code = NULL WHERE id = 'attempt3'"
    )
    database.connection.execute(
        "UPDATE lane_attempts SET state = 'completed' "
        "WHERE run_attempt_id = 'attempt3'"
    )
    database.connection.commit()
    return run_id


def test_completion_persists_report_and_gate_before_completed(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run_id = _prepare_evaluating_run(database)
        database.connection.executescript(
            """
            CREATE TEMP TRIGGER require_report_and_gate_before_completion
            BEFORE UPDATE OF state ON runs
            WHEN NEW.state = 'completed'
              AND OLD.state <> NEW.state
              AND (
                NOT EXISTS (SELECT 1 FROM reports WHERE run_id = NEW.id)
                OR NOT EXISTS (
                  SELECT 1 FROM quality_gate_results WHERE run_id = NEW.id
                )
              )
            BEGIN
              SELECT RAISE(ABORT, 'run completed before report and gate');
            END;
            """
        )
        database.connection.commit()

        result = RunCompletionPipeline(
            database,
            event_writer=EventWriter(database),
        ).complete(run_id)

        detail = RunRepository(database).get(run_id)
        report = ReportRepository(database).get(run_id)
        assert result.state == "completed"
        assert result.gate_verdict == "not_configured"
        assert detail.state == "completed"
        assert detail.gate_verdict == "not_configured"
        assert detail.outcome_counts == {
            "pass": 1,
            "fail": 0,
            "error": 1,
            "cancelled": 0,
            "incomplete": 2,
        }
        assert report["id"] == result.report_id
        assert report["gate"]["verdict"] == "not_configured"
    finally:
        database.close()


@pytest.mark.asyncio
async def test_supervisor_completes_with_report_and_verdict_before_report_get(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    materialize_env = _ExecutableFakeEnv(label="materialize")
    execute_env = _ExecutableFakeEnv(label="execute")
    envs = iter([materialize_env, execute_env])
    executor = SerialRunExecutor(
        database,
        settings,
        task_factory=_ExecutableTaskFactory(),
        env_factory=lambda lane: next(envs),
        agent_factory=lambda lane: _FakeAgent(),
    )
    broker = SSEBroker()
    supervisor = RunSupervisor(database, settings, executor=executor, broker=broker)
    try:
        run = _create_run(database, settings, repeat_n=1)
        await supervisor.start()
        subscription = await broker.subscribe(run.id)
        supervisor.submit(run.id)

        while True:
            event = await asyncio.wait_for(subscription.queue.get(), timeout=5.0)
            assert event is not None
            if event.type in {"run.completed", "run.failed"}:
                break

        detail = RunRepository(database).get(run.id)
        assert event.type == "run.completed"
        assert detail.state == "completed"
        assert detail.gate_verdict == "not_configured"
        assert database.connection.execute(
            "SELECT COUNT(*) FROM reports WHERE run_id = ?",
            (run.id,),
        ).fetchone()[0] == 1
        assert database.connection.execute(
            "SELECT COUNT(*) FROM quality_gate_results WHERE run_id = ?",
            (run.id,),
        ).fetchone()[0] == 1
        persisted_events = RunRepository(database).list_events(run.id)
        event_types = [item.type for item in persisted_events]
        assert event_types.index("report.completed") < event_types.index(
            "gate.not_configured"
        ) < event_types.index("run.completed")
        assert event_types.count("run.completed") == 1
        terminal_event = next(
            item for item in persisted_events if item.type == "run.completed"
        )
        assert terminal_event.payload["gate_verdict"] == "not_configured"
        assert terminal_event.payload["outcome_counts"] == {
            "pass": 1,
            "fail": 0,
            "error": 0,
            "cancelled": 0,
            "incomplete": 0,
        }
    finally:
        await supervisor.stop()
        database.close()


@pytest.mark.asyncio
async def test_supervisor_owns_terminal_event_after_completion_failure(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    materialize_env = _ExecutableFakeEnv(label="materialize")
    execute_env = _ExecutableFakeEnv(label="execute")
    envs = iter([materialize_env, execute_env])
    executor = SerialRunExecutor(
        database,
        settings,
        task_factory=_ExecutableTaskFactory(),
        env_factory=lambda lane: next(envs),
        agent_factory=lambda lane: _FakeAgent(),
    )
    broker = SSEBroker()
    supervisor = RunSupervisor(database, settings, executor=executor, broker=broker)
    try:
        run = _create_run(database, settings, repeat_n=1)
        database.connection.executescript(
            """
            CREATE TEMP TRIGGER fail_supervised_report_persistence
            BEFORE INSERT ON reports
            BEGIN
              SELECT RAISE(ABORT, 'forced supervised report failure');
            END;
            """
        )
        await supervisor.start()
        subscription = await broker.subscribe(run.id)
        supervisor.submit(run.id)

        while True:
            event = await asyncio.wait_for(subscription.queue.get(), timeout=5.0)
            assert event is not None
            if event.type in {"run.completed", "run.failed"}:
                break

        persisted_events = RunRepository(database).list_events(run.id)
        event_types = [item.type for item in persisted_events]
        detail = RunRepository(database).get(run.id)
        assert event.type == "run.failed"
        assert detail.state == "failed"
        assert event_types.index("report.failed") < event_types.index("run.failed")
        assert event_types.count("run.failed") == 1
        assert "run.completed" not in event_types
    finally:
        await supervisor.stop()
        database.close()


def test_report_persistence_failure_marks_run_failed_and_preserves_attempts(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run_id = _prepare_evaluating_run(database)
        database.connection.executescript(
            """
            CREATE TEMP TRIGGER fail_report_persistence
            BEFORE INSERT ON reports
            BEGIN
              SELECT RAISE(ABORT, 'forced report persistence failure');
            END;
            """
        )
        database.connection.commit()
        attempt_count = database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts"
        ).fetchone()[0]

        with pytest.raises(RunDomainError) as exc_info:
            RunCompletionPipeline(
                database,
                event_writer=EventWriter(database),
            ).complete(run_id)

        detail = RunRepository(database).get(run_id)
        assert exc_info.value.code == "REPORT_PERSISTENCE_FAILED"
        assert detail.state == "failed"
        assert detail.run_attempts[-1]["state"] == "failed"
        assert database.connection.execute(
            "SELECT error_code FROM run_attempts WHERE id = 'attempt3'"
        ).fetchone()[0] == "REPORT_PERSISTENCE_FAILED"
        assert database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts"
        ).fetchone()[0] == attempt_count
        assert [
            event.type for event in RunRepository(database).list_events(run_id)
        ] == ["report.failed"]
    finally:
        database.close()


def test_gate_persistence_failure_marks_run_failed_and_rolls_back_report(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run_id = _prepare_evaluating_run(database)
        database.connection.executescript(
            """
            CREATE TEMP TRIGGER fail_gate_persistence
            BEFORE INSERT ON quality_gate_results
            BEGIN
              SELECT RAISE(ABORT, 'forced gate persistence failure');
            END;
            """
        )
        attempt_count = database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts"
        ).fetchone()[0]

        with pytest.raises(RunDomainError) as exc_info:
            RunCompletionPipeline(
                database,
                event_writer=EventWriter(database),
            ).complete(run_id)

        detail = RunRepository(database).get(run_id)
        assert exc_info.value.code == "QUALITY_GATE_PERSISTENCE_FAILED"
        assert detail.state == "failed"
        assert detail.run_attempts[-1]["state"] == "failed"
        assert database.connection.execute(
            "SELECT error_code FROM run_attempts WHERE id = 'attempt3'"
        ).fetchone()[0] == "QUALITY_GATE_PERSISTENCE_FAILED"
        assert database.connection.execute(
            "SELECT COUNT(*) FROM reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 0
        assert database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts"
        ).fetchone()[0] == attempt_count
        assert [
            event.type for event in RunRepository(database).list_events(run_id)
        ] == ["gate.failed"]
    finally:
        database.close()


def test_completion_is_idempotent_for_same_report_input(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run_id = _prepare_evaluating_run(database)
        pipeline = RunCompletionPipeline(
            database,
            event_writer=EventWriter(database),
        )

        first = pipeline.complete(run_id)
        second = pipeline.complete(run_id)

        assert second == first
        assert database.connection.execute(
            "SELECT COUNT(*) FROM reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1
        assert database.connection.execute(
            "SELECT COUNT(*) FROM quality_gate_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1
    finally:
        database.close()
