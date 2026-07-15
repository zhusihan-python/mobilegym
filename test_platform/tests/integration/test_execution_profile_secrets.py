from dataclasses import asdict, dataclass
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.domain.model_compatibility import (
    COMPATIBLE,
    INDETERMINATE,
    UNSUPPORTED_VISION,
    CompatibilityResult,
)
from test_platform.domain.execution_profiles import (
    CredentialReferenceBindingInput,
    ExecutionProfileDomainError,
    PublishProfile,
    SaveProfileDraft,
    UpdateProfileDraft,
)
from test_platform.domain.execution_secrets import SecretResolutionError
from test_platform.domain.run_launch import (
    CreateRunLaunch,
    LaneBindingInput,
    PreviewRunLaunch,
)
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    ProjectRepository,
    TargetRepository,
    WorkflowRepository,
)
from test_platform.services.compatibility_preflight import CompatibilityPreflight
from test_platform.services.execution_profiles import ExecutionProfiles
from test_platform.services.run_launch import RunLaunch
from test_platform.services.runs import FakeRunSupervisor


def _profile_spec() -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent": {"id": "generic_v2"},
        "model": {
            "protocol": "openai_chat_completions",
            "base_url": "http://127.0.0.1:1234/v1",
            "name": "credential-model",
        },
        "image_input": {"format": "data_url"},
        "generation": {
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 4096,
            "stream": True,
        },
        "inference": {"timeout_seconds": 300},
        "credentials": {"required_slots": ["model_api_key"]},
    }


def _catalog() -> TaskCatalogSnapshot:
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="git-tp-ep03",
        digest="sha256:catalog-tp-ep03",
        items=[
            TaskCatalogItem(
                task_base_id="account.Railway12306ChangePassword",
                suite="account",
                class_name="Railway12306ChangePassword",
                apps=["railway12306"],
                templates=["Complete the credential-bound task."],
                parameters={},
                difficulty="L1",
                scope="S1",
                objective="operate",
                composition="atomic",
                capabilities=[],
                max_steps=15,
                answer_fields=False,
                optimal_path_lengths=[],
            )
        ],
    )


@dataclass(frozen=True)
class _PreparedCredentialLaunch:
    database: Database
    settings: PlatformSettings
    project_id: str
    workflow_version_id: str
    target_revision_id: str
    profile_revision_id: str


def _prepare_credential_launch(tmp_path) -> _PreparedCredentialLaunch:
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    project = ProjectRepository(database).create("Credential-bound launch")
    workflow_repository = WorkflowRepository(database)
    workflow = workflow_repository.create(
        project_id=project.id,
        name="Credential-bound Single",
        definition={
            "schema_version": 2,
            "name": "Credential-bound Single",
            "nodes": [
                {
                    "id": "tasks",
                    "type": "task_selection",
                    "depends_on": [],
                    "config": {
                        "task_ids": ["account.Railway12306ChangePassword"],
                        "sample_n": 1,
                    },
                },
                {
                    "id": "slots",
                    "type": "matrix",
                    "depends_on": ["tasks"],
                    "config": {
                        "lane_slots": {"candidate": {"role": "candidate"}},
                        "repeat_n": 1,
                    },
                },
                {
                    "id": "execute",
                    "type": "execute",
                    "depends_on": ["slots"],
                    "config": {
                        "parallel": 1,
                        "processes": 1,
                        "eval_mode": "grounded",
                        "judge_mode": "rule",
                    },
                },
            ],
        },
    )
    workflow_version = workflow_repository.publish(workflow.id)
    target_repository = TargetRepository(database)
    target = target_repository.create(
        project_id=project.id,
        name="Credential simulator",
        config={
            "kind": "simulator",
            "connection": {"env_url": "http://127.0.0.1:5173"},
            "device_profile": {
                "name": "Pixel 7",
                "viewport_width": 393,
                "viewport_height": 852,
                "physical_width": 1080,
                "physical_height": 2400,
                "device_scale_factor": 2.75,
            },
            "runtime": {},
        },
    )
    target_revision = target_repository.record_revision(
        target_id=target.id,
        metadata={
            "schema_version": 1,
            "data": {"revision": "credential-v1"},
            "resolved_at": "2026-07-15T00:00:00.000Z",
        },
        warnings=[],
        health_status="healthy",
    )
    profiles = ExecutionProfiles(database)
    profile = profiles.save_draft(
        SaveProfileDraft(
            project_id=project.id,
            name="Credential-bound generic v2",
            draft_spec=_profile_spec(),
            credential_bindings=(
                CredentialReferenceBindingInput(
                    slot="model_api_key",
                    project_id=project.id,
                    backend="request",
                    reference_id="primary-model-key",
                    private_locator="request://transient/model-api-key",
                ),
            ),
        )
    )
    profile_revision = profiles.publish(
        PublishProfile(
            project_id=project.id,
            execution_profile_id=profile.id,
        )
    )
    return _PreparedCredentialLaunch(
        database=database,
        settings=settings,
        project_id=project.id,
        workflow_version_id=workflow_version.id,
        target_revision_id=target_revision.id,
        profile_revision_id=profile_revision.id,
    )


def _preview_command(fixture: _PreparedCredentialLaunch) -> PreviewRunLaunch:
    return PreviewRunLaunch(
        project_id=fixture.project_id,
        workflow_version_id=fixture.workflow_version_id,
        name="Credential-bound Single",
        seed=20260715,
        comparison_intent="single",
        lane_bindings=(
            LaneBindingInput(
                lane_slot="candidate",
                target_revision_id=fixture.target_revision_id,
                execution_profile_revision_id=fixture.profile_revision_id,
            ),
        ),
    )


class _RecordingSecretResolver:
    def __init__(self) -> None:
        self.calls = []

    def resolve(self, requirements, supplied_bindings):
        self.calls.append((requirements, dict(supplied_bindings)))
        return SimpleNamespace(values=dict(supplied_bindings))


class _CompatibleProbe:
    def __init__(self) -> None:
        self.calls = []

    def check(self, **kwargs):
        self.calls.append(kwargs)
        return CompatibilityResult(
            code=COMPATIBLE,
            explanation="provider response body must stay private",
            latency_ms=12,
            checked_model=kwargs["model"],
            checked_image_format=kwargs["image_url_format"],
        )


class _VariableCompatibleProbe:
    def __init__(self) -> None:
        self.calls = []

    def check(self, **kwargs):
        self.calls.append(kwargs)
        return CompatibilityResult(
            code=COMPATIBLE,
            explanation="provider text is not provenance",
            latency_ms=len(self.calls) * 10,
            checked_model=kwargs["model"],
            checked_image_format=kwargs["image_url_format"],
        )


class _IncompatibleProbe:
    def __init__(self, code: str, provider_text: str) -> None:
        self.code = code
        self.provider_text = provider_text

    def check(self, **kwargs):
        return CompatibilityResult(
            code=self.code,
            explanation=self.provider_text,
            latency_ms=31,
            checked_model=self.provider_text,
            checked_image_format=self.provider_text,
        )


class _UnavailableSecretResolver:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.calls = []

    def resolve(self, requirements, supplied_bindings):
        self.calls.append((requirements, dict(supplied_bindings)))
        raise SecretResolutionError(
            "RUN_EXECUTION_SECRET_UNAVAILABLE",
            f"provider failed for {self.secret}",
            status_code=503,
            details=[
                {
                    "slot": "model_api_key",
                    "private_locator": requirements[0].private_locator,
                    "provider_text": self.secret,
                }
            ],
        )


def test_profile_revision_publishes_private_credential_reference_as_redacted_identity(
    tmp_path,
):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        project = ProjectRepository(database).create("Credential publication")
        profiles = ExecutionProfiles(database)
        private_locator = "local://sensitive/provider/model-key"
        draft = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="Credential-bound generic v2",
                draft_spec=_profile_spec(),
                credential_bindings=(
                    CredentialReferenceBindingInput(
                        slot="model_api_key",
                        project_id=project.id,
                        backend="request",
                        reference_id="primary-model-key",
                        private_locator=private_locator,
                    ),
                ),
            )
        )

        assert draft.credential_readiness.ready is True
        assert draft.credential_readiness.required_slots == ["model_api_key"]
        assert draft.credential_readiness.bound_slots == ["model_api_key"]
        assert private_locator not in draft.model_dump_json()

        revision = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
            )
        )
        reloaded = profiles.get_revision(
            project_id=project.id,
            revision_id=revision.id,
        )

        assert revision.credential_readiness.ready is True
        assert revision.credential_binding_digest.startswith("sha256:")
        assert reloaded.credential_binding_digest == revision.credential_binding_digest
        assert private_locator not in revision.model_dump_json()
        assert private_locator not in reloaded.model_dump_json()
    finally:
        database.close()


def test_profile_credential_binding_is_accepted_over_http_but_never_returned(
    tmp_path,
):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    app = create_app(settings)
    private_locator = "request://transient/model-api-key"

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Credential publication API"},
        ).json()
        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={
                "name": "Credential-bound generic v2",
                "draft_spec": _profile_spec(),
                "credential_bindings": [
                    {
                        "slot": "model_api_key",
                        "project_id": project["id"],
                        "backend": "request",
                        "reference_id": "primary-model-key",
                        "private_locator": private_locator,
                    }
                ],
            },
        )

        assert response.status_code == 201
        assert response.json()["credential_readiness"] == {
            "required_slots": ["model_api_key"],
            "bound_slots": ["model_api_key"],
            "missing_slots": [],
            "ready": True,
            "binding_digest": response.json()["credential_readiness"][
                "binding_digest"
            ],
        }
        assert private_locator not in response.text

        published = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{response.json()['id']}/publish"
        )

    assert published.status_code == 200
    assert published.json()["credential_readiness"]["ready"] is True
    assert private_locator not in published.text


def test_profile_publication_rejects_missing_credential_reference_binding(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        project = ProjectRepository(database).create("Missing credential binding")
        profiles = ExecutionProfiles(database)
        draft = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="Incomplete credential profile",
                draft_spec=_profile_spec(),
            )
        )

        assert draft.credential_readiness.ready is False
        assert draft.credential_readiness.missing_slots == ["model_api_key"]
        with pytest.raises(ExecutionProfileDomainError) as captured:
            profiles.publish(
                PublishProfile(
                    project_id=project.id,
                    execution_profile_id=draft.id,
                )
            )

        assert captured.value.code == (
            "EXECUTION_PROFILE_CREDENTIAL_BINDING_MISSING"
        )
        assert draft.head_revision is None
    finally:
        database.close()


def test_profile_draft_rejects_unsupported_credential_backend_without_leakage(
    tmp_path,
):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        project = ProjectRepository(database).create("Unsupported backend")
        profiles = ExecutionProfiles(database)
        backend = "provider-vault-sentinel"
        private_locator = "vault://secret-locator-sentinel"

        with pytest.raises(ExecutionProfileDomainError) as captured:
            profiles.save_draft(
                SaveProfileDraft(
                    project_id=project.id,
                    name="Unsupported credential backend",
                    draft_spec=_profile_spec(),
                    credential_bindings=(
                        CredentialReferenceBindingInput(
                            slot="model_api_key",
                            project_id=project.id,
                            backend=backend,
                            reference_id="primary-model-key",
                            private_locator=private_locator,
                        ),
                    ),
                )
            )

        public_error = json.dumps(
            {
                "code": captured.value.code,
                "message": captured.value.message,
                "details": captured.value.details,
            }
        )
        assert captured.value.code == (
            "EXECUTION_PROFILE_CREDENTIAL_BACKEND_UNSUPPORTED"
        )
        assert backend not in public_error
        assert private_locator not in public_error
        assert profiles.list(project_id=project.id) == []
    finally:
        database.close()


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("extra", "EXECUTION_PROFILE_CREDENTIAL_BINDING_EXTRA"),
        ("cross_project", "EXECUTION_PROFILE_CREDENTIAL_BINDING_CROSS_PROJECT"),
    ],
)
def test_profile_rejects_extra_or_cross_project_credential_reference_without_leakage(
    tmp_path,
    case,
    expected_code,
):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    app = create_app(settings)
    private_locator = "local://private/provider/model-key"

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Credential binding validation"},
        ).json()
        other_project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Other credential owner"},
        ).json()
        spec = _profile_spec()
        if case == "extra":
            spec["credentials"] = {"required_slots": []}
        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={
                "name": "Invalid credential binding",
                "draft_spec": spec,
                "credential_bindings": [
                    {
                        "slot": "model_api_key",
                        "project_id": (
                            other_project["id"]
                            if case == "cross_project"
                            else project["id"]
                        ),
                        "backend": "request",
                        "reference_id": "primary-model-key",
                        "private_locator": private_locator,
                    }
                ],
            },
        )
        listed = client.get(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles"
        )

    assert response.status_code in {400, 409}
    assert response.json()["error"]["code"] == expected_code
    assert private_locator not in response.text
    assert listed.json()["items"] == []


def test_credential_reference_identity_changes_revision_binding_digest(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        project = ProjectRepository(database).create("Credential identity")
        profiles = ExecutionProfiles(database)
        revisions = []
        for name, reference_id in (
            ("Primary credential", "primary-model-key"),
            ("Secondary credential", "secondary-model-key"),
        ):
            draft = profiles.save_draft(
                SaveProfileDraft(
                    project_id=project.id,
                    name=name,
                    draft_spec=_profile_spec(),
                    credential_bindings=(
                        CredentialReferenceBindingInput(
                            slot="model_api_key",
                            project_id=project.id,
                            backend="request",
                            reference_id=reference_id,
                            private_locator="request://transient/model-api-key",
                        ),
                    ),
                )
            )
            revisions.append(
                profiles.publish(
                    PublishProfile(
                        project_id=project.id,
                        execution_profile_id=draft.id,
                    )
                )
            )

        assert revisions[0].public_spec_hash == revisions[1].public_spec_hash
        assert revisions[0].credential_binding_digest != (
            revisions[1].credential_binding_digest
        )
    finally:
        database.close()


def test_updating_public_draft_fields_preserves_private_binding_when_omitted(
    tmp_path,
):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        project = ProjectRepository(database).create("Credential draft update")
        profiles = ExecutionProfiles(database)
        draft = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="Credential draft",
                draft_spec=_profile_spec(),
                credential_bindings=(
                    CredentialReferenceBindingInput(
                        slot="model_api_key",
                        project_id=project.id,
                        backend="request",
                        reference_id="primary-model-key",
                        private_locator="request://transient/model-api-key",
                    ),
                ),
            )
        )
        changed_spec = _profile_spec()
        changed_spec["model"] = {
            **changed_spec["model"],
            "name": "credential-model-v2",
        }

        updated = profiles.update_draft(
            UpdateProfileDraft(
                project_id=project.id,
                execution_profile_id=draft.id,
                draft_spec=changed_spec,
            )
        )
        revision = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
            )
        )

        assert updated.credential_readiness.ready is True
        assert updated.credential_readiness.binding_digest == (
            draft.credential_readiness.binding_digest
        )
        assert revision.credential_binding_digest == (
            draft.credential_readiness.binding_digest
        )
    finally:
        database.close()


def test_credential_bound_launch_resolves_preflights_and_records_redacted_attempt(
    tmp_path,
):
    fixture = _prepare_credential_launch(tmp_path)
    resolver = _RecordingSecretResolver()
    probe = _CompatibleProbe()
    supervisor = FakeRunSupervisor()
    secret = "sk-tp-ep03-transient-sentinel"
    try:
        launch = RunLaunch(
            fixture.database,
            settings=fixture.settings,
            supervisor=supervisor,
            catalog_builder=_catalog,
            secret_resolver=resolver,
            compatibility_preflight=CompatibilityPreflight(probe),
        )
        preview_command = _preview_command(fixture)
        preview = launch.preview(preview_command)

        assert preview.credential_requirements == ["model_api_key"]

        run = launch.create(
            CreateRunLaunch(**preview_command.__dict__),
            expected_preview_token=preview.preview_token,
            secret_bindings={"model_api_key": secret},
            idempotency_key="tp-ep03-compatible-credential",
        )

        requirements, supplied = resolver.calls[0]
        assert [requirement.slot for requirement in requirements] == [
            "model_api_key"
        ]
        assert requirements[0].reference_id == "primary-model-key"
        assert supplied == {"model_api_key": secret}
        assert probe.calls == [
            {
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": secret,
                "model": "credential-model",
                "image_url_format": "data_url",
                "timeout_seconds": 300.0,
            }
        ]
        assert run.run_attempts[0]["compatibility"] == [
            {
                "outcome": "passed",
                "code": "compatible",
                "explanation": "The model accepted the screenshot request.",
                "latency_ms": 12,
                "cached": False,
                "checked_model": "credential-model",
                "checked_image_format": "data_url",
                "lane_keys": ["candidate"],
            }
        ]
        public_payload = json.dumps(asdict(run), sort_keys=True)
        artifact_payload = (
            fixture.settings.runs_dir / run.id / "platform" / "run-plan.json"
        ).read_text(encoding="utf-8")
        assert secret not in public_payload
        assert secret not in artifact_payload
        assert "provider response body must stay private" not in public_payload
        assert "compatibility" not in run.run_plan
    finally:
        fixture.database.close()


def test_unavailable_launch_secret_returns_stable_redacted_error_with_no_side_effects(
    tmp_path,
):
    fixture = _prepare_credential_launch(tmp_path)
    secret = "sk-unavailable-provider-sentinel"
    resolver = _UnavailableSecretResolver(secret)
    supervisor = FakeRunSupervisor()
    app = create_app(
        fixture.settings,
        database=fixture.database,
        supervisor=supervisor,
        secret_resolver=resolver,
        compatibility_probe=_CompatibleProbe(),
    )
    command = asdict(_preview_command(fixture))
    command.pop("project_id")

    with TestClient(app) as client:
        preview = client.post(
            f"/api/platform/v1/projects/{fixture.project_id}/run-launch/preview",
            json=command,
        )
        assert preview.status_code == 200, preview.text
        response = client.post(
            f"/api/platform/v1/projects/{fixture.project_id}/run-launch",
            json={
                **command,
                "preview_token": preview.json()["preview_token"],
                "secret_bindings": {"model_api_key": secret},
            },
            headers={"Idempotency-Key": "tp-ep03-secret-unavailable"},
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={fixture.project_id}"
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == (
        "RUN_EXECUTION_SECRET_UNAVAILABLE"
    )
    assert response.json()["error"]["message"] == (
        "Required execution credentials are unavailable."
    )
    assert response.json()["error"]["details"] == [
        {"slots": ["model_api_key"]}
    ]
    assert secret not in response.text
    assert "request://transient/model-api-key" not in response.text
    assert runs.json()["items"] == []
    assert list(fixture.settings.runs_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("compatibility_code", "expected_message"),
    [
        (UNSUPPORTED_VISION, "The model does not accept image input."),
        (INDETERMINATE, "The provider returned an unclassified response."),
    ],
)
def test_failed_preflight_uses_frozen_public_identity_and_creates_no_run(
    tmp_path,
    compatibility_code,
    expected_message,
):
    fixture = _prepare_credential_launch(tmp_path)
    secret = "sk-incompatible-sentinel"
    provider_text = f"provider rejected {secret} at private upstream"
    supervisor = FakeRunSupervisor()
    app = create_app(
        fixture.settings,
        database=fixture.database,
        supervisor=supervisor,
        secret_resolver=_RecordingSecretResolver(),
        compatibility_probe=_IncompatibleProbe(
            compatibility_code,
            provider_text,
        ),
    )
    command = asdict(_preview_command(fixture))
    command.pop("project_id")

    with TestClient(app) as client:
        preview = client.post(
            f"/api/platform/v1/projects/{fixture.project_id}/run-launch/preview",
            json=command,
        )
        response = client.post(
            f"/api/platform/v1/projects/{fixture.project_id}/run-launch",
            json={
                **command,
                "preview_token": preview.json()["preview_token"],
                "secret_bindings": {"model_api_key": secret},
            },
            headers={
                "Idempotency-Key": f"tp-ep03-preflight-{compatibility_code}"
            },
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={fixture.project_id}"
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_COMPATIBILITY_CHECK_FAILED"
    assert response.json()["error"]["message"] == expected_message
    assert response.json()["error"]["details"] == [
        {
            "code": compatibility_code,
            "lane_keys": ["candidate"],
            "checked_model": "credential-model",
            "checked_image_format": "data_url",
        }
    ]
    assert secret not in response.text
    assert provider_text not in response.text
    assert runs.json()["items"] == []
    assert list(fixture.settings.runs_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("secret_bindings", "expected_code", "expected_slots"),
    [
        ({}, "RUN_EXECUTION_SECRET_MISSING", ["model_api_key"]),
        (
            {
                "model_api_key": "sk-declared-sentinel",
                "judge_api_key": "sk-extra-sentinel",
            },
            "RUN_EXECUTION_SECRET_SLOT_INVALID",
            ["judge_api_key"],
        ),
    ],
)
def test_missing_or_extra_launch_secret_fails_before_resolution_and_side_effects(
    tmp_path,
    secret_bindings,
    expected_code,
    expected_slots,
):
    fixture = _prepare_credential_launch(tmp_path)
    resolver = _RecordingSecretResolver()
    supervisor = FakeRunSupervisor()
    app = create_app(
        fixture.settings,
        database=fixture.database,
        supervisor=supervisor,
        secret_resolver=resolver,
        compatibility_probe=_CompatibleProbe(),
    )
    command = asdict(_preview_command(fixture))
    command.pop("project_id")

    with TestClient(app) as client:
        preview = client.post(
            f"/api/platform/v1/projects/{fixture.project_id}/run-launch/preview",
            json=command,
        )
        response = client.post(
            f"/api/platform/v1/projects/{fixture.project_id}/run-launch",
            json={
                **command,
                "preview_token": preview.json()["preview_token"],
                "secret_bindings": secret_bindings,
            },
            headers={"Idempotency-Key": f"tp-ep03-{expected_code}"},
        )
        runs = client.get(
            f"/api/platform/v1/runs?project_id={fixture.project_id}"
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["details"] == [{"slots": expected_slots}]
    assert not any(value in response.text for value in secret_bindings.values())
    assert resolver.calls == []
    assert runs.json()["items"] == []
    assert list(fixture.settings.runs_dir.iterdir()) == []


def test_rotating_transient_value_keeps_revision_and_plan_identity_stable(tmp_path):
    fixture = _prepare_credential_launch(tmp_path)
    resolver = _RecordingSecretResolver()
    probe = _VariableCompatibleProbe()
    supervisor = FakeRunSupervisor()
    secrets = ("sk-rotated-value-one", "sk-rotated-value-two")
    try:
        launch = RunLaunch(
            fixture.database,
            settings=fixture.settings,
            supervisor=supervisor,
            catalog_builder=_catalog,
            secret_resolver=resolver,
            compatibility_preflight=CompatibilityPreflight(probe),
        )
        preview_command = _preview_command(fixture)
        preview = launch.preview(preview_command)
        runs = [
            launch.create(
                CreateRunLaunch(**preview_command.__dict__),
                expected_preview_token=preview.preview_token,
                secret_bindings={"model_api_key": secret},
                idempotency_key=f"tp-ep03-rotation-{index}",
            )
            for index, secret in enumerate(secrets, start=1)
        ]

        assert runs[0].id != runs[1].id
        assert runs[0].fingerprint == runs[1].fingerprint == (
            preview.run_plan_fingerprint
        )
        assert (
            runs[0].execution_identity.lane_bindings[
                0
            ].execution_profile_revision_hash
            == runs[1].execution_identity.lane_bindings[
                0
            ].execution_profile_revision_hash
        )
        assert runs[0].run_attempts[0]["compatibility"][0]["latency_ms"] == 10
        assert runs[1].run_attempts[0]["compatibility"][0]["latency_ms"] == 20
        assert all("compatibility" not in run.run_plan for run in runs)

        public_payload = json.dumps([asdict(run) for run in runs], sort_keys=True)
        artifact_payload = "".join(
            (
                fixture.settings.runs_dir
                / run.id
                / "platform"
                / "run-plan.json"
            ).read_text(encoding="utf-8")
            for run in runs
        )
        assert all(secret not in public_payload for secret in secrets)
        assert all(secret not in artifact_payload for secret in secrets)
    finally:
        fixture.database.close()
