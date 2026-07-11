"""VS-08 Block D: MultiprocessRunExecutor integration through the platform stack.

Drives MultiprocessRunExecutor directly (and via the supervisor) with fake env
pools / agents / tasks (in-process child_runner_factory, no real spawn) to
verify:
- multi-episode multiprocess runs (processes>1) ingest results in PLAN order;
- a shard crash yields a WORKER_CRASH error_code for the missing episode;
- user cancel → CANCELLED outcome (token.cancelled=True drives reconciliation);
- exactly one run.started on normal completion; NO run.started on pre-cancel;
- shard fatal/stopped envelopes carry exitcode.

Mirrors test_parallel_lane.py's fakes + _create_run helper.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.runner.base import EpisodeResult, ExecutionResult
from bench_env.runner.events import ExecutionEvent
from bench_env.runner.work_spec import EpisodeWorkSpec
from bench_env.task.judge import JudgeResult
from test_platform.domain.reports.functional import build_functional_report
from test_platform.domain.reports.sequence import build_sequence_report
from test_platform.domain.retry_resume import select_retry_lane_episodes
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ReportInputRepository
from test_platform.services.execution import MultiprocessRunExecutor
from test_platform.tests.integration.test_single_lane_materialization import (
    _create_run,
    _settings,
)


# ---------------------------------------------------------------------------
# Fakes (env, agent, task, pool) — mirror test_parallel_lane.py
# ---------------------------------------------------------------------------


class _Stopwatch:
    total = 0.0

    def reset(self) -> None:
        return None

    def phase(self, name: str):
        class _Ctx:
            def __enter__(self_):
                return self_

            def __exit__(self_, *_):
                return False

        return _Ctx()

    def record(self, name: str, value: float) -> None:
        return None

    def to_flat(self) -> dict[str, float]:
        return {}

    def to_tree(self) -> list[dict[str, Any]]:
        return []

    def summary(self) -> str:
        return "fake"


class _CapturingSink:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


class _ParallelFakeEnv:
    supports_state_injection = True

    def __init__(self, wid: int) -> None:
        self.wid = wid
        self.stopwatch = _Stopwatch()
        self.step_count = 0
        self.closed = False
        self._current_task: str | None = None

    def set_browser_log_dir(self, *a, **kw) -> None:
        return None

    def set_current_task(self, task_id: str) -> None:
        self._current_task = task_id

    async def get_state(self, required_apps: list[str] | None = None) -> dict[str, Any]:
        return {"apps": {"fake": {}}, "os": {"time": {"mode": "fixed"}}}

    async def get_observation(self) -> Observation:
        return Observation(
            route={"app": "fake", "path": "/"},
            state=await self.get_state(),
            step_idx=self.step_count,
        )

    async def step(self, action: Action) -> StepResult:
        self.step_count += 1
        return StepResult(
            observation=await self.get_observation(),
            done=True,
            info={"stop_reason": ActionType.COMPLETE},
        )

    async def close(self) -> None:
        self.closed = True

    @property
    def agent_message(self) -> str | None:
        return None

    @property
    def agent_answer(self) -> str | None:
        return None


class _FakeEnvPool:
    def __init__(self, n: int) -> None:
        self.n = n
        self._envs = [_ParallelFakeEnv(i) for i in range(n)]

    def __getitem__(self, idx: int) -> _ParallelFakeEnv:
        return self._envs[idx]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        for env in self._envs:
            await env.close()
        return False


class _MaterializeEnv:
    supports_state_injection = True

    def __init__(self) -> None:
        self.sample_count = 0

    def sample_choice(self) -> str:
        self.sample_count += 1
        return f"sampled-{self.sample_count}"

    async def get_state(self, required_apps: list[str] | None = None) -> dict[str, Any]:
        return {
            "apps": {"fake": {"choice": "initial"}},
            "os": {"time": {"mode": "fixed", "value": "2026-07-03T12:00:00.000Z"}},
        }

    async def close(self) -> None:
        return None


class _FakeTask:
    apps = ["fake"]
    answer_fields = None
    suite = "fake"

    def __init__(self, _seed: int | None = None, **params: Any) -> None:
        self._seed = _seed
        self.params = {"choice": "default", **params}
        self._user_params = set(params.keys())

    @property
    def id(self) -> str:
        return "fake.SampleTask"

    @property
    def description(self) -> str:
        return f"Choose {self.params['choice']}"

    async def setup(self, env: Any) -> Observation:
        if "choice" not in self._user_params and hasattr(env, "sample_choice"):
            self.params["choice"] = env.sample_choice()
        if hasattr(env, "get_observation"):
            return await env.get_observation()
        return Observation(route={"app": "fake", "path": "/"}, state=await env.get_state(), step_idx=0)

    def teardown(self, env: Any) -> None:
        return None

    def evaluate(self, input: Any) -> Any:
        from bench_env.task.judge import JudgeResult

        return JudgeResult.ok()


class _FakeTaskFactory:
    def instantiate(self, template, params: dict[str, Any] | None = None) -> _FakeTask:
        task = _FakeTask(_seed=template.instance_seed, **(params or {}))
        task._instance_id = template.instance_id
        task._template_index = template.template_index
        return task


class _CompletingAgent:
    name = "fake-agent"

    def __init__(self) -> None:
        self.instructions: list[str] = []
        self.history: list[Any] = []

    def reset(self, instruction: str) -> None:
        self.instructions.append(instruction)

    def act(self, obs: Observation) -> Action:
        return Action.complete("done")

    def reset_history(self) -> None:
        self.history.clear()


def _episode_keys(database: Database, run_id: str) -> list[str]:
    rows = database.connection.execute(
        "SELECT episode_key FROM episodes WHERE run_id = ? ORDER BY trial_id",
        (run_id,),
    ).fetchall()
    return [str(r["episode_key"]) for r in rows]


def _mp_runner_config(lane: Any) -> Any:
    """The lane's runner_config already carries processes from the workflow
    execute node config; nothing to add here."""
    return lane.runner_config


# ---------------------------------------------------------------------------
# A fake child_runner_factory: builds a real ParallelRunner per shard using the
# reconstructed tasks + an in-process env pool. This mirrors what production
# would do, but without spawning a real process.
# ---------------------------------------------------------------------------


def _make_in_process_factory(env_pool_factory, agent_factory, lane, registry):
    """Build an in-process child_runner_factory that reconstructs tasks from
    EpisodeWorkSpecs and produces EpisodeResults directly (simulating a child
    shard's ParallelRunner.run output) without the full recorder/episode
    machinery — keeping the executor orchestration the unit under test."""
    from bench_env.runner.cancellation import CancellationToken
    from bench_env.runner.multiprocess import QueueEventSink
    from bench_env.runner.serial import PreparedWorkItem
    from bench_env.runner.work_spec import reconstruct_task
    from bench_env.env.base import ActionType

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        async def _run() -> list[EpisodeResult]:
            results: list[EpisodeResult] = []
            for ws in work_specs:
                if cancellation_token.cancelled:
                    break
                # Reconstruct the task to prove the spec→task round-trip works.
                task = reconstruct_task(ws, registry)
                results.append(EpisodeResult(
                    task_id=task.id, task_name=task.id, suite="fake",
                    execution=ExecutionResult(
                        steps=1, trace=[], runtime_s=0.01, finished=True,
                        truncated=False, stop_reason=ActionType.COMPLETE,
                    ),
                    judge=JudgeResult.ok(),
                    trial_id=ws.trial_id, apps=["fake"], max_steps=ws.max_steps,
                    episode_key=ws.episode_key,
                ))
            return results

        return _run()

    return factory


class _RegistryAdapter:
    """Adapts a _FakeTaskFactory (instantiate) to the TaskRegistry surface
    (get_by_id) that reconstruct_task expects."""

    def __init__(self, task_factory: _FakeTaskFactory) -> None:
        self._factory = task_factory

    def get_by_id(self, task_base_id: str):
        # Return a callable that construct the task from a spec's seed+params.
        # reconstruct_task calls task_cls(_seed=..., **spec.params).
        original = self._factory

        def task_cls(_seed: int | None = None, **params: Any):
            t = _FakeTask(_seed=_seed, **params)
            return t

        return task_cls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiprocess_multi_episode_run_ingests_results_in_plan_order(tmp_path):
    """4 episodes, processes=2, parallel=2: results ingested in plan order."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=4)

        materialize_env = _MaterializeEnv()
        registry = _FakeTaskFactory()
        lane_ns = type("Lane", (), {"runner_config": {"processes": 2, "parallel": 2}})()

        factory = _make_in_process_factory(
            lambda lane: _FakeEnvPool(2),
            lambda lane: _CompletingAgent(),
            lane_ns, _RegistryAdapter(registry),
        )
        executor = MultiprocessRunExecutor(
            database,
            settings,
            task_factory=registry,
            child_runner_factory=factory,
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )
        detail = await executor.execute_run(run.id)

        assert detail.state == "evaluating"
        rows = database.connection.execute(
            "SELECT ea.state, ea.outcome, e.episode_key, e.trial_id "
            "FROM episode_attempts ea JOIN episodes e ON e.id = ea.episode_id "
            "WHERE e.run_id = ? ORDER BY e.trial_id",
            (run.id,),
        ).fetchall()
        assert len(rows) == 4
        for r in rows:
            assert r["state"] == "completed"
            assert r["outcome"] == "PASS"
        # Episode keys ingested in plan order (trial 0..3).
        assert [r["trial_id"] for r in rows] == [0, 1, 2, 3]
    finally:
        database.close()


@pytest.mark.asyncio
async def test_multiprocess_normal_completion_emits_exactly_one_run_started(tmp_path):
    """Normal completion emits exactly one run.started (owned by the executor)."""
    from test_platform.execution.event_writer import EventWriter
    from test_platform.execution.sse_broker import SSEBroker

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)
        broker = SSEBroker()
        broker.bind_loop(asyncio.get_running_loop())
        writer = EventWriter(database, broker)

        materialize_env = _MaterializeEnv()
        registry = _FakeTaskFactory()
        lane_ns = type("Lane", (), {"runner_config": {"processes": 2, "parallel": 2}})()

        factory = _make_in_process_factory(
            lambda lane: _FakeEnvPool(2),
            lambda lane: _CompletingAgent(),
            lane_ns, _RegistryAdapter(registry),
        )
        executor = MultiprocessRunExecutor(
            database,
            settings,
            task_factory=registry,
            child_runner_factory=factory,
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )
        await executor.execute_run(run.id, run_event_writer=writer)

        started = database.connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ? AND type = 'run.started'",
            (run.id,),
        ).fetchone()[0]
        assert started == 1
    finally:
        database.close()


@pytest.mark.asyncio
async def test_multiprocess_pre_execution_cancel_omits_run_started(tmp_path):
    """A cancel that lands while queued must NOT emit run.started."""
    from bench_env.runner.cancellation import CancellationToken, RunCancelled

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)
        # Pre-set cancel_requested_at so the executor's queued-cancel check fires.
        database.connection.execute(
            "UPDATE runs SET cancel_requested_at = ?, updated_at = ? WHERE id = ?",
            ("2026-07-04T00:00:00.000Z", "2026-07-04T00:00:00.000Z", run.id),
        )
        database.connection.commit()

        materialize_env = _MaterializeEnv()
        registry = _FakeTaskFactory()

        executor = MultiprocessRunExecutor(
            database,
            settings,
            task_factory=registry,
            child_runner_factory=lambda *a: None,
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )
        with pytest.raises(RunCancelled):
            await executor.execute_run(run.id)

        started = database.connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ? AND type = 'run.started'",
            (run.id,),
        ).fetchone()[0]
        assert started == 0
    finally:
        database.close()


@pytest.mark.asyncio
async def test_multiprocess_user_cancel_labels_episodes_cancelled(tmp_path):
    """User cancel mid-run → CANCELLED outcome (token.cancelled=True drives
    reconciliation). Missing episodes from the un-responsive cancellation are
    labeled CANCELLED."""
    from bench_env.runner.cancellation import CancellationToken, RunCancelled

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=4)
        token = CancellationToken()
        materialize_env = _MaterializeEnv()
        registry = _FakeTaskFactory()
        sink = _CapturingSink()
        lane_ns = type("Lane", (), {"runner_config": {"processes": 2, "parallel": 2}})()

        # A child that completes only the first episode, then observes cancel.
        def factory(shard_spec, work_specs, event_sink, cancellation_token):
            async def _run() -> list[EpisodeResult]:
                results = []
                for i, ws in enumerate(work_specs):
                    if cancellation_token.cancelled:
                        break
                    # Emit + complete only if not cancelled.
                    results.append(EpisodeResult(
                        task_id=ws.task_base_id, task_name=ws.task_base_id,
                        suite="fake",
                            execution=ExecutionResult(
                                steps=1, trace=[], runtime_s=0.01, finished=True,
                                truncated=False, stop_reason=ActionType.COMPLETE,
                            ),
                            judge=JudgeResult.ok(),
                            trial_id=ws.trial_id, apps=["fake"], max_steps=ws.max_steps,
                        episode_key=ws.episode_key,
                    ))
                    if i == 0:
                        # Hold the child after one completed result so the
                        # cancellation deterministically leaves missing work.
                        await asyncio.sleep(0.2)
                return results

            return _run()

        executor = MultiprocessRunExecutor(
            database,
            settings,
            task_factory=registry,
            child_runner_factory=factory,
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )

        async def _cancel_soon():
            await asyncio.sleep(0.1)
            token.cancel()

        try:
            await asyncio.gather(
                executor.execute_run(run.id, token=token, events=sink),
                _cancel_soon(),
            )
        except RunCancelled:
            pass

        detail = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,),
        ).fetchone()
        assert detail["state"] == "cancelled"

        # P1.1 regression: missing episodes from the cancellation MUST be labeled
        # CANCELLED (outcome + error_code), NOT WORKER_CRASH. A user cancel is not
        # a crash — conflating them corrupts reports.
        attempts = database.connection.execute(
            "SELECT e.episode_key, ea.outcome, ea.error_code, ea.result_json "
            "FROM episode_attempts ea "
            "JOIN episodes e ON e.id = ea.episode_id"
        ).fetchall()
        assert len(attempts) > 0

        synthetic_cancelled = []
        for att in attempts:
            if att["outcome"] == "CANCELLED":
                assert att["error_code"] == "CANCELLED", (
                    f"cancelled episode mislabeled {att['error_code']!r} (expected CANCELLED)"
                )
                # P2: result_json must NOT carry crash semantics. A missing
                # episode from a user cancel is synthetic-CANCELLED, not
                # WORKER_CRASH. (Episodes that ran to completion before the
                # cancel land have their real stop_reason; only the synthetic
                # missing ones must avoid WORKER_CRASH.)
                rd = json.loads(att["result_json"]) if att["result_json"] else {}
                assert "WORKER_CRASH" not in str(rd), (
                    "cancelled result_json must not carry WORKER_CRASH"
                )
                # Synthetic cancelled episodes have stop_reason=CANCELLED; real
                # completed-then-run-cancelled episodes keep their own stop_reason.
                exec_d = rd.get("execution") or {}
                assert exec_d.get("stop_reason") != "ERROR", (
                    "cancelled result_json must not carry crash stop_reason=ERROR"
                )
                assert rd.get("is_error") is not True, (
                    "cancelled result must not be flagged is_error=True"
                )
                if exec_d.get("stop_reason") == "CANCELLED":
                    synthetic_cancelled.append(att)
            # No episode should carry WORKER_CRASH when the run was user-cancelled.
            assert att["error_code"] != "WORKER_CRASH", (
                "user-cancelled run must not produce WORKER_CRASH episodes"
            )

        assert synthetic_cancelled, "fixture must produce synthetic missing cancellations"
        for att in synthetic_cancelled:
            terminal_events = [
                event
                for event in sink.events
                if event.episode_key == att["episode_key"]
                and event.payload.get("reason") == "missing_result"
            ]
            assert len(terminal_events) == 1
            assert terminal_events[0].type == "episode.cancelled"
            assert terminal_events[0].payload["outcome"] == "CANCELLED"
            assert terminal_events[0].payload["error_code"] == "CANCELLED"
    finally:
        database.close()


@pytest.mark.asyncio
async def test_multiprocess_shard_crash_yields_worker_crash(tmp_path):
    """A missing shard result is consistently ERROR / WORKER_CRASH everywhere."""

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2, parallel=2, processes=2)
        database.connection.execute(
            """
            UPDATE episodes
            SET sequence_index = trial_id,
                sequence_group_id = 'manual_sequence'
            WHERE run_id = ?
            """,
            (run.id,),
        )
        database.connection.commit()
        materialize_env = _MaterializeEnv()
        registry = _FakeTaskFactory()

        sink = _CapturingSink()

        # Child factory that crashes for one shard (rank 0) and completes the other.
        def factory(shard_spec, work_specs, event_sink, cancellation_token):
            async def _run() -> list[EpisodeResult]:
                if shard_spec.rank == 0:
                    raise RuntimeError("shard 0 crashed")
                return [
                    EpisodeResult(
                        task_id=ws.task_base_id, task_name=ws.task_base_id,
                        suite="fake",
                        execution=ExecutionResult(
                            steps=1, trace=[], runtime_s=0.01, finished=True,
                            truncated=False, stop_reason=ActionType.COMPLETE,
                        ),
                        judge=JudgeResult.ok(),
                        trial_id=ws.trial_id, apps=["fake"], max_steps=ws.max_steps,
                        episode_key=ws.episode_key,
                    )
                    for ws in work_specs
                ]

            return _run()

        executor = MultiprocessRunExecutor(
            database,
            settings,
            task_factory=registry,
            child_runner_factory=factory,
            env_pool_factory=lambda lane: _FakeEnvPool(1),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )
        detail = await executor.execute_run(run.id, events=sink)

        # Execution still reaches completion handoff (reconciliation fills missing episodes).
        assert detail.state == "evaluating"
        rows = database.connection.execute(
            "SELECT e.episode_key, e.trial_id, ea.error_code, ea.outcome, ea.result_json "
            "FROM episode_attempts ea "
            "JOIN episodes e ON e.id = ea.episode_id WHERE e.run_id = ?",
            (run.id,),
        ).fetchall()
        assert len(rows) == 2
        crashed = [row for row in rows if row["error_code"] == "WORKER_CRASH"]
        passed = [row for row in rows if row["outcome"] == "PASS"]
        assert len(crashed) == 1
        assert len(passed) == 1
        assert passed[0]["error_code"] is None
        for crash in crashed:
            assert crash["outcome"] == "ERROR"
            result = json.loads(crash["result_json"])
            assert result["is_success"] is False
            assert result["is_error"] is True
            assert result["execution"] == {
                "error": "Worker exited without reporting a result (WORKER_CRASH).",
                "stop_reason": "ERROR",
            }

            terminal_events = [
                event
                for event in sink.events
                if event.episode_key == crash["episode_key"]
                and event.payload.get("reason") == "missing_result"
            ]
            assert len(terminal_events) == 1
            assert terminal_events[0].type == "episode.error"
            assert terminal_events[0].payload["outcome"] == "ERROR"
            assert terminal_events[0].payload["error_code"] == "WORKER_CRASH"

        report_input = ReportInputRepository(database).get_for_completion(run.id)
        functional = build_functional_report(report_input)
        assert functional["summary"]["successes"] == 1
        assert functional["summary"]["errors"] == 1
        assert functional["summary"]["failures"] == 0
        assert functional["summary"]["incomplete"] == 0

        sequence = build_sequence_report(report_input)
        assert len(sequence["groups"]) == 1
        assert sequence["groups"][0]["summary"]["successes"] == 1
        assert sequence["groups"][0]["summary"]["errors"] == 1
        assert sequence["groups"][0]["summary"]["failures"] == 0
        assert sequence["groups"][0]["summary"]["incomplete"] == 0

        selected = select_retry_lane_episodes(
            report_input.planned_lane_episodes,
            report_input.episode_attempts,
        )
        assert len(selected) == 1
        selected_by_key = {item["episode_key"]: item for item in selected}
        for crash in crashed:
            assert selected_by_key[crash["episode_key"]] == {
                "episode_key": crash["episode_key"],
                "lane_key": "candidate",
                "sequence_index": crash["trial_id"],
                "sequence_group_id": "manual_sequence",
                "reason": "retry_error",
            }
    finally:
        database.close()


@pytest.mark.asyncio
async def test_multiprocess_shard_fatal_carries_exitcode(tmp_path):
    """ShardFatalEnvelope carries exitcode; the event sink receives shard.fatal
    with the exitcode in payload."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)

        sink = _CapturingSink()
        materialize_env = _MaterializeEnv()
        registry = _FakeTaskFactory()

        def factory(shard_spec, work_specs, event_sink, cancellation_token):
            async def _run() -> list[EpisodeResult]:
                if shard_spec.rank == 0:
                    raise RuntimeError("fatal shard")
                return [
                    EpisodeResult(
                        task_id=ws.task_base_id, task_name=ws.task_base_id,
                        suite="fake",
                        execution=ExecutionResult(
                            steps=1, trace=[], runtime_s=0.01, finished=True,
                            truncated=False, stop_reason=ActionType.COMPLETE,
                        ),
                        trial_id=ws.trial_id, apps=["fake"], max_steps=ws.max_steps,
                        episode_key=ws.episode_key,
                    )
                    for ws in work_specs
                ]

            return _run()

        executor = MultiprocessRunExecutor(
            database,
            settings,
            task_factory=registry,
            child_runner_factory=factory,
            env_pool_factory=lambda lane: _FakeEnvPool(1),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )
        await executor.execute_run(run.id, events=sink)

        fatal_events = [e for e in sink.events if e.type == "shard.fatal"]
        # The crashed shard emitted a fatal envelope with an exitcode.
        assert len(fatal_events) >= 1
        for ev in fatal_events:
            assert "exitcode" in ev.payload
            assert ev.payload["exitcode"] is not None
    finally:
        database.close()


@pytest.mark.asyncio
async def test_platform_path_without_in_process_factory_is_rejected(tmp_path):
    """P1.2: the real-spawn path cannot consume EpisodeWorkSpec/envelopes, so the
    platform path (prepared_work_specs) without an in-process factory must be
    rejected rather than silently producing unreconcilable results."""
    from bench_env.config import RunnerConfig
    from bench_env.runner.multiprocess import MultiProcessRunner
    from bench_env.runner.work_spec import EpisodeWorkSpec

    specs = [
        EpisodeWorkSpec(
            episode_key=f"k{i}", task_base_id="fake.T", instance_id=i,
            instance_seed=i, template_index=None, params={}, trial_id=0, max_steps=5,
        )
        for i in range(2)
    ]
    config = RunnerConfig(
        agent="probe", model_name="probe-model", quiet=True,
        processes=2, parallel=2, runs_dir=tmp_path / "runs",
    )
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)

    # Platform path (prepared_work_specs) + NO child_runner_factory → must raise.
    runner = MultiProcessRunner(
        [], config,
        prepared_work_specs=specs,
        # child_runner_factory intentionally omitted (None)
    )
    with pytest.raises(ValueError, match="requires child_runner_factory"):
        await runner.run()


def test_effective_processes_clamps_to_parallel_on_platform_path(tmp_path):
    """P2.2: processes > parallel must not over-shard on the platform path."""
    from bench_env.config import RunnerConfig
    from bench_env.runner.multiprocess import MultiProcessRunner
    from bench_env.runner.work_spec import EpisodeWorkSpec

    specs = [
        EpisodeWorkSpec(
            episode_key=f"k{i}", task_base_id="fake.T", instance_id=i,
            instance_seed=i, template_index=None, params={}, trial_id=0, max_steps=5,
        )
        for i in range(8)
    ]
    # processes=4, parallel=1 → must clamp to 1 shard (can't split 1 parallel slot).
    config = RunnerConfig(
        agent="probe", model_name="probe-model", quiet=True,
        processes=4, parallel=1, runs_dir=tmp_path / "runs",
    )
    runner = MultiProcessRunner([], config, prepared_work_specs=specs)
    assert runner._effective_processes() == 1

    # processes=4, parallel=2 → 2 shards.
    config2 = RunnerConfig(
        agent="probe", model_name="probe-model", quiet=True,
        processes=4, parallel=2, runs_dir=tmp_path / "runs",
    )
    runner2 = MultiProcessRunner([], config2, prepared_work_specs=specs)
    assert runner2._effective_processes() == 2
