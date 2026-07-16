from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.execution_profiles import (
    ArchiveProfile,
    CloneProfileRevision,
    CredentialReferenceBindingInput,
    ExecutionProfileDomainError,
    ExecutionProfileView,
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
    expected_draft_version: int | None = Field(default=None, ge=1)


class ExecutionProfileMutationIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_draft_version: int | None = Field(default=None, ge=1)
    expected_head_revision_id: str | None = None


class PublishExecutionProfileRequest(ExecutionProfileMutationIdentityRequest):
    pass


class CloneExecutionProfileRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=100)


class ArchiveExecutionProfileRequest(ExecutionProfileMutationIdentityRequest):
    pass


def _resolve_mutation_identity(
    body: ExecutionProfileMutationIdentityRequest | None,
    current: ExecutionProfileView,
) -> tuple[int, str | None]:
    expected_draft_version = (
        body.expected_draft_version
        if body is not None and body.expected_draft_version is not None
        else current.draft_version
    )
    expected_head_revision_id = (
        body.expected_head_revision_id
        if body is not None
        else current.head_revision.id
        if current.head_revision is not None
        else None
    )
    return expected_draft_version, expected_head_revision_id


@router.get("/projects/{project_id}/execution-profiles")
def list_execution_profiles(
    request: Request,
    project_id: str,
    include_archived: bool = False,
) -> dict[str, object]:
    try:
        items = ExecutionProfiles(get_database(request)).list(
            project_id=project_id,
            include_archived=include_archived,
        )
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
        profiles = ExecutionProfiles(get_database(request))
        current = profiles.get(
            project_id=project_id,
            execution_profile_id=execution_profile_id,
        )
        view = profiles.update_draft(
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
                expected_draft_version=(
                    body.expected_draft_version
                    if body.expected_draft_version is not None
                    else current.draft_version
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
    body: PublishExecutionProfileRequest | None = None,
) -> dict[str, object]:
    try:
        profiles = ExecutionProfiles(get_database(request))
        current = profiles.get(
            project_id=project_id,
            execution_profile_id=execution_profile_id,
        )
        expected_draft_version, expected_head_revision_id = (
            _resolve_mutation_identity(body, current)
        )
        view = profiles.publish(
            PublishProfile(
                project_id=project_id,
                execution_profile_id=execution_profile_id,
                expected_draft_version=expected_draft_version,
                expected_head_revision_id=expected_head_revision_id,
            )
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return view.model_dump(mode="json")


@router.get(
    "/projects/{project_id}/execution-profiles/{execution_profile_id}/revisions"
)
def list_execution_profile_revisions(
    request: Request,
    project_id: str,
    execution_profile_id: str,
) -> dict[str, object]:
    try:
        items = ExecutionProfiles(get_database(request)).list_revisions(
            project_id=project_id,
            execution_profile_id=execution_profile_id,
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "next_cursor": None,
    }


@router.get("/projects/{project_id}/execution-profile-revision-diff")
def diff_execution_profile_revisions(
    request: Request,
    project_id: str,
    from_revision_id: str,
    to_revision_id: str,
) -> dict[str, object]:
    try:
        result = ExecutionProfiles(get_database(request)).diff_revisions(
            project_id=project_id,
            from_revision_id=from_revision_id,
            to_revision_id=to_revision_id,
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return result.model_dump(mode="json")


@router.post(
    "/projects/{project_id}/execution-profile-revisions/{revision_id}/clone",
    status_code=201,
)
def clone_execution_profile_revision(
    request: Request,
    project_id: str,
    revision_id: str,
    body: CloneExecutionProfileRevisionRequest,
) -> dict[str, object]:
    try:
        result = ExecutionProfiles(get_database(request)).clone(
            CloneProfileRevision(
                project_id=project_id,
                revision_id=revision_id,
                name=body.name,
            )
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return result.model_dump(mode="json")


@router.post(
    "/projects/{project_id}/execution-profiles/{execution_profile_id}/archive"
)
def archive_execution_profile(
    request: Request,
    project_id: str,
    execution_profile_id: str,
    body: ArchiveExecutionProfileRequest | None = None,
) -> dict[str, object]:
    try:
        profiles = ExecutionProfiles(get_database(request))
        current = profiles.get(
            project_id=project_id,
            execution_profile_id=execution_profile_id,
        )
        expected_draft_version, expected_head_revision_id = (
            _resolve_mutation_identity(body, current)
        )
        result = profiles.archive(
            ArchiveProfile(
                project_id=project_id,
                execution_profile_id=execution_profile_id,
                expected_draft_version=expected_draft_version,
                expected_head_revision_id=expected_head_revision_id,
            )
        )
    except ExecutionProfileDomainError as exc:
        raise _execution_profile_error(exc) from exc
    return result.model_dump(mode="json")


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
