from dataclasses import asdict

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import RunRepository
from test_platform.services.runs import RunService

router = APIRouter(prefix="/api/platform/v1")


class RunOverrides(BaseModel):
    seed: int = 0


class CreateRunRequest(BaseModel):
    workflow_version_id: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=100)
    overrides: RunOverrides = Field(default_factory=RunOverrides)


@router.post("/runs", status_code=201)
def create_run(
    request: Request,
    body: CreateRunRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
) -> dict[str, object]:
    try:
        run = RunService(
            get_database(request),
            request.app.state.settings,
            supervisor=request.app.state.supervisor,
        ).create_run(
            workflow_version_id=body.workflow_version_id,
            name=body.name,
            seed=body.overrides.seed,
            idempotency_key=idempotency_key,
        )
    except RunDomainError as exc:
        raise _run_error(exc) from exc
    return asdict(run)


@router.get("/runs")
def list_runs(request: Request, project_id: str | None = None) -> dict[str, object]:
    database = get_database(request)
    readiness = database.readiness()
    if not readiness["ready"]:
        raise ApiError(
            "SERVICE_NOT_READY",
            "The Test Platform service is not ready.",
            status_code=503,
            details=[
                {"name": name, **check}
                for name, check in readiness["checks"].items()
                if not check["ready"]
            ],
        )

    return {
        "items": [asdict(run) for run in RunRepository(database).list(project_id=project_id)],
        "next_cursor": None,
    }


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str) -> dict[str, object]:
    try:
        return asdict(RunRepository(get_database(request)).get(run_id))
    except RunDomainError as exc:
        raise _run_error(exc) from exc


def _run_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
