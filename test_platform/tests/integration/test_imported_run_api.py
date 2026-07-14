from __future__ import annotations

import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _legacy_run(tmp_path):
    root = tmp_path / "external-runs" / "20260706_legacy"
    root.mkdir(parents=True)
    (root / "meta.json").write_text(
        json.dumps(
            {
                "start_time": "2026-07-06T00:00:00.000Z",
                "agent": "legacy-agent",
                "model_name": "legacy-model",
                "repeat_n": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    result = {
        "id": "wechat.OpenBlacklist",
        "task_name": "Open blacklist",
        "suite": "wechat",
        "apps": ["wechat"],
        "trial_id": 0,
        "max_steps": 30,
        "execution": {"steps": 3, "runtime_s": 1.25, "stop_reason": "COMPLETE"},
        "judge": {"success": True, "clean": True, "progress": 1.0},
        "is_success": True,
        "is_error": False,
        "progress": 1.0,
    }
    (root / "results.jsonl").write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    (root / "summary.json").write_text(
        json.dumps(
            {
                "start_time": "2026-07-06T00:00:00.000Z",
                "end_time": "2026-07-06T00:00:10.000Z",
                "success": 1,
                "failed": 0,
                "error": 0,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    trajectory = root / "trajectory" / "wechat_OpenBlacklist"
    trajectory.mkdir(parents=True)
    (trajectory / "trace.json").write_text('{"ok": true}', encoding="utf-8")
    return root


def test_imported_run_api_preserves_source_and_blocks_strict_baseline(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        project = client.post("/api/platform/v1/projects", json={"name": "Imported"}).json()
        source = _legacy_run(tmp_path)
        before = (source / "results.jsonl").read_bytes()

        response = client.post(
            "/api/platform/v1/runs/import",
            json={
                "project_id": project["id"],
                "source_path": str(source),
                "name": "Imported CLI run",
            },
        )

        assert response.status_code == 201
        imported = response.json()
        assert imported["state"] == "completed"
        assert imported["imported"]["source_path"] == str(source.resolve())
        assert imported["imported"]["provenance_missing"] == [
            "workflow",
            "target_revision",
            "task_source",
        ]
        assert (source / "results.jsonl").read_bytes() == before
        assert (settings.runs_dir / imported["id"]).is_symlink()

        detail_response = client.get(f"/api/platform/v1/runs/{imported['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["execution_identity"] == {
            "kind": "legacy",
            "label": "Legacy Execution Identity",
            "schema_version": 1,
        }
        assert detail["run_plan"]["imported"]["source_name"] == "20260706_legacy"
        assert detail["lane_attempts"][0]["artifact_root"] == "."
        assert detail["episode_attempts"][0]["outcome"] == "PASS"

        version = client.get(
            f"/api/platform/v1/workflow-versions/{detail['workflow_version_id']}"
        )
        workflows = client.get(
            f"/api/platform/v1/projects/{project['id']}/workflows"
        )
        assert version.status_code == 200
        assert version.json()["definition"]["imported"]["source_name"] == (
            "20260706_legacy"
        )
        assert workflows.status_code == 200
        assert workflows.json()["items"][0]["latest_version"]["id"] == (
            detail["workflow_version_id"]
        )

        artifacts = client.get(f"/api/platform/v1/runs/{imported['id']}/artifacts")
        assert artifacts.status_code == 200
        assert artifacts.json()["items"][0]["relative_path"] == "trajectory/wechat_OpenBlacklist/trace.json"

        report = client.get(f"/api/platform/v1/runs/{imported['id']}/report")
        assert report.status_code == 200
        assert report.json()["provenance"]["imported"]["source_name"] == "20260706_legacy"

        eligibility = client.get(
            f"/api/platform/v1/runs/{imported['id']}/baseline/eligibility"
        )
        baseline = client.post(
            f"/api/platform/v1/runs/{imported['id']}/baseline",
            json={"display_name": "Imported baseline"},
        )
        assert eligibility.status_code == 200
        assert eligibility.json()["eligible"] is False
        assert "STRICT_PROVENANCE_INCOMPLETE" in [
            reason["code"] for reason in eligibility.json()["reasons"]
        ]
        assert baseline.status_code == 409
        assert baseline.json()["error"]["code"] == "BASELINE_PROMOTION_INELIGIBLE"


def test_import_run_rejects_unknown_project(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        source = _legacy_run(tmp_path)
        response = client.post(
            "/api/platform/v1/runs/import",
            json={"project_id": "missing", "source_path": str(source)},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
