"""Domain layer for screenshot-model compatibility checks.

This module is free of vendor SDK dependencies. The adapter layer
(``test_platform.adapters.model_compatibility``) owns the OpenAI client and
provider exception inspection, then delegates classification to the pure
function here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# ---- Stable codes ----------------------------------------------------------

COMPATIBLE = "compatible"
UNREACHABLE = "unreachable"
AUTHENTICATION_FAILURE = "authentication_failure"
MISSING_MODEL = "missing_model"
UNSUPPORTED_VISION = "unsupported_vision"
UNSUPPORTED_IMAGE_FORMAT = "unsupported_image_format"
TIMEOUT = "timeout"
INDETERMINATE = "indeterminate"

ALL_CODES = frozenset({
    COMPATIBLE,
    UNREACHABLE,
    AUTHENTICATION_FAILURE,
    MISSING_MODEL,
    UNSUPPORTED_VISION,
    UNSUPPORTED_IMAGE_FORMAT,
    TIMEOUT,
    INDETERMINATE,
})

# ---- Explanation templates (allowlisted — never echo provider text) --------

_EXPLANATIONS: dict[str, str] = {
    COMPATIBLE: "The model accepted the screenshot request.",
    UNREACHABLE: "The endpoint could not be reached.",
    AUTHENTICATION_FAILURE: "Authentication was rejected by the provider.",
    MISSING_MODEL: "The model was not found at the endpoint.",
    UNSUPPORTED_VISION: "The model does not accept image input.",
    UNSUPPORTED_IMAGE_FORMAT: "The model rejected the image format.",
    TIMEOUT: "The request timed out.",
    INDETERMINATE: "The provider returned an unclassified response.",
}


def explanation_for(code: str, *, timeout_seconds: float | None = None) -> str:
    """Return a stable, allowlisted explanation for *code*.

    Never includes provider response body, exception text, or URL.
    """
    base = _EXPLANATIONS.get(code, _EXPLANATIONS[INDETERMINATE])
    if code == TIMEOUT and timeout_seconds is not None:
        return f"The request timed out within {timeout_seconds:.0f}s."
    return base


# ---- Result ----------------------------------------------------------------


@dataclass(frozen=True)
class CompatibilityResult:
    code: str
    explanation: str
    latency_ms: int
    checked_model: str
    checked_image_format: str


# ---- Probe protocol --------------------------------------------------------


class CompatibilityProbe(Protocol):
    def check(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        image_url_format: str,
        timeout_seconds: float,
    ) -> CompatibilityResult:
        ...


# ---- Pure classification function ------------------------------------------


# Keyword sets (lower-cased substring match against response body / error text).
# Order matters: checked in priority sequence by ``classify_response``.

_MODEL_KEYWORDS = ("model", "does not exist", "no such model")
_FORMAT_KEYWORDS = ("format", "base64", "data url", "data_url", "content part", "image_url")
_VISION_KEYWORDS = ("vision", "multimodal", "image input", "does not support image")


def classify_response(
    *,
    status_code: int | None,
    body_text: str,
    exc_type: str | None = None,
) -> str:
    """Classify a provider response into a stable code.

    Pure function: receives already-extracted data from the adapter layer.
    Priority: auth → explicit missing-model → explicit format rejection →
    explicit vision rejection → timeout → connection → indeterminate.
    """
    text = (body_text or "").lower()

    # Success.
    if status_code == 200:
        return COMPATIBLE

    # Exception-driven classification (no HTTP status available).
    if status_code is None:
        if exc_type and "timeout" in exc_type.lower():
            return TIMEOUT
        if exc_type and "connection" in exc_type.lower():
            return UNREACHABLE
        return INDETERMINATE

    # HTTP-status-driven classification by priority.
    if status_code in (401, 403):
        return AUTHENTICATION_FAILURE

    if status_code == 404:
        if any(kw in text for kw in _MODEL_KEYWORDS):
            return MISSING_MODEL
        return INDETERMINATE

    if status_code == 400:
        # Format rejection takes priority over vision rejection — a body
        # mentioning both "image" and "base64 format" is a format error.
        if any(kw in text for kw in _FORMAT_KEYWORDS):
            return UNSUPPORTED_IMAGE_FORMAT
        if any(kw in text for kw in _VISION_KEYWORDS):
            return UNSUPPORTED_VISION
        return INDETERMINATE

    if status_code == 422:
        if any(kw in text for kw in _VISION_KEYWORDS):
            return UNSUPPORTED_VISION
        if any(kw in text for kw in _FORMAT_KEYWORDS):
            return UNSUPPORTED_IMAGE_FORMAT
        return INDETERMINATE

    # 429, 5xx, empty, non-JSON, or anything else.
    return INDETERMINATE
