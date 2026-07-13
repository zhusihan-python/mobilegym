from __future__ import annotations

import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.execution.event_writer import EventWriter
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
        assert payload["schema_version"] == 2
        assert payload["run_id"] == run_id
        assert payload["summary"]["total"] == 3
        assert {item["code"] for item in payload["items"]} == {
            "EXECUTION_ERROR",
            "CANDIDATE_ERROR",
            "QUALITY_GATE_FAILED",
        }
        assert all(isinstance(item["artifacts"], list) for item in payload["items"])
        assert payload["next_cursor"] is None
        by_code = {item["code"]: item for item in payload["items"]}
        assert by_code["EXECUTION_ERROR"]["target_id"] == "target_cand"
        assert by_code["EXECUTION_ERROR"]["task_id"] == "fake.Task"
        assert by_code["EXECUTION_ERROR"]["lane_key"] == "candidate"
        assert by_code["EXECUTION_ERROR"]["episode_key"] == "fake.Task::0"
        assert by_code["CANDIDATE_ERROR"]["comparison_id"] == "cmp1"
        assert by_code["CANDIDATE_ERROR"]["comparison_pair_id"] == "pair_row_1"
        assert by_code["CANDIDATE_ERROR"]["pair_key"] == "pair0"
        assert by_code["CANDIDATE_ERROR"]["candidate_episode_attempt_id"] == (
            "ea_cand_ep0"
        )
        assert by_code["QUALITY_GATE_FAILED"]["gate_result_id"] is not None
        assert by_code["QUALITY_GATE_FAILED"]["report_id"] is not None

        persisted = database.connection.execute(
            "SELECT COUNT(*) AS n FROM diagnostics WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert persisted["n"] == 3


def test_diagnostics_api_filters_normalized_event_identity_and_artifact_facts(
    tmp_path,
):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _seed_reportable_paired_run(database)
        event = EventWriter(database).emit(
            run_id,
            "diagnostic.browser",
            {
                "code": "BROWSER_REQUEST_FAILED",
                "message": "connection refused",
                "phase": "browser.network",
                "task_id": "fake.Task",
                "episode_key": "fake.Task::0",
                "step": 3,
                "app_ids": ["fake"],
            },
            run_attempt_id="attempt3",
            lane_id="lane_cand",
            lane_attempt_id="attempt3_candidate",
            episode_id="ep0",
            worker_id="W2",
            entity_type="diagnostic",
            entity_id="BROWSER_REQUEST_FAILED",
        )
        assert event is not None
        database.connection.execute(
            """
            INSERT INTO artifacts (
              id, run_id, run_attempt_id, lane_attempt_id, episode_attempt_id,
              kind, relative_path, media_type, size_bytes, sha256, created_at
            )
            VALUES ('artifact-browser-log', ?, 'attempt3', 'attempt3_candidate',
                    'ea_cand_ep0', 'log', 'private/browser_W2.log', 'text/plain',
                    12, 'sha256:browser-log', '2026-07-13T00:00:00.000Z')
            """,
            (run_id,),
        )
        database.connection.commit()

        response = client.get(
            f"/api/platform/v1/runs/{run_id}/diagnostics"
            "?category=network&severity=error&target_id=target_cand"
            "&app_id=fake&task_id=fake.Task&retryable=true"
            "&lane_key=candidate&episode_key=fake.Task%3A%3A0&attempt_no=3"
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["schema_version"] == 2
        assert payload["summary"] == {
            "total": 1,
            "by_category": {"network": 1},
            "by_severity": {"error": 1},
        }
        assert payload["next_cursor"] is None
        assert payload["items"] == [
            {
                "id": payload["items"][0]["id"],
                "source_event_id": event.id,
                "code": "BROWSER_REQUEST_FAILED",
                "category": "network",
                "phase": "browser.network",
                "severity": "error",
                "retryable": True,
                "message": "connection refused",
                "recommended_action": "Inspect the browser log and retry the request.",
                "entity_type": "diagnostic_event",
                "scope": "episode",
                "run_id": run_id,
                "run_attempt_id": "attempt3",
                "run_attempt_no": 3,
                "lane_id": "lane_cand",
                "lane_attempt_id": "attempt3_candidate",
                "lane_key": "candidate",
                "target_id": "target_cand",
                "episode_id": "ep0",
                "episode_attempt_id": "ea_cand_ep0",
                "episode_key": "fake.Task::0",
                "episode_attempt_no": 1,
                "worker_id": "W2",
                "step": 3,
                "task_id": "fake.Task",
                "app_ids": ["fake"],
                "artifacts": [
                    {
                        "id": "artifact-browser-log",
                        "kind": "log",
                        "media_type": "text/plain",
                        "href": (
                            f"/api/platform/v1/runs/{run_id}/artifacts/"
                            "artifact-browser-log/content"
                        ),
                    }
                ],
            }
        ]
        assert "private/browser_W2.log" not in response.text

        first_page = client.get(
            f"/api/platform/v1/runs/{run_id}/diagnostics?limit=2"
        ).json()
        repeated_first_page = client.get(
            f"/api/platform/v1/runs/{run_id}/diagnostics?limit=2"
        ).json()
        assert first_page == repeated_first_page
        assert len(first_page["items"]) == 2
        assert first_page["next_cursor"] is not None

        second_page = client.get(
            f"/api/platform/v1/runs/{run_id}/diagnostics?limit=2"
            f"&cursor={first_page['next_cursor']}"
        ).json()
        assert len(second_page["items"]) == 1
        assert second_page["next_cursor"] is None
        assert {item["id"] for item in first_page["items"]}.isdisjoint(
            item["id"] for item in second_page["items"]
        )

        database.connection.execute(
            """
            INSERT INTO episode_attempts (
              id, episode_id, lane_attempt_id, attempt_no, state, outcome,
              error_code, result_json, artifact_root, started_at, ended_at, created_at
            )
            SELECT 'ea_cand_ep0_retry', episode_id, lane_attempt_id, 2, state, outcome,
                   error_code, result_json, artifact_root, started_at, ended_at,
                   '2026-07-13T00:00:01.000Z'
            FROM episode_attempts
            WHERE id = 'ea_cand_ep0'
            """
        )
        database.connection.commit()

        ambiguous_attempt = client.get(
            f"/api/platform/v1/runs/{run_id}/diagnostics"
            "?category=network&task_id=fake.Task"
        ).json()["items"][0]
        assert ambiguous_attempt["source_event_id"] == event.id
        assert ambiguous_attempt["episode_id"] == "ep0"
        assert ambiguous_attempt["episode_attempt_id"] is None
        assert ambiguous_attempt["episode_attempt_no"] is None
        assert ambiguous_attempt["scope"] == "run"
