from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from bench_env.env.base import ActionType
from bench_env.runner.base import EpisodeResult, ExecutionResult
from bench_env.runner.events import ExecutionEvent
from bench_env.task.judge import JudgeResult
from test_platform.domain.reports.functional import build_functional_report
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ReplayRepository, ReportInputRepository
from test_platform.services.execution import (
    LaneExpectedEpisode,
    LaneObservedResult,
    LaneOutcomeBatch,
    LaneOutcomeCommitter,
    MultiprocessRunExecutor,
    ParallelRunExecutor,
    SerialRunExecutor,
)
from test_platform.tests.integration.test_multiprocess_lane import (
    _CompletingAgent as _MultiprocessAgent,
    _FakeEnvPool as _MultiprocessEnvPool,
    _FakeTaskFactory as _MultiprocessTaskFactory,
    _MaterializeEnv as _MultiprocessMaterializeEnv,
)
from test_platform.tests.integration.test_parallel_lane import (
    _CompletingAgent as _ParallelAgent,
    _FakeEnvPool as _ParallelEnvPool,
    _FakeTaskFactory as _ParallelTaskFactory,
    _MaterializeEnv as _ParallelMaterializeEnv,
)
from test_platform.tests.integration.test_result_ingestor_split import (
    _FakeResult,
    _episode_keys,
    _running_lane_attempt,
)
from test_platform.tests.integration.test_serial_run_execution import (
    _ExecutableFakeEnv,
    _ExecutableTaskFactory,
    _FakeAgent as _SerialAgent,
)
from test_platform.tests.integration.test_single_lane_materialization import (
    _create_run,
    _settings,
)


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


def _expected(keys: list[str]) -> tuple[LaneExpectedEpisode, ...]:
    return tuple(
        LaneExpectedEpisode(
            episode_key=key,
            task_id="fake.SampleTask",
            trial_id=index,
        )
        for index, key in enumerate(keys)
    )


def _object_observation(key: str, trial_id: int) -> LaneObservedResult:
    result = _FakeResult(trial_id=trial_id, success=True)
    result.episode_key = key
    return LaneObservedResult(episode_key=key, result=result)


def _dict_observation(key: str, trial_id: int) -> LaneObservedResult:
    return LaneObservedResult(
        episode_key=key,
        result={
            "id": "fake.SampleTask",
            "trial_id": trial_id,
            "is_success": True,
            "is_error": False,
        },
    )


def _result_for_outcome(
    trial_id: int,
    outcome: str,
    *,
    episode_key: str | None,
) -> EpisodeResult:
    error = "forced execution error" if outcome == "ERROR" else None
    judge = (
        JudgeResult.ok()
        if outcome == "PASS"
        else JudgeResult.fail("forced functional failure")
    )
    return EpisodeResult(
        task_id="fake.SampleTask",
        task_name="fake.SampleTask",
        suite="fake",
        execution=ExecutionResult(
            steps=1,
            trace=[],
            runtime_s=0.01,
            finished=outcome != "ERROR",
            truncated=False,
            stop_reason="ERROR" if outcome == "ERROR" else ActionType.COMPLETE,
            error=error,
        ),
        judge=judge,
        trial_id=trial_id,
        apps=["fake"],
        max_steps=15,
        episode_key=episode_key,
    )


def _emit_observed_terminal(
    events: Any,
    *,
    episode_key: str,
    trial_id: int,
    outcome: str,
) -> None:
    events.emit(
        ExecutionEvent(
            type="episode.error" if outcome == "ERROR" else "episode.completed",
            timestamp="",
            phase="execute",
            task_id="fake.SampleTask",
            trial_id=trial_id,
            episode_key=episode_key,
            payload={"outcome": outcome, "steps": 1},
        )
    )


def _batch(
    run_id: str,
    lane_attempt: dict[str, str],
    keys: list[str],
    observed: list[LaneObservedResult],
    *,
    cancelled: bool = False,
) -> LaneOutcomeBatch:
    return LaneOutcomeBatch(
        run_id=run_id,
        lane_attempt_id=lane_attempt["id"],
        lane_artifact_root=lane_attempt["artifact_root"],
        expected=_expected(keys),
        observed=tuple(observed),
        cancelled=cancelled,
        repeat_n=len(keys),
    )


@pytest.mark.parametrize(
    ("shape", "delivery_order"),
    [
        ("object", [0, 1, 2]),
        ("object", [2, 0, 1]),
        ("dict", [1, 2, 0]),
    ],
)
def test_lane_outcome_committer_normalizes_shapes_and_commits_in_plan_order(
    tmp_path,
    shape: str,
    delivery_order: list[int],
):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=3)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)
        observation = _object_observation if shape == "object" else _dict_observation
        observed = [observation(keys[index], index) for index in delivery_order]

        LaneOutcomeCommitter(database).commit(
            _batch(run.id, lane_attempt, keys, observed),
            events=_CapturingEvents(),
        )

        rows = database.connection.execute(
            """
            SELECT e.episode_key, ea.outcome
            FROM episode_attempts ea
            JOIN episodes e ON e.id = ea.episode_id
            WHERE e.run_id = ?
            ORDER BY ea.rowid
            """,
            (run.id,),
        ).fetchall()
        assert [(row["episode_key"], row["outcome"]) for row in rows] == [
            (key, "PASS") for key in keys
        ]
        assert database.connection.execute(
            "SELECT state FROM lane_attempts WHERE id = ?",
            (lane_attempt["id"],),
        ).fetchone()["state"] == "completed"
    finally:
        database.close()


@pytest.mark.parametrize(
    ("cancelled", "missing_outcome", "missing_code", "event_type"),
    [
        (False, "ERROR", "WORKER_CRASH", "episode.error"),
        (True, "CANCELLED", "CANCELLED", "episode.cancelled"),
    ],
)
def test_lane_outcome_committer_interprets_missing_results_by_completion_cause(
    tmp_path,
    cancelled: bool,
    missing_outcome: str,
    missing_code: str,
    event_type: str,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)
        events = _CapturingEvents()

        LaneOutcomeCommitter(database).commit(
            _batch(
                run.id,
                lane_attempt,
                keys,
                [_object_observation(keys[0], 0)],
                cancelled=cancelled,
            ),
            events=events,
        )

        rows = database.connection.execute(
            """
            SELECT e.episode_key, ea.outcome, ea.error_code, ea.result_json
            FROM episode_attempts ea
            JOIN episodes e ON e.id = ea.episode_id
            WHERE e.run_id = ?
            ORDER BY ea.rowid
            """,
            (run.id,),
        ).fetchall()
        assert rows[0]["outcome"] == "PASS"
        assert rows[1]["outcome"] == missing_outcome
        assert rows[1]["error_code"] == missing_code
        terminal = [event for event in events.events if event.episode_key == keys[1]]
        assert len(terminal) == 1
        assert terminal[0].type == event_type
        assert terminal[0].payload["outcome"] == missing_outcome
        assert terminal[0].payload["error_code"] == missing_code
    finally:
        database.close()


@pytest.mark.parametrize("invalid", ["unknown", "duplicate", "missing_key"])
def test_lane_outcome_committer_rejects_invalid_observations_before_ingestion(
    tmp_path,
    invalid: str,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)
        if invalid == "unknown":
            observed = [_object_observation("unknown", 0)]
        elif invalid == "duplicate":
            observed = [
                _object_observation(keys[0], 0),
                _object_observation(keys[0], 0),
            ]
        else:
            observed = [LaneObservedResult(episode_key=None, result={})]

        with pytest.raises(RunDomainError) as exc_info:
            LaneOutcomeCommitter(database).commit(
                _batch(run.id, lane_attempt, keys, observed),
                events=_CapturingEvents(),
            )

        assert exc_info.value.code == "EPISODE_RESULT_UNKNOWN"
        assert database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts"
        ).fetchone()[0] == 0
        assert database.connection.execute(
            "SELECT state FROM lane_attempts WHERE id = ?",
            (lane_attempt["id"],),
        ).fetchone()["state"] == "running"
    finally:
        database.close()


def test_lane_outcome_committer_rolls_back_the_whole_batch_before_emitting_events(
    tmp_path,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)
        events = _CapturingEvents()
        database.connection.executescript(
            """
            CREATE TRIGGER fail_second_episode_attempt
            BEFORE INSERT ON episode_attempts
            WHEN (SELECT COUNT(*) FROM episode_attempts) >= 1
            BEGIN
              SELECT RAISE(ABORT, 'forced second episode failure');
            END;
            """
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="forced second episode failure",
        ):
            LaneOutcomeCommitter(database).commit(
                _batch(
                    run.id,
                    lane_attempt,
                    keys,
                    [_object_observation(keys[0], 0)],
                ),
                events=events,
            )

        assert database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts"
        ).fetchone()[0] == 0
        assert database.connection.execute(
            "SELECT state FROM lane_attempts WHERE id = ?",
            (lane_attempt["id"],),
        ).fetchone()["state"] == "running"
        assert database.connection.execute(
            "SELECT state FROM run_attempts WHERE run_id = ?",
            (run.id,),
        ).fetchone()["state"] == "running"
        assert database.connection.execute(
            "SELECT state FROM runs WHERE id = ?",
            (run.id,),
        ).fetchone()["state"] == "running"
        assert events.events == []
    finally:
        database.close()


def test_lane_outcome_committer_finalizes_the_lane_once_after_all_attempts(
    tmp_path,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=3)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)
        database.connection.executescript(
            """
            CREATE TEMP TABLE lane_finalization_audit (lane_attempt_id TEXT);
            CREATE TEMP TRIGGER audit_lane_finalization
            AFTER UPDATE OF state ON lane_attempts
            WHEN OLD.state <> NEW.state
            BEGIN
              INSERT INTO lane_finalization_audit (lane_attempt_id)
              VALUES (NEW.id);
            END;
            """
        )

        LaneOutcomeCommitter(database).commit(
            _batch(
                run.id,
                lane_attempt,
                keys,
                [_object_observation(key, index) for index, key in enumerate(keys)],
            )
        )

        assert database.connection.execute(
            "SELECT COUNT(*) FROM lane_finalization_audit WHERE lane_attempt_id = ?",
            (lane_attempt["id"],),
        ).fetchone()[0] == 1
        assert database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts"
        ).fetchone()[0] == 3
    finally:
        database.close()


async def _missing_executor_snapshot(tmp_path, mode: str, monkeypatch) -> dict[str, Any]:
    settings = _settings(tmp_path / mode)
    database = Database(settings)
    database.initialize()
    events = _CapturingEvents()
    try:
        run = _create_run(
            database,
            settings,
            repeat_n=3,
            parallel=2 if mode != "serial" else 1,
            processes=2 if mode == "multiprocess" else None,
        )
        if mode == "serial":
            from bench_env.runner.serial import SerialRunner

            async def no_results(_runner) -> list[Any]:
                return []

            monkeypatch.setattr(SerialRunner, "run", no_results)
            envs = iter(
                [
                    _ExecutableFakeEnv(label="materialize"),
                    _ExecutableFakeEnv(label="execute"),
                ]
            )
            executor = SerialRunExecutor(
                database,
                settings,
                task_factory=_ExecutableTaskFactory(),
                env_factory=lambda lane: next(envs),
                agent_factory=lambda lane: _SerialAgent(),
            )
        elif mode == "parallel":
            from bench_env.runner.parallel import ParallelRunner

            async def no_results(_runner) -> list[Any]:
                return []

            monkeypatch.setattr(ParallelRunner, "run", no_results)
            executor = ParallelRunExecutor(
                database,
                settings,
                task_factory=_ParallelTaskFactory(),
                env_pool_factory=lambda lane: _ParallelEnvPool(2),
                agent_factory=lambda lane: _ParallelAgent(),
                env_factory=lambda lane: _ParallelMaterializeEnv(),
            )
        else:
            def child_factory(shard_spec, work_specs, event_sink, cancellation_token):
                async def no_results() -> list[EpisodeResult]:
                    return []

                return no_results()

            executor = MultiprocessRunExecutor(
                database,
                settings,
                task_factory=_MultiprocessTaskFactory(),
                child_runner_factory=child_factory,
                env_pool_factory=lambda lane: _MultiprocessEnvPool(2),
                agent_factory=lambda lane: _MultiprocessAgent(),
                env_factory=lambda lane: _MultiprocessMaterializeEnv(),
            )

        await executor.execute_run(run.id, events=events)
        attempts = database.connection.execute(
            """
            SELECT e.trial_id, ea.outcome, ea.error_code, ea.artifact_root
            FROM episode_attempts ea
            JOIN episodes e ON e.id = ea.episode_id
            WHERE e.run_id = ?
            ORDER BY e.trial_id
            """,
            (run.id,),
        ).fetchall()
        terminal_events = sorted(
            (
                event.episode_key,
                event.type,
                event.payload.get("outcome"),
                event.payload.get("error_code"),
            )
            for event in events.events
            if event.payload.get("reason") == "missing_result"
        )
        functional = build_functional_report(
            ReportInputRepository(database).get_for_completion(run.id)
        )
        return {
            "attempts": [dict(row) for row in attempts],
            "events": terminal_events,
            "summary": functional["summary"],
        }
    finally:
        database.close()


async def _observed_executor_snapshot(tmp_path, mode: str, monkeypatch) -> dict[str, Any]:
    settings = _settings(tmp_path / mode)
    database = Database(settings)
    database.initialize()
    events = _CapturingEvents()
    outcomes = ("PASS", "FAIL", "ERROR")
    try:
        run = _create_run(
            database,
            settings,
            repeat_n=3,
            parallel=2 if mode != "serial" else 1,
            processes=2 if mode == "multiprocess" else None,
        )
        if mode == "serial":
            from bench_env.runner.serial import SerialRunner

            async def positional_results(runner) -> list[EpisodeResult]:
                results = []
                for index, work_item in enumerate(runner.prepared_work_items):
                    outcome = outcomes[index]
                    _emit_observed_terminal(
                        runner.event_sink,
                        episode_key=work_item.episode_key,
                        trial_id=index,
                        outcome=outcome,
                    )
                    results.append(
                        _result_for_outcome(index, outcome, episode_key=None)
                    )
                return results

            monkeypatch.setattr(SerialRunner, "run", positional_results)
            envs = iter(
                [
                    _ExecutableFakeEnv(label="materialize"),
                    _ExecutableFakeEnv(label="execute"),
                ]
            )
            executor = SerialRunExecutor(
                database,
                settings,
                task_factory=_ExecutableTaskFactory(),
                env_factory=lambda lane: next(envs),
                agent_factory=lambda lane: _SerialAgent(),
            )
        elif mode == "parallel":
            from bench_env.runner.parallel import ParallelRunner

            async def unordered_object_results(runner) -> list[EpisodeResult]:
                results = []
                for index in (2, 0, 1):
                    work_item = runner.prepared_work_items[index]
                    outcome = outcomes[index]
                    _emit_observed_terminal(
                        runner.event_sink,
                        episode_key=work_item.episode_key,
                        trial_id=index,
                        outcome=outcome,
                    )
                    results.append(
                        _result_for_outcome(
                            index,
                            outcome,
                            episode_key=work_item.episode_key,
                        )
                    )
                return results

            monkeypatch.setattr(ParallelRunner, "run", unordered_object_results)
            executor = ParallelRunExecutor(
                database,
                settings,
                task_factory=_ParallelTaskFactory(),
                env_pool_factory=lambda lane: _ParallelEnvPool(2),
                agent_factory=lambda lane: _ParallelAgent(),
                env_factory=lambda lane: _ParallelMaterializeEnv(),
            )
        else:
            from bench_env.runner.multiprocess import MultiProcessRunner

            async def unordered_dict_results(runner) -> list[dict[str, Any]]:
                results = []
                for index in (2, 0, 1):
                    work_spec = runner._prepared_work_specs[index]
                    outcome = outcomes[index]
                    _emit_observed_terminal(
                        runner._external_event_sink,
                        episode_key=work_spec.episode_key,
                        trial_id=index,
                        outcome=outcome,
                    )
                    results.append(
                        _result_for_outcome(
                            index,
                            outcome,
                            episode_key=work_spec.episode_key,
                        ).to_dict()
                    )
                return results

            monkeypatch.setattr(MultiProcessRunner, "run", unordered_dict_results)
            executor = MultiprocessRunExecutor(
                database,
                settings,
                task_factory=_MultiprocessTaskFactory(),
                child_runner_factory=lambda *args: None,
                env_pool_factory=lambda lane: _MultiprocessEnvPool(2),
                agent_factory=lambda lane: _MultiprocessAgent(),
                env_factory=lambda lane: _MultiprocessMaterializeEnv(),
            )

        await executor.execute_run(run.id, events=events)
        attempts = database.connection.execute(
            """
            SELECT e.episode_key, e.trial_id, ea.outcome, ea.error_code,
                   ea.artifact_root
            FROM episode_attempts ea
            JOIN episodes e ON e.id = ea.episode_id
            WHERE e.run_id = ?
            ORDER BY e.trial_id
            """,
            (run.id,),
        ).fetchall()
        for attempt in attempts:
            episode_dir = settings.runs_dir / run.id / attempt["artifact_root"]
            episode_dir.mkdir(parents=True, exist_ok=True)
            (episode_dir / "trajectory.json").write_text("[]", encoding="utf-8")
        replay_repository = ReplayRepository(database)
        replays = [
            replay_repository.get_episode_replay(run.id, attempt["episode_key"])
            for attempt in attempts
        ]
        functional = build_functional_report(
            ReportInputRepository(database).get_for_completion(run.id)
        )
        terminal_events = sorted(
            (
                event.episode_key,
                event.type,
                event.payload.get("outcome"),
            )
            for event in events.events
            if event.type in {"episode.completed", "episode.error"}
        )
        return {
            "attempts": [dict(row) for row in attempts],
            "events": terminal_events,
            "summary": functional["summary"],
            "replays": [
                {
                    "episode_key": replay["episode_key"],
                    "outcome": replay["outcome"],
                    "error_code": replay["error_code"],
                    "artifact_root": replay["artifact_root"],
                    "steps": replay["steps"],
                }
                for replay in replays
            ],
        }
    finally:
        database.close()


@pytest.mark.asyncio
async def test_single_lane_executor_adapters_commit_identical_missing_outcomes(
    tmp_path,
    monkeypatch,
):
    snapshots = {}
    for mode in ("serial", "parallel", "multiprocess"):
        with monkeypatch.context() as context:
            snapshots[mode] = await _missing_executor_snapshot(
                tmp_path,
                mode,
                context,
            )

    assert snapshots["parallel"] == snapshots["serial"]
    assert snapshots["multiprocess"] == snapshots["serial"]
    assert [item["outcome"] for item in snapshots["serial"]["attempts"]] == [
        "ERROR",
        "ERROR",
        "ERROR",
    ]
    assert snapshots["serial"]["summary"]["errors"] == 3


@pytest.mark.asyncio
async def test_single_lane_executor_raw_adapters_preserve_observed_outcome_parity(
    tmp_path,
    monkeypatch,
):
    snapshots = {}
    for mode in ("serial", "parallel", "multiprocess"):
        with monkeypatch.context() as context:
            snapshots[mode] = await _observed_executor_snapshot(
                tmp_path,
                mode,
                context,
            )

    assert snapshots["parallel"] == snapshots["serial"]
    assert snapshots["multiprocess"] == snapshots["serial"]
    assert [item["outcome"] for item in snapshots["serial"]["attempts"]] == [
        "PASS",
        "FAIL",
        "ERROR",
    ]
    assert snapshots["serial"]["summary"]["successes"] == 1
    assert snapshots["serial"]["summary"]["failures"] == 1
    assert snapshots["serial"]["summary"]["errors"] == 1
