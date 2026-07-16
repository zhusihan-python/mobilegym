from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from test_platform.domain.execution_profiles import (
    ArchiveProfile,
    CloneProfileRevision,
    CredentialReadiness,
    CredentialReferenceBindingInput,
    ExecutionProfile,
    ExecutionProfileDomainError,
    ExecutionProfileSpec,
    ExecutionProfileView,
    ExecutionProfileRevision,
    ExecutionProfileRevisionDiff,
    ExecutionProfileRevisionFieldChange,
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
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._repository = ExecutionProfileRepository(database, clock=clock)

    def save_draft(self, command: SaveProfileDraft) -> ExecutionProfileView:
        name = require_execution_profile_name(command.name)
        normalized_spec = self._normalize_spec(command.draft_spec)
        bindings = self._normalize_bindings(
            project_id=command.project_id,
            required_slots=normalized_spec.credentials.required_slots,
            bindings=command.credential_bindings,
        )

        profile = self._repository.create(
            project_id=command.project_id,
            name=name,
            draft_spec=normalized_spec.model_dump(mode="json"),
            draft_credential_bindings=bindings,
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
        bindings = self._normalize_bindings(
            project_id=command.project_id,
            required_slots=normalized_spec.credentials.required_slots,
            bindings=(
                profile.draft_credential_bindings
                if command.credential_bindings is None
                else command.credential_bindings
            ),
        )
        updated = self._repository.update_draft(
            execution_profile_id=profile.id,
            name=name,
            draft_spec=normalized_spec.model_dump(mode="json"),
            draft_credential_bindings=bindings,
            expected_draft_version=command.expected_draft_version,
        )
        return self._profile_view(updated)

    def list(
        self,
        *,
        project_id: str,
        include_archived: bool = False,
    ) -> list[ExecutionProfileView]:
        return [
            self._profile_view(profile)
            for profile in self._repository.list(
                project_id=project_id,
                include_archived=include_archived,
            )
        ]

    def archive(self, command: ArchiveProfile) -> ExecutionProfileView:
        profile = self._repository.get(command.execution_profile_id)
        self._assert_project(profile.project_id, command.project_id)
        archived = self._repository.archive(
            execution_profile_id=profile.id,
            expected_draft_version=command.expected_draft_version,
            expected_head_revision_id=command.expected_head_revision_id,
        )
        return self._profile_view(archived)

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
        normalized_spec = self._normalize_spec(profile.draft_spec)
        bindings = self._normalize_bindings(
            project_id=command.project_id,
            required_slots=normalized_spec.credentials.required_slots,
            bindings=profile.draft_credential_bindings,
        )
        readiness = self._credential_readiness(
            normalized_spec.credentials.required_slots,
            bindings,
        )
        if not readiness.ready:
            raise ExecutionProfileDomainError(
                "EXECUTION_PROFILE_CREDENTIAL_BINDING_MISSING",
                "Every required credential slot must have a Credential Reference binding.",
                details=[{"slots": readiness.missing_slots}],
            )
        revision = self._repository.publish(
            execution_profile_id=profile.id,
            public_spec=normalized_spec.model_dump(mode="json"),
            public_spec_hash=canonical_sha256(normalized_spec.model_dump(mode="json")),
            credential_bindings=bindings,
            credential_binding_digest=readiness.binding_digest,
            expected_draft_version=command.expected_draft_version,
            expected_head_revision_id=command.expected_head_revision_id,
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

    def list_revisions(
        self,
        *,
        project_id: str,
        execution_profile_id: str,
    ) -> list[ExecutionProfileRevisionView]:
        profile = self._repository.get(execution_profile_id)
        self._assert_project(profile.project_id, project_id)
        return [
            self._revision_view(revision)
            for revision in self._repository.list_revisions(execution_profile_id)
        ]

    def diff_revisions(
        self,
        *,
        project_id: str,
        from_revision_id: str,
        to_revision_id: str,
    ) -> ExecutionProfileRevisionDiff:
        from_revision, from_profile = self._repository.get_revision(from_revision_id)
        to_revision, to_profile = self._repository.get_revision(to_revision_id)
        self._assert_project(from_profile.project_id, project_id)
        self._assert_project(to_profile.project_id, project_id)
        if from_profile.id != to_profile.id:
            raise ExecutionProfileDomainError(
                "EXECUTION_PROFILE_REVISION_DIFF_INVALID",
                "Execution Profile Revision diff requires one Execution Profile.",
                details=[
                    {
                        "from_revision_id": from_revision_id,
                        "to_revision_id": to_revision_id,
                    }
                ],
            )
        before = self._flatten_public_spec(from_revision.public_spec)
        after = self._flatten_public_spec(to_revision.public_spec)
        if (
            from_revision.credential_binding_digest
            != to_revision.credential_binding_digest
        ):
            before["credentials.binding_digest"] = (
                from_revision.credential_binding_digest
            )
            after["credentials.binding_digest"] = to_revision.credential_binding_digest
        changes = [
            ExecutionProfileRevisionFieldChange(
                path=path,
                before=before.get(path),
                after=after.get(path),
            )
            for path in sorted(set(before) | set(after))
            if before.get(path) != after.get(path)
        ]
        return ExecutionProfileRevisionDiff(
            from_revision_id=from_revision.id,
            to_revision_id=to_revision.id,
            changes=changes,
        )

    def clone(self, command: CloneProfileRevision) -> ExecutionProfileView:
        revision, source_profile = self._repository.get_revision(command.revision_id)
        self._assert_project(source_profile.project_id, command.project_id)
        name = require_execution_profile_name(command.name)
        normalized_spec = self._normalize_spec(revision.public_spec)
        bindings = self._normalize_bindings(
            project_id=command.project_id,
            required_slots=normalized_spec.credentials.required_slots,
            bindings=revision.credential_bindings,
        )
        clone = self._repository.create(
            project_id=command.project_id,
            name=name,
            draft_spec=normalized_spec.model_dump(mode="json"),
            draft_credential_bindings=bindings,
        )
        return self._profile_view(clone)

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
    def _flatten_public_spec(
        value: object,
        *,
        path: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            return {".".join(path): value}
        flattened: dict[str, object] = {}
        for key in sorted(value):
            flattened.update(
                ExecutionProfiles._flatten_public_spec(
                    value[key],
                    path=(*path, str(key)),
                )
            )
        return flattened

    @staticmethod
    def _normalize_bindings(
        *,
        project_id: str,
        required_slots: list[str],
        bindings: tuple[CredentialReferenceBindingInput, ...],
    ) -> tuple[CredentialReferenceBindingInput, ...]:
        normalized: dict[str, CredentialReferenceBindingInput] = {}
        for binding in bindings:
            slot = binding.slot.strip()
            if not slot or slot in normalized:
                raise ExecutionProfileDomainError(
                    "EXECUTION_PROFILE_CREDENTIAL_BINDING_INVALID",
                    "Credential Reference binding slots must be non-empty and unique.",
                    details=[{"slot": slot}],
                )
            if binding.backend != "request":
                raise ExecutionProfileDomainError(
                    "EXECUTION_PROFILE_CREDENTIAL_BACKEND_UNSUPPORTED",
                    "Credential Reference backend is not supported.",
                    details=[{"slot": slot}],
                )
            if binding.project_id != project_id:
                raise ExecutionProfileDomainError(
                    "EXECUTION_PROFILE_CREDENTIAL_BINDING_CROSS_PROJECT",
                    "Credential Reference bindings must belong to the selected Project.",
                    status_code=409,
                    details=[{"slot": slot}],
                )
            if slot not in required_slots:
                raise ExecutionProfileDomainError(
                    "EXECUTION_PROFILE_CREDENTIAL_BINDING_EXTRA",
                    "Credential Reference binding was supplied for an undeclared slot.",
                    details=[{"slot": slot}],
                )
            reference_id = binding.reference_id.strip()
            private_locator = binding.private_locator.strip()
            if not reference_id or not private_locator:
                raise ExecutionProfileDomainError(
                    "EXECUTION_PROFILE_CREDENTIAL_BINDING_INVALID",
                    "Credential Reference identity is incomplete.",
                    details=[{"slot": slot}],
                )
            normalized[slot] = CredentialReferenceBindingInput(
                slot=slot,
                project_id=project_id,
                backend=binding.backend,
                reference_id=reference_id,
                private_locator=private_locator,
            )
        return tuple(normalized[slot] for slot in sorted(normalized))

    @staticmethod
    def _credential_readiness(
        required_slots: list[str],
        bindings: tuple[CredentialReferenceBindingInput, ...],
    ) -> CredentialReadiness:
        bound_slots = sorted(binding.slot for binding in bindings)
        missing_slots = sorted(set(required_slots) - set(bound_slots))
        identity = {
            binding.slot: {
                "project_id": binding.project_id,
                "backend": binding.backend,
                "reference_id": binding.reference_id,
                "private_locator": binding.private_locator,
            }
            for binding in bindings
        }
        return CredentialReadiness(
            required_slots=sorted(required_slots),
            bound_slots=bound_slots,
            missing_slots=missing_slots,
            ready=not missing_slots,
            binding_digest=canonical_sha256(identity),
        )

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
        spec = ExecutionProfileSpec.model_validate(revision.public_spec)
        return ExecutionProfileRevisionView(
            id=revision.id,
            execution_profile_id=revision.execution_profile_id,
            revision_no=revision.revision_no,
            public_spec=spec,
            public_spec_hash=revision.public_spec_hash,
            credential_binding_digest=revision.credential_binding_digest,
            credential_readiness=ExecutionProfiles._credential_readiness(
                spec.credentials.required_slots,
                revision.credential_bindings,
            ),
            published_at=revision.published_at,
        )

    def _profile_view(self, profile: ExecutionProfile) -> ExecutionProfileView:
        draft_spec = self._normalize_spec(profile.draft_spec)
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
            draft_spec=draft_spec,
            credential_readiness=self._credential_readiness(
                draft_spec.credentials.required_slots,
                profile.draft_credential_bindings,
            ),
            draft_version=profile.draft_version,
            head_revision=head_revision,
            archived_at=profile.archived_at,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )
