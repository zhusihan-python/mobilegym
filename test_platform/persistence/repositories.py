from datetime import UTC, datetime
import hashlib
import json
import mimetypes
import sqlite3
from pathlib import Path
from typing import Any

from test_platform.artifacts.paths import (
    ArtifactPathError,
    resolve_artifact_directory,
    resolve_artifact_path,
)
from test_platform.domain.canonical_json import canonical_json, canonical_sha256
from test_platform.domain.diagnostics.builder import build_diagnostics
from test_platform.domain.diagnostics.input import DiagnosticInput
from test_platform.domain.projects import (
    DuplicateProjectName,
    Project,
    ProjectNotFound,
    clean_project_name,
    next_available_slug,
    normalize_project_name,
)
from test_platform.domain.events import PersistedEvent
from test_platform.domain.ids import new_id
from test_platform.domain.reports.comparison import build_comparison_report
from test_platform.domain.reports.functional import build_functional_report
from test_platform.domain.reports.gates import evaluate_gates
from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.performance import build_performance_report
from test_platform.domain.reports.sequence import build_sequence_report
from test_platform.domain.runs import RunDetail, RunDomainError, RunNotFound, RunSummary
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
                   run_plan_json, run_plan_hash, created_at, started_at, ended_at
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
                   instance_id, instance_seed, template_index, trial_id, max_steps,
                   sequence_index, sequence_group_id
            FROM episodes
            WHERE run_id = ?
            ORDER BY sequence_index IS NULL, sequence_index, episode_key
            """,
            (run_id,),
        ).fetchall()
        lane_attempts = self.database.connection.execute(
            """
            SELECT la.id, la.lane_id, l.lane_key, la.run_attempt_id,
                   ra.attempt_no, ra.reason, la.state, la.artifact_root,
                   la.started_at, la.ended_at
            FROM lane_attempts AS la
            JOIN lanes AS l ON l.id = la.lane_id
            JOIN run_attempts AS ra ON ra.id = la.run_attempt_id
            WHERE l.run_id = ?
            ORDER BY ra.attempt_no, l.lane_key, la.created_at
            """,
            (run_id,),
        ).fetchall()
        run_attempts = self.database.connection.execute(
            """
            SELECT id, attempt_no, reason, state, started_at, ended_at, created_at
            FROM run_attempts
            WHERE run_id = ?
            ORDER BY attempt_no, created_at
            """,
            (run_id,),
        ).fetchall()
        episode_attempts = self.database.connection.execute(
            """
            SELECT e.episode_key, l.lane_key, la.run_attempt_id,
                   la.id AS lane_attempt_id, ea.id AS episode_attempt_id,
                   ra.attempt_no, ea.attempt_no AS episode_attempt_no,
                   ea.state, ea.outcome, ea.error_code, ea.artifact_root
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            JOIN lanes AS l ON l.id = la.lane_id
            JOIN run_attempts AS ra ON ra.id = la.run_attempt_id
            WHERE e.run_id = ?
            ORDER BY ra.attempt_no, e.episode_key, l.lane_key, ea.attempt_no
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
                "run_attempts": [dict(attempt) for attempt in run_attempts],
                "episode_identities": [dict(episode) for episode in episodes],
                "lane_attempts": [dict(attempt) for attempt in lane_attempts],
                "episode_attempts": [dict(attempt) for attempt in episode_attempts],
            }
        )

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[PersistedEvent]:
        """Return committed events for a run with sequence > after_sequence.

        Reads autocommit (no transaction) so SSE backlog queries never block the
        writer. Returns an empty list for unknown runs.
        """
        rows = self.database.connection.execute(
            """
            SELECT id, run_id, sequence, type, entity_type, entity_id,
                   occurred_at, payload_json, payload_version,
                   run_attempt_id, lane_id, lane_attempt_id,
                   episode_id, episode_attempt_id, worker_id
            FROM events
            WHERE run_id = ? AND sequence > ?
            ORDER BY sequence
            """,
            (run_id, after_sequence),
        ).fetchall()
        return [
            PersistedEvent(
                id=row["id"],
                run_id=row["run_id"],
                sequence=int(row["sequence"]),
                type=row["type"],
                occurred_at=row["occurred_at"],
                payload=json.loads(row["payload_json"]),
                payload_version=int(row["payload_version"]),
                run_attempt_id=row["run_attempt_id"],
                lane_id=row["lane_id"],
                lane_attempt_id=row["lane_attempt_id"],
                episode_id=row["episode_id"],
                episode_attempt_id=row["episode_attempt_id"],
                worker_id=row["worker_id"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
            )
            for row in rows
        ]

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
              ) AS completed_episodes,
              (
                SELECT COUNT(*)
                FROM episode_attempts AS ea
                JOIN episodes AS e ON e.id = ea.episode_id
                WHERE e.run_id = ? AND ea.state IN ('completed', 'cancelled')
              ) AS completed_lane_episodes
            """,
            (row["id"], row["id"], row["id"], row["id"]),
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
        gate_row = self.database.connection.execute(
            """
            SELECT verdict
            FROM quality_gate_results
            WHERE run_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
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
                "completed_lane_episodes": int(counts["completed_lane_episodes"]),
            },
            lanes=[dict(lane) for lane in lane_rows],
            gate_verdict=str(gate_row["verdict"]) if gate_row is not None else None,
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            imported=_imported_metadata(_json_or_empty_object(row["run_plan_json"])),
        )


class ReportInputRepository:
    """Build the stable raw input used by report builders.

    This intentionally does not reuse ``RunRepository.get`` because the UI DTO
    omits report-critical raw fields such as ``episode_attempts.id`` and
    ``result_json``.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_for_run(self, run_id: str) -> ReportInput:
        run = self.database.connection.execute(
            """
            SELECT id, project_id, workflow_version_id, run_plan_json,
                   run_plan_hash
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if run is None:
            raise RunNotFound(run_id)

        run_attempt = self.database.connection.execute(
            """
            SELECT id, run_id, attempt_no, reason, state, started_at, ended_at,
                   error_code, created_at
            FROM run_attempts
            WHERE run_id = ?
              AND state IN ('completed', 'failed')
            ORDER BY attempt_no DESC, id DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if run_attempt is None:
            raise RunNotFound(run_id)

        run_attempt_id = str(run_attempt["id"])
        run_plan = _json_or_empty_object(run["run_plan_json"])
        lanes = self._lanes_for_attempt(run_id, run_attempt_id)
        episodes = self._episodes(run_id)
        episode_attempts = self._episode_attempts(run_id, run_attempt_id)
        attempts_by_id = {attempt["id"]: attempt for attempt in episode_attempts}
        latest_attempts_by_lane_episode = _latest_attempts_by_lane_episode(
            episode_attempts
        )
        planned_lane_episodes = _planned_lane_episodes(
            episodes=episodes,
            lanes=lanes,
            latest_attempts=latest_attempts_by_lane_episode,
        )
        comparison = self._comparison(run_id, run_attempt_id, attempts_by_id)
        provenance = {
            "project_id": str(run["project_id"]),
            "run_id": str(run["id"]),
            "run_attempt_id": run_attempt_id,
            "workflow_version_id": str(run["workflow_version_id"]),
            "run_plan_hash": str(run["run_plan_hash"]),
            "task_source_digest": _task_source_digest(run_plan),
            "target_revision_ids": {
                lane["lane_key"]: lane["target_revision_id"] for lane in lanes
            },
        }
        imported = _imported_metadata(run_plan)
        if imported is not None:
            provenance["imported"] = imported
        return ReportInput.from_payload(
            run_id=str(run["id"]),
            run_attempt_id=run_attempt_id,
            provenance=provenance,
            planned_lane_episodes=planned_lane_episodes,
            episode_attempts=episode_attempts,
            comparison=comparison,
        )

    def _lanes_for_attempt(
        self,
        run_id: str,
        run_attempt_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            """
            SELECT l.id AS lane_id, l.lane_key, l.role, l.target_id,
                   l.target_revision_id, la.id AS lane_attempt_id,
                   la.state AS lane_attempt_state
            FROM lanes AS l
            LEFT JOIN lane_attempts AS la
              ON la.lane_id = l.id AND la.run_attempt_id = ?
            WHERE l.run_id = ?
            ORDER BY l.lane_key
            """,
            (run_attempt_id, run_id),
        ).fetchall()
        return [
            {
                "lane_id": str(row["lane_id"]),
                "lane_key": str(row["lane_key"]),
                "role": str(row["role"]),
                "target_id": str(row["target_id"]),
                "target_revision_id": str(row["target_revision_id"]),
                "lane_attempt_id": (
                    str(row["lane_attempt_id"])
                    if row["lane_attempt_id"] is not None
                    else None
                ),
                "lane_attempt_state": (
                    str(row["lane_attempt_state"])
                    if row["lane_attempt_state"] is not None
                    else None
                ),
            }
            for row in rows
        ]

    def _episodes(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            """
            SELECT id, episode_key, materialization_key, pair_key, task_base_id,
                   task_id, instance_id, instance_seed, template_index,
                   trial_id, max_steps, sequence_index, sequence_group_id
            FROM episodes
            WHERE run_id = ?
            ORDER BY sequence_index IS NULL, sequence_index, episode_key
            """,
            (run_id,),
        ).fetchall()
        return [
            {
                "episode_id": str(row["id"]),
                "episode_key": str(row["episode_key"]),
                "materialization_key": str(row["materialization_key"]),
                "pair_key": str(row["pair_key"]),
                "task_base_id": str(row["task_base_id"]),
                "task_id": str(row["task_id"]),
                "instance_id": int(row["instance_id"]),
                "instance_seed": int(row["instance_seed"]),
                "template_index": row["template_index"],
                "trial_id": int(row["trial_id"]),
                "max_steps": int(row["max_steps"]),
                "sequence_index": row["sequence_index"],
                "sequence_group_id": (
                    str(row["sequence_group_id"])
                    if row["sequence_group_id"] is not None
                    else None
                ),
            }
            for row in rows
        ]

    def _episode_attempts(
        self,
        run_id: str,
        run_attempt_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            """
            SELECT ea.id, ea.episode_id, e.episode_key, e.pair_key,
                   ea.lane_attempt_id, la.lane_id, l.lane_key, ea.attempt_no,
                   ea.state, ea.outcome, ea.error_code, ea.result_json,
                   ea.artifact_root, ea.started_at, ea.ended_at, ea.created_at
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE e.run_id = ?
              AND la.run_attempt_id = ?
            ORDER BY e.episode_key, l.lane_key, ea.attempt_no, ea.id
            """,
            (run_id, run_attempt_id),
        ).fetchall()
        return [_map_report_episode_attempt(row) for row in rows]

    def _comparison(
        self,
        run_id: str,
        run_attempt_id: str,
        attempts_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        row = self.database.connection.execute(
            """
            SELECT id, run_id, run_attempt_id, baseline_lane_id,
                   candidate_lane_id, policy_json, summary_json, created_at
            FROM comparisons
            WHERE run_id = ? AND run_attempt_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (run_id, run_attempt_id),
        ).fetchone()
        if row is None:
            return None

        pair_rows = self.database.connection.execute(
            """
            SELECT id, comparison_id, pair_key, baseline_episode_attempt_id,
                   candidate_episode_attempt_id, classification, integrity_json,
                   delta_json
            FROM comparison_pairs
            WHERE comparison_id = ?
            ORDER BY pair_key
            """,
            (row["id"],),
        ).fetchall()
        pairs = []
        for pair in pair_rows:
            baseline_attempt_id = pair["baseline_episode_attempt_id"]
            candidate_attempt_id = pair["candidate_episode_attempt_id"]
            pairs.append(
                {
                    "id": str(pair["id"]),
                    "comparison_id": str(pair["comparison_id"]),
                    "pair_key": str(pair["pair_key"]),
                    "baseline_episode_attempt_id": (
                        str(baseline_attempt_id)
                        if baseline_attempt_id is not None
                        else None
                    ),
                    "candidate_episode_attempt_id": (
                        str(candidate_attempt_id)
                        if candidate_attempt_id is not None
                        else None
                    ),
                    "classification": str(pair["classification"]),
                    "integrity": _json_or_empty_object(pair["integrity_json"]),
                    "delta": _json_or_empty_object(pair["delta_json"]),
                    "baseline_attempt": attempts_by_id.get(str(baseline_attempt_id))
                    if baseline_attempt_id is not None
                    else None,
                    "candidate_attempt": attempts_by_id.get(str(candidate_attempt_id))
                    if candidate_attempt_id is not None
                    else None,
                }
            )
        return {
            "id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "run_attempt_id": str(row["run_attempt_id"]),
            "baseline_lane_id": str(row["baseline_lane_id"]),
            "candidate_lane_id": str(row["candidate_lane_id"]),
            "policy": _json_or_empty_object(row["policy_json"]),
            "summary": _json_or_empty_object(row["summary_json"])
            if row["summary_json"] is not None
            else None,
            "created_at": str(row["created_at"]),
            "pairs": pairs,
        }


class DiagnosticInputRepository:
    """Build the stable raw input used by diagnostic builders."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_for_run(self, run_id: str) -> DiagnosticInput:
        report_input = ReportInputRepository(self.database).get_for_run(run_id)
        report = self._latest_report(report_input.run_id, report_input.run_attempt_id)
        gate_results = self._gate_results(
            report_input.run_id,
            report_input.run_attempt_id,
            report["id"] if report is not None else None,
        )
        return DiagnosticInput.from_payload(
            run_id=report_input.run_id,
            run_attempt_id=report_input.run_attempt_id,
            provenance=report_input.provenance,
            planned_lane_episodes=report_input.planned_lane_episodes,
            episode_attempts=report_input.episode_attempts,
            comparison=report_input.comparison,
            report=report,
            gate_results=gate_results,
        )

    def _latest_report(
        self,
        run_id: str,
        run_attempt_id: str,
    ) -> dict[str, Any] | None:
        row = self.database.connection.execute(
            """
            SELECT id, run_id, run_attempt_id, schema_version, input_hash,
                   report_json, created_at
            FROM reports
            WHERE run_id = ?
              AND run_attempt_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (run_id, run_attempt_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "run_attempt_id": str(row["run_attempt_id"]),
            "schema_version": int(row["schema_version"]),
            "input_hash": str(row["input_hash"]),
            "report": _json_or_empty_object(row["report_json"]),
            "created_at": str(row["created_at"]),
        }

    def _gate_results(
        self,
        run_id: str,
        run_attempt_id: str,
        report_id: str | None,
    ) -> list[dict[str, Any]]:
        if report_id is None:
            return []
        rows = self.database.connection.execute(
            """
            SELECT id, report_id, run_id, run_attempt_id, verdict,
                   thresholds_json, observed_json, reasons_json, created_at
            FROM quality_gate_results
            WHERE run_id = ?
              AND run_attempt_id = ?
              AND report_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (run_id, run_attempt_id, report_id),
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "report_id": str(row["report_id"]),
                "run_id": str(row["run_id"]),
                "run_attempt_id": str(row["run_attempt_id"]),
                "verdict": str(row["verdict"]),
                "thresholds": _json_or_empty_object(row["thresholds_json"]),
                "observed": _json_or_empty_object(row["observed_json"]),
                "reasons": _json_or_empty_array(row["reasons_json"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]


class ReportRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_or_create(self, run_id: str) -> dict[str, Any]:
        report_input = ReportInputRepository(self.database).get_for_run(run_id)
        existing = self._find_existing(report_input)
        if existing is not None:
            return existing

        report_id = new_id()
        now = _utc_timestamp()
        report = _build_report_payload(
            report_id=report_id,
            created_at=now,
            report_input=report_input,
            thresholds=_frozen_gate_thresholds(self.database, run_id),
        )
        gate = report["gate"]
        connection = self.database.connection
        with self.database._lock:  # noqa: SLF100
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO reports (
                      id, run_id, run_attempt_id, schema_version, input_hash,
                      report_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        report_input.run_id,
                        report_input.run_attempt_id,
                        report["schema_version"],
                        report_input.input_hash,
                        canonical_json(report),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO quality_gate_results (
                      id, report_id, run_id, run_attempt_id, verdict,
                      thresholds_json, observed_json, reasons_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        report_id,
                        report_input.run_id,
                        report_input.run_attempt_id,
                        gate["verdict"],
                        canonical_json(gate["thresholds"]),
                        canonical_json(gate["observed"]),
                        canonical_json(gate["reasons"]),
                        now,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                existing = self._find_existing(report_input)
                if existing is not None:
                    return existing
                raise
            except Exception:
                connection.rollback()
                raise
        return report

    def _find_existing(self, report_input: ReportInput) -> dict[str, Any] | None:
        row = self.database.connection.execute(
            """
            SELECT report_json
            FROM reports
            WHERE run_id = ?
              AND run_attempt_id = ?
              AND schema_version = 1
              AND input_hash = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (report_input.run_id, report_input.run_attempt_id, report_input.input_hash),
        ).fetchone()
        return json.loads(row["report_json"]) if row is not None else None


class DiagnosticRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_or_create(self, run_id: str) -> dict[str, Any]:
        ReportRepository(self.database).get_or_create(run_id)
        diagnostic_input = DiagnosticInputRepository(self.database).get_for_run(run_id)
        payload = build_diagnostics(diagnostic_input)
        now = _utc_timestamp()
        connection = self.database.connection
        with self.database._lock:  # noqa: SLF100
            connection.execute("BEGIN IMMEDIATE")
            try:
                for item in payload["items"]:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO diagnostics (
                          id, run_id, run_attempt_id, lane_attempt_id,
                          episode_attempt_id, comparison_id, comparison_pair_id,
                          gate_result_id, entity_type, code, category, phase,
                          severity, retryable, message, recommended_action,
                          raw_json, artifact_refs_json, input_hash, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["id"],
                            payload["run_id"],
                            payload["run_attempt_id"],
                            item.get("lane_attempt_id"),
                            item.get("episode_attempt_id"),
                            item.get("comparison_id"),
                            item.get("comparison_pair_id"),
                            item.get("gate_result_id"),
                            item["entity_type"],
                            item["code"],
                            item["category"],
                            item.get("phase"),
                            item["severity"],
                            1 if item["retryable"] else 0,
                            item["message"],
                            item.get("recommended_action"),
                            canonical_json(item.get("raw") or {}),
                            canonical_json(item.get("artifact_refs") or []),
                            payload["input_hash"],
                            now,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return payload


class ArtifactRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_for_run(self, run_id: str) -> dict[str, Any]:
        self._ensure_run_exists(run_id)
        self._index_run(run_id)
        rows = self.database.connection.execute(
            """
            SELECT id, run_id, run_attempt_id, lane_attempt_id,
                   episode_attempt_id, kind, relative_path, media_type,
                   size_bytes, sha256, created_at
            FROM artifacts
            WHERE run_id = ?
            ORDER BY relative_path, id
            """,
            (run_id,),
        ).fetchall()
        return {"items": [_map_artifact(row) for row in rows]}

    def content_path(self, run_id: str, artifact_id: str) -> tuple[dict[str, Any], Path]:
        self._ensure_run_exists(run_id)
        row = self.database.connection.execute(
            """
            SELECT id, run_id, run_attempt_id, lane_attempt_id,
                   episode_attempt_id, kind, relative_path, media_type,
                   size_bytes, sha256, created_at
            FROM artifacts
            WHERE run_id = ? AND id = ?
            """,
            (run_id, artifact_id),
        ).fetchone()
        if row is None:
            self._index_run(run_id)
            row = self.database.connection.execute(
                """
                SELECT id, run_id, run_attempt_id, lane_attempt_id,
                       episode_attempt_id, kind, relative_path, media_type,
                       size_bytes, sha256, created_at
                FROM artifacts
                WHERE run_id = ? AND id = ?
                """,
                (run_id, artifact_id),
            ).fetchone()
        if row is None:
            raise RunDomainError(
                "ARTIFACT_NOT_FOUND",
                "The requested artifact does not exist.",
                status_code=404,
                details=[{"run_id": run_id, "artifact_id": artifact_id}],
            )
        artifact = _map_artifact(row)
        path = resolve_artifact_path(
            self.database.settings.runs_dir / run_id,
            artifact["relative_path"],
        )
        return artifact, path

    def _ensure_run_exists(self, run_id: str) -> None:
        row = self.database.connection.execute(
            "SELECT id FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunNotFound(run_id)

    def _index_run(self, run_id: str) -> None:
        rows = self.database.connection.execute(
            """
            SELECT ea.id AS episode_attempt_id, ea.artifact_root,
                   la.id AS lane_attempt_id, la.run_attempt_id
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            WHERE e.run_id = ?
            ORDER BY ea.artifact_root, ea.id
            """,
            (run_id,),
        ).fetchall()
        now = _utc_timestamp()
        run_root = self.database.settings.runs_dir / run_id
        resolved_run_root = run_root.resolve()
        with self.database._lock:  # noqa: SLF100
            connection = self.database.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    try:
                        artifact_root = resolve_artifact_directory(
                            run_root,
                            str(row["artifact_root"]),
                        )
                    except ArtifactPathError:
                        continue
                    for file_path in sorted(artifact_root.rglob("*")):
                        if not file_path.is_file():
                            continue
                        try:
                            relative_path = file_path.relative_to(run_root).as_posix()
                        except ValueError:
                            try:
                                relative_path = file_path.relative_to(resolved_run_root).as_posix()
                            except ValueError:
                                continue
                        try:
                            resolved_file = resolve_artifact_path(
                                run_root,
                                relative_path,
                            )
                        except ArtifactPathError:
                            continue
                        payload = {
                            "run_id": run_id,
                            "relative_path": relative_path,
                        }
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO artifacts (
                              id, run_id, run_attempt_id, lane_attempt_id,
                              episode_attempt_id, kind, relative_path,
                              media_type, size_bytes, sha256, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "artifact_"
                                + canonical_sha256(payload).removeprefix("sha256:"),
                                run_id,
                                str(row["run_attempt_id"]),
                                str(row["lane_attempt_id"]),
                                str(row["episode_attempt_id"]),
                                _artifact_kind(relative_path),
                                relative_path,
                                _media_type(relative_path),
                                resolved_file.stat().st_size,
                                _file_sha256(resolved_file),
                                now,
                            ),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


class ReplayRepository:
    """Build the VS-15 ``EpisodeReplay`` DTO for one episode attempt.

    Loads ``trajectory.json`` from the attempt's ``artifact_root`` and maps each
    step's screenshot/prompt/response references to registered artifact ids.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_episode_replay(
        self,
        run_id: str,
        episode_key: str,
        *,
        lane_key: str | None = None,
        attempt_no: str = "latest",
    ) -> dict[str, Any]:
        self._ensure_run_exists(run_id)
        attempt = self._resolve_episode_attempt(
            run_id,
            episode_key,
            lane_key=lane_key,
            attempt_no=attempt_no,
        )
        artifact_root = str(attempt["artifact_root"])
        run_root = self.database.settings.runs_dir / run_id

        # The episode_attempt.artifact_root is the full trajectory directory
        # (e.g. ``lanes/candidate/trajectory/<name>`` for new runs or
        # ``trajectory/<name>`` for legacy imports). trajectory.json lives at
        # its root. resolve_artifact_directory enforces containment.
        try:
            episode_dir = resolve_artifact_directory(run_root, artifact_root)
        except ArtifactPathError as exc:
            raise RunDomainError(
                "REPLAY_ARTIFACT_MISSING",
                "The replay trajectory directory is not available.",
                status_code=404,
                details=[{"run_id": run_id, "artifact_root": artifact_root}],
            ) from exc

        trajectory_path = episode_dir / "trajectory.json"
        if not trajectory_path.is_file():
            raise RunDomainError(
                "REPLAY_ARTIFACT_MISSING",
                "The replay trajectory.json is not available for this episode attempt.",
                status_code=404,
                details=[
                    {
                        "run_id": run_id,
                        "episode_key": episode_key,
                        "artifact_root": artifact_root,
                    }
                ],
            )
        try:
            raw = trajectory_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise RunDomainError(
                "REPLAY_ARTIFACT_INVALID",
                "The replay trajectory.json could not be parsed.",
                status_code=422,
                details=[
                    {
                        "run_id": run_id,
                        "episode_key": episode_key,
                        "artifact_root": artifact_root,
                    }
                ],
            ) from exc
        if not isinstance(payload, list):
            raise RunDomainError(
                "REPLAY_ARTIFACT_INVALID",
                "The replay trajectory.json is not a list of steps.",
                status_code=422,
                details=[
                    {
                        "run_id": run_id,
                        "episode_key": episode_key,
                        "artifact_root": artifact_root,
                    }
                ],
            )

        # Index artifacts (idempotent) and build a relative-path → id map for
        # this run. Indexing is scoped to episode_attempts, so it covers the
        # files referenced by this trajectory.
        ArtifactRepository(self.database)._index_run(run_id)
        artifact_id_by_path = self._artifact_id_by_relative_path(run_id)

        steps = [
            self._map_step(step, artifact_root, artifact_id_by_path)
            for step in payload
            if isinstance(step, dict)
        ]

        return {
            "run_id": run_id,
            "episode_key": episode_key,
            "lane_key": str(attempt["lane_key"]),
            "attempt_no": int(attempt["attempt_no"]),
            "episode_attempt_id": str(attempt["id"]),
            "artifact_root": artifact_root,
            "outcome": (
                str(attempt["outcome"]) if attempt["outcome"] is not None else None
            ),
            "error_code": (
                str(attempt["error_code"])
                if attempt["error_code"] is not None
                else None
            ),
            "result": _json_or_none(attempt["result_json"]),
            "steps": steps,
        }

    def _ensure_run_exists(self, run_id: str) -> None:
        row = self.database.connection.execute(
            "SELECT id FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunNotFound(run_id)

    def _resolve_episode_attempt(
        self,
        run_id: str,
        episode_key: str,
        *,
        lane_key: str | None,
        attempt_no: str,
    ) -> dict[str, Any]:
        episode_row = self.database.connection.execute(
            "SELECT id FROM episodes WHERE run_id = ? AND episode_key = ?",
            (run_id, episode_key),
        ).fetchone()
        if episode_row is None:
            raise RunDomainError(
                "EPISODE_NOT_FOUND",
                "No episode with that key exists for this run.",
                status_code=404,
                details=[{"run_id": run_id, "episode_key": episode_key}],
            )
        episode_id = str(episode_row["id"])

        # ``attempt_no`` may be ``"latest"`` or a numeric string. We always
        # order by attempt_no desc so the latest terminal attempt wins when
        # ``"latest"`` is requested.
        #
        # P2 fix: when lane_key is omitted on a multi-lane (paired) run, default
        # to "candidate" rather than mixing baseline/candidate attempts. This
        # prevents the default replay from landing on an arbitrary lane or
        # jumping lanes after a retry on the other side.
        if lane_key is None:
            lane_count = self.database.connection.execute(
                "SELECT COUNT(*) AS n FROM lanes WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if int(lane_count["n"]) > 1:
                lane_key = "candidate"  # safe default for paired runs
        rows = self.database.connection.execute(
            """
            SELECT ea.id, ra.attempt_no, ea.attempt_no AS episode_attempt_no,
                   ea.state, ea.outcome, ea.error_code, ea.result_json,
                   ea.artifact_root, l.lane_key
            FROM episode_attempts AS ea
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            JOIN lanes AS l ON l.id = la.lane_id
            JOIN run_attempts AS ra ON ra.id = la.run_attempt_id
            WHERE ea.episode_id = ?
            ORDER BY ra.attempt_no DESC, ea.attempt_no DESC, ea.created_at DESC, ea.id DESC
            """,
            (episode_id,),
        ).fetchall()
        candidates: list[sqlite3.Row] = []
        for row in rows:
            if lane_key is not None and str(row["lane_key"]) != lane_key:
                continue
            candidates.append(row)
        if not candidates:
            raise RunDomainError(
                "EPISODE_ATTEMPT_NOT_FOUND",
                "No episode attempt matches the requested lane and episode.",
                status_code=404,
                details=[
                    {
                        "run_id": run_id,
                        "episode_key": episode_key,
                        "lane_key": lane_key,
                    }
                ],
            )

        if attempt_no == "latest":
            chosen = candidates[0]
        else:
            try:
                requested_no = int(attempt_no)
            except ValueError as exc:
                raise RunDomainError(
                    "EPISODE_ATTEMPT_NOT_FOUND",
                    "attempt_no must be 'latest' or an integer.",
                    status_code=404,
                    details=[
                        {
                            "run_id": run_id,
                            "episode_key": episode_key,
                            "attempt_no": attempt_no,
                        }
                    ],
                ) from exc
            chosen = next(
                (row for row in candidates if int(row["attempt_no"]) == requested_no),
                None,
            )
            if chosen is None:
                raise RunDomainError(
                    "EPISODE_ATTEMPT_NOT_FOUND",
                    "No episode attempt matches the requested attempt number.",
                    status_code=404,
                    details=[
                        {
                            "run_id": run_id,
                            "episode_key": episode_key,
                            "attempt_no": attempt_no,
                        }
                    ],
                )
        return dict(chosen)

    def _artifact_id_by_relative_path(self, run_id: str) -> dict[str, str]:
        rows = self.database.connection.execute(
            "SELECT id, relative_path FROM artifacts WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {str(row["relative_path"]): str(row["id"]) for row in rows}

    def _map_step(
        self,
        step: dict[str, Any],
        artifact_root: str,
        artifact_id_by_path: dict[str, str],
    ) -> dict[str, Any]:
        def _artifact_id(field: str) -> str | None:
            relative = step.get(field)
            if not isinstance(relative, str) or not relative:
                return None
            full_path = f"{artifact_root}/{relative}"
            return artifact_id_by_path.get(full_path)

        return {
            "step": step.get("step"),
            "route": step.get("route") or {},
            "action_type": step.get("action_type") or "",
            "action_data": step.get("action_data") or {},
            "thought": step.get("thought") or "",
            "explain": step.get("explain") or "",
            "summary": step.get("summary") or "",
            "screenshot_artifact_id": _artifact_id("screenshot"),
            "screenshot_annotated_artifact_id": _artifact_id("screenshot_annotated"),
            "model_response_artifact_id": _artifact_id("model_response_path"),
            "model_prompt_artifact_id": _artifact_id("model_prompt_path"),
        }


class BaselineRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def promote(self, run_id: str, *, lane_key: str | None = None) -> dict[str, Any]:
        report = ReportRepository(self.database).get_or_create(run_id)
        run_row = self.database.connection.execute(
            "SELECT state FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        attempt_row = self.database.connection.execute(
            "SELECT state FROM run_attempts WHERE id = ?",
            (report["run_attempt_id"],),
        ).fetchone()
        if (
            run_row is None
            or attempt_row is None
            or run_row["state"] != "completed"
            or attempt_row["state"] != "completed"
        ):
            raise RunDomainError(
                "BASELINE_PROMOTION_INVALID_RUN_STATE",
                "Only completed runs can be promoted as baselines.",
                status_code=409,
                details=[
                    {
                        "run_id": run_id,
                        "run_state": run_row["state"] if run_row is not None else None,
                        "run_attempt_state": (
                            attempt_row["state"] if attempt_row is not None else None
                        ),
                    }
                ],
            )

        provenance = report["provenance"]
        imported = provenance.get("imported")
        if isinstance(imported, dict) and imported.get("provenance_missing"):
            raise RunDomainError(
                "BASELINE_PROMOTION_IMPORTED_PROVENANCE_MISSING",
                "Imported runs with missing provenance cannot be promoted as strict baselines.",
                status_code=409,
                details=[
                    {
                        "run_id": run_id,
                        "provenance_missing": imported.get("provenance_missing"),
                        "source_path": imported.get("source_path"),
                    }
                ],
            )
        target_revision_ids = provenance.get("target_revision_ids") or {}
        selected_lane_key = lane_key or _default_baseline_lane_key(target_revision_ids)
        target_revision_id = target_revision_ids.get(selected_lane_key)
        if target_revision_id is None:
            raise RunDomainError(
                "BASELINE_LANE_NOT_FOUND",
                "The requested lane is not present in the report provenance.",
                status_code=400,
                details=[{"run_id": run_id, "lane_key": selected_lane_key}],
            )

        baseline_id = new_id()
        now = _utc_timestamp()
        row = {
            "id": baseline_id,
            "report_id": report["id"],
            "run_id": report["run_id"],
            "project_id": provenance["project_id"],
            "workflow_version_id": provenance["workflow_version_id"],
            "run_plan_hash": provenance["run_plan_hash"],
            "task_source_digest": provenance["task_source_digest"],
            "target_revision_ids": target_revision_ids,
            "lane_key": selected_lane_key,
            "target_revision_id": target_revision_id,
            "created_at": now,
        }
        self.database.connection.execute(
            """
            INSERT INTO baselines (
              id, report_id, run_id, project_id, workflow_version_id,
              run_plan_hash, task_source_digest, target_revision_ids_json,
              lane_key, target_revision_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                baseline_id,
                row["report_id"],
                row["run_id"],
                row["project_id"],
                row["workflow_version_id"],
                row["run_plan_hash"],
                row["task_source_digest"],
                canonical_json(target_revision_ids),
                selected_lane_key,
                target_revision_id,
                now,
            ),
        )
        self.database.connection.commit()
        return row


class ComparisonRepository:
    """CRUD for VS-09 comparisons + comparison_pairs.

    A comparison belongs to one run_attempt and joins baseline/candidate
    episode_attempts by pair_key. ``record_comparison`` + ``record_pair`` build
    the comparison graph; ``get_comparison`` / ``list_pairs`` read it back for
    the API and frontend.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def record_comparison(
        self,
        *,
        run_id: str,
        run_attempt_id: str,
        baseline_lane_id: str,
        candidate_lane_id: str,
        policy: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> str:
        comparison_id = new_id()
        now = _utc_timestamp()
        connection = self.database.connection
        with self.database._lock:  # noqa: SLF100
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO comparisons (
                      id, run_id, run_attempt_id, baseline_lane_id,
                      candidate_lane_id, policy_json, summary_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comparison_id,
                        run_id,
                        run_attempt_id,
                        baseline_lane_id,
                        candidate_lane_id,
                        json.dumps(policy or {}, sort_keys=True, ensure_ascii=False),
                        json.dumps(summary, sort_keys=True, ensure_ascii=False)
                        if summary is not None
                        else None,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return comparison_id

    def record_pair(
        self,
        *,
        comparison_id: str,
        pair_key: str,
        classification: str,
        baseline_episode_attempt_id: str | None = None,
        candidate_episode_attempt_id: str | None = None,
        integrity: dict[str, Any] | None = None,
        delta: dict[str, Any] | None = None,
    ) -> str:
        pair_id = new_id()
        connection = self.database.connection
        with self.database._lock:  # noqa: SLF100
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO comparison_pairs (
                      id, comparison_id, pair_key, baseline_episode_attempt_id,
                      candidate_episode_attempt_id, classification,
                      integrity_json, delta_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pair_id,
                        comparison_id,
                        pair_key,
                        baseline_episode_attempt_id,
                        candidate_episode_attempt_id,
                        classification,
                        json.dumps(integrity or {}, sort_keys=True, ensure_ascii=False),
                        json.dumps(delta or {}, sort_keys=True, ensure_ascii=False),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return pair_id

    def get_comparison(self, run_id: str) -> dict[str, Any] | None:
        """Return the latest comparison for a run, or None if none exists."""
        row = self.database.connection.execute(
            """
            SELECT id, run_id, run_attempt_id, baseline_lane_id, candidate_lane_id,
                   policy_json, summary_json, created_at
            FROM comparisons
            WHERE run_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        pairs = self.list_pairs(str(row["id"]))
        return {
            "id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "run_attempt_id": str(row["run_attempt_id"]),
            "baseline_lane_id": str(row["baseline_lane_id"]),
            "candidate_lane_id": str(row["candidate_lane_id"]),
            "policy": json.loads(row["policy_json"]) if row["policy_json"] else {},
            "summary": json.loads(row["summary_json"]) if row["summary_json"] else None,
            "created_at": str(row["created_at"]),
            "pairs": pairs,
        }

    def list_pairs(self, comparison_id: str) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            """
            SELECT id, comparison_id, pair_key, baseline_episode_attempt_id,
                   candidate_episode_attempt_id, classification, integrity_json,
                   delta_json
            FROM comparison_pairs
            WHERE comparison_id = ?
            ORDER BY pair_key
            """,
            (comparison_id,),
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "comparison_id": str(row["comparison_id"]),
                "pair_key": str(row["pair_key"]),
                "baseline_episode_attempt_id": (
                    str(row["baseline_episode_attempt_id"])
                    if row["baseline_episode_attempt_id"] is not None
                    else None
                ),
                "candidate_episode_attempt_id": (
                    str(row["candidate_episode_attempt_id"])
                    if row["candidate_episode_attempt_id"] is not None
                    else None
                ),
                "classification": str(row["classification"]),
                "integrity": json.loads(row["integrity_json"])
                if row["integrity_json"]
                else {},
                "delta": json.loads(row["delta_json"]) if row["delta_json"] else {},
            }
            for row in rows
        ]


def _json_or_empty_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _json_or_empty_array(value: Any) -> list[Any]:
    if value is None:
        return []
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, list) else []


def _imported_metadata(run_plan: dict[str, Any]) -> dict[str, Any] | None:
    imported = run_plan.get("imported")
    return imported if isinstance(imported, dict) else None


def _build_report_payload(
    *,
    report_id: str,
    created_at: str,
    report_input: ReportInput,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    functional = build_functional_report(report_input)
    performance = build_performance_report(report_input)
    comparison = build_comparison_report(report_input)
    sequence = build_sequence_report(report_input)
    report = {
        "id": report_id,
        "schema_version": 1,
        "run_id": report_input.run_id,
        "run_attempt_id": report_input.run_attempt_id,
        "input_hash": report_input.input_hash,
        "provenance": report_input.provenance,
        "functional": functional,
        "performance": performance,
        "comparison": comparison,
        "sequence": sequence,
        "created_at": created_at,
    }
    report["gate"] = evaluate_gates(report, thresholds)
    return report


def _frozen_gate_thresholds(database: Database, run_id: str) -> dict[str, Any]:
    row = database.connection.execute(
        "SELECT run_plan_json FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    run_plan = _json_or_empty_object(row["run_plan_json"])
    gates = run_plan.get("gates")
    return gates if isinstance(gates, dict) else {}


def _default_baseline_lane_key(target_revision_ids: dict[str, Any]) -> str:
    if "candidate" in target_revision_ids:
        return "candidate"
    if len(target_revision_ids) == 1:
        return next(iter(target_revision_ids))
    return sorted(target_revision_ids)[0]


def _json_or_none(value: Any) -> Any:
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


def _task_source_digest(run_plan: dict[str, Any]) -> str | None:
    task_source = run_plan.get("task_source")
    if not isinstance(task_source, dict):
        return None
    digest = task_source.get("registry_digest")
    return str(digest) if digest is not None else None


def _map_report_episode_attempt(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "episode_id": str(row["episode_id"]),
        "episode_key": str(row["episode_key"]),
        "pair_key": str(row["pair_key"]),
        "lane_attempt_id": str(row["lane_attempt_id"]),
        "lane_id": str(row["lane_id"]),
        "lane_key": str(row["lane_key"]),
        "attempt_no": int(row["attempt_no"]),
        "state": str(row["state"]),
        "outcome": str(row["outcome"]) if row["outcome"] is not None else None,
        "error_code": str(row["error_code"]) if row["error_code"] is not None else None,
        "result_json": _json_or_none(row["result_json"]),
        "artifact_root": str(row["artifact_root"]),
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "created_at": str(row["created_at"]),
    }


def _map_artifact(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "run_attempt_id": (
            str(row["run_attempt_id"]) if row["run_attempt_id"] is not None else None
        ),
        "lane_attempt_id": (
            str(row["lane_attempt_id"]) if row["lane_attempt_id"] is not None else None
        ),
        "episode_attempt_id": (
            str(row["episode_attempt_id"])
            if row["episode_attempt_id"] is not None
            else None
        ),
        "kind": str(row["kind"]),
        "relative_path": str(row["relative_path"]),
        "media_type": (
            str(row["media_type"]) if row["media_type"] is not None else None
        ),
        "size_bytes": int(row["size_bytes"]) if row["size_bytes"] is not None else None,
        "sha256": str(row["sha256"]) if row["sha256"] is not None else None,
        "created_at": str(row["created_at"]),
    }


def _artifact_kind(relative_path: str) -> str:
    suffix = relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
    if suffix in {"png", "jpg", "jpeg", "webp"}:
        return "screenshot"
    if suffix in {"json", "jsonl"}:
        return "json"
    if suffix in {"log", "txt"}:
        return "log"
    return "file"


def _media_type(relative_path: str) -> str:
    guessed, _encoding = mimetypes.guess_type(relative_path)
    if guessed is not None:
        return guessed
    if relative_path.endswith(".jsonl"):
        return "application/x-ndjson"
    return "application/octet-stream"


def _file_sha256(path: Any) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _latest_attempts_by_lane_episode(
    attempts: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for attempt in attempts:
        latest[(attempt["episode_id"], attempt["lane_attempt_id"])] = attempt
    return latest


def _planned_lane_episodes(
    *,
    episodes: list[dict[str, Any]],
    lanes: list[dict[str, Any]],
    latest_attempts: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for episode in episodes:
        for lane in lanes:
            lane_attempt_id = lane["lane_attempt_id"]
            latest = (
                latest_attempts.get((episode["episode_id"], lane_attempt_id))
                if lane_attempt_id is not None
                else None
            )
            item = {
                "episode_id": episode["episode_id"],
                "episode_key": episode["episode_key"],
                "pair_key": episode["pair_key"],
                "task_base_id": episode["task_base_id"],
                "task_id": episode["task_id"],
                "lane_id": lane["lane_id"],
                "lane_key": lane["lane_key"],
                "role": lane["role"],
                "lane_attempt_id": lane_attempt_id,
                "episode_attempt_id": latest["id"] if latest is not None else None,
                "status": latest["state"] if latest is not None else "incomplete",
                "outcome": latest["outcome"] if latest is not None else None,
                "error_code": latest["error_code"] if latest is not None else None,
            }
            if (
                episode.get("sequence_index") is not None
                or episode.get("sequence_group_id") is not None
            ):
                item["sequence_index"] = episode.get("sequence_index")
                item["sequence_group_id"] = episode.get("sequence_group_id")
            planned.append(item)
    return planned


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
