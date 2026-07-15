from fastapi.testclient import TestClient
import pytest

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _workflow_definition_v2():
    return {
        "schema_version": 2,
        "name": "Profile-aware Single",
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


def _profile_spec():
    return {
        "schema_version": 1,
        "agent": {"id": "generic_v2"},
        "model": {
            "protocol": "openai_chat_completions",
            "base_url": "http://127.0.0.1:1234/v1",
            "name": "deterministic-model",
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
    def __init__(self):
        self.build_id = "tp-ep02"

    def check_health(self, config):
        return {
            "healthy": True,
            "executable": True,
            "metadata": {
                "schemaVersion": 1,
                "simulator": {
                    "product": "mobile-gym",
                    "version": "0.1.0",
                    "buildId": self.build_id,
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


def _seed_launch(client):
    project = client.post(
        "/api/platform/v1/projects",
        json={"name": "Profile-aware launch"},
    ).json()
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "Profile-aware Single",
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
            "name": "Deterministic simulator",
            "config": _target_config(),
        },
    ).json()
    target_revision = client.post(
        f"/api/platform/v1/targets/{target['id']}/health"
    ).json()["revision"]
    profile = client.post(
        f"/api/platform/v1/projects/{project['id']}/execution-profiles",
        json={
            "name": "Deterministic generic v2",
            "draft_spec": _profile_spec(),
        },
    ).json()
    profile_revision = client.post(
        f"/api/platform/v1/projects/{project['id']}"
        f"/execution-profiles/{profile['id']}/publish"
    ).json()
    return (
        project,
        workflow_version,
        target,
        target_revision,
        profile,
        profile_revision,
    )


def _launch_command(workflow_version, target_revision, profile_revision):
    return {
        "workflow_version_id": workflow_version["id"],
        "name": "Deterministic Single",
        "seed": 20260715,
        "comparison_intent": "single",
        "lane_bindings": [
            {
                "lane_slot": "candidate",
                "target_revision_id": target_revision["id"],
                "execution_profile_revision_id": profile_revision["id"],
            }
        ],
    }


def test_workflow_v2_publishes_one_target_free_candidate_lane_slot(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Profile-aware launch"},
        ).json()
        created = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={
                "name": "Profile-aware Single",
                "definition": _workflow_definition_v2(),
            },
        )
        assert created.status_code == 201
        workflow = created.json()

        validation = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/validate"
        )
        preview = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/compile-preview"
        )
        published = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/publish"
        )

    assert validation.status_code == 200
    assert validation.json() == {"valid": True, "issues": []}
    assert preview.status_code == 200
    assert preview.json()["lane_keys"] == ["candidate"]
    assert preview.json()["lane_count"] == 1
    assert preview.json()["total_episodes"] == 1
    assert published.status_code == 200
    definition = published.json()["version"]["definition"]
    assert definition["schema_version"] == 2
    assert definition["nodes"][1]["config"]["lane_slots"] == {
        "candidate": {"role": "candidate"}
    }
    assert "target_id" not in published.text
    assert "execution_profile" not in published.text


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("bound_target", "WORKFLOW_LANE_SLOT_MUTABLE_RESOURCE_FORBIDDEN"),
        ("inline_model", "WORKFLOW_EXECUTION_SUBJECT_FORBIDDEN"),
    ],
)
def test_workflow_v2_rejects_bound_resources_and_inline_subject_settings(
    tmp_path,
    case,
    expected_code,
):
    app = create_app(_settings(tmp_path))
    definition = _workflow_definition_v2()
    if case == "bound_target":
        definition["nodes"][1]["config"]["lane_slots"]["candidate"][
            "target_id"
        ] = "mutable-target"
    else:
        definition["nodes"][2]["config"]["model_name"] = "inline-model"

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Invalid profile-aware workflow"},
        ).json()
        workflow = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={"name": "Invalid v2", "definition": definition},
        ).json()

        published = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/publish"
        )

    assert published.status_code == 400
    assert published.json()["error"]["code"] == "WORKFLOW_VALIDATION_FAILED"
    assert expected_code in {
        issue["code"] for issue in published.json()["error"]["details"]
    }


def test_run_launch_http_previews_creates_and_reloads_exact_identity(tmp_path):
    supervisor = RecordingSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
    )

    with TestClient(app) as client:
        (
            project,
            workflow_version,
            _target,
            target_revision,
            _profile,
            profile_revision,
        ) = _seed_launch(client)
        command = _launch_command(
            workflow_version,
            target_revision,
            profile_revision,
        )
        preview = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        )
        assert preview.status_code == 200

        created = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch",
            json={**command, "preview_token": preview.json()["preview_token"]},
            headers={"Idempotency-Key": "tp-ep02-http"},
        )
        assert created.status_code == 201
        reloaded = client.get(
            f"/api/platform/v1/runs/{created.json()['id']}"
        )

    assert reloaded.status_code == 200
    body = reloaded.json()
    assert body["run_plan"]["schema_version"] == 2
    assert body["workflow_version_id"] == workflow_version["id"]
    assert body["lanes"][0]["target_revision_id"] == target_revision["id"]
    assert body["lanes"][0]["execution_profile_revision_id"] == (
        profile_revision["id"]
    )
    assert body["execution_identity"]["kind"] == "profile_aware"
    identity = body["execution_identity"]["lane_bindings"][0]
    assert identity["target_revision_id"] == target_revision["id"]
    assert identity["execution_profile_revision_id"] == profile_revision["id"]
    assert identity["execution_profile_public_hash"] == (
        profile_revision["public_spec_hash"]
    )
    assert identity["lane_fingerprint"].startswith("sha256:")
    assert supervisor.submitted == [body["id"]]


@pytest.mark.parametrize(
    "case",
    ["missing_lane_slot", "latest_target", "latest_profile", "profile_draft"],
)
def test_run_launch_preview_rejects_non_exact_or_incomplete_bindings(
    tmp_path,
    case,
):
    app = create_app(_settings(tmp_path), adapter_registry=FakeRegistry())

    with TestClient(app) as client:
        (
            project,
            workflow_version,
            _target,
            target_revision,
            profile,
            profile_revision,
        ) = _seed_launch(client)
        command = _launch_command(
            workflow_version,
            target_revision,
            profile_revision,
        )
        if case == "missing_lane_slot":
            command["lane_bindings"] = []
        elif case == "latest_target":
            command["lane_bindings"][0]["target_revision_id"] = "latest"
        elif case == "latest_profile":
            command["lane_bindings"][0][
                "execution_profile_revision_id"
            ] = "latest"
        else:
            command["lane_bindings"][0][
                "execution_profile_revision_id"
            ] = profile["id"]

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={project['id']}"
        )

    assert response.status_code in {400, 404}
    expected_code = (
        "EXECUTION_PROFILE_REVISION_NOT_FOUND"
        if case == "profile_draft"
        else "RUN_LANE_BINDING_INCOMPLETE"
    )
    assert response.json()["error"]["code"] == expected_code
    assert runs.json()["items"] == []


def test_run_launch_preview_rejects_stale_target_revision_without_writes(tmp_path):
    registry = FakeRegistry()
    app = create_app(_settings(tmp_path), adapter_registry=registry)

    with TestClient(app) as client:
        (
            project,
            workflow_version,
            target,
            target_revision,
            _profile,
            profile_revision,
        ) = _seed_launch(client)
        registry.build_id = "tp-ep02-next"
        next_health = client.post(
            f"/api/platform/v1/targets/{target['id']}/health"
        )
        assert next_health.status_code == 200
        assert next_health.json()["revision"]["id"] != target_revision["id"]

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=_launch_command(
                workflow_version,
                target_revision,
                profile_revision,
            ),
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={project['id']}"
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_REVISION_STALE"
    assert runs.json()["items"] == []


def test_run_launch_preview_rejects_cross_project_revisions_without_writes(tmp_path):
    app = create_app(_settings(tmp_path), adapter_registry=FakeRegistry())

    with TestClient(app) as client:
        (
            project,
            workflow_version,
            _target,
            target_revision,
            _profile,
            profile_revision,
        ) = _seed_launch(client)
        other_project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Other project"},
        ).json()
        other_target = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": other_project["id"],
                "name": "Other simulator",
                "config": _target_config(),
            },
        ).json()
        other_target_revision = client.post(
            f"/api/platform/v1/targets/{other_target['id']}/health"
        ).json()["revision"]
        other_profile = client.post(
            f"/api/platform/v1/projects/{other_project['id']}/execution-profiles",
            json={
                "name": "Other profile",
                "draft_spec": _profile_spec(),
            },
        ).json()
        other_profile_revision = client.post(
            f"/api/platform/v1/projects/{other_project['id']}"
            f"/execution-profiles/{other_profile['id']}/publish"
        ).json()

        target_cross = _launch_command(
            workflow_version,
            other_target_revision,
            profile_revision,
        )
        profile_cross = _launch_command(
            workflow_version,
            target_revision,
            other_profile_revision,
        )
        responses = [
            client.post(
                f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
                json=command,
            )
            for command in (target_cross, profile_cross)
        ]
        runs = client.get(
            f"/api/platform/v1/runs?project_id={project['id']}"
        )

    assert [response.status_code for response in responses] == [409, 409]
    assert {
        response.json()["error"]["code"] for response in responses
    } == {"RUN_LANE_BINDING_CROSS_PROJECT"}
    assert runs.json()["items"] == []
