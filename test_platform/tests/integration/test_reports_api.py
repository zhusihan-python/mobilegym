from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.execution.event_writer import EventWriter
from test_platform.services.completion import RunCompletionPipeline
from test_platform.services.execution import SerialRunExecutor
from test_platform.tests.integration.test_report_input import _seed_reportable_paired_run
from test_platform.tests.integration.test_serial_run_execution import (
    _ExecutableFakeEnv,
    _ExecutableTaskFactory,
    _FakeAgent,
)
from test_platform.tests.integration.test_single_lane_materialization import _create_run


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
        assert report["schema_version"] == 2
        assert "reliability" in report
        assert "tasks" in report["reliability"]
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
        assert promoted.status_code == 409
        assert promoted.json()["error"]["code"] == "BASELINE_PROMOTION_INELIGIBLE"
        assert [
            reason["code"]
            for reason in promoted.json()["error"]["details"][0]["reasons"]
        ] == ["SELECTED_LANE_INCOMPLETE", "SELECTED_LANE_OUTCOME_NOT_PASS"]


def test_baseline_eligibility_is_strict_for_the_selected_lane(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _make_completed_reportable_run(client)
        database.connection.execute(
            "UPDATE episode_attempts SET outcome = 'PASS' WHERE id = 'ea_base_ep0'"
        )
        database.connection.executemany(
            """
            INSERT INTO episode_attempts (
              id, episode_id, lane_attempt_id, attempt_no, state, outcome,
              error_code, result_json, artifact_root, started_at, ended_at, created_at
            )
            VALUES (?, 'ep1', ?, 1, 'completed', 'PASS', NULL, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "ea_base_ep1",
                    "attempt3_baseline",
                    json.dumps({"is_success": True}),
                    "artifacts/base1",
                    "2026-07-11T00:00:00.000Z",
                    "2026-07-11T00:00:01.000Z",
                    "2026-07-11T00:00:01.000Z",
                ),
                (
                    "ea_cand_ep1",
                    "attempt3_candidate",
                    json.dumps({"is_success": True}),
                    "artifacts/cand1",
                    "2026-07-11T00:00:00.000Z",
                    "2026-07-11T00:00:01.000Z",
                    "2026-07-11T00:00:01.000Z",
                ),
            ],
        )
        database.connection.commit()

        report = client.get(f"/api/platform/v1/runs/{run_id}/report")
        assert report.status_code == 200
        assert report.json()["gate"]["verdict"] == "failed"

        baseline_eligibility = client.get(
            f"/api/platform/v1/runs/{run_id}/baseline/eligibility?lane_key=baseline"
        )
        candidate_eligibility = client.get(
            f"/api/platform/v1/runs/{run_id}/baseline/eligibility?lane_key=candidate"
        )

        assert baseline_eligibility.status_code == 200
        assert baseline_eligibility.json()["eligible"] is True
        assert baseline_eligibility.json()["reasons"] == []
        assert candidate_eligibility.status_code == 200
        assert candidate_eligibility.json()["eligible"] is False
        assert [reason["code"] for reason in candidate_eligibility.json()["reasons"]] == [
            "SELECTED_LANE_OUTCOME_NOT_PASS"
        ]
        assert candidate_eligibility.json()["counts"] == {
            "planned": 2,
            "pass": 1,
            "fail": 0,
            "error": 1,
            "cancelled": 0,
            "incomplete": 0,
        }

        promoted = client.post(
            f"/api/platform/v1/runs/{run_id}/baseline",
            json={"lane_key": "baseline"},
        )
        rejected = client.post(
            f"/api/platform/v1/runs/{run_id}/baseline",
            json={"lane_key": "candidate"},
        )

        assert promoted.status_code == 201
        assert promoted.json()["lane_key"] == "baseline"
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "BASELINE_PROMOTION_INELIGIBLE"
        assert rejected.json()["error"]["details"][0]["reasons"] == [
            {
                "code": "SELECTED_LANE_OUTCOME_NOT_PASS",
                "message": "Every selected-lane episode must have outcome PASS.",
                "details": {"error": 1},
            }
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "reason_code", "count_key", "gate_verdict"),
    [
        ("FAIL", "SELECTED_LANE_OUTCOME_NOT_PASS", "fail", "not_configured"),
        ("ERROR", "SELECTED_LANE_OUTCOME_NOT_PASS", "error", "passed"),
        ("CANCELLED", "SELECTED_LANE_OUTCOME_NOT_PASS", "cancelled", "passed"),
        (None, "SELECTED_LANE_INCOMPLETE", "incomplete", "passed"),
    ],
)
async def test_single_lane_non_pass_work_is_never_strict_baseline_eligible(
    tmp_path,
    outcome,
    reason_code,
    count_key,
    gate_verdict,
):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        database = client.app.state.database
        run = _create_run(database, settings, repeat_n=1)
        envs = iter([
            _ExecutableFakeEnv(label="materialize"),
            _ExecutableFakeEnv(label="execute"),
        ])
        await SerialRunExecutor(
            database,
            settings,
            task_factory=_ExecutableTaskFactory(),
            env_factory=lambda lane: next(envs),
            agent_factory=lambda lane: _FakeAgent(),
        ).execute_run(run.id)
        if outcome is None:
            database.connection.execute(
                "DELETE FROM episode_attempts WHERE episode_id IN "
                "(SELECT id FROM episodes WHERE run_id = ?)",
                (run.id,),
            )
        else:
            database.connection.execute(
                "UPDATE episode_attempts SET outcome = ?, state = ? WHERE episode_id IN "
                "(SELECT id FROM episodes WHERE run_id = ?)",
                (outcome, "cancelled" if outcome == "CANCELLED" else "completed", run.id),
            )
        plan_row = database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?",
            (run.id,),
        ).fetchone()
        run_plan = json.loads(plan_row["run_plan_json"])
        if gate_verdict == "passed":
            run_plan["gates"] = {"min_success_rate": 0}
        database.connection.execute(
            "UPDATE runs SET run_plan_json = ? WHERE id = ?",
            (json.dumps(run_plan, sort_keys=True), run.id),
        )
        database.connection.commit()
        RunCompletionPipeline(
            database,
            event_writer=EventWriter(database),
        ).complete(run.id)

        eligibility = client.get(
            f"/api/platform/v1/runs/{run.id}/baseline/eligibility?lane_key=candidate"
        )
        report = client.get(f"/api/platform/v1/runs/{run.id}/report")
        promoted = client.post(
            f"/api/platform/v1/runs/{run.id}/baseline",
            json={"lane_key": "candidate"},
        )

        assert eligibility.status_code == 200
        assert report.json()["gate"]["verdict"] == gate_verdict
        assert eligibility.json()["eligible"] is False
        assert eligibility.json()["counts"][count_key] == 1
        assert reason_code in [reason["code"] for reason in eligibility.json()["reasons"]]
        assert promoted.status_code == 409
        assert promoted.json()["error"]["code"] == "BASELINE_PROMOTION_INELIGIBLE"


def test_promote_baseline_rejects_non_completed_run(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        run_id = _seed_reportable_paired_run(client.app.state.database)

        promoted = client.post(f"/api/platform/v1/runs/{run_id}/baseline")

    assert promoted.status_code == 409
    assert promoted.json()["error"]["code"] == "BASELINE_PROMOTION_INELIGIBLE"
    assert promoted.json()["error"]["details"][0]["reasons"][0]["code"] == (
        "REPORT_NOT_PERSISTED"
    )


def test_baseline_promotion_has_no_force_override(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        run_id = _make_completed_reportable_run(client)
        client.get(f"/api/platform/v1/runs/{run_id}/report")

        response = client.post(
            f"/api/platform/v1/runs/{run_id}/baseline",
            json={"lane_key": "candidate", "force": True},
        )

    assert response.status_code == 422


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


def test_legacy_schema_v1_report_remains_readable_and_exportable(tmp_path):
    """A report persisted with schema_version=1 (pre-TP-H11) must remain
    readable via GET and exportable, even though new reports use version 2."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        run_id = _make_completed_reportable_run(client)
        db = client.app.state.database

        # Overwrite the cached report with a legacy v1 payload.
        legacy_report = {
            "id": "legacy-report",
            "schema_version": 1,
            "run_id": run_id,
            "run_attempt_id": "attempt3",
            "functional": {"summary": {}},
            "gate": {"verdict": "passed", "thresholds": {}, "observed": {}, "reasons": []},
            "created_at": "2026-07-01T00:00:00Z",
        }
        db.connection.execute(
            "DELETE FROM reports WHERE run_id = ?", (run_id,)
        )
        db.connection.execute(
            """
            INSERT INTO reports (id, run_id, run_attempt_id, schema_version,
                                 input_hash, report_json, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
            (
                "legacy-report",
                run_id,
                "attempt3",
                "sha256:legacy",
                _canonical_json(legacy_report),
                "2026-07-01T00:00:00Z",
            ),
        )
        db.connection.commit()

        # GET must return the legacy report (readable).
        report_response = client.get(f"/api/platform/v1/runs/{run_id}/report")
        assert report_response.status_code == 200
        body = report_response.json()
        assert body["schema_version"] == 1

        # JSON export must work.
        export_response = client.get(
            f"/api/platform/v1/runs/{run_id}/report/export?format=json"
        )
        assert export_response.status_code == 200
        assert "passed" in export_response.text


def _canonical_json(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
