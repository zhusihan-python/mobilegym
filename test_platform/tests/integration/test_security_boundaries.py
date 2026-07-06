from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.main import validate_runtime_settings
from test_platform.tests.integration.test_report_input import _seed_reportable_paired_run


def _settings(tmp_path, **overrides):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
        **overrides,
    )


def test_non_loopback_binding_requires_explicit_opt_in(tmp_path):
    settings = _settings(tmp_path, host="0.0.0.0")

    with pytest.raises(RuntimeError, match="TEST_PLATFORM_ALLOW_NON_LOOPBACK"):
        validate_runtime_settings(settings)

    validate_runtime_settings(_settings(tmp_path, host="0.0.0.0", allow_non_loopback=True))


def test_mutating_requests_reject_cross_origin_browser_calls(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        response = client.post(
            "/api/platform/v1/projects",
            json={"name": "Blocked"},
            headers={"Origin": "https://evil.example"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MUTATION_ORIGIN_REJECTED"


def test_report_exports_redact_configured_secret_values(tmp_path):
    settings = _settings(tmp_path, auth_token=SecretStr("configured-secret"))
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_reportable_paired_run(client.app.state.database)
        row = client.app.state.database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        run_plan = json.loads(row["run_plan_json"])
        run_plan["task_source"]["registry_digest"] = "configured-secret"
        client.app.state.database.connection.execute(
            "UPDATE runs SET state = 'completed', run_plan_json = ? WHERE id = ?",
            (json.dumps(run_plan, sort_keys=True), run_id),
        )
        client.app.state.database.connection.execute(
            "UPDATE run_attempts SET state = 'completed' WHERE id = 'attempt3'"
        )
        client.app.state.database.connection.execute(
            "UPDATE lane_attempts SET state = 'completed' WHERE run_attempt_id = 'attempt3'"
        )
        client.app.state.database.connection.commit()

        json_export = client.get(f"/api/platform/v1/runs/{run_id}/report/export")
        html_export = client.get(f"/api/platform/v1/runs/{run_id}/report/export?format=html")

    assert json_export.status_code == 200
    assert "configured-secret" not in json_export.text
    assert "[REDACTED_SECRET]" in json_export.text
    assert html_export.status_code == 200
    assert "configured-secret" not in html_export.text
    assert "[REDACTED_SECRET]" in html_export.text
