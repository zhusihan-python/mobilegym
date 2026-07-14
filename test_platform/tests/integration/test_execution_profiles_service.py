from test_platform.config import PlatformSettings
from test_platform.domain.execution_profiles import PublishProfile, SaveProfileDraft
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
