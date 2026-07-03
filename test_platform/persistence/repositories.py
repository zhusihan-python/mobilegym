from datetime import UTC, datetime
import json
import sqlite3
from typing import Any

from test_platform.domain.ids import new_id
from test_platform.domain.projects import (
    DuplicateProjectName,
    Project,
    ProjectNotFound,
    clean_project_name,
    next_available_slug,
    normalize_project_name,
)
from test_platform.domain.targets import (
    DuplicateTargetName,
    Target,
    TargetNotFound,
    TargetRevision,
    canonical_metadata_hash,
    new_target_id,
    new_target_revision_id,
    utc_timestamp,
    validate_target_config,
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


class TargetRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, *, project_id: str, name: str, config: dict[str, Any]) -> Target:
        ProjectRepository(self.database).get(project_id)
        normalized_config = validate_target_config(config)
        target_id = new_target_id()
        now = _utc_timestamp()

        try:
            self.database.connection.execute(
                """
                INSERT INTO targets (
                  id, project_id, name, kind, enabled, config_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    target_id,
                    project_id,
                    name.strip(),
                    normalized_config["kind"],
                    json.dumps(normalized_config, sort_keys=True, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self.database.connection.commit()
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise DuplicateTargetName(name) from exc
            raise

        return self.get(target_id)

    def list(self, *, project_id: str) -> list[tuple[Target, TargetRevision | None]]:
        rows = self.database.connection.execute(
            """
            SELECT id, project_id, name, kind, enabled, config_json, created_at, updated_at
            FROM targets
            WHERE project_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (project_id,),
        ).fetchall()
        targets = [_map_target(row) for row in rows]
        return [(target, self.latest_revision(target.id)) for target in targets]

    def get(self, target_id: str) -> Target:
        row = self.database.connection.execute(
            """
            SELECT id, project_id, name, kind, enabled, config_json, created_at, updated_at
            FROM targets
            WHERE id = ?
            """,
            (target_id,),
        ).fetchone()
        if row is None:
            raise TargetNotFound(target_id)
        return _map_target(row)

    def latest_revision(self, target_id: str) -> TargetRevision | None:
        row = self.database.connection.execute(
            """
            SELECT id, target_id, metadata_json, metadata_hash, health_status, warnings_json, resolved_at
            FROM target_revisions
            WHERE target_id = ?
            ORDER BY resolved_at DESC, id DESC
            LIMIT 1
            """,
            (target_id,),
        ).fetchone()
        return _map_target_revision(row) if row is not None else None

    def record_revision(
        self,
        *,
        target_id: str,
        metadata: dict[str, Any],
        warnings: list[str],
        health_status: str,
    ) -> TargetRevision:
        self.get(target_id)
        metadata_hash = canonical_metadata_hash(metadata)
        existing = self.database.connection.execute(
            """
            SELECT id, target_id, metadata_json, metadata_hash, health_status, warnings_json, resolved_at
            FROM target_revisions
            WHERE target_id = ? AND metadata_hash = ?
            """,
            (target_id, metadata_hash),
        ).fetchone()
        if existing is not None:
            return _map_target_revision(existing)

        resolved_at = str(metadata.get("resolved_at") or utc_timestamp())
        self.database.connection.execute(
            """
            INSERT INTO target_revisions (
              id, target_id, metadata_json, metadata_hash, health_status, warnings_json, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_target_revision_id(),
                target_id,
                json.dumps(metadata, sort_keys=True, ensure_ascii=False),
                metadata_hash,
                health_status,
                json.dumps(warnings, ensure_ascii=False),
                resolved_at,
            ),
        )
        self.database.connection.commit()
        revision = self.latest_revision(target_id)
        if revision is None:
            raise TargetNotFound(target_id)
        return revision


def _map_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        archived_at=row["archived_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_target(row: sqlite3.Row) -> Target:
    return Target(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        kind=row["kind"],
        enabled=bool(row["enabled"]),
        config=json.loads(row["config_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_target_revision(row: sqlite3.Row) -> TargetRevision:
    return TargetRevision(
        id=row["id"],
        target_id=row["target_id"],
        metadata=json.loads(row["metadata_json"]),
        metadata_hash=row["metadata_hash"],
        health_status=row["health_status"],
        warnings=json.loads(row["warnings_json"]),
        resolved_at=row["resolved_at"],
    )


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
