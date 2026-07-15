from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from test_platform.config import PlatformSettings
from test_platform.domain.execution_profiles import (
    PublishProfile,
    SaveProfileDraft,
    UpdateProfileDraft,
)
from test_platform.domain.run_launch import (
    CreateRunLaunch,
    LaneBindingInput,
    PreviewRunLaunch,
    RunLaunchError,
)
from test_platform.domain.task_catalog import TaskCatalogItem, TaskCatalogSnapshot
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import (
    ProjectRepository,
    TargetRepository,
    WorkflowRepository,
)
from test_platform.services.execution import SerialRunExecutor
from test_platform.services.execution_profiles import ExecutionProfiles
from test_platform.services.compatibility_preflight import CompatibilityPreflight
from test_platform.services.run_launch import RunLaunch
from test_platform.testing.fake_compat import FakeCompatibilityProbe
from test_platform.testing.deterministic import (
    DeterministicAgent,
    DeterministicEnvironment,
    DeterministicTaskFactory,
)


class RecordingSupervisor:
    def __init__(self):
        self.submitted: list[str] = []

    def submit(self, run_id: str) -> None:
        self.submitted.append(run_id)


@dataclass(frozen=True)
class PreparedLaunch:
    database: Database
    settings: PlatformSettings
    project: object
    workflow_version: object
    target: object
    target_revision: object
    profile: object
    profile_revision: object


def _catalog():
    return TaskCatalogSnapshot(
        schema_version=1,
        repository_revision="git-tp-ep02",
        digest="sha256:catalog-tp-ep02",
        items=[
            TaskCatalogItem(
                task_base_id="fake.ProfileAwareTask",
                suite="fake",
                class_name="ProfileAwareTask",
                apps=["fake"],
                templates=["Complete the deterministic profile-aware task."],
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


def _workflow_definition():
    return {
        "schema_version": 2,
        "name": "Profile-aware Single",
        "nodes": [
            {
                "id": "tasks",
                "type": "task_selection",
                "depends_on": [],
                "config": {
                    "task_ids": ["fake.ProfileAwareTask"],
                    "sample_n": 1,
                },
            },
            {
                "id": "slots",
                "type": "matrix",
                "depends_on": ["tasks"],
                "config": {
                    "lane_slots": {
                        "candidate": {"role": "candidate"},
                    },
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
    }


def _profile_spec():
    return {
        "schema_version": 1,
        "agent": {"id": "generic_v2"},
        "model": {
            "protocol": "openai_chat_completions",
            "base_url": "http://127.0.0.1:1234/v1",
            "name": "deterministic-model",
        },
        "image_input": {"format": "data_url"},
        "generation": {
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 4096,
            "stream": True,
        },
        "inference": {"timeout_seconds": 300},
        "credentials": {"required_slots": []},
    }


def _target_config():
    return {
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
    }


def _prepare_launch(tmp_path) -> PreparedLaunch:
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    project = ProjectRepository(database).create("Profile-aware launch")
    workflow_repository = WorkflowRepository(database)
    workflow = workflow_repository.create(
        project_id=project.id,
        name="Profile-aware Single",
        definition=_workflow_definition(),
    )
    workflow_version = workflow_repository.publish(workflow.id)

    target_repository = TargetRepository(database)
    target = target_repository.create(
        project_id=project.id,
        name="Deterministic simulator",
        config=_target_config(),
    )
    target_revision = target_repository.record_revision(
        target_id=target.id,
        metadata={
            "schema_version": 1,
            "data": {"revision": "seed-v1"},
            "resolved_at": "2026-07-15T00:00:00.000Z",
        },
        warnings=[],
        health_status="healthy",
    )

    profiles = ExecutionProfiles(database)
    profile = profiles.save_draft(
        SaveProfileDraft(
            project_id=project.id,
            name="Deterministic generic v2",
            draft_spec=_profile_spec(),
        )
    )
    profile_revision = profiles.publish(
        PublishProfile(
            project_id=project.id,
            execution_profile_id=profile.id,
        )
    )
    return PreparedLaunch(
        database=database,
        settings=settings,
        project=project,
        workflow_version=workflow_version,
        target=target,
        target_revision=target_revision,
        profile=profile,
        profile_revision=profile_revision,
    )


def _preview_command(fixture: PreparedLaunch) -> PreviewRunLaunch:
    return PreviewRunLaunch(
        project_id=fixture.project.id,
        workflow_version_id=fixture.workflow_version.id,
        name="Deterministic Single",
        seed=20260715,
        comparison_intent="single",
        lane_bindings=(
            LaneBindingInput(
                lane_slot="candidate",
                target_revision_id=fixture.target_revision.id,
                execution_profile_revision_id=fixture.profile_revision.id,
            ),
        ),
    )


def _create_command(fixture: PreparedLaunch) -> CreateRunLaunch:
    preview = _preview_command(fixture)
    return CreateRunLaunch(**preview.__dict__)


def test_run_launch_preview_resolves_one_exact_profile_aware_lane_binding(tmp_path):
    fixture = _prepare_launch(tmp_path)
    try:
        preview = RunLaunch(
            fixture.database,
            catalog_builder=_catalog,
        ).preview(_preview_command(fixture))

        assert preview.workflow_version_id == fixture.workflow_version.id
        assert preview.workflow_version_hash == fixture.workflow_version.definition_hash
        assert preview.comparison_intent == "single"
        assert preview.episode_count == 1
        assert len(preview.lane_bindings) == 1
        resolved = preview.lane_bindings[0]
        assert resolved.lane_slot == "candidate"
        assert resolved.role == "candidate"
        assert resolved.target_id == fixture.target.id
        assert resolved.target_revision_id == fixture.target_revision.id
        assert resolved.target_revision_hash == fixture.target_revision.metadata_hash
        assert resolved.execution_profile_id == fixture.profile.id
        assert (
            resolved.execution_profile_revision_id == fixture.profile_revision.id
        )
        assert (
            resolved.execution_profile_public_hash
            == fixture.profile_revision.public_spec_hash
        )
        assert resolved.execution_profile_revision_hash.startswith("sha256:")
        assert resolved.lane_fingerprint.startswith("sha256:")
        assert preview.run_plan_fingerprint.startswith("sha256:")
        assert preview.preview_token.startswith("sha256:")
        assert preview.credential_requirements == []
    finally:
        fixture.database.close()


def test_run_launch_create_persists_run_plan_v2_before_dispatch(tmp_path):
    fixture = _prepare_launch(tmp_path)
    supervisor = RecordingSupervisor()
    try:
        launch = RunLaunch(
            fixture.database,
            settings=fixture.settings,
            supervisor=supervisor,
            catalog_builder=_catalog,
            compatibility_preflight=CompatibilityPreflight(
                FakeCompatibilityProbe()
            ),
        )
        preview = launch.preview(_preview_command(fixture))

        run = launch.create(
            _create_command(fixture),
            expected_preview_token=preview.preview_token,
            secret_bindings={},
            idempotency_key="tp-ep02-single",
        )

        assert run.state == "queued"
        assert supervisor.submitted == [run.id]
        assert run.fingerprint == preview.run_plan_fingerprint
        assert run.run_plan["schema_version"] == 2
        assert run.run_plan["workflow_version_id"] == fixture.workflow_version.id
        assert run.run_plan["lanes"][0]["target_revision_id"] == (
            fixture.target_revision.id
        )
        assert run.run_plan["lanes"][0]["execution_profile_revision_id"] == (
            fixture.profile_revision.id
        )
        snapshot = run.run_plan["execution_snapshots"][fixture.profile_revision.id]
        assert snapshot["public_spec"] == fixture.profile_revision.public_spec.model_dump(
            mode="json"
        )
        assert run.execution_identity.kind == "profile_aware"
        assert run.execution_identity.lane_bindings[0].execution_profile_revision_id == (
            fixture.profile_revision.id
        )
        artifact = fixture.settings.runs_dir / run.id / "platform" / "run-plan.json"
        assert json.loads(artifact.read_text(encoding="utf-8")) == run.run_plan

        replayed = launch.create(
            _create_command(fixture),
            expected_preview_token=preview.preview_token,
            secret_bindings={},
            idempotency_key="tp-ep02-single",
        )
        assert replayed.id == run.id
        assert supervisor.submitted == [run.id]
    finally:
        fixture.database.close()


def test_stale_preview_token_has_no_run_dispatch_or_idempotency_side_effect(
    tmp_path,
):
    fixture = _prepare_launch(tmp_path)
    supervisor = RecordingSupervisor()
    try:
        launch = RunLaunch(
            fixture.database,
            settings=fixture.settings,
            supervisor=supervisor,
            catalog_builder=_catalog,
            compatibility_preflight=CompatibilityPreflight(
                FakeCompatibilityProbe()
            ),
        )
        command = _preview_command(fixture)
        preview = launch.preview(command)
        changed = CreateRunLaunch(
            **{
                **command.__dict__,
                "seed": command.seed + 1,
            }
        )

        with pytest.raises(RunLaunchError) as captured:
            launch.create(
                changed,
                expected_preview_token=preview.preview_token,
                secret_bindings={},
                idempotency_key="tp-ep02-stale",
            )

        assert captured.value.code == "RUN_LAUNCH_PREVIEW_STALE"
        assert supervisor.submitted == []

        created = launch.create(
            _create_command(fixture),
            expected_preview_token=preview.preview_token,
            secret_bindings={},
            idempotency_key="tp-ep02-stale",
        )
        assert created.state == "queued"
        assert supervisor.submitted == [created.id]
    finally:
        fixture.database.close()


def test_run_launch_artifact_write_failure_has_no_durable_or_file_side_effect(
    tmp_path,
    monkeypatch,
):
    fixture = _prepare_launch(tmp_path)
    supervisor = RecordingSupervisor()
    original_write_text = Path.write_text

    def fail_run_plan_write(path, *args, **kwargs):
        if path.name == "run-plan.json":
            raise OSError("run plan write blocked")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_run_plan_write)
    try:
        launch = RunLaunch(
            fixture.database,
            settings=fixture.settings,
            supervisor=supervisor,
            catalog_builder=_catalog,
            compatibility_preflight=CompatibilityPreflight(
                FakeCompatibilityProbe()
            ),
        )
        preview = launch.preview(_preview_command(fixture))

        with pytest.raises(OSError, match="run plan write blocked"):
            launch.create(
                _create_command(fixture),
                expected_preview_token=preview.preview_token,
                secret_bindings={},
                idempotency_key="tp-ep02-artifact-write-failure",
            )

        for table in (
            "runs",
            "run_attempts",
            "lanes",
            "lane_attempts",
            "episodes",
            "events",
            "idempotency_keys",
        ):
            count = fixture.database.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            assert count == 0
        assert supervisor.submitted == []
        assert list(fixture.settings.runs_dir.iterdir()) == []
    finally:
        fixture.database.close()


@pytest.mark.asyncio
async def test_profile_aware_run_plan_executes_through_the_single_lane_adapter(
    tmp_path,
):
    fixture = _prepare_launch(tmp_path)
    supervisor = RecordingSupervisor()
    try:
        launch = RunLaunch(
            fixture.database,
            settings=fixture.settings,
            supervisor=supervisor,
            catalog_builder=_catalog,
            compatibility_preflight=CompatibilityPreflight(
                FakeCompatibilityProbe()
            ),
        )
        preview = launch.preview(_preview_command(fixture))
        run = launch.create(
            _create_command(fixture),
            expected_preview_token=preview.preview_token,
            secret_bindings={},
            idempotency_key="tp-ep02-executor-compatible",
        )

        detail = await SerialRunExecutor(
            fixture.database,
            fixture.settings,
            task_factory=DeterministicTaskFactory(),
            env_factory=lambda _lane: DeterministicEnvironment(),
            agent_factory=lambda _lane: DeterministicAgent(),
        ).execute_run(run.id)

        assert detail.state == "evaluating"
        assert detail.progress["completed_episodes"] == 1
        assert detail.execution_identity.kind == "profile_aware"
    finally:
        fixture.database.close()


def test_run_and_lane_fingerprints_are_stable_and_revision_sensitive(tmp_path):
    fixture = _prepare_launch(tmp_path)
    try:
        launch = RunLaunch(fixture.database, catalog_builder=_catalog)
        command = _preview_command(fixture)

        first = launch.preview(command)
        repeated = launch.preview(command)

        profiles = ExecutionProfiles(fixture.database)
        profiles.update_draft(
            UpdateProfileDraft(
                project_id=fixture.project.id,
                execution_profile_id=fixture.profile.id,
                draft_spec=_profile_spec(),
                name="Renamed deterministic generic v2",
            )
        )
        after_profile_rename = launch.preview(command)

        changed_spec = _profile_spec()
        changed_spec["model"]["name"] = "next-deterministic-model"
        changed_profile = profiles.save_draft(
            SaveProfileDraft(
                project_id=fixture.project.id,
                name="Next deterministic generic v2",
                draft_spec=changed_spec,
            )
        )
        changed_profile_revision = profiles.publish(
            PublishProfile(
                project_id=fixture.project.id,
                execution_profile_id=changed_profile.id,
            )
        )
        profile_changed_command = PreviewRunLaunch(
            **{
                **command.__dict__,
                "lane_bindings": (
                    LaneBindingInput(
                        lane_slot="candidate",
                        target_revision_id=fixture.target_revision.id,
                        execution_profile_revision_id=changed_profile_revision.id,
                    ),
                ),
            }
        )
        profile_changed = launch.preview(profile_changed_command)

        target_changed_revision = TargetRepository(
            fixture.database
        ).record_revision(
            target_id=fixture.target.id,
            metadata={
                "schema_version": 1,
                "data": {"revision": "seed-v2"},
                "resolved_at": "2026-07-16T00:00:00.000Z",
            },
            warnings=[],
            health_status="healthy",
        )
        target_changed_command = PreviewRunLaunch(
            **{
                **command.__dict__,
                "lane_bindings": (
                    LaneBindingInput(
                        lane_slot="candidate",
                        target_revision_id=target_changed_revision.id,
                        execution_profile_revision_id=fixture.profile_revision.id,
                    ),
                ),
            }
        )
        target_changed = launch.preview(target_changed_command)

        assert repeated.preview_token == first.preview_token
        assert repeated.run_plan_fingerprint == first.run_plan_fingerprint
        assert after_profile_rename.preview_token == first.preview_token
        assert after_profile_rename.run_plan_fingerprint == first.run_plan_fingerprint
        assert (
            repeated.lane_bindings[0].lane_fingerprint
            == first.lane_bindings[0].lane_fingerprint
        )
        assert (
            after_profile_rename.lane_bindings[0].lane_fingerprint
            == first.lane_bindings[0].lane_fingerprint
        )
        assert profile_changed.run_plan_fingerprint != first.run_plan_fingerprint
        assert (
            profile_changed.lane_bindings[0].lane_fingerprint
            != first.lane_bindings[0].lane_fingerprint
        )
        assert target_changed.run_plan_fingerprint != first.run_plan_fingerprint
        assert (
            target_changed.lane_bindings[0].lane_fingerprint
            != first.lane_bindings[0].lane_fingerprint
        )
    finally:
        fixture.database.close()
