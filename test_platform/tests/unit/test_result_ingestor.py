from __future__ import annotations

import json

import pytest

from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.services.execution import ResultIngestor


NOW = "2026-07-03T12:00:00.000Z"


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _insert_graph(database: Database) -> dict[str, str]:
    connection = database.connection
    connection.execute(
        """
        INSERT INTO projects (id, name, slug, name_key, created_at, updated_at)
        VALUES ('project-1', 'Project', 'project', 'project', ?, ?)
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO targets (
          id, project_id, name, kind, enabled, config_json, created_at, updated_at
        )
        VALUES ('target-1', 'project-1', 'Target', 'simulator', 1, '{}', ?, ?)
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO target_revisions (
          id, target_id, metadata_json, metadata_hash, health_status, warnings_json, resolved_at
        )
        VALUES ('revision-1', 'target-1', '{}', 'sha256:revision', 'healthy', '[]', ?)
        """,
        (NOW,),
    )
    connection.execute(
        """
        INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at)
        VALUES ('workflow-1', 'project-1', 'Workflow', '{}', ?, ?)
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO workflow_versions (
          id, workflow_id, version_no, status, definition_json,
          definition_hash, created_at, published_at
        )
        VALUES ('version-1', 'workflow-1', 1, 'published', '{}', 'sha256:workflow', ?, ?)
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO runs (
          id, project_id, workflow_version_id, name, state,
          run_plan_json, run_plan_hash, artifact_root,
          next_event_sequence, created_at, updated_at
        )
        VALUES (
          'run-1', 'project-1', 'version-1', 'Run', 'running',
          '{}', 'sha256:run', 'runs/run-1',
          1, ?, ?
        )
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, created_at, started_at)
        VALUES ('run-attempt-1', 'run-1', 1, 'initial', 'running', ?, ?)
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO lanes (
          id, run_id, lane_key, role, target_id, target_revision_id,
          runner_config_json, reproducibility_fingerprint, created_at
        )
        VALUES (
          'lane-1', 'run-1', 'candidate', 'candidate', 'target-1', 'revision-1',
          '{}', 'sha256:lane', ?
        )
        """,
        (NOW,),
    )
    connection.execute(
        """
        INSERT INTO lane_attempts (
          id, lane_id, run_attempt_id, state, artifact_root, created_at, started_at
        )
        VALUES ('lane-attempt-1', 'lane-1', 'run-attempt-1', 'running', 'lanes/candidate', ?, ?)
        """,
        (NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO episodes (
          id, run_id, episode_key, materialization_key, pair_key,
          task_base_id, task_id, instance_id, instance_seed,
          template_index, trial_id, max_steps, created_at
        )
        VALUES (
          'episode-1', 'run-1', 'fake.Sample|i0|s1|r1|t0', 'fake.Sample|i0|s1|r1',
          'fake.Sample|i0|s1|r1|t0', 'fake.Sample', 'fake.Sample',
          0, 1, NULL, 0, 15, ?
        )
        """,
        (NOW,),
    )
    connection.commit()
    return {
        "run_id": "run-1",
        "lane_attempt_id": "lane-attempt-1",
        "episode_key": "fake.Sample|i0|s1|r1|t0",
    }


@pytest.mark.parametrize(
    ("result", "expected_outcome", "expected_error_code"),
    [
        ({"id": "fake.Sample", "trial_id": 0, "is_success": True, "is_error": False}, "PASS", None),
        ({"id": "fake.Sample", "trial_id": 0, "is_success": False, "is_error": False}, "FAIL", "ASSERTION_FAILURE"),
        (
            {
                "id": "fake.Sample",
                "trial_id": 0,
                "is_success": False,
                "is_error": True,
                "execution": {"error": "RuntimeError: boom"},
            },
            "ERROR",
            "EXECUTION_ERROR",
        ),
    ],
)
def test_result_ingestor_persists_terminal_episode_lane_and_run_state(
    tmp_path,
    result,
    expected_outcome,
    expected_error_code,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        graph = _insert_graph(database)

        ingested = ResultIngestor(database).ingest_episode_result(
            run_id=graph["run_id"],
            lane_attempt_id=graph["lane_attempt_id"],
            episode_key=graph["episode_key"],
            result=result,
            artifact_root="lanes/candidate/trajectory/fake_Sample",
        )

        assert ingested["outcome"] == expected_outcome
        row = database.connection.execute(
            "SELECT state, outcome, error_code, result_json, artifact_root FROM episode_attempts"
        ).fetchone()
        assert row["state"] == "completed"
        assert row["outcome"] == expected_outcome
        assert row["error_code"] == expected_error_code
        assert json.loads(row["result_json"])["id"] == "fake.Sample"
        assert row["artifact_root"] == "lanes/candidate/trajectory/fake_Sample"

        assert database.connection.execute("SELECT state FROM lane_attempts").fetchone()[0] == "completed"
        assert database.connection.execute("SELECT state FROM run_attempts").fetchone()[0] == "completed"
        assert database.connection.execute("SELECT state FROM runs").fetchone()[0] == "completed"
    finally:
        database.close()
