from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.domain.ids import new_id


class WorkflowDomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


class WorkflowNotFound(WorkflowDomainError):
    def __init__(self, workflow_id: str) -> None:
        super().__init__(
            "WORKFLOW_NOT_FOUND",
            "Workflow was not found.",
            status_code=404,
            details=[{"workflow_id": workflow_id}],
        )


class WorkflowVersionNotFound(WorkflowDomainError):
    def __init__(self, workflow_version_id: str) -> None:
        super().__init__(
            "WORKFLOW_VERSION_NOT_FOUND",
            "Workflow version was not found.",
            status_code=404,
            details=[{"workflow_version_id": workflow_version_id}],
        )


@dataclass(frozen=True)
class Workflow:
    id: str
    project_id: str
    name: str
    draft_definition: dict[str, Any] | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkflowVersion:
    id: str
    workflow_id: str
    version_no: int
    status: str
    definition: dict[str, Any]
    definition_hash: str
    created_at: str
    published_at: str


def new_workflow_id() -> str:
    return new_id()


def new_workflow_version_id() -> str:
    return new_id()


def workflow_utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_definition_hash(definition: dict[str, Any]) -> str:
    payload = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


WorkflowNodeType = Literal[
    "task_selection",
    "matrix",
    "execute",
    "compare",
    "gate",
    "report",
]


class WorkflowNode(BaseModel):
    id: str = Field(min_length=1)
    type: WorkflowNodeType
    depends_on: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinition(BaseModel):
    schema_version: int = 1
    name: str = Field(min_length=1)
    nodes: list[WorkflowNode] = Field(default_factory=list)


class WorkflowIssue(BaseModel):
    code: str
    message: str
    pointer: str
    node_id: str | None = None


class WorkflowValidationResult(BaseModel):
    valid: bool
    issues: list[WorkflowIssue] = Field(default_factory=list)


class WorkflowCompilePreview(BaseModel):
    task_count: int
    task_instance_count: int
    trial_count: int
    lane_count: int
    total_episodes: int
    lane_keys: list[str]
    ordered_task_ids: list[str] = Field(default_factory=list)
    execution_strategy: str = "batch"
    # VS-10 Contract 3: advisory constraint violations for paired (2-lane)
    # workflows. Populated by the compile-preview route after resolving the
    # latest target revisions and evaluating the compare node's
    # target_constraints. Empty list for single-lane workflows or when the
    # constraints are satisfied. The preview is ALWAYS a 200 (advisory) — the
    # authoritative gate is create-run.
    violations: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowValidator:
    def validate(
        self,
        definition: WorkflowDefinition,
        catalog: TaskCatalogSnapshot,
        targets: dict[str, Any],
    ) -> WorkflowValidationResult:
        issues: list[WorkflowIssue] = []
        node_indices = {node.id: index for index, node in enumerate(definition.nodes)}

        seen_ids: dict[str, int] = {}
        for index, node in enumerate(definition.nodes):
            if node.id in seen_ids:
                issues.append(
                    WorkflowIssue(
                        code="WORKFLOW_NODE_ID_DUPLICATE",
                        message=f"Workflow node '{node.id}' is duplicated.",
                        pointer=f"/nodes/{index}/id",
                        node_id=node.id,
                    )
                )
            seen_ids[node.id] = index

        for index, node in enumerate(definition.nodes):
            for dep_index, dependency in enumerate(node.depends_on):
                if dependency not in node_indices:
                    issues.append(
                        WorkflowIssue(
                            code="WORKFLOW_DEPENDENCY_MISSING",
                            message=f"Workflow node '{node.id}' depends on missing node '{dependency}'.",
                            pointer=f"/nodes/{index}/depends_on/{dep_index}",
                            node_id=node.id,
                        )
                    )

        if _has_cycle(definition.nodes, node_indices):
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_CYCLE",
                    message="Workflow dependencies contain a cycle.",
                    pointer="/nodes",
                )
            )

        node_types = {node.type for node in definition.nodes}
        for required_type in ("task_selection", "matrix", "execute"):
            if required_type not in node_types:
                issues.append(
                    WorkflowIssue(
                        code="WORKFLOW_NODE_TYPE_MISSING",
                        message=f"Workflow requires a {required_type} node.",
                        pointer="/nodes",
                    )
                )

        catalog_by_id = {item.task_base_id: item for item in catalog.items}
        for node_index, node in enumerate(definition.nodes):
            if node.type == "task_selection":
                issues.extend(_validate_task_selection(node, node_index, catalog, catalog_by_id))
            if node.type == "matrix":
                issues.extend(_validate_matrix_targets(node, node_index, targets))
            if node.type == "compare":
                issues.extend(_validate_compare_config(node, node_index))
            if node.type == "gate":
                issues.extend(_validate_gate_config(node, node_index))

        issues.extend(_validate_manual_sequence(definition, node_indices))

        return WorkflowValidationResult(valid=not issues, issues=issues)


class WorkflowCompiler:
    def compile_preview(
        self,
        definition: WorkflowDefinition,
        catalog: TaskCatalogSnapshot,
    ) -> WorkflowCompilePreview:
        selected_tasks: list[TaskCatalogItem] = []
        selected_ids: set[str] = set()
        max_sample_n = 1

        for node in definition.nodes:
            if node.type != "task_selection":
                continue
            sample_n = _positive_int(node.config.get("sample_n"), default=1)
            max_sample_n = max(max_sample_n, sample_n)
            for item in _select_tasks(node.config, catalog):
                if item.task_base_id not in selected_ids:
                    selected_tasks.append(item)
                    selected_ids.add(item.task_base_id)

        matrix_node = next((node for node in definition.nodes if node.type == "matrix"), None)
        lanes = matrix_node.config.get("lanes", {}) if matrix_node else {}
        if not isinstance(lanes, dict):
            lanes = {}
        repeat_n = _positive_int(matrix_node.config.get("repeat_n") if matrix_node else None, default=1)
        execute_node = next((node for node in definition.nodes if node.type == "execute"), None)
        execution_strategy = (
            str(execute_node.config.get("execution_strategy"))
            if execute_node and execute_node.config.get("execution_strategy")
            else "batch"
        )

        task_count = len(selected_tasks)
        task_instance_count = task_count * max_sample_n
        lane_keys = [str(key) for key in lanes.keys()]
        lane_count = len(lane_keys)
        total_episodes = task_instance_count * repeat_n * lane_count

        return WorkflowCompilePreview(
            task_count=task_count,
            task_instance_count=task_instance_count,
            trial_count=repeat_n,
            lane_count=lane_count,
            total_episodes=total_episodes,
            lane_keys=lane_keys,
            ordered_task_ids=[item.task_base_id for item in selected_tasks],
            execution_strategy=execution_strategy,
        )


def _validate_task_selection(
    node: WorkflowNode,
    node_index: int,
    catalog: TaskCatalogSnapshot,
    catalog_by_id: dict[str, TaskCatalogItem],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    task_ids = node.config.get("task_ids")
    if isinstance(task_ids, list):
        for task_index, task_id in enumerate(task_ids):
            if task_id not in catalog_by_id:
                issues.append(
                    WorkflowIssue(
                        code="WORKFLOW_TASK_MISSING",
                        message=f"Task '{task_id}' is not in the catalog snapshot.",
                        pointer=f"/nodes/{node_index}/config/task_ids/{task_index}",
                        node_id=node.id,
                    )
                )
        return issues

    if not _select_tasks(node.config, catalog):
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_TASK_SELECTION_EMPTY",
                message="Task selection did not match any catalog tasks.",
                pointer=f"/nodes/{node_index}/config",
                node_id=node.id,
            )
        )
    return issues


def _validate_matrix_targets(
    node: WorkflowNode,
    node_index: int,
    targets: dict[str, Any],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    lanes = node.config.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        return [
            WorkflowIssue(
                code="WORKFLOW_MATRIX_EMPTY",
                message="Matrix node requires at least one lane.",
                pointer=f"/nodes/{node_index}/config/lanes",
                node_id=node.id,
            )
        ]

    for lane_key, lane in lanes.items():
        target_id = lane.get("target_id") if isinstance(lane, dict) else None
        pointer = f"/nodes/{node_index}/config/lanes/{lane_key}/target_id"
        if not isinstance(target_id, str) or not target_id:
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_TARGET_MISSING",
                    message=f"Lane '{lane_key}' does not reference a target.",
                    pointer=pointer,
                    node_id=node.id,
                )
            )
            continue

        target = targets.get(target_id)
        if target is None:
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_TARGET_MISSING",
                    message=f"Target '{target_id}' was not found.",
                    pointer=pointer,
                    node_id=node.id,
                )
            )
            continue

        if not _target_field(target, "enabled", default=False):
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_TARGET_DISABLED",
                    message=f"Target '{target_id}' is disabled.",
                    pointer=pointer,
                    node_id=node.id,
                )
            )
        if _target_field(target, "kind", default=None) != "simulator":
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_TARGET_UNSUPPORTED",
                    message=f"Target '{target_id}' must be a simulator target.",
                    pointer=pointer,
                    node_id=node.id,
                )
            )

    return issues


_VALID_TARGET_CONSTRAINTS = {"same_app", "same_device", "same_data"}
_VALID_INITIAL_STATE_POLICIES = {"strict_snapshot", "task_projection"}
_VALID_EXECUTION_MODES = {"serial", "parallel"}
_VALID_GATE_THRESHOLDS = {
    "max_regressions",
    "max_candidate_errors",
    "min_success_rate",
    "max_success_rate_drop",
    "max_runtime_p95_increase",
    "max_unpaired",
}

_MANUAL_SEQUENCE_FILTER_KEYS = {
    "app",
    "apps",
    "capability",
    "capabilities",
    "composition",
    "compositions",
    "difficulties",
    "difficulty",
    "objective",
    "objectives",
    "scope",
    "scopes",
    "split",
    "splits",
    "suite",
    "suites",
    "taxonomy",
}


def _validate_manual_sequence(
    definition: WorkflowDefinition,
    node_indices: dict[str, int],
) -> list[WorkflowIssue]:
    all_execute_entries = [
        (index, node)
        for index, node in enumerate(definition.nodes)
        if node.type == "execute"
    ]
    execute_entries = [
        (index, node)
        for index, node in all_execute_entries
        if node.config.get("execution_strategy") == "linear_sequence"
    ]
    if not execute_entries:
        return []

    issues: list[WorkflowIssue] = []
    task_entries = [
        (index, node)
        for index, node in enumerate(definition.nodes)
        if node.type == "task_selection"
    ]
    matrix_entries = [
        (index, node)
        for index, node in enumerate(definition.nodes)
        if node.type == "matrix"
    ]

    if len(task_entries) != 1:
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence requires exactly one task_selection node.",
                pointer="/nodes",
            )
        )
    if len(matrix_entries) != 1:
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence requires exactly one matrix node.",
                pointer="/nodes",
            )
        )
    if len(execute_entries) != 1 or len(all_execute_entries) != 1:
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence requires exactly one execute node and it must use linear_sequence.",
                pointer="/nodes",
            )
        )

    if task_entries:
        task_index, task_node = task_entries[0]
        issues.extend(_validate_manual_sequence_tasks(task_node, task_index))
    if matrix_entries:
        matrix_index, matrix_node = matrix_entries[0]
        issues.extend(_validate_manual_sequence_matrix(matrix_node, matrix_index))
    if execute_entries:
        execute_index, execute_node = execute_entries[0]
        issues.extend(_validate_manual_sequence_execute(execute_node, execute_index))

    for node_index, node in enumerate(definition.nodes):
        if node.type == "compare":
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                    message="Manual sequence v1 does not support paired comparison.",
                    pointer=f"/nodes/{node_index}",
                    node_id=node.id,
                )
            )

    # Keep the graph shape linear for v1: tasks -> matrix -> execute. Gates and
    # reports may be appended later, but no manual sequence node should fan in
    # from multiple prerequisites or point at missing IDs beyond the base DAG
    # validator.
    for node_index, node in enumerate(definition.nodes):
        dependencies = [dep for dep in node.depends_on if dep in node_indices]
        if len(dependencies) > 1:
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                    message="Manual sequence v1 does not support branching or fan-in dependencies.",
                    pointer=f"/nodes/{node_index}/depends_on",
                    node_id=node.id,
                )
            )

    return issues


def _validate_manual_sequence_tasks(
    node: WorkflowNode,
    node_index: int,
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    config = node.config or {}
    task_ids = config.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids:
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence requires an explicit non-empty ordered task_ids list.",
                pointer=f"/nodes/{node_index}/config/task_ids",
                node_id=node.id,
            )
        )

    order_policy = config.get("order_policy")
    if order_policy is not None and order_policy != "manual":
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence order_policy must be 'manual'.",
                pointer=f"/nodes/{node_index}/config/order_policy",
                node_id=node.id,
            )
        )

    sample_n = config.get("sample_n")
    if sample_n is not None and not _is_config_one(sample_n):
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence v1 requires sample_n to be 1.",
                pointer=f"/nodes/{node_index}/config/sample_n",
                node_id=node.id,
            )
        )

    for key in sorted(_MANUAL_SEQUENCE_FILTER_KEYS):
        if key in config and _has_config_value(config.get(key)):
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                    message="Manual sequence v1 requires explicit task_ids and does not support task filters.",
                    pointer=f"/nodes/{node_index}/config/{key}",
                    node_id=node.id,
                )
            )
    return issues


def _validate_manual_sequence_matrix(
    node: WorkflowNode,
    node_index: int,
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    config = node.config or {}
    lanes = config.get("lanes")
    if not isinstance(lanes, dict) or len(lanes) != 1:
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence v1 requires exactly one candidate lane.",
                pointer=f"/nodes/{node_index}/config/lanes",
                node_id=node.id,
            )
        )
    elif lanes:
        lane_key, lane_config = next(iter(lanes.items()))
        lane_role = (
            lane_config.get("role")
            if isinstance(lane_config, dict)
            else None
        )
        effective_role = str(lane_role or lane_key)
        if effective_role != "candidate":
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                    message="Manual sequence v1 only supports the candidate lane role.",
                    pointer=f"/nodes/{node_index}/config/lanes/{lane_key}/role",
                    node_id=node.id,
                )
            )

    repeat_n = config.get("repeat_n")
    if repeat_n is not None and not _is_config_one(repeat_n):
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence v1 requires repeat_n to be 1.",
                pointer=f"/nodes/{node_index}/config/repeat_n",
                node_id=node.id,
            )
        )
    return issues


def _validate_manual_sequence_execute(
    node: WorkflowNode,
    node_index: int,
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    config = node.config or {}
    for key in ("parallel", "processes"):
        value = config.get(key)
        if value is not None and not _is_config_one(value):
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                    message=f"Manual sequence v1 requires {key} to be 1.",
                    pointer=f"/nodes/{node_index}/config/{key}",
                    node_id=node.id,
                )
            )

    state_policy = config.get("state_policy")
    if state_policy is not None and state_policy != "isolated":
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence v1 only supports state_policy 'isolated'.",
                pointer=f"/nodes/{node_index}/config/state_policy",
                node_id=node.id,
            )
        )

    failure_policy = config.get("failure_policy")
    if failure_policy is not None and failure_policy != "continue":
        issues.append(
            WorkflowIssue(
                code="WORKFLOW_MANUAL_SEQUENCE_INVALID_CONFIG",
                message="Manual sequence v1 only supports failure_policy 'continue'.",
                pointer=f"/nodes/{node_index}/config/failure_policy",
                node_id=node.id,
            )
        )
    return issues


def _has_config_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set, dict, str)):
        return len(value) > 0
    return True


def _is_config_one(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _validate_compare_config(node: WorkflowNode, node_index: int) -> list[WorkflowIssue]:
    """Validate the compare node's three-axis config (Contract 1, publish schema).

    This is STRUCTURAL validation only (valid values for known keys). It does
    NOT resolve target revisions — that happens at compile-preview (advisory)
    and create-run (authoritative gate).
    """
    issues: list[WorkflowIssue] = []
    config = node.config or {}

    # target_constraints: must be a list of known values.
    tc = config.get("target_constraints")
    if tc is not None:
        if not isinstance(tc, list):
            issues.append(WorkflowIssue(
                code="WORKFLOW_COMPARE_INVALID_CONFIG",
                message="compare.target_constraints must be a list.",
                pointer=f"/nodes/{node_index}/config/target_constraints",
                node_id=node.id,
            ))
        else:
            for i, c in enumerate(tc):
                if c not in _VALID_TARGET_CONSTRAINTS:
                    issues.append(WorkflowIssue(
                        code="WORKFLOW_COMPARE_INVALID_CONFIG",
                        message=f"Unknown target_constraint '{c}'. Valid: {sorted(_VALID_TARGET_CONSTRAINTS)}.",
                        pointer=f"/nodes/{node_index}/config/target_constraints/{i}",
                        node_id=node.id,
                    ))

    # initial_state_policy: must be a known value.
    isp = config.get("initial_state_policy")
    if isp is not None and isp not in _VALID_INITIAL_STATE_POLICIES:
        issues.append(WorkflowIssue(
            code="WORKFLOW_COMPARE_INVALID_CONFIG",
            message=f"Unknown initial_state_policy '{isp}'. Valid: {sorted(_VALID_INITIAL_STATE_POLICIES)}.",
            pointer=f"/nodes/{node_index}/config/initial_state_policy",
            node_id=node.id,
        ))

    # execution: must be a known value.
    exec_mode = config.get("execution")
    if exec_mode is not None and exec_mode not in _VALID_EXECUTION_MODES:
        issues.append(WorkflowIssue(
            code="WORKFLOW_COMPARE_INVALID_CONFIG",
            message=f"Unknown execution '{exec_mode}'. Valid: {sorted(_VALID_EXECUTION_MODES)}.",
            pointer=f"/nodes/{node_index}/config/execution",
            node_id=node.id,
        ))

    return issues


def _validate_gate_config(node: WorkflowNode, node_index: int) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    config = node.config or {}
    thresholds = config.get("thresholds") if isinstance(config.get("thresholds"), dict) else config
    if not isinstance(thresholds, dict):
        return [
            WorkflowIssue(
                code="WORKFLOW_GATE_INVALID_CONFIG",
                message="gate thresholds must be an object.",
                pointer=f"/nodes/{node_index}/config/thresholds",
                node_id=node.id,
            )
        ]

    prefix = (
        f"/nodes/{node_index}/config/thresholds"
        if isinstance(config.get("thresholds"), dict)
        else f"/nodes/{node_index}/config"
    )
    for key, value in thresholds.items():
        if key not in _VALID_GATE_THRESHOLDS:
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_GATE_INVALID_CONFIG",
                    message=f"Unknown gate threshold '{key}'. Valid: {sorted(_VALID_GATE_THRESHOLDS)}.",
                    pointer=f"{prefix}/{key}",
                    node_id=node.id,
                )
            )
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(
                WorkflowIssue(
                    code="WORKFLOW_GATE_INVALID_CONFIG",
                    message=f"Gate threshold '{key}' must be numeric.",
                    pointer=f"{prefix}/{key}",
                    node_id=node.id,
                )
            )
    return issues


def _select_tasks(config: dict[str, Any], catalog: TaskCatalogSnapshot) -> list[TaskCatalogItem]:
    task_ids = config.get("task_ids")
    if isinstance(task_ids, list) and task_ids:
        catalog_by_id = {item.task_base_id: item for item in catalog.items}
        selected: list[TaskCatalogItem] = []
        seen: set[str] = set()
        for task_id in task_ids:
            normalized = str(task_id)
            if normalized in seen:
                continue
            item = catalog_by_id.get(normalized)
            if item is not None:
                selected.append(item)
                seen.add(normalized)
        return selected

    suites = {str(suite) for suite in config.get("suites", []) if suite} if isinstance(config.get("suites"), list) else set()
    difficulties = (
        {str(difficulty) for difficulty in config.get("difficulty", []) if difficulty}
        if isinstance(config.get("difficulty"), list)
        else set()
    )
    return [
        item
        for item in catalog.items
        if (not suites or item.suite in suites)
        and (not difficulties or item.difficulty in difficulties)
    ]


def _has_cycle(nodes: list[WorkflowNode], node_indices: dict[str, int]) -> bool:
    graph: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        graph[node.id] = [dependency for dependency in node.depends_on if dependency in node_indices]

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for dependency in graph[node_id]:
            if visit(dependency):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node.id) for node in nodes)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _target_field(target: Any, field: str, *, default: Any) -> Any:
    if isinstance(target, dict):
        return target.get(field, default)
    return getattr(target, field, default)
