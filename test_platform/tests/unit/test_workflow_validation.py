from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.domain.workflows import WorkflowDefinition, WorkflowValidator


def _catalog():
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="test-rev",
        digest="sha256:test",
        items=[
            TaskCatalogItem(
                task_base_id="wechat.BlacklistContact",
                suite="wechat",
                class_name="BlacklistContact",
                apps=["wechat"],
                templates=["Blacklist a contact"],
                parameters={},
                difficulty="L3",
                scope="S1",
                objective="operate",
                composition="sequential",
                capabilities=["nav", "settings"],
                max_steps=None,
                answer_fields=False,
                optimal_path_lengths=[],
            )
        ],
    )


def _target(enabled=True):
    return {
        "id": "target-1",
        "kind": "simulator",
        "enabled": enabled,
    }


def test_cycles_and_missing_dependencies_return_json_pointer_errors():
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": 1,
            "name": "Broken workflow",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": ["execute"],
                    "config": {"task_ids": ["wechat.BlacklistContact"]},
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix", "missing"],
                    "config": {"parallel": 1},
                },
                {
                    "id": "matrix",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lanes": {"candidate": {"target_id": "target-1"}},
                        "repeat_n": 1,
                    },
                },
            ],
        }
    )

    result = WorkflowValidator().validate(definition, _catalog(), {"target-1": _target()})

    assert result.valid is False
    assert {
        (issue.code, issue.pointer, issue.node_id)
        for issue in result.issues
    } >= {
        ("WORKFLOW_DEPENDENCY_MISSING", "/nodes/1/depends_on/1", "execute"),
        ("WORKFLOW_CYCLE", "/nodes", None),
    }


def test_disabled_or_missing_targets_invalidate_publication():
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": 1,
            "name": "Target workflow",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {"task_ids": ["wechat.BlacklistContact"]},
                },
                {
                    "id": "matrix",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lanes": {
                            "disabled": {"target_id": "target-1"},
                            "missing": {"target_id": "target-missing"},
                        },
                        "repeat_n": 1,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {"parallel": 1},
                },
            ],
        }
    )

    result = WorkflowValidator().validate(definition, _catalog(), {"target-1": _target(enabled=False)})

    assert result.valid is False
    assert {
        issue.code for issue in result.issues
    } >= {"WORKFLOW_TARGET_DISABLED", "WORKFLOW_TARGET_MISSING"}


def test_gate_node_validation_rejects_unknown_or_non_numeric_thresholds():
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": 1,
            "name": "Gate workflow",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {"task_ids": ["wechat.BlacklistContact"]},
                },
                {
                    "id": "matrix",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lanes": {"candidate": {"target_id": "target-1"}},
                        "repeat_n": 1,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {"parallel": 1},
                },
                {
                    "id": "quality",
                    "type": "gate",
                    "depends_on": ["execute"],
                    "config": {
                        "thresholds": {
                            "max_regressions": 0,
                            "unknown_metric": 1,
                            "min_success_rate": "high",
                        }
                    },
                },
            ],
        }
    )

    result = WorkflowValidator().validate(definition, _catalog(), {"target-1": _target()})

    assert result.valid is False
    assert [
        (issue.code, issue.pointer, issue.node_id)
        for issue in result.issues
        if issue.code == "WORKFLOW_GATE_INVALID_CONFIG"
    ] == [
        ("WORKFLOW_GATE_INVALID_CONFIG", "/nodes/3/config/thresholds/unknown_metric", "quality"),
        ("WORKFLOW_GATE_INVALID_CONFIG", "/nodes/3/config/thresholds/min_success_rate", "quality"),
    ]
