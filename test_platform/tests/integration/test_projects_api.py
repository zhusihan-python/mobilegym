from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def test_projects_api_creates_lists_gets_and_archives_projects(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        created = client.post(
            "/api/platform/v1/projects",
            json={"name": "Mobile App Regression"},
        )
        assert created.status_code == 201
        project = created.json()
        assert project["name"] == "Mobile App Regression"
        assert project["slug"] == "mobile-app-regression"
        assert project["archived_at"] is None

        listing = client.get("/api/platform/v1/projects")
        assert listing.status_code == 200
        assert listing.json()["items"] == [project]

        detail = client.get(f"/api/platform/v1/projects/{project['id']}")
        assert detail.status_code == 200
        assert detail.json() == project

        archived = client.post(f"/api/platform/v1/projects/{project['id']}/archive")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        hidden_listing = client.get("/api/platform/v1/projects")
        assert hidden_listing.status_code == 200
        assert hidden_listing.json()["items"] == []


def test_projects_api_rejects_duplicate_active_names(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        first = client.post(
            "/api/platform/v1/projects",
            json={"name": "Mobile App Regression"},
        )
        assert first.status_code == 201

        duplicate = client.post(
            "/api/platform/v1/projects",
            json={"name": " mobile app regression "},
        )

    assert duplicate.status_code == 409
    payload = duplicate.json()
    assert payload["error"]["code"] == "PROJECT_NAME_EXISTS"
    assert "already exists" in payload["error"]["message"]
