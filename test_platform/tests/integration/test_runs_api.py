import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.services.runs import (
    FakeRunSupervisor,
    _RUN_SECRET_STORE,
    _runner_config_for_lane,
)
from test_platform.testing.fake_compat import FakeCompatibilityProbe
from test_platform.services.compatibility_preflight import CompatibilityPreflight


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _execution_overrides():
    return {
        "agent": "generic_v2",
        "model_base_url": "http://127.0.0.1:1234/v1",
        "model_name": "dogfood-model",
        "image_url_format": "data_url",
    }


class FakeRegistry:
    def check_health(self, config):
        return {
            "healthy": True,
            "executable": True,
            "metadata": {
                "schemaVersion": 1,
                "simulator": {
                    "product": "mobile-gym",
                    "version": "0.1.0",
                    "buildId": "build-vs04",
                },
                "apps": [
                    {
                        "id": "wechat",
                        "packageName": "com.tencent.mm",
                        "displayName": "WeChat",
                        "displayNameEn": "WeChat",
                        "version": "8.0.46",
                        "versionCode": 80046,
                        "type": "plugin",
                    }
                ],
                "data": {"revision": "seed-v1", "bundleHash": "data-sha"},
                "capabilities": ["sim.metadata.v1"],
            },
            "warnings": [],
            "error": None,
        }


def _published_version(client):
    project = client.post("/api/platform/v1/projects", json={"name": "Runs"}).json()
    target = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": "Local simulator",
            "config": {
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
                "labels": {},
            },
        },
    ).json()
    health = client.post(f"/api/platform/v1/targets/{target['id']}/health")
    assert health.status_code == 200
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "WeChat smoke",
            "definition": {
                "schema_version": 1,
                "name": "WeChat smoke",
                "nodes": [
                    {
                        "id": "tasks",
                        "type": "task_selection",
                        "depends_on": [],
                        "config": {
                            "task_ids": ["wechat.OpenBlacklist"],
                            "sample_n": 1,
                        },
                    },
                    {
                        "id": "matrix",
                        "type": "matrix",
                        "depends_on": ["tasks"],
                        "config": {
                            "lanes": {
                                "candidate": {
                                    "target_id": target["id"],
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
                        "config": {"parallel": 1},
                    },
                ],
            },
        },
    ).json()
    published = client.post(f"/api/platform/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200
    return project, health.json()["revision"], published.json()["version"]


def _published_manual_sequence_version(client):
    project = client.post(
        "/api/platform/v1/projects",
        json={"name": "Manual Sequence Runs"},
    ).json()
    target = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": "Local simulator",
            "config": {
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
                "labels": {},
            },
        },
    ).json()
    health = client.post(f"/api/platform/v1/targets/{target['id']}/health")
    assert health.status_code == 200
    task_ids = ["wechat.BlacklistContact", "wechat.OpenBlacklist"]
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "Manual sequence",
            "definition": {
                "schema_version": 1,
                "name": "Manual sequence",
                "nodes": [
                    {
                        "id": "tasks",
                        "type": "task_selection",
                        "depends_on": [],
                        "config": {
                            "task_ids": task_ids,
                            "order_policy": "manual",
                            "sample_n": 1,
                        },
                    },
                    {
                        "id": "matrix",
                        "type": "matrix",
                        "depends_on": ["tasks"],
                        "config": {
                            "lanes": {
                                "candidate": {
                                    "target_id": target["id"],
                                    "role": "candidate",
                                }
                            },
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
            },
        },
    ).json()
    published = client.post(f"/api/platform/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200
    return project, health.json()["revision"], published.json()["version"], task_ids


def test_runs_api_creates_idempotently_lists_and_returns_frozen_detail(tmp_path):
    supervisor = FakeRunSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project, revision, version = _published_version(client)
        request = {
            "workflow_version_id": version["id"],
            "name": "VS-04 API run",
            "overrides": {
                "seed": 321,
                "execution": _execution_overrides(),
            },
        }
        first = client.post(
            "/api/platform/v1/runs",
            json=request,
            headers={"Idempotency-Key": "ci-launch-1"},
        )
        duplicate = client.post(
            "/api/platform/v1/runs",
            json=request,
            headers={"Idempotency-Key": "ci-launch-1"},
        )

        assert first.status_code == 201
        assert duplicate.status_code == 201
        assert duplicate.json()["id"] == first.json()["id"]
        assert len(supervisor.snapshot()["queued_run_ids"]) == 1

        listing = client.get(f"/api/platform/v1/runs?project_id={project['id']}")
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()["items"]] == [first.json()["id"]]

        detail = client.get(f"/api/platform/v1/runs/{first.json()['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["state"] == "queued"
        assert body["progress"]["planned_episodes"] == 2
        assert body["progress"]["planned_lane_episodes"] == 2
        assert body["target_revisions"] == [
            {
                "target_id": body["lanes"][0]["target_id"],
                "target_revision_id": revision["id"],
                "metadata_hash": revision["metadata_hash"],
            }
        ]
        assert len(body["episode_identities"]) == 2
        assert {item["sequence_index"] for item in body["episode_identities"]} == {None}
        assert {item["sequence_group_id"] for item in body["episode_identities"]} == {None}
        assert body["fingerprint"].startswith("sha256:")
        assert body["run_plan"]["lanes"][0]["runner_config"]["agent"] == "generic_v2"
        assert body["run_plan"]["lanes"][0]["runner_config"]["model_base_url"] == "http://127.0.0.1:1234/v1"
        assert body["run_plan"]["lanes"][0]["runner_config"]["model_name"] == "dogfood-model"
        assert body["run_plan"]["lanes"][0]["runner_config"]["image_url_format"] == "data_url"


def test_run_detail_identifies_v1_run_as_legacy_execution(tmp_path):
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=FakeRunSupervisor(),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        _project, _revision, version = _published_version(client)
        created = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "Legacy execution identity",
                "overrides": {
                    "seed": 321,
                    "execution": _execution_overrides(),
                },
            },
            headers={"Idempotency-Key": "legacy-execution-identity"},
        )
        assert created.status_code == 201

        detail = client.get(f"/api/platform/v1/runs/{created.json()['id']}")

        assert detail.status_code == 200
        assert detail.json()["execution_identity"] == {
            "kind": "legacy",
            "label": "Legacy Execution Identity",
            "schema_version": 1,
        }


def test_run_detail_rejects_unknown_run_plan_schema_without_rewriting_it(tmp_path):
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=FakeRunSupervisor(),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        _project, _revision, version = _published_version(client)
        created = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "Future run plan",
                "overrides": {
                    "seed": 321,
                    "execution": _execution_overrides(),
                },
            },
            headers={"Idempotency-Key": "future-run-plan"},
        )
        assert created.status_code == 201
        run_id = created.json()["id"]
        stored = client.app.state.database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()["run_plan_json"]
        future_plan = json.loads(stored)
        future_plan["schema_version"] = 99
        future_plan_json = json.dumps(future_plan, sort_keys=True, separators=(",", ":"))
        client.app.state.database.connection.execute(
            "UPDATE runs SET run_plan_json = ? WHERE id = ?",
            (future_plan_json, run_id),
        )
        client.app.state.database.connection.commit()

        detail = client.get(f"/api/platform/v1/runs/{run_id}")

        assert detail.status_code == 409
        assert detail.json()["error"] == {
            "code": "RUN_PLAN_SCHEMA_UNSUPPORTED",
            "message": "Run Plan schema version is not supported.",
            "details": [
                {
                    "schema_version": 99,
                    "supported_schema_versions": [1, 2],
                }
            ],
            "request_id": detail.json()["error"]["request_id"],
        }
        persisted = client.app.state.database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()["run_plan_json"]
        assert persisted == future_plan_json


def test_runs_api_exposes_manual_sequence_episode_metadata(tmp_path):
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=FakeRunSupervisor(),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        _project, _revision, version, task_ids = _published_manual_sequence_version(client)
        response = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "Manual sequence API run",
                "overrides": {
                    "seed": 321,
                    "execution": _execution_overrides(),
                },
            },
            headers={"Idempotency-Key": "ci-launch-manual-sequence"},
        )

        assert response.status_code == 201
        body = response.json()
        identities = body["episode_identities"]
        assert [item["task_base_id"] for item in identities] == task_ids
        assert [item["sequence_index"] for item in identities] == [0, 1]
        assert [item["sequence_group_id"] for item in identities] == [
            "manual_sequence",
            "manual_sequence",
        ]

        rows = client.app.state.database.connection.execute(
            """
            SELECT task_base_id, sequence_index, sequence_group_id
            FROM episodes
            WHERE run_id = ?
            ORDER BY sequence_index
            """,
            (body["id"],),
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {
                "task_base_id": task_ids[0],
                "sequence_index": 0,
                "sequence_group_id": "manual_sequence",
            },
            {
                "task_base_id": task_ids[1],
                "sequence_index": 1,
                "sequence_group_id": "manual_sequence",
            },
        ]


def test_runs_api_accepts_online_model_key_without_persisting_secret(tmp_path):
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=FakeRunSupervisor(),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        _project, _revision, version = _published_version(client)
        response = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "online vision model",
                "overrides": {
                    "seed": 321,
                    "execution": {
                        **_execution_overrides(),
                        "model_api_key": "sk-online-vision-secret",
                    },
                },
            },
            headers={"Idempotency-Key": "ci-launch-online-model-key"},
        )

        assert response.status_code == 201
        assert "sk-online-vision-secret" not in response.text
        body = response.json()
        runner_config = body["run_plan"]["lanes"][0]["runner_config"]
        assert runner_config["model_api_key_configured"] is True
        assert "model_api_key" not in runner_config

        lane = type(
            "Lane",
            (),
            {
                "run_id": body["id"],
                "runner_config": runner_config,
            },
        )()
        config = _runner_config_for_lane(lane, client.app.state.settings)
        assert config.model_api_key == "sk-online-vision-secret"
        _RUN_SECRET_STORE.discard(body["id"])


def test_runs_api_rejects_launch_without_execution_config(tmp_path):
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=FakeRunSupervisor(),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        _project, _revision, version = _published_version(client)
        response = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "missing model config",
                "overrides": {"seed": 321},
            },
            headers={"Idempotency-Key": "ci-launch-missing-execution"},
        )

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "RUN_EXECUTION_CONFIG_MISSING"
        assert error["details"][0]["field"] == "overrides.execution.agent"


def test_runs_api_rejects_invalid_image_url_format(tmp_path):
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=FakeRunSupervisor(),
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        _project, _revision, version = _published_version(client)
        response = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "invalid image format",
                "overrides": {
                    "seed": 321,
                    "execution": {
                        **_execution_overrides(),
                        "image_url_format": "file_path",
                    },
                },
            },
            headers={"Idempotency-Key": "ci-launch-invalid-image-url-format"},
        )

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "RUN_EXECUTION_CONFIG_INVALID"
        assert error["details"][0]["field"] == "overrides.execution.image_url_format"


# ---------------------------------------------------------------------------
# VS-10 Block B: create-run authoritative constraint gate (Contract 3)
# ---------------------------------------------------------------------------


def _paired_definition_with_constraints(baseline_target_id, candidate_target_id):
    return {
        "schema_version": 1,
        "name": "Paired comparison",
        "nodes": [
            {
                "id": "tasks",
                "type": "task_selection",
                "depends_on": [],
                "config": {"task_ids": ["wechat.OpenBlacklist"], "sample_n": 1},
            },
            {
                "id": "matrix",
                "type": "matrix",
                "depends_on": ["tasks"],
                "config": {
                    "lanes": {
                        "baseline": {"target_id": baseline_target_id, "role": "baseline"},
                        "candidate": {"target_id": candidate_target_id, "role": "candidate"},
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
            {
                "id": "compare",
                "type": "compare",
                "depends_on": ["execute"],
                "config": {
                    "target_constraints": ["same_app", "same_device", "same_data"],
                    "initial_state_policy": "task_projection",
                    "execution": "serial",
                },
            },
        ],
    }


def _record_revision(client, target_id, *, version_code, data_revision, port=5173):
    client.app.state.database.connection.execute(
        """
        INSERT INTO target_revisions (
          id, target_id, metadata_json, metadata_hash, health_status,
          warnings_json, resolved_at
        )
        VALUES (?, ?, ?, ?, 'healthy', '[]', '2026-07-05T00:00:00.000Z')
        """,
        (
            f"rev-{target_id}-{version_code}-{data_revision}",
            target_id,
            __import__("json").dumps(
                {
                    "schema_version": 1,
                    "apps": [
                        {
                            "id": "wechat",
                            "packageName": "com.tencent.mm",
                            "displayName": "WeChat",
                            "version": "8.0.46",
                            "versionCode": version_code,
                            "type": "plugin",
                        }
                    ],
                    "data": {"revision": data_revision},
                    "device_profile": {
                        "name": "Pixel 7",
                        "viewport_width": 393,
                        "viewport_height": 852,
                        "physical_width": 1080,
                        "physical_height": 2400,
                        "device_scale_factor": 2.75,
                    },
                },
                sort_keys=True,
            ),
            f"sha256:{target_id}-{version_code}-{data_revision}",
        ),
    )
    client.app.state.database.connection.commit()


def _published_paired_workflow(client, baseline_target_id, candidate_target_id):
    workflow = client.post(
        f"/api/platform/v1/projects/{_first_project_id(client)}/workflows",
        json={
            "name": "Paired comparison",
            "definition": _paired_definition_with_constraints(
                baseline_target_id, candidate_target_id
            ),
        },
    ).json()
    published = client.post(f"/api/platform/v1/workflows/{workflow['id']}/publish")
    assert published.status_code == 200
    return published.json()["version"]


def _first_project_id(client):
    row = client.app.state.database.connection.execute(
        "SELECT id FROM projects ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    return str(row["id"])


def _make_sim_target(client, project_id, *, name, port):
    return client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project_id,
            "name": name,
            "config": {
                "kind": "simulator",
                "connection": {"env_url": f"http://127.0.0.1:{port}"},
                "device_profile": {
                    "name": "Pixel 7",
                    "viewport_width": 393,
                    "viewport_height": 852,
                    "physical_width": 1080,
                    "physical_height": 2400,
                    "device_scale_factor": 2.75,
                },
                "runtime": {},
                "labels": {},
            },
        },
    ).json()


def test_create_run_blocks_when_target_constraints_violated(tmp_path):
    """Contract 3: create-run is the AUTHORITATIVE gate. A mismatched versionCode
    raises RunDomainError → 409 with violation details (NOT a success response
    with a violations field)."""
    supervisor = FakeRunSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project = client.post("/api/platform/v1/projects", json={"name": "Runs"}).json()
        baseline = _make_sim_target(client, project["id"], name="baseline", port=5173)
        candidate = _make_sim_target(client, project["id"], name="candidate", port=5174)
        # Mismatched versionCode → same_app violation.
        _record_revision(client, baseline["id"], version_code=80046, data_revision="seed-v1")
        _record_revision(client, candidate["id"], version_code=80047, data_revision="seed-v1")
        version = _published_paired_workflow(client, baseline["id"], candidate["id"])

        response = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "VS-10 blocked run",
                "overrides": {"seed": 1, "execution": _execution_overrides()},
            },
            headers={"Idempotency-Key": "blocked-pair-1"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "COMPARISON_CONSTRAINT_VIOLATED"
    codes = {d.get("code") for d in body["error"]["details"]}
    assert "APP_VERSION_CODE_MISMATCH" in codes
    # No run was created (no queued run).
    assert supervisor.snapshot()["queued_run_ids"] == []


def test_create_run_proceeds_when_constraints_satisfied(tmp_path):
    """When all constraints are satisfied, create-run proceeds normally."""
    supervisor = FakeRunSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
        compatibility_probe=FakeCompatibilityProbe(),
    )

    with TestClient(app) as client:
        project = client.post("/api/platform/v1/projects", json={"name": "Runs"}).json()
        baseline = _make_sim_target(client, project["id"], name="baseline", port=5173)
        candidate = _make_sim_target(client, project["id"], name="candidate", port=5174)
        _record_revision(client, baseline["id"], version_code=80046, data_revision="seed-v1")
        _record_revision(client, candidate["id"], version_code=80046, data_revision="seed-v1")
        version = _published_paired_workflow(client, baseline["id"], candidate["id"])

        response = client.post(
            "/api/platform/v1/runs",
            json={
                "workflow_version_id": version["id"],
                "name": "VS-10 ok run",
                "overrides": {"seed": 1, "execution": _execution_overrides()},
            },
            headers={"Idempotency-Key": "ok-pair-1"},
        )

    assert response.status_code == 201
    assert response.json()["state"] == "queued"
    assert len(supervisor.snapshot()["queued_run_ids"]) == 1
