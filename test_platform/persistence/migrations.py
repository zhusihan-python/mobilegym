from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in migrations_dir.glob("*.sql"):
        version_token = path.stem.split("_", 1)[0]
        if not version_token.isdigit():
            continue
        migrations.append(Migration(int(version_token), path.name, path))
    return sorted(migrations, key=lambda migration: migration.version)


def apply_migrations(
    connection: sqlite3.Connection,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> None:
    _ensure_schema_migrations(connection)
    applied = applied_versions(connection)

    for migration in discover_migrations(migrations_dir):
        if migration.version in applied:
            continue
        _apply_one(connection, migration)
        applied.add(migration.version)


def applied_versions(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def _ensure_schema_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _apply_one(connection: sqlite3.Connection, migration: Migration) -> None:
    sql = migration.path.read_text(encoding="utf-8")
    escaped_name = migration.name.replace("'", "''")
    applied_at = _utc_timestamp()
    script = f"""
    BEGIN IMMEDIATE;
    {sql}
    INSERT INTO schema_migrations (version, name, applied_at)
    VALUES ({migration.version}, '{escaped_name}', '{applied_at}');
    COMMIT;
    """

    try:
        connection.executescript(script)
    except sqlite3.Error:
        connection.rollback()
        raise


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
