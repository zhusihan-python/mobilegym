from __future__ import annotations

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.tests.integration.test_report_input import _seed_reportable_paired_run


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def test_artifact_api_indexes_and_serves_contained_files(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_reportable_paired_run(client.app.state.database)
        artifact = settings.runs_dir / run_id / "artifacts" / "cand0" / "trace.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"ok": true}', encoding="utf-8")

        list_response = client.get(f"/api/platform/v1/runs/{run_id}/artifacts")

        assert list_response.status_code == 200
        items = list_response.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["run_id"] == run_id
        assert item["episode_attempt_id"] == "ea_cand_ep0"
        assert item["relative_path"] == "artifacts/cand0/trace.json"
        assert item["media_type"] == "application/json"
        assert item["size_bytes"] == len('{"ok": true}')

        content_response = client.get(
            f"/api/platform/v1/runs/{run_id}/artifacts/{item['id']}/content"
        )

        assert content_response.status_code == 200
        assert content_response.headers["content-type"].startswith("application/json")
        assert content_response.text == '{"ok": true}'
