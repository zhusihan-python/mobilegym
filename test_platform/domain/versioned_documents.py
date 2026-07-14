from __future__ import annotations

from typing import Any

from test_platform.domain.run_plans import RunPlan
from test_platform.domain.runs import LegacyExecutionIdentity, RunDomainError
from test_platform.domain.workflows import WorkflowDefinition, WorkflowDomainError


SUPPORTED_RUN_PLAN_SCHEMA_VERSIONS = (1,)
SUPPORTED_WORKFLOW_SCHEMA_VERSIONS = (1,)


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


def read_run_plan(payload: dict[str, Any]) -> RunPlan:
    schema_version = payload.get("schema_version", 1)
    if schema_version not in SUPPORTED_RUN_PLAN_SCHEMA_VERSIONS:
        raise UnsupportedRunPlanSchema(schema_version)
    return RunPlan.model_validate(payload)


def read_workflow_definition(payload: dict[str, Any]) -> WorkflowDefinition:
    schema_version = payload.get("schema_version", 1)
    if schema_version not in SUPPORTED_WORKFLOW_SCHEMA_VERSIONS:
        raise UnsupportedWorkflowSchema(schema_version)
    if "name" not in payload and "nodes" not in payload and "imported" in payload:
        return ImportedWorkflowDefinition.model_validate(payload)
    return WorkflowDefinition.model_validate(payload)


def execution_identity_for_run_plan(
    run_plan: RunPlan,
) -> LegacyExecutionIdentity:
    return LegacyExecutionIdentity(schema_version=run_plan.schema_version)
