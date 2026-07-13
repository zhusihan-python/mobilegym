import sqlite3
import shutil

import pytest
from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.persistence.migrations import MIGRATIONS_DIR, apply_migrations


def _table_names(database_path):
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}


def _migration_rows(database_path):
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()


def test_database_initialization_creates_minimum_schema_and_migration_record(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)

    try:
        database.initialize()
    finally:
        database.close()

    assert {
        "schema_migrations",
        "projects",
        "runs",
        "targets",
        "target_revisions",
        "workflows",
        "workflow_versions",
        "run_attempts",
        "workflow_node_runs",
        "lanes",
        "lane_attempts",
        "prepared_tasks",
        "episodes",
        "episode_attempts",
        "events",
        "idempotency_keys",
        "comparisons",
        "comparison_pairs",
        "reports",
        "quality_gate_results",
        "baselines",
        "diagnostics",
        "artifacts",
        "run_attempt_episode_selection",
    } <= _table_names(settings.database_path)
    assert _migration_rows(settings.database_path) == [
        (1, "0001_initial.sql"),
        (2, "0002_project_workspace.sql"),
        (3, "0003_targets.sql"),
        (4, "0004_workflows.sql"),
        (5, "0005_run_planning.sql"),
        (6, "0006_serial_execution.sql"),
        (7, "0007_event_envelope.sql"),
        (8, "0008_comparison.sql"),
        (9, "0009_reports_baselines.sql"),
        (10, "0010_diagnostics_artifacts.sql"),
        (11, "0011_retry_resume_selection.sql"),
        (12, "0012_manual_sequence_episode_metadata.sql"),
        (13, "0013_run_attempt_compatibility.sql"),
        (14, "0014_named_baselines.sql"),
    ]


def test_diagnostics_and_artifacts_tables_are_created(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    try:
        database.initialize()
        diagnostic_columns = {
            row[1]
            for row in database.connection.execute("PRAGMA table_info(diagnostics)")
        }
        artifact_columns = {
            row[1]
            for row in database.connection.execute("PRAGMA table_info(artifacts)")
        }

        assert {
            "id",
            "run_id",
            "run_attempt_id",
            "entity_type",
            "code",
            "category",
            "phase",
            "severity",
            "retryable",
            "message",
            "raw_json",
            "artifact_refs_json",
            "input_hash",
            "created_at",
        } <= diagnostic_columns
        assert {
            "id",
            "run_id",
            "run_attempt_id",
            "episode_attempt_id",
            "kind",
            "relative_path",
            "media_type",
            "size_bytes",
            "sha256",
            "created_at",
        } <= artifact_columns
    finally:
        database.close()


def test_retry_resume_selection_table_is_created(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    try:
        database.initialize()
        columns = {
            row[1]
            for row in database.connection.execute(
                "PRAGMA table_info(run_attempt_episode_selection)"
            )
        }
        assert {
            "id",
            "run_attempt_id",
            "lane_id",
            "episode_id",
            "reason",
            "created_at",
        } <= columns
    finally:
        database.close()


def test_manual_sequence_episode_metadata_columns_are_nullable(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    try:
        database.initialize()
        columns = {
            row[1]: row
            for row in database.connection.execute("PRAGMA table_info(episodes)")
        }
        assert {"sequence_index", "sequence_group_id"} <= set(columns)
        assert columns["sequence_index"][3] == 0
        assert columns["sequence_group_id"][3] == 0
    finally:
        database.close()


def test_event_envelope_columns_are_nullable_and_backward_compatible(tmp_path):
    """0007 adds envelope columns; VS-05's run.created rows (which predate them)
    must still be readable, and the new columns must accept NULL."""
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    try:
        database.initialize()
        connection = database.connection

        connection.execute(
            "INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
            "VALUES ('p1','P','p','p',NULL,'2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
            "VALUES ('w1','p1','W','{}','2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO workflow_versions "
            "(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
            "VALUES ('wv1','w1',1,'published','{}','h','2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO runs "
            "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
            " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at) "
            "VALUES ('r1','p1','wv1',NULL,'queued','{}','h','runs/r1',1,NULL,"
            " '2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
        )
        # A legacy-style event row (VS-05 shape: only the original columns).
        connection.execute(
            "INSERT INTO events (id, run_id, sequence, type, entity_type, entity_id, occurred_at, payload_json) "
            "VALUES ('e1','r1',1,'run.created','run','r1','2026-07-04T00:00:00.000Z','{\"state\":\"queued\"}')"
        )
        connection.commit()

        # Legacy row reads back with NULL envelope fields and default payload_version.
        legacy = connection.execute(
            "SELECT * FROM events WHERE id = 'e1'"
        ).fetchone()
        assert legacy["run_attempt_id"] is None
        assert legacy["lane_id"] is None
        assert legacy["episode_id"] is None
        assert legacy["worker_id"] is None
        assert legacy["payload_version"] == 1

        # A new-style row populates every non-FK envelope column.
        connection.execute(
            "INSERT INTO events (id, run_id, sequence, type, occurred_at, payload_json, "
            " payload_version, lane_id, lane_attempt_id, episode_id, "
            " episode_attempt_id, worker_id, entity_type, entity_id) "
            "VALUES ('e2','r1',2,'episode.completed','2026-07-04T00:00:00.000Z','{}',"
            " 2,'lane1','la1','ep1','ea1','w0','episode','ep1')"
        )
        connection.commit()
        full = connection.execute("SELECT * FROM events WHERE id = 'e2'").fetchone()
        assert full["lane_id"] == "lane1"
        assert full["episode_id"] == "ep1"
        assert full["worker_id"] == "w0"
        assert full["payload_version"] == 2
    finally:
        database.close()


def test_idempotency_keys_have_response_json_column(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    try:
        database.initialize()
        columns = {
            row[1]
            for row in database.connection.execute("PRAGMA table_info(idempotency_keys)")
        }
        assert "response_json" in columns
    finally:
        database.close()


def test_database_initialization_is_idempotent(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)

    try:
        database.initialize()
        database.initialize()
    finally:
        database.close()

    assert _migration_rows(settings.database_path) == [
        (1, "0001_initial.sql"),
        (2, "0002_project_workspace.sql"),
        (3, "0003_targets.sql"),
        (4, "0004_workflows.sql"),
        (5, "0005_run_planning.sql"),
        (6, "0006_serial_execution.sql"),
        (7, "0007_event_envelope.sql"),
        (8, "0008_comparison.sql"),
        (9, "0009_reports_baselines.sql"),
        (10, "0010_diagnostics_artifacts.sql"),
        (11, "0011_retry_resume_selection.sql"),
        (12, "0012_manual_sequence_episode_metadata.sql"),
        (13, "0013_run_attempt_compatibility.sql"),
        (14, "0014_named_baselines.sql"),
    ]


def test_baseline_catalog_migration_upgrades_legacy_rows_and_releases_archived_names(
    tmp_path,
):
    legacy_migrations = tmp_path / "legacy_migrations"
    legacy_migrations.mkdir()
    for migration_path in MIGRATIONS_DIR.glob("*.sql"):
        if int(migration_path.name.split("_", 1)[0]) <= 13:
            shutil.copy2(migration_path, legacy_migrations / migration_path.name)

    database_path = tmp_path / "platform.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        apply_migrations(connection, legacy_migrations)
        connection.execute(
            "INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
            "VALUES ('p1','Project','project','project',NULL,'2026-07-13T00:00:00.000Z','2026-07-13T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
            "VALUES ('w1','p1','Workflow','{}','2026-07-13T00:00:00.000Z','2026-07-13T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO workflow_versions "
            "(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
            "VALUES ('wv1','w1',1,'published','{}','hash','2026-07-13T00:00:00.000Z','2026-07-13T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO runs "
            "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
            "artifact_root, next_event_sequence, created_at, updated_at) "
            "VALUES ('r1','p1','wv1','Source run','completed','{}','run-hash','runs/r1',1,"
            "'2026-07-13T00:00:00.000Z','2026-07-13T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO run_attempts "
            "(id, run_id, attempt_no, reason, state, created_at) "
            "VALUES ('ra1','r1',1,'initial','completed','2026-07-13T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO reports "
            "(id, run_id, run_attempt_id, schema_version, input_hash, report_json, created_at) "
            "VALUES ('report1','r1','ra1',2,'report-hash','{}','2026-07-13T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO baselines "
            "(id, report_id, run_id, project_id, workflow_version_id, run_plan_hash, "
            "task_source_digest, target_revision_ids_json, lane_key, target_revision_id, created_at) "
            "VALUES ('legacy-a','report1','r1','p1','wv1','run-hash','tasks-hash',"
            "'{\"candidate\":\"rev1\"}','candidate','rev1','2026-07-13T00:00:00.000Z')"
        )
        connection.commit()

        apply_migrations(connection)

        legacy = connection.execute(
            "SELECT display_name, name_key, archived_at FROM baselines WHERE id = 'legacy-a'"
        ).fetchone()
        assert dict(legacy) == {
            "display_name": "Legacy baseline legacy-a",
            "name_key": "legacy baseline legacy-a",
            "archived_at": None,
        }

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO baselines "
                "(id, report_id, run_id, project_id, workflow_version_id, run_plan_hash, "
                "task_source_digest, target_revision_ids_json, lane_key, target_revision_id, "
                "display_name, name_key, archived_at, created_at) "
                "VALUES ('duplicate','report1','r1','p1','wv1','run-hash','tasks-hash',"
                "'{\"candidate\":\"rev1\"}','candidate','rev1','Legacy baseline legacy-a',"
                "'legacy baseline legacy-a',NULL,'2026-07-13T00:01:00.000Z')"
            )
        connection.rollback()
        connection.execute(
            "UPDATE baselines SET archived_at = '2026-07-13T00:02:00.000Z' "
            "WHERE id = 'legacy-a'"
        )
        connection.execute(
            "INSERT INTO baselines "
            "(id, report_id, run_id, project_id, workflow_version_id, run_plan_hash, "
            "task_source_digest, target_revision_ids_json, lane_key, target_revision_id, "
            "display_name, name_key, archived_at, created_at) "
            "VALUES ('reused','report1','r1','p1','wv1','run-hash','tasks-hash',"
            "'{\"candidate\":\"rev1\"}','candidate','rev1','Legacy baseline legacy-a',"
            "'legacy baseline legacy-a',NULL,'2026-07-13T00:03:00.000Z')"
        )
        connection.commit()

        settings = PlatformSettings(
            database_path=database_path,
            runs_dir=tmp_path / "runs",
        )
        connection.close()
        with TestClient(create_app(settings)) as client:
            listed = client.get(
                "/api/platform/v1/projects/p1/baselines?include_archived=true"
            )
            assert listed.status_code == 200
            legacy_summary = next(
                item for item in listed.json()["items"] if item["id"] == "legacy-a"
            )
            assert legacy_summary["display_name"] == "Legacy baseline legacy-a"
            assert legacy_summary["archived_at"] == "2026-07-13T00:02:00.000Z"

            detail = client.get("/api/platform/v1/baselines/legacy-a")
            assert detail.status_code == 200
            assert detail.json()["baseline"] == legacy_summary
            assert detail.json()["source_report"] == {
                "id": "report1",
                "run_id": "r1",
                "run_attempt_id": "ra1",
                "schema_version": 2,
                "href": "/api/platform/v1/reports/report1",
            }
    finally:
        connection.close()
