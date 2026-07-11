"""Test-only fake compatibility probe for route-layer testing.

This does NOT validate the production adapter — use ``OpenAICompatibilityProbe``
with ``httpx.MockTransport`` for that (see ``test_model_compatibility.py``).
This fake is only for testing endpoint wiring, secret redaction, and response
shape without constructing a real OpenAI client.
"""

from __future__ import annotations

from test_platform.domain.model_compatibility import (
    COMPATIBLE,
    CompatibilityResult,
    explanation_for,
)


class FakeCompatibilityProbe:
    """Returns a preset code based on the model_name suffix.

    Convention: model_name ending with a known suffix returns the matching
    code; otherwise returns ``compatible``.
    """

    _SUFFIX_MAP = {
        "#auth": "authentication_failure",
        "#missing": "missing_model",
        "#vision": "unsupported_vision",
        "#format": "unsupported_image_format",
        "#timeout": "timeout",
        "#unreachable": "unreachable",
        "#indeterminate": "indeterminate",
    }

    def check(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        image_url_format: str,
        timeout_seconds: float,
    ) -> CompatibilityResult:
        code = COMPATIBLE
        for suffix, mapped in self._SUFFIX_MAP.items():
            if model.endswith(suffix):
                code = mapped
                break
        return CompatibilityResult(
            code=code,
            explanation=explanation_for(code, timeout_seconds=timeout_seconds),
            latency_ms=42,
            checked_model=model,
            checked_image_format=image_url_format,
        )
