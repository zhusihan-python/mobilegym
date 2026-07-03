from __future__ import annotations

import json
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.task.judge import JudgeResult
from test_platform.services.execution import SerialRunExecutor
from test_platform.tests.integration.test_single_lane_materialization import _create_run, _settings
from test_platform.persistence.database import Database


class _Phase:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Stopwatch:
    total = 0.0

    def reset(self) -> None:
        return None

    def phase(self, name: str) -> _Phase:
        return _Phase()

    def record(self, name: str, value: float) -> None:
        return None

    def to_flat(self) -> dict[str, float]:
        return {}

    def to_tree(self) -> list[dict[str, Any]]:
        return []

    def summary(self) -> str:
        return "fake"


class _ExecutableFakeEnv:
    supports_state_injection = True

    def __init__(self, *, label: str) -> None:
        self.label = label
        self.stopwatch = _Stopwatch()
        self.sample_count = 0
        self.step_count = 0
        self.closed = False
        self._agent_message: str | None = None
        self._agent_answer: str | None = None

    def sample_choice(self) -> str:
        self.sample_count += 1
        return f"{self.label}-sampled-{self.sample_count}"

    async def get_state(self, required_apps: list[str] | None = None) -> dict[str, Any]:
        return {"apps": {"fake": {"label": self.label}}, "os": {"time": {"mode": "fixed"}}}

    async def get_observation(self) -> Observation:
        return Observation(
            route={"app": "fake", "path": "/"},
            state=await self.get_state(),
            step_idx=self.step_count,
        )

    async def step(self, action: Action) -> StepResult:
        self.step_count += 1
        if action.action_type == ActionType.COMPLETE:
            self._agent_message = action.data.get("return", "")
            return StepResult(
                observation=await self.get_observation(),
                done=True,
                info={"stop_reason": ActionType.COMPLETE},
            )
        return StepResult(observation=await self.get_observation(), done=False, info={})

    async def close(self) -> None:
        self.closed = True

    @property
    def agent_message(self) -> str | None:
        return self._agent_message

    @property
    def agent_answer(self) -> str | None:
        return self._agent_answer


class _ExecutableFakeTask:
    apps = ["fake"]
    answer_fields = None
    suite = "fake"

    def __init__(self, _seed: int | None = None, **params: Any) -> None:
        self._seed = _seed
        self.params = {"choice": "default", **params}
        self._user_params = set(params.keys())
        self.setup_count = 0

    @property
    def id(self) -> str:
        return "fake.SampleTask"

    @property
    def description(self) -> str:
        return f"Choose {self.params['choice']}"

    async def setup(self, env: _ExecutableFakeEnv) -> Observation:
        self.setup_count += 1
        if "choice" not in self._user_params:
            self.params["choice"] = env.sample_choice()
        return await env.get_observation()

    def teardown(self, env: _ExecutableFakeEnv) -> None:
        return None

    def evaluate(self, input) -> JudgeResult:
        return JudgeResult.ok()


class _ExecutableTaskFactory:
    def __init__(self) -> None:
        self.instances: list[_ExecutableFakeTask] = []

    def instantiate(self, template, params: dict[str, Any] | None = None) -> _ExecutableFakeTask:
        task = _ExecutableFakeTask(_seed=template.instance_seed, **(params or {}))
        task._instance_id = template.instance_id
        task._template_index = template.template_index
        self.instances.append(task)
        return task


class _FakeAgent:
    name = "fake-agent"

    def __init__(self) -> None:
        self.history: list[Any] = []
        self.instructions: list[str] = []
        self.act_count = 0

    def reset(self, instruction: str) -> None:
        self.instructions.append(instruction)

    def act(self, obs: Observation) -> Action:
        self.act_count += 1
        return Action.complete("done")

    def reset_history(self) -> None:
        self.history.clear()


@pytest.mark.asyncio
async def test_serial_run_execution_materializes_executes_ingests_and_writes_lane_artifacts(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    task_factory = _ExecutableTaskFactory()
    materialize_env = _ExecutableFakeEnv(label="materialize")
    execute_env = _ExecutableFakeEnv(label="execute")
    agent = _FakeAgent()
    envs = iter([materialize_env, execute_env])
    try:
        run = _create_run(database, settings, repeat_n=1)

        detail = await SerialRunExecutor(
            database,
            settings,
            task_factory=task_factory,
            env_factory=lambda lane: next(envs),
            agent_factory=lambda lane: agent,
        ).execute_run(run.id)

        assert detail.state == "completed"
        assert materialize_env.sample_count == 1
        assert execute_env.sample_count == 0
        assert agent.instructions == ["Choose materialize-sampled-1"]
        assert agent.act_count == 1

        episode_attempt = database.connection.execute(
            "SELECT state, outcome, result_json FROM episode_attempts"
        ).fetchone()
        assert episode_attempt["state"] == "completed"
        assert episode_attempt["outcome"] == "PASS"
        assert json.loads(episode_attempt["result_json"])["is_success"] is True

        lane_root = settings.runs_dir / run.id / "lanes" / "candidate"
        assert (lane_root / "meta.json").exists()
        assert (lane_root / "results.jsonl").exists()
        assert (lane_root / "errors.jsonl").exists()
        assert (lane_root / "summary.json").exists()
        assert detail.progress["completed_episodes"] == 1
    finally:
        database.close()
