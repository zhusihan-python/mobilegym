from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _project(client):
    response = client.post("/api/platform/v1/projects", json={"name": "Workflows"})
    assert response.status_code == 201
    return response.json()


def _target(client, project_id, *, enabled=True):
    response = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project_id,
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
    )
    assert response.status_code == 201
    target = response.json()
    if not enabled:
        client.app.state.database.connection.execute(
            "UPDATE targets SET enabled = 0 WHERE id = ?",
            (target["id"],),
        )
        client.app.state.database.connection.commit()
        target = client.get(f"/api/platform/v1/targets/{target['id']}").json()
    return target


def _definition(target_id, *, repeat_n=2):
    return {
        "schema_version": 1,
        "name": "WeChat smoke",
        "nodes": [
            {
                "id": "tasks",
                "type": "task_selection",
                "depends_on": [],
                "config": {
                    "task_ids": [
                        "wechat.BlacklistContact",
                        "wechat.OpenBlacklist",
                    ],
                    "sample_n": 1,
                },
            },
            {
                "id": "matrix",
                "type": "matrix",
                "depends_on": ["tasks"],
                "config": {
                    "lanes": {"candidate": {"target_id": target_id}},
                    "repeat_n": repeat_n,
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


def test_workflow_api_validates_previews_publishes_and_freezes_versions(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = _project(client)
        target = _target(client, project["id"])
        created = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={"name": "WeChat smoke", "definition": _definition(target["id"])},
        )
        assert created.status_code == 201
        workflow = created.json()

        validation = client.post(f"/api/platform/v1/workflows/{workflow['id']}/validate")
        assert validation.status_code == 200
        assert validation.json()["valid"] is True

        preview = client.post(f"/api/platform/v1/workflows/{workflow['id']}/compile-preview")
        assert preview.status_code == 200
        assert preview.json()["total_episodes"] == 4

        published = client.post(f"/api/platform/v1/workflows/{workflow['id']}/publish")
        assert published.status_code == 200
        version = published.json()["version"]
        assert version["version_no"] == 1
        assert version["status"] == "published"
        assert version["definition"]["nodes"][1]["config"]["repeat_n"] == 2

        edited = client.patch(
            f"/api/platform/v1/workflows/{workflow['id']}/draft",
            json={"definition": _definition(target["id"], repeat_n=3)},
        )
        assert edited.status_code == 200
        frozen = client.get(f"/api/platform/v1/workflow-versions/{version['id']}")
        assert frozen.status_code == 200
        assert frozen.json()["definition"]["nodes"][1]["config"]["repeat_n"] == 2


def test_publish_rejects_disabled_targets_with_structured_errors(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = _project(client)
        target = _target(client, project["id"], enabled=False)
        workflow = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={"name": "Disabled target workflow", "definition": _definition(target["id"])},
        ).json()

        published = client.post(f"/api/platform/v1/workflows/{workflow['id']}/publish")

    assert published.status_code == 400
    body = published.json()
    assert body["error"]["code"] == "WORKFLOW_VALIDATION_FAILED"
    assert body["error"]["details"][0]["code"] == "WORKFLOW_TARGET_DISABLED"


# ---------------------------------------------------------------------------
# VS-10 Block B: compile-preview advisory constraint validation (Contract 3)
# ---------------------------------------------------------------------------


def _paired_definition(baseline_target_id, candidate_target_id, *, repeat_n=1):
    """A paired (2-lane) workflow with a compare node carrying the three axes."""
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
                        "baseline": {
                            "target_id": baseline_target_id,
                            "role": "baseline",
                        },
                        "candidate": {
                            "target_id": candidate_target_id,
                            "role": "candidate",
                        },
                    },
                    "repeat_n": repeat_n,
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


def _record_revision(client, target_id, *, version_code=80046, data_revision="seed-v1"):
    """Record a target revision with explicit apps/data so the constraint engine
    can compare frozen metadata."""
    from test_platform.api.app import create_app  # noqa: F401

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


def test_compile_preview_returns_violations_advisory_for_mismatched_app(tmp_path):
    """Contract 3: compile-preview is ADVISORY — it resolves latest revisions,
    validates, and returns ``violations`` in the SUCCESS response (200), not an
    error. A mismatched versionCode surfaces as a same_app violation."""
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = _project(client)
        baseline = _target(client, project["id"])
        # Create a distinct candidate target (different name/port).
        candidate2 = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": "Candidate simulator",
                "config": {
                    "kind": "simulator",
                    "connection": {"env_url": "http://127.0.0.1:5174"},
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
        _record_revision(client, baseline["id"], version_code=80046)
        _record_revision(client, candidate2["id"], version_code=80047)  # mismatched

        workflow = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={
                "name": "Paired comparison",
                "definition": _paired_definition(baseline["id"], candidate2["id"]),
            },
        ).json()

        preview = client.post(f"/api/platform/v1/workflows/{workflow['id']}/compile-preview")

    assert preview.status_code == 200  # advisory — does not block
    body = preview.json()
    # Contract 3: violations field present in the success response.
    assert "violations" in body
    codes = {v["code"] for v in body["violations"]}
    assert "APP_VERSION_CODE_MISMATCH" in codes


def test_compile_preview_returns_no_violations_when_constraints_satisfied(tmp_path):
    """When both lanes satisfy all constraints, violations is an empty list."""
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = _project(client)
        baseline = _target(client, project["id"])
        candidate = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": "Candidate simulator",
                "config": {
                    "kind": "simulator",
                    "connection": {"env_url": "http://127.0.0.1:5174"},
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
        _record_revision(client, baseline["id"], version_code=80046)
        _record_revision(client, candidate["id"], version_code=80046)

        workflow = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={
                "name": "Paired comparison",
                "definition": _paired_definition(baseline["id"], candidate["id"]),
            },
        ).json()

        preview = client.post(f"/api/platform/v1/workflows/{workflow['id']}/compile-preview")

    assert preview.status_code == 200
    body = preview.json()
    assert body["violations"] == []

