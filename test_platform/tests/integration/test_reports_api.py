from __future__ import annotations

import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.execution.event_writer import EventWriter
from test_platform.services.completion import RunCompletionPipeline
from test_platform.tests.integration.test_report_input import _seed_reportable_paired_run


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _make_completed_reportable_run(client: TestClient) -> str:
    database = client.app.state.database
    run_id = _seed_reportable_paired_run(database)
    row = database.connection.execute(
        "SELECT run_plan_json FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    run_plan = json.loads(row["run_plan_json"])
    run_plan["task_source"]["registry_digest"] = "<script>alert(1)</script>"
    run_plan["gates"] = {
        "max_candidate_errors": 0,
        "max_unpaired": 0,
    }
    database.connection.execute(
        "UPDATE runs SET state = 'completed', run_plan_json = ? WHERE id = ?",
        (json.dumps(run_plan, sort_keys=True), run_id),
    )
    database.connection.execute(
        "UPDATE run_attempts SET state = 'completed' WHERE id = 'attempt3'"
    )
    database.connection.execute(
        "UPDATE lane_attempts SET state = 'completed' WHERE run_attempt_id = 'attempt3'"
    )
    database.connection.commit()
    return run_id


def test_reports_api_builds_exports_and_promotes_baseline(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        run_id = _make_completed_reportable_run(client)

        report_response = client.get(f"/api/platform/v1/runs/{run_id}/report")
        assert report_response.status_code == 200
        report = report_response.json()
        assert report["provenance"]["run_id"] == run_id
        assert report["functional"]["summary"]["planned_lane_episodes"] == 4
        assert report["comparison"]["classification_counts"]["candidate_errors"] == 1
        assert report["gate"]["verdict"] == "failed"
        assert report["gate"]["thresholds"] == {
            "max_candidate_errors": 0,
            "max_unpaired": 0,
        }

        runs = client.get("/api/platform/v1/runs?project_id=proj1")
        assert runs.status_code == 200
        assert runs.json()["items"][0]["gate_verdict"] == "failed"

        json_export = client.get(f"/api/platform/v1/runs/{run_id}/report/export")
        same_json_export = client.get(f"/api/platform/v1/runs/{run_id}/report/export")
        assert json_export.status_code == 200
        assert json_export.headers["content-type"].startswith("application/json")
        assert json_export.text == same_json_export.text

        html_export = client.get(
            f"/api/platform/v1/runs/{run_id}/report/export?format=html"
        )
        assert html_export.status_code == 200
        assert "Gate verdict: failed" in html_export.text
        assert "Provenance" in html_export.text
        assert "<script>alert(1)</script>" not in html_export.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_export.text

        promoted = client.post(f"/api/platform/v1/runs/{run_id}/baseline")
        assert promoted.status_code == 201
        baseline = promoted.json()
        assert baseline["run_id"] == run_id
        assert baseline["workflow_version_id"] == "wv1"
        assert baseline["run_plan_hash"] == "sha256:plan"
        assert baseline["task_source_digest"] == "<script>alert(1)</script>"
        assert baseline["lane_key"] == "candidate"
        assert baseline["target_revision_id"] == "trev_cand"


def test_promote_baseline_rejects_non_completed_run(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        run_id = _seed_reportable_paired_run(client.app.state.database)

        promoted = client.post(f"/api/platform/v1/runs/{run_id}/baseline")

    assert promoted.status_code == 409
    assert promoted.json()["error"]["code"] == "BASELINE_PROMOTION_INVALID_RUN_STATE"


def test_completed_run_api_already_has_report_verdict_and_outcome_counts(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_reportable_paired_run(database)
        database.connection.execute(
            "UPDATE runs SET state = 'evaluating', ended_at = NULL WHERE id = ?",
            (run_id,),
        )
        database.connection.execute(
            "UPDATE run_attempts SET state = 'evaluating', ended_at = NULL "
            "WHERE id = 'attempt3'"
        )
        database.connection.execute(
            "UPDATE lane_attempts SET state = 'completed' "
            "WHERE run_attempt_id = 'attempt3'"
        )
        database.connection.commit()

        RunCompletionPipeline(
            database,
            event_writer=EventWriter(database),
        ).complete(run_id)
        report_count_before_get = database.connection.execute(
            "SELECT COUNT(*) FROM reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]

        run_response = client.get(f"/api/platform/v1/runs/{run_id}")
        report_response = client.get(f"/api/platform/v1/runs/{run_id}/report")

        assert run_response.status_code == 200
        assert run_response.json()["state"] == "completed"
        assert run_response.json()["gate_verdict"] == "not_configured"
        assert run_response.json()["outcome_counts"] == {
            "pass": 1,
            "fail": 0,
            "error": 1,
            "cancelled": 0,
            "incomplete": 2,
        }
        assert report_response.status_code == 200
        assert report_response.json()["gate"]["verdict"] == "not_configured"
        assert report_count_before_get == 1
        assert database.connection.execute(
            "SELECT COUNT(*) FROM reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1
