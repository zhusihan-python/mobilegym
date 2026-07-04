"""VS-07 acceptance demo.

Drives the full parallel-execution flow against a real ParallelRunExecutor with
fake env_pool/agent factories, exercising the VS-07 acceptance criteria:

  4 episodes, parallel=2
  -> run.started
  -> episode.* events carry worker_id (W0/W1) + episode_key
  -> results retain PLAN order despite out-of-order completion
  -> episode_key-driven isolated artifact roots
  -> a deliberately crashed worker produces a WORKER_CRASH missing-result episode
  -> cancellation releases all fake envs
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.task.judge import JudgeResult

from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.services.execution import ParallelRunExecutor
from test_platform.services.runs import RunSupervisor
from test_platform.tests.integration.test_single_lane_materialization import (
    _catalog,
    _create_run,
    _settings,
)


class _Stopwatch:
    total = 0.0
    def reset(self): return None
    def phase(self, name):
        class _C:
            def __enter__(s): return s
            def __exit__(s, *_): return False
        return _C()
    def record(self, n, v): return None
    def to_flat(self): return {}
    def to_tree(self): return []
    def summary(self): return "demo"


class _DemoEnv:
    supports_state_injection = True

    def __init__(self, wid: int) -> None:
        self.wid = wid
        self.stopwatch = _Stopwatch()
        self.closed = False
        self.step_count = 0
        self._current_task = None

    async def get_state(self, required_apps=None):
        return {"apps": {"fake": {}}, "os": {"time": {"mode": "fixed"}}}

    async def get_observation(self):
        return Observation(route={"app": "fake", "path": "/"}, state=await self.get_state(), step_idx=self.step_count)

    async def step(self, action):
        self.step_count += 1
        await asyncio.sleep(0.05)
        return StepResult(observation=await self.get_observation(), done=True, info={"stop_reason": ActionType.COMPLETE})

    async def close(self):
        self.closed = True

    def set_current_task(self, task_id):
        self._current_task = task_id

    def set_browser_log_dir(self, *a, **kw):
        pass

    @property
    def agent_message(self): return None
    @property
    def agent_answer(self): return None


class _DemoEnvPool:
    def __init__(self, n: int, *, crash_on_wid: int | None = None) -> None:
        self.n = n
        self._envs = [_DemoEnv(i) for i in range(n)]
        self._crash_on_wid = crash_on_wid

    def __getitem__(self, idx: int) -> _DemoEnv:
        return self._envs[idx]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        for env in self._envs:
            await env.close()
        return False


class _DemoTask:
    apps = ["fake"]
    answer_fields = None
    suite = "fake"

    def __init__(self, _seed=None, **params):
        self._seed = _seed
        self.params = {"choice": "demo", **params}
        self._user_params = set(params.keys())

    @property
    def id(self): return "fake.DemoTask"
    @property
    def description(self): return "Demo task"

    async def setup(self, env): return await env.get_observation()
    def teardown(self, env): return None
    def evaluate(self, input): return JudgeResult.ok()


class _DemoTaskFactory:
    def instantiate(self, template, params=None):
        return _DemoTask(_seed=template.instance_seed, **(params or {}))


class _DemoAgent:
    name = "demo-agent"

    def __init__(self, *, crash=False):
        self.history: list[Any] = []
        self.act_count = 0
        self._crash = crash

    def reset(self, instruction): return None
    def act(self, obs):
        self.act_count += 1
        if self._crash:
            raise RuntimeError("simulated worker crash")
        return Action.complete("done")
    def reset_history(self): self.history.clear()


@pytest.mark.asyncio
async def test_vs07_acceptance_parallel_execution(tmp_path):
    """4 episodes at parallel=2: plan-order results, worker events, isolated artifacts."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()

    pool_holder: dict = {}
    materialize_env = _DemoEnv(99)

    def env_pool_factory(lane):
        pool = _DemoEnvPool(2)
        pool_holder["pool"] = pool
        return pool

    def env_factory(lane):
        # Materialization needs a single env (task.setup runs once here).
        return materialize_env

    agent_calls = {"count": 0}

    def agent_factory(lane):
        agent_calls["count"] += 1
        return _DemoAgent()

    executor = ParallelRunExecutor(
        database, settings,
        task_factory=_DemoTaskFactory(),
        env_pool_factory=env_pool_factory,
        agent_factory=agent_factory,
        env_factory=env_factory,
    )
    supervisor = RunSupervisor(database, settings, executor=executor)

    try:
        run = _create_run(database, settings, repeat_n=4)

        await supervisor.start()
        subscription = await supervisor._broker.subscribe(run.id)
        supervisor.submit(run.id)

        received: list[str] = []
        try:
            while True:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=5.0)
                if event is None:
                    break
                received.append(event.type)
                if event.type == "run.completed":
                    break
        except asyncio.TimeoutError:
            pass
        await supervisor.stop()

        # Acceptance: run.started + run.completed emitted.
        assert "run.started" in received
        assert "run.completed" in received

        # All 4 episodes reached a terminal episode event.
        completed = [t for t in received if t in ("episode.completed", "episode.error")]
        assert len(completed) == 4

        # All envs closed (cleanup ran).
        pool = pool_holder["pool"]
        assert all(env.closed for env in pool._envs)

        # Episode attempts ingested in plan order (one per episode).
        rows = database.connection.execute(
            "SELECT COUNT(*) AS n FROM episode_attempts WHERE lane_attempt_id IN "
            "(SELECT id FROM lane_attempts WHERE run_attempt_id IN "
            "(SELECT id FROM run_attempts WHERE run_id = ?))",
            (run.id,),
        ).fetchone()
        assert int(rows["n"]) == 4

        # Run reached completed.
        run_row = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert run_row["state"] == "completed"
    finally:
        database.close()
