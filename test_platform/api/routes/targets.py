from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from test_platform.adapters.targets import RealDeviceAdapter, TargetAdapterRegistry
from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.projects import ProjectNotFound
from test_platform.domain.targets import (
    DuplicateTargetName,
    Target,
    TargetDomainError,
    TargetRevision,
    build_target_revision_metadata,
    redact_target_config,
    utc_timestamp,
)
from test_platform.persistence.repositories import TargetRepository

router = APIRouter(prefix="/api/platform/v1")


class CreateTargetRequest(BaseModel):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, Any]


@router.get("/targets")
def list_targets(request: Request, project_id: str) -> dict[str, object]:
    repository = TargetRepository(get_database(request))
    return {
        "items": [
            _target_response(target, revision)
            for target, revision in repository.list(project_id=project_id)
        ],
        "next_cursor": None,
    }


@router.post("/targets", status_code=201)
def create_target(request: Request, body: CreateTargetRequest) -> dict[str, object]:
    repository = TargetRepository(get_database(request))
    try:
        target = repository.create(
            project_id=body.project_id,
            name=body.name,
            config=body.config,
        )
    except ProjectNotFound as exc:
        raise _api_error(exc) from exc
    except (DuplicateTargetName, TargetDomainError) as exc:
        raise _api_error(exc) from exc
    return _target_response(target, None)


@router.get("/targets/{target_id}")
def get_target(request: Request, target_id: str) -> dict[str, object]:
    repository = TargetRepository(get_database(request))
    try:
        target = repository.get(target_id)
    except TargetDomainError as exc:
        raise _api_error(exc) from exc
    return _target_response(target, repository.latest_revision(target.id))


@router.post("/targets/{target_id}/health")
def check_target_health(request: Request, target_id: str) -> dict[str, object]:
    repository = TargetRepository(get_database(request))
    try:
        target = repository.get(target_id)
    except TargetDomainError as exc:
        raise _api_error(exc) from exc

    if target.kind == "real_device":
        return _health_response(RealDeviceAdapter().check_health(target.config), None)

    registry = getattr(request.app.state, "adapter_registry", None) or TargetAdapterRegistry()
    try:
        result = registry.check_health(target.config)
    except TargetDomainError as exc:
        raise _api_error(exc) from exc

    if result.get("error"):
        return _health_response(result, None)

    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise _api_error(TargetDomainError("TARGET_METADATA_INVALID", "Target metadata was missing."))

    try:
        resolved_at = utc_timestamp()
        revision_metadata = build_target_revision_metadata(
            config=target.config,
            sim_metadata=metadata,
            resolved_at=resolved_at,
        )
        revision = repository.record_revision(
            target_id=target.id,
            metadata=revision_metadata,
            warnings=list(result.get("warnings") or []),
            health_status="healthy" if result.get("healthy") else "unhealthy",
        )
    except TargetDomainError as exc:
        raise _api_error(exc) from exc

    return _health_response(result, revision)


def _target_response(target: Target, revision: TargetRevision | None) -> dict[str, object]:
    return {
        "id": target.id,
        "project_id": target.project_id,
        "name": target.name,
        "kind": target.kind,
        "enabled": target.enabled,
        "config": redact_target_config(target.config),
        "latest_revision": _revision_response(revision) if revision else None,
        "created_at": target.created_at,
        "updated_at": target.updated_at,
    }


def _revision_response(revision: TargetRevision | None) -> dict[str, object] | None:
    if revision is None:
        return None
    return {
        "id": revision.id,
        "metadata_hash": revision.metadata_hash,
        "health_status": revision.health_status,
        "resolved_at": revision.resolved_at,
        "warnings": revision.warnings,
        "metadata": revision.metadata,
    }


def _health_response(result: dict[str, Any], revision: TargetRevision | None) -> dict[str, object]:
    return {
        "healthy": bool(result.get("healthy")),
        "executable": bool(result.get("executable")),
        "revision": _revision_response(revision),
        "warnings": list(result.get("warnings") or []),
        "error": result.get("error"),
    }


def _api_error(error: TargetDomainError | ProjectNotFound) -> ApiError:
    if isinstance(error, ProjectNotFound):
        return ApiError(
            "PROJECT_NOT_FOUND",
            "Project was not found.",
            status_code=404,
        )
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
