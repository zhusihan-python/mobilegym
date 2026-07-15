from __future__ import annotations

from typing import Any

from test_platform.domain.run_plans import RunPlan, RunPlanV2
from test_platform.domain.runs import (
    LegacyExecutionIdentity,
    ProfileAwareExecutionIdentity,
    ProfileAwareLaneIdentity,
    RunDomainError,
)
from test_platform.domain.workflows import (
    WorkflowDefinition,
    WorkflowDefinitionV2,
    WorkflowDomainError,
)


SUPPORTED_RUN_PLAN_SCHEMA_VERSIONS = (1, 2)
SUPPORTED_WORKFLOW_SCHEMA_VERSIONS = (1, 2)


class UnsupportedRunPlanSchema(RunDomainError):
    def __init__(self, schema_version: object) -> None:
        super().__init__(
            "RUN_PLAN_SCHEMA_UNSUPPORTED",
            "Run Plan schema version is not supported.",
            status_code=409,
            details=[
                {
                    "schema_version": schema_version,
                    "supported_schema_versions": list(
                        SUPPORTED_RUN_PLAN_SCHEMA_VERSIONS
                    ),
                }
            ],
        )


class UnsupportedWorkflowSchema(WorkflowDomainError):
    def __init__(self, schema_version: object) -> None:
        super().__init__(
            "WORKFLOW_SCHEMA_UNSUPPORTED",
            "Workflow schema version is not supported.",
            status_code=409,
            details=[
                {
                    "schema_version": schema_version,
                    "supported_schema_versions": list(
                        SUPPORTED_WORKFLOW_SCHEMA_VERSIONS
                    ),
                }
            ],
        )


class ImportedWorkflowDefinition(WorkflowDefinition):
    name: str = "Imported legacy workflow"
    imported: dict[str, Any]


def read_run_plan(payload: dict[str, Any]) -> RunPlan | RunPlanV2:
    schema_version = payload.get("schema_version", 1)
    if schema_version not in SUPPORTED_RUN_PLAN_SCHEMA_VERSIONS:
        raise UnsupportedRunPlanSchema(schema_version)
    if schema_version == 2:
        return RunPlanV2.model_validate(payload)
    return RunPlan.model_validate(payload)


def read_workflow_definition(
    payload: dict[str, Any],
) -> WorkflowDefinition | WorkflowDefinitionV2:
    schema_version = payload.get("schema_version", 1)
    if schema_version not in SUPPORTED_WORKFLOW_SCHEMA_VERSIONS:
        raise UnsupportedWorkflowSchema(schema_version)
    if "name" not in payload and "nodes" not in payload and "imported" in payload:
        return ImportedWorkflowDefinition.model_validate(payload)
    if schema_version == 2:
        return WorkflowDefinitionV2.model_validate(payload)
    return WorkflowDefinition.model_validate(payload)


def execution_identity_for_run_plan(
    run_plan: RunPlan | RunPlanV2,
) -> LegacyExecutionIdentity | ProfileAwareExecutionIdentity:
    if isinstance(run_plan, RunPlanV2):
        lane_bindings = []
        for lane in run_plan.lanes:
            snapshot = run_plan.execution_snapshots[lane.execution_snapshot_key]
            lane_bindings.append(
                ProfileAwareLaneIdentity(
                    lane_slot=lane.lane_key,
                    target_revision_id=lane.target_revision_id,
                    target_revision_hash=lane.target_revision_hash,
                    execution_profile_id=snapshot.execution_profile_id,
                    execution_profile_name=snapshot.execution_profile_name,
                    execution_profile_revision_id=(
                        lane.execution_profile_revision_id
                    ),
                    execution_profile_revision_no=(
                        snapshot.execution_profile_revision_no
                    ),
                    execution_profile_public_hash=snapshot.public_spec_hash,
                    execution_profile_revision_hash=(
                        lane.execution_profile_revision_hash
                    ),
                    lane_fingerprint=lane.fingerprint,
                )
            )
        return ProfileAwareExecutionIdentity(lane_bindings=lane_bindings)
    return LegacyExecutionIdentity(schema_version=run_plan.schema_version)
