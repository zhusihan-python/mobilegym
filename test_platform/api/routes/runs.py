from dataclasses import asdict

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.runs import RunDomainError, RunIdempotencyConflict
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


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    request: Request,
    run_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    """Request cooperative cancellation of a run.

    The DB write (cancel_requested_at) is committed synchronously before this
    returns, so the flag is durable by the time the HTTP response is sent. The
    token signal and event publish happen inline. Repeated cancels are idempotent:
    they return the current run state without error. Cancelling a terminal run
    (completed/failed/cancelled) is a no-op that returns the current state.

    When an `Idempotency-Key` is provided, the same key+run_id replays the first
    response verbatim (stored in idempotency_keys.response_json). A key reused
    with a different run returns 409.
    """
    database = get_database(request)
    supervisor = request.app.state.supervisor

    # Validate the run exists (404 if not).
    try:
        RunRepository(database).get(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc

    # request_hash is scoped to the run so a key cannot be replayed across runs.
    from test_platform.domain.canonical_json import canonical_sha256

    request_hash = canonical_sha256({"route": "cancel", "run_id": run_id})

    try:
        if hasattr(supervisor, "request_cancel"):
            result = supervisor.request_cancel(
                run_id, idempotency_key=idempotency_key, request_hash=request_hash
            )
        else:  # pragma: no cover — FakeRunSupervisor fallback
            result = False
    except RunIdempotencyConflict as exc:
        raise _run_error(
            RunDomainError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
                details=exc.details,
            )
        ) from exc

    if isinstance(result, dict):
        return result

    # Non-idempotent path: build the response from the current state.
    detail = RunRepository(database).get(run_id)
    return {
        "run_id": run_id,
        "cancel_requested": bool(result),
        "state": detail.state,
    }


def _run_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
