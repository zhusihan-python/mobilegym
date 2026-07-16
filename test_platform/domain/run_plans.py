from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import random
from typing import Any, Literal
import zlib

from pydantic import BaseModel, Field

from test_platform.domain.canonical_json import canonical_sha256
from test_platform.domain.comparison_constraints import DEFAULT_TARGET_CONSTRAINTS
from test_platform.domain.execution_profiles import (
    ExecutionProfile,
    ExecutionProfileRevision,
    ExecutionProfileSpec,
)
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.domain.targets import Target, TargetRevision
from test_platform.domain.workflows import WorkflowDefinition, WorkflowDefinitionV2


class TaskSourceRevision(BaseModel):
    repository_revision: str | None
    registry_digest: str
    selection: dict[str, Any] = Field(default_factory=dict)


class PlannedLane(BaseModel):
    lane_id: str
    lane_key: str
    role: str
    target_id: str
    target_revision_id: str
    target_revision_hash: str
    runner_config: dict[str, Any]


class EpisodeTemplate(BaseModel):
    episode_key: str
    materialization_key: str
    pair_key: str
    task_base_id: str
    task_id: str
    instance_id: int
    instance_seed: int
    template_index: int | None
    trial_id: int
    max_steps: int
    sequence_index: int | None = None
    sequence_group_id: str | None = None


class RunPlan(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    workflow_version_id: str
    task_source: TaskSourceRevision
    lanes: list[PlannedLane]
    episodes: list[EpisodeTemplate]
    materialization: dict[str, Any]
    comparison: dict[str, Any]
    gates: dict[str, Any] = Field(default_factory=dict)
    agent: dict[str, Any]
    judge: dict[str, Any]
    artifacts: dict[str, Any]
    created_at: str
    fingerprint: str


class FrozenExecutionSnapshot(BaseModel):
    execution_profile_id: str
    execution_profile_name: str
    execution_profile_revision_id: str
    execution_profile_revision_no: int
    public_spec_hash: str
    revision_hash: str
    credential_binding_digest: str
    public_spec: ExecutionProfileSpec


@dataclass(frozen=True)
class ResolvedLanePlanBinding:
    target: Target
    target_revision: TargetRevision
    execution_profile: ExecutionProfile
    execution_profile_revision: ExecutionProfileRevision


class PlannedLaneV2(BaseModel):
    lane_id: str
    lane_key: str
    role: str
    target_id: str
    target_revision_id: str
    target_revision_hash: str
    execution_profile_revision_id: str
    execution_profile_revision_hash: str
    execution_snapshot_key: str
    effective_runner_config: dict[str, Any]
    fingerprint: str

    @property
    def runner_config(self) -> dict[str, Any]:
        return self.effective_runner_config


class RunPlanV2(BaseModel):
    schema_version: Literal[2] = 2
    run_id: str
    workflow_version_id: str
    workflow_version_hash: str
    task_source: TaskSourceRevision
    execution_snapshots: dict[str, FrozenExecutionSnapshot]
    lanes: list[PlannedLaneV2]
    episodes: list[EpisodeTemplate]
    materialization: dict[str, Any]
    evaluation: dict[str, Any]
    comparison: dict[str, Any]
    gates: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any]
    created_at: str
    fingerprint: str


class RunPlanCompiler:
    def compile(
        self,
        *,
        run_id: str,
        workflow_version_id: str,
        definition: WorkflowDefinition,
        catalog: TaskCatalogSnapshot,
        targets: dict[str, Any],
        seed: int,
        created_at: str,
        compatibility_summary: dict[str, Any] | None = None,
    ) -> RunPlan:
        task_node = _required_node(definition, "task_selection")
        matrix_node = _required_node(definition, "matrix")
        execute_node = _required_node(definition, "execute")
        selected_tasks = _selected_tasks(task_node.config, catalog)
        sample_n = _positive_int(task_node.config.get("sample_n"), default=1)
        repeat_n = _positive_int(matrix_node.config.get("repeat_n"), default=1)

        lanes = _compile_lanes(
            run_id=run_id,
            lanes_config=matrix_node.config.get("lanes"),
            execute_config=execute_node.config,
            targets=targets,
        )
        episodes = _compile_episodes(
            selected_tasks,
            sample_n=sample_n,
            repeat_n=repeat_n,
            seed=seed,
            execute_config=execute_node.config,
        )
        task_source = TaskSourceRevision(
            repository_revision=catalog.repository_revision,
            registry_digest=catalog.digest,
            selection={
                "task_ids": [item.task_base_id for item in selected_tasks],
                "sample_n": sample_n,
                "seed": seed,
            },
        )
        comparison = _node_config(definition, "compare")
        gates = _gate_thresholds(definition)
        agent = _pick_config(
            execute_node.config,
            (
                "agent",
                "model_name",
                "model_base_url",
                "image_url_format",
                "temperature",
                "top_p",
                "max_tokens",
            ),
        )
        if compatibility_summary is not None:
            agent["compatibility"] = compatibility_summary
        judge = _pick_config(
            execute_node.config,
            ("judge_mode", "judge_model", "judge_base_url", "eval_mode"),
        )
        artifacts = {
            "run_plan": "platform/run-plan.json",
            "target_revisions": "platform/target-revisions.json",
        }
        materialization = {
            "source_lane_id": lanes[0].lane_id if lanes else None,
            "policy": "shared_explicit_params",
            "strict_data_revision": True,
            "strict_time_location": True,
        }

        fingerprint_payload = {
            "schema_version": 1,
            "workflow_version_id": workflow_version_id,
            "task_source": task_source.model_dump(mode="json"),
            "lanes": [
                lane.model_dump(
                    mode="json",
                    exclude={"lane_id"},
                )
                for lane in lanes
            ],
            "episodes": [episode.model_dump(mode="json") for episode in episodes],
            "materialization": {
                **materialization,
                "source_lane_id": lanes[0].lane_key if lanes else None,
            },
            "comparison": comparison,
            "gates": gates,
            # Exclude compatibility summary from fingerprint — it contains
            # latency/cache metadata that varies per check and must not break
            # fingerprint reproducibility.
            "agent": {k: v for k, v in agent.items() if k != "compatibility"},
            "judge": judge,
            "artifacts": artifacts,
        }

        return RunPlan(
            run_id=run_id,
            workflow_version_id=workflow_version_id,
            task_source=task_source,
            lanes=lanes,
            episodes=episodes,
            materialization=materialization,
            comparison=comparison,
            gates=gates,
            agent=agent,
            judge=judge,
            artifacts=artifacts,
            created_at=created_at,
            fingerprint=canonical_sha256(fingerprint_payload),
        )


class RunPlanV2Compiler:
    def compile(
        self,
        *,
        run_id: str,
        workflow_version_id: str,
        workflow_version_hash: str,
        definition: WorkflowDefinitionV2,
        catalog: TaskCatalogSnapshot,
        bindings: dict[str, ResolvedLanePlanBinding],
        seed: int,
        created_at: str,
        comparison_intent: Literal["single", "target_comparison"],
    ) -> RunPlanV2:
        task_node = _required_node(definition, "task_selection")
        matrix_node = _required_node(definition, "matrix")
        execute_node = _required_node(definition, "execute")
        selected_tasks = _selected_tasks(task_node.config, catalog)
        sample_n = _positive_int(task_node.config.get("sample_n"), default=1)
        repeat_n = _positive_int(matrix_node.config.get("repeat_n"), default=1)
        lane_slots = matrix_node.config.get("lane_slots")
        if not isinstance(lane_slots, dict):
            raise ValueError("Workflow v2 matrix lane_slots must be an object.")

        workflow_execution = _workflow_execution_projection(execute_node.config)
        snapshots: dict[str, FrozenExecutionSnapshot] = {}
        lanes: list[PlannedLaneV2] = []
        for lane_slot in sorted(lane_slots):
            slot_config = lane_slots[lane_slot]
            resolved = bindings.get(lane_slot)
            if not isinstance(slot_config, dict) or resolved is None:
                raise ValueError(f"Lane Slot '{lane_slot}' was not resolved.")
            target = resolved.target
            target_revision = resolved.target_revision
            profile = resolved.execution_profile
            profile_revision = resolved.execution_profile_revision
            public_spec = ExecutionProfileSpec.model_validate(
                _field(profile_revision, "public_spec")
            )
            public_spec_hash = str(_field(profile_revision, "public_spec_hash"))
            credential_binding_digest = str(
                _field(profile_revision, "credential_binding_digest")
            )
            revision_hash = canonical_sha256(
                {
                    "public_spec_hash": public_spec_hash,
                    "credential_binding_digest": credential_binding_digest,
                }
            )
            snapshot_key = str(_field(profile_revision, "id"))
            snapshots[snapshot_key] = FrozenExecutionSnapshot(
                execution_profile_id=str(_field(profile, "id")),
                execution_profile_name=str(_field(profile, "name")),
                execution_profile_revision_id=snapshot_key,
                execution_profile_revision_no=int(
                    _field(profile_revision, "revision_no")
                ),
                public_spec_hash=public_spec_hash,
                revision_hash=revision_hash,
                credential_binding_digest=credential_binding_digest,
                public_spec=public_spec,
            )
            effective_runner_config = _compose_profile_aware_runner_config(
                target=target,
                workflow_execution=workflow_execution,
                public_spec=public_spec,
            )
            lane_payload = {
                "lane_key": lane_slot,
                "role": str(slot_config.get("role") or lane_slot),
                "target_id": str(_field(target, "id")),
                "target_revision_id": str(_field(target_revision, "id")),
                "target_revision_hash": str(
                    _field(target_revision, "metadata_hash")
                ),
                "execution_profile_revision_id": snapshot_key,
                "execution_profile_revision_hash": revision_hash,
                "execution_snapshot_key": snapshot_key,
                "effective_runner_config": effective_runner_config,
            }
            lane_fingerprint = canonical_sha256(lane_payload)
            lanes.append(
                PlannedLaneV2(
                    lane_id=f"{run_id}:{lane_slot}",
                    **lane_payload,
                    fingerprint=lane_fingerprint,
                )
            )

        episodes = _compile_episodes(
            selected_tasks,
            sample_n=sample_n,
            repeat_n=repeat_n,
            seed=seed,
            execute_config=execute_node.config,
        )
        task_source = TaskSourceRevision(
            repository_revision=catalog.repository_revision,
            registry_digest=catalog.digest,
            selection={
                "task_ids": [item.task_base_id for item in selected_tasks],
                "sample_n": sample_n,
                "seed": seed,
            },
        )
        materialization = {
            "source_lane_slot": lanes[0].lane_key if lanes else None,
            "policy": "shared_explicit_params",
            "strict_data_revision": True,
            "strict_time_location": True,
        }
        evaluation = _pick_config(
            execute_node.config,
            ("judge_mode", "judge_model", "judge_base_url", "eval_mode"),
        )
        comparison: dict[str, Any] = {"intent": comparison_intent}
        if comparison_intent == "target_comparison":
            compare_node = _required_node(definition, "compare")
            configured_constraints = compare_node.config.get("target_constraints")
            comparison.update(
                {
                    "target_constraints": (
                        list(configured_constraints)
                        if isinstance(configured_constraints, list)
                        else list(DEFAULT_TARGET_CONSTRAINTS)
                    ),
                    "initial_state_policy": str(
                        compare_node.config.get("initial_state_policy")
                        or "task_projection"
                    ),
                    "execution": str(
                        compare_node.config.get("execution") or "serial"
                    ),
                }
            )
        gates = _gate_thresholds(definition)
        artifacts = {
            "run_plan": "platform/run-plan.json",
            "target_revisions": "platform/target-revisions.json",
        }
        fingerprint_payload = {
            "schema_version": 2,
            "workflow_version_id": workflow_version_id,
            "workflow_version_hash": workflow_version_hash,
            "task_source": task_source.model_dump(mode="json"),
            "execution_snapshots": {
                key: snapshot.model_dump(
                    mode="json",
                    exclude={"execution_profile_name"},
                )
                for key, snapshot in sorted(snapshots.items())
            },
            "lane_fingerprints": [lane.fingerprint for lane in lanes],
            "episodes": [episode.model_dump(mode="json") for episode in episodes],
            "materialization": materialization,
            "evaluation": evaluation,
            "comparison": comparison,
            "gates": gates,
            "artifacts": artifacts,
        }
        return RunPlanV2(
            run_id=run_id,
            workflow_version_id=workflow_version_id,
            workflow_version_hash=workflow_version_hash,
            task_source=task_source,
            execution_snapshots=snapshots,
            lanes=lanes,
            episodes=episodes,
            materialization=materialization,
            evaluation=evaluation,
            comparison=comparison,
            gates=gates,
            artifacts=artifacts,
            created_at=created_at,
            fingerprint=canonical_sha256(fingerprint_payload),
        )


def _required_node(definition: WorkflowDefinition, node_type: str):
    node = next((item for item in definition.nodes if item.type == node_type), None)
    if node is None:
        raise ValueError(f"Workflow requires a {node_type} node.")
    return node


def _node_config(definition: WorkflowDefinition, node_type: str) -> dict[str, Any]:
    node = next((item for item in definition.nodes if item.type == node_type), None)
    return _sanitize(node.config) if node else {}


def _gate_thresholds(definition: WorkflowDefinition) -> dict[str, Any]:
    config = _node_config(definition, "gate")
    thresholds = config.get("thresholds") if isinstance(config.get("thresholds"), dict) else config
    return _sanitize(thresholds) if isinstance(thresholds, dict) else {}


def _selected_tasks(
    config: dict[str, Any],
    catalog: TaskCatalogSnapshot,
) -> list[TaskCatalogItem]:
    task_ids = config.get("task_ids")
    requested_order = _ordered_task_ids(task_ids)
    requested = set(requested_order)
    suites = {str(suite) for suite in config.get("suites", [])} if isinstance(config.get("suites"), list) else set()
    difficulties = (
        {str(value) for value in config.get("difficulty", [])}
        if isinstance(config.get("difficulty"), list)
        else set()
    )
    if requested_order:
        catalog_by_id = {item.task_base_id: item for item in catalog.items}
        selected = [
            catalog_by_id[task_id]
            for task_id in requested_order
            if task_id in catalog_by_id
        ]
    else:
        selected = [
            item
            for item in catalog.items
            if (not suites or item.suite in suites)
            and (not difficulties or item.difficulty in difficulties)
        ]
    missing = sorted(requested - {item.task_base_id for item in selected})
    if missing:
        raise ValueError(
            f"Published workflow tasks are missing from the catalog: {', '.join(missing)}."
        )
    if not selected:
        raise ValueError("Published workflow task selection is empty.")
    return selected


def _ordered_task_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for item in value:
        task_id = str(item)
        if task_id in seen:
            continue
        ordered.append(task_id)
        seen.add(task_id)
    return ordered


def _compile_lanes(
    *,
    run_id: str,
    lanes_config: Any,
    execute_config: dict[str, Any],
    targets: dict[str, Any],
) -> list[PlannedLane]:
    if not isinstance(lanes_config, dict):
        raise ValueError("Workflow matrix lanes must be an object.")

    lanes: list[PlannedLane] = []
    for lane_key in sorted(lanes_config):
        lane_config = lanes_config[lane_key]
        if not isinstance(lane_config, dict):
            raise ValueError(f"Lane '{lane_key}' config must be an object.")
        target_id = str(lane_config.get("target_id") or "")
        resolved = targets.get(target_id)
        if resolved is None:
            raise ValueError(f"Target '{target_id}' was not resolved.")
        target = _field(resolved, "target")
        revision = _field(resolved, "revision")
        if revision is None:
            raise ValueError(f"Target '{target_id}' does not have a resolved revision.")

        target_config = deepcopy(_field(target, "config", {}))
        connection = target_config.get("connection", {})
        device_profile = target_config.get("device_profile", {})
        runner_config = {
            "device": "sim" if target_config.get("kind") == "simulator" else "real",
            "env_url": connection.get("env_url"),
            "viewport_size": [
                device_profile.get("viewport_width"),
                device_profile.get("viewport_height"),
            ],
            "physical_size": [
                device_profile.get("physical_width"),
                device_profile.get("physical_height"),
            ],
            "device_scale_factor": device_profile.get("device_scale_factor"),
            "runtime": target_config.get("runtime", {}),
            **execute_config,
            **{
                key: value
                for key, value in lane_config.items()
                if key not in {"target_id", "role"}
            },
        }
        if isinstance(connection, dict):
            runner_config.update(connection)

        lanes.append(
            PlannedLane(
                lane_id=f"{run_id}:{lane_key}",
                lane_key=lane_key,
                role=str(lane_config.get("role") or lane_key),
                target_id=target_id,
                target_revision_id=str(_field(revision, "id")),
                target_revision_hash=str(_field(revision, "metadata_hash")),
                runner_config=_sanitize(runner_config),
            )
        )
    return lanes


def _compile_episodes(
    tasks: list[TaskCatalogItem],
    *,
    sample_n: int,
    repeat_n: int,
    seed: int,
    execute_config: dict[str, Any],
) -> list[EpisodeTemplate]:
    episodes: list[EpisodeTemplate] = []
    sample_templates = bool(execute_config.get("sample_templates", False))
    is_linear_sequence = execute_config.get("execution_strategy") == "linear_sequence"
    for task in tasks:
        for instance_id in range(sample_n):
            instance_seed = (
                seed ^ zlib.crc32(f"{task.task_base_id}:{instance_id}".encode())
            ) & 0xFFFFFFFF
            template_index = None
            if sample_templates and len(task.templates) > 1:
                template_index = random.Random(
                    f"tpl:{instance_seed}:{task.task_base_id}:{instance_id}"
                ).randrange(len(task.templates))
            materialization_key = (
                f"{task.task_base_id}|i{instance_id}|s{instance_seed}|r1"
            )
            task_id = task.task_base_id if sample_n == 1 else f"{task.task_base_id}_i{instance_id}"
            max_steps = _max_steps(task, execute_config)
            for trial_id in range(repeat_n):
                episode_key = f"{materialization_key}|t{trial_id}"
                sequence_index = len(episodes) if is_linear_sequence else None
                episodes.append(
                    EpisodeTemplate(
                        episode_key=episode_key,
                        materialization_key=materialization_key,
                        pair_key=episode_key,
                        task_base_id=task.task_base_id,
                        task_id=task_id,
                        instance_id=instance_id,
                        instance_seed=instance_seed,
                        template_index=template_index,
                        trial_id=trial_id,
                        max_steps=max_steps,
                        sequence_index=sequence_index,
                        sequence_group_id="manual_sequence" if is_linear_sequence else None,
                    )
                )
    return episodes


def _max_steps(task: TaskCatalogItem, execute_config: dict[str, Any]) -> int:
    explicit = execute_config.get("max_steps")
    if isinstance(explicit, int) and not isinstance(explicit, bool) and explicit > 0:
        steps = explicit
    elif task.max_steps is not None:
        steps = task.max_steps
    else:
        steps = {"L1": 15, "L2": 30, "L3": 45, "L4": 60}.get(task.difficulty, 30)
    if execute_config.get("eval_mode", "grounded") == "grounded" and task.answer_fields:
        steps += 15
    return steps


_PROFILE_AWARE_WORKFLOW_EXECUTION_KEYS = (
    "eval_mode",
    "execution_strategy",
    "failure_policy",
    "judge_base_url",
    "judge_mode",
    "judge_model",
    "max_steps",
    "parallel",
    "processes",
    "sample_templates",
    "state_policy",
)


def _workflow_execution_projection(config: dict[str, Any]) -> dict[str, Any]:
    return _pick_config(config, _PROFILE_AWARE_WORKFLOW_EXECUTION_KEYS)


def _compose_profile_aware_runner_config(
    *,
    target: Any,
    workflow_execution: dict[str, Any],
    public_spec: ExecutionProfileSpec,
) -> dict[str, Any]:
    target_config = deepcopy(_field(target, "config", {}))
    connection = target_config.get("connection", {})
    device_profile = target_config.get("device_profile", {})
    if not isinstance(connection, dict):
        connection = {}
    if not isinstance(device_profile, dict):
        device_profile = {}
    return _sanitize(
        {
            "device": (
                "sim" if target_config.get("kind") == "simulator" else "real"
            ),
            "env_url": connection.get("env_url"),
            "proxy_configured": bool(connection.get("proxy_secret_ref")),
            "viewport_size": [
                device_profile.get("viewport_width"),
                device_profile.get("viewport_height"),
            ],
            "physical_size": [
                device_profile.get("physical_width"),
                device_profile.get("physical_height"),
            ],
            "device_scale_factor": device_profile.get("device_scale_factor"),
            "runtime": target_config.get("runtime", {}),
            **workflow_execution,
            "agent": public_spec.agent.id,
            "model_base_url": public_spec.model.base_url,
            "model_name": public_spec.model.name,
            "image_url_format": public_spec.image_input.format,
            "temperature": public_spec.generation.temperature,
            "top_p": public_spec.generation.top_p,
            "max_tokens": public_spec.generation.max_tokens,
            "no_stream": not public_spec.generation.stream,
            "infer_timeout": public_spec.inference.timeout_seconds,
        }
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            normalized = key.lower()
            if any(token in normalized for token in ("api_key", "password", "secret", "token")):
                public_key = _configured_key(key)
                sanitized[public_key] = bool(child)
            else:
                sanitized[key] = _sanitize(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    if isinstance(value, tuple):
        return [_sanitize(child) for child in value]
    return value


def _configured_key(key: str) -> str:
    if key.endswith("_secret_ref"):
        return f"{key[:-len('_secret_ref')]}_configured"
    if key.endswith("_api_key"):
        return f"{key}_configured"
    for suffix in ("_password", "_token"):
        if key.endswith(suffix):
            return f"{key[:-len(suffix)]}_configured"
    return f"{key}_configured"


def _pick_config(config: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return _sanitize({key: config[key] for key in keys if key in config})


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
