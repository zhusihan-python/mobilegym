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
        self.diagnostic_sinks: list[Any] = []
        self.diagnostic_contexts: list[dict[str, Any]] = []

    def set_diagnostic_event_sink(self, sink: Any) -> None:
        self.diagnostic_sinks.append(sink)

    def set_diagnostic_context(self, **context: Any) -> None:
        self.diagnostic_contexts.append(context)

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
async def test_controller_binds_diagnostic_sink_and_episode_step_context() -> None:
    env = _FakeEnv()
    task = _FakeTask()
    agent = _FakeAgent(env=env)
    sink = _CollectingSink()

    await Controller.run_loop(
        env,
        agent,
        task,
        max_steps=1,
        event_sink=sink,
        worker_id="W0",
        episode_key="fake.Task::0",
        trial_id=2,
    )

    assert env.diagnostic_sinks[0] is sink
    assert env.diagnostic_contexts[:2] == [
        {
            "episode_key": "fake.Task::0",
            "step": None,
            "app_ids": ["fake"],
            "trial_id": 2,
        },
        {
            "episode_key": "fake.Task::0",
            "step": 1,
            "app_ids": ["fake"],
            "trial_id": 2,
        },
    ]


@pytest.mark.asyncio
async def test_controller_emits_structured_runner_diagnostic_for_action_error() -> None:
    env = _FakeEnv()
    task = _FakeTask()
    agent = _FakeAgent(env=env)
    sink = _CollectingSink()

    async def invalid_step(action: Action) -> StepResult:
        raise ValueError("point is outside the viewport")

    env.step = invalid_step  # type: ignore[assignment]
    await Controller.run(
        env,
        agent,
        task,
        await env.get_observation(),
        max_steps=2,
        event_sink=sink,
        worker_id="W0",
        episode_key="fake.Task::0",
    )

    diagnostic = next(event for event in sink.events if event.type == "diagnostic.runner")
    assert diagnostic.phase == "runner.action"
    assert diagnostic.worker_id == "W0"
    assert diagnostic.task_id == "fake.Task"
    assert diagnostic.episode_key == "fake.Task::0"
    assert diagnostic.payload == {
        "code": "ACTION_FORMAT_ERROR",
        "message": "point is outside the viewport",
        "severity": "error",
        "retryable": True,
        "step": 1,
        "app_ids": ["fake"],
    }


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


# ---------------------------------------------------------------------------
# VS-07 Block B: worker_id param + episode.completed/error terminal events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_episode_emits_terminal_completed_event_on_success() -> None:
    """A passing episode must emit episode.completed with episode_key + worker_id."""
    env = _FakeEnv()

    class _DoneEnv(_FakeEnv):
        async def step(self, action):
            self.step_count += 1
            return StepResult(
                observation=await self.get_observation(), done=True,
                info={"stop_reason": ActionType.COMPLETE},
            )

    env = _DoneEnv()

    class _CompleteAgent(_FakeAgent):
        def act(self, obs):
            self.act_count += 1
            return Action.complete("done")

    task = _FakeTask()
    agent = _CompleteAgent(env=env)
    sink = _CollectingSink()

    result = await BaseRunner.run_episode(
        env, agent, task, max_steps=5, event_sink=sink,
        worker_id="W1", episode_key="fake.T|i0|s1|r1|t0",
    )

    assert result.episode_key == "fake.T|i0|s1|r1|t0"
    completed = [e for e in sink.events if e.type == "episode.completed"]
    errors = [e for e in sink.events if e.type == "episode.error"]
    assert len(completed) == 1
    assert errors == []
    assert completed[0].worker_id == "W1"
    assert completed[0].episode_key == "fake.T|i0|s1|r1|t0"
    assert completed[0].payload["outcome"] in {"PASS", "FAIL"}


@pytest.mark.asyncio
async def test_run_episode_emits_terminal_error_event_on_error() -> None:
    """A genuine execution error (result.error truthy — e.g. setup crash) must
    emit episode.error, using the SAME yardstick as ingestion (result.error).
    FORMAT_ERROR (model fault, result.error is None) is NOT an error outcome —
    it emits episode.completed with FAIL, matching result_is_error semantics."""

    class _CrashingTask(_FakeTask):
        async def setup(self, env):
            raise RuntimeError("setup exploded")

    env = _FakeEnv()

    class _NoOpAgent(_FakeAgent):
        def act(self, obs):
            self.act_count += 1
            return Action.complete("done")

    task = _CrashingTask()
    agent = _NoOpAgent(env=env)
    sink = _CollectingSink()

    result = await BaseRunner.run_episode(
        env, agent, task, max_steps=5, event_sink=sink,
        worker_id="W2", episode_key="fake.E|i0|s1|r1|t0",
    )

    assert result.error is not None  # genuine execution error
    errors = [e for e in sink.events if e.type == "episode.error"]
    completed = [e for e in sink.events if e.type == "episode.completed"]
    assert len(errors) == 1
    assert completed == []
    assert errors[0].worker_id == "W2"
    assert errors[0].episode_key == "fake.E|i0|s1|r1|t0"
    assert errors[0].payload["outcome"] == "ERROR"


@pytest.mark.asyncio
async def test_worker_id_flows_into_episode_events() -> None:
    """Controller.run episode.* events carry the supplied worker_id, not 'serial'."""
    env = _FakeEnv()

    class _DoneEnv(_FakeEnv):
        async def step(self, action):
            self.step_count += 1
            return StepResult(
                observation=await self.get_observation(), done=True,
                info={"stop_reason": ActionType.COMPLETE},
            )

    env = _DoneEnv()

    class _CompleteAgent(_FakeAgent):
        def act(self, obs):
            self.act_count += 1
            return Action.complete("done")

    task = _FakeTask()
    agent = _CompleteAgent(env=env)
    sink = _CollectingSink()

    await Controller.run(
        env, agent, task, await env.get_observation(), max_steps=5,
        event_sink=sink, worker_id="W7", episode_key="k9",
    )

    started = [e for e in sink.events if e.type == "episode.started"]
    assert started and started[0].worker_id == "W7"
    assert started[0].episode_key == "k9"
