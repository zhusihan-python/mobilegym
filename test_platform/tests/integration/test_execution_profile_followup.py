from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.persistence.database import Database
from test_platform.services.runs import _RUN_SECRET_STORE
from test_platform.testing.deterministic import (
    build_deterministic_executor_resolver,
    build_deterministic_target_registry,
)
from test_platform.testing.fake_compat import FakeCompatibilityProbe
from test_platform.tests.integration.test_execution_comparison_launch import (
    FakeRegistry,
    _profile_spec,
    _seed_execution_comparison,
    _settings,
    _wait_for_run_state,
)


class MutableTargetRegistry(FakeRegistry):
    def __init__(self):
        self.data_revision = "seed-v1"

    def check_health(self, config):
        result = super().check_health(config)
        result["metadata"]["data"]["revision"] = self.data_revision
        return result


class MutableCompatibilityProbe(FakeCompatibilityProbe):
    def __init__(self):
        self.available = True

    def check(self, **kwargs):
        if self.available:
            return super().check(**kwargs)
        return super().check(
            **{
                **kwargs,
                "model": f"{kwargs['model']}#missing",
            }
        )


def _profile_aware_app(
    tmp_path,
    *,
    adapter_registry=None,
    compatibility_probe=None,
):
    settings = _settings(tmp_path)
    database = Database(settings)
    return create_app(
        settings,
        database=database,
        adapter_registry=(
            adapter_registry or build_deterministic_target_registry()
        ),
        executor_resolver=build_deterministic_executor_resolver(
            database,
            settings,
            enabled=True,
        ),
        compatibility_probe=compatibility_probe or FakeCompatibilityProbe(),
    )


def _launch_completed_execution_comparison(client, *, idempotency_key: str):
    project, target_revision, profile_revisions, command = (
        _seed_execution_comparison(
            client,
            profile_model_names=(
                "deterministic-profile-pass",
                "deterministic-profile-fail",
            ),
        )
    )
    launch_preview = client.post(
        f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
        json=command,
    ).json()
    created = client.post(
        f"/api/platform/v1/projects/{project['id']}/run-launch",
        json={
            **command,
            "preview_token": launch_preview["preview_token"],
        },
        headers={"Idempotency-Key": idempotency_key},
    )
    assert created.status_code == 201
    run = _wait_for_run_state(client, created.json()["id"], "completed")
    return run, target_revision, profile_revisions


def _launch_credential_bound_execution_comparison(
    client,
    *,
    idempotency_key: str,
):
    project, _target_revision, _profile_revisions, command = (
        _seed_execution_comparison(
            client,
            profile_model_names=(
                "deterministic-profile-pass",
                "deterministic-profile-fail",
            ),
        )
    )
    credential_spec = _profile_spec("deterministic-profile-fail")
    credential_spec["credentials"] = {"required_slots": ["model_api_key"]}
    credential_profile = client.post(
        f"/api/platform/v1/projects/{project['id']}/execution-profiles",
        json={
            "name": "Credential-bound candidate",
            "draft_spec": credential_spec,
            "credential_bindings": [
                {
                    "slot": "model_api_key",
                    "project_id": project["id"],
                    "backend": "request",
                    "reference_id": "candidate-model-key",
                    "private_locator": "request://transient/candidate-model-key",
                }
            ],
        },
    ).json()
    credential_revision = client.post(
        f"/api/platform/v1/projects/{project['id']}"
        f"/execution-profiles/{credential_profile['id']}/publish"
    ).json()
    command["lane_bindings"][1]["execution_profile_revision_id"] = (
        credential_revision["id"]
    )
    launch_preview = client.post(
        f"/api/platform/v1/projects/{project['id']}/run-launch/preview",
        json=command,
    ).json()
    created = client.post(
        f"/api/platform/v1/projects/{project['id']}/run-launch",
        json={
            **command,
            "preview_token": launch_preview["preview_token"],
            "secret_bindings": {"model_api_key": "sk-tp-ep07-transient"},
        },
        headers={"Idempotency-Key": idempotency_key},
    )
    assert created.status_code == 201
    return _wait_for_run_state(client, created.json()["id"], "completed")


def test_profile_aware_retry_preview_exposes_original_frozen_lane_bindings(tmp_path):
    app = _profile_aware_app(tmp_path)

    with TestClient(app) as client:
        run, target_revision, profile_revisions = (
            _launch_completed_execution_comparison(
                client,
                idempotency_key="tp-ep07-frozen-preview-identity",
            )
        )

        retry_preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/retry/preview"
        )
        resume_preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/resume/preview"
        )

    assert retry_preview.status_code == 200
    preview = retry_preview.json()
    assert preview["execution_identity"] == run["execution_identity"]
    assert resume_preview.status_code == 200
    assert resume_preview.json()["execution_identity"] == run["execution_identity"]
    assert {
        binding["target_revision_id"]
        for binding in preview["execution_identity"]["lane_bindings"]
    } == {target_revision["id"]}
    assert {
        binding["execution_profile_revision_id"]
        for binding in preview["execution_identity"]["lane_bindings"]
    } == {revision["id"] for revision in profile_revisions}
    assert preview["selected_lane_episodes"] == [
        {
            "episode_key": preview["selected_lane_episodes"][0]["episode_key"],
            "lane_key": "candidate",
            "reason": "retry_failed",
        }
    ]


def test_profile_aware_retry_rejects_lane_binding_override_without_new_attempt(
    tmp_path,
):
    app = _profile_aware_app(tmp_path)

    with TestClient(app) as client:
        run, _target_revision, _profile_revisions = (
            _launch_completed_execution_comparison(
                client,
                idempotency_key="tp-ep07-reject-binding-override",
            )
        )
        preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/retry/preview"
        ).json()

        rejected = client.post(
            f"/api/platform/v1/runs/{run['id']}/retry",
            json={
                "preview_token": preview["preview_token"],
                "target_revision_id": "target-revision-substitution",
            },
        )
        reloaded = client.get(f"/api/platform/v1/runs/{run['id']}").json()

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert rejected.json()["error"]["details"] == [
        {
            "loc": ["body", "target_revision_id"],
            "type": "extra_forbidden",
        }
    ]
    assert len(reloaded["run_attempts"]) == len(run["run_attempts"])


def test_profile_aware_retry_rejects_stale_target_without_new_attempt(tmp_path):
    adapter_registry = MutableTargetRegistry()
    app = _profile_aware_app(tmp_path, adapter_registry=adapter_registry)

    with TestClient(app) as client:
        run, _target_revision, _profile_revisions = (
            _launch_completed_execution_comparison(
                client,
                idempotency_key="tp-ep07-reject-stale-target",
            )
        )
        target_id = run["lanes"][0]["target_id"]
        adapter_registry.data_revision = "seed-v2"
        newer_revision = client.post(
            f"/api/platform/v1/targets/{target_id}/health"
        ).json()["revision"]
        preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/retry/preview"
        ).json()

        rejected = client.post(
            f"/api/platform/v1/runs/{run['id']}/retry",
            json={"preview_token": preview["preview_token"]},
        )
        reloaded = client.get(f"/api/platform/v1/runs/{run['id']}").json()

    assert rejected.status_code == 409
    error = rejected.json()["error"]
    assert error["code"] == "RUN_RETRY_INCOMPATIBLE_REVISION"
    assert error["details"] == [
        {
            "kind": "target_revision",
            "lane_key": "baseline",
            "target_id": target_id,
            "expected_revision_id": run["lanes"][0]["target_revision_id"],
            "current_revision_id": newer_revision["id"],
        },
        {
            "kind": "target_revision",
            "lane_key": "candidate",
            "target_id": target_id,
            "expected_revision_id": run["lanes"][1]["target_revision_id"],
            "current_revision_id": newer_revision["id"],
        },
    ]
    assert len(reloaded["run_attempts"]) == len(run["run_attempts"])


def test_profile_aware_retry_requires_original_credential_without_new_attempt(
    tmp_path,
):
    app = _profile_aware_app(tmp_path)

    with TestClient(app) as client:
        run = _launch_credential_bound_execution_comparison(
            client,
            idempotency_key="tp-ep07-missing-original-credential",
        )
        _RUN_SECRET_STORE.discard(run["id"])
        preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/retry/preview"
        ).json()

        rejected = client.post(
            f"/api/platform/v1/runs/{run['id']}/retry",
            json={"preview_token": preview["preview_token"]},
        )
        reloaded = client.get(f"/api/platform/v1/runs/{run['id']}").json()

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "RUN_EXECUTION_SECRET_MISSING"
    assert rejected.json()["error"]["details"] == [
        {"field": "execution.model_api_key"}
    ]
    assert len(reloaded["run_attempts"]) == len(run["run_attempts"])


def test_profile_aware_retry_rejects_undeclared_credential_without_new_attempt(
    tmp_path,
):
    app = _profile_aware_app(tmp_path)

    with TestClient(app) as client:
        run, _target_revision, _profile_revisions = (
            _launch_completed_execution_comparison(
                client,
                idempotency_key="tp-ep07-reject-undeclared-credential",
            )
        )
        preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/retry/preview"
        ).json()

        rejected = client.post(
            f"/api/platform/v1/runs/{run['id']}/retry",
            json={
                "preview_token": preview["preview_token"],
                "execution": {"model_api_key": "sk-not-declared"},
            },
        )
        reloaded = client.get(f"/api/platform/v1/runs/{run['id']}").json()

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "RUN_FOLLOWUP_CONFIG_INVALID"
    assert rejected.json()["error"]["details"] == [
        {"field": "execution.model_api_key"}
    ]
    assert len(reloaded["run_attempts"]) == len(run["run_attempts"])


def test_profile_aware_retry_uses_frozen_revision_after_new_head_and_archive(
    tmp_path,
):
    app = _profile_aware_app(tmp_path)

    with TestClient(app) as client:
        run, _target_revision, _profile_revisions = (
            _launch_completed_execution_comparison(
                client,
                idempotency_key="tp-ep07-new-head-archive",
            )
        )
        frozen_identity = run["execution_identity"]
        candidate = next(
            binding
            for binding in frozen_identity["lane_bindings"]
            if binding["lane_slot"] == "candidate"
        )
        profile = client.get(
            f"/api/platform/v1/projects/{run['project_id']}"
            f"/execution-profiles/{candidate['execution_profile_id']}"
        ).json()
        next_spec = profile["draft_spec"]
        next_spec["model"]["name"] = "new-head-must-not-run"
        updated = client.patch(
            f"/api/platform/v1/projects/{run['project_id']}"
            f"/execution-profiles/{profile['id']}/draft",
            json={
                "draft_spec": next_spec,
                "expected_draft_version": profile["draft_version"],
            },
        ).json()
        revision_two = client.post(
            f"/api/platform/v1/projects/{run['project_id']}"
            f"/execution-profiles/{profile['id']}/publish",
            json={
                "expected_draft_version": updated["draft_version"],
                "expected_head_revision_id": candidate[
                    "execution_profile_revision_id"
                ],
            },
        ).json()
        archived = client.post(
            f"/api/platform/v1/projects/{run['project_id']}"
            f"/execution-profiles/{profile['id']}/archive",
            json={
                "expected_draft_version": updated["draft_version"],
                "expected_head_revision_id": revision_two["id"],
            },
        )
        assert archived.status_code == 200

        preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/retry/preview"
        ).json()
        queued = client.post(
            f"/api/platform/v1/runs/{run['id']}/retry",
            json={"preview_token": preview["preview_token"]},
        )
        reloaded = client.get(f"/api/platform/v1/runs/{run['id']}").json()

    assert queued.status_code == 202
    assert preview["execution_identity"] == frozen_identity
    assert reloaded["execution_identity"] == frozen_identity
    assert reloaded["episode_identities"] == run["episode_identities"]
    assert revision_two["id"] not in str(preview)
    retry_attempt = next(
        attempt
        for attempt in reloaded["run_attempts"]
        if attempt["attempt_no"] == queued.json()["attempt_no"]
    )
    assert retry_attempt["compatibility"] is not None
    assert {
        lane_key
        for result in retry_attempt["compatibility"]
        for lane_key in result["lane_keys"]
    } == {"baseline", "candidate"}


def test_profile_aware_retry_preflight_failure_creates_no_attempt(tmp_path):
    compatibility_probe = MutableCompatibilityProbe()
    app = _profile_aware_app(
        tmp_path,
        compatibility_probe=compatibility_probe,
    )

    with TestClient(app) as client:
        run = _launch_credential_bound_execution_comparison(
            client,
            idempotency_key="tp-ep07-unavailable-frozen-subject",
        )
        compatibility_probe.available = False
        preview = client.get(
            f"/api/platform/v1/runs/{run['id']}/retry/preview"
        ).json()

        rejected = client.post(
            f"/api/platform/v1/runs/{run['id']}/retry",
            json={
                "preview_token": preview["preview_token"],
                "execution": {"model_api_key": "sk-tp-ep07-transient"},
            },
        )
        reloaded = client.get(f"/api/platform/v1/runs/{run['id']}").json()

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "RUN_COMPATIBILITY_CHECK_FAILED"
    assert rejected.json()["error"]["details"][0]["code"] == "missing_model"
    assert len(reloaded["run_attempts"]) == len(run["run_attempts"])
