from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.domain.workflows import WorkflowDefinition, WorkflowCompiler


def test_compile_preview_counts_tasks_trials_lanes_and_total_episodes():
    catalog = TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="test-rev",
        digest="sha256:test",
        items=[
            TaskCatalogItem(
                task_base_id=f"wechat.Task{i}",
                suite="wechat",
                class_name=f"Task{i}",
                apps=["wechat"],
                templates=[f"Task {i}"],
                parameters={},
                difficulty="L1",
                scope="S1",
                objective="operate",
                composition="atomic",
                capabilities=[],
                max_steps=15,
                answer_fields=False,
                optimal_path_lengths=[],
            )
            for i in range(2)
        ],
    )
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": 1,
            "name": "Preview workflow",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {"task_ids": ["wechat.Task0", "wechat.Task1"], "sample_n": 1},
                },
                {
                    "id": "matrix",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lanes": {
                            "baseline": {"target_id": "target-a"},
                            "candidate": {"target_id": "target-b"},
                        },
                        "repeat_n": 3,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {"parallel": 2},
                },
            ],
        }
    )

    preview = WorkflowCompiler().compile_preview(definition, catalog)

    assert preview.model_dump() == {
        "task_count": 2,
        "task_instance_count": 2,
        "trial_count": 3,
        "lane_count": 2,
        "total_episodes": 12,
        "lane_keys": ["baseline", "candidate"],
        "ordered_task_ids": ["wechat.Task0", "wechat.Task1"],
        "execution_strategy": "batch",
        # VS-10: violations defaults to [] at the domain layer; the
        # compile-preview ROUTE populates it after resolving revisions.
        "violations": [],
    }


def test_manual_sequence_compile_preview_preserves_explicit_task_order():
    catalog = TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="test-rev",
        digest="sha256:test",
        items=[
            TaskCatalogItem(
                task_base_id=f"wechat.Task{i}",
                suite="wechat",
                class_name=f"Task{i}",
                apps=["wechat"],
                templates=[f"Task {i}"],
                parameters={},
                difficulty="L1",
                scope="S1",
                objective="operate",
                composition="atomic",
                capabilities=[],
                max_steps=15,
                answer_fields=False,
                optimal_path_lengths=[],
            )
            for i in range(3)
        ],
    )
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": 1,
            "name": "Manual sequence preview",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {
                        "task_ids": ["wechat.Task2", "wechat.Task0", "wechat.Task1"],
                        "order_policy": "manual",
                        "sample_n": 1,
                    },
                },
                {
                    "id": "matrix",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lanes": {"candidate": {"target_id": "target-a"}},
                        "repeat_n": 1,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {
                        "execution_strategy": "linear_sequence",
                        "state_policy": "isolated",
                        "failure_policy": "continue",
                        "parallel": 1,
                        "processes": 1,
                    },
                },
            ],
        }
    )

    preview = WorkflowCompiler().compile_preview(definition, catalog)

    assert preview.ordered_task_ids == ["wechat.Task2", "wechat.Task0", "wechat.Task1"]
    assert preview.execution_strategy == "linear_sequence"
    assert preview.total_episodes == 3
