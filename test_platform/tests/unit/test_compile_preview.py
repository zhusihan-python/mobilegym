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
    }
