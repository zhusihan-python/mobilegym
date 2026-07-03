from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def test_ready_reports_false_before_database_initialization_and_true_after_lifespan(
    tmp_path,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    app = create_app(settings, database=database)

    cold_client = TestClient(app)
    cold_response = cold_client.get("/health/ready")

    assert cold_response.status_code == 503
    cold_payload = cold_response.json()
    assert cold_payload["ready"] is False
    assert cold_payload["checks"]["database"]["ready"] is False
    assert "initialize" in cold_payload["checks"]["database"]["message"].lower()

    with TestClient(app) as client:
        live_response = client.get("/health/live")
        ready_response = client.get("/health/ready")

    assert live_response.status_code == 200
    assert live_response.json() == {"live": True}
    assert ready_response.status_code == 200
    ready_payload = ready_response.json()
    assert ready_payload["ready"] is True
    assert ready_payload["checks"]["database"]["ready"] is True
    assert ready_payload["checks"]["migrations"]["ready"] is True
    assert ready_payload["checks"]["runs_dir"]["ready"] is True
