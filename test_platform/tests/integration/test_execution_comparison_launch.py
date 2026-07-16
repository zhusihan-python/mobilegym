import time

from fastapi.testclient import TestClient
import pytest

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.testing.deterministic import (
    build_deterministic_executor_resolver,
    build_deterministic_target_registry,
)
from test_platform.testing.fake_compat import FakeCompatibilityProbe


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _workflow_definition_v2():
    return {
        "schema_version": 2,
        "name": "Execution Comparison",
        "nodes": [
            {
                "id": "tasks",
                "type": "task_selection",
                "depends_on": [],
                "config": {
                    "task_ids": ["account.Railway12306ChangePassword"],
                    "sample_n": 1,
                },
            },
            {
                "id": "slots",
                "type": "matrix",
                "depends_on": ["tasks"],
                "config": {
                    "lane_slots": {
                        "baseline": {"role": "baseline"},
                        "candidate": {"role": "candidate"},
                    },
                    "repeat_n": 1,
                },
            },
            {
                "id": "execute",
                "type": "execute",
                "depends_on": ["slots"],
                "config": {
                    "parallel": 1,
                    "processes": 1,
                    "eval_mode": "grounded",
                    "judge_mode": "rule",
                },
            },
            {
                "id": "compare",
                "type": "compare",
                "depends_on": ["execute"],
                "config": {
                    "target_constraints": [
                        "same_app",
                        "same_device",
                        "same_data",
                    ],
                    "initial_state_policy": "task_projection",
                    "execution": "serial",
                },
            },
        ],
    }


def _target_config():
    return {
        "kind": "simulator",
        "connection": {"env_url": "http://127.0.0.1:5173"},
        "device_profile": {
            "name": "Pixel 7",
            "viewport_width": 393,
            "viewport_height": 852,
            "physical_width": 1080,
            "physical_height": 2400,
            "device_scale_factor": 2.75,
        },
        "runtime": {},
    }


def _profile_spec(model_name: str):
    return {
        "schema_version": 1,
        "agent": {"id": "generic_v2"},
        "model": {
            "protocol": "openai_chat_completions",
            "base_url": "http://127.0.0.1:1234/v1",
            "name": model_name,
        },
        "image_input": {"format": "data_url"},
        "generation": {
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 4096,
            "stream": True,
        },
        "inference": {"timeout_seconds": 300},
        "credentials": {"required_slots": []},
    }


class FakeRegistry:
    def check_health(self, _config):
        return {
            "healthy": True,
            "executable": True,
            "metadata": {
                "schemaVersion": 1,
                "simulator": {
                    "product": "mobile-gym",
                    "version": "0.1.0",
                    "buildId": "tp-ep06",
                },
                "apps": [
                    {
                        "id": "fake",
                        "packageName": "com.example.fake",
                        "displayName": "Fake",
                        "displayNameEn": "Fake",
                        "version": "1.0.0",
                        "versionCode": 1,
                        "type": "plugin",
                    }
                ],
                "data": {"revision": "seed-v1"},
                "capabilities": ["sim.metadata.v1"],
            },
            "warnings": [],
            "error": None,
        }


class RecordingSupervisor:
    def __init__(self):
        self.submitted: list[str] = []

    def submit(self, run_id: str) -> None:
        self.submitted.append(run_id)


def _seed_execution_comparison(
    client,
    *,
    profile_model_names=("deterministic-model-a", "deterministic-model-b"),
):
    project = client.post(
        "/api/platform/v1/projects",
        json={"name": "Execution Comparison"},
    ).json()
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "Execution Comparison",
            "definition": _workflow_definition_v2(),
        },
    ).json()
    workflow_version = client.post(
        f"/api/platform/v1/workflows/{workflow['id']}/publish"
    ).json()["version"]
    target = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": "Shared simulator",
            "config": _target_config(),
        },
    ).json()
    target_revision = client.post(
        f"/api/platform/v1/targets/{target['id']}/health"
    ).json()["revision"]

    profile_revisions = []
    for label, model_name in zip(
        ("Baseline subject", "Candidate subject"),
        profile_model_names,
        strict=True,
    ):
        profile = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": label, "draft_spec": _profile_spec(model_name)},
        ).json()
        profile_revisions.append(
            client.post(
                f"/api/platform/v1/projects/{project['id']}"
                f"/execution-profiles/{profile['id']}/publish"
            ).json()
        )

    command = {
        "workflow_version_id": workflow_version["id"],
        "name": "Deterministic Execution Comparison",
        "seed": 20260717,
        "comparison_intent": "execution_comparison",
        "lane_bindings": [
            {
                "lane_slot": lane_slot,
                "target_revision_id": target_revision["id"],
                "execution_profile_revision_id": profile_revision["id"],
            }
            for lane_slot, profile_revision in zip(
                ("baseline", "candidate"),
                profile_revisions,
                strict=True,
            )
        ],
    }
    return project, target_revision, profile_revisions, command


def _wait_for_run_state(client, run_id: str, expected: str, *, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/platform/v1/runs/{run_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["state"] == expected:
            return latest
        if latest["state"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    raise AssertionError(
        f"Run {run_id} did not reach {expected}; latest state was "
        f"{latest['state'] if latest else 'unavailable'}."
    )


def test_execution_comparison_launch_freezes_one_target_and_two_profiles(tmp_path):
    supervisor = RecordingSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project, target_revision, profile_revisions, command = (
            _seed_execution_comparison(client)
        )
        preview = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        )
        assert preview.status_code == 200
        preview_body = preview.json()

        created = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch",
            json={**command, "preview_token": preview_body["preview_token"]},
            headers={"Idempotency-Key": "tp-ep06-execution-comparison"},
        )
        assert created.status_code == 201
        reloaded = client.get(
            f"/api/platform/v1/runs/{created.json()['id']}"
        ).json()

    assert preview_body["comparison_intent"] == "execution_comparison"
    assert preview_body["constraint_violations"] == []
    assert {
        binding["target_revision_id"]
        for binding in preview_body["lane_bindings"]
    } == {target_revision["id"]}
    assert {
        binding["execution_profile_revision_id"]
        for binding in preview_body["lane_bindings"]
    } == {revision["id"] for revision in profile_revisions}
    assert preview_body["execution_profile_diff"] == {
        "from_revision_id": profile_revisions[0]["id"],
        "to_revision_id": profile_revisions[1]["id"],
        "changes": [
            {
                "path": "model.name",
                "before": "deterministic-model-a",
                "after": "deterministic-model-b",
            }
        ],
    }
    assert reloaded["run_plan"]["comparison"] == {
        "intent": "execution_comparison",
        "initial_state_policy": "task_projection",
        "execution": "serial",
    }
    assert len(reloaded["run_plan"]["episodes"]) == 1
    assert len(reloaded["episode_identities"]) == 1
    assert {
        lane["target_revision_id"] for lane in reloaded["run_plan"]["lanes"]
    } == {target_revision["id"]}
    assert {
        lane["execution_profile_revision_id"]
        for lane in reloaded["run_plan"]["lanes"]
    } == {revision["id"] for revision in profile_revisions}
    assert {
        binding["execution_profile_revision_id"]
        for binding in reloaded["execution_identity"]["lane_bindings"]
    } == {revision["id"] for revision in profile_revisions}
    assert len(reloaded["run_attempts"][0]["compatibility"]) == 2
    assert supervisor.submitted == [reloaded["id"]]


def test_execution_comparison_deterministic_outcome_uses_frozen_profile_snapshot(
    tmp_path,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    app = create_app(
        settings,
        database=database,
        adapter_registry=build_deterministic_target_registry(),
        executor_resolver=build_deterministic_executor_resolver(
            database,
            settings,
            enabled=True,
        ),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project, target_revision, profile_revisions, command = (
            _seed_execution_comparison(
                client,
                profile_model_names=(
                    "deterministic-profile-pass",
                    "deterministic-profile-fail",
                ),
            )
        )
        preview = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        )
        assert preview.status_code == 200
        created = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch",
            json={**command, "preview_token": preview.json()["preview_token"]},
            headers={"Idempotency-Key": "tp-ep06-deterministic-profile-outcome"},
        )
        assert created.status_code == 201
        run = _wait_for_run_state(client, created.json()["id"], "completed")
        comparison = client.get(
            f"/api/platform/v1/runs/{run['id']}/comparison"
        )

    assert comparison.status_code == 200
    assert comparison.json()["pairs"][0]["classification"] == "regression"
    assert {
        binding["target_revision_id"]
        for binding in run["execution_identity"]["lane_bindings"]
    } == {target_revision["id"]}
    assert {
        binding["execution_profile_revision_id"]
        for binding in run["execution_identity"]["lane_bindings"]
    } == {revision["id"] for revision in profile_revisions}


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("no_variation", "RUN_COMPARISON_NO_VARIATION"),
        ("confounded", "RUN_COMPARISON_CONFOUNDED"),
    ],
)
def test_execution_comparison_rejects_invalid_axes_without_durable_writes(
    tmp_path,
    case,
    expected_code,
):
    settings = _settings(tmp_path)
    supervisor = RecordingSupervisor()
    app = create_app(
        settings,
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project, _target_revision, _profile_revisions, command = (
            _seed_execution_comparison(client)
        )
        if case == "no_variation":
            command["lane_bindings"][1]["execution_profile_revision_id"] = (
                command["lane_bindings"][0]["execution_profile_revision_id"]
            )
        else:
            second_target = client.post(
                "/api/platform/v1/targets",
                json={
                    "project_id": project["id"],
                    "name": "Confounding simulator",
                    "config": _target_config(),
                },
            ).json()
            second_revision = client.post(
                f"/api/platform/v1/targets/{second_target['id']}/health"
            ).json()["revision"]
            command["lane_bindings"][1]["target_revision_id"] = second_revision["id"]

        rejected = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch",
            json={**command, "preview_token": "invalid-request-never-previewed"},
            headers={"Idempotency-Key": f"tp-ep06-{case}"},
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={project['id']}"
        ).json()["items"]

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == expected_code
    assert runs == []
    assert supervisor.submitted == []
    assert list(settings.runs_dir.iterdir()) == []
