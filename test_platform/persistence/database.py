import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from test_platform.config import PlatformSettings
from test_platform.persistence.migrations import (
    applied_versions,
    apply_migrations,
    discover_migrations,
)


class Database:
    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings
        self.path = Path(settings.database_path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.settings.runs_dir.mkdir(parents=True, exist_ok=True)
            connection = self._connection or self._connect()
            apply_migrations(connection)
            self._connection = connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def readiness(self) -> dict[str, Any]:
        checks = {
            "database": self._database_check(),
            "migrations": self._migration_check(),
            "runs_dir": self._runs_dir_check(),
        }
        return {
            "ready": all(check["ready"] for check in checks.values()),
            "checks": checks,
        }

    def list_runs(self) -> list[dict[str, Any]]:
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT id, name, state, created_at, started_at, ended_at
            FROM runs
            ORDER BY created_at DESC, id ASC
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "state": row["state"],
                "progress": {},
                "lanes": [],
                "gate_verdict": None,
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database has not been initialized.")
        return self._connection

    def _database_check(self) -> dict[str, object]:
        if self._connection is None:
            return {
                "ready": False,
                "message": "Database has not been initialized.",
            }

        try:
            self._connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return {"ready": False, "message": "SQLite database is not reachable."}

        return {"ready": True, "message": "SQLite database is ready."}

    def _migration_check(self) -> dict[str, object]:
        if self._connection is None:
            return {"ready": False, "message": "Migrations have not run."}

        expected = {migration.version for migration in discover_migrations()}
        try:
            applied = applied_versions(self._connection)
        except sqlite3.Error:
            return {"ready": False, "message": "Migrations have not run."}

        missing = sorted(expected - applied)
        if missing:
            return {
                "ready": False,
                "message": f"Missing migrations: {', '.join(str(v) for v in missing)}.",
            }

        return {"ready": True, "message": "All migrations applied."}

    def _runs_dir_check(self) -> dict[str, object]:
        runs_dir = self.settings.runs_dir
        if not runs_dir.exists():
            return {
                "ready": False,
                "message": "Runs directory has not been initialized.",
            }
        if not runs_dir.is_dir():
            return {"ready": False, "message": "Runs path is not a directory."}

        try:
            with tempfile.NamedTemporaryFile(dir=runs_dir, delete=True):
                pass
        except OSError:
            return {"ready": False, "message": "Runs directory is not writable."}

        return {"ready": True, "message": "Runs directory is writable."}
