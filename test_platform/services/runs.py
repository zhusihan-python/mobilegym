from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from test_platform.config import PlatformSettings
from test_platform.domain.canonical_json import canonical_json, canonical_sha256
from test_platform.domain.ids import new_id
from test_platform.domain.run_plans import RunPlan, RunPlanCompiler
from test_platform.domain.runs import RunDomainError, RunIdempotencyConflict, RunDetail
from test_platform.domain.task_catalog import TaskCatalogSnapshot, build_task_catalog_snapshot
from test_platform.domain.workflows import WorkflowDefinition, WorkflowDomainError
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    RunRepository,
    TargetRepository,
    WorkflowRepository,
)


class FakeRunSupervisor:
    def __init__(self) -> None:
        self._queued_run_ids: list[str] = []

    def submit(self, run_id: str) -> None:
        if run_id not in self._queued_run_ids:
            self._queued_run_ids.append(run_id)

    def snapshot(self) -> dict[str, list[str]]:
        return {"queued_run_ids": list(self._queued_run_ids)}


class RunService:
    def __init__(
        self,
        database: Database,
        settings: PlatformSettings,
        *,
        supervisor: Any,
        catalog_builder: Callable[[], TaskCatalogSnapshot] = build_task_catalog_snapshot,
    ) -> None:
        self.database = database
        self.settings = settings
        self.supervisor = supervisor
        self.catalog_builder = catalog_builder

    def create_run(
        self,
        *,
        workflow_version_id: str,
        name: str | None,
        seed: int,
        idempotency_key: str,
    ) -> RunDetail:
        request_hash = canonical_sha256(
            {
                "workflow_version_id": workflow_version_id,
                "name": name,
                "seed": seed,
            }
        )
        existing = self._find_idempotent_run(idempotency_key, request_hash)
        if existing is not None:
            return existing

        workflow_repository = WorkflowRepository(self.database)
        try:
            version = workflow_repository.get_version(workflow_version_id)
            workflow = workflow_repository.get(version.workflow_id)
        except WorkflowDomainError as exc:
            raise RunDomainError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
                details=exc.details,
            ) from exc

        definition = WorkflowDefinition.model_validate(version.definition)
        targets = self._resolve_targets(definition)
        run_id = new_id()
        created_at = _utc_timestamp()
        try:
            plan = RunPlanCompiler().compile(
                run_id=run_id,
                workflow_version_id=workflow_version_id,
                definition=definition,
                catalog=self.catalog_builder(),
                targets=targets,
                seed=seed,
                created_at=created_at,
            )
        except ValueError as exc:
            raise RunDomainError(
                "RUN_PLAN_INVALID",
                str(exc),
                status_code=409,
            ) from exc
        temporary_root = self.settings.runs_dir / f".{run_id}.tmp"
        final_root = self.settings.runs_dir / run_id
        self._write_plan_artifact(temporary_root, plan)

        connection = self.database.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_graph(
                connection,
                project_id=workflow.project_id,
                name=name,
                definition=definition,
                plan=plan,
                artifact_root=final_root,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

        try:
            temporary_root.replace(final_root)
        except OSError as exc:
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ?, ended_at = ? WHERE id = ?",
                ("failed", _utc_timestamp(), _utc_timestamp(), run_id),
            )
            connection.commit()
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise RunDomainError(
                "ARTIFACT_IO_ERROR",
                "The run plan artifact could not be finalized.",
                status_code=500,
                details=[{"run_id": run_id}],
            ) from exc

        self.supervisor.submit(run_id)
        return RunRepository(self.database).get(run_id)

    def _find_idempotent_run(
        self,
        idempotency_key: str,
        request_hash: str,
    ) -> RunDetail | None:
        row = self.database.connection.execute(
            """
            SELECT request_hash, run_id
            FROM idempotency_keys
            WHERE key = ? AND route = ?
            """,
            (idempotency_key, "POST /api/platform/v1/runs"),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise RunIdempotencyConflict(idempotency_key)
        return RunRepository(self.database).get(row["run_id"])

    def _resolve_targets(self, definition: WorkflowDefinition) -> dict[str, Any]:
        matrix = next((node for node in definition.nodes if node.type == "matrix"), None)
        lanes = matrix.config.get("lanes", {}) if matrix else {}
        if not isinstance(lanes, dict):
            raise RunDomainError("RUN_PLAN_INVALID", "Workflow matrix lanes must be an object.")

        repository = TargetRepository(self.database)
        targets: dict[str, Any] = {}
        for lane in lanes.values():
            target_id = lane.get("target_id") if isinstance(lane, dict) else None
            if not isinstance(target_id, str):
                raise RunDomainError("RUN_PLAN_INVALID", "Every lane requires a target.")
            target = repository.get(target_id)
            revision = repository.latest_revision(target_id)
            if revision is None or revision.health_status != "healthy":
                raise RunDomainError(
                    "TARGET_REVISION_MISSING",
                    "Every run lane requires a healthy target revision.",
                    details=[{"target_id": target_id}],
                )
            targets[target_id] = {"target": target, "revision": revision}
        return targets

    def _write_plan_artifact(self, temporary_root: Path, plan: RunPlan) -> None:
        platform_dir = temporary_root / "platform"
        platform_dir.mkdir(parents=True, exist_ok=False)
        (platform_dir / "run-plan.json").write_text(
            f"{canonical_json(plan.model_dump(mode='json'))}\n",
            encoding="utf-8",
        )

    def _insert_graph(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        name: str | None,
        definition: WorkflowDefinition,
        plan: RunPlan,
        artifact_root: Path,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        plan_json = canonical_json(plan.model_dump(mode="json"))
        connection.execute(
            """
            INSERT INTO runs (
              id, project_id, workflow_version_id, name, state,
              run_plan_json, run_plan_hash, artifact_root,
              next_event_sequence, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 2, ?, ?)
            """,
            (
                plan.run_id,
                project_id,
                plan.workflow_version_id,
                name,
                plan_json,
                plan.fingerprint,
                str(artifact_root),
                plan.created_at,
                plan.created_at,
            ),
        )
        run_attempt_id = new_id()
        connection.execute(
            """
            INSERT INTO run_attempts (
              id, run_id, attempt_no, reason, state, created_at
            )
            VALUES (?, ?, 1, 'initial', 'queued', ?)
            """,
            (run_attempt_id, plan.run_id, plan.created_at),
        )
        for node in definition.nodes:
            connection.execute(
                """
                INSERT INTO workflow_node_runs (
                  id, run_attempt_id, node_id, node_type, state, created_at
                )
                VALUES (?, ?, ?, ?, 'queued', ?)
                """,
                (new_id(), run_attempt_id, node.id, node.type, plan.created_at),
            )
        for lane in plan.lanes:
            connection.execute(
                """
                INSERT INTO lanes (
                  id, run_id, lane_key, role, target_id, target_revision_id,
                  runner_config_json, reproducibility_fingerprint, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lane.lane_id,
                    plan.run_id,
                    lane.lane_key,
                    lane.role,
                    lane.target_id,
                    lane.target_revision_id,
                    canonical_json(lane.runner_config),
                    canonical_sha256(
                        lane.model_dump(mode="json", exclude={"lane_id"})
                    ),
                    plan.created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO lane_attempts (
                  id, lane_id, run_attempt_id, state, artifact_root, created_at
                )
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (
                    new_id(),
                    lane.lane_id,
                    run_attempt_id,
                    f"lanes/{lane.lane_key}",
                    plan.created_at,
                ),
            )
        for episode in plan.episodes:
            connection.execute(
                """
                INSERT INTO episodes (
                  id, run_id, episode_key, materialization_key, pair_key,
                  task_base_id, task_id, instance_id, instance_seed,
                  template_index, trial_id, max_steps, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    plan.run_id,
                    episode.episode_key,
                    episode.materialization_key,
                    episode.pair_key,
                    episode.task_base_id,
                    episode.task_id,
                    episode.instance_id,
                    episode.instance_seed,
                    episode.template_index,
                    episode.trial_id,
                    episode.max_steps,
                    plan.created_at,
                ),
            )
        connection.execute(
            """
            INSERT INTO events (
              id, run_id, sequence, type, entity_type, entity_id,
              occurred_at, payload_json
            )
            VALUES (?, ?, 1, 'run.created', 'run', ?, ?, ?)
            """,
            (
                new_id(),
                plan.run_id,
                plan.run_id,
                plan.created_at,
                canonical_json(
                    {
                        "state": "queued",
                        "fingerprint": plan.fingerprint,
                    }
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO idempotency_keys (
              key, route, request_hash, response_status, run_id, created_at
            )
            VALUES (?, ?, ?, 201, ?, ?)
            """,
            (
                idempotency_key,
                "POST /api/platform/v1/runs",
                request_hash,
                plan.run_id,
                plan.created_at,
            ),
        )


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
