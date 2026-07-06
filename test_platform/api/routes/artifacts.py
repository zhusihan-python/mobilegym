from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.artifacts.paths import ArtifactPathError
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import ArtifactRepository


router = APIRouter(prefix="/api/platform/v1")


@router.get("/runs/{run_id}/artifacts")
def list_run_artifacts(request: Request, run_id: str) -> dict[str, object]:
    try:
        return ArtifactRepository(get_database(request)).list_for_run(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.get("/runs/{run_id}/artifacts/{artifact_id}/content")
def get_artifact_content(request: Request, run_id: str, artifact_id: str) -> FileResponse:
    try:
        artifact, path = ArtifactRepository(get_database(request)).content_path(
            run_id,
            artifact_id,
        )
    except RunDomainError as exc:
        raise _run_error(exc) from exc
    except ArtifactPathError as exc:
        raise ApiError(
            "ARTIFACT_PATH_INVALID",
            "The artifact path is invalid or no longer available.",
            status_code=404,
            details=[{"run_id": run_id, "artifact_id": artifact_id}],
        ) from exc

    return FileResponse(path, media_type=artifact.get("media_type"))


def _run_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
