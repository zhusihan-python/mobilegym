from __future__ import annotations

import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.tests.integration.test_report_input import _seed_reportable_paired_run


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def test_diagnostics_api_builds_and_persists_stable_records(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_reportable_paired_run(database)
        row = database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        run_plan = json.loads(row["run_plan_json"])
        run_plan["gates"] = {"max_candidate_errors": 0}
        database.connection.execute(
            "UPDATE runs SET run_plan_json = ? WHERE id = ?",
            (json.dumps(run_plan, sort_keys=True), run_id),
        )
        database.connection.commit()

        response = client.get(f"/api/platform/v1/runs/{run_id}/diagnostics")
        same_response = client.get(f"/api/platform/v1/runs/{run_id}/diagnostics")

        assert response.status_code == 200
        assert response.json() == same_response.json()
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["summary"]["total"] == 3
        assert {item["code"] for item in payload["items"]} == {
            "EXECUTION_ERROR",
            "CANDIDATE_ERROR",
            "QUALITY_GATE_FAILED",
        }
        assert all(item["artifact_refs"] is not None for item in payload["items"])

        persisted = database.connection.execute(
            "SELECT COUNT(*) AS n FROM diagnostics WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert persisted["n"] == 3
