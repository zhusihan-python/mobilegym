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
from test_platform.domain.runs import RunDetail, RunNotFound, RunSummary
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
from test_platform.domain.workflows import (
    Workflow,
    WorkflowNotFound,
    WorkflowVersion,
    WorkflowVersionNotFound,
    canonical_definition_hash,
    new_workflow_id,
    new_workflow_version_id,
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


class WorkflowRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        project_id: str,
        name: str,
        definition: dict[str, Any] | None,
    ) -> Workflow:
        ProjectRepository(self.database).get(project_id)
        workflow_id = new_workflow_id()
        now = _utc_timestamp()
        self.database.connection.execute(
            """
            INSERT INTO workflows (
              id, project_id, name, draft_definition_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                project_id,
                name.strip(),
                _definition_json(definition),
                now,
                now,
            ),
        )
        self.database.connection.commit()
        return self.get(workflow_id)

    def list(self, *, project_id: str) -> list[tuple[Workflow, WorkflowVersion | None]]:
        ProjectRepository(self.database).get(project_id)
        rows = self.database.connection.execute(
            """
            SELECT id, project_id, name, draft_definition_json, created_at, updated_at
            FROM workflows
            WHERE project_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (project_id,),
        ).fetchall()
        workflows = [_map_workflow(row) for row in rows]
        return [(workflow, self.latest_version(workflow.id)) for workflow in workflows]

    def get(self, workflow_id: str) -> Workflow:
        row = self.database.connection.execute(
            """
            SELECT id, project_id, name, draft_definition_json, created_at, updated_at
            FROM workflows
            WHERE id = ?
            """,
            (workflow_id,),
        ).fetchone()
        if row is None:
            raise WorkflowNotFound(workflow_id)
        return _map_workflow(row)

    def update_draft(
        self,
        *,
        workflow_id: str,
        definition: dict[str, Any],
        name: str | None = None,
    ) -> Workflow:
        workflow = self.get(workflow_id)
        self.database.connection.execute(
            """
            UPDATE workflows
            SET name = ?, draft_definition_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                (name or workflow.name).strip(),
                _definition_json(definition),
                _utc_timestamp(),
                workflow_id,
            ),
        )
        self.database.connection.commit()
        return self.get(workflow_id)

    def latest_version(self, workflow_id: str) -> WorkflowVersion | None:
        row = self.database.connection.execute(
            """
            SELECT id, workflow_id, version_no, status, definition_json,
                   definition_hash, created_at, published_at
            FROM workflow_versions
            WHERE workflow_id = ?
            ORDER BY version_no DESC
            LIMIT 1
            """,
            (workflow_id,),
        ).fetchone()
        return _map_workflow_version(row) if row is not None else None

    def get_version(self, workflow_version_id: str) -> WorkflowVersion:
        row = self.database.connection.execute(
            """
            SELECT id, workflow_id, version_no, status, definition_json,
                   definition_hash, created_at, published_at
            FROM workflow_versions
            WHERE id = ?
            """,
            (workflow_version_id,),
        ).fetchone()
        if row is None:
            raise WorkflowVersionNotFound(workflow_version_id)
        return _map_workflow_version(row)

    def publish(self, workflow_id: str) -> WorkflowVersion:
        workflow = self.get(workflow_id)
        if workflow.draft_definition is None:
            raise WorkflowNotFound(workflow_id)

        latest = self.latest_version(workflow_id)
        version_no = 1 if latest is None else latest.version_no + 1
        now = _utc_timestamp()
        definition = workflow.draft_definition
        self.database.connection.execute(
            """
            INSERT INTO workflow_versions (
              id, workflow_id, version_no, status, definition_json,
              definition_hash, created_at, published_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_workflow_version_id(),
                workflow_id,
                version_no,
                "published",
                _definition_json(definition),
                canonical_definition_hash(definition),
                now,
                now,
            ),
        )
        self.database.connection.commit()
        version = self.latest_version(workflow_id)
        if version is None:
            raise WorkflowNotFound(workflow_id)
        return version


class RunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, *, project_id: str | None = None) -> list[RunSummary]:
        where_clause = ""
        params: tuple[str, ...] = ()
        if project_id is not None:
            where_clause = "WHERE project_id = ?"
            params = (project_id,)
        rows = self.database.connection.execute(
            f"""
            SELECT id, project_id, workflow_version_id, name, state,
                   run_plan_hash, created_at, started_at, ended_at
            FROM runs
            {where_clause}
            ORDER BY created_at DESC, id ASC
            """,
            params,
        ).fetchall()
        return [self._summary(row) for row in rows]

    def get(self, run_id: str) -> RunDetail:
        row = self.database.connection.execute(
            """
            SELECT id, project_id, workflow_version_id, name, state,
                   run_plan_json, run_plan_hash, created_at, started_at, ended_at
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunNotFound(run_id)

        summary = self._summary(row)
        lane_rows = self.database.connection.execute(
            """
            SELECT l.id, l.lane_key, l.role, l.target_id, l.target_revision_id,
                   tr.metadata_hash
            FROM lanes AS l
            JOIN target_revisions AS tr ON tr.id = l.target_revision_id
            WHERE l.run_id = ?
            ORDER BY l.lane_key
            """,
            (run_id,),
        ).fetchall()
        episodes = self.database.connection.execute(
            """
            SELECT episode_key, materialization_key, pair_key, task_base_id, task_id,
                   instance_id, instance_seed, template_index, trial_id, max_steps
            FROM episodes
            WHERE run_id = ?
            ORDER BY episode_key
            """,
            (run_id,),
        ).fetchall()
        lane_attempts = self.database.connection.execute(
            """
            SELECT la.id, la.lane_id, l.lane_key, la.state, la.artifact_root,
                   la.started_at, la.ended_at
            FROM lane_attempts AS la
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE l.run_id = ?
            ORDER BY l.lane_key, la.created_at
            """,
            (run_id,),
        ).fetchall()
        episode_attempts = self.database.connection.execute(
            """
            SELECT e.episode_key, l.lane_key, ea.attempt_no, ea.state,
                   ea.outcome, ea.error_code, ea.artifact_root
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE e.run_id = ?
            ORDER BY e.episode_key, l.lane_key, ea.attempt_no
            """,
            (run_id,),
        ).fetchall()
        lanes = [
            {
                "id": lane["id"],
                "lane_key": lane["lane_key"],
                "role": lane["role"],
                "target_id": lane["target_id"],
                "target_revision_id": lane["target_revision_id"],
            }
            for lane in lane_rows
        ]
        return RunDetail(
            **{
                **summary.__dict__,
                "lanes": lanes,
                "run_plan": json.loads(row["run_plan_json"]),
                "target_revisions": [
                    {
                        "target_id": lane["target_id"],
                        "target_revision_id": lane["target_revision_id"],
                        "metadata_hash": lane["metadata_hash"],
                    }
                    for lane in lane_rows
                ],
                "episode_identities": [dict(episode) for episode in episodes],
                "lane_attempts": [dict(attempt) for attempt in lane_attempts],
                "episode_attempts": [dict(attempt) for attempt in episode_attempts],
            }
        )

    def _summary(self, row: sqlite3.Row) -> RunSummary:
        counts = self.database.connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM episodes WHERE run_id = ?) AS episodes,
              (SELECT COUNT(*) FROM lanes WHERE run_id = ?) AS lanes,
              (
                SELECT COUNT(DISTINCT e.id)
                FROM episodes AS e
                JOIN episode_attempts AS ea ON ea.episode_id = e.id
                WHERE e.run_id = ? AND ea.state = 'completed'
              ) AS completed_episodes
            """,
            (row["id"], row["id"], row["id"]),
        ).fetchone()
        lane_rows = self.database.connection.execute(
            """
            SELECT id, lane_key, role, target_id, target_revision_id
            FROM lanes
            WHERE run_id = ?
            ORDER BY lane_key
            """,
            (row["id"],),
        ).fetchall()
        planned_episodes = int(counts["episodes"])
        lane_count = int(counts["lanes"])
        return RunSummary(
            id=row["id"],
            project_id=row["project_id"],
            workflow_version_id=row["workflow_version_id"],
            name=row["name"],
            state=row["state"],
            fingerprint=row["run_plan_hash"],
            progress={
                "planned_episodes": planned_episodes,
                "planned_lane_episodes": planned_episodes * lane_count,
                "completed_episodes": int(counts["completed_episodes"]),
            },
            lanes=[dict(lane) for lane in lane_rows],
            gate_verdict=None,
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )


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


def _map_workflow(row: sqlite3.Row) -> Workflow:
    definition_json = row["draft_definition_json"]
    return Workflow(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        draft_definition=json.loads(definition_json) if definition_json else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_workflow_version(row: sqlite3.Row) -> WorkflowVersion:
    return WorkflowVersion(
        id=row["id"],
        workflow_id=row["workflow_id"],
        version_no=int(row["version_no"]),
        status=row["status"],
        definition=json.loads(row["definition_json"]),
        definition_hash=row["definition_hash"],
        created_at=row["created_at"],
        published_at=row["published_at"],
    )


def _definition_json(definition: dict[str, Any] | None) -> str | None:
    if definition is None:
        return None
    return json.dumps(definition, sort_keys=True, ensure_ascii=False)


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
