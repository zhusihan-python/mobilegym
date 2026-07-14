from __future__ import annotations

from pydantic import ValidationError

from test_platform.domain.execution_profiles import (
    ExecutionProfile,
    ExecutionProfileDomainError,
    ExecutionProfileSpec,
    ExecutionProfileView,
    ExecutionProfileRevision,
    ExecutionProfileRevisionView,
    PublishProfile,
    reject_credential_bearing_urls,
    reject_secret_like_fields,
    require_execution_profile_name,
    SaveProfileDraft,
    UpdateProfileDraft,
)
from test_platform.domain.canonical_json import canonical_sha256
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ExecutionProfileRepository


class ExecutionProfiles:
    def __init__(self, database: Database) -> None:
        self._repository = ExecutionProfileRepository(database)

    def save_draft(self, command: SaveProfileDraft) -> ExecutionProfileView:
        name = require_execution_profile_name(command.name)
        normalized_spec = self._normalize_spec(command.draft_spec)

        profile = self._repository.create(
            project_id=command.project_id,
            name=name,
            draft_spec=normalized_spec.model_dump(mode="json"),
        )
        return self._profile_view(profile)

    def update_draft(self, command: UpdateProfileDraft) -> ExecutionProfileView:
        profile = self._repository.get(command.execution_profile_id)
        self._assert_project(profile.project_id, command.project_id)
        name = (
            profile.name
            if command.name is None
            else require_execution_profile_name(command.name)
        )
        normalized_spec = self._normalize_spec(command.draft_spec)
        updated = self._repository.update_draft(
            execution_profile_id=profile.id,
            name=name,
            draft_spec=normalized_spec.model_dump(mode="json"),
        )
        return self._profile_view(updated)

    def list(self, *, project_id: str) -> list[ExecutionProfileView]:
        return [
            self._profile_view(profile)
            for profile in self._repository.list(project_id=project_id)
        ]

    def get(
        self,
        *,
        project_id: str,
        execution_profile_id: str,
    ) -> ExecutionProfileView:
        profile = self._repository.get(execution_profile_id)
        self._assert_project(profile.project_id, project_id)
        return self._profile_view(profile)

    def publish(self, command: PublishProfile) -> ExecutionProfileRevisionView:
        profile = self._repository.get(command.execution_profile_id)
        self._assert_project(profile.project_id, command.project_id)
        if profile.head_revision_id is not None:
            raise ExecutionProfileDomainError(
                "EXECUTION_PROFILE_ALREADY_PUBLISHED",
                "Execution Profile revision 1 has already been published.",
                status_code=409,
            )
        normalized_spec = self._normalize_spec(profile.draft_spec)
        revision = self._repository.publish(
            execution_profile_id=profile.id,
            public_spec=normalized_spec.model_dump(mode="json"),
            public_spec_hash=canonical_sha256(normalized_spec.model_dump(mode="json")),
            credential_binding_digest=canonical_sha256({}),
        )
        return self._revision_view(revision)

    def get_revision(
        self,
        *,
        project_id: str,
        revision_id: str,
    ) -> ExecutionProfileRevisionView:
        revision, profile = self._repository.get_revision(revision_id)
        if profile.project_id != project_id:
            raise ExecutionProfileDomainError(
                "EXECUTION_PROFILE_REVISION_NOT_FOUND",
                "Execution Profile Revision was not found.",
                status_code=404,
                details=[{"execution_profile_revision_id": revision_id}],
            )
        return self._revision_view(revision)

    def _normalize_spec(self, payload: dict[str, object]) -> ExecutionProfileSpec:
        reject_secret_like_fields(payload)
        reject_credential_bearing_urls(payload)
        try:
            return ExecutionProfileSpec.model_validate(payload)
        except ValidationError as exc:
            raise ExecutionProfileDomainError(
                "EXECUTION_PROFILE_SPEC_INVALID",
                "Execution Profile spec is invalid.",
                details=[
                    {
                        "field": ".".join(str(item) for item in error["loc"]),
                        "message": error["msg"],
                    }
                    for error in exc.errors()
                ],
            ) from exc

    @staticmethod
    def _assert_project(actual_project_id: str, expected_project_id: str) -> None:
        if actual_project_id != expected_project_id:
            raise ExecutionProfileDomainError(
                "EXECUTION_PROFILE_NOT_FOUND",
                "Execution Profile was not found.",
                status_code=404,
            )

    @staticmethod
    def _revision_view(
        revision: ExecutionProfileRevision,
    ) -> ExecutionProfileRevisionView:
        return ExecutionProfileRevisionView(
            id=revision.id,
            execution_profile_id=revision.execution_profile_id,
            revision_no=revision.revision_no,
            public_spec=ExecutionProfileSpec.model_validate(revision.public_spec),
            public_spec_hash=revision.public_spec_hash,
            credential_binding_digest=revision.credential_binding_digest,
            published_at=revision.published_at,
        )

    def _profile_view(self, profile: ExecutionProfile) -> ExecutionProfileView:
        head_revision = None
        if profile.head_revision_id is not None:
            revision, _profile = self._repository.get_revision(
                profile.head_revision_id
            )
            head_revision = self._revision_view(revision)
        return ExecutionProfileView(
            id=profile.id,
            project_id=profile.project_id,
            name=profile.name,
            draft_spec=self._normalize_spec(profile.draft_spec),
            head_revision=head_revision,
            archived_at=profile.archived_at,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )
