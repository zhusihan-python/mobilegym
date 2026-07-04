from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.runner.base import BaseRunner, Controller, Evaluator
from bench_env.runner.cancellation import CancellationToken


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class _Stopwatch:
    def __init__(self) -> None:
        self.total = 0.0

    def reset(self) -> None:
        return None

    def phase(self, name: str):
        @dataclass
        class _Ctx:
            def __enter__(self_):
                return self_

            def __exit__(self_, *exc):
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


class _FakeEnv:
    """Minimal env: records step/close/teardown calls and yields observations."""

    supports_state_injection = True

    def __init__(self, *, cancel_during_act: bool = False) -> None:
        self.stopwatch = _Stopwatch()
        self.step_count = 0
        self.closed = False
        self.cancel_during_act = cancel_during_act
        self._agent_message: str | None = None
        self._agent_answer: str | None = None

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
        return StepResult(observation=await self.get_observation(), done=False, info={})

    async def close(self) -> None:
        self.closed = True

    @property
    def agent_message(self) -> str | None:
        return self._agent_message

    @property
    def agent_answer(self) -> str | None:
        return self._agent_answer


class _FakeTask:
    apps = ["fake"]
    answer_fields = None
    suite = "fake"
    task_name = "fake task"

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self._seed = 1
        self._instance_id = 0
        self._template_index = 0
        self.teardown_count = 0

    @property
    def id(self) -> str:
        return "fake.Task"

    @property
    def description(self) -> str:
        return "fake task"

    async def setup(self, env: Any) -> Observation:
        return await env.get_observation()

    def teardown(self, env: Any) -> None:
        self.teardown_count += 1

    def evaluate(self, input: Any) -> Any:
        from bench_env.task.judge import JudgeResult

        return JudgeResult.ok()


class _FakeAgent:
    name = "fake-agent"

    def __init__(self, *, env: _FakeEnv, token: CancellationToken | None = None) -> None:
        self.history: list[Any] = []
        self.act_count = 0
        self._env = env
        self._token = token

    def reset(self, instruction: str) -> None:
        return None

    def act(self, obs: Observation) -> Action:
        self.act_count += 1
        # Optionally trigger cancellation during the model call — emulates a user
        # cancelling while inference is in flight.
        if self._env.cancel_during_act and self._token is not None:
            self._token.cancel()
        return Action(action_type=ActionType.CLICK, data={"point": [0, 0]})

    def reset_history(self) -> None:
        self.history.clear()


class _NoEvaluator(Evaluator):
    """Evaluator that records whether it was invoked."""

    def __init__(self) -> None:
        super().__init__(eval_mode="text")
        self.invoked = False

    async def evaluate(self, task, init_obs, last_obs, exec_result, episode=None):  # type: ignore[override]
        self.invoked = True
        from bench_env.task.judge import JudgeResult

        return JudgeResult.ok()


@pytest.mark.asyncio
async def test_cancellation_before_setup_returns_cancelled_not_error() -> None:
    """Cancelling before setup must prevent agent.act and yield CANCELLED."""
    env = _FakeEnv()
    task = _FakeTask()
    agent = _FakeAgent(env=env)
    token = CancellationToken()
    token.cancel()  # cancelled before anything runs

    exec_result, init_obs, last_obs, episode, returned_task = await Controller.run_loop(
        env, agent, task, max_steps=5, cancellation_token=token,
    )

    assert exec_result.stop_reason == "CANCELLED"
    assert exec_result.error is None  # NOT an ERROR
    assert agent.act_count == 0


@pytest.mark.asyncio
async def test_cancellation_before_inference_skips_agent_act() -> None:
    """If the token is cancelled between steps, agent.act must not run again."""
    env = _FakeEnv()
    task = _FakeTask()
    token = CancellationToken()
    agent = _FakeAgent(env=env)
    # Cancel after the first step so the loop sees it before the second act().
    original_step = env.step

    async def step_then_cancel(action):
        result = await original_step(action)
        token.cancel()
        return result

    env.step = step_then_cancel  # type: ignore[assignment]

    exec_result, _, _, _, _ = await Controller.run(
        env, agent, task, await env.get_observation(), max_steps=5,
        cancellation_token=token,
    )

    assert exec_result.stop_reason == "CANCELLED"
    assert agent.act_count == 1  # ran once; cancelled before the second act


@pytest.mark.asyncio
async def test_cancellation_after_act_before_env_step() -> None:
    """Cancellation during agent.act must prevent env.step for that action."""
    env = _FakeEnv()
    task = _FakeTask()
    token = CancellationToken()
    agent = _FakeAgent(env=env, token=token)
    # The agent cancels mid-act; env.step must not be invoked afterwards.
    step_calls = 0
    original_step = env.step

    async def counting_step(action):
        nonlocal step_calls
        step_calls += 1
        return await original_step(action)

    env.step = counting_step  # type: ignore[assignment]
    env.cancel_during_act = True

    exec_result, _, _, _, _ = await Controller.run(
        env, agent, task, await env.get_observation(), max_steps=5,
        cancellation_token=token,
    )

    assert exec_result.stop_reason == "CANCELLED"
    assert agent.act_count == 1  # the act that triggered cancellation ran
    assert step_calls == 0  # env.step never executed the cancelled action


@pytest.mark.asyncio
async def test_cancelled_result_runs_teardown() -> None:
    """teardown must still run on cancellation (env.close is the runner's job)."""
    env = _FakeEnv()
    task = _FakeTask()
    token = CancellationToken()
    token.cancel()
    agent = _FakeAgent(env=env)

    await Controller.run_loop(env, agent, task, max_steps=5, cancellation_token=token)

    assert task.teardown_count == 1
    # env.close is the SerialRunner / executor's responsibility, not the
    # Controller's; it is verified at the executor level in test_cancel_run.py.


@pytest.mark.asyncio
async def test_cancelled_result_skips_evaluator() -> None:
    """A CANCELLED execution result must not be sent to the evaluator."""
    env = _FakeEnv()
    task = _FakeTask()
    token = CancellationToken()
    token.cancel()
    agent = _FakeAgent(env=env)
    evaluator = _NoEvaluator()

    result = await BaseRunner.run_episode(
        env, agent, task, max_steps=5, evaluator=evaluator,
        cancellation_token=token,
    )

    assert result.execution.stop_reason == "CANCELLED"
    assert evaluator.invoked is False


@pytest.mark.asyncio
async def test_no_token_preserves_cli_behaviour() -> None:
    """Without a cancellation_token the loop runs to completion exactly as CLI."""
    env = _FakeEnv()

    class _CompletingEnv(_FakeEnv):
        async def step(self, action: Action) -> StepResult:
            self.step_count += 1
            if action.action_type == ActionType.COMPLETE:
                return StepResult(
                    observation=await self.get_observation(),
                    done=True,
                    info={"stop_reason": ActionType.COMPLETE},
                )
            return StepResult(observation=await self.get_observation(), done=False, info={})

    env = _CompletingEnv()

    class _CompletingAgent(_FakeAgent):
        def act(self, obs):
            self.act_count += 1
            return Action.complete("done")

    task = _FakeTask()
    agent = _CompletingAgent(env=env)

    exec_result, _, _, _, _ = await Controller.run(
        env, agent, task, await env.get_observation(), max_steps=5,
        # No cancellation_token — defaults to a fresh non-cancelled token.
    )

    # Normal completion path (agent emitted COMPLETE) — not CANCELLED, not ERROR.
    assert exec_result.stop_reason != "CANCELLED"
    assert exec_result.finished is True


# ---------------------------------------------------------------------------
# P1: episode.step_recorded is emitted per step end-to-end through Controller.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_recorded_event_emitted_per_step() -> None:
    """Controller.run emits one episode.step_recorded event per recorded step.

    Verifies the real runner path produces step events that the SSE/UI contract
    depends on (not just that the adapter can forward a hand-built event).
    """
    from bench_env.env.base import Action, ActionType, StepResult

    class _SteppingEnv(_FakeEnv):
        async def step(self, action):
            self.step_count += 1
            # Return done on the 3rd step so the loop terminates.
            done = self.step_count >= 3
            return StepResult(
                observation=await self.get_observation(),
                done=done,
                info={"stop_reason": ActionType.COMPLETE} if done else {},
            )

    env = _SteppingEnv()

    class _ClickAgent(_FakeAgent):
        def act(self, obs):
            self.act_count += 1
            return Action(action_type=ActionType.CLICK, data={"point": [0, 0]})

    task = _FakeTask()
    agent = _ClickAgent(env=env)
    sink = _CollectingSink()

    await Controller.run(
        env, agent, task, await env.get_observation(),
        max_steps=5, event_sink=sink,
    )

    step_events = [e for e in sink.events if e.type == "episode.step_recorded"]
    started_events = [e for e in sink.events if e.type == "episode.started"]
    assert len(started_events) == 1
    assert len(step_events) == 3  # one per recorded step
    # Payload carries step number + action type.
    assert step_events[0].payload["step"] == 1
    assert step_events[0].payload["action_type"] == str(ActionType.CLICK)
