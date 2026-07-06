from dataclasses import asdict
import json

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, Field

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.runs import RunDomainError, RunIdempotencyConflict
from test_platform.persistence.repositories import (
    ComparisonRepository,
    RunRepository,
)
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


@router.get("/runs/{run_id}/comparison")
def get_run_comparison(request: Request, run_id: str) -> dict[str, object]:
    """Return the comparison (pairs + classifications) for a paired run.

    VS-09 Contract 8: each pair carries a ``prepared`` DTO with only the
    params/instruction/projection_hash (NOT the full prepared_tasks payload_json).
    Returns 404 if the run has no comparison yet.
    """
    database = get_database(request)
    # Validate the run exists (404 if not).
    try:
        RunRepository(database).get(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc

    comparison = ComparisonRepository(database).get_comparison(run_id)
    if comparison is None:
        raise ApiError(
            "COMPARISON_NOT_FOUND",
            "No comparison has been recorded for this run yet.",
            status_code=404,
            details=[{"run_id": run_id}],
        )
    # Attach a prepared DTO to each pair (Contract 8: subset of payload_json).
    prepared_by_key = _prepared_dtos_by_materialization_key(database, run_id)
    for pair in comparison["pairs"]:
        # pair_key == episode_key == materialization_key|t{trial}; resolve the
        # materialization_key from the episodes table for this pair_key.
        mat_key = _materialization_key_for_pair(database, run_id, pair["pair_key"])
        if mat_key is not None and mat_key in prepared_by_key:
            pair["prepared"] = prepared_by_key[mat_key]
        else:
            pair["prepared"] = None
    return comparison


def _prepared_dtos_by_materialization_key(database, run_id: str) -> dict[str, dict]:
    """Map materialization_key → prepared DTO (params/instruction/projection_hash)."""
    rows = database.connection.execute(
        "SELECT materialization_key, payload_json FROM prepared_tasks WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    dtos: dict[str, dict] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        dtos[str(row["materialization_key"])] = {
            "params": payload.get("params", {}),
            "instruction": payload.get("instruction"),
            "projection_hash": payload.get("projection_hash"),
        }
    return dtos


def _materialization_key_for_pair(database, run_id: str, pair_key: str) -> str | None:
    """Resolve the materialization_key for an episode/pair_key."""
    row = database.connection.execute(
        "SELECT materialization_key FROM episodes WHERE run_id = ? AND pair_key = ? "
        "LIMIT 1",
        (run_id, pair_key),
    ).fetchone()
    return str(row["materialization_key"]) if row else None


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


@router.post("/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_run(request: Request, run_id: str) -> dict[str, object]:
    try:
        return RunService(
            get_database(request),
            request.app.state.settings,
            supervisor=request.app.state.supervisor,
        ).retry_run(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


@router.post("/runs/{run_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_run(request: Request, run_id: str) -> dict[str, object]:
    try:
        return RunService(
            get_database(request),
            request.app.state.settings,
            supervisor=request.app.state.supervisor,
        ).resume_run(run_id)
    except RunDomainError as exc:
        raise _run_error(exc) from exc


def _run_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
