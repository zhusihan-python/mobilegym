from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from test_platform.domain.runs import RunDomainError


class RunLaunchError(RunDomainError):
    pass


@dataclass(frozen=True)
class LaneBindingInput:
    lane_slot: str
    target_revision_id: str
    execution_profile_revision_id: str


@dataclass(frozen=True)
class PreviewRunLaunch:
    project_id: str
    workflow_version_id: str
    name: str | None
    seed: int
    comparison_intent: Literal["single"]
    lane_bindings: tuple[LaneBindingInput, ...]


@dataclass(frozen=True)
class CreateRunLaunch:
    project_id: str
    workflow_version_id: str
    name: str | None
    seed: int
    comparison_intent: Literal["single"]
    lane_bindings: tuple[LaneBindingInput, ...]


class ResolvedLaneBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    lane_slot: str
    role: str
    target_id: str
    target_revision_id: str
    target_revision_hash: str
    execution_profile_id: str
    execution_profile_name: str
    execution_profile_revision_id: str
    execution_profile_revision_no: int
    execution_profile_public_hash: str
    execution_profile_revision_hash: str
    lane_fingerprint: str


class RunLaunchPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_version_id: str
    workflow_version_hash: str
    comparison_intent: Literal["single"]
    lane_bindings: list[ResolvedLaneBinding]
    episode_count: int
    fingerprint_inputs: dict[str, Any]
    run_plan_fingerprint: str
    preview_token: str
    credential_requirements: list[str]
