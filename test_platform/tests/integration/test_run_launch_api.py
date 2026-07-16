from fastapi.testclient import TestClient
import pytest

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.testing.fake_compat import FakeCompatibilityProbe


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


def _target_comparison_definition_v2():
    definition = _workflow_definition_v2()
    definition["name"] = "Profile-aware Target Comparison"
    definition["nodes"][1]["config"]["lane_slots"] = {
        "baseline": {"role": "baseline"},
        "candidate": {"role": "candidate"},
    }
    definition["nodes"].append(
        {
            "id": "compare",
            "type": "compare",
            "depends_on": ["execute"],
            "config": {
                "target_constraints": ["same_app", "same_device", "same_data"],
                "initial_state_policy": "task_projection",
                "execution": "serial",
            },
        }
    )
    return definition


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
    def __init__(self, *, candidate_version_code=1):
        self.build_id = "tp-ep02"
        self.candidate_version_code = candidate_version_code

    def check_health(self, config):
        is_candidate = str(config["connection"]["env_url"]).endswith(":5174")
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
                        "versionCode": (
                            self.candidate_version_code if is_candidate else 1
                        ),
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


def _seed_target_comparison(client):
    project = client.post(
        "/api/platform/v1/projects",
        json={"name": "Profile-aware Target Comparison"},
    ).json()
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "Profile-aware Target Comparison",
            "definition": _target_comparison_definition_v2(),
        },
    ).json()
    workflow_version = client.post(
        f"/api/platform/v1/workflows/{workflow['id']}/publish"
    ).json()["version"]
    target_revisions = []
    for name, port in (("Baseline simulator", 5173), ("Candidate simulator", 5174)):
        config = _target_config()
        config["connection"]["env_url"] = f"http://127.0.0.1:{port}"
        target = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": name,
                "config": config,
            },
        ).json()
        target_revisions.append(
            client.post(
                f"/api/platform/v1/targets/{target['id']}/health"
            ).json()["revision"]
        )
    profile = client.post(
        f"/api/platform/v1/projects/{project['id']}/execution-profiles",
        json={
            "name": "Shared deterministic subject",
            "draft_spec": _profile_spec(),
        },
    ).json()
    profile_revision = client.post(
        f"/api/platform/v1/projects/{project['id']}"
        f"/execution-profiles/{profile['id']}/publish"
    ).json()
    return project, workflow_version, target_revisions, profile_revision


def _target_comparison_command(workflow_version, target_revisions, profile_revision):
    return {
        "workflow_version_id": workflow_version["id"],
        "name": "Deterministic Target Comparison",
        "seed": 20260716,
        "comparison_intent": "target_comparison",
        "lane_bindings": [
            {
                "lane_slot": lane_slot,
                "target_revision_id": target_revision["id"],
                "execution_profile_revision_id": profile_revision["id"],
            }
            for lane_slot, target_revision in zip(
                ("baseline", "candidate"),
                target_revisions,
                strict=True,
            )
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


def test_workflow_v2_publishes_target_free_paired_lane_slots(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Profile-aware Target Comparison"},
        ).json()
        workflow = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={
                "name": "Profile-aware Target Comparison",
                "definition": _target_comparison_definition_v2(),
            },
        ).json()

        validation = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/validate"
        )
        published = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/publish"
        )

    assert validation.json() == {"valid": True, "issues": []}
    assert published.status_code == 200
    definition = published.json()["version"]["definition"]
    assert definition["nodes"][1]["config"]["lane_slots"] == {
        "baseline": {"role": "baseline"},
        "candidate": {"role": "candidate"},
    }
    assert "target_id" not in published.text
    assert "execution_profile_revision_id" not in published.text


def test_workflow_v2_paired_lane_slots_require_comparison_policy(tmp_path):
    app = create_app(_settings(tmp_path))
    definition = _target_comparison_definition_v2()
    definition["nodes"] = [
        node for node in definition["nodes"] if node["type"] != "compare"
    ]

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Incomplete Target Comparison"},
        ).json()
        workflow = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={"name": "Incomplete Target Comparison", "definition": definition},
        ).json()
        published = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/publish"
        )

    assert published.status_code == 400
    assert published.json()["error"]["code"] == "WORKFLOW_VALIDATION_FAILED"
    assert {
        issue["code"] for issue in published.json()["error"]["details"]
    } == {"WORKFLOW_COMPARE_REQUIRED"}


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
        compatibility_probe=FakeCompatibilityProbe(),
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


def test_target_comparison_launch_freezes_two_targets_and_one_profile(tmp_path):
    supervisor = RecordingSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project, workflow_version, target_revisions, profile_revision = (
            _seed_target_comparison(client)
        )
        command = _target_comparison_command(
            workflow_version,
            target_revisions,
            profile_revision,
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
            headers={"Idempotency-Key": "tp-ep05-target-comparison"},
        )
        assert created.status_code == 201
        reloaded = client.get(
            f"/api/platform/v1/runs/{created.json()['id']}"
        ).json()

    assert preview_body["comparison_intent"] == "target_comparison"
    assert preview_body["constraint_violations"] == []
    assert [binding["lane_slot"] for binding in preview_body["lane_bindings"]] == [
        "baseline",
        "candidate",
    ]
    assert len(reloaded["run_plan"]["episodes"]) == 1
    assert reloaded["run_plan"]["comparison"] == {
        "intent": "target_comparison",
        "target_constraints": ["same_app", "same_device", "same_data"],
        "initial_state_policy": "task_projection",
        "execution": "serial",
    }
    assert {
        lane["target_revision_id"] for lane in reloaded["run_plan"]["lanes"]
    } == {revision["id"] for revision in target_revisions}
    assert {
        lane["execution_profile_revision_id"]
        for lane in reloaded["run_plan"]["lanes"]
    } == {profile_revision["id"]}
    compatibility = reloaded["run_attempts"][0]["compatibility"]
    assert len(compatibility) == 1
    assert compatibility[0]["lane_keys"] == ["baseline", "candidate"]
    assert supervisor.submitted == [reloaded["id"]]


def test_target_comparison_constraints_are_advisory_in_preview_and_block_create(
    tmp_path,
):
    supervisor = RecordingSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(candidate_version_code=2),
        supervisor=supervisor,
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project, workflow_version, target_revisions, profile_revision = (
            _seed_target_comparison(client)
        )
        command = _target_comparison_command(
            workflow_version,
            target_revisions,
            profile_revision,
        )
        preview = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        )
        created = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch",
            json={**command, "preview_token": preview.json()["preview_token"]},
            headers={"Idempotency-Key": "tp-ep05-constraint-rejected"},
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={project['id']}"
        ).json()["items"]

    assert preview.status_code == 200
    assert [
        violation["code"] for violation in preview.json()["constraint_violations"]
    ] == ["APP_VERSION_CODE_MISMATCH"]
    assert created.status_code == 409
    assert created.json()["error"]["code"] == "RUN_COMPARISON_CONSTRAINT_VIOLATED"
    assert created.json()["error"]["details"] == preview.json()[
        "constraint_violations"
    ]
    assert runs == []
    assert supervisor.submitted == []


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("same_target", "RUN_COMPARISON_NO_VARIATION"),
        ("different_profiles", "RUN_COMPARISON_CONFOUNDED"),
    ],
)
def test_target_comparison_rejects_invalid_revision_axes_without_writes(
    tmp_path,
    case,
    expected_code,
):
    app = create_app(_settings(tmp_path), adapter_registry=FakeRegistry())

    with TestClient(app) as client:
        project, workflow_version, target_revisions, profile_revision = (
            _seed_target_comparison(client)
        )
        command = _target_comparison_command(
            workflow_version,
            target_revisions,
            profile_revision,
        )
        if case == "same_target":
            command["lane_bindings"][1]["target_revision_id"] = (
                command["lane_bindings"][0]["target_revision_id"]
            )
        else:
            second_profile = client.post(
                f"/api/platform/v1/projects/{project['id']}/execution-profiles",
                json={
                    "name": "Different deterministic subject",
                    "draft_spec": _profile_spec(),
                },
            ).json()
            second_revision = client.post(
                f"/api/platform/v1/projects/{project['id']}"
                f"/execution-profiles/{second_profile['id']}/publish"
            ).json()
            command["lane_bindings"][1]["execution_profile_revision_id"] = (
                second_revision["id"]
            )

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={project['id']}"
        ).json()["items"]

    assert response.status_code == 409
    assert response.json()["error"]["code"] == expected_code
    assert runs == []


def test_archived_profile_blocks_new_launch_without_changing_frozen_run(tmp_path):
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        (
            project,
            workflow_version,
            _target,
            target_revision,
            profile,
            revision_one,
        ) = _seed_launch(client)
        command = _launch_command(workflow_version, target_revision, revision_one)
        preview = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        ).json()
        created = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch",
            json={**command, "preview_token": preview["preview_token"]},
            headers={"Idempotency-Key": "tp-ep04-frozen-before-archive"},
        )
        changed_spec = _profile_spec()
        changed_spec["model"]["name"] = "newer-model"
        changed = client.patch(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/draft",
            json={
                "draft_spec": changed_spec,
                "expected_draft_version": profile["draft_version"],
            },
        ).json()
        revision_two = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/publish",
            json={
                "expected_draft_version": changed["draft_version"],
                "expected_head_revision_id": revision_one["id"],
            },
        ).json()
        archived = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/archive",
            json={
                "expected_draft_version": changed["draft_version"],
                "expected_head_revision_id": revision_two["id"],
            },
        )
        frozen = client.get(f"/api/platform/v1/runs/{created.json()['id']}")
        blocked = client.post(
            f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
            json=command,
        )

    assert created.status_code == 201
    assert archived.status_code == 200
    assert frozen.status_code == 200
    assert frozen.json()["lanes"][0]["execution_profile_revision_id"] == (
        revision_one["id"]
    )
    assert frozen.json()["run_plan"]["execution_snapshots"][
        revision_one["id"]
    ]["public_spec"]["model"]["name"] == "deterministic-model"
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "EXECUTION_PROFILE_ARCHIVED"


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
