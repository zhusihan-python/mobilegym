from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _project(client):
    response = client.post("/api/platform/v1/projects", json={"name": "Targets"})
    assert response.status_code == 201
    return response.json()


def _simulator_config(secret_ref="secret://mobilegym/proxy"):
    return {
        "kind": "simulator",
        "connection": {
            "env_url": "http://127.0.0.1:5173",
            "proxy_secret_ref": secret_ref,
        },
        "device_profile": {
            "name": "Pixel 7",
            "viewport_width": 393,
            "viewport_height": 852,
            "physical_width": 1080,
            "physical_height": 2400,
            "device_scale_factor": 2.75,
        },
        "runtime": {"locale": "en-US"},
        "labels": {"lane": "local"},
    }


def _real_device_config():
    return {
        "kind": "real_device",
        "connection": {"adb_serial": "emulator-5554"},
        "device_profile": {
            "name": "Lab Device",
            "viewport_width": 393,
            "viewport_height": 852,
            "physical_width": 1080,
            "physical_height": 2400,
            "device_scale_factor": 2.75,
        },
        "app_artifact": {"path": "artifacts/app.apk"},
        "runtime": {},
        "labels": {"lane": "reserved"},
    }


def _metadata(build_id="build-vs02"):
    return {
        "schemaVersion": 1,
        "simulator": {
            "product": "mobile-gym",
            "version": "0.1.0",
            "buildId": build_id,
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
    }


class FakeRegistry:
    def __init__(self, metadata):
        self.metadata = metadata

    def check_health(self, config):
        return {
            "healthy": True,
            "executable": True,
            "metadata": self.metadata,
            "warnings": ["Data revision seed-v1 is not pinned."],
            "error": None,
        }


def test_targets_api_creates_lists_and_redacts_secret_config(tmp_path):
    app = create_app(_settings(tmp_path), adapter_registry=FakeRegistry(_metadata()))

    with TestClient(app) as client:
        project = _project(client)
        created = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": "Local simulator",
                "config": _simulator_config(),
            },
        )

        assert created.status_code == 201
        target = created.json()
        assert target["kind"] == "simulator"
        assert target["config"]["connection"] == {
            "env_url": "http://127.0.0.1:5173",
            "proxy_configured": True,
        }
        assert "proxy_secret_ref" not in created.text
        assert "secret://mobilegym/proxy" not in created.text

        listing = client.get(f"/api/platform/v1/targets?project_id={project['id']}")
        assert listing.status_code == 200
        assert listing.json()["items"] == [target]

        detail = client.get(f"/api/platform/v1/targets/{target['id']}")
        assert detail.status_code == 200
        assert detail.json() == target


def test_simulator_health_reuses_revision_for_identical_metadata(tmp_path):
    app = create_app(_settings(tmp_path), adapter_registry=FakeRegistry(_metadata()))

    with TestClient(app) as client:
        project = _project(client)
        target = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": "Local simulator",
                "config": _simulator_config(secret_ref=None),
            },
        ).json()

        first = client.post(f"/api/platform/v1/targets/{target['id']}/health")
        second = client.post(f"/api/platform/v1/targets/{target['id']}/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["healthy"] is True
    assert first.json()["revision"]["id"] == second.json()["revision"]["id"]
    assert first.json()["warnings"] == ["Data revision seed-v1 is not pinned."]


def test_invalid_simulator_metadata_returns_structured_error(tmp_path):
    metadata = _metadata()
    del metadata["apps"][0]["packageName"]
    app = create_app(_settings(tmp_path), adapter_registry=FakeRegistry(metadata))

    with TestClient(app) as client:
        project = _project(client)
        target = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": "Local simulator",
                "config": _simulator_config(secret_ref=None),
            },
        ).json()

        response = client.post(f"/api/platform/v1/targets/{target['id']}/health")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TARGET_METADATA_INVALID"


def test_real_device_health_reports_execution_disabled(tmp_path):
    app = create_app(_settings(tmp_path), adapter_registry=FakeRegistry(_metadata()))

    with TestClient(app) as client:
        project = _project(client)
        target = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": "Reserved device",
                "config": _real_device_config(),
            },
        ).json()

        response = client.post(f"/api/platform/v1/targets/{target['id']}/health")

    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is False
    assert body["executable"] is False
    assert body["error"]["code"] == "TARGET_KIND_NOT_EXECUTABLE"
