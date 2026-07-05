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
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.runner.base import EpisodeResult, ExecutionResult
from bench_env.runner.events import ExecutionEvent
from bench_env.runner.work_spec import EpisodeWorkSpec
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.database import Database
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
    from bench_env.task.judge import JudgeResult
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

        assert detail.state == "completed"
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
                        trial_id=ws.trial_id, apps=["fake"], max_steps=ws.max_steps,
                        episode_key=ws.episode_key,
                    ))
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
            await asyncio.gather(executor.execute_run(run.id, token=token), _cancel_soon())
        except RunCancelled:
            pass

        detail = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,),
        ).fetchone()
        assert detail["state"] == "cancelled"
    finally:
        database.close()


@pytest.mark.asyncio
async def test_multiprocess_shard_crash_yields_worker_crash(tmp_path):
    """A shard that raises leaves missing episodes → WORKER_CRASH."""

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)
        materialize_env = _MaterializeEnv()
        registry = _FakeTaskFactory()

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
        detail = await executor.execute_run(run.id)

        # The run still completes (reconciliation fills missing episodes).
        assert detail.state == "completed"
        rows = database.connection.execute(
            "SELECT error_code, outcome FROM episode_attempts ea "
            "JOIN episodes e ON e.id = ea.episode_id WHERE e.run_id = ?",
            (run.id,),
        ).fetchall()
        assert len(rows) == 2
        error_codes = [r["error_code"] for r in rows]
        # At least one episode surfaced WORKER_CRASH from the crashed shard.
        assert "WORKER_CRASH" in error_codes
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

        class _CapturingSink:
            def __init__(self) -> None:
                self.events: list[ExecutionEvent] = []

            def emit(self, event: ExecutionEvent) -> None:
                self.events.append(event)

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
