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

    assert {"schema_migrations", "projects", "runs"} <= _table_names(
        settings.database_path
    )
    assert _migration_rows(settings.database_path) == [
        (1, "0001_initial.sql"),
        (2, "0002_project_workspace.sql"),
    ]


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
    ]
