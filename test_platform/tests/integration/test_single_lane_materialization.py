from __future__ import annotations

import json
from typing import Any

import pytest

from test_platform.config import PlatformSettings
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    ProjectRepository,
    TargetRepository,
    WorkflowRepository,
)
from test_platform.services.execution import SingleLaneMaterializer
from test_platform.services.runs import FakeRunSupervisor, RunService


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _catalog():
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="git-vs05",
        digest="sha256:catalog-vs05",
        items=[
            TaskCatalogItem(
                task_base_id="fake.SampleTask",
                suite="fake",
                class_name="SampleTask",
                apps=["fake"],
                templates=["Choose {choice}"],
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
        ],
    )


def _create_run(database: Database, settings: PlatformSettings, *, repeat_n: int = 2):
    project = ProjectRepository(database).create("Materialization")
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
        name="Fake smoke",
        definition={
            "schema_version": 1,
            "name": "Fake smoke",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {
                        "task_ids": ["fake.SampleTask"],
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
                        "repeat_n": repeat_n,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {"parallel": 1, "agent": "fake", "model_name": "fake-model"},
                },
            ],
        },
    )
    version = WorkflowRepository(database).publish(workflow.id)
    return RunService(
        database,
        settings,
        supervisor=FakeRunSupervisor(),
        catalog_builder=_catalog,
    ).create_run(
        workflow_version_id=version.id,
        name="VS-05 materialized run",
        seed=4242,
        idempotency_key="materialize-1",
    )


class _FakeTask:
    apps = ["fake"]
    answer_fields = None

    def __init__(self, _seed: int | None = None, **params: Any) -> None:
        self._seed = _seed
        self.params = {"choice": "default", **params}
        self.setup_count = 0
        self.teardown_count = 0

    @property
    def id(self) -> str:
        return "fake.SampleTask"

    @property
    def description(self) -> str:
        return f"Choose {self.params['choice']}"

    async def setup(self, env: Any) -> object:
        self.setup_count += 1
        if "choice" not in getattr(self, "_user_params", set()):
            self.params["choice"] = env.sample_choice()
        return object()

    def teardown(self, env: Any) -> None:
        self.teardown_count += 1


class _FakeTaskFactory:
    def __init__(self) -> None:
        self.instances: list[_FakeTask] = []

    def instantiate(self, template, params: dict[str, Any] | None = None) -> _FakeTask:
        task = _FakeTask(_seed=template.instance_seed, **(params or {}))
        task._instance_id = template.instance_id
        task._template_index = template.template_index
        task._user_params = set((params or {}).keys())
        self.instances.append(task)
        return task


class _FakeEnv:
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


@pytest.mark.asyncio
async def test_single_lane_materialization_persists_one_prepared_task_for_all_trials(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    factory = _FakeTaskFactory()
    env = _FakeEnv()
    try:
        run = _create_run(database, settings)

        prepared = await SingleLaneMaterializer(
            database,
            settings,
            task_factory=factory,
            env_factory=lambda lane: env,
        ).materialize_run(run.id)

        assert len(prepared) == 1
        assert factory.instances[0].setup_count == 1
        assert factory.instances[0].teardown_count == 1
        assert env.sample_count == 1

        row = database.connection.execute(
            "SELECT id, materialization_key, payload_json, payload_hash FROM prepared_tasks"
        ).fetchone()
        payload = json.loads(row["payload_json"])
        assert row["materialization_key"] == run.episode_identities[0]["materialization_key"]
        assert row["payload_hash"] == payload["fingerprint"]
        assert payload["params"] == {"choice": "sampled-1"}
        assert payload["instruction"] == "Choose sampled-1"
        assert payload["data_revision"] == "seed-v1"

        artifact = settings.runs_dir / run.id / payload["initial_state_relative_path"]
        assert artifact.exists()
        assert json.loads(artifact.read_text(encoding="utf-8"))["apps"]["fake"]["choice"] == "initial"

        linked = database.connection.execute(
            "SELECT COUNT(DISTINCT prepared_task_id) FROM episodes WHERE run_id = ?",
            (run.id,),
        ).fetchone()[0]
        assert linked == 1
    finally:
        database.close()
