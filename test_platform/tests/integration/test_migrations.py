import sqlite3

from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database


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
    ]


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
    ]
