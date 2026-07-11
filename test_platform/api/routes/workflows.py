from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.projects import ProjectNotFound
from test_platform.domain.task_catalog import build_task_catalog_snapshot
from test_platform.domain.workflows import (
    Workflow,
    WorkflowCompiler,
    WorkflowDefinition,
    WorkflowDomainError,
    WorkflowValidator,
    WorkflowVersion,
)
from test_platform.persistence.repositories import TargetRepository, WorkflowRepository

router = APIRouter(prefix="/api/platform/v1")


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    definition: WorkflowDefinition


class UpdateWorkflowDraftRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    definition: WorkflowDefinition


@router.post("/projects/{project_id}/workflows", status_code=201)
def create_workflow(
    request: Request,
    project_id: str,
    body: CreateWorkflowRequest,
) -> dict[str, object]:
    repository = WorkflowRepository(get_database(request))
    try:
        workflow = repository.create(
            project_id=project_id,
            name=body.name,
            definition=body.definition.model_dump(mode="json"),
        )
    except ProjectNotFound as exc:
        raise _project_not_found_error(project_id) from exc
    return _workflow_response(workflow, None)


@router.get("/projects/{project_id}/workflows")
def list_workflows(request: Request, project_id: str) -> dict[str, object]:
    repository = WorkflowRepository(get_database(request))
    try:
        workflows = repository.list(project_id=project_id)
    except ProjectNotFound as exc:
        raise _project_not_found_error(project_id) from exc
    return {
        "items": [_workflow_response(workflow, version) for workflow, version in workflows],
        "next_cursor": None,
    }


@router.patch("/workflows/{workflow_id}/draft")
def update_workflow_draft(
    request: Request,
    workflow_id: str,
    body: UpdateWorkflowDraftRequest,
) -> dict[str, object]:
    repository = WorkflowRepository(get_database(request))
    try:
        workflow = repository.update_draft(
            workflow_id=workflow_id,
            definition=body.definition.model_dump(mode="json"),
            name=body.name,
        )
    except WorkflowDomainError as exc:
        raise _workflow_error(exc) from exc
    return _workflow_response(workflow, repository.latest_version(workflow.id))


@router.post("/workflows/{workflow_id}/validate")
def validate_workflow(request: Request, workflow_id: str) -> dict[str, object]:
    repository = WorkflowRepository(get_database(request))
    try:
        workflow = repository.get(workflow_id)
    except WorkflowDomainError as exc:
        raise _workflow_error(exc) from exc

    definition = _workflow_definition(workflow)
    result = _validate_definition(request, workflow, definition)
    return result.model_dump(mode="json")


@router.post("/workflows/{workflow_id}/compile-preview")
def compile_workflow_preview(request: Request, workflow_id: str) -> dict[str, object]:
    repository = WorkflowRepository(get_database(request))
    try:
        workflow = repository.get(workflow_id)
    except WorkflowDomainError as exc:
        raise _workflow_error(exc) from exc

    definition = _workflow_definition(workflow)
    catalog = build_task_catalog_snapshot()
    preview = WorkflowCompiler().compile_preview(definition, catalog)
    # VS-10 Contract 3: advisory constraint validation. Resolve the latest
    # target revisions for the lane targets and evaluate the compare node's
    # target_constraints. The preview is ALWAYS a 200 (advisory) — violations
    # are surfaced in the success response, not as an error.
    preview.violations = _advisory_constraint_violations(
        request, workflow.project_id, definition
    )
    return preview.model_dump(mode="json")


@router.post("/workflows/{workflow_id}/publish")
def publish_workflow(request: Request, workflow_id: str) -> dict[str, object]:
    repository = WorkflowRepository(get_database(request))
    try:
        workflow = repository.get(workflow_id)
    except WorkflowDomainError as exc:
        raise _workflow_error(exc) from exc

    definition = _workflow_definition(workflow)
    validation = _validate_definition(request, workflow, definition)
    if not validation.valid:
        raise ApiError(
            "WORKFLOW_VALIDATION_FAILED",
            "Workflow draft is not valid.",
            status_code=400,
            details=[issue.model_dump(mode="json") for issue in validation.issues],
        )

    try:
        version = repository.publish(workflow_id)
    except WorkflowDomainError as exc:
        raise _workflow_error(exc) from exc

    return {
        "workflow_id": workflow.id,
        "workflow_version_id": version.id,
        "version": _workflow_version_response(version),
    }


@router.get("/workflow-versions/{workflow_version_id}")
def get_workflow_version(request: Request, workflow_version_id: str) -> dict[str, object]:
    repository = WorkflowRepository(get_database(request))
    try:
        version = repository.get_version(workflow_version_id)
    except WorkflowDomainError as exc:
        raise _workflow_error(exc) from exc
    return _workflow_version_response(version)


def _validate_definition(request: Request, workflow: Workflow, definition: WorkflowDefinition):
    catalog = build_task_catalog_snapshot()
    targets = {
        target.id: {"id": target.id, "kind": target.kind, "enabled": target.enabled}
        for target, _revision in TargetRepository(get_database(request)).list(project_id=workflow.project_id)
    }
    return WorkflowValidator().validate(definition, catalog, targets)


def _advisory_constraint_violations(
    request: Request,
    project_id: str,
    definition: WorkflowDefinition,
) -> list[dict[str, Any]]:
    """VS-10 Contract 3: evaluate the compare node's target_constraints against
    the latest target revisions for a paired (2-lane) workflow. Returns a list
    of violation dicts (empty when satisfied or not applicable). Advisory: never
    raises — unknown targets / missing revisions degrade to no violations so the
    preview stays a 200.
    """
    matrix_node = next(
        (node for node in definition.nodes if node.type == "matrix"), None
    )
    compare_node = next(
        (node for node in definition.nodes if node.type == "compare"), None
    )
    if matrix_node is None or compare_node is None:
        return []
    lanes = matrix_node.config.get("lanes")
    if not isinstance(lanes, dict) or len(lanes) < 2:
        return []
    constraints = compare_node.config.get("target_constraints")
    if not isinstance(constraints, list):
        constraints = None  # default to all axes

    # Resolve the latest revision for each lane target. Lanes are unordered
    # (a dict); order by lane_key so baseline/candidate are deterministic.
    repository = TargetRepository(get_database(request))
    ordered_keys = sorted(lanes)
    revisions: list[dict[str, Any] | None] = []
    target_ids: list[str] = []
    for lane_key in ordered_keys:
        lane = lanes[lane_key]
        target_id = lane.get("target_id") if isinstance(lane, dict) else None
        if not isinstance(target_id, str):
            return []  # malformed lane — leave to the validator
        target_ids.append(target_id)
        revision = repository.latest_revision(target_id)
        revisions.append(revision.metadata if revision is not None else None)

    from test_platform.domain.comparison_constraints import evaluate_target_constraints

    # Pairwise compare baseline vs candidate (the first two ordered lanes).
    violations = evaluate_target_constraints(
        baseline_metadata=revisions[0],
        candidate_metadata=revisions[1],
        constraints=constraints,
    )
    return [v.to_dict() for v in violations]


def _workflow_definition(workflow: Workflow) -> WorkflowDefinition:
    if workflow.draft_definition is None:
        raise ApiError(
            "WORKFLOW_DRAFT_MISSING",
            "Workflow does not have a draft definition.",
            status_code=400,
            details=[{"workflow_id": workflow.id}],
        )
    return WorkflowDefinition.model_validate(workflow.draft_definition)


def _workflow_response(
    workflow: Workflow,
    latest_version: WorkflowVersion | None,
) -> dict[str, object]:
    return {
        "id": workflow.id,
        "project_id": workflow.project_id,
        "name": workflow.name,
        "draft_definition": workflow.draft_definition,
        "latest_version": _workflow_version_response(latest_version) if latest_version else None,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
    }


def _workflow_version_response(version: WorkflowVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "workflow_id": version.workflow_id,
        "version_no": version.version_no,
        "status": version.status,
        "definition": version.definition,
        "definition_hash": version.definition_hash,
        "created_at": version.created_at,
        "published_at": version.published_at,
    }


def _project_not_found_error(project_id: str) -> ApiError:
    return ApiError(
        "PROJECT_NOT_FOUND",
        "Project was not found.",
        status_code=404,
        details=[{"project_id": project_id}],
    )


def _workflow_error(error: WorkflowDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
