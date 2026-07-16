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
from test_platform.domain.comparison_constraints import evaluate_target_constraints
from test_platform.domain.execution_profiles import (
    ExecutionProfileDomainError,
    ExecutionProfileRevisionDiff,
)
from test_platform.domain.execution_secrets import (
    SecretRequirement,
    SecretResolutionError,
    SecretResolver,
)
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
from test_platform.services.compatibility_preflight import CompatibilityPreflight
from test_platform.services.execution_profiles import ExecutionProfiles
from test_platform.services.runs import register_run_execution_secrets


_NON_EXACT_REVISION_SELECTORS = frozenset({"current", "draft", "head", "latest"})


class RunLaunch:
    def __init__(
        self,
        database: Database,
        *,
        settings: PlatformSettings | None = None,
        supervisor: Any = None,
        catalog_builder: Callable[[], TaskCatalogSnapshot] = build_task_catalog_snapshot,
        secret_resolver: SecretResolver | None = None,
        compatibility_preflight: CompatibilityPreflight | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._supervisor = supervisor
        self._catalog_builder = catalog_builder
        self._secret_resolver = secret_resolver
        self._compatibility_preflight = compatibility_preflight

    def preview(self, command: PreviewRunLaunch) -> RunLaunchPreview:
        plan, workflow_version_hash, constraint_violations = self._compile_plan(
            command,
            run_id="preview",
            created_at="1970-01-01T00:00:00.000Z",
        )
        return self._preview_from_plan(
            command,
            plan,
            workflow_version_hash,
            constraint_violations,
        )

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
        if self._compatibility_preflight is None:
            raise RuntimeError("RunLaunch create requires compatibility preflight.")
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
        if preview.constraint_violations:
            raise RunLaunchError(
                "RUN_COMPARISON_CONSTRAINT_VIOLATED",
                "The Target Comparison constraints are not satisfied.",
                status_code=409,
                details=preview.constraint_violations,
            )

        run_id = new_id()
        created_at = _utc_timestamp()
        plan, _workflow_version_hash, _constraint_violations = self._compile_plan(
            preview_command,
            run_id=run_id,
            created_at=created_at,
        )
        requirements = self._secret_requirements(
            plan,
            project_id=command.project_id,
        )
        execution_secrets = self._resolve_secrets(requirements, secret_bindings)
        compatibility = self._preflight(plan, execution_secrets)
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
            (
                transaction_plan,
                transaction_workflow_hash,
                transaction_constraint_violations,
            ) = self._compile_plan(
                preview_command,
                run_id=run_id,
                created_at=created_at,
            )
            transaction_preview = self._preview_from_plan(
                preview_command,
                transaction_plan,
                transaction_workflow_hash,
                transaction_constraint_violations,
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
                compatibility=compatibility,
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

        if execution_secrets:
            register_run_execution_secrets(run_id, execution_secrets)
        self._supervisor.submit(run_id)
        return RunRepository(self._database).get(run_id)

    def _preview_from_plan(
        self,
        command: PreviewRunLaunch,
        plan: RunPlanV2,
        workflow_version_hash: str,
        constraint_violations: list[dict[str, Any]],
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
            execution_profile_diff=self._execution_profile_diff(
                command,
                plan,
            ),
            constraint_violations=constraint_violations,
            episode_count=len(plan.episodes),
            fingerprint_inputs=fingerprint_inputs,
            run_plan_fingerprint=plan.fingerprint,
            preview_token=preview_token,
            credential_requirements=sorted(
                {
                    requirement.slot
                    for requirement in self._secret_requirements(
                        plan,
                        project_id=command.project_id,
                    )
                }
            ),
        )

    def _execution_profile_diff(
        self,
        command: PreviewRunLaunch,
        plan: RunPlanV2,
    ) -> ExecutionProfileRevisionDiff | None:
        if command.comparison_intent != "execution_comparison":
            return None
        lane_by_key = {lane.lane_key: lane for lane in plan.lanes}
        return ExecutionProfiles(self._database).diff_revisions(
            project_id=command.project_id,
            from_revision_id=(
                lane_by_key["baseline"].execution_profile_revision_id
            ),
            to_revision_id=(
                lane_by_key["candidate"].execution_profile_revision_id
            ),
        )

    def _compile_plan(
        self,
        command: PreviewRunLaunch,
        *,
        run_id: str,
        created_at: str,
    ) -> tuple[RunPlanV2, str, list[dict[str, Any]]]:
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
        if command.comparison_intent not in {
            "single",
            "target_comparison",
            "execution_comparison",
        }:
            raise RunLaunchError(
                "RUN_COMPARISON_UNSUPPORTED",
                "The requested comparison intent is not supported.",
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
        _validate_comparison_shape(
            command.comparison_intent,
            lane_slots=lane_slots,
            binding_by_slot=binding_by_slot,
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
        constraint_violations = _target_comparison_constraint_violations(
            definition,
            resolved_bindings,
        ) if command.comparison_intent == "target_comparison" else []
        return plan, version.definition_hash, constraint_violations

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

    def _secret_requirements(
        self,
        plan: RunPlanV2,
        *,
        project_id: str,
    ) -> tuple[SecretRequirement, ...]:
        repository = ExecutionProfileRepository(self._database)
        requirements: list[SecretRequirement] = []
        for lane in plan.lanes:
            snapshot = plan.execution_snapshots[lane.execution_snapshot_key]
            required_slots = set(snapshot.public_spec.credentials.required_slots)
            revision, profile = repository.get_revision(
                lane.execution_profile_revision_id
            )
            if profile.project_id != project_id:
                raise _cross_project_error(
                    "execution_profile_revision",
                    revision.id,
                )
            bindings = {binding.slot: binding for binding in revision.credential_bindings}
            if set(bindings) != required_slots:
                raise RunLaunchError(
                    "RUN_EXECUTION_SECRET_UNAVAILABLE",
                    "The frozen credential references are unavailable.",
                    status_code=503,
                    details=[
                        {
                            "lane_slot": lane.lane_key,
                            "slots": sorted(required_slots),
                        }
                    ],
                )
            for slot in sorted(required_slots):
                binding = bindings[slot]
                if binding.project_id != project_id:
                    raise RunLaunchError(
                        "EXECUTION_PROFILE_CREDENTIAL_BINDING_CROSS_PROJECT",
                        "A frozen Credential Reference belongs to another Project.",
                        status_code=409,
                        details=[{"lane_slot": lane.lane_key, "slot": slot}],
                    )
                requirements.append(
                    SecretRequirement(
                        slot=slot,
                        project_id=project_id,
                        execution_profile_revision_id=revision.id,
                        backend=binding.backend,
                        reference_id=binding.reference_id,
                        private_locator=binding.private_locator,
                        lane_keys=(lane.lane_key,),
                    )
                )
        return tuple(requirements)

    def _resolve_secrets(
        self,
        requirements: tuple[SecretRequirement, ...],
        supplied_bindings: dict[str, str],
    ) -> dict[str, str]:
        required_slots = {requirement.slot for requirement in requirements}
        supplied_slots = set(supplied_bindings)
        missing_slots = sorted(
            slot
            for slot in required_slots
            if slot not in supplied_bindings
            or not isinstance(supplied_bindings[slot], str)
            or not supplied_bindings[slot].strip()
        )
        if missing_slots:
            raise RunLaunchError(
                "RUN_EXECUTION_SECRET_MISSING",
                "Required execution credentials were not supplied.",
                details=[{"slots": missing_slots}],
            )
        extra_slots = sorted(supplied_slots - required_slots)
        if extra_slots:
            raise RunLaunchError(
                "RUN_EXECUTION_SECRET_SLOT_INVALID",
                "Execution credentials were supplied for undeclared slots.",
                details=[{"slots": extra_slots}],
            )
        if not requirements:
            return {}
        if self._secret_resolver is None:
            raise RuntimeError("RunLaunch create requires a SecretResolver.")
        declared_bindings = {
            slot: supplied_bindings[slot]
            for slot in sorted(required_slots)
        }
        try:
            lease = self._secret_resolver.resolve(requirements, declared_bindings)
        except SecretResolutionError as exc:
            code = (
                exc.code
                if exc.code
                in {
                    "RUN_EXECUTION_SECRET_MISSING",
                    "RUN_EXECUTION_SECRET_SLOT_INVALID",
                    "RUN_EXECUTION_SECRET_UNAVAILABLE",
                }
                else "RUN_EXECUTION_SECRET_UNAVAILABLE"
            )
            messages = {
                "RUN_EXECUTION_SECRET_MISSING": (
                    "Required execution credentials were not supplied."
                ),
                "RUN_EXECUTION_SECRET_SLOT_INVALID": (
                    "Execution credentials were supplied for undeclared slots."
                ),
                "RUN_EXECUTION_SECRET_UNAVAILABLE": (
                    "Required execution credentials are unavailable."
                ),
            }
            raise RunLaunchError(
                code,
                messages[code],
                status_code=(
                    503 if code == "RUN_EXECUTION_SECRET_UNAVAILABLE" else 400
                ),
                details=[{"slots": sorted(required_slots)}],
            ) from exc
        values = {
            key: value.strip()
            for key, value in lease.values.items()
            if key in required_slots
            and isinstance(value, str)
            and value.strip()
        }
        unresolved_slots = sorted(required_slots - set(values))
        if unresolved_slots:
            raise RunLaunchError(
                "RUN_EXECUTION_SECRET_UNAVAILABLE",
                "Required execution credentials are unavailable.",
                status_code=503,
                details=[{"slots": unresolved_slots}],
            )
        return values

    def _preflight(
        self,
        plan: RunPlanV2,
        execution_secrets: dict[str, str],
    ) -> list[dict[str, Any]]:
        if self._compatibility_preflight is None:
            raise RuntimeError("RunLaunch create requires compatibility preflight.")
        grouped: dict[tuple[str, str, str, str, str], list[str]] = {}
        lane_by_key = {lane.lane_key: lane for lane in plan.lanes}
        for lane in plan.lanes:
            config = lane.effective_runner_config
            subject = (
                str(config.get("agent") or ""),
                str(config.get("model_base_url") or ""),
                str(config.get("model_name") or ""),
                str(config.get("image_url_format") or "data_url"),
                str(execution_secrets.get("model_api_key") or ""),
            )
            grouped.setdefault(subject, []).append(lane.lane_key)

        checks: list[dict[str, Any]] = []
        for subject, lane_keys in grouped.items():
            agent, base_url, model, image_url_format, api_key = subject
            lane = lane_by_key[lane_keys[0]]
            result = self._compatibility_preflight.check(
                agent=agent,
                base_url=base_url,
                model=model,
                image_url_format=image_url_format,
                api_key=api_key,
                timeout_seconds=float(
                    lane.effective_runner_config.get("infer_timeout") or 15.0
                ),
            )
            evidence = result.to_provenance()
            evidence["lane_keys"] = sorted(lane_keys)
            checks.append(evidence)
            if result.outcome == "failed":
                raise RunLaunchError(
                    "RUN_COMPATIBILITY_CHECK_FAILED",
                    result.explanation,
                    status_code=409,
                    details=[
                        {
                            "code": result.code,
                            "lane_keys": sorted(lane_keys),
                            "checked_model": result.checked_model or model,
                            "checked_image_format": (
                                result.checked_image_format or image_url_format
                            ),
                        }
                    ],
                )
        return checks

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
        compatibility: list[dict[str, Any]],
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
              id, run_id, attempt_no, reason, state, compatibility_json,
              created_at
            )
            VALUES (?, ?, 1, 'initial', 'queued', ?, ?)
            """,
            (
                run_attempt_id,
                plan.run_id,
                canonical_json(compatibility),
                plan.created_at,
            ),
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


def _validate_comparison_shape(
    comparison_intent: str,
    *,
    lane_slots: dict[str, dict[str, Any]],
    binding_by_slot: dict[str, Any],
) -> None:
    if comparison_intent == "single":
        if set(lane_slots) != {"candidate"}:
            raise RunLaunchError(
                "RUN_LANE_BINDING_INCOMPLETE",
                "Single launch requires exactly one candidate Lane Slot.",
                details=[{"lane_slots": sorted(lane_slots)}],
            )
        return

    if set(lane_slots) != {"baseline", "candidate"}:
        raise RunLaunchError(
            "RUN_LANE_BINDING_INCOMPLETE",
            "A paired comparison requires baseline and candidate Lane Slots.",
            details=[{"lane_slots": sorted(lane_slots)}],
        )

    target_revision_ids = {
        binding.target_revision_id for binding in binding_by_slot.values()
    }
    profile_revision_ids = {
        binding.execution_profile_revision_id for binding in binding_by_slot.values()
    }
    if len(target_revision_ids) == 2 and len(profile_revision_ids) == 2:
        raise RunLaunchError(
            "RUN_COMPARISON_CONFOUNDED",
            "Target and Execution Profile Revisions cannot both vary.",
            status_code=409,
        )
    if len(target_revision_ids) == 1 and len(profile_revision_ids) == 1:
        raise RunLaunchError(
            "RUN_COMPARISON_NO_VARIATION",
            "A comparison requires one varying revision axis.",
            status_code=409,
        )
    if comparison_intent == "target_comparison":
        if len(target_revision_ids) == 2 and len(profile_revision_ids) == 1:
            return
        message = (
            "Target Comparison requires different Target Revisions and one shared "
            "Execution Profile Revision."
        )
    else:
        if len(target_revision_ids) == 1 and len(profile_revision_ids) == 2:
            return
        message = (
            "Execution Comparison requires one shared Target Revision and different "
            "Execution Profile Revisions."
        )
    raise RunLaunchError(
        "RUN_COMPARISON_INTENT_MISMATCH",
        message,
        status_code=409,
    )


def _target_comparison_constraint_violations(
    definition: WorkflowDefinitionV2,
    resolved_bindings: dict[str, ResolvedLanePlanBinding],
) -> list[dict[str, Any]]:
    compare = next((node for node in definition.nodes if node.type == "compare"), None)
    configured_constraints = (
        compare.config.get("target_constraints") if compare is not None else None
    )
    constraints = configured_constraints if isinstance(configured_constraints, list) else None
    baseline = resolved_bindings["baseline"].target_revision
    candidate = resolved_bindings["candidate"].target_revision
    return [
        violation.to_dict()
        for violation in evaluate_target_constraints(
            baseline_metadata=baseline.metadata,
            candidate_metadata=candidate.metadata,
            constraints=constraints,
        )
    ]


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
