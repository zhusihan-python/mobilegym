from __future__ import annotations

from fastapi import APIRouter, Query, Request

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import DiagnosticRepository


router = APIRouter(prefix="/api/platform/v1")


@router.get("/runs/{run_id}/diagnostics")
def get_run_diagnostics(
    request: Request,
    run_id: str,
    category: str | None = None,
    severity: str | None = None,
    target_id: str | None = None,
    app_id: str | None = None,
    task_id: str | None = None,
    retryable: bool | None = None,
    lane_key: str | None = None,
    episode_key: str | None = None,
    attempt_no: int | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    try:
        return DiagnosticRepository(get_database(request)).get_or_create(
            run_id,
            category=category,
            severity=severity,
            target_id=target_id,
            app_id=app_id,
            task_id=task_id,
            retryable=retryable,
            lane_key=lane_key,
            episode_key=episode_key,
            attempt_no=attempt_no,
            cursor=cursor,
            limit=limit,
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
