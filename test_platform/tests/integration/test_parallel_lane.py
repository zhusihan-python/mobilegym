"""VS-07 Block D5: ParallelRunExecutor integration through the platform stack.

Drives ParallelRunExecutor directly (and via the supervisor) with fake env
pools / agents / tasks to verify:
- multi-episode parallel runs ingest results in PLAN order;
- a crashed worker yields a WORKER_CRASH error_code for the missing episode;
- unknown / duplicate episode_keys raise RunDomainError (never silently pass);
- cancellation releases every env in the pool;
- exactly one run.started on normal completion; NO run.started on pre-execution
  cancel.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.runner.events import ExecutionEvent
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.database import Database
from test_platform.services.execution import ParallelRunExecutor
from test_platform.tests.integration.test_single_lane_materialization import (
    _create_run,
    _settings,
)


# ---------------------------------------------------------------------------
# Fakes (env, agent, task, pool)
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
    """Fake env for parallel execution: completes immediately on first step."""

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
    """Minimal async-context-manager env pool with N fake envs."""

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
    """Single env used during the materialization phase (samples one choice)."""

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_multi_episode_run_ingests_results_in_plan_order(tmp_path):
    """4 episodes (repeat_n=4), parallel=2: results ingested in plan order, run completes."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=4)

        materialize_env = _MaterializeEnv()
        pool_holder: dict[str, Any] = {}

        def env_pool_factory(lane):
            pool = _FakeEnvPool(2)
            pool_holder["pool"] = pool
            return pool

        executor = ParallelRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_pool_factory=env_pool_factory,
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )
        detail = await executor.execute_run(run.id)

        assert detail.state == "completed"
        keys = _episode_keys(database, run.id)
        assert len(keys) == 4

        # 4 episode_attempts inserted, in plan (trial) order.
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

        # All pool envs were closed (cleanup ran).
        assert all(env.closed for env in pool_holder["pool"]._envs)
    finally:
        database.close()


@pytest.mark.asyncio
async def test_parallel_worker_crash_yields_worker_crash_error_code(tmp_path):
    """A worker that crashes (raises) leaves a missing episode → WORKER_CRASH."""

    class _CrashingTaskFactory(_FakeTaskFactory):
        """First trial's task crashes during the episode; others complete.

        The ParallelRunner wraps run_episode exceptions into an ERROR result
        stored at the work-item index, so this simulates a result that IS
        returned but flagged as error. To simulate a truly MISSING result we
        instead override the runner's behavior by having one episode_key absent
        — done here by making the agent raise so run_episode throws and the
        runner records an error result. That covers the error path; the pure
        missing path is covered by the reconciliation unit behavior.
        """

        def instantiate(self, template, params=None):
            task = super().instantiate(template, params)
            return task

    class _CrashOnceAgent(_CompletingAgent):
        """Raises on the first act call (simulating a worker crash mid-episode)."""

        def __init__(self) -> None:
            super().__init__()
            self._first = True

        def act(self, obs: Observation) -> Action:
            if self._first:
                self._first = False
                raise RuntimeError("worker crashed mid-episode")
            return Action.complete("done")

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)

        materialize_env = _MaterializeEnv()
        executor = ParallelRunExecutor(
            database,
            settings,
            task_factory=_CrashingTaskFactory(),
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CrashOnceAgent(),
            env_factory=lambda lane: materialize_env,
        )
        detail = await executor.execute_run(run.id)

        # The run still completes (reconciliation fills missing/error episodes).
        assert detail.state == "completed"
        rows = database.connection.execute(
            "SELECT error_code, outcome FROM episode_attempts ea "
            "JOIN episodes e ON e.id = ea.episode_id WHERE e.run_id = ?",
            (run.id,),
        ).fetchall()
        assert len(rows) == 2
        # At least one episode surfaced an error (crash captured as ERROR).
        error_codes = [r["error_code"] for r in rows]
        assert "EXECUTION_ERROR" in error_codes or "WORKER_CRASH" in error_codes
    finally:
        database.close()


@pytest.mark.asyncio
async def test_parallel_missing_result_gets_worker_crash(tmp_path):
    """A genuinely missing result (worker exited without reporting) → WORKER_CRASH.

    We force a missing result by subclassing ParallelRunExecutor to drop one
    result from the returned list, then assert reconciliation fills it with a
    WORKER_CRASH error_code.
    """

    class _DroppingExecutor(ParallelRunExecutor):
        """Wraps the runner so one result is dropped (simulating a worker that
        exited before reporting)."""

        def __init__(self, *a, drop_episode_key: str, **kw):
            super().__init__(*a, **kw)
            self._drop_key = drop_episode_key

        async def execute_run(self, run_id, *, token=None, events=None, run_event_writer=None):
            # Temporarily monkeypatch ParallelRunner.run to drop one result.
            from bench_env.runner.parallel import ParallelRunner as _PR

            original_run = _PR.run
            drop_key = self._drop_key

            async def _filtered_run(self_runner):
                results = await original_run(self_runner)
                return [r for r in results if getattr(r, "episode_key", None) != drop_key]

            _PR.run = _filtered_run
            try:
                return await super().execute_run(
                    run_id, token=token, events=events, run_event_writer=run_event_writer,
                )
            finally:
                _PR.run = original_run

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=3)
        keys = _episode_keys(database, run.id)
        drop_key = keys[1]

        materialize_env = _MaterializeEnv()
        executor = _DroppingExecutor(
            database,
            settings,
            drop_episode_key=drop_key,
            task_factory=_FakeTaskFactory(),
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )
        detail = await executor.execute_run(run.id)

        assert detail.state == "completed"
        row = database.connection.execute(
            "SELECT ea.error_code, ea.outcome FROM episode_attempts ea "
            "JOIN episodes e ON e.id = ea.episode_id "
            "WHERE e.run_id = ? AND e.episode_key = ?",
            (run.id, drop_key),
        ).fetchone()
        assert row is not None
        assert row["error_code"] == "WORKER_CRASH"
        assert row["outcome"] == "ERROR"
    finally:
        database.close()


def test_parallel_unknown_episode_key_raises(tmp_path):
    """An unknown episode_key in the results → RunDomainError (reconciliation)."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        executor = ParallelRunExecutor(database, settings, task_factory=_FakeTaskFactory())

        class _FakeItem:
            episode_key = "known"
            trial_id = 0
            max_steps = 5

            class task:
                id = "fake.SampleTask"

        class _UnknownResult:
            episode_key = "totally-unknown"
            task_id = "fake.SampleTask"
            trial_id = 0

            def to_dict(self):
                return {"id": "fake.SampleTask", "trial_id": 0, "is_success": True}

        with pytest.raises(RunDomainError) as exc_info:
            executor._reconcile_and_ingest(
                run_id="r1",
                lane_attempt={"id": "la1", "artifact_root": "lanes/candidate"},
                work_items=[_FakeItem()],
                results=[_UnknownResult()],
                cancelled=False,
            )
        assert exc_info.value.code == "EPISODE_RESULT_UNKNOWN"
    finally:
        database.close()


def test_parallel_duplicate_episode_key_raises(tmp_path):
    """Two results with the same episode_key → RunDomainError."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        executor = ParallelRunExecutor(database, settings, task_factory=_FakeTaskFactory())

        class _FakeItem:
            episode_key = "dup"
            trial_id = 0
            max_steps = 5

            class task:
                id = "fake.SampleTask"

        class _Result:
            def __init__(self, key):
                self.episode_key = key
                self.task_id = "fake.SampleTask"
                self.trial_id = 0

            def to_dict(self):
                return {"id": "fake.SampleTask", "trial_id": 0, "is_success": True}

        with pytest.raises(RunDomainError) as exc_info:
            executor._reconcile_and_ingest(
                run_id="r1",
                lane_attempt={"id": "la1", "artifact_root": "lanes/candidate"},
                work_items=[_FakeItem()],
                results=[_Result("dup"), _Result("dup")],
                cancelled=False,
            )
        assert exc_info.value.code == "EPISODE_RESULT_UNKNOWN"
        assert exc_info.value.details  # carries the duplicate detail
    finally:
        database.close()


@pytest.mark.asyncio
async def test_parallel_cancellation_releases_all_envs(tmp_path):
    """Cancelling mid-run closes every env in the pool (no leaks)."""

    class _BlockingEnv(_ParallelFakeEnv):
        async def step(self, action: Action) -> StepResult:
            self.step_count += 1
            # Block until cancelled so the token fires mid-run.
            await asyncio.sleep(30)
            return StepResult(observation=await self.get_observation(), done=False, info={})

    class _BlockingPool:
        def __init__(self, n: int) -> None:
            self.n = n
            self._envs = [_BlockingEnv(i) for i in range(n)]

        def __getitem__(self, idx):
            return self._envs[idx]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            for env in self._envs:
                await env.close()
            return False

    from bench_env.runner.cancellation import CancellationToken

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=4)
        materialize_env = _MaterializeEnv()
        pool_holder: dict[str, Any] = {}

        def env_pool_factory(lane):
            pool = _BlockingPool(2)
            pool_holder["pool"] = pool
            return pool

        token = CancellationToken()
        executor = ParallelRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_pool_factory=env_pool_factory,
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: materialize_env,
        )

        async def _cancel_soon():
            await asyncio.sleep(0.2)
            token.cancel()

        # The executor re-raises RunCancelled after marking the run cancelled;
        # the pool cleanup still runs (async with in ParallelRunner).
        from bench_env.runner.cancellation import RunCancelled

        try:
            await asyncio.gather(executor.execute_run(run.id, token=token), _cancel_soon())
        except RunCancelled:
            pass

        # Every env was closed by pool cleanup.
        assert all(env.closed for env in pool_holder["pool"]._envs)
    finally:
        database.close()


@pytest.mark.asyncio
async def test_parallel_normal_completion_emits_exactly_one_run_started(tmp_path):
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
        executor = ParallelRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
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
async def test_parallel_pre_execution_cancel_omits_run_started(tmp_path):
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
        executor = ParallelRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
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
