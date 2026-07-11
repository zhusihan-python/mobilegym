"""Integration tests for the model-compatibility endpoint.

These tests use ``FakeCompatibilityProbe`` for route wiring and secret
redaction. The production adapter behavior is validated separately in
``test_platform/tests/unit/test_model_compatibility.py`` via MockTransport.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.testing.fake_compat import FakeCompatibilityProbe

_SENTINEL = "sk-sentinel-secret-12345"


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _app(tmp_path, *, probe=None):
    settings = _settings(tmp_path)
    return create_app(settings, compatibility_probe=probe or FakeCompatibilityProbe())


class TestCompatibilityEndpoint:
    def test_default_composition_wires_production_probe(self, tmp_path: Path):
        """create_app(settings) without injecting a probe must construct a real
        OpenAICompatibilityProbe (not return 503). Verified via isinstance +
        monkeypatched check — no live network access."""
        from test_platform.adapters.model_compatibility import OpenAICompatibilityProbe

        settings = _settings(tmp_path)
        app = create_app(settings)  # no compatibility_probe kwarg

        # The default probe is a real production adapter instance.
        assert isinstance(app.state.compatibility_probe, OpenAICompatibilityProbe)

        # Monkeypatch its check() to return a fixed result, then verify the
        # endpoint wiring works end-to-end without any network call.
        from test_platform.domain.model_compatibility import CompatibilityResult

        app.state.compatibility_probe.check = lambda **kw: CompatibilityResult(
            code="compatible",
            explanation="wired",
            latency_ms=0,
            checked_model=kw["model"],
            checked_image_format=kw["image_url_format"],
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "test-model",
                    "image_url_format": "data_url",
                    "timeout_seconds": 15,
                },
            )
        assert response.status_code == 200
        assert response.json()["code"] == "compatible"

    def test_runtime_dependencies_importable(self):
        """The production adapter's direct dependencies (openai, httpx, Pillow)
        must be importable — they are declared in runtime requirements."""
        import httpx  # noqa: F401
        import openai  # noqa: F401
        from PIL import Image  # noqa: F401

        from test_platform.adapters.model_compatibility import (
            OpenAICompatibilityProbe as _Probe,
        )

        # Constructing the probe should not raise ModuleNotFoundError.
        _Probe()

    def test_compatible_success(self, tmp_path: Path):
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "vision-model",
                    "model_api_key": "sk-test",
                    "image_url_format": "data_url",
                    "timeout_seconds": 15,
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "compatible"
        assert body["checked_model"] == "vision-model"
        assert body["checked_image_format"] == "data_url"
        assert "latency_ms" in body
        assert "explanation" in body

    def test_authentication_failure(self, tmp_path: Path):
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "test#auth",
                    "image_url_format": "data_url",
                },
            )
        assert response.status_code == 200
        assert response.json()["code"] == "authentication_failure"

    def test_response_does_not_contain_api_key(self, tmp_path: Path):
        """Sentinel secret must not appear in any response body."""
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "vision-model",
                    "model_api_key": _SENTINEL,
                    "image_url_format": "data_url",
                },
            )
        assert response.status_code == 200
        assert _SENTINEL not in response.text

    def test_422_missing_model_name_does_not_leak_key(self, tmp_path: Path):
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_api_key": _SENTINEL,
                    "image_url_format": "data_url",
                },
            )
        assert response.status_code == 422
        assert _SENTINEL not in response.text
        # Sanitized: only loc + type, no input
        details = response.json()["error"]["details"]
        for detail in details:
            assert "input" not in detail
            assert "ctx" not in detail

    def test_422_invalid_url_does_not_leak_key(self, tmp_path: Path):
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "not-a-url",
                    "model_name": "model",
                    "model_api_key": _SENTINEL,
                },
            )
        assert response.status_code == 422
        assert _SENTINEL not in response.text

    def test_422_userinfo_url_does_not_leak_key(self, tmp_path: Path):
        """URL with userinfo (http://user:key@host) must be rejected without
        echoing the URL (which contains the sentinel)."""
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": f"http://user:{_SENTINEL}@provider.invalid/v1",
                    "model_name": "model",
                },
            )
        assert response.status_code == 422
        assert _SENTINEL not in response.text

    def test_422_timeout_out_of_range(self, tmp_path: Path):
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "model",
                    "timeout_seconds": 100,
                },
            )
        assert response.status_code == 422

    def test_422_extra_field_rejected(self, tmp_path: Path):
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "model",
                    "unexpected_field": "value",
                },
            )
        assert response.status_code == 422

    def test_422_empty_model_name(self, tmp_path: Path):
        with TestClient(_app(tmp_path)) as client:
            response = client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "   ",
                },
            )
        assert response.status_code == 422

    def test_log_does_not_contain_api_key(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.INFO):
            with TestClient(_app(tmp_path)) as client:
                client.post(
                    "/api/platform/v1/model-compatibility/check",
                    json={
                        "model_base_url": "http://provider.invalid/v1",
                        "model_name": "vision-model",
                        "model_api_key": _SENTINEL,
                        "image_url_format": "data_url",
                    },
                )
        full_log = "\n".join(record.getMessage() for record in caplog.records)
        assert _SENTINEL not in full_log

    def test_no_persistence_side_effects(self, tmp_path: Path):
        """Compatibility check must not create any runs, events, artifacts,
        or other persisted records. Verified by table counts before/after."""
        app = _app(tmp_path)
        with TestClient(app) as client:
            db = app.state.database
            tables = ["runs", "run_attempts", "lane_attempts", "episode_attempts",
                      "episodes", "artifacts", "reports", "comparison_pairs"]

            def _counts():
                return {
                    t: db.connection.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                    for t in tables
                    if _table_exists(db, t)
                }

            before = _counts()
            # Run a compatible check.
            client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "vision-model",
                    "image_url_format": "data_url",
                },
            )
            # Run a failure check.
            client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "test#auth",
                    "image_url_format": "data_url",
                },
            )
            # Run a 422 check.
            client.post(
                "/api/platform/v1/model-compatibility/check",
                json={"model_base_url": "bad", "model_name": "model"},
            )
            after = _counts()

        assert before == after, f"table counts changed: {before} -> {after}"

    def test_sentinel_not_in_database(self, tmp_path: Path):
        """The sentinel secret must not appear anywhere in the SQLite file."""
        app = _app(tmp_path)
        with TestClient(app) as client:
            client.post(
                "/api/platform/v1/model-compatibility/check",
                json={
                    "model_base_url": "http://provider.invalid/v1",
                    "model_name": "vision-model",
                    "model_api_key": _SENTINEL,
                    "image_url_format": "data_url",
                },
            )
            db_path = app.state.settings.database_path
            # Read raw SQLite text and search for sentinel.
            conn = sqlite3.connect(str(db_path))
            try:
                result = conn.execute(
                    "SELECT group_concat(name) FROM sqlite_master WHERE type='table'"
                ).fetchone()
                tables = [t for t in (result[0] or "").split(",") if t]
                for table in tables:
                    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                    for row in rows:
                        for cell in row:
                            if isinstance(cell, str) and _SENTINEL in cell:
                                conn.close()
                                raise AssertionError(
                                    f"sentinel found in table {table}: {cell}"
                                )
            finally:
                conn.close()


def _table_exists(db, table_name: str) -> bool:
    row = db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None
