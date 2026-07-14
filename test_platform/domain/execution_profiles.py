from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from test_platform.domain.ids import new_id


_SECRET_LIKE_FIELD_KEYS = frozenset(
    {
        "apikey",
        "apitoken",
        "accesstoken",
        "authorization",
        "bearertoken",
        "credentialvalue",
        "password",
        "secret",
        "secretvalue",
        "token",
    }
)


class ExecutionProfileDomainError(Exception):
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


class AgentSubjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: Literal["generic_v2"]


class ModelSubjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocol: Literal["openai_chat_completions"]
    base_url: str = Field(min_length=1)
    name: str = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        cleaned = value.strip()
        parsed = urlsplit(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("model.base_url must be an HTTP(S) URL")
        path = parsed.path.rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("model.name is required")
        return cleaned


class ImageInputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["data_url", "bare_base64"]


class GenerationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)
    max_tokens: int = Field(ge=1)
    stream: bool


class InferenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timeout_seconds: float = Field(gt=0, le=3600)


class CredentialRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    required_slots: list[str] = Field(default_factory=list, max_length=0)


class ExecutionProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    agent: AgentSubjectSpec
    model: ModelSubjectSpec
    image_input: ImageInputSpec
    generation: GenerationSpec
    inference: InferenceSpec
    credentials: CredentialRequirements = Field(default_factory=CredentialRequirements)


@dataclass(frozen=True)
class ExecutionProfile:
    id: str
    project_id: str
    name: str
    name_key: str
    draft_spec: dict[str, Any]
    head_revision_id: str | None
    archived_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ExecutionProfileRevision:
    id: str
    execution_profile_id: str
    revision_no: int
    public_spec: dict[str, Any]
    public_spec_hash: str
    credential_binding_digest: str
    published_at: str


class ExecutionProfileRevisionView(BaseModel):
    id: str
    execution_profile_id: str
    revision_no: int
    public_spec: ExecutionProfileSpec
    public_spec_hash: str
    credential_binding_digest: str
    published_at: str


class ExecutionProfileView(BaseModel):
    id: str
    project_id: str
    name: str
    draft_spec: ExecutionProfileSpec
    head_revision: ExecutionProfileRevisionView | None
    archived_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SaveProfileDraft:
    project_id: str
    name: str
    draft_spec: dict[str, Any]


@dataclass(frozen=True)
class PublishProfile:
    project_id: str
    execution_profile_id: str


@dataclass(frozen=True)
class UpdateProfileDraft:
    project_id: str
    execution_profile_id: str
    draft_spec: dict[str, Any]
    name: str | None = None


def new_execution_profile_id() -> str:
    return new_id()


def new_execution_profile_revision_id() -> str:
    return new_id()


def clean_execution_profile_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip())


def require_execution_profile_name(name: str) -> str:
    cleaned = clean_execution_profile_name(name)
    if not cleaned:
        raise ExecutionProfileDomainError(
            "EXECUTION_PROFILE_NAME_REQUIRED",
            "Execution Profile name is required.",
        )
    return cleaned


def normalize_execution_profile_name(name: str) -> str:
    return clean_execution_profile_name(name).casefold()


def execution_profile_utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reject_secret_like_fields(
    value: Any,
    *,
    path: tuple[str, ...] = (),
) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            field_path = (*path, key_text)
            normalized_key = re.sub(r"[^a-z0-9]", "", key_text.casefold())
            if normalized_key in _SECRET_LIKE_FIELD_KEYS:
                raise ExecutionProfileDomainError(
                    "EXECUTION_PROFILE_SECRET_VALUE_FORBIDDEN",
                    "Execution Profile specs must not contain secret values.",
                    details=[{"field": ".".join(field_path)}],
                )
            reject_secret_like_fields(nested, path=field_path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_secret_like_fields(nested, path=(*path, str(index)))


def reject_credential_bearing_urls(payload: dict[str, Any]) -> None:
    model = payload.get("model")
    if not isinstance(model, dict):
        return
    base_url = model.get("base_url")
    if not isinstance(base_url, str):
        return
    parsed = urlsplit(base_url)
    if parsed.username is not None or parsed.password is not None or parsed.query:
        raise ExecutionProfileDomainError(
            "EXECUTION_PROFILE_URL_CREDENTIALS_FORBIDDEN",
            "Execution Profile URLs must not contain user info or query parameters.",
            details=[{"field": "model.base_url"}],
        )
