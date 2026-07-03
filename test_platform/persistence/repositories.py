from datetime import UTC, datetime
import sqlite3

from test_platform.domain.ids import new_id
from test_platform.domain.projects import (
    DuplicateProjectName,
    Project,
    ProjectNotFound,
    clean_project_name,
    next_available_slug,
    normalize_project_name,
)
from test_platform.persistence.database import Database


class ProjectRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, name: str) -> Project:
        cleaned_name = clean_project_name(name)
        name_key = normalize_project_name(cleaned_name)
        self._ensure_active_name_available(name_key)

        existing_slugs = self._existing_slugs()
        now = _utc_timestamp()
        project_id = new_id()
        slug = next_available_slug(cleaned_name, existing_slugs)

        self.database.connection.execute(
            """
            INSERT INTO projects (
              id, name, slug, name_key, archived_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?)
            """,
            (project_id, cleaned_name, slug, name_key, now, now),
        )
        self.database.connection.commit()
        return self.get(project_id)

    def list(self, *, include_archived: bool = False) -> list[Project]:
        where_clause = "" if include_archived else "WHERE archived_at IS NULL"
        rows = self.database.connection.execute(
            f"""
            SELECT id, name, slug, archived_at, created_at, updated_at
            FROM projects
            {where_clause}
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        return [_map_project(row) for row in rows]

    def get(self, project_id: str) -> Project:
        row = self.database.connection.execute(
            """
            SELECT id, name, slug, archived_at, created_at, updated_at
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return _map_project(row)

    def rename(self, project_id: str, name: str) -> Project:
        existing = self.get(project_id)
        cleaned_name = clean_project_name(name)
        name_key = normalize_project_name(cleaned_name)
        self._ensure_active_name_available(name_key, excluding_project_id=project_id)

        self.database.connection.execute(
            """
            UPDATE projects
            SET name = ?, name_key = ?, updated_at = ?
            WHERE id = ?
            """,
            (cleaned_name, name_key, _utc_timestamp(), existing.id),
        )
        self.database.connection.commit()
        return self.get(project_id)

    def archive(self, project_id: str) -> Project:
        self.get(project_id)
        now = _utc_timestamp()
        self.database.connection.execute(
            """
            UPDATE projects
            SET archived_at = COALESCE(archived_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (now, now, project_id),
        )
        self.database.connection.commit()
        return self.get(project_id)

    def _ensure_active_name_available(
        self,
        name_key: str,
        *,
        excluding_project_id: str | None = None,
    ) -> None:
        params: tuple[str, ...]
        exclusion = ""
        if excluding_project_id is None:
            params = (name_key,)
        else:
            exclusion = "AND id <> ?"
            params = (name_key, excluding_project_id)

        row = self.database.connection.execute(
            f"""
            SELECT id
            FROM projects
            WHERE name_key = ?
              AND archived_at IS NULL
              {exclusion}
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is not None:
            raise DuplicateProjectName(name_key)

    def _existing_slugs(self) -> set[str]:
        rows = self.database.connection.execute("SELECT slug FROM projects").fetchall()
        return {row["slug"] for row in rows}


def _map_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        archived_at=row["archived_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
