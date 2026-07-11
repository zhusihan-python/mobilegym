"""Compatibility-check endpoint for screenshot-model testing."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, field_validator

from test_platform.api.errors import ApiError
from test_platform.domain.model_compatibility import CompatibilityProbe

router = APIRouter(prefix="/api/platform/v1")


class CompatibilityCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_base_url: str
    model_name: str
    model_api_key: str = ""
    image_url_format: str = "data_url"
    timeout_seconds: float = 15.0

    @field_validator("model_name")
    @classmethod
    def _validate_model_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("model_name must not be empty")
        return trimmed

    @field_validator("model_base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("model_base_url must use http or https scheme")
        if parsed.username or parsed.password:
            raise ValueError("model_base_url must not contain userinfo")
        if not parsed.hostname:
            raise ValueError("model_base_url must have a hostname")
        return v

    @field_validator("image_url_format")
    @classmethod
    def _validate_image_format(cls, v: str) -> str:
        if v not in ("data_url", "bare_base64"):
            raise ValueError("image_url_format must be 'data_url' or 'bare_base64'")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: float) -> float:
        if not (1 <= v <= 30):
            raise ValueError("timeout_seconds must be between 1 and 30")
        return v


@router.post("/model-compatibility/check")
def check_model_compatibility(
    request: Request,
    body: CompatibilityCheckRequest,
) -> dict[str, object]:
    probe: CompatibilityProbe | None = getattr(
        request.app.state, "compatibility_probe", None
    )
    if probe is None:
        raise ApiError(
            "COMPATIBILITY_CHECK_UNAVAILABLE",
            "Compatibility check is not configured on this server.",
            status_code=503,
        )

    result = probe.check(
        base_url=body.model_base_url,
        api_key=body.model_api_key,
        model=body.model_name,
        image_url_format=body.image_url_format,
        timeout_seconds=body.timeout_seconds,
    )
    # CompatibilityResult fields only — api_key is never included.
    return {
        "code": result.code,
        "explanation": result.explanation,
        "latency_ms": result.latency_ms,
        "checked_model": result.checked_model,
        "checked_image_format": result.checked_image_format,
    }
