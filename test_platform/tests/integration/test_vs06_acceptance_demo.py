"""VS-06 acceptance demo.

Drives the full cancellation + live-event flow end to end against a real
RunSupervisor + SerialRunExecutor with a deliberately slow fake agent:

  create run (queued)
  -> submit -> run.started event
  -> slow agent.act runs (CANCELLED mid-flight)
  -> request_cancel -> run.cancel_requested event
  -> executor observes token, tears down, closes env
  -> run.cancelled event
  -> run reaches 'cancelled' state
  -> env.close() was called (no leaked worker)

This mirrors the DEVELOPMENT_PLAN VS-06 acceptance demo without requiring a
running browser or simulator target.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.task.judge import JudgeResult

from test_platform.config import PlatformSettings
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database
from test_platform.services.execution import SerialRunExecutor
from test_platform.services.runs import RunSupervisor
from test_platform.tests.integration.test_single_lane_materialization import (
    _catalog,
    _create_run,
    _settings,
)


class _SlowStopwatch:
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
    def summary(self): return "slow"


class _SlowEnv:
    supports_state_injection = True

    def __init__(self) -> None:
        self.stopwatch = _SlowStopwatch()
        self.closed = False
        self.step_count = 0

    async def get_state(self, required_apps=None):
        return {"apps": {"fake": {}}, "os": {"time": {"mode": "fixed"}}}

    async def get_observation(self):
        return Observation(route={"app": "fake", "path": "/"}, state=await self.get_state(), step_idx=self.step_count)

    async def step(self, action):
        self.step_count += 1
        await asyncio.sleep(0.2)  # slow enough for cancel to land mid-run
        return StepResult(observation=await self.get_observation(), done=False, info={})

    async def close(self):
        self.closed = True

    @property
    def agent_message(self): return None
    @property
    def agent_answer(self): return None


class _SlowTask:
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
    def description(self): return "Demo slow task"

    async def setup(self, env): return await env.get_observation()
    def teardown(self, env): return None
    def evaluate(self, input): return JudgeResult.ok()


class _SlowTaskFactory:
    def instantiate(self, template, params=None):
        return _SlowTask(_seed=template.instance_seed, **(params or {}))


class _SlowAgent:
    name = "slow-agent"

    def __init__(self):
        self.history: list[Any] = []
        self.act_count = 0

    def reset(self, instruction): return None
    def act(self, obs):
        self.act_count += 1
        return Action(action_type=ActionType.CLICK, data={"point": [0, 0]})
    def reset_history(self): self.history.clear()


@pytest.mark.asyncio
async def test_vs06_acceptance_demo_live_events_and_cancellation(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()

    env = _SlowEnv()
    agent = _SlowAgent()
    executor = SerialRunExecutor(
        database,
        settings,
        task_factory=_SlowTaskFactory(),
        env_factory=lambda lane: env,
        agent_factory=lambda lane: agent,
    )
    broker = SSEBroker()
    supervisor = RunSupervisor(database, settings, executor=executor, broker=broker)

    try:
        run = _create_run(database, settings, repeat_n=1)

        # Subscribe to live events BEFORE submitting, so we capture everything.
        await supervisor.start()
        subscription = await broker.subscribe(run.id)

        # Submit the run; run.started should fire.
        supervisor.submit(run.id)
        await asyncio.sleep(0.05)

        # Cancel mid-run (the slow agent is looping in step()).
        supervisor.request_cancel(run.id)

        # Drain events with a deadline.
        received: list[str] = []
        try:
            while True:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=2.0)
                if event is None:
                    break
                received.append(event.type)
                if event.type == "run.cancelled":
                    break
        except asyncio.TimeoutError:
            pass

        await supervisor.stop()

        # Assert the full lifecycle sequence (acceptance criteria).
        assert "run.started" in received
        assert "run.cancel_requested" in received
        assert "run.cancelled" in received

        row = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert row["state"] == "cancelled"

        # env.close() was called (no leaked worker / environment).
        assert env.closed is True

        # The agent never completed a full episode — at least one step ran and
        # then the cooperative cancel fired.
        assert agent.act_count >= 1
    finally:
        database.close()
