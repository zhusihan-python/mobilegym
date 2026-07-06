from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class RunDomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


class RunNotFound(RunDomainError):
    def __init__(self, run_id: str) -> None:
        super().__init__(
            "RUN_NOT_FOUND",
            "Run was not found.",
            status_code=404,
            details=[{"run_id": run_id}],
        )


class RunIdempotencyConflict(RunDomainError):
    def __init__(self, key: str) -> None:
        super().__init__(
            "IDEMPOTENCY_KEY_CONFLICT",
            "The idempotency key was already used with a different request.",
            status_code=409,
            details=[{"idempotency_key": key}],
        )


@dataclass(frozen=True)
class RunSummary:
    id: str
    project_id: str
    workflow_version_id: str
    name: str | None
    state: str
    fingerprint: str
    progress: dict[str, int]
    lanes: list[dict[str, Any]]
    gate_verdict: str | None
    created_at: str
    started_at: str | None
    ended_at: str | None


@dataclass(frozen=True)
class RunDetail(RunSummary):
    run_plan: dict[str, Any]
    run_attempts: list[dict[str, Any]]
    lane_attempts: list[dict[str, Any]]
    target_revisions: list[dict[str, str]]
    episode_identities: list[dict[str, Any]]
    episode_attempts: list[dict[str, Any]]
