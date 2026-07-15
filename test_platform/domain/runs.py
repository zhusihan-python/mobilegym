from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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
class LegacyExecutionIdentity:
    kind: Literal["legacy"] = "legacy"
    label: str = "Legacy Execution Identity"
    schema_version: Literal[1] = 1


@dataclass(frozen=True)
class ProfileAwareLaneIdentity:
    lane_slot: str
    target_revision_id: str
    target_revision_hash: str
    execution_profile_id: str
    execution_profile_name: str
    execution_profile_revision_id: str
    execution_profile_revision_no: int
    execution_profile_public_hash: str
    execution_profile_revision_hash: str
    lane_fingerprint: str


@dataclass(frozen=True)
class ProfileAwareExecutionIdentity:
    lane_bindings: list[ProfileAwareLaneIdentity]
    kind: Literal["profile_aware"] = "profile_aware"
    label: str = "Execution Profile Revision"
    schema_version: Literal[2] = 2


@dataclass(frozen=True)
class RunSummary:
    id: str
    project_id: str
    workflow_version_id: str
    name: str | None
    state: str
    fingerprint: str
    progress: dict[str, int]
    outcome_counts: dict[str, int]
    lanes: list[dict[str, Any]]
    gate_verdict: str | None
    created_at: str
    started_at: str | None
    ended_at: str | None
    imported: dict[str, Any] | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class RunDetail(RunSummary):
    run_plan: dict[str, Any]
    execution_identity: LegacyExecutionIdentity | ProfileAwareExecutionIdentity
    run_attempts: list[dict[str, Any]]
    lane_attempts: list[dict[str, Any]]
    target_revisions: list[dict[str, str]]
    episode_identities: list[dict[str, Any]]
    episode_attempts: list[dict[str, Any]]
