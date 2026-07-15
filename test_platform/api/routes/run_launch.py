from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.run_launch import (
    CreateRunLaunch,
    LaneBindingInput,
    PreviewRunLaunch,
)
from test_platform.domain.runs import RunDomainError
from test_platform.services.run_launch import RunLaunch


router = APIRouter(prefix="/api/platform/v1")


class LaneBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    lane_slot: str = Field(min_length=1)
    target_revision_id: str = Field(min_length=1)
    execution_profile_revision_id: str = Field(min_length=1)


class PreviewRunLaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_version_id: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=100)
    seed: int
    comparison_intent: Literal["single"]
    lane_bindings: list[LaneBindingRequest]


class CreateRunLaunchRequest(PreviewRunLaunchRequest):
    preview_token: str = Field(min_length=1)
    secret_bindings: dict[str, str] = Field(default_factory=dict)


@router.post("/projects/{project_id}/run-launch/preview")
def preview_run_launch(
    request: Request,
    project_id: str,
    body: PreviewRunLaunchRequest,
) -> dict[str, object]:
    try:
        preview = RunLaunch(get_database(request)).preview(
            _preview_command(project_id, body)
        )
    except RunDomainError as exc:
        raise _run_launch_error(exc) from exc
    return preview.model_dump(mode="json")


@router.post(
    "/projects/{project_id}/run-launch",
    status_code=status.HTTP_201_CREATED,
)
def create_run_launch(
    request: Request,
    project_id: str,
    body: CreateRunLaunchRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
) -> dict[str, object]:
    preview_command = _preview_command(project_id, body)
    try:
        run = RunLaunch(
            get_database(request),
            settings=request.app.state.settings,
            supervisor=request.app.state.supervisor,
            secret_resolver=request.app.state.secret_resolver,
            compatibility_preflight=request.app.state.compatibility_preflight,
        ).create(
            CreateRunLaunch(**preview_command.__dict__),
            expected_preview_token=body.preview_token,
            secret_bindings=body.secret_bindings,
            idempotency_key=idempotency_key,
        )
    except RunDomainError as exc:
        raise _run_launch_error(exc) from exc
    return asdict(run)


def _preview_command(
    project_id: str,
    body: PreviewRunLaunchRequest,
) -> PreviewRunLaunch:
    return PreviewRunLaunch(
        project_id=project_id,
        workflow_version_id=body.workflow_version_id,
        name=body.name,
        seed=body.seed,
        comparison_intent=body.comparison_intent,
        lane_bindings=tuple(
            LaneBindingInput(
                lane_slot=binding.lane_slot,
                target_revision_id=binding.target_revision_id,
                execution_profile_revision_id=(
                    binding.execution_profile_revision_id
                ),
            )
            for binding in body.lane_bindings
        ),
    )


def _run_launch_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
