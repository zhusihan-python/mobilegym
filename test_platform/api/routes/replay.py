from __future__ import annotations

from fastapi import APIRouter, Request

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import ReplayRepository


router = APIRouter(prefix="/api/platform/v1")


@router.get("/runs/{run_id}/episodes/{episode_key}/replay")
def get_episode_replay(
    request: Request,
    run_id: str,
    episode_key: str,
    lane_key: str | None = None,
    attempt_no: str = "latest",
) -> dict[str, object]:
    """Return the VS-15 ``EpisodeReplay`` DTO for one episode attempt.

    Loads ``trajectory.json`` from the attempt's ``artifact_root`` and maps each
    step's screenshot/prompt/response references to registered artifact ids so
    the frontend never has to infer filesystem layout.
    """
    try:
        return ReplayRepository(get_database(request)).get_episode_replay(
            run_id,
            episode_key,
            lane_key=lane_key,
            attempt_no=attempt_no,
        )
    except RunDomainError as exc:
        raise _run_error(exc) from exc


def _run_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
