from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from test_platform.config import PlatformSettings
from test_platform.domain.canonical_json import canonical_json, canonical_sha256
from test_platform.domain.execution_profiles import ExecutionProfileDomainError
from test_platform.domain.run_launch import (
    CreateRunLaunch,
    PreviewRunLaunch,
    ResolvedLaneBinding,
    RunLaunchError,
    RunLaunchPreview,
)
from test_platform.domain.ids import new_id
from test_platform.domain.run_plans import (
    ResolvedLanePlanBinding,
    RunPlanV2,
    RunPlanV2Compiler,
)
from test_platform.domain.task_catalog import (
    TaskCatalogSnapshot,
    build_task_catalog_snapshot,
)
from test_platform.domain.targets import TargetDomainError
from test_platform.domain.versioned_documents import read_workflow_definition
from test_platform.domain.workflows import WorkflowDefinitionV2, WorkflowDomainError
from test_platform.domain.runs import RunDetail, RunIdempotencyConflict
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    ExecutionProfileRepository,
    RunRepository,
    TargetRepository,
    WorkflowRepository,
)


_NON_EXACT_REVISION_SELECTORS = frozenset({"current", "draft", "head", "latest"})


class RunLaunch:
    def __init__(
        self,
        database: Database,
        *,
        settings: PlatformSettings | None = None,
        supervisor: Any = None,
        catalog_builder: Callable[[], TaskCatalogSnapshot] = build_task_catalog_snapshot,
    ) -> None:
        self._database = database
        self._settings = settings
        self._supervisor = supervisor
        self._catalog_builder = catalog_builder

    def preview(self, command: PreviewRunLaunch) -> RunLaunchPreview:
        plan, workflow_version_hash = self._compile_plan(
            command,
            run_id="preview",
            created_at="1970-01-01T00:00:00.000Z",
        )
        return self._preview_from_plan(command, plan, workflow_version_hash)

    def create(
        self,
        command: CreateRunLaunch,
        *,
        expected_preview_token: str,
        secret_bindings: dict[str, str],
        idempotency_key: str,
    ) -> RunDetail:
        if self._settings is None or self._supervisor is None:
            raise RuntimeError("RunLaunch create requires settings and a supervisor.")
        if secret_bindings:
            raise RunLaunchError(
                "RUN_EXECUTION_SECRET_SLOT_INVALID",
                "TP-EP02 does not accept secret bindings.",
            )
        request_hash = canonical_sha256(
            {
                "command": asdict(command),
                "expected_preview_token": expected_preview_token,
            }
        )
        existing = self._find_idempotent_run(idempotency_key, request_hash)
        if existing is not None:
            return existing

        preview_command = PreviewRunLaunch(
            project_id=command.project_id,
            workflow_version_id=command.workflow_version_id,
            name=command.name,
            seed=command.seed,
            comparison_intent=command.comparison_intent,
            lane_bindings=command.lane_bindings,
        )
        preview = self.preview(preview_command)
        if preview.preview_token != expected_preview_token:
            raise RunLaunchError(
                "RUN_LAUNCH_PREVIEW_STALE",
                "Run Launch preview is stale.",
                status_code=409,
                details=[
                    {
                        "expected_preview_token": expected_preview_token,
                        "current_preview_token": preview.preview_token,
                    }
                ],
            )

        run_id = new_id()
        created_at = _utc_timestamp()
        plan, _workflow_version_hash = self._compile_plan(
            preview_command,
            run_id=run_id,
            created_at=created_at,
        )
        temporary_root = self._settings.runs_dir / f".{run_id}.tmp"
        final_root = self._settings.runs_dir / run_id
        try:
            self._write_plan_artifact(temporary_root, plan)
        except OSError:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

        connection = self._database.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            transaction_plan, transaction_workflow_hash = self._compile_plan(
                preview_command,
                run_id=run_id,
                created_at=created_at,
            )
            transaction_preview = self._preview_from_plan(
                preview_command,
                transaction_plan,
                transaction_workflow_hash,
            )
            if transaction_preview.preview_token != expected_preview_token:
                raise RunLaunchError(
                    "RUN_LAUNCH_PREVIEW_STALE",
                    "Run Launch preview changed before commit.",
                    status_code=409,
                )
            replay = self._find_idempotent_run(idempotency_key, request_hash)
            if replay is not None:
                connection.rollback()
                shutil.rmtree(temporary_root, ignore_errors=True)
                return replay
            self._insert_graph(
                connection,
                project_id=command.project_id,
                name=command.name,
                definition=_read_workflow_definition_for_plan(
                    self._database,
                    command.workflow_version_id,
                ),
                plan=transaction_plan,
                artifact_root=final_root,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise

        try:
            temporary_root.replace(final_root)
        except OSError as exc:
            now = _utc_timestamp()
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ?, ended_at = ? WHERE id = ?",
                ("failed", now, now, run_id),
            )
            connection.commit()
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise RunLaunchError(
                "ARTIFACT_IO_ERROR",
                "The Run Plan artifact could not be finalized.",
                status_code=500,
                details=[{"run_id": run_id}],
            ) from exc

        self._supervisor.submit(run_id)
        return RunRepository(self._database).get(run_id)

    def _preview_from_plan(
        self,
        command: PreviewRunLaunch,
        plan: RunPlanV2,
        workflow_version_hash: str,
    ) -> RunLaunchPreview:
        fingerprint_inputs = {
            "workflow_version_id": plan.workflow_version_id,
            "workflow_version_hash": workflow_version_hash,
            "task_source_digest": plan.task_source.registry_digest,
            "seed": command.seed,
            "comparison_intent": command.comparison_intent,
            "lane_fingerprints": {
                lane.lane_key: lane.fingerprint for lane in plan.lanes
            },
        }
        preview_token = canonical_sha256(
            {
                "schema_version": 1,
                "project_id": command.project_id,
                "run_plan_fingerprint": plan.fingerprint,
                "fingerprint_inputs": fingerprint_inputs,
            }
        )
        return RunLaunchPreview(
            workflow_version_id=plan.workflow_version_id,
            workflow_version_hash=workflow_version_hash,
            comparison_intent=command.comparison_intent,
            lane_bindings=[
                self._resolved_lane_view(plan, lane.lane_key) for lane in plan.lanes
            ],
            episode_count=len(plan.episodes),
            fingerprint_inputs=fingerprint_inputs,
            run_plan_fingerprint=plan.fingerprint,
            preview_token=preview_token,
            credential_requirements=[],
        )

    def _compile_plan(
        self,
        command: PreviewRunLaunch,
        *,
        run_id: str,
        created_at: str,
    ) -> tuple[RunPlanV2, str]:
        workflow_repository = WorkflowRepository(self._database)
        try:
            version = workflow_repository.get_version(command.workflow_version_id)
            workflow = workflow_repository.get(version.workflow_id)
        except WorkflowDomainError as exc:
            raise RunLaunchError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
                details=exc.details,
            ) from exc
        if workflow.project_id != command.project_id:
            raise _cross_project_error(
                "workflow_version",
                command.workflow_version_id,
            )
        definition = read_workflow_definition(version.definition)
        if not isinstance(definition, WorkflowDefinitionV2):
            raise RunLaunchError(
                "RUN_WORKFLOW_SCHEMA_REQUIRED",
                "Profile-aware launch requires a Workflow Version v2.",
                status_code=409,
                details=[
                    {
                        "workflow_version_id": version.id,
                        "schema_version": definition.schema_version,
                    }
                ],
            )
        if command.comparison_intent != "single":
            raise RunLaunchError(
                "RUN_COMPARISON_UNSUPPORTED",
                "TP-EP02 supports only Single Run launch.",
                status_code=409,
            )

        lane_slots = _lane_slots(definition)
        binding_by_slot = _binding_map(command.lane_bindings)
        if set(binding_by_slot) != set(lane_slots):
            raise RunLaunchError(
                "RUN_LANE_BINDING_INCOMPLETE",
                "Every Workflow Lane Slot requires one exact Lane Binding.",
                details=[
                    {
                        "expected_lane_slots": sorted(lane_slots),
                        "provided_lane_slots": sorted(binding_by_slot),
                    }
                ],
            )
        if len(lane_slots) != 1 or "candidate" not in lane_slots:
            raise RunLaunchError(
                "RUN_LANE_BINDING_INCOMPLETE",
                "TP-EP02 requires exactly one candidate Lane Slot.",
                details=[{"lane_slots": sorted(lane_slots)}],
            )

        target_repository = TargetRepository(self._database)
        profile_repository = ExecutionProfileRepository(self._database)
        resolved_bindings: dict[str, ResolvedLanePlanBinding] = {}
        for lane_slot, binding in binding_by_slot.items():
            _require_exact_revision_id(
                binding.target_revision_id,
                field="target_revision_id",
                lane_slot=lane_slot,
            )
            _require_exact_revision_id(
                binding.execution_profile_revision_id,
                field="execution_profile_revision_id",
                lane_slot=lane_slot,
            )
            try:
                target_revision, target = target_repository.get_revision(
                    binding.target_revision_id
                )
            except TargetDomainError as exc:
                raise RunLaunchError(
                    "RUN_REVISION_STALE",
                    "The selected Target Revision is unavailable.",
                    status_code=409,
                    details=[
                        {
                            "lane_slot": lane_slot,
                            "target_revision_id": binding.target_revision_id,
                        }
                    ],
                ) from exc
            try:
                profile_revision, profile = profile_repository.get_revision(
                    binding.execution_profile_revision_id
                )
            except ExecutionProfileDomainError as exc:
                raise RunLaunchError(
                    exc.code,
                    exc.message,
                    status_code=exc.status_code,
                    details=exc.details,
                ) from exc

            if target.project_id != command.project_id:
                raise _cross_project_error("target_revision", target_revision.id)
            if profile.project_id != command.project_id:
                raise _cross_project_error(
                    "execution_profile_revision",
                    profile_revision.id,
                )
            latest_target_revision = target_repository.latest_revision(target.id)
            if (
                latest_target_revision is None
                or latest_target_revision.id != target_revision.id
                or target_revision.health_status != "healthy"
                or not target.enabled
                or target.kind != "simulator"
            ):
                raise RunLaunchError(
                    "RUN_REVISION_STALE",
                    "The selected Target Revision is not the current executable revision.",
                    status_code=409,
                    details=[
                        {
                            "lane_slot": lane_slot,
                            "target_revision_id": target_revision.id,
                        }
                    ],
                )
            if profile.archived_at is not None:
                raise RunLaunchError(
                    "EXECUTION_PROFILE_ARCHIVED",
                    "The selected Execution Profile is archived.",
                    status_code=409,
                    details=[{"execution_profile_id": profile.id}],
                )
            required_slots = profile_revision.public_spec.get("credentials", {}).get(
                "required_slots", []
            )
            if required_slots:
                raise RunLaunchError(
                    "RUN_EXECUTION_SECRET_MISSING",
                    "TP-EP02 supports only no-secret Execution Profile Revisions.",
                    details=[{"lane_slot": lane_slot, "required_slots": required_slots}],
                )
            resolved_bindings[lane_slot] = ResolvedLanePlanBinding(
                target=target,
                target_revision=target_revision,
                execution_profile=profile,
                execution_profile_revision=profile_revision,
            )

        try:
            plan = RunPlanV2Compiler().compile(
                run_id=run_id,
                workflow_version_id=version.id,
                workflow_version_hash=version.definition_hash,
                definition=definition,
                catalog=self._catalog_builder(),
                bindings=resolved_bindings,
                seed=command.seed,
                created_at=created_at,
                comparison_intent=command.comparison_intent,
            )
        except ValueError as exc:
            raise RunLaunchError(
                "RUN_PLAN_INVALID",
                str(exc),
                status_code=409,
            ) from exc
        return plan, version.definition_hash

    @staticmethod
    def _resolved_lane_view(
        plan: RunPlanV2,
        lane_slot: str,
    ) -> ResolvedLaneBinding:
        lane = next(item for item in plan.lanes if item.lane_key == lane_slot)
        snapshot = plan.execution_snapshots[lane.execution_snapshot_key]
        return ResolvedLaneBinding(
            lane_slot=lane.lane_key,
            role=lane.role,
            target_id=lane.target_id,
            target_revision_id=lane.target_revision_id,
            target_revision_hash=lane.target_revision_hash,
            execution_profile_id=snapshot.execution_profile_id,
            execution_profile_name=snapshot.execution_profile_name,
            execution_profile_revision_id=lane.execution_profile_revision_id,
            execution_profile_revision_no=snapshot.execution_profile_revision_no,
            execution_profile_public_hash=snapshot.public_spec_hash,
            execution_profile_revision_hash=lane.execution_profile_revision_hash,
            lane_fingerprint=lane.fingerprint,
        )

    def _find_idempotent_run(
        self,
        idempotency_key: str,
        request_hash: str,
    ) -> RunDetail | None:
        row = self._database.connection.execute(
            """
            SELECT request_hash, run_id
            FROM idempotency_keys
            WHERE key = ? AND route = ?
            """,
            (idempotency_key, "POST /api/platform/v1/run-launch"),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise RunIdempotencyConflict(idempotency_key)
        return RunRepository(self._database).get(str(row["run_id"]))

    @staticmethod
    def _write_plan_artifact(temporary_root: Path, plan: RunPlanV2) -> None:
        platform_dir = temporary_root / "platform"
        platform_dir.mkdir(parents=True, exist_ok=False)
        (platform_dir / "run-plan.json").write_text(
            f"{canonical_json(plan.model_dump(mode='json'))}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _insert_graph(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        name: str | None,
        definition: WorkflowDefinitionV2,
        plan: RunPlanV2,
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
                  execution_profile_revision_id,
                  execution_profile_revision_hash,
                  runner_config_json, reproducibility_fingerprint, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lane.lane_id,
                    plan.run_id,
                    lane.lane_key,
                    lane.role,
                    lane.target_id,
                    lane.target_revision_id,
                    lane.execution_profile_revision_id,
                    lane.execution_profile_revision_hash,
                    canonical_json(lane.effective_runner_config),
                    lane.fingerprint,
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
                  template_index, trial_id, max_steps,
                  sequence_index, sequence_group_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    episode.sequence_index,
                    episode.sequence_group_id,
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
                    {"state": "queued", "fingerprint": plan.fingerprint}
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
                "POST /api/platform/v1/run-launch",
                request_hash,
                plan.run_id,
                plan.created_at,
            ),
        )


def _lane_slots(definition: WorkflowDefinitionV2) -> dict[str, dict[str, Any]]:
    matrix = next((node for node in definition.nodes if node.type == "matrix"), None)
    lane_slots = matrix.config.get("lane_slots") if matrix is not None else None
    if not isinstance(lane_slots, dict) or not lane_slots:
        raise RunLaunchError(
            "RUN_LANE_BINDING_INCOMPLETE",
            "Workflow Version v2 does not define Lane Slots.",
        )
    return lane_slots


def _binding_map(bindings) -> dict[str, Any]:
    by_slot: dict[str, Any] = {}
    for binding in bindings:
        lane_slot = binding.lane_slot.strip()
        if not lane_slot or lane_slot in by_slot:
            raise RunLaunchError(
                "RUN_LANE_BINDING_INCOMPLETE",
                "Lane Binding slots must be non-empty and unique.",
            )
        by_slot[lane_slot] = binding
    return by_slot


def _require_exact_revision_id(value: str, *, field: str, lane_slot: str) -> None:
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in _NON_EXACT_REVISION_SELECTORS:
        raise RunLaunchError(
            "RUN_LANE_BINDING_INCOMPLETE",
            "Lane Bindings require exact revision IDs.",
            details=[{"lane_slot": lane_slot, "field": field}],
        )


def _cross_project_error(resource: str, resource_id: str) -> RunLaunchError:
    return RunLaunchError(
        "RUN_LANE_BINDING_CROSS_PROJECT",
        "Lane Binding revisions must belong to the selected Project.",
        status_code=409,
        details=[{"resource": resource, "resource_id": resource_id}],
    )


def _read_workflow_definition_for_plan(
    database: Database,
    workflow_version_id: str,
) -> WorkflowDefinitionV2:
    version = WorkflowRepository(database).get_version(workflow_version_id)
    definition = read_workflow_definition(version.definition)
    if not isinstance(definition, WorkflowDefinitionV2):
        raise RunLaunchError(
            "RUN_WORKFLOW_SCHEMA_REQUIRED",
            "Profile-aware launch requires a Workflow Version v2.",
            status_code=409,
        )
    return definition


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )
