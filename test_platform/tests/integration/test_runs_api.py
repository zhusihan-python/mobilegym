from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.services.runs import FakeRunSupervisor


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


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


def test_runs_api_creates_idempotently_lists_and_returns_frozen_detail(tmp_path):
    supervisor = FakeRunSupervisor()
    app = create_app(
        _settings(tmp_path),
        adapter_registry=FakeRegistry(),
        supervisor=supervisor,
    )

    with TestClient(app) as client:
        project, revision, version = _published_version(client)
        request = {
            "workflow_version_id": version["id"],
            "name": "VS-04 API run",
            "overrides": {"seed": 321},
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
        assert body["fingerprint"].startswith("sha256:")
