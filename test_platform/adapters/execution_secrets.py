from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from test_platform.domain.execution_secrets import (
    SecretLease,
    SecretRequirement,
    SecretResolutionError,
)


class RequestSecretResolver:
    """Resolve request-backed credential references into a process-local lease."""

    def resolve(
        self,
        requirements: tuple[SecretRequirement, ...],
        supplied_bindings: Mapping[str, str],
    ) -> SecretLease:
        values: dict[str, str] = {}
        for requirement in requirements:
            if requirement.backend != "request":
                raise SecretResolutionError(
                    "RUN_EXECUTION_SECRET_UNAVAILABLE",
                    "A required execution credential is unavailable.",
                    status_code=503,
                    details=[{"slot": requirement.slot}],
                )
            value = supplied_bindings.get(requirement.slot)
            if not isinstance(value, str) or not value.strip():
                raise SecretResolutionError(
                    "RUN_EXECUTION_SECRET_MISSING",
                    "A required execution credential was not supplied.",
                    details=[{"slot": requirement.slot}],
                )
            values[requirement.slot] = value.strip()
        return SecretLease(values=MappingProxyType(values))
