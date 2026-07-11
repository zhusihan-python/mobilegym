"""Integration tests for compatibility preflight gating on run creation.

These tests verify that an incompatible endpoint blocks run creation with zero
persistent side effects, while a compatible endpoint creates the run with a
redacted compatibility summary in provenance.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.services.runs import FakeRunSupervisor, _RUN_SECRET_STORE
from test_platform.services.compatibility_preflight import CompatibilityPreflight
from test_platform.testing.fake_compat import FakeCompatibilityProbe

from test_runs_api import FakeRegistry

_SENTINEL = "sk-sentinel-preflight"


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _run_dir_snapshot(runs_dir: Path) -> list[tuple[str, bool]]:
    """Snapshot the runs directory as a sorted list of (relative_path, is_dir).

    Includes directories so that a newly created empty artifact root is detected.
    """
    if not runs_dir.exists():
        return []
    return sorted(
        (str(p.relative_to(runs_dir)), p.is_dir())
        for p in runs_dir.rglob("*")
    )


def _published_version(client, agent="generic_v2"):
    project = client.post("/api/platform/v1/projects", json={"name": "P"}).json()
    target = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": "T",
            "config": {
                "kind": "simulator",
                "connection": {"env_url": "http://sim.invalid"},
                "device_profile": {
                    "name": "Pixel",
                    "viewport_width": 393,
                    "viewport_height": 852,
                    "physical_width": 1080,
                    "physical_height": 2400,
                    "device_scale_factor": 2.75,
                },
                "runtime": {},
                "labels": {},
            },
        },
    ).json()
    client.post(f"/api/platform/v1/targets/{target['id']}/health")
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "W",
            "definition": {
                "schema_version": 1,
                "name": "W",
                "nodes": [
                    {
                        "id": "tasks",
                        "type": "task_selection",
                        "depends_on": [],
                        "config": {"task_ids": [], "sample_n": 1},
                    },
                    {
                        "id": "matrix",
                        "type": "matrix",
                        "depends_on": ["tasks"],
                        "config": {
                            "lanes": {"candidate": {"target_id": target["id"]}},
                            "repeat_n": 1,
                        },
                    },
                    {
                        "id": "execute",
                        "type": "execute",
                        "depends_on": ["matrix"],
                        "config": {"agent": agent, "parallel": 1},
                    },
                ],
            },
        },
    )
    published = client.post(f"/api/platform/v1/workflows/{workflow.json()['id']}/publish")
    assert published.status_code == 200
    return published.json()["version"]


def _execution_overrides(model_name="vision-model"):
    return {
        "agent": "generic_v2",
        "model_base_url": "http://provider.invalid/v1",
        "model_name": model_name,
        "image_url_format": "data_url",
    }


class TestPreflightBlocksIncompatible:
    def test_incompatible_run_returns_409_and_creates_nothing(self, tmp_path: Path):
        """A vision-rejecting endpoint must block creation with zero side effects."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            db = app.state.database
            runs_dir = app.state.settings.runs_dir

            def _counts():
                return {
                    t: db.connection.execute(
                        f"SELECT COUNT(*) AS n FROM {t}"
                    ).fetchone()["n"]
                    for t in ("runs", "run_attempts", "lanes", "lane_attempts", "episodes", "events")
                }

            def _run_dirs():
                return _run_dir_snapshot(runs_dir)

            before = _counts()
            before_dirs = _run_dirs()
            response = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "preflight-block"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "blocked",
                    "overrides": {"seed": 1, "execution": _execution_overrides("test#vision")},
                },
            )
            after = _counts()
            after_dirs = _run_dirs()

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RUN_COMPATIBILITY_CHECK_FAILED"
        # Zero persistent side effects.
        assert before == after, f"tables changed: {before} -> {after}"
        assert before_dirs == after_dirs, "artifact root changed after blocked create"
        # No new secret store entries.
        assert len(_RUN_SECRET_STORE._by_run_id) == 0, "secret store has entries after blocked create"

    def test_compatible_run_creates_with_provenance(self, tmp_path: Path):
        """A compatible endpoint creates the run with a redacted compatibility summary."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            response = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "preflight-ok"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "ok",
                    "overrides": {
                        "seed": 1,
                        "execution": {
                            **_execution_overrides("vision-model"),
                            "model_api_key": _SENTINEL,
                        },
                    },
                },
            )
        assert response.status_code == 201
        run_plan = response.json()["run_plan"]
        compat = run_plan["agent"].get("compatibility")
        assert compat is not None
        assert compat["checks"][0]["outcome"] == "passed"
        assert compat["checks"][0]["code"] == "compatible"
        # Provenance must not contain the api key.
        assert _SENTINEL not in json.dumps(compat)
        assert _SENTINEL not in json.dumps(run_plan)

    def test_skip_creates_run_with_skipped_provenance(self, tmp_path: Path):
        """An explicit skip creates the run with a 'skipped' compatibility summary."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            response = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "preflight-skip"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "skip",
                    "overrides": {"seed": 1, "execution": _execution_overrides("test#vision")},
                    "skip_compatibility_check": True,
                },
            )
        assert response.status_code == 201
        compat = response.json()["run_plan"]["agent"]["compatibility"]
        assert compat["checks"][0]["outcome"] == "skipped"

    def test_non_screenshot_agent_skips_preflight(self, tmp_path: Path):
        """A non-screenshot agent (autoglm) must not trigger preflight."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client, agent="autoglm")
            response = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "preflight-autoglm"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "autoglm",
                    "overrides": {
                        "seed": 1,
                        "execution": {
                            "agent": "autoglm",
                            "model_base_url": "http://provider.invalid/v1",
                            "model_name": "glm-5v-turbo",
                            "image_url_format": "data_url",
                        },
                    },
                },
            )
        assert response.status_code == 201
        # autoglm is not gated — compatibility summary reflects "not required".
        compat = response.json()["run_plan"]["agent"]["compatibility"]
        assert compat["checks"][0]["outcome"] == "passed"
        assert compat["checks"][0]["code"] is None

    def test_provenance_excluded_from_fingerprint(self, tmp_path: Path):
        """The compatibility summary must not affect the fingerprint."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            r1 = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "fp-1"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "fp1",
                    "overrides": {"seed": 1, "execution": _execution_overrides("vision-model")},
                },
            )
            r2 = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "fp-2"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "fp2",
                    "overrides": {"seed": 1, "execution": _execution_overrides("vision-model")},
                },
            )
        fp1 = r1.json()["fingerprint"]
        fp2 = r2.json()["fingerprint"]
        assert fp1 == fp2, "fingerprint changed despite identical config (compatibility summary leaked?)"


class TestRetryPreflight:
    """Retry/resume preflight: skip provenance + secret ordering."""

    def _create_completed_run(self, client, version):
        """Create a compatible run and mark it completed so it can be retried."""
        resp = client.post(
            "/api/platform/v1/runs",
            headers={"Idempotency-Key": "retry-setup"},
            json={
                "workflow_version_id": version["id"],
                "name": "retryable",
                "overrides": {"seed": 1, "execution": _execution_overrides("vision-model")},
            },
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def _force_failed_with_episode(self, app, run_id):
        """Force a run into 'failed' state with one failed episode attempt,
        so retry has something to select."""
        db = app.state.database
        db.connection.execute(
            "UPDATE runs SET state = 'failed' WHERE id = ?", (run_id,)
        )
        db.connection.execute(
            "UPDATE run_attempts SET state = 'failed' WHERE run_id = ?", (run_id,)
        )
        # Create a failed episode attempt so retry can select it.
        lane_attempt = db.connection.execute(
            """
            SELECT la.id FROM lane_attempts AS la
            JOIN lanes ON lanes.id = la.lane_id
            WHERE lanes.run_id = ? LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        episode = db.connection.execute(
            "SELECT id FROM episodes WHERE run_id = ? LIMIT 1", (run_id,)
        ).fetchone()
        if lane_attempt and episode:
            db.connection.execute(
                """
                INSERT INTO episode_attempts (
                  id, episode_id, lane_attempt_id, attempt_no, state,
                  outcome, error_code, result_json, artifact_root,
                  started_at, ended_at, created_at
                )
                VALUES (?, ?, ?, 1, 'completed', 'FAIL', 'ASSERTION_FAILURE',
                        '{}', '', '2026-07-12T00:00:00Z', '2026-07-12T00:00:00Z',
                        '2026-07-12T00:00:00Z')
                """,
                (
                    f"ea-{run_id[:8]}",
                    episode["id"],
                    lane_attempt["id"],
                ),
            )
        db.connection.commit()

    def test_retry_skip_creates_attempt_with_provenance(self, tmp_path: Path):
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            run_id = self._create_completed_run(client, version)
            self._force_failed_with_episode(app, run_id)

            response = client.post(
                f"/api/platform/v1/runs/{run_id}/retry",
                json={
                    "skip_compatibility_check": True,
                },
            )
            assert response.status_code == 202
            # The new run attempt must have compatibility provenance persisted.
            db = app.state.database
            row = db.connection.execute(
                "SELECT compatibility_json FROM run_attempts WHERE run_id = ? ORDER BY attempt_no DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            assert row is not None
            assert row["compatibility_json"] is not None
            provenance = json.loads(row["compatibility_json"])
            assert any(p["outcome"] == "skipped" for p in provenance)

    def test_retry_with_new_key_registers_after_preflight(self, tmp_path: Path):
        """Verify the secret is registered AFTER preflight passes, not before."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            run_id = self._create_completed_run(client, version)
            self._force_failed_with_episode(app, run_id)

            response = client.post(
                f"/api/platform/v1/runs/{run_id}/retry",
                json={
                    "execution": {"model_api_key": "sk-retry-new-key"},
                    "skip_compatibility_check": True,
                },
            )
        assert response.status_code == 202
        # The new key was registered (preflight passed → secret stored).
        secrets = _RUN_SECRET_STORE.get(run_id) or {}
        assert secrets.get("model_api_key") == "sk-retry-new-key"

    def test_incompatible_retry_blocks_with_zero_side_effects(self, tmp_path: Path):
        """An incompatible endpoint at retry time must block with zero new
        attempts, events, artifacts, and no change to the secret store."""
        from test_platform.domain.model_compatibility import CompatibilityResult

        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            run_id = self._create_completed_run(client, version)
            self._force_failed_with_episode(app, run_id)

            # Register a baseline secret to verify it's not overwritten.
            _RUN_SECRET_STORE.register(run_id, {"model_api_key": "sk-original"})

            db = app.state.database
            before_attempts = db.connection.execute(
                "SELECT COUNT(*) AS n FROM run_attempts WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            before_events = db.connection.execute(
                "SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            before_secrets = copy.deepcopy(_RUN_SECRET_STORE._by_run_id)
            runs_dir = app.state.settings.runs_dir
            before_artifacts = _run_dir_snapshot(runs_dir)

            # Monkeypatch the probe to return incompatible for this retry.
            preflight = app.state.compatibility_preflight
            with preflight._lock:
                preflight._cache.clear()
            original_check = preflight._probe.check
            preflight._probe.check = lambda **kw: CompatibilityResult(
                code="unsupported_vision",
                explanation="Vision rejected.",
                latency_ms=5,
                checked_model=kw.get("model", ""),
                checked_image_format=kw.get("image_url_format", ""),
            )

            response = client.post(
                f"/api/platform/v1/runs/{run_id}/retry",
                json={"execution": {"model_api_key": "sk-candidate"}},
            )

            # Restore.
            preflight._probe.check = original_check

            after_attempts = db.connection.execute(
                "SELECT COUNT(*) AS n FROM run_attempts WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            after_events = db.connection.execute(
                "SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            after_artifacts = _run_dir_snapshot(runs_dir)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RUN_COMPATIBILITY_CHECK_FAILED"
        # Zero new attempts, events, and artifacts.
        assert after_attempts == before_attempts, "new run_attempt created on blocked retry"
        assert after_events == before_events, "new event created on blocked retry"
        assert after_artifacts == before_artifacts, "artifact root changed on blocked retry"
        # Secret store unchanged — candidate key was NOT registered.
        assert copy.deepcopy(_RUN_SECRET_STORE._by_run_id) == before_secrets
        assert (_RUN_SECRET_STORE.get(run_id) or {}).get("model_api_key") == "sk-original"

    def test_retry_provenance_visible_in_run_detail(self, tmp_path: Path):
        """Follow-up compatibility provenance must be visible via GET /runs/{id}."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            run_id = self._create_completed_run(client, version)
            self._force_failed_with_episode(app, run_id)

            client.post(
                f"/api/platform/v1/runs/{run_id}/retry",
                json={"skip_compatibility_check": True},
            )

            # Provenance must be visible in the public run detail API.
            detail = client.get(f"/api/platform/v1/runs/{run_id}").json()
            attempts = detail["run_attempts"]
            # The retry attempt (attempt_no=2) must have compatibility provenance.
            retry_attempt = next(a for a in attempts if a["attempt_no"] == 2)
            assert retry_attempt["compatibility"] is not None
            assert any(c["outcome"] == "skipped" for c in retry_attempt["compatibility"])
            # The raw storage column must NOT leak into the public API.
            assert "compatibility_json" not in retry_attempt


class TestResumePreflight:
    def test_incompatible_resume_blocks_with_zero_side_effects(self, tmp_path: Path):
        """An incompatible endpoint at resume time must block with zero new
        attempts, events, artifacts, and no change to the secret store."""
        from test_platform.domain.model_compatibility import CompatibilityResult

        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_version(client)
            run_id = self._create_completed_run_for_resume(client, version)
            self._force_failed_for_resume(app, run_id)

            _RUN_SECRET_STORE.register(run_id, {"model_api_key": "sk-original"})

            db = app.state.database
            before_attempts = db.connection.execute(
                "SELECT COUNT(*) AS n FROM run_attempts WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            before_events = db.connection.execute(
                "SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            before_secrets = copy.deepcopy(_RUN_SECRET_STORE._by_run_id)
            runs_dir = app.state.settings.runs_dir
            before_artifacts = _run_dir_snapshot(runs_dir)

            preflight = app.state.compatibility_preflight
            with preflight._lock:
                preflight._cache.clear()
            original_check = preflight._probe.check
            preflight._probe.check = lambda **kw: CompatibilityResult(
                code="unsupported_vision",
                explanation="Vision rejected.",
                latency_ms=5,
                checked_model=kw.get("model", ""),
                checked_image_format=kw.get("image_url_format", ""),
            )

            response = client.post(
                f"/api/platform/v1/runs/{run_id}/resume",
                json={"execution": {"model_api_key": "sk-candidate"}},
            )
            preflight._probe.check = original_check

            after_attempts = db.connection.execute(
                "SELECT COUNT(*) AS n FROM run_attempts WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            after_events = db.connection.execute(
                "SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            after_artifacts = _run_dir_snapshot(runs_dir)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RUN_COMPATIBILITY_CHECK_FAILED"
        assert after_attempts == before_attempts
        assert after_events == before_events
        assert after_artifacts == before_artifacts, "artifact root changed on blocked resume"
        assert copy.deepcopy(_RUN_SECRET_STORE._by_run_id) == before_secrets

    @staticmethod
    def _create_completed_run_for_resume(client, version):
        resp = client.post(
            "/api/platform/v1/runs",
            headers={"Idempotency-Key": "resume-setup"},
            json={
                "workflow_version_id": version["id"],
                "name": "resumable",
                "overrides": {"seed": 1, "execution": _execution_overrides("vision-model")},
            },
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    @staticmethod
    def _force_failed_for_resume(app, run_id):
        db = app.state.database
        db.connection.execute(
            "UPDATE runs SET state = 'failed' WHERE id = ?", (run_id,)
        )
        db.connection.execute(
            "UPDATE run_attempts SET state = 'failed' WHERE run_id = ?", (run_id,)
        )
        db.connection.commit()


def _published_paired_version(client, agent="generic_v2", *, baseline_model=None, candidate_model=None):
    """Publish a 2-lane (baseline + candidate) workflow for multi-lane tests.

    If baseline_model/candidate_model are set, they override the execute node's
    model_name per lane (testing distinct effective configs).
    """
    project = client.post("/api/platform/v1/projects", json={"name": "P"}).json()
    target = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": "T",
            "config": {
                "kind": "simulator",
                "connection": {"env_url": "http://sim.invalid"},
                "device_profile": {
                    "name": "Pixel", "viewport_width": 393, "viewport_height": 852,
                    "physical_width": 1080, "physical_height": 2400, "device_scale_factor": 2.75,
                },
                "runtime": {}, "labels": {},
            },
        },
    ).json()
    client.post(f"/api/platform/v1/targets/{target['id']}/health")

    baseline_lane = {"target_id": target["id"], "role": "baseline"}
    candidate_lane = {"target_id": target["id"]}
    if baseline_model:
        baseline_lane["model_name"] = baseline_model
    if candidate_model:
        candidate_lane["model_name"] = candidate_model

    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "W",
            "definition": {
                "schema_version": 1,
                "name": "W",
                "nodes": [
                    {"id": "tasks", "type": "task_selection", "depends_on": [],
                     "config": {"task_ids": [], "sample_n": 1}},
                    {"id": "matrix", "type": "matrix", "depends_on": ["tasks"],
                     "config": {
                         "lanes": {
                             "baseline": baseline_lane,
                             "candidate": candidate_lane,
                         },
                         "repeat_n": 1,
                     }},
                    {"id": "execute", "type": "execute", "depends_on": ["matrix"],
                     "config": {"agent": agent, "parallel": 1, "model_base_url": "http://p.invalid/v1", "image_url_format": "data_url"}},
                    {"id": "compare", "type": "compare", "depends_on": ["execute"], "config": {}},
                ],
            },
        },
    )
    published = client.post(f"/api/platform/v1/workflows/{workflow.json()['id']}/publish")
    assert published.status_code == 200
    return published.json()["version"]


class TestMultiLanePreflight:
    def test_same_config_lanes_merge_into_one_check(self, tmp_path: Path):
        """Two lanes with identical config must produce one check with both lane_keys."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            version = _published_paired_version(client)
            response = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "multi-same"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "multi-same",
                    "overrides": {"seed": 1, "execution": _execution_overrides("vision-model")},
                },
            )
        assert response.status_code == 201
        checks = response.json()["run_plan"]["agent"]["compatibility"]["checks"]
        assert len(checks) == 1, f"expected 1 check for identical configs, got {len(checks)}"
        assert set(checks[0]["lane_keys"]) == {"baseline", "candidate"}

    def test_distinct_config_second_incompatible_blocks_with_zero_side_effects(self, tmp_path: Path):
        """When baseline uses a compatible model and candidate uses an incompatible
        one, the candidate's check must fail and block the entire creation with
        zero persistent side effects. Proves the second DISTINCT config is checked."""
        app = create_app(
            _settings(tmp_path),
            adapter_registry=FakeRegistry(),
            supervisor=FakeRunSupervisor(),
            compatibility_probe=FakeCompatibilityProbe(),
        )
        with TestClient(app) as client:
            # baseline model is compatible; candidate model is vision-rejecting.
            version = _published_paired_version(
                client,
                baseline_model="vision-model",
                candidate_model="test#vision",
            )
            db = app.state.database
            runs_dir = app.state.settings.runs_dir

            def _counts():
                return {
                    t: db.connection.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                    for t in ("runs", "run_attempts", "lanes", "lane_attempts", "episodes", "events")
                }

            before = _counts()
            before_dirs = _run_dir_snapshot(runs_dir)

            response = client.post(
                "/api/platform/v1/runs",
                headers={"Idempotency-Key": "multi-distinct-incompat"},
                json={
                    "workflow_version_id": version["id"],
                    "name": "multi-distinct",
                    "overrides": {"seed": 1, "execution": {
                        "agent": "generic_v2",
                        "model_base_url": "http://p.invalid/v1",
                        "model_name": "vision-model",
                        "image_url_format": "data_url",
                    }},
                },
            )
            after = _counts()
            after_dirs = _run_dir_snapshot(runs_dir)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RUN_COMPATIBILITY_CHECK_FAILED"
        # Error details must point to the candidate lane (the incompatible one).
        details = response.json()["error"]["details"][0]
        assert "candidate" in details.get("lane_keys", [])
        # Zero persistent side effects.
        assert before == after, f"tables changed: {before} -> {after}"
        assert before_dirs == after_dirs, "artifact root changed"
