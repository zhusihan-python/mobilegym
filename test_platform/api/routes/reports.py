from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.reports.export import export_report_html, export_report_json
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import BaselineRepository, ReportRepository
from test_platform.services.baselines import BaselineEligibility


router = APIRouter(prefix="/api/platform/v1")


class PromoteBaselineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    lane_key: str | None = None


@router.get("/runs/{run_id}/report")
def get_run_report(request: Request, run_id: str) -> dict[str, object]:
    try:
        return ReportRepository(get_database(request)).get_or_repair(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.get("/runs/{run_id}/report/export")
def export_run_report(
    request: Request,
    run_id: str,
    format: str = "json",  # noqa: A002 - API query parameter name
) -> Response:
    try:
        report = ReportRepository(get_database(request)).get_or_repair(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc

    if format == "json":
        return Response(
            export_report_json(
                report,
                secret_values=_configured_secret_values(request.app.state.settings),
            ),
            media_type="application/json",
        )
    if format == "html":
        return Response(
            export_report_html(
                report,
                secret_values=_configured_secret_values(request.app.state.settings),
            ),
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
    body: PromoteBaselineRequest,
) -> dict[str, object]:
    try:
        return BaselineRepository(get_database(request)).promote(
            run_id,
            display_name=body.display_name,
            lane_key=body.lane_key,
        )
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.get("/reports/{report_id}")
def get_report(request: Request, report_id: str) -> dict[str, object]:
    try:
        return ReportRepository(get_database(request)).get_by_id(report_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.get("/projects/{project_id}/baselines")
def list_project_baselines(
    request: Request,
    project_id: str,
    include_archived: bool = False,
) -> dict[str, object]:
    items = BaselineRepository(get_database(request)).list_for_project(
        project_id,
        include_archived=include_archived,
    )
    return {"items": items, "next_cursor": None}


@router.get("/baselines/{baseline_id}")
def get_baseline(request: Request, baseline_id: str) -> dict[str, object]:
    try:
        return BaselineRepository(get_database(request)).get(baseline_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.post("/baselines/{baseline_id}/archive")
def archive_baseline(request: Request, baseline_id: str) -> dict[str, object]:
    try:
        return BaselineRepository(get_database(request)).archive(baseline_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.get("/runs/{run_id}/baseline/eligibility")
def get_baseline_eligibility(
    request: Request,
    run_id: str,
    lane_key: str | None = None,
) -> dict[str, object]:
    try:
        return BaselineEligibility(get_database(request)).evaluate(
            run_id,
            lane_key=lane_key,
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


def _configured_secret_values(settings) -> list[str]:
    auth_token = getattr(settings, "auth_token", None)
    if auth_token is None:
        return []
    get_secret_value = getattr(auth_token, "get_secret_value", None)
    if callable(get_secret_value):
        value = get_secret_value()
        return [value] if value else []
    value = str(auth_token)
    return [value] if value else []
