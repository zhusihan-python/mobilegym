from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.execution_profiles import (
    CredentialReferenceBindingInput,
    ExecutionProfileDomainError,
    PublishProfile,
    SaveProfileDraft,
    UpdateProfileDraft,
)
from test_platform.domain.projects import ProjectNotFound
from test_platform.services.execution_profiles import ExecutionProfiles

router = APIRouter(prefix="/api/platform/v1")


class CredentialReferenceBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    slot: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    backend: Literal["request"]
    reference_id: str = Field(min_length=1)
    private_locator: str = Field(min_length=1)


class CreateExecutionProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    draft_spec: dict[str, Any]
    credential_bindings: list[CredentialReferenceBindingRequest] = Field(
        default_factory=list
    )


class UpdateExecutionProfileDraftRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    draft_spec: dict[str, Any]
    credential_bindings: list[CredentialReferenceBindingRequest] | None = None


@router.get("/projects/{project_id}/execution-profiles")
def list_execution_profiles(
    request: Request,
    project_id: str,
) -> dict[str, object]:
    try:
        items = ExecutionProfiles(get_database(request)).list(project_id=project_id)
    except ProjectNotFound as exc:
        raise _project_not_found_error(project_id) from exc
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "next_cursor": None,
    }


@router.post(
    "/projects/{project_id}/execution-profiles",
    status_code=201,
)
def create_execution_profile(
    request: Request,
    project_id: str,
    body: CreateExecutionProfileRequest,
) -> dict[str, object]:
    try:
        view = ExecutionProfiles(get_database(request)).save_draft(
            SaveProfileDraft(
                project_id=project_id,
                name=body.name,
                draft_spec=body.draft_spec,
                credential_bindings=tuple(
                    CredentialReferenceBindingInput(**binding.model_dump())
                    for binding in body.credential_bindings
                ),
            )
        )
    except ProjectNotFound as exc:
        raise _project_not_found_error(project_id) from exc
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return view.model_dump(mode="json")


@router.get(
    "/projects/{project_id}/execution-profiles/{execution_profile_id}"
)
def get_execution_profile(
    request: Request,
    project_id: str,
    execution_profile_id: str,
) -> dict[str, object]:
    try:
        view = ExecutionProfiles(get_database(request)).get(
            project_id=project_id,
            execution_profile_id=execution_profile_id,
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return view.model_dump(mode="json")


@router.patch(
    "/projects/{project_id}/execution-profiles/{execution_profile_id}/draft"
)
def update_execution_profile_draft(
    request: Request,
    project_id: str,
    execution_profile_id: str,
    body: UpdateExecutionProfileDraftRequest,
) -> dict[str, object]:
    try:
        view = ExecutionProfiles(get_database(request)).update_draft(
            UpdateProfileDraft(
                project_id=project_id,
                execution_profile_id=execution_profile_id,
                name=body.name,
                draft_spec=body.draft_spec,
                credential_bindings=(
                    None
                    if body.credential_bindings is None
                    else tuple(
                        CredentialReferenceBindingInput(**binding.model_dump())
                        for binding in body.credential_bindings
                    )
                ),
            )
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return view.model_dump(mode="json")


@router.post(
    "/projects/{project_id}/execution-profiles/{execution_profile_id}/publish"
)
def publish_execution_profile(
    request: Request,
    project_id: str,
    execution_profile_id: str,
) -> dict[str, object]:
    try:
        view = ExecutionProfiles(get_database(request)).publish(
            PublishProfile(
                project_id=project_id,
                execution_profile_id=execution_profile_id,
            )
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return view.model_dump(mode="json")


@router.get(
    "/projects/{project_id}/execution-profile-revisions/{revision_id}"
)
def get_execution_profile_revision(
    request: Request,
    project_id: str,
    revision_id: str,
) -> dict[str, object]:
    try:
        view = ExecutionProfiles(get_database(request)).get_revision(
            project_id=project_id,
            revision_id=revision_id,
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return view.model_dump(mode="json")


def _execution_profile_error(error: ExecutionProfileDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )


def _project_not_found_error(project_id: str) -> ApiError:
    return ApiError(
        "PROJECT_NOT_FOUND",
        "Project was not found.",
        status_code=404,
        details=[{"project_id": project_id}],
    )
