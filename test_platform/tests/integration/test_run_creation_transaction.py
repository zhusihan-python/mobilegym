import sqlite3

import pytest

from test_platform.config import PlatformSettings
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    ProjectRepository,
    TargetRepository,
    WorkflowRepository,
)
from test_platform.services.runs import FakeRunSupervisor, RunService


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _catalog():
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="git-vs04",
        digest="sha256:catalog",
        items=[
            TaskCatalogItem(
                task_base_id="wechat.OpenBlacklist",
                suite="wechat",
                class_name="OpenBlacklist",
                apps=["wechat"],
                templates=["Open blacklist"],
                parameters={},
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


def _create_published_workflow(database):
    project = ProjectRepository(database).create("Run planning")
    target = TargetRepository(database).create(
        project_id=project.id,
        name="Local simulator",
        config={
            "kind": "simulator",
            "connection": {
                "env_url": "http://127.0.0.1:5173",
                "proxy_secret_ref": "secret://must-not-leak",
            },
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
        name="WeChat smoke",
        definition={
            "schema_version": 1,
            "name": "WeChat smoke",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {
                        "task_ids": ["wechat.OpenBlacklist"],
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
                        "repeat_n": 2,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {
                        "parallel": 1,
                        "model_api_key": "never-persist-this",
                    },
                },
            ],
        },
    )
    return WorkflowRepository(database).publish(workflow.id)


def test_run_creation_persists_the_complete_graph_and_artifact_atomically(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    supervisor = FakeRunSupervisor()
    try:
        version = _create_published_workflow(database)
        run = RunService(
            database,
            settings,
            supervisor=supervisor,
            catalog_builder=_catalog,
        ).create_run(
            workflow_version_id=version.id,
            name="VS-04 planned run",
            seed=123,
            idempotency_key="launch-1",
        )

        assert run.state == "queued"
        assert run.progress == {
            "planned_episodes": 2,
            "planned_lane_episodes": 2,
            "completed_episodes": 0,
        }
        assert supervisor.snapshot()["queued_run_ids"] == [run.id]
        assert database.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert database.connection.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 1
        assert database.connection.execute("SELECT COUNT(*) FROM lanes").fetchone()[0] == 1
        assert database.connection.execute("SELECT COUNT(*) FROM lane_attempts").fetchone()[0] == 1
        assert database.connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 2
        assert database.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1

        artifact = settings.runs_dir / run.id / "platform" / "run-plan.json"
        assert artifact.exists()
        assert "never-persist-this" not in artifact.read_text(encoding="utf-8")
        assert "secret://must-not-leak" not in artifact.read_text(encoding="utf-8")
    finally:
        database.close()


def test_run_creation_rolls_back_every_row_when_episode_insert_fails(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        version = _create_published_workflow(database)
        database.connection.execute(
            """
            CREATE TRIGGER block_episode_insert
            BEFORE INSERT ON episodes
            BEGIN
              SELECT RAISE(ABORT, 'episode insert blocked');
            END
            """
        )
        database.connection.commit()
        service = RunService(
            database,
            settings,
            supervisor=FakeRunSupervisor(),
            catalog_builder=_catalog,
        )

        with pytest.raises(sqlite3.DatabaseError, match="episode insert blocked"):
            service.create_run(
                workflow_version_id=version.id,
                name="Must roll back",
                seed=123,
                idempotency_key="launch-fails",
            )

        for table in ("runs", "run_attempts", "lanes", "lane_attempts", "episodes", "events"):
            assert database.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert list(settings.runs_dir.glob("*/platform/run-plan.json")) == []
    finally:
        database.close()
