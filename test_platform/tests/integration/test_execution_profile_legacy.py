from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.services.runs import FakeRunSupervisor
from test_platform.testing.fake_compat import FakeCompatibilityProbe
from test_platform.tests.integration.test_retry_run import (
    _seed_retryable_single_lane_run,
)
from test_platform.tests.integration.test_runs_api import (
    FakeRegistry,
    _execution_overrides,
    _published_version,
    _settings,
)


def test_legacy_create_read_and_followup_remain_profile_free(tmp_path):
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
                "name": "Legacy compatibility window",
                "overrides": {
                    "seed": 321,
                    "execution": _execution_overrides(),
                },
            },
            headers={"Idempotency-Key": "legacy-compatibility-window"},
        )

        assert created.status_code == 201
        detail = client.get(f"/api/platform/v1/runs/{created.json()['id']}")
        assert detail.status_code == 200
        assert detail.json()["execution_identity"] == {
            "kind": "legacy",
            "label": "Legacy Execution Identity",
            "schema_version": 1,
        }

        retryable_run_id = _seed_retryable_single_lane_run(client.app.state.database)
        preview = client.get(
            f"/api/platform/v1/runs/{retryable_run_id}/retry/preview"
        )

        assert preview.status_code == 200
        assert preview.json()["execution_identity"] == {
            "kind": "legacy",
            "label": "Legacy Execution Identity",
            "schema_version": 1,
        }
        assert preview.json()["can_execute"] is True

        retried = client.post(
            f"/api/platform/v1/runs/{retryable_run_id}/retry",
            json={
                "preview_token": preview.json()["preview_token"],
                "skip_compatibility_check": True,
            },
        )

        assert retried.status_code == 202
        retry_detail = client.get(f"/api/platform/v1/runs/{retryable_run_id}")
        assert retry_detail.status_code == 200
        assert retry_detail.json()["execution_identity"]["kind"] == "legacy"
