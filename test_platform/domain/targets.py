from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from test_platform.domain.ids import new_id


class TargetDomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


class TargetNotFound(TargetDomainError):
    def __init__(self, target_id: str) -> None:
        super().__init__(
            "TARGET_NOT_FOUND",
            "Target was not found.",
            status_code=404,
            details=[{"target_id": target_id}],
        )


class TargetRevisionNotFound(TargetDomainError):
    def __init__(self, target_revision_id: str) -> None:
        super().__init__(
            "TARGET_REVISION_NOT_FOUND",
            "Target Revision was not found.",
            status_code=404,
            details=[{"target_revision_id": target_revision_id}],
        )


class DuplicateTargetName(TargetDomainError):
    def __init__(self, name: str) -> None:
        super().__init__(
            "TARGET_NAME_EXISTS",
            "A target with this name already exists in the project.",
            status_code=409,
            details=[{"name": name}],
        )


@dataclass(frozen=True)
class Target:
    id: str
    project_id: str
    name: str
    kind: str
    enabled: bool
    config: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TargetRevision:
    id: str
    target_id: str
    metadata: dict[str, Any]
    metadata_hash: str
    health_status: str
    warnings: list[str]
    resolved_at: str


def new_target_id() -> str:
    return new_id()


def new_target_revision_id() -> str:
    return new_id()


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_target_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise TargetDomainError("TARGET_CONFIG_INVALID", "Target config must be an object.")

    kind = config.get("kind")
    if kind not in {"simulator", "real_device"}:
        raise TargetDomainError(
            "TARGET_CONFIG_INVALID",
            "Target kind must be simulator or real_device.",
            details=[{"field": "kind"}],
        )

    connection = config.get("connection")
    if not isinstance(connection, dict):
        raise TargetDomainError(
            "TARGET_CONFIG_INVALID",
            "Target connection must be an object.",
            details=[{"field": "connection"}],
        )

    if kind == "simulator" and not _non_empty_string(connection.get("env_url")):
        raise TargetDomainError(
            "TARGET_CONFIG_INVALID",
            "Simulator target requires connection.env_url.",
            details=[{"field": "connection.env_url"}],
        )

    device_profile = config.get("device_profile")
    if not isinstance(device_profile, dict):
        raise TargetDomainError(
            "TARGET_CONFIG_INVALID",
            "Target device_profile must be an object.",
            details=[{"field": "device_profile"}],
        )

    required_profile_fields = (
        "name",
        "viewport_width",
        "viewport_height",
        "physical_width",
        "physical_height",
        "device_scale_factor",
    )
    for field in required_profile_fields:
        if field not in device_profile:
            raise TargetDomainError(
                "TARGET_CONFIG_INVALID",
                "Target device_profile is missing required fields.",
                details=[{"field": f"device_profile.{field}"}],
            )

    normalized = deepcopy(config)
    normalized.setdefault("runtime", {})
    normalized.setdefault("labels", {})
    return normalized


def redact_target_config(config: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(config)
    kind = public.get("kind")
    connection = public.get("connection")
    if isinstance(connection, dict):
        if kind == "simulator":
            public["connection"] = {
                "env_url": connection.get("env_url"),
                "proxy_configured": bool(connection.get("proxy_secret_ref")),
            }
        elif kind == "real_device":
            public["connection"] = {
                "adb_configured": bool(
                    connection.get("adb_serial")
                    or connection.get("adb_serial_secret_ref")
                ),
            }
        else:
            public["connection"] = {}
    return public


def validate_sim_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise _metadata_error("Metadata must be an object.")
    if metadata.get("schemaVersion") != 1:
        raise _metadata_error("Metadata schemaVersion must be 1.", "schemaVersion")

    simulator = metadata.get("simulator")
    if not isinstance(simulator, dict):
        raise _metadata_error("Metadata simulator must be an object.", "simulator")
    for field in ("product", "version", "buildId"):
        if not _non_empty_string(simulator.get(field)):
            raise _metadata_error(
                f"Metadata simulator.{field} is required.",
                f"simulator.{field}",
            )

    apps = metadata.get("apps")
    if not isinstance(apps, list):
        raise _metadata_error("Metadata apps must be a list.", "apps")
    for index, app in enumerate(apps):
        if not isinstance(app, dict):
            raise _metadata_error("Metadata app entries must be objects.", f"apps[{index}]")
        for field in ("id", "packageName", "displayName", "version", "type"):
            if not _non_empty_string(app.get(field)):
                raise _metadata_error(
                    f"Metadata app {field} is required.",
                    f"apps[{index}].{field}",
                )
        if not isinstance(app.get("versionCode"), int):
            raise _metadata_error(
                "Metadata app versionCode must be an integer.",
                f"apps[{index}].versionCode",
            )

    data = metadata.get("data")
    if not isinstance(data, dict):
        raise _metadata_error("Metadata data must be an object.", "data")

    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise _metadata_error(
            "Metadata capabilities must be a string list.",
            "capabilities",
        )

    return metadata


def build_target_revision_metadata(
    *,
    config: dict[str, Any],
    sim_metadata: dict[str, Any],
    resolved_at: str,
) -> dict[str, Any]:
    validated = validate_sim_metadata(sim_metadata)
    return {
        "schema_version": 1,
        "target_kind": config["kind"],
        "simulator": deepcopy(validated["simulator"]),
        "apps": deepcopy(validated["apps"]),
        "data": deepcopy(validated["data"]),
        "device_profile": deepcopy(config["device_profile"]),
        "runtime_config_hash": canonical_metadata_hash(config.get("runtime", {})),
        "capabilities": deepcopy(validated["capabilities"]),
        "resolved_at": resolved_at,
    }


def canonical_metadata_hash(metadata: dict[str, Any]) -> str:
    canonical = deepcopy(metadata)
    if isinstance(canonical, dict):
        canonical.pop("resolved_at", None)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _metadata_error(message: str, field: str | None = None) -> TargetDomainError:
    details = [{"field": field}] if field else []
    return TargetDomainError(
        "TARGET_METADATA_INVALID",
        message,
        status_code=400,
        details=details,
    )


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
