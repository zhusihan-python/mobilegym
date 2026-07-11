"""OpenAI-compatible adapter for screenshot-model compatibility checks.

This module owns the vendor SDK dependency (``openai``, ``httpx``) and the
provider exception/body inspection. It delegates classification to the pure
function in ``test_platform.domain.model_compatibility``.

The probe sends a single bounded, non-streaming ``chat.completions.create``
request carrying a minimal 64x64 screenshot, using the same screenshot-message
helper and image-format adapter as real inference.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any, Callable

import httpx
from openai import OpenAI
from PIL import Image

from bench_env.agent.screenshot_message import build_screenshot_user_message
from bench_env.llm.openai_chat import _format_image_url_messages

from test_platform.domain.model_compatibility import (
    COMPATIBLE,
    CompatibilityResult,
    classify_response,
    explanation_for,
)

logger = logging.getLogger(__name__)


def _minimal_png_bytes() -> bytes:
    """Generate a valid 64x64 solid-color PNG via Pillow.

    Large enough to avoid VLM minimum-size rejections, small enough to be a
    truly minimal probe payload. Generated at module load and verified by the
    unit test (Pillow Image.verify + size check).
    """
    img = Image.new("RGB", (64, 64), color=(24, 32, 48))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_MINIMAL_PNG_BYTES = _minimal_png_bytes()


def _minimal_image_data_url() -> str:
    """Return a data URL for the probe PNG.

    Uses base64 encoding matching the real ``Observation.image_data_url`` path
    (MIME sniffed from PNG magic bytes, then base64-encoded).
    """
    b64 = base64.b64encode(_MINIMAL_PNG_BYTES).decode()
    return f"data:image/png;base64,{b64}"


# Type alias: factory that produces an OpenAI client ready for ``with``.
ClientFactory = Callable[..., OpenAI]


def default_client_factory(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    http_client: httpx.Client | None = None,
) -> OpenAI:
    """Construct a production OpenAI client with retries disabled."""
    return OpenAI(
        base_url=base_url.rstrip("/"),
        api_key=api_key or "",
        max_retries=0,
        timeout=httpx.Timeout(timeout_seconds),
        http_client=http_client,
    )


class OpenAICompatibilityProbe:
    """Production probe that sends a minimal screenshot request via the SDK."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = default_client_factory,
    ) -> None:
        self._client_factory = client_factory

    def check(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        image_url_format: str,
        timeout_seconds: float,
    ) -> CompatibilityResult:
        start = time.monotonic()

        # Build the probe message using the SAME helper as GenericAgentV2,
        # then apply the SAME provider format adapter as LLMClient.chat().
        message = build_screenshot_user_message(
            _minimal_image_data_url(),
            "compatibility probe",
        )
        messages = _format_image_url_messages([message], image_url_format)

        code = COMPATIBLE
        body_text = ""
        exc_type: str | None = None
        status_code: int | None = None

        try:
            with self._client_factory(
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
            ) as client:
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=False,
                    max_tokens=1,
                )
                status_code = 200
        except Exception as exc:  # noqa: BLE001 — probe must classify, not crash
            exc_type = type(exc).__name__
            status_code, body_text = _extract_error_context(exc)

        latency_ms = int((time.monotonic() - start) * 1000)

        if status_code == 200:
            result_code = COMPATIBLE
        else:
            result_code = classify_response(
                status_code=status_code,
                body_text=body_text,
                exc_type=exc_type,
            )

        # Log only safe fields — never URL, exception text, body, or api_key.
        logger.info(
            "model_compatibility_check code=%s model=%s image_format=%s latency_ms=%d",
            result_code,
            model,
            image_url_format,
            latency_ms,
        )

        return CompatibilityResult(
            code=result_code,
            explanation=explanation_for(result_code, timeout_seconds=timeout_seconds),
            latency_ms=latency_ms,
            checked_model=model,
            checked_image_format=image_url_format,
        )


def _extract_error_context(exc: Exception) -> tuple[int | None, str]:
    """Extract HTTP status code and a lower-cased body/error text from an SDK exception.

    The returned text is used ONLY for classification — it never reaches the
    API response or logs (the domain layer's explanation templates are allowlisted).
    """
    # OpenAI SDK status errors carry .response and .status_code
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)

    body = ""
    # APIStatusError has a .body attribute (parsed JSON or raw)
    raw_body = getattr(exc, "body", None)
    if raw_body is not None:
        if isinstance(raw_body, dict):
            body = str(raw_body)
        else:
            body = str(raw_body)
    else:
        # Fall back to the exception message (may contain response snippet)
        body = str(exc)

    return status, body
