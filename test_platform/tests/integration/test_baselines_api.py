import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.tests.integration.test_execution_profile_followup import (
    _launch_completed_execution_comparison,
    _profile_aware_app,
)
from test_platform.tests.integration.test_reports_api import (
    _make_completed_reportable_run,
    _settings,
)


def test_profile_aware_baseline_freezes_selected_lane_strict_provenance(tmp_path):
    app = _profile_aware_app(tmp_path)

    with TestClient(app) as client:
        run, _target_revision, _profile_revisions = (
            _launch_completed_execution_comparison(
                client,
                idempotency_key="tp-ep08-baseline-provenance",
            )
        )
        report = client.get(f"/api/platform/v1/runs/{run['id']}/report").json()
        selected_binding = next(
            binding
            for binding in report["provenance"]["execution_identity"][
                "lane_bindings"
            ]
            if binding["lane_slot"] == "baseline"
        )
        expected_preflight = [
            check
            for check in report["provenance"]["completed_run_attempt"][
                "compatibility_preflight"
            ]
            if "baseline" in check["lane_keys"]
        ]

        eligibility = client.get(
            f"/api/platform/v1/runs/{run['id']}"
            "/baseline/eligibility?lane_key=baseline"
        )
        promoted = client.post(
            f"/api/platform/v1/runs/{run['id']}/baseline",
            json={
                "display_name": "Profile-aware baseline",
                "lane_key": "baseline",
            },
        )

        assert eligibility.status_code == 200
        assert eligibility.json()["eligible"] is True
        assert eligibility.json()["report_id"] == report["id"]
        assert promoted.status_code == 201
        baseline = promoted.json()
        assert baseline["strict_provenance"] == {
            "kind": "profile_aware",
            "version": 1,
            "execution_profile_revision_id": selected_binding[
                "execution_profile_revision_id"
            ],
            "execution_profile_revision_hash": selected_binding[
                "execution_profile_revision_hash"
            ],
            "lane_fingerprint": selected_binding["lane_fingerprint"],
            "run_attempt_id": report["run_attempt_id"],
        }

        profiles = client.get(
            f"/api/platform/v1/projects/{run['project_id']}/execution-profiles"
        ).json()["items"]
        for profile in profiles:
            archived = client.post(
                f"/api/platform/v1/projects/{run['project_id']}"
                f"/execution-profiles/{profile['id']}/archive",
                json={
                    "expected_draft_version": profile["draft_version"],
                    "expected_head_revision_id": profile["head_revision"]["id"],
                },
            )
            assert archived.status_code == 200

        listed = client.get(
            f"/api/platform/v1/projects/{run['project_id']}/baselines"
        )
        detail = client.get(f"/api/platform/v1/baselines/{baseline['id']}")

    assert listed.status_code == 200
    assert listed.json()["items"] == [baseline]
    assert detail.status_code == 200
    assert detail.json()["baseline"] == baseline
    assert detail.json()["compatibility_preflight"] == expected_preflight


def test_legacy_report_stays_readable_but_cannot_be_newly_promoted(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        database = client.app.state.database
        run_id = _make_completed_reportable_run(client)
        database.connection.execute(
            "UPDATE episode_attempts SET outcome = 'PASS' WHERE id = 'ea_base_ep0'"
        )
        database.connection.execute(
            """
            INSERT INTO episode_attempts (
              id, episode_id, lane_attempt_id, attempt_no, state, outcome,
              error_code, result_json, artifact_root, started_at, ended_at, created_at
            )
            VALUES ('ea_base_ep1', 'ep1', 'attempt3_baseline', 1, 'completed',
                    'PASS', NULL, '{"is_success":true}', 'artifacts/base1',
                    '2026-07-17T00:00:00.000Z', '2026-07-17T00:00:01.000Z',
                    '2026-07-17T00:00:01.000Z')
            """
        )
        database.connection.commit()

        report_response = client.get(f"/api/platform/v1/runs/{run_id}/report")
        export_response = client.get(
            f"/api/platform/v1/runs/{run_id}/report/export"
        )
        eligibility = client.get(
            f"/api/platform/v1/runs/{run_id}"
            "/baseline/eligibility?lane_key=baseline"
        )
        promoted = client.post(
            f"/api/platform/v1/runs/{run_id}/baseline",
            json={"display_name": "Rejected legacy", "lane_key": "baseline"},
        )

        report = report_response.json()
        database.connection.execute(
            """
            INSERT INTO baselines (
              id, report_id, run_id, project_id, workflow_version_id,
              run_plan_hash, task_source_digest, target_revision_ids_json,
              lane_key, target_revision_id, display_name, name_key, created_at
            )
            VALUES (
              'legacy-baseline-ep08', ?, ?, 'proj1', 'wv1', 'sha256:plan',
              'sha256:catalog-vs11',
              '{"baseline":"trev_base","candidate":"trev_cand"}',
              'baseline', 'trev_base', 'Historical baseline',
              'historical baseline', '2026-07-17T00:00:02.000Z'
            )
            """,
            (report["id"], run_id),
        )
        database.connection.commit()

        listed = client.get("/api/platform/v1/projects/proj1/baselines")
        detail = client.get("/api/platform/v1/baselines/legacy-baseline-ep08")

    assert report_response.status_code == 200
    assert report["schema_version"] == 2
    assert "execution_identity" not in report["provenance"]
    assert export_response.status_code == 200
    assert export_response.json() == report
    assert eligibility.status_code == 200
    assert eligibility.json()["eligible"] is False
    assert eligibility.json()["reasons"] == [
        {
            "code": "PROFILE_AWARE_STRICT_PROVENANCE_REQUIRED",
            "message": (
                "Only a profile-aware report can be promoted to a new strict "
                "baseline."
            ),
            "details": {"report_schema_version": 2},
        }
    ]
    assert promoted.status_code == 409
    assert promoted.json()["error"]["code"] == "BASELINE_PROMOTION_INELIGIBLE"
    legacy = listed.json()["items"][0]
    assert legacy["strict_provenance"] == {"kind": "legacy", "version": None}
    assert detail.status_code == 200
    assert detail.json()["baseline"] == legacy
    assert detail.json()["compatibility_preflight"] == []


def test_profile_aware_baseline_requires_complete_selected_lane_provenance(tmp_path):
    app = _profile_aware_app(tmp_path)

    with TestClient(app) as client:
        run, _target_revision, _profile_revisions = (
            _launch_completed_execution_comparison(
                client,
                idempotency_key="tp-ep08-incomplete-baseline-provenance",
            )
        )
        report = client.get(f"/api/platform/v1/runs/{run['id']}/report").json()
        report["provenance"]["execution_profile_revision_hashes"].pop(
            "baseline"
        )
        report["provenance"]["lane_fingerprints"].pop("baseline")
        next(
            binding
            for binding in report["provenance"]["execution_identity"][
                "lane_bindings"
            ]
            if binding["lane_slot"] == "baseline"
        ).pop("execution_profile_revision_id")
        report["provenance"]["completed_run_attempt"][
            "compatibility_preflight"
        ] = [
            check
            for check in report["provenance"]["completed_run_attempt"][
                "compatibility_preflight"
            ]
            if "baseline" not in check["lane_keys"]
        ]
        client.app.state.database.connection.execute(
            "UPDATE reports SET report_json = ? WHERE id = ?",
            (json.dumps(report, sort_keys=True), report["id"]),
        )
        client.app.state.database.connection.commit()

        eligibility = client.get(
            f"/api/platform/v1/runs/{run['id']}"
            "/baseline/eligibility?lane_key=baseline"
        )

    assert eligibility.status_code == 200
    assert eligibility.json()["eligible"] is False
    assert eligibility.json()["reasons"] == [
        {
            "code": "STRICT_PROVENANCE_INCOMPLETE",
            "message": "Strict baseline provenance is incomplete.",
            "details": {
                "missing": [
                    "execution_profile_revision_hashes.baseline",
                    "lane_fingerprints.baseline",
                    (
                        "execution_identity.lane_bindings.baseline."
                        "execution_profile_revision_id"
                    ),
                    "completed_run_attempt.compatibility_preflight.baseline",
                ]
            },
        }
    ]
