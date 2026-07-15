from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class SecretRequirement:
    slot: str
    project_id: str
    execution_profile_revision_id: str
    backend: str
    reference_id: str
    private_locator: str
    lane_keys: tuple[str, ...]


@dataclass(frozen=True)
class SecretLease:
    values: Mapping[str, str]


class SecretResolutionError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


class SecretResolver(Protocol):
    def resolve(
        self,
        requirements: tuple[SecretRequirement, ...],
        supplied_bindings: Mapping[str, str],
    ) -> SecretLease: ...
