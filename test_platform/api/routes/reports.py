from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.reports.export import export_report_html, export_report_json
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import BaselineRepository, ReportRepository


router = APIRouter(prefix="/api/platform/v1")


class PromoteBaselineRequest(BaseModel):
    lane_key: str | None = None


@router.get("/runs/{run_id}/report")
def get_run_report(request: Request, run_id: str) -> dict[str, object]:
    try:
        return ReportRepository(get_database(request)).get_or_create(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.get("/runs/{run_id}/report/export")
def export_run_report(
    request: Request,
    run_id: str,
    format: str = "json",  # noqa: A002 - API query parameter name
) -> Response:
    try:
        report = ReportRepository(get_database(request)).get_or_create(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc

    if format == "json":
        return Response(
            export_report_json(report),
            media_type="application/json",
        )
    if format == "html":
        return Response(
            export_report_html(report),
            media_type="text/html",
        )
    raise ApiError(
        "REPORT_EXPORT_FORMAT_UNSUPPORTED",
        "Report export format is not supported.",
        status_code=400,
        details=[{"format": format}],
    )


@router.post("/runs/{run_id}/baseline", status_code=status.HTTP_201_CREATED)
def promote_run_baseline(
    request: Request,
    run_id: str,
    body: PromoteBaselineRequest | None = None,
) -> dict[str, object]:
    try:
        return BaselineRepository(get_database(request)).promote(
            run_id,
            lane_key=body.lane_key if body is not None else None,
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
