from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.domain.runs import RunDomainError
from test_platform.domain.task_catalog import TaskCatalogSnapshot
from test_platform.services.execution import ParallelRunExecutor
from test_platform.services.runs import (
    FakeRunSupervisor,
    RunService,
    _RUN_SECRET_STORE,
    _runner_config_for_lane,
)
from test_platform.tests.integration.test_parallel_lane import (
    _CompletingAgent,
    _FakeEnvPool,
    _FakeTaskFactory,
    _MaterializeEnv,
)
from test_platform.tests.integration.test_paired_serial_run import (
    _PairedEnv,
    _PairedTaskFactory,
    _ScriptedAgent,
)
from test_platform.tests.integration.test_materializer import _create_paired_run, _make_target
from test_platform.tests.integration.test_single_lane_materialization import (
    _catalog as _single_lane_catalog,
    _create_run,
)
from test_platform.persistence.repositories import ProjectRepository, TargetRepository


NOW = "2026-07-06T00:00:00.000Z"


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _seed_retryable_single_lane_run(
    database,
    *,
    runner_config: dict[str, object] | None = None,
) -> str:
    connection = database.connection
    runner_config = runner_config or {}
    plan = {
        "schema_version": 1,
        "run_id": "run-retry",
        "workflow_version_id": "wv1",
        "task_source": {
            "repository_revision": "git-vs13",
            "registry_digest": "sha256:tasks",
            "selection": {"task_ids": ["fake.Task"], "seed": 1},
        },
        "lanes": [
            {
                "lane_id": "lane-c",
                "lane_key": "candidate",
                "role": "candidate",
                "target_id": "target-c",
                "target_revision_id": "trev-c",
                "target_revision_hash": "sha256:target",
                "runner_config": runner_config,
            }
        ],
        "episodes": [
            {
                "episode_key": f"fake.Task::{index}",
                "materialization_key": f"mat{index}",
                "pair_key": f"pair{index}",
                "task_base_id": "fake.Task",
                "task_id": "fake.Task",
                "instance_id": index,
                "instance_seed": 100 + index,
                "template_index": 0,
                "trial_id": index,
                "max_steps": 20,
            }
            for index in range(3)
        ],
        "materialization": {},
        "comparison": {},
        "gates": {},
        "agent": {},
        "judge": {},
        "artifacts": {},
        "created_at": NOW,
        "fingerprint": "sha256:plan",
    }
    connection.execute(
        "INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
        "VALUES ('proj1', 'Project', 'project', 'project', NULL, ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
        "VALUES ('wf1', 'proj1', 'Workflow', '{}', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO workflow_versions "
        "(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
        "VALUES ('wv1', 'wf1', 1, 'published', '{}', 'sha256:wf', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO targets "
        "(id, project_id, name, kind, enabled, config_json, created_at, updated_at) "
        "VALUES ('target-c', 'proj1', 'Candidate', 'simulator', 1, '{}', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO target_revisions "
        "(id, target_id, metadata_json, metadata_hash, health_status, warnings_json, resolved_at) "
        "VALUES ('trev-c', 'target-c', '{}', 'sha256:target', 'healthy', '[]', ?)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO runs "
        "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
        " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at, started_at, ended_at) "
        "VALUES ('run-retry', 'proj1', 'wv1', 'Retry run', 'completed', ?, 'sha256:plan', "
        " 'runs/run-retry', 1, NULL, ?, ?, ?, ?)",
        (json.dumps(plan, sort_keys=True), NOW, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO lanes "
        "(id, run_id, lane_key, role, target_id, target_revision_id, runner_config_json, "
        " reproducibility_fingerprint, created_at) "
        "VALUES ('lane-c', 'run-retry', 'candidate', 'candidate', 'target-c', 'trev-c', ?, 'sha256:lane', ?)",
        (json.dumps(runner_config, sort_keys=True), NOW),
    )
    for index in range(3):
        connection.execute(
            "INSERT INTO prepared_tasks "
            "(id, run_id, materialization_key, payload_json, payload_hash, created_at) "
            "VALUES (?, 'run-retry', ?, ?, ?, ?)",
            (
                f"prep{index}",
                f"mat{index}",
                json.dumps({"materialization_key": f"mat{index}", "params": {}}, sort_keys=True),
                f"sha256:prep{index}",
                NOW,
            ),
        )
        connection.execute(
            "INSERT INTO episodes "
            "(id, run_id, episode_key, materialization_key, pair_key, task_base_id, task_id, "
            " instance_id, instance_seed, template_index, trial_id, max_steps, prepared_task_id, created_at) "
            "VALUES (?, 'run-retry', ?, ?, ?, 'fake.Task', 'fake.Task', ?, ?, 0, ?, 20, ?, ?)",
            (
                f"ep{index}",
                f"fake.Task::{index}",
                f"mat{index}",
                f"pair{index}",
                index,
                100 + index,
                index,
                f"prep{index}",
                NOW,
            ),
        )
    connection.execute(
        "INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, started_at, ended_at, created_at) "
        "VALUES ('attempt1', 'run-retry', 1, 'initial', 'completed', ?, ?, ?)",
        (NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO lane_attempts "
        "(id, lane_id, run_attempt_id, state, artifact_root, started_at, ended_at, created_at) "
        "VALUES ('la1', 'lane-c', 'attempt1', 'completed', 'lanes/candidate/attempts/0001', ?, ?, ?)",
        (NOW, NOW, NOW),
    )
    for index, outcome, error_code in (
        (0, "PASS", None),
        (1, "FAIL", "ASSERTION_FAILURE"),
        (2, "ERROR", "EXECUTION_ERROR"),
    ):
        connection.execute(
            "INSERT INTO episode_attempts "
            "(id, episode_id, lane_attempt_id, attempt_no, state, outcome, error_code, result_json, "
            " artifact_root, started_at, ended_at, created_at) "
            "VALUES (?, ?, 'la1', 1, 'completed', ?, ?, '{}', ?, ?, ?, ?)",
            (
                f"ea{index}",
                f"ep{index}",
                outcome,
                error_code,
                f"lanes/candidate/attempts/0001/trajectory/fake_Task_{index}",
                NOW,
                NOW,
                NOW,
            ),
        )
    connection.commit()
    return "run-retry"


def test_retry_creates_new_attempt_selection_without_mutating_original_results(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_retryable_single_lane_run(database)

        response = client.post(f"/api/platform/v1/runs/{run_id}/retry")

        assert response.status_code == 202
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["run_attempt_id"] != "attempt1"
        assert payload["attempt_no"] == 2
        assert payload["selected_lane_episodes"] == [
            {"episode_key": "fake.Task::1", "lane_key": "candidate", "reason": "retry_failed"},
            {"episode_key": "fake.Task::2", "lane_key": "candidate", "reason": "retry_error"},
        ]

        old_attempts = database.connection.execute(
            "SELECT id, outcome, error_code FROM episode_attempts ORDER BY id"
        ).fetchall()
        assert [(row["id"], row["outcome"], row["error_code"]) for row in old_attempts] == [
            ("ea0", "PASS", None),
            ("ea1", "FAIL", "ASSERTION_FAILURE"),
            ("ea2", "ERROR", "EXECUTION_ERROR"),
        ]
        lane_attempts = database.connection.execute(
            "SELECT run_attempt_id, artifact_root FROM lane_attempts ORDER BY created_at, id"
        ).fetchall()
        assert [row["artifact_root"] for row in lane_attempts] == [
            "lanes/candidate/attempts/0001",
            "lanes/candidate/attempts/0002",
        ]
        selected = database.connection.execute(
            "SELECT episode_id, lane_id, reason FROM run_attempt_episode_selection "
            "WHERE run_attempt_id = ? ORDER BY episode_id",
            (payload["run_attempt_id"],),
        ).fetchall()
        assert [(row["episode_id"], row["lane_id"], row["reason"]) for row in selected] == [
            ("ep1", "lane-c", "retry_failed"),
            ("ep2", "lane-c", "retry_error"),
        ]


def test_retry_preserves_manual_sequence_metadata_in_selected_episodes(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_retryable_single_lane_run(database)
        database.connection.execute(
            """
            UPDATE episodes
            SET sequence_index = CASE id WHEN 'ep2' THEN 0 WHEN 'ep1' THEN 1 END,
                sequence_group_id = 'manual_sequence'
            WHERE id IN ('ep1', 'ep2')
            """
        )
        database.connection.commit()

        response = client.post(f"/api/platform/v1/runs/{run_id}/retry")

        assert response.status_code == 202
        assert response.json()["selected_lane_episodes"] == [
            {
                "episode_key": "fake.Task::2",
                "lane_key": "candidate",
                "sequence_index": 0,
                "sequence_group_id": "manual_sequence",
                "reason": "retry_error",
            },
            {
                "episode_key": "fake.Task::1",
                "lane_key": "candidate",
                "sequence_index": 1,
                "sequence_group_id": "manual_sequence",
                "reason": "retry_failed",
            },
        ]


def test_retry_requires_model_api_key_when_configured_secret_is_missing(tmp_path):
    app = create_app(_settings(tmp_path), supervisor=FakeRunSupervisor())

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_retryable_single_lane_run(
            database,
            runner_config={
                "agent": "autoglm",
                "model_name": "glm-5v-turbo",
                "model_base_url": "https://open.bigmodel.cn/api/paas/v4",
                "image_url_format": "bare_base64",
                "model_api_key_configured": True,
            },
        )
        _RUN_SECRET_STORE.discard(run_id)

        response = client.post(f"/api/platform/v1/runs/{run_id}/retry")

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "RUN_EXECUTION_SECRET_MISSING"
        assert error["details"] == [{"field": "execution.model_api_key"}]
        assert database.connection.execute(
            "SELECT COUNT(*) FROM run_attempts WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1


def test_retry_accepts_model_api_key_without_persisting_secret(tmp_path):
    app = create_app(_settings(tmp_path), supervisor=FakeRunSupervisor())

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_retryable_single_lane_run(
            database,
            runner_config={
                "agent": "autoglm",
                "model_name": "glm-5v-turbo",
                "model_base_url": "https://open.bigmodel.cn/api/paas/v4",
                "image_url_format": "bare_base64",
                "model_api_key_configured": True,
            },
        )
        _RUN_SECRET_STORE.discard(run_id)

        response = client.post(
            f"/api/platform/v1/runs/{run_id}/retry",
            json={"execution": {"model_api_key": "sk-retry-secret"}},
        )

        assert response.status_code == 202
        assert "sk-retry-secret" not in response.text
        lane_config = json.loads(
            database.connection.execute(
                "SELECT runner_config_json FROM lanes WHERE id = 'lane-c'",
            ).fetchone()["runner_config_json"]
        )
        assert "model_api_key" not in lane_config

        lane = type(
            "Lane",
            (),
            {
                "run_id": run_id,
                "runner_config": lane_config,
            },
        )()
        config = _runner_config_for_lane(lane, client.app.state.settings)
        assert config.model_api_key == "sk-retry-secret"
        _RUN_SECRET_STORE.discard(run_id)


def test_retry_rejects_followup_execution_config_mutation(tmp_path):
    app = create_app(_settings(tmp_path), supervisor=FakeRunSupervisor())

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_retryable_single_lane_run(database)

        response = client.post(
            f"/api/platform/v1/runs/{run_id}/retry",
            json={"execution": {"model_base_url": "https://other.example/v1"}},
        )

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "RUN_FOLLOWUP_CONFIG_INVALID"
        assert error["details"] == [{"field": "execution.model_base_url"}]
        assert database.connection.execute(
            "SELECT COUNT(*) FROM run_attempts WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1


def test_resume_creates_new_attempt_for_missing_and_service_restarted_episodes(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run_id = _seed_retryable_single_lane_run(database)
        database.connection.execute("UPDATE runs SET state = 'failed' WHERE id = ?", (run_id,))
        database.connection.execute(
            "UPDATE run_attempts SET state = 'failed' WHERE id = 'attempt1'"
        )
        database.connection.execute(
            "UPDATE lane_attempts SET state = 'failed' WHERE id = 'la1'"
        )
        database.connection.execute("DELETE FROM episode_attempts WHERE id = 'ea1'")
        database.connection.execute(
            "UPDATE episode_attempts SET outcome = 'ERROR', error_code = 'SERVICE_RESTARTED' "
            "WHERE id = 'ea2'"
        )
        database.connection.commit()

        response = RunService(
            database,
            settings,
            supervisor=FakeRunSupervisor(),
            catalog_builder=lambda: TaskCatalogSnapshot(
                repository_revision="git-vs13",
                digest="sha256:tasks",
                items=[],
            ),
        ).resume_run(run_id)

        assert response["run_id"] == run_id
        assert response["attempt_no"] == 2
        assert response["reason"] == "resume"
        assert response["selected_lane_episodes"] == [
            {"episode_key": "fake.Task::1", "lane_key": "candidate", "reason": "resume_missing"},
            {
                "episode_key": "fake.Task::2",
                "lane_key": "candidate",
                "reason": "resume_service_restarted",
            },
        ]

        selected = database.connection.execute(
            "SELECT episode_id, lane_id, reason FROM run_attempt_episode_selection "
            "WHERE run_attempt_id = ? ORDER BY episode_id",
            (response["run_attempt_id"],),
        ).fetchall()
        assert [(row["episode_id"], row["lane_id"], row["reason"]) for row in selected] == [
            ("ep1", "lane-c", "resume_missing"),
            ("ep2", "lane-c", "resume_service_restarted"),
        ]
    finally:
        database.close()


def test_resume_preserves_manual_sequence_metadata_in_selected_episodes(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run_id = _seed_retryable_single_lane_run(database)
        database.connection.execute("UPDATE runs SET state = 'failed' WHERE id = ?", (run_id,))
        database.connection.execute(
            "UPDATE run_attempts SET state = 'failed' WHERE id = 'attempt1'"
        )
        database.connection.execute(
            "UPDATE lane_attempts SET state = 'failed' WHERE id = 'la1'"
        )
        database.connection.execute("DELETE FROM episode_attempts WHERE id = 'ea1'")
        database.connection.execute(
            "UPDATE episode_attempts SET outcome = 'ERROR', error_code = 'SERVICE_RESTARTED' "
            "WHERE id = 'ea2'"
        )
        database.connection.execute(
            """
            UPDATE episodes
            SET sequence_index = CASE id WHEN 'ep2' THEN 0 WHEN 'ep1' THEN 1 END,
                sequence_group_id = 'manual_sequence'
            WHERE id IN ('ep1', 'ep2')
            """
        )
        database.connection.commit()

        response = RunService(
            database,
            settings,
            supervisor=FakeRunSupervisor(),
            catalog_builder=lambda: TaskCatalogSnapshot(
                repository_revision="git-vs13",
                digest="sha256:tasks",
                items=[],
            ),
        ).resume_run(run_id)

        assert response["selected_lane_episodes"] == [
            {
                "episode_key": "fake.Task::2",
                "lane_key": "candidate",
                "sequence_index": 0,
                "sequence_group_id": "manual_sequence",
                "reason": "resume_service_restarted",
            },
            {
                "episode_key": "fake.Task::1",
                "lane_key": "candidate",
                "sequence_index": 1,
                "sequence_group_id": "manual_sequence",
                "reason": "resume_missing",
            },
        ]
    finally:
        database.close()


def test_resume_blocks_when_target_revision_changed_since_run_plan(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=1)
        lane = database.connection.execute(
            "SELECT target_id, target_revision_id FROM lanes WHERE run_id = ?",
            (run.id,),
        ).fetchone()
        TargetRepository(database).record_revision(
            target_id=lane["target_id"],
            metadata={
                "schema_version": 1,
                "data": {"revision": "seed-v2"},
                "resolved_at": "2026-07-07T12:00:00.000Z",
            },
            warnings=[],
            health_status="healthy",
        )
        database.connection.execute("UPDATE runs SET state = 'failed' WHERE id = ?", (run.id,))
        database.connection.execute(
            "UPDATE run_attempts SET state = 'failed' WHERE run_id = ?", (run.id,)
        )
        database.connection.execute(
            "UPDATE lane_attempts SET state = 'failed' "
            "WHERE run_attempt_id IN (SELECT id FROM run_attempts WHERE run_id = ?)",
            (run.id,),
        )
        database.connection.commit()

        with pytest.raises(RunDomainError) as exc_info:
            RunService(
                database,
                settings,
                supervisor=FakeRunSupervisor(),
                catalog_builder=_single_lane_catalog,
            ).resume_run(run.id)

        assert exc_info.value.code == "RUN_RESUME_INCOMPATIBLE_REVISION"
        assert exc_info.value.status_code == 409
        assert exc_info.value.details == [
            {
                "kind": "target_revision",
                "lane_key": "candidate",
                "target_id": lane["target_id"],
                "expected_revision_id": lane["target_revision_id"],
                "current_revision_id": TargetRepository(database).latest_revision(lane["target_id"]).id,
            }
        ]
        assert database.connection.execute(
            "SELECT COUNT(*) FROM run_attempts WHERE run_id = ?", (run.id,)
        ).fetchone()[0] == 1
    finally:
        database.close()


def test_resume_blocks_when_task_source_digest_changed_since_run_plan(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=1)
        database.connection.execute("UPDATE runs SET state = 'failed' WHERE id = ?", (run.id,))
        database.connection.execute(
            "UPDATE run_attempts SET state = 'failed' WHERE run_id = ?", (run.id,)
        )
        database.connection.execute(
            "UPDATE lane_attempts SET state = 'failed' "
            "WHERE run_attempt_id IN (SELECT id FROM run_attempts WHERE run_id = ?)",
            (run.id,),
        )
        database.connection.commit()

        def changed_catalog():
            return _single_lane_catalog().model_copy(update={"digest": "sha256:changed"})

        with pytest.raises(RunDomainError) as exc_info:
            RunService(
                database,
                settings,
                supervisor=FakeRunSupervisor(),
                catalog_builder=changed_catalog,
            ).resume_run(run.id)

        assert exc_info.value.code == "RUN_RESUME_INCOMPATIBLE_REVISION"
        assert exc_info.value.status_code == 409
        assert exc_info.value.details == [
            {
                "kind": "task_source",
                "expected_repository_revision": "git-vs05",
                "current_repository_revision": "git-vs05",
                "expected_registry_digest": "sha256:catalog-vs05",
                "current_registry_digest": "sha256:changed",
            }
        ]
        assert database.connection.execute(
            "SELECT COUNT(*) FROM run_attempts WHERE run_id = ?", (run.id,)
        ).fetchone()[0] == 1
    finally:
        database.close()


@pytest.mark.asyncio
async def test_retry_execution_runs_only_selected_failed_and_error_episodes(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=3)

        await ParallelRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: _MaterializeEnv(),
        ).execute_run(run.id)
        prepared_before_retry = database.connection.execute(
            "SELECT materialization_key, payload_hash "
            "FROM prepared_tasks WHERE run_id = ? ORDER BY materialization_key",
            (run.id,),
        ).fetchall()
        frozen_revision_before_retry = database.connection.execute(
            "SELECT target_revision_id FROM lanes WHERE run_id = ?",
            (run.id,),
        ).fetchone()["target_revision_id"]

        first_attempt_id = database.connection.execute(
            "SELECT id FROM run_attempts WHERE run_id = ? AND attempt_no = 1",
            (run.id,),
        ).fetchone()["id"]
        rows = database.connection.execute(
            """
            SELECT ea.id, e.trial_id
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            WHERE la.run_attempt_id = ?
            ORDER BY e.trial_id
            """,
            (first_attempt_id,),
        ).fetchall()
        assert [row["trial_id"] for row in rows] == [0, 1, 2]
        database.connection.execute(
            "UPDATE episode_attempts SET outcome = 'FAIL', error_code = 'ASSERTION_FAILURE' WHERE id = ?",
            (rows[1]["id"],),
        )
        database.connection.execute(
            "UPDATE episode_attempts SET outcome = 'ERROR', error_code = 'EXECUTION_ERROR' WHERE id = ?",
            (rows[2]["id"],),
        )
        database.connection.commit()

        retry = RunService(
            database,
            settings,
            supervisor=FakeRunSupervisor(),
        ).retry_run(run.id)

        await ParallelRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_pool_factory=lambda lane: _FakeEnvPool(2),
            agent_factory=lambda lane: _CompletingAgent(),
            env_factory=lambda lane: _MaterializeEnv(),
        ).execute_run(run.id)

        retried_rows = database.connection.execute(
            """
            SELECT e.trial_id, ea.outcome, ea.artifact_root, la.artifact_root AS lane_artifact_root
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            WHERE la.run_attempt_id = ?
            ORDER BY e.trial_id
            """,
            (retry["run_attempt_id"],),
        ).fetchall()

        assert [row["trial_id"] for row in retried_rows] == [1, 2]
        assert [row["outcome"] for row in retried_rows] == ["PASS", "PASS"]
        assert {row["lane_artifact_root"] for row in retried_rows} == {
            "lanes/candidate/attempts/0002"
        }
        assert all("/attempts/0002/" in row["artifact_root"] for row in retried_rows)
        prepared_after_retry = database.connection.execute(
            "SELECT materialization_key, payload_hash "
            "FROM prepared_tasks WHERE run_id = ? ORDER BY materialization_key",
            (run.id,),
        ).fetchall()
        assert [tuple(row) for row in prepared_after_retry] == [
            tuple(row) for row in prepared_before_retry
        ]
        assert database.connection.execute(
            "SELECT target_revision_id FROM lanes WHERE run_id = ?",
            (run.id,),
        ).fetchone()["target_revision_id"] == frozen_revision_before_retry
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_retry_execution_runs_only_selected_episode_pair_on_current_attempt(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        ProjectRepository(database).create("Paired retry")
        baseline_target = _make_target(database, name="baseline", revision="seed-v1")
        candidate_target = _make_target(database, name="candidate", revision="seed-v1")
        run = _create_paired_run(
            database,
            settings,
            baseline_target_id=baseline_target,
            candidate_target_id=candidate_target,
            repeat_n=3,
        )

        from test_platform.services.execution import PairedSerialRunExecutor

        initial_factory = _PairedTaskFactory()
        initial_envs = iter([
            _PairedEnv(label="materialize"),
            _PairedEnv(label="baseline"),
            _PairedEnv(label="candidate"),
        ])
        initial_agents = {
            "baseline": _ScriptedAgent(succeed=True),
            "candidate": _ScriptedAgent(succeed=True),
        }
        await PairedSerialRunExecutor(
            database,
            settings,
            task_factory=initial_factory,
            env_factory=lambda lane: next(initial_envs),
            agent_factory=lambda lane: initial_agents[lane.lane_key],
        ).execute_run(run.id)

        candidate_trial_1 = database.connection.execute(
            """
            SELECT ea.id, e.episode_key
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            JOIN lanes AS l ON l.id = la.lane_id
            JOIN run_attempts AS ra ON ra.id = la.run_attempt_id
            WHERE ra.attempt_no = 1
              AND e.trial_id = 1
              AND l.lane_key = 'candidate'
            """
        ).fetchone()
        database.connection.execute(
            "UPDATE episode_attempts SET outcome = 'FAIL', error_code = 'ASSERTION_FAILURE' WHERE id = ?",
            (candidate_trial_1["id"],),
        )
        database.connection.commit()

        retry = RunService(
            database,
            settings,
            supervisor=FakeRunSupervisor(),
        ).retry_run(run.id)
        assert retry["selected_lane_episodes"] == [
            {
                "episode_key": candidate_trial_1["episode_key"],
                "lane_key": "candidate",
                "reason": "retry_failed",
            }
        ]

        retry_factory = _PairedTaskFactory()
        retry_envs = iter([
            _PairedEnv(label="baseline-retry"),
            _PairedEnv(label="candidate-retry"),
        ])
        retry_agents = {
            "baseline": _ScriptedAgent(succeed=True),
            "candidate": _ScriptedAgent(succeed=True),
        }
        await PairedSerialRunExecutor(
            database,
            settings,
            task_factory=retry_factory,
            env_factory=lambda lane: next(retry_envs),
            agent_factory=lambda lane: retry_agents[lane.lane_key],
        ).execute_run(run.id)

        retried_attempts = database.connection.execute(
            """
            SELECT l.lane_key, e.trial_id, ea.outcome, la.artifact_root AS lane_artifact_root
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE la.run_attempt_id = ?
            ORDER BY l.lane_key, e.trial_id
            """,
            (retry["run_attempt_id"],),
        ).fetchall()

        assert [(row["lane_key"], row["trial_id"], row["outcome"]) for row in retried_attempts] == [
            ("baseline", 1, "PASS"),
            ("candidate", 1, "PASS"),
        ]
        assert {row["lane_artifact_root"] for row in retried_attempts} == {
            "lanes/baseline/attempts/0002",
            "lanes/candidate/attempts/0002",
        }
    finally:
        database.close()
