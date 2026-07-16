import pytest

from test_platform.config import PlatformSettings
from test_platform.domain.execution_profiles import (
    ArchiveProfile,
    CloneProfileRevision,
    CredentialReferenceBindingInput,
    ExecutionProfileDomainError,
    PublishProfile,
    SaveProfileDraft,
    UpdateProfileDraft,
)
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ProjectRepository
from test_platform.services.execution_profiles import ExecutionProfiles


def _public_spec():
    return {
        "schema_version": 1,
        "agent": {"id": "generic_v2"},
        "model": {
            "protocol": "openai_chat_completions",
            "base_url": "http://127.0.0.1:1234/v1/",
            "name": "  local-model  ",
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


def test_execution_profiles_publishes_and_discovers_revision_through_module_interface(
    tmp_path,
):
    database = Database(
        PlatformSettings(
            database_path=tmp_path / "platform.sqlite3",
            runs_dir=tmp_path / "runs",
        )
    )
    database.initialize()
    try:
        project = ProjectRepository(database).create("Execution profiles")
        profiles = ExecutionProfiles(database)

        draft = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="  Generic v2 / local model  ",
                draft_spec=_public_spec(),
            )
        )
        revision = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
                expected_draft_version=draft.draft_version,
                expected_head_revision_id=None,
            )
        )

        discovered = profiles.list(project_id=project.id)
        reloaded_revision = profiles.get_revision(
            project_id=project.id,
            revision_id=revision.id,
        )

        assert draft.name == "Generic v2 / local model"
        assert draft.draft_spec.model.base_url == "http://127.0.0.1:1234/v1"
        assert draft.draft_spec.model.name == "local-model"
        assert revision.revision_no == 1
        assert revision.public_spec_hash == (
            "sha256:3d71ef81a34bc6d78054f83d6c094360be1893f79b2d1ffac1bc79a90d29c37d"
        )
        assert [profile.id for profile in discovered] == [draft.id]
        assert discovered[0].head_revision == revision
        assert reloaded_revision == revision
    finally:
        database.close()


def test_execution_profiles_archive_releases_active_normalized_name(tmp_path):
    archived_at = "2026-07-16T10:00:00.000Z"
    database = Database(
        PlatformSettings(
            database_path=tmp_path / "platform.sqlite3",
            runs_dir=tmp_path / "runs",
        )
    )
    database.initialize()
    try:
        project = ProjectRepository(database).create("Profile catalog")
        profiles = ExecutionProfiles(database, clock=lambda: archived_at)
        original = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="  Candidate   Subject  ",
                draft_spec=_public_spec(),
            )
        )

        with pytest.raises(ExecutionProfileDomainError) as captured:
            profiles.save_draft(
                SaveProfileDraft(
                    project_id=project.id,
                    name="candidate subject",
                    draft_spec=_public_spec(),
                )
            )

        archived = profiles.archive(
            ArchiveProfile(
                project_id=project.id,
                execution_profile_id=original.id,
                expected_draft_version=original.draft_version,
                expected_head_revision_id=None,
            )
        )
        replacement = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="CANDIDATE SUBJECT",
                draft_spec=_public_spec(),
            )
        )

        assert captured.value.code == "EXECUTION_PROFILE_NAME_CONFLICT"
        assert archived.archived_at == archived_at
        assert [item.id for item in profiles.list(project_id=project.id)] == [
            replacement.id
        ]
        assert {
            item.id
            for item in profiles.list(project_id=project.id, include_archived=True)
        } == {original.id, replacement.id}
    finally:
        database.close()


def test_execution_profiles_list_diff_and_clone_exact_revisions_without_private_data(
    tmp_path,
):
    database = Database(
        PlatformSettings(
            database_path=tmp_path / "platform.sqlite3",
            runs_dir=tmp_path / "runs",
        )
    )
    database.initialize()
    try:
        project = ProjectRepository(database).create("Revision discovery")
        profiles = ExecutionProfiles(database)
        first_spec = _public_spec()
        first_spec["credentials"]["required_slots"] = ["model_api_key"]
        private_locator = "request://private/provider-sentinel"
        draft = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="Original subject",
                draft_spec=first_spec,
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
        revision_one = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
                expected_draft_version=draft.draft_version,
                expected_head_revision_id=None,
            )
        )
        second_spec = _public_spec()
        second_spec["model"]["name"] = "next-model"
        second_spec["credentials"]["required_slots"] = ["model_api_key"]
        changed = profiles.update_draft(
            UpdateProfileDraft(
                project_id=project.id,
                execution_profile_id=draft.id,
                draft_spec=second_spec,
                credential_bindings=(
                    CredentialReferenceBindingInput(
                        slot="model_api_key",
                        project_id=project.id,
                        backend="request",
                        reference_id="secondary-model-key",
                        private_locator="request://private/secondary-sentinel",
                    ),
                ),
                expected_draft_version=draft.draft_version,
            )
        )
        revision_two = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
                expected_draft_version=changed.draft_version,
                expected_head_revision_id=revision_one.id,
            )
        )

        revisions = profiles.list_revisions(
            project_id=project.id,
            execution_profile_id=draft.id,
        )
        diff = profiles.diff_revisions(
            project_id=project.id,
            from_revision_id=revision_one.id,
            to_revision_id=revision_two.id,
        )
        clone = profiles.clone(
            CloneProfileRevision(
                project_id=project.id,
                revision_id=revision_one.id,
                name="Cloned revision one",
            )
        )

        assert [revision.revision_no for revision in revisions] == [1, 2]
        assert [change.path for change in diff.changes] == [
            "credentials.binding_digest",
            "model.name",
        ]
        assert clone.head_revision is None
        assert clone.draft_spec == revision_one.public_spec
        assert clone.credential_readiness.binding_digest == (
            revision_one.credential_binding_digest
        )
        public_payload = diff.model_dump_json() + clone.model_dump_json()
        assert private_locator not in public_payload
        assert "secondary-sentinel" not in public_payload
        assert "primary-model-key" not in public_payload
        assert "secondary-model-key" not in public_payload
    finally:
        database.close()


def test_execution_profiles_publish_changed_drafts_and_reuse_unchanged_head(
    tmp_path,
):
    database = Database(
        PlatformSettings(
            database_path=tmp_path / "platform.sqlite3",
            runs_dir=tmp_path / "runs",
        )
    )
    database.initialize()
    try:
        project = ProjectRepository(database).create("Revision lifecycle")
        profiles = ExecutionProfiles(database)
        draft = profiles.save_draft(
            SaveProfileDraft(
                project_id=project.id,
                name="Generic v2 / local model",
                draft_spec=_public_spec(),
            )
        )

        revision_one = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
                expected_draft_version=draft.draft_version,
                expected_head_revision_id=None,
            )
        )
        unchanged = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
                expected_draft_version=draft.draft_version,
                expected_head_revision_id=None,
            )
        )
        changed_spec = _public_spec()
        changed_spec["model"]["name"] = "next-model"
        changed_draft = profiles.update_draft(
            UpdateProfileDraft(
                project_id=project.id,
                execution_profile_id=draft.id,
                draft_spec=changed_spec,
                expected_draft_version=draft.draft_version,
            )
        )
        revision_two = profiles.publish(
            PublishProfile(
                project_id=project.id,
                execution_profile_id=draft.id,
                expected_draft_version=changed_draft.draft_version,
                expected_head_revision_id=revision_one.id,
            )
        )

        assert unchanged == revision_one
        assert changed_draft.draft_version == draft.draft_version + 1
        assert revision_two.revision_no == 2
        assert revision_two.public_spec.model.name == "next-model"
        assert profiles.get_revision(
            project_id=project.id,
            revision_id=revision_one.id,
        ).public_spec.model.name == "local-model"
    finally:
        database.close()
