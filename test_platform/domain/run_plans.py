from __future__ import annotations

from copy import deepcopy
import random
from typing import Any, Literal
import zlib

from pydantic import BaseModel, Field

from test_platform.domain.canonical_json import canonical_sha256
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.domain.workflows import WorkflowDefinition


class TaskSourceRevision(BaseModel):
    repository_revision: str
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


class RunPlan(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    workflow_version_id: str
    task_source: TaskSourceRevision
    lanes: list[PlannedLane]
    episodes: list[EpisodeTemplate]
    materialization: dict[str, Any]
    comparison: dict[str, Any]
    agent: dict[str, Any]
    judge: dict[str, Any]
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
        agent = _pick_config(
            execute_node.config,
            ("agent", "model_name", "model_base_url", "temperature", "top_p", "max_tokens"),
        )
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
            "agent": agent,
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
            agent=agent,
            judge=judge,
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


def _selected_tasks(
    config: dict[str, Any],
    catalog: TaskCatalogSnapshot,
) -> list[TaskCatalogItem]:
    task_ids = config.get("task_ids")
    requested = {str(task_id) for task_id in task_ids} if isinstance(task_ids, list) else set()
    suites = {str(suite) for suite in config.get("suites", [])} if isinstance(config.get("suites"), list) else set()
    difficulties = (
        {str(value) for value in config.get("difficulty", [])}
        if isinstance(config.get("difficulty"), list)
        else set()
    )
    selected = [
        item
        for item in catalog.items
        if (not requested or item.task_base_id in requested)
        and (not suites or item.suite in suites)
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
