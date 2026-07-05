"""VS-09 Block B: PairedMaterializer materializes ONE prepared task for a 2-lane run.

The baseline/candidate lanes share a single PreparedTaskInstance (frozen params +
instruction). The materializer samples ONCE (via the source lane) and both lanes
reuse the result. This file extends the single-lane fakes from
``test_single_lane_materialization`` to a 2-lane matrix.
"""
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
from test_platform.services.execution import PairedMaterializer
from test_platform.services.runs import FakeRunSupervisor, RunService


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _catalog():
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="git-vs09",
        digest="sha256:catalog-vs09",
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


def _create_paired_run(
    database: Database,
    settings: PlatformSettings,
    *,
    baseline_target_id: str,
    candidate_target_id: str,
    repeat_n: int = 2,
):
    """Build a 2-lane matrix run {baseline, candidate} with two distinct targets."""
    workflow = WorkflowRepository(database).create(
        project_id=ProjectRepository(database).list()[0].id,
        name="Paired smoke",
        definition={
            "schema_version": 1,
            "name": "Paired smoke",
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
                            "baseline": {
                                "target_id": baseline_target_id,
                                "role": "baseline",
                            },
                            "candidate": {
                                "target_id": candidate_target_id,
                                "role": "candidate",
                            },
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
        name="VS-09 paired run",
        seed=4242,
        idempotency_key="paired-1",
    )


def _make_target(database: Database, *, name: str, revision: str) -> str:
    project = ProjectRepository(database).list()[0]
    port = 5173 if name == "baseline" else 5174
    target = TargetRepository(database).create(
        project_id=project.id,
        name=name,
        config={
            "kind": "simulator",
            "connection": {"env_url": f"http://127.0.0.1:{port}"},
            "device_profile": {
                "name": name,
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
            "data": {"revision": revision},
            "resolved_at": "2026-07-03T12:00:00.000Z",
        },
        warnings=[],
        health_status="healthy",
    )
    return target.id


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
    """Materialization env: samples a choice once, returns a deterministic state."""

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
async def test_paired_materializer_persists_single_prepared_task_for_two_lanes(tmp_path):
    """A 2-lane run materializes ONCE: one prepared_tasks row shared by both lanes."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    factory = _FakeTaskFactory()
    env = _FakeEnv()
    try:
        project = ProjectRepository(database).create("Paired")
        baseline_target = _make_target(database, name="baseline", revision="seed-v1")
        candidate_target = _make_target(database, name="candidate", revision="seed-v1")
        run = _create_paired_run(
            database,
            settings,
            baseline_target_id=baseline_target,
            candidate_target_id=candidate_target,
        )

        prepared = await PairedMaterializer(
            database,
            settings,
            task_factory=factory,
            env_factory=lambda lane: env,
        ).materialize_run(run.id)

        # One task instance → one prepared_tasks row, regardless of 2 lanes × 2 trials.
        assert len(prepared) == 1
        # Setup ran exactly once (the source lane); teardown once.
        assert factory.instances[0].setup_count == 1
        assert factory.instances[0].teardown_count == 1
        # _post_sample (env.sample_choice) ran once.
        assert env.sample_count == 1

        rows = database.connection.execute(
            "SELECT materialization_key, payload_json FROM prepared_tasks WHERE run_id = ?",
            (run.id,),
        ).fetchall()
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        # Explicit (sampled) params survive.
        assert payload["params"] == {"choice": "sampled-1"}
        assert payload["instruction"] == "Choose sampled-1"
        # Both lanes' episodes link to the same prepared_task_id.
        linked = database.connection.execute(
            "SELECT COUNT(DISTINCT prepared_task_id) FROM episodes WHERE run_id = ?",
            (run.id,),
        ).fetchone()[0]
        assert linked == 1
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_materializer_projection_hash_uses_v1_semantics(tmp_path):
    """The projection_hash field uses projection_hash_v1 (volatile-stripped),
    not the raw initial_state_hash. This makes baseline/candidate comparable."""
    from test_platform.domain.state_projection import projection_hash_v1

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    factory = _FakeTaskFactory()
    env = _FakeEnv()
    try:
        ProjectRepository(database).create("Paired")
        baseline_target = _make_target(database, name="baseline", revision="seed-v1")
        candidate_target = _make_target(database, name="candidate", revision="seed-v1")
        run = _create_paired_run(
            database,
            settings,
            baseline_target_id=baseline_target,
            candidate_target_id=candidate_target,
        )

        prepared = await PairedMaterializer(
            database,
            settings,
            task_factory=factory,
            env_factory=lambda lane: env,
        ).materialize_run(run.id)

        payload = json.loads(
            database.connection.execute(
                "SELECT payload_json FROM prepared_tasks WHERE run_id = ?",
                (run.id,),
            ).fetchone()["payload_json"]
        )
        raw_state = {
            "apps": {"fake": {"choice": "initial"}},
            "os": {"time": {"mode": "fixed", "value": "2026-07-03T12:00:00.000Z"}},
        }
        expected_projection = projection_hash_v1(raw_state)
        assert payload["projection_hash"] == expected_projection
        # projection_hash differs from raw initial_state_hash (time is volatile).
        assert payload["projection_hash"] != payload["initial_state_hash"]
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_materializer_source_lane_resolved_by_lane_id(tmp_path):
    """The materializer resolves the source lane from plan.materialization
    ['source_lane_id'] by matching PlannedLane.lane_id (Contract 11). The env
    factory receives that lane object."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    factory = _FakeTaskFactory()
    env = _FakeEnv()
    lanes_seen: list[Any] = []

    def env_factory(lane):
        lanes_seen.append(lane)
        return env

    try:
        ProjectRepository(database).create("Paired")
        baseline_target = _make_target(database, name="baseline", revision="seed-v1")
        candidate_target = _make_target(database, name="candidate", revision="seed-v1")
        run = _create_paired_run(
            database,
            settings,
            baseline_target_id=baseline_target,
            candidate_target_id=candidate_target,
        )

        await PairedMaterializer(
            database,
            settings,
            task_factory=factory,
            env_factory=env_factory,
        ).materialize_run(run.id)

        # The source lane is the FIRST lane (baseline, sorted by lane_key).
        assert len(lanes_seen) == 1
        assert lanes_seen[0].lane_key == "baseline"
    finally:
        database.close()
