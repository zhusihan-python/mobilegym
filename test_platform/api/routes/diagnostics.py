from __future__ import annotations

from fastapi import APIRouter, Request

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import DiagnosticRepository


router = APIRouter(prefix="/api/platform/v1")


@router.get("/runs/{run_id}/diagnostics")
def get_run_diagnostics(request: Request, run_id: str) -> dict[str, object]:
    try:
        return DiagnosticRepository(get_database(request)).get_or_create(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


def _run_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
