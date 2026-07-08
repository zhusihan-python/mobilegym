from __future__ import annotations

import json
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.runner.cancellation import CancellationToken, RunCancelled
from bench_env.task.judge import JudgeResult
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    ProjectRepository,
    ReplayRepository,
    TargetRepository,
    WorkflowRepository,
)
from test_platform.services.execution import SerialRunExecutor
from test_platform.services.runs import FakeRunSupervisor, RunService
from test_platform.tests.integration.test_single_lane_materialization import _create_run, _settings


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

    def __init__(self, *, label: str, on_close: Any | None = None) -> None:
        self.label = label
        self.on_close = on_close
        self.stopwatch = _Stopwatch()
        self.sample_count = 0
        self.step_count = 0
        self.closed = False
        self.marker = "clean"
        self._agent_message: str | None = None
        self._agent_answer: str | None = None

    def sample_choice(self) -> str:
        self.sample_count += 1
        return f"{self.label}-sampled-{self.sample_count}"

    async def get_state(self, required_apps: list[str] | None = None) -> dict[str, Any]:
        return {
            "apps": {"fake": {"label": self.label, "marker": self.marker}},
            "os": {"time": {"mode": "fixed"}},
        }

    async def get_observation(self) -> Observation:
        return Observation(
            route={"app": "fake", "path": "/"},
            state=await self.get_state(),
            step_idx=self.step_count,
        )

    async def step(self, action: Action) -> StepResult:
        self.step_count += 1
        if action.action_type == ActionType.COMPLETE:
            self.marker = f"{self.label}-mutated"
            self._agent_message = action.data.get("return", "")
            return StepResult(
                observation=await self.get_observation(),
                done=True,
                info={"stop_reason": ActionType.COMPLETE},
            )
        return StepResult(observation=await self.get_observation(), done=False, info={})

    async def close(self) -> None:
        self.closed = True
        if callable(self.on_close):
            self.on_close()

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
        self.setup_state: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        return getattr(self, "task_base_id", "fake.SampleTask")

    @property
    def description(self) -> str:
        if self.id != "fake.SampleTask":
            return f"{self.id} choose {self.params['choice']}"
        return f"Choose {self.params['choice']}"

    async def setup(self, env: _ExecutableFakeEnv) -> Observation:
        self.setup_count += 1
        self.setup_state = await env.get_state()
        if "choice" not in self._user_params:
            self.params["choice"] = env.sample_choice()
        return await env.get_observation()

    def teardown(self, env: _ExecutableFakeEnv) -> None:
        return None

    def evaluate(self, input) -> JudgeResult:
        if "Fail" in self.id:
            return JudgeResult.fail("intentional sequence failure")
        return JudgeResult.ok()


class _ExecutableTaskFactory:
    def __init__(self) -> None:
        self.instances: list[_ExecutableFakeTask] = []

    def instantiate(self, template, params: dict[str, Any] | None = None) -> _ExecutableFakeTask:
        task = _ExecutableFakeTask(_seed=template.instance_seed, **(params or {}))
        task.task_base_id = template.task_base_id
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


def _manual_sequence_catalog() -> TaskCatalogSnapshot:
    def item(task_base_id: str) -> TaskCatalogItem:
        return TaskCatalogItem(
            task_base_id=task_base_id,
            suite="fake",
            class_name=task_base_id.rsplit(".", maxsplit=1)[-1],
            apps=["fake"],
            templates=[f"{task_base_id} choose {{choice}}"],
            parameters={"choice": {"type": "string", "default": "default"}},
            difficulty="L1",
            scope="S1",
            objective="operate",
            composition="atomic",
            capabilities=[],
            max_steps=15,
            answer_fields=False,
            optimal_path_lengths=[],
        )

    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="git-vs05",
        digest="sha256:manual-sequence-catalog",
        items=[
            item("fake.ZFailFirstTask"),
            item("fake.APassSecondTask"),
        ],
    )


def _create_manual_sequence_run(database: Database, settings) -> Any:
    project = ProjectRepository(database).create("Manual sequence execution")
    target = TargetRepository(database).create(
        project_id=project.id,
        name="Local simulator",
        config={
            "kind": "simulator",
            "connection": {"env_url": "http://127.0.0.1:5173"},
            "device_profile": {
                "name": "Pixel 7",
                "viewport_width": 393,
                "viewport_height": 852,
                "physical_width": 1080,
                "physical_height": 2400,
                "device_scale_factor": 2.75,
            },
            "runtime": {},
            "labels": {},
        },
    )
    TargetRepository(database).record_revision(
        target_id=target.id,
        metadata={
            "schema_version": 1,
            "data": {"revision": "seed-v1"},
            "resolved_at": "2026-07-03T12:00:00.000Z",
        },
        warnings=[],
        health_status="healthy",
    )
    workflow = WorkflowRepository(database).create(
        project_id=project.id,
        name="Manual sequence",
        definition={
            "schema_version": 1,
            "name": "Manual sequence",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {
                        "task_ids": [
                            "fake.ZFailFirstTask",
                            "fake.APassSecondTask",
                        ],
                        "order_policy": "manual",
                        "sample_n": 1,
                    },
                },
                {
                    "id": "matrix",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lanes": {
                            "candidate": {
                                "target_id": target.id,
                                "role": "candidate",
                            }
                        },
                        "repeat_n": 1,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {
                        "execution_strategy": "linear_sequence",
                        "state_policy": "isolated",
                        "failure_policy": "continue",
                        "parallel": 1,
                        "processes": 1,
                        "agent": "fake",
                        "model_name": "fake-model",
                    },
                },
            ],
        },
    )
    version = WorkflowRepository(database).publish(workflow.id)
    return RunService(
        database,
        settings,
        supervisor=FakeRunSupervisor(),
        catalog_builder=_manual_sequence_catalog,
    ).create_run(
        workflow_version_id=version.id,
        name="M05 manual sequence execution",
        seed=4242,
        idempotency_key="manual-sequence-execution-1",
    )


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
            """
            SELECT ea.state, ea.outcome, ea.result_json, ea.artifact_root, e.episode_key
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            """
        ).fetchone()
        assert episode_attempt["state"] == "completed"
        assert episode_attempt["outcome"] == "PASS"
        assert json.loads(episode_attempt["result_json"])["is_success"] is True
        episode_dir = settings.runs_dir / run.id / episode_attempt["artifact_root"]
        assert episode_dir.exists()
        assert (episode_dir / "trajectory.json").exists()
        replay = ReplayRepository(database).get_episode_replay(
            run.id,
            episode_attempt["episode_key"],
            lane_key="candidate",
            attempt_no="1",
        )
        assert replay["artifact_root"] == episode_attempt["artifact_root"]
        assert replay["steps"]

        lane_root = settings.runs_dir / run.id / "lanes" / "candidate"
        assert (lane_root / "meta.json").exists()
        assert (lane_root / "results.jsonl").exists()
        assert (lane_root / "errors.jsonl").exists()
        assert (lane_root / "summary.json").exists()
        assert detail.progress["completed_episodes"] == 1
    finally:
        database.close()


@pytest.mark.asyncio
async def test_serial_run_execution_runs_manual_sequence_in_order_and_continues_after_failure(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    task_factory = _ExecutableTaskFactory()
    materialize_a = _ExecutableFakeEnv(label="materialize-a")
    materialize_z = _ExecutableFakeEnv(label="materialize-z")
    execute_z = _ExecutableFakeEnv(label="execute-z")
    execute_a = _ExecutableFakeEnv(label="execute-a")
    agent = _FakeAgent()
    envs = iter([materialize_a, materialize_z, execute_z, execute_a])
    try:
        run = _create_manual_sequence_run(database, settings)

        detail = await SerialRunExecutor(
            database,
            settings,
            task_factory=task_factory,
            env_factory=lambda lane: next(envs),
            agent_factory=lambda lane: agent,
        ).execute_run(run.id)

        assert detail.state == "completed"
        assert [item["task_base_id"] for item in detail.episode_identities] == [
            "fake.ZFailFirstTask",
            "fake.APassSecondTask",
        ]
        assert [item["sequence_index"] for item in detail.episode_identities] == [0, 1]
        assert agent.instructions == [
            "fake.ZFailFirstTask choose materialize-z-sampled-1",
            "fake.APassSecondTask choose materialize-a-sampled-1",
        ]
        assert agent.act_count == 2
        assert execute_z.marker == "execute-z-mutated"
        assert execute_a.marker == "execute-a-mutated"
        execution_tasks = task_factory.instances[2:]
        assert [
            task.setup_state["apps"]["fake"]["marker"]
            for task in execution_tasks
        ] == ["clean", "clean"]
        assert execute_z.closed is True
        assert execute_a.closed is True

        attempts = database.connection.execute(
            """
            SELECT e.task_base_id, e.sequence_index, ea.state, ea.outcome
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            WHERE e.run_id = ?
            ORDER BY e.sequence_index
            """,
            (run.id,),
        ).fetchall()
        assert [dict(row) for row in attempts] == [
            {
                "task_base_id": "fake.ZFailFirstTask",
                "sequence_index": 0,
                "state": "completed",
                "outcome": "FAIL",
            },
            {
                "task_base_id": "fake.APassSecondTask",
                "sequence_index": 1,
                "state": "completed",
                "outcome": "PASS",
            },
        ]
        assert detail.progress["completed_episodes"] == 2
        assert detail.progress["completed_lane_episodes"] == 2
    finally:
        database.close()


@pytest.mark.asyncio
async def test_serial_manual_sequence_cancel_after_first_result_synthesizes_remaining(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    task_factory = _ExecutableTaskFactory()
    token = CancellationToken()
    materialize_a = _ExecutableFakeEnv(label="materialize-a")
    materialize_z = _ExecutableFakeEnv(label="materialize-z")
    execute_z = _ExecutableFakeEnv(label="execute-z", on_close=token.cancel)
    envs = iter([materialize_a, materialize_z, execute_z])
    try:
        run = _create_manual_sequence_run(database, settings)

        with pytest.raises(RunCancelled):
            await SerialRunExecutor(
                database,
                settings,
                task_factory=task_factory,
                env_factory=lambda lane: next(envs),
                agent_factory=lambda lane: _FakeAgent(),
            ).execute_run(run.id, token=token)

        attempts = database.connection.execute(
            """
            SELECT e.task_base_id, e.sequence_index, ea.state, ea.outcome, ea.error_code
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            WHERE e.run_id = ?
            ORDER BY e.sequence_index
            """,
            (run.id,),
        ).fetchall()
        assert [dict(row) for row in attempts] == [
            {
                "task_base_id": "fake.ZFailFirstTask",
                "sequence_index": 0,
                "state": "cancelled",
                "outcome": "CANCELLED",
                "error_code": "CANCELLED",
            },
            {
                "task_base_id": "fake.APassSecondTask",
                "sequence_index": 1,
                "state": "cancelled",
                "outcome": "CANCELLED",
                "error_code": "CANCELLED",
            },
        ]
        run_row = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?",
            (run.id,),
        ).fetchone()
        assert run_row["state"] == "cancelled"
    finally:
        database.close()
