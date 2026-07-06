import pytest

from test_platform.domain.run_plans import RunPlanCompiler
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.domain.workflows import WorkflowDefinition


def _catalog():
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="git-vs04",
        digest="sha256:catalog",
        items=[
            TaskCatalogItem(
                task_base_id="wechat.OpenBlacklist",
                suite="wechat",
                class_name="OpenBlacklist",
                apps=["wechat"],
                templates=["Open blacklist"],
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
        ],
    )


def _definition(target_id="target-a"):
    return WorkflowDefinition.model_validate(
        {
            "schema_version": 1,
            "name": "WeChat smoke",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {
                        "task_ids": ["wechat.OpenBlacklist"],
                        "sample_n": 2,
                    },
                },
                {
                    "id": "matrix",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lanes": {
                            "candidate": {
                                "target_id": target_id,
                                "role": "candidate",
                            }
                        },
                        "repeat_n": 2,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["matrix"],
                    "config": {
                        "parallel": 1,
                        "agent": "fake-agent",
                        "model_name": "fake-model",
                        "model_api_key": "never-persist-this",
                    },
                },
            ],
        }
    )


def _definition_with_gate():
    payload = _definition().model_dump(mode="json")
    payload["nodes"].append(
        {
            "id": "quality",
            "type": "gate",
            "depends_on": ["execute"],
            "config": {
                "thresholds": {
                    "max_regressions": 0,
                    "min_success_rate": 0.9,
                }
            },
        }
    )
    return WorkflowDefinition.model_validate(payload)


def _targets():
    return {
        "target-a": {
            "target": {
                "id": "target-a",
                "config": {
                    "kind": "simulator",
                    "connection": {
                        "env_url": "http://127.0.0.1:5173",
                        "proxy_secret_ref": "secret://proxy",
                    },
                    "device_profile": {
                        "name": "Pixel 7",
                        "viewport_width": 393,
                        "viewport_height": 852,
                        "physical_width": 1080,
                        "physical_height": 2400,
                        "device_scale_factor": 2.75,
                    },
                    "runtime": {},
                },
            },
            "revision": {
                "id": "revision-a",
                "metadata_hash": "metadata-a",
                "metadata": {"data": {"revision": "seed-v1"}},
            },
        },
        "target-b": {
            "target": {
                "id": "target-b",
                "config": {
                    "kind": "simulator",
                    "connection": {"env_url": "http://127.0.0.1:5174"},
                    "device_profile": {
                        "name": "Pixel 8",
                        "viewport_width": 412,
                        "viewport_height": 915,
                        "physical_width": 1080,
                        "physical_height": 2400,
                        "device_scale_factor": 2.625,
                    },
                    "runtime": {},
                },
            },
            "revision": {
                "id": "revision-b",
                "metadata_hash": "metadata-b",
                "metadata": {"data": {"revision": "seed-v1"}},
            },
        },
    }


def _compile(*, seed=123, target_id="target-a"):
    return RunPlanCompiler().compile(
        run_id="run-random",
        workflow_version_id="workflow-version-1",
        definition=_definition(target_id),
        catalog=_catalog(),
        targets=_targets(),
        seed=seed,
        created_at="2026-07-03T12:00:00.000Z",
    )


def test_identical_inputs_produce_identical_plan_fingerprints_and_episode_identities():
    first = _compile()
    second = _compile()

    assert first.fingerprint == second.fingerprint
    assert [episode.episode_key for episode in first.episodes] == [
        episode.episode_key for episode in second.episodes
    ]
    assert len(first.episodes) == 4


def test_lane_or_seed_changes_alter_the_plan_fingerprint():
    original = _compile()

    assert _compile(seed=124).fingerprint != original.fingerprint
    assert _compile(target_id="target-b").fingerprint != original.fingerprint


def test_compiled_plan_contains_no_secret_values():
    plan = _compile()
    payload = plan.model_dump_json()

    assert "never-persist-this" not in payload
    assert "secret://proxy" not in payload
    assert plan.lanes[0].runner_config["model_api_key_configured"] is True
    assert plan.lanes[0].runner_config["proxy_configured"] is True


def test_compiler_rejects_catalog_drift_that_removes_a_published_task():
    catalog = _catalog()
    catalog.items = []

    with pytest.raises(ValueError, match="wechat.OpenBlacklist"):
        RunPlanCompiler().compile(
            run_id="run-random",
            workflow_version_id="workflow-version-1",
            definition=_definition(),
            catalog=catalog,
            targets=_targets(),
            seed=123,
            created_at="2026-07-03T12:00:00.000Z",
        )


def test_compiler_freezes_gate_thresholds_into_run_plan():
    plan = RunPlanCompiler().compile(
        run_id="run-random",
        workflow_version_id="workflow-version-1",
        definition=_definition_with_gate(),
        catalog=_catalog(),
        targets=_targets(),
        seed=123,
        created_at="2026-07-03T12:00:00.000Z",
    )

    assert plan.gates == {
        "max_regressions": 0,
        "min_success_rate": 0.9,
    }


def test_legacy_run_plan_payload_defaults_gates_to_empty_dict():
    plan = _compile()
    payload = plan.model_dump(mode="json")
    payload.pop("gates")

    reparsed = type(plan).model_validate(payload)

    assert reparsed.gates == {}
